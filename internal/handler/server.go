// Package handler exposes the fixed-principal demo surface over HTTP and SSE.
package handler

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"sync"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/service"
)

type Server struct {
	store *service.Store
	auth  CallerAuthenticator
	mu    sync.RWMutex
	subs  map[chan service.Event]string

	// beforePublish is injected only by deterministic integration tests. Send has
	// already committed when publish is called, so pausing here can force delivery
	// inversion without changing persistence semantics.
	hookMu        sync.RWMutex
	beforePublish func(service.Event)
}

// CallerAuthenticator is supplied by the host application's real identity layer.
// The general SSE route deliberately fails closed when no such layer exists.
type CallerAuthenticator interface {
	CallerID(*http.Request) (string, error)
}

func New(store *service.Store) *Server {
	return NewWithAuthenticator(store, nil)
}

func NewWithAuthenticator(store *service.Store, auth CallerAuthenticator) *Server {
	return &Server{store: store, auth: auth, subs: make(map[chan service.Event]string)}
}

// setBeforePublishHookForTest installs the Z9h rendezvous at the post-commit
// boundary. Production constructors leave it nil.
func (s *Server) setBeforePublishHookForTest(hook func(service.Event)) {
	s.hookMu.Lock()
	defer s.hookMu.Unlock()
	s.beforePublish = hook
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", func(w http.ResponseWriter, _ *http.Request) { w.WriteHeader(http.StatusNoContent) })
	mux.HandleFunc("GET /api/demo/feed", s.feed)
	mux.HandleFunc("POST /api/demo/messages", s.send)
	mux.HandleFunc("POST /api/demo/drafts", s.draft)
	mux.HandleFunc("GET /api/demo/events", s.events)
	mux.HandleFunc("GET /api/demo/media", s.media)
	mux.HandleFunc("GET /api/events", s.generalEvents)
	mux.HandleFunc("GET /api/search", func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(w, http.StatusNotImplemented, map[string]string{"error": "search is not available yet"})
	})
	return cors(mux)
}

func (s *Server) feed(w http.ResponseWriter, r *http.Request) {
	query := r.URL.Query()
	if query.Get("after") != "" && query.Get("before") != "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "after and before cannot be combined"})
		return
	}
	limit, err := parseInt(query.Get("limit"))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid limit"})
		return
	}
	if rawBefore := query.Get("before"); rawBefore != "" {
		before, err := strconv.ParseInt(rawBefore, 10, 64)
		if err != nil {
			writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid before"})
			return
		}
		items, err := s.store.FeedBefore(r.Context(), s.store.DemoViewerID(), before, int(limit))
		if err != nil {
			writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		writeJSON(w, http.StatusOK, map[string]any{"messages": items})
		return
	}
	after, err := parseAfter(query.Get("after"))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid after"})
		return
	}
	items, err := s.store.Feed(r.Context(), after, int(limit))
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"messages": items})
}

func (s *Server) send(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Text     string   `json:"text"`
		MediaIDs []string `json:"media_ids"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid JSON"})
		return
	}
	event, err := s.store.SendDemo(r.Context(), body.Text, body.MediaIDs)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	s.publish(event)
	writeJSON(w, http.StatusCreated, event)
}

// draft accepts a small raw body. The server derives caller/channel from the demo route.
func (s *Server) draft(w http.ResponseWriter, r *http.Request) {
	content, err := io.ReadAll(http.MaxBytesReader(w, r.Body, 10<<20))
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid upload"})
		return
	}
	draft, err := s.store.CreateDemoDraft(r.Context(), r.Header.Get("Content-Type"), content)
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, draft)
}

func (s *Server) media(w http.ResponseWriter, r *http.Request) {
	mediaID := r.URL.Query().Get("id")
	if mediaID == "" {
		http.Error(w, "missing media id", http.StatusBadRequest)
		return
	}
	item, err := s.store.GetDemoMedia(r.Context(), mediaID)
	if err != nil {
		http.Error(w, "media not found", http.StatusNotFound)
		return
	}
	contentType := item.ContentType
	if contentType == "" || contentType == "application/octet-stream" {
		contentType = http.DetectContentType(item.Bytes)
	}
	w.Header().Set("Content-Type", contentType)
	w.Header().Set("Cache-Control", "public, max-age=86400")
	w.WriteHeader(http.StatusOK)
	_, _ = w.Write(item.Bytes)
}

func (s *Server) events(w http.ResponseWriter, r *http.Request) {
	s.eventsFor(w, r, s.store.DemoViewerID())
}

func (s *Server) generalEvents(w http.ResponseWriter, r *http.Request) {
	if s.auth == nil {
		http.Error(w, "authentication required", http.StatusUnauthorized)
		return
	}
	callerID, err := s.auth.CallerID(r)
	if err != nil || callerID == "" {
		http.Error(w, "authentication required", http.StatusUnauthorized)
		return
	}
	s.eventsFor(w, r, callerID)
}

func (s *Server) eventsFor(w http.ResponseWriter, r *http.Request, callerID string) {
	flusher, ok := w.(http.Flusher)
	if !ok {
		http.Error(w, "streaming unsupported", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Connection", "keep-alive")
	lastID, err := parseLastEventID(r.Header.Get("Last-Event-ID"))
	if err != nil {
		http.Error(w, "invalid Last-Event-ID", http.StatusBadRequest)
		return
	}
	stream := make(chan service.Event, 64)
	s.mu.Lock()
	s.subs[stream] = callerID
	s.mu.Unlock()
	defer func() { s.mu.Lock(); delete(s.subs, stream); s.mu.Unlock() }()
	// Subscribe before replay so a concurrent publish is queued instead of lost.
	replay, err := s.store.EventsAfter(r.Context(), callerID, lastID)
	if err != nil {
		http.Error(w, "event replay failed", http.StatusInternalServerError)
		return
	}
	for _, event := range replay {
		if !writeEvent(w, flusher, event) {
			return
		}
	}
	flusher.Flush()
	for {
		select {
		case <-r.Context().Done():
			return
		case event := <-stream:
			if !writeEvent(w, flusher, event) {
				return
			}
		}
	}
}

func (s *Server) publish(event service.Event) {
	s.hookMu.RLock()
	hook := s.beforePublish
	s.hookMu.RUnlock()
	if hook != nil {
		hook(event)
	}
	s.mu.RLock()
	subs := make(map[chan service.Event]string, len(s.subs))
	for sub, callerID := range s.subs {
		subs[sub] = callerID
	}
	s.mu.RUnlock()

	for sub, callerID := range subs {
		visible, err := s.store.EventVisible(context.Background(), callerID, event.ID)
		if err != nil || !visible {
			continue
		}
		// The subscriber snapshot means this cannot deadlock with connection cleanup.
		// Backpressure is intentional: a connected client must never silently miss
		// an event while its EventSource remains open. A future dispatcher may bound
		// this without weakening the no-drop/replay contract.
		sub <- event
	}
}

func parseAfter(raw string) (int64, error) {
	if raw == "" {
		return -1, nil
	}
	return strconv.ParseInt(raw, 10, 64)
}
func parseInt(raw string) (int64, error) {
	if raw == "" {
		return 0, nil
	}
	return strconv.ParseInt(raw, 10, 64)
}
func parseLastEventID(raw string) (int64, error) {
	if raw == "" {
		return 0, nil
	}
	return strconv.ParseInt(raw, 10, 64)
}
func writeEvent(w http.ResponseWriter, flusher http.Flusher, event service.Event) bool {
	payload, err := service.EncodeEvent(event)
	if err != nil {
		return false
	}
	_, err = fmt.Fprintf(w, "id: %d\nevent: message\ndata: %s\n\n", event.ID, payload)
	if err != nil {
		return false
	}
	flusher.Flush()
	return true
}
func writeJSON(w http.ResponseWriter, status int, value any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(value)
}
func cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "http://localhost:5173")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		next.ServeHTTP(w, r)
	})
}
