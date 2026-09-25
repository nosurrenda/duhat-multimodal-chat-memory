package handler

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/service"
)

// TestZ9hPostCommitPauseForcesDeliveryInversion uses the same channel that an
// SSE connection consumes. It proves the client can receive N+1 before N; the
// frontend's gap-repair tests own the separate rendered-order assertion.
func TestZ9hPostCommitPauseForcesDeliveryInversion(t *testing.T) {
	store, callerID := openSSEIntegrationStore(t)
	server := New(store)
	stream := make(chan service.Event, 2)
	server.mu.Lock()
	server.subs[stream] = callerID
	server.mu.Unlock()
	t.Cleanup(func() {
		server.mu.Lock()
		delete(server.subs, stream)
		server.mu.Unlock()
	})

	paused := make(chan struct{})
	release := make(chan struct{})
	var firstPublish atomic.Bool
	server.setBeforePublishHookForTest(func(service.Event) {
		if firstPublish.CompareAndSwap(false, true) {
			close(paused)
			<-release
		}
	})

	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	firstResult := make(chan sendResult, 1)
	go func() {
		event, err := store.SendDemo(ctx, "z9h first", nil)
		if err == nil {
			server.publish(event)
		}
		firstResult <- sendResult{event: event, err: err}
	}()
	select {
	case <-paused:
	case <-ctx.Done():
		t.Fatalf("first publish did not reach the post-commit rendezvous: %v", ctx.Err())
	}

	second, err := store.SendDemo(ctx, "z9h second", nil)
	if err != nil {
		t.Fatal(err)
	}
	server.publish(second)
	gotSecond := receiveEvent(t, ctx, stream)
	if gotSecond.ID != second.ID {
		t.Fatalf("first delivered event id = %d, want higher event id %d", gotSecond.ID, second.ID)
	}

	close(release)
	first := <-firstResult
	if first.err != nil {
		t.Fatal(first.err)
	}
	gotFirst := receiveEvent(t, ctx, stream)
	if gotFirst.ID != first.event.ID {
		t.Fatalf("second delivered event id = %d, want delayed event id %d", gotFirst.ID, first.event.ID)
	}
	if first.event.FeedOrdinal+1 != second.FeedOrdinal {
		t.Fatalf("feed ordinals = %d then %d, want consecutive allocation", first.event.FeedOrdinal, second.FeedOrdinal)
	}
}

type sendResult struct {
	event service.Event
	err   error
}

func receiveEvent(t *testing.T, ctx context.Context, stream <-chan service.Event) service.Event {
	t.Helper()
	select {
	case event := <-stream:
		return event
	case <-ctx.Done():
		t.Fatalf("timed out waiting for SSE delivery: %v", ctx.Err())
		return service.Event{}
	}
}

func openSSEIntegrationStore(t *testing.T) (*service.Store, string) {
	t.Helper()
	if os.Getenv("VSF_INTEGRATION") != "1" {
		t.Skip("set VSF_INTEGRATION=1 after docker compose up to run Postgres/MinIO integration controls")
	}
	baseURL := integrationSetting("DATABASE_URL", "postgres://vsf:vsf_local_only@localhost:5433/vsf?sslmode=disable")
	schema := fmt.Sprintf("vsf_z9h_%d", time.Now().UnixNano())
	admin, err := sql.Open("pgx", baseURL)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Exec("CREATE SCHEMA " + schema); err != nil {
		_ = admin.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = admin.Exec("DROP SCHEMA IF EXISTS " + schema + " CASCADE")
		_ = admin.Close()
	})

	databaseURL := schemaURL(t, baseURL, schema)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	callerID := "z9h_viewer"
	channelID := "z9h_demo"
	store, err := service.Open(ctx, databaseURL, service.Config{
		DemoViewerID:   callerID,
		DemoChannelID:  channelID,
		DraftMediaTTL:  time.Hour,
		MinIOEndpoint:  integrationSetting("MINIO_ENDPOINT", "http://localhost:9000"),
		MinIOAccessKey: integrationSetting("MINIO_ROOT_USER", "vsf_minio"),
		MinIOSecretKey: integrationSetting("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"),
		MinIOBucket:    integrationSetting("MINIO_BUCKET", "vsf-media"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}

	fixture, err := sql.Open("pgx", databaseURL)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = fixture.Close() })
	for _, statement := range []string{
		"INSERT INTO channels (channel_id, source_family, source_dialogue) VALUES ('z9h_demo', 'live', 'z9h')",
		"INSERT INTO senders (sender_id, display_name, is_corpus) VALUES ('z9h_viewer', 'Z9h viewer', false)",
		"INSERT INTO channel_memberships (caller_id, channel_id, role, valid_from) VALUES ('z9h_viewer', 'z9h_demo', 'owner', now())",
		"INSERT INTO feed_state (singleton, last_feed_ordinal) VALUES (true, 0)",
	} {
		if _, err := fixture.ExecContext(ctx, statement); err != nil {
			t.Fatal(err)
		}
	}
	return store, callerID
}

func schemaURL(t *testing.T, rawURL, schema string) string {
	t.Helper()
	parsed, err := url.Parse(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	query := parsed.Query()
	query.Set("search_path", schema)
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func integrationSetting(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}
