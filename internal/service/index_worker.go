package service

import (
	"context"
	"log"
	"time"
)

// IndexModel is the narrow external-model contract needed by the indexing use case.
// Concrete HTTP details belong to internal/client, keeping this orchestration testable.
type IndexModel interface {
	Append(context.Context, IndexMessage, *Chunk) (AssembledChunk, error)
	EmbedText(context.Context, string, string) (TextEmbedding, error)
	IndexLexical(context.Context, string, string) error
}

// IndexWorker is deliberately a single sequential loop for the demo. Chunk state and
// the file-backed lexical artifact therefore have one writer by construction.
type IndexWorker struct {
	store       *Store
	model       IndexModel
	maxMessages int
	maxTokens   int
	pollEvery   time.Duration
	observer    VisibilityObserver
}

// VisibilityObserver receives the two durable milestones for a closing chunk.
// It is optional and is used by the isolated SLA integration replay; production
// indexing does not depend on it or persist its observations.
type VisibilityObserver interface {
	DenseCommitted(eventID int64, committedAt time.Time)
	LexicalCommitted(eventID int64, committedAt time.Time)
}

func NewIndexWorker(store *Store, model IndexModel, maxMessages, maxTokens int) *IndexWorker {
	return &IndexWorker{store: store, model: model, maxMessages: maxMessages, maxTokens: maxTokens, pollEvery: time.Second}
}

// WithVisibilityObserver attaches optional measurement instrumentation to a worker.
func (w *IndexWorker) WithVisibilityObserver(observer VisibilityObserver) *IndexWorker {
	w.observer = observer
	return w
}

func (w *IndexWorker) Run(ctx context.Context) {
	for {
		if err := w.ProcessOne(ctx); err != nil && !IsNoIndexWork(err) {
			log.Printf("phase 3 index worker: %v", err)
		}
		select {
		case <-ctx.Done():
			return
		case <-time.After(w.pollEvery):
		}
	}
}

// BuildSeed feeds frozen canonical messages through the same RPC and state
// transitions as live ingestion, without manufacturing live outbox events.
func (w *IndexWorker) BuildSeed(ctx context.Context) error {
	messages, err := w.store.SeedIndexMessages(ctx)
	if err != nil {
		return err
	}
	for _, message := range messages {
		if err := w.processMessage(ctx, message); err != nil {
			return err
		}
	}
	return nil
}

// ProcessOne is exposed for deterministic integration tests and performs no parallel work.
func (w *IndexWorker) ProcessOne(ctx context.Context) error {
	message, status, err := w.store.NextIndexMessage(ctx)
	if err != nil {
		return err
	}
	if status == "lexical_pending" {
		closed, err := w.store.LexicalChunkForEvent(ctx, message.EventID)
		if err != nil {
			return w.store.RescheduleIndex(ctx, message.EventID, err)
		}
		if err := w.model.IndexLexical(ctx, closed.ChunkID, closed.Text); err != nil {
			return w.store.RecordLexicalFailure(ctx, message.EventID, err)
		}
		return w.store.MarkIndexed(ctx, message.EventID)
	}
	return w.processMessage(ctx, message)
}

func (w *IndexWorker) processMessage(ctx context.Context, message IndexMessage) error {
	open, err := w.store.OpenChunk(ctx, message.ChannelID)
	if err != nil {
		return err
	}
	assembled, err := w.model.Append(ctx, message, open)
	if err != nil {
		return w.retryOrFail(ctx, message.EventID, err)
	}
	if open == nil || (len(assembled.MessageIDs) <= w.maxMessages && assembled.TokenCount <= w.maxTokens) {
		return w.store.CompleteOpenAppend(ctx, message, assembled)
	}
	embedding, err := w.model.EmbedText(ctx, open.FirstMessageID, open.Text)
	if err != nil {
		return w.retryOrFail(ctx, message.EventID, err)
	}
	// The over-limit candidate proves the prior tail must close. Reassemble the
	// triggering message from nil so the next mutable chunk never overlaps it.
	fresh, err := w.model.Append(ctx, message, nil)
	if err != nil {
		return w.retryOrFail(ctx, message.EventID, err)
	}
	if err := w.store.CloseAndOpen(ctx, message, *open, fresh, embedding); err != nil {
		return err
	}
	if w.observer != nil {
		w.observer.DenseCommitted(message.EventID, time.Now().UTC())
	}
	if err := w.model.IndexLexical(ctx, open.ChunkID, open.Text); err != nil {
		if message.EventID == 0 {
			return err
		}
		return w.store.RecordLexicalFailure(ctx, message.EventID, err)
	}
	if w.observer != nil {
		w.observer.LexicalCommitted(message.EventID, time.Now().UTC())
	}
	return w.store.MarkIndexed(ctx, message.EventID)
}

func (w *IndexWorker) retryOrFail(ctx context.Context, eventID int64, cause error) error {
	if eventID == 0 {
		return cause
	}
	return w.store.RescheduleIndex(ctx, eventID, cause)
}
