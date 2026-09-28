package service_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"net/url"
	"os"
	"reflect"
	"sort"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/client"
	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/service"
)

const (
	visibilityChannels      = 12
	visibilityConcurrency   = 4
	visibilityMessagesBatch = 21
)

type visibilityMilestone struct {
	acknowledgedAt time.Time
	denseAt        time.Time
	lexicalAt      time.Time
}

type visibilityRecorder struct {
	mu     sync.Mutex
	events map[int64]*visibilityMilestone
}

func newVisibilityRecorder() *visibilityRecorder {
	return &visibilityRecorder{events: make(map[int64]*visibilityMilestone)}
}

func (r *visibilityRecorder) acknowledge(eventID int64, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.events[eventID] = &visibilityMilestone{acknowledgedAt: at}
}

func (r *visibilityRecorder) DenseCommitted(eventID int64, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if event := r.events[eventID]; event != nil {
		event.denseAt = at
	}
}

func (r *visibilityRecorder) LexicalCommitted(eventID int64, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if event := r.events[eventID]; event != nil {
		event.lexicalAt = at
	}
}

func (r *visibilityRecorder) latencies(t *testing.T) (dense, lexical []float64) {
	t.Helper()
	r.mu.Lock()
	defer r.mu.Unlock()
	for eventID, event := range r.events {
		// Ordinary appends intentionally have no dense or lexical commit. Only a
		// boundary-crossing event is an INDEX_VISIBILITY_SLA sample.
		if event.denseAt.IsZero() && event.lexicalAt.IsZero() {
			continue
		}
		if event.denseAt.IsZero() || event.lexicalAt.IsZero() {
			t.Fatalf("closing event %d did not reach both visibility milestones", eventID)
		}
		dense = append(dense, float64(event.denseAt.Sub(event.acknowledgedAt).Microseconds())/1000)
		lexical = append(lexical, float64(event.lexicalAt.Sub(event.acknowledgedAt).Microseconds())/1000)
	}
	return dense, lexical
}

type latencyPercentiles struct {
	P50MS float64 `json:"p50_ms"`
	P95MS float64 `json:"p95_ms"`
	P99MS float64 `json:"p99_ms"`
}

func latencySummary(values []float64) latencyPercentiles {
	sorted := append([]float64(nil), values...)
	sort.Float64s(sorted)
	nearestRank := func(percentile float64) float64 {
		if len(sorted) == 0 {
			return 0
		}
		index := int(percentile*float64(len(sorted)) - 1)
		if index < 0 {
			index = 0
		}
		return sorted[index]
	}
	return latencyPercentiles{P50MS: nearestRank(0.50), P95MS: nearestRank(0.95), P99MS: nearestRank(0.99)}
}

type semanticChunk struct {
	FirstMessageID  string
	ChunkID         string
	ChannelID       string
	ChunkIndex      int
	MessageIDs      []string
	MediaIDs        []string
	Day             string
	Text            string
	TokenCount      int
	LastFeedOrdinal int64
	Status          string
}

func env(key, fallback string) string {
	if val := os.Getenv(key); val != "" {
		return val
	}
	return fallback
}

func openWorkerIntegrationStore(t *testing.T, schemaPrefix string) (*service.Store, *sql.DB, string) {
	t.Helper()
	if os.Getenv("VSF_INTEGRATION") != "1" {
		t.Skip("set VSF_INTEGRATION=1 after docker compose up to run Postgres/MinIO integration controls")
	}
	baseURL := env("DATABASE_URL", "postgres://vsf:vsf_local_only@localhost:5433/vsf?sslmode=disable")
	schema := fmt.Sprintf("vsf_%s_%d", schemaPrefix, time.Now().UnixNano())
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
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	store, err := service.Open(ctx, databaseURL, service.Config{
		DemoViewerID:   "worker_viewer",
		DemoChannelID:  "worker_channel",
		DraftMediaTTL:  time.Hour,
		MinIOEndpoint:  env("MINIO_ENDPOINT", "http://localhost:9000"),
		MinIOAccessKey: env("MINIO_ROOT_USER", "vsf_minio"),
		MinIOSecretKey: env("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"),
		MinIOBucket:    env("MINIO_BUCKET", "vsf-media"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = store.Close() })
	if err := store.Migrate(ctx); err != nil {
		t.Fatal(err)
	}
	return store, admin, schema
}

func schemaURL(t *testing.T, rawURL, schema string) string {
	t.Helper()
	parsed, err := url.Parse(rawURL)
	if err != nil {
		t.Fatal(err)
	}
	query := parsed.Query()
	query.Set("search_path", schema+",public")
	parsed.RawQuery = query.Encode()
	return parsed.String()
}

func populateFixtureConversation(t *testing.T, db *sql.DB, schema, channelID, senderID string, messageCount int) {
	populateFixtureConversationWithFeedOffset(t, db, schema, channelID, senderID, messageCount, 0)
}

func populateFixtureConversationWithFeedOffset(t *testing.T, db *sql.DB, schema, channelID, senderID string, messageCount, feedOffset int) {
	t.Helper()
	ctx := context.Background()
	qual := schema + "."

	if _, err := db.ExecContext(ctx, fmt.Sprintf(`INSERT INTO %schannels (channel_id, source_family, source_dialogue) VALUES ($1, 'test', 'dialogue')`, qual), channelID); err != nil {
		t.Fatal(err)
	}
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`INSERT INTO %ssenders (sender_id, display_name, is_corpus) VALUES ($1, $2, true)`, qual), senderID, "Test Sender "+senderID); err != nil {
		t.Fatal(err)
	}

	baseTime := time.Date(2026, 9, 24, 10, 0, 0, 0, time.UTC)
	for i := range messageCount {
		msgID := fmt.Sprintf("%s:msg_%d", channelID, i)
		ordinal := int64(i + 1)
		feedOrdinal := int64(feedOffset + i + 1)
		occurredAt := baseTime.Add(time.Duration(i) * time.Minute)
		body := fmt.Sprintf("Message payload number %d with some descriptive text content to accumulate tokens.", i)

		if _, err := db.ExecContext(ctx, fmt.Sprintf(`
INSERT INTO %smessages (
    message_id, channel_id, sender_id, ordinal, chronological_rank, 
    occurred_at, timestamp_source, source_turn_index, session_index, 
    source_session_id, temporal_order_conflict, body, feed_ordinal
)
VALUES ($1, $2, $3, $4, $5, $6, 'declared', $7, 1, 'session1', false, $8, $9)`, qual),
			msgID, channelID, senderID, ordinal, ordinal, occurredAt, i, body, feedOrdinal); err != nil {
			t.Fatal(err)
		}
	}
}

func querySemanticChunks(t *testing.T, db *sql.DB, schema, channelID string) []semanticChunk {
	t.Helper()
	ctx := context.Background()
	rows, err := db.QueryContext(ctx, fmt.Sprintf(`
SELECT first_message_id, chunk_id, channel_id, chunk_index,
       to_json(message_ids)::text, to_json(media_ids)::text,
       day::text, text, token_count, last_feed_ordinal, status
FROM %s.chunks
WHERE channel_id = $1
ORDER BY chunk_index`, schema), channelID)
	if err != nil {
		t.Fatal(err)
	}
	defer rows.Close()

	var chunks []semanticChunk
	for rows.Next() {
		var c semanticChunk
		var msgIDsJSON, mediaIDsJSON string
		if err := rows.Scan(&c.FirstMessageID, &c.ChunkID, &c.ChannelID, &c.ChunkIndex,
			&msgIDsJSON, &mediaIDsJSON, &c.Day, &c.Text, &c.TokenCount, &c.LastFeedOrdinal, &c.Status); err != nil {
			t.Fatal(err)
		}
		c.MessageIDs = splitJSONStrings(msgIDsJSON)
		c.MediaIDs = splitJSONStrings(mediaIDsJSON)
		chunks = append(chunks, c)
	}
	return chunks
}

func splitJSONStrings(raw string) []string {
	raw = strings.TrimPrefix(raw, "[")
	raw = strings.TrimSuffix(raw, "]")
	if strings.TrimSpace(raw) == "" {
		return []string{}
	}
	parts := strings.Split(raw, ",")
	results := make([]string, 0, len(parts))
	for _, p := range parts {
		cleaned := strings.Trim(strings.TrimSpace(p), `"`)
		results = append(results, cleaned)
	}
	return results
}

// TestPhase3VisibilitySLA replays concurrent acknowledged sends against a temporary
// schema. Each channel's 21st message closes its first chunk, exercising the real
// BGE, Postgres, and BM25 RPC path without changing the demo corpus.
func TestPhase3VisibilitySLA(t *testing.T) {
	if os.Getenv("PHASE3_WRITE_BENCHMARK") != "1" {
		t.Skip("set PHASE3_WRITE_BENCHMARK=1 to run the measured visibility replay")
	}
	store, db, schema := openWorkerIntegrationStore(t, "visibility_sla")
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Minute)
	defer cancel()

	recorder := newVisibilityRecorder()
	worker := service.NewIndexWorker(store, client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090")), 20, 400).WithVisibilityObserver(recorder)
	for channel := range visibilityChannels {
		populateFixtureConversationWithFeedOffset(t, db, schema, fmt.Sprintf("visibility_channel_%02d", channel), fmt.Sprintf("visibility_sender_%02d", channel), visibilityMessagesBatch, channel*visibilityMessagesBatch)
	}

	// The outbox insert is the durable acknowledgement boundary. Four concurrent
	// senders make queueing part of the measured closed-chunk visibility latency.
	errCh := make(chan error, visibilityConcurrency)
	var senders sync.WaitGroup
	for lane := range visibilityConcurrency {
		senders.Add(1)
		go func(lane int) {
			defer senders.Done()
			for channel := lane; channel < visibilityChannels; channel += visibilityConcurrency {
				channelID := fmt.Sprintf("visibility_channel_%02d", channel)
				for message := range visibilityMessagesBatch {
					messageID := fmt.Sprintf("%s:msg_%d", channelID, message)
					feedOrdinal := int64(channel*visibilityMessagesBatch + message + 1)
					var eventID int64
					err := db.QueryRowContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal) VALUES ($1, $2) RETURNING event_id`, schema), messageID, feedOrdinal).Scan(&eventID)
					if err != nil {
						errCh <- err
						return
					}
					recorder.acknowledge(eventID, time.Now().UTC())
				}
			}
		}(lane)
	}
	senders.Wait()
	close(errCh)
	for err := range errCh {
		t.Fatal(err)
	}

	for processed := 0; processed < visibilityChannels*visibilityMessagesBatch; processed++ {
		if err := worker.ProcessOne(ctx); err != nil {
			t.Fatalf("process event %d: %v", processed, err)
		}
	}
	dense, lexical := recorder.latencies(t)
	if len(dense) != visibilityChannels || len(lexical) != visibilityChannels {
		t.Fatalf("want %d chunk-close samples, got dense=%d lexical=%d", visibilityChannels, len(dense), len(lexical))
	}

	report := struct {
		MeasuredAt        time.Time          `json:"measured_at"`
		SampleCount       int                `json:"sample_count"`
		BatchSize         int                `json:"batch_size"`
		Concurrency       int                `json:"concurrency"`
		Lexical           latencyPercentiles `json:"lexical"`
		Dense             latencyPercentiles `json:"dense"`
		Visual            latencyPercentiles `json:"visual"`
		VisualSampleCount int                `json:"visual_sample_count"`
		VisualNote        string             `json:"visual_note"`
		Methodology       string             `json:"methodology"`
	}{
		MeasuredAt:        time.Now().UTC(),
		SampleCount:       len(dense),
		BatchSize:         visibilityMessagesBatch,
		Concurrency:       visibilityConcurrency,
		Lexical:           latencySummary(lexical),
		Dense:             latencySummary(dense),
		Visual:            latencyPercentiles{},
		VisualSampleCount: 0,
		VisualNote:        "No live image-embedding path is implemented, so this replay has no visual commit samples; zeroes are not a latency claim.",
		Methodology:       "12 isolated channels x 21 acknowledged messages; four concurrent senders; the one sequential worker processes every event; dense and lexical timestamps are captured immediately after their real durable commit/RPC completion.",
	}
	payload, err := json.MarshalIndent(report, "", "  ")
	if err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll("../../data/benchmarks", 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile("../../data/benchmarks/phase3_visibility_sla.json", append(payload, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Logf("wrote Phase 3 visibility SLA report with %d chunk-close samples", len(dense))
}

// TestZ1aChunkIdentityBulkVsLiveParity asserts byte-identity on all semantic fields
// between bulk seed build (S2) and real sequential outbox replay (S6/S7).
func TestZ1aChunkIdentityBulkVsLiveParity(t *testing.T) {
	model := client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090"))
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	channelID := "worker_channel"
	senderID := "z1a_sender"
	msgCount := 25 // Exceeds CHUNK_MAX_MESSAGES (20) to force boundary crossing

	// 1. Bulk path (S2)
	storeBulk, dbBulk, schemaBulk := openWorkerIntegrationStore(t, "z1a_bulk")
	populateFixtureConversation(t, dbBulk, schemaBulk, channelID, senderID, msgCount)
	workerBulk := service.NewIndexWorker(storeBulk, model, 20, 400)
	if err := workerBulk.BuildSeed(ctx); err != nil {
		t.Fatalf("BuildSeed failed: %v", err)
	}
	bulkChunks := querySemanticChunks(t, dbBulk, schemaBulk, channelID)

	// 2. Live outbox path (S6/S7)
	storeLive, dbLive, schemaLive := openWorkerIntegrationStore(t, "z1a_live")
	populateFixtureConversation(t, dbLive, schemaLive, channelID, senderID, msgCount)
	// Insert corresponding outbox rows
	for i := range msgCount {
		msgID := fmt.Sprintf("%s:msg_%d", channelID, i)
		ordinal := int64(i + 1)
		if _, err := dbLive.ExecContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal, status)
VALUES ($1, $2, 'pending')`, schemaLive), msgID, ordinal); err != nil {
			t.Fatal(err)
		}
	}

	workerLive := service.NewIndexWorker(storeLive, model, 20, 400)
	for {
		err := workerLive.ProcessOne(ctx)
		if service.IsNoIndexWork(err) {
			break
		}
		if err != nil {
			t.Fatalf("ProcessOne failed: %v", err)
		}
	}
	liveChunks := querySemanticChunks(t, dbLive, schemaLive, channelID)

	// Assert parity
	if len(bulkChunks) != len(liveChunks) {
		t.Fatalf("chunk count mismatch: bulk produced %d chunks, live produced %d", len(bulkChunks), len(liveChunks))
	}
	if len(bulkChunks) < 2 {
		t.Fatalf("expected boundary crossing to produce >= 2 chunks, got %d", len(bulkChunks))
	}

	for i := range bulkChunks {
		b, l := bulkChunks[i], liveChunks[i]
		if !reflect.DeepEqual(b, l) {
			t.Errorf("chunk %d semantic mismatch:\nbulk: %+v\nlive: %+v", i, b, l)
		}
	}

	// Verify both paths end with status='open'
	if bulkChunks[len(bulkChunks)-1].Status != "open" {
		t.Errorf("bulk final chunk status is not 'open': %s", bulkChunks[len(bulkChunks)-1].Status)
	}
	if liveChunks[len(liveChunks)-1].Status != "open" {
		t.Errorf("live final chunk status is not 'open': %s", liveChunks[len(liveChunks)-1].Status)
	}
}

// TestZ1bPositiveControlBoundaryPerturbation asserts that perturbing chunk limits
// produces distinct chunk boundaries, proving Z1a detects variations.
func TestZ1bPositiveControlBoundaryPerturbation(t *testing.T) {
	model := client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090"))
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	channelID := "worker_channel"
	senderID := "z1b_sender"
	msgCount := 25

	// Run with maxMessages = 20
	store1, db1, schema1 := openWorkerIntegrationStore(t, "z1b_m20")
	populateFixtureConversation(t, db1, schema1, channelID, senderID, msgCount)
	w1 := service.NewIndexWorker(store1, model, 20, 400)
	if err := w1.BuildSeed(ctx); err != nil {
		t.Fatal(err)
	}
	chunks1 := querySemanticChunks(t, db1, schema1, channelID)

	// Run with perturbed limit (maxMessages = 10)
	store2, db2, schema2 := openWorkerIntegrationStore(t, "z1b_m10")
	populateFixtureConversation(t, db2, schema2, channelID, senderID, msgCount)
	w2 := service.NewIndexWorker(store2, model, 10, 400)
	if err := w2.BuildSeed(ctx); err != nil {
		t.Fatal(err)
	}
	chunks2 := querySemanticChunks(t, db2, schema2, channelID)

	// Assert perturbation causes different chunk boundaries
	if reflect.DeepEqual(chunks1, chunks2) {
		t.Fatalf("positive control failed: perturbed limit did not change chunks output")
	}
	if len(chunks1[0].MessageIDs) == len(chunks2[0].MessageIDs) {
		t.Fatalf("positive control failed: perturbed limit did not change first chunk message count (%d vs %d)",
			len(chunks1[0].MessageIDs), len(chunks2[0].MessageIDs))
	}
}

// TestZ5aNonClosingCrashRecovery verifies that a worker restarting after claiming
// a row in 'processing' resumes cleanly and commits the message exactly once.
func TestZ5aNonClosingCrashRecovery(t *testing.T) {
	model := client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090"))
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	store, db, schema := openWorkerIntegrationStore(t, "z5a_crash")
	channelID := "z5a_channel"
	senderID := "z5a_sender"
	populateFixtureConversation(t, db, schema, channelID, senderID, 1)

	msgID := fmt.Sprintf("%s:msg_0", channelID)
	// Simulate claimed row that crashed before completing transaction
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal, status, attempt_count)
VALUES ($1, 1, 'processing', 1)`, schema), msgID); err != nil {
		t.Fatal(err)
	}

	worker := service.NewIndexWorker(store, model, 20, 400)
	// Restarting worker calls ProcessOne
	if err := worker.ProcessOne(ctx); err != nil {
		t.Fatalf("ProcessOne after crash failed: %v", err)
	}

	// Verify outbox status reached 'done'
	var outboxStatus string
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT status FROM %s.outbox WHERE message_id = $1`, schema), msgID).Scan(&outboxStatus); err != nil {
		t.Fatal(err)
	}
	if outboxStatus != "done" {
		t.Fatalf("expected outbox status 'done', got '%s'", outboxStatus)
	}

	// Verify message in chunk exactly once
	chunks := querySemanticChunks(t, db, schema, channelID)
	if len(chunks) != 1 || len(chunks[0].MessageIDs) != 1 {
		t.Fatalf("expected exactly 1 chunk with 1 message, got %+v", chunks)
	}
}

// TestZ5bCloseTransactionIsAtomic verifies a closing append makes its vector,
// native pg_search text, and completion visible in one database transaction.
func TestZ5bCloseTransactionIsAtomic(t *testing.T) {
	model := client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090"))
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	store, db, schema := openWorkerIntegrationStore(t, "z5b_atomic")
	channelID := "z5b_channel"
	senderID := "z5b_sender"
	populateFixtureConversation(t, db, schema, channelID, senderID, 2)

	firstID := fmt.Sprintf("%s:msg_0", channelID)
	secondID := fmt.Sprintf("%s:msg_1", channelID)
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal, status, attempt_count)
VALUES ($1, 1, 'processing', 1)`, schema), firstID); err != nil {
		t.Fatal(err)
	}
	worker := service.NewIndexWorker(store, model, 1, 400)
	if err := worker.ProcessOne(ctx); err != nil {
		t.Fatalf("initial open append failed: %v", err)
	}

	var eventID int64
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`
	INSERT INTO %s.outbox (message_id, feed_ordinal, status, attempt_count)
	VALUES ($1, 2, 'processing', 1)`, schema), secondID); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT event_id FROM %s.outbox WHERE message_id=$1`, schema), secondID).Scan(&eventID); err != nil {
		t.Fatal(err)
	}
	if err := worker.ProcessOne(ctx); err != nil {
		t.Fatalf("closing append failed: %v", err)
	}

	var outboxStatus string
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT status FROM %s.outbox WHERE event_id = $1`, schema), eventID).Scan(&outboxStatus); err != nil {
		t.Fatal(err)
	}
	if outboxStatus != "done" {
		t.Fatalf("expected outbox status 'done', got '%s'", outboxStatus)
	}
	var embeddings, lexicalHits, openTailHits int
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT count(*) FROM %s.chunk_embeddings WHERE first_message_id=$1`, schema), firstID).Scan(&embeddings); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT count(*) FROM %s.chunks WHERE status='closed' AND first_message_id=$1 AND text @@@ 'descriptive'`, schema), firstID).Scan(&lexicalHits); err != nil {
		t.Fatal(err)
	}
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT count(*) FROM %s.chunks WHERE status='open' AND text @@@ 'descriptive'`, schema)).Scan(&openTailHits); err != nil {
		t.Fatal(err)
	}
	if embeddings != 1 || lexicalHits != 1 || openTailHits != 0 {
		t.Fatalf("closing transaction invariant failed: embeddings=%d lexical_hits=%d open_tail_hits=%d", embeddings, lexicalHits, openTailHits)
	}
}

// TestD75LegacyNonPartialIndexIsUpgraded ensures an older D75 database cannot
// retain an index that would expose mutable open-tail chunks to lexical search.
func TestD75LegacyNonPartialIndexIsUpgraded(t *testing.T) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	store, db, schema := openWorkerIntegrationStore(t, "d75_legacy_index")
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`
DROP INDEX %s.chunks_bm25;
CREATE INDEX chunks_bm25 ON %s.chunks USING paradedb (first_message_id, text)
  WITH (key_field = 'first_message_id');`, schema, schema)); err != nil {
		t.Fatalf("create legacy non-partial index: %v", err)
	}
	if err := store.Migrate(ctx); err != nil {
		t.Fatalf("upgrade legacy non-partial index: %v", err)
	}

	var predicate sql.NullString
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`
SELECT pg_get_expr(index_entry.indpred, index_entry.indrelid)
FROM pg_index index_entry
JOIN pg_class index_class ON index_class.oid = index_entry.indexrelid
WHERE index_class.oid = to_regclass($1)`), schema+".chunks_bm25").Scan(&predicate); err != nil {
		t.Fatal(err)
	}
	if !predicate.Valid || !strings.Contains(predicate.String, "status = 'closed'") {
		t.Fatalf("expected closed-only partial index after migration, got %q", predicate.String)
	}
}

// TestZ5cRetryLimitToDeadLetter asserts that an event failing 5 times reaches dead_letter.
func TestZ5cRetryLimitToDeadLetter(t *testing.T) {
	_, db, schema := openWorkerIntegrationStore(t, "z5c_dlq")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	channelID := "z5c_channel"
	senderID := "z5c_sender"
	populateFixtureConversation(t, db, schema, channelID, senderID, 1)
	msgID := fmt.Sprintf("%s:msg_0", channelID)

	// Insert row with attempt_count = 5
	var eventID int64
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal, status, attempt_count)
VALUES ($1, 1, 'processing', 5) RETURNING event_id`, schema), msgID).Scan(&eventID); err != nil {
		t.Fatal(err)
	}

	// Call Reschedule on store
	databaseURL := schemaURL(t, env("DATABASE_URL", "postgres://vsf:vsf_local_only@localhost:5433/vsf?sslmode=disable"), schema)
	store, err := service.Open(ctx, databaseURL, service.Config{
		DemoViewerID:   "z5c_viewer",
		DemoChannelID:  "z5c_channel",
		DraftMediaTTL:  time.Hour,
		MinIOEndpoint:  env("MINIO_ENDPOINT", "http://localhost:9000"),
		MinIOAccessKey: env("MINIO_ROOT_USER", "vsf_minio"),
		MinIOSecretKey: env("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"),
		MinIOBucket:    env("MINIO_BUCKET", "vsf-media"),
	})
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()

	if err := store.RescheduleIndex(ctx, eventID, fmt.Errorf("persistent error")); err != nil {
		t.Fatal(err)
	}

	var status string
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT status FROM %s.outbox WHERE event_id = $1`, schema), eventID).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != "dead_letter" {
		t.Fatalf("expected status 'dead_letter', got '%s'", status)
	}
}

// TestZ5dPositiveControlDoneAndNoop asserts that a clean event reaches done
// and subsequent ProcessOne on empty queue returns IsNoIndexWork.
func TestZ5dPositiveControlDoneAndNoop(t *testing.T) {
	model := client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090"))
	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	store, db, schema := openWorkerIntegrationStore(t, "z5d_done")
	channelID := "z5d_channel"
	senderID := "z5d_sender"
	populateFixtureConversation(t, db, schema, channelID, senderID, 1)

	msgID := fmt.Sprintf("%s:msg_0", channelID)
	if _, err := db.ExecContext(ctx, fmt.Sprintf(`
INSERT INTO %s.outbox (message_id, feed_ordinal, status)
VALUES ($1, 1, 'pending')`, schema), msgID); err != nil {
		t.Fatal(err)
	}

	worker := service.NewIndexWorker(store, model, 20, 400)
	if err := worker.ProcessOne(ctx); err != nil {
		t.Fatalf("ProcessOne failed: %v", err)
	}

	var status string
	if err := db.QueryRowContext(ctx, fmt.Sprintf(`SELECT status FROM %s.outbox WHERE message_id = $1`, schema), msgID).Scan(&status); err != nil {
		t.Fatal(err)
	}
	if status != "done" {
		t.Fatalf("expected status 'done', got '%s'", status)
	}

	// No-op check on empty queue
	err := worker.ProcessOne(ctx)
	if !service.IsNoIndexWork(err) {
		t.Fatalf("expected IsNoIndexWork error on empty queue, got %v", err)
	}
}
