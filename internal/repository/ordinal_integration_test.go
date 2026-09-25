package repository

import (
	"context"
	"database/sql"
	"fmt"
	"net/url"
	"os"
	"strings"
	"testing"
	"time"
)

// TestZ1fUnsafeMaximumMutantFailsDeterministically keeps the deliberately
// unsafe allocator in test code. The production allocator remains the atomic
// UPDATE ... RETURNING sequence in Send.
func TestZ1fUnsafeMaximumMutantFailsDeterministically(t *testing.T) {
	store, db := openOrdinalIntegrationStore(t)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	channelID := "z1f_channel"
	senderID := "z1f_sender"
	if _, err := db.ExecContext(ctx, `INSERT INTO channels (channel_id, source_family, source_dialogue) VALUES ($1, 'test', 'z1f')`, channelID); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, `INSERT INTO senders (sender_id, display_name, is_corpus) VALUES ($1, 'Z1f sender', false)`, senderID); err != nil {
		t.Fatal(err)
	}

	arrived := make(chan struct{}, 2)
	release := make(chan struct{})
	errs := make(chan error, 2)
	for i := range 2 {
		go func(i int) {
			errs <- unsafeMaximumInsert(ctx, store, channelID, senderID, i, arrived, release)
		}(i)
	}
	for range 2 {
		select {
		case <-arrived:
		case <-ctx.Done():
			t.Fatalf("unsafe allocators did not both reach the rendezvous: %v", ctx.Err())
		}
	}
	close(release)

	successes := 0
	for range 2 {
		if err := <-errs; err == nil {
			successes++
		}
	}
	if successes != 1 {
		t.Fatalf("unsafe SELECT MAX(ordinal)+1 mutant successes = %d, want exactly 1", successes)
	}
}

func unsafeMaximumInsert(ctx context.Context, store *Store, channelID, senderID string, number int, arrived chan<- struct{}, release <-chan struct{}) error {
	tx, err := store.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var ordinal int64
	if err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(ordinal), 0) + 1 FROM messages WHERE channel_id = $1`, channelID).Scan(&ordinal); err != nil {
		return err
	}
	// Both transactions have read the same stale maximum before either inserts.
	arrived <- struct{}{}
	<-release
	_, err = tx.ExecContext(ctx, `INSERT INTO messages (message_id, channel_id, sender_id, body, occurred_at, timestamp_source, source_turn_index, session_index, source_session_id, chronological_rank, temporal_order_conflict, ordinal, feed_ordinal)
VALUES ($1, $2, $3, 'unsafe mutant', now(), 'test', 0, 0, 'z1f', -1, false, $4, $5)`, fmt.Sprintf("z1f_message_%d", number), channelID, senderID, ordinal, int64(number+1))
	if err != nil {
		return err
	}
	return tx.Commit()
}

func openOrdinalIntegrationStore(t *testing.T) (*Store, *sql.DB) {
	t.Helper()
	if os.Getenv("VSF_INTEGRATION") != "1" {
		t.Skip("set VSF_INTEGRATION=1 after docker compose up to run Postgres/MinIO integration controls")
	}
	baseURL := integrationEnv("DATABASE_URL", "postgres://vsf:vsf_local_only@localhost:5433/vsf?sslmode=disable")
	schema := fmt.Sprintf("vsf_z1f_%d", time.Now().UnixNano())
	admin, err := sql.Open("pgx", baseURL)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := admin.Exec("CREATE SCHEMA " + schema); err != nil {
		admin.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() {
		_, _ = admin.Exec("DROP SCHEMA IF EXISTS " + schema + " CASCADE")
		_ = admin.Close()
	})

	databaseURL := integrationSchemaURL(t, baseURL, schema)
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	store, err := Open(ctx, databaseURL, Config{
		DemoViewerID:   "z1f_viewer",
		DemoChannelID:  "z1f_demo",
		DraftMediaTTL:  time.Hour,
		MinIOEndpoint:  integrationEnv("MINIO_ENDPOINT", "http://localhost:9000"),
		MinIOAccessKey: integrationEnv("MINIO_ROOT_USER", "vsf_minio"),
		MinIOSecretKey: integrationEnv("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"),
		MinIOBucket:    integrationEnv("MINIO_BUCKET", "vsf-media"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return store, store.db
}

func integrationSchemaURL(t *testing.T, rawURL, schema string) string {
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

func integrationEnv(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}
	return fallback
}
