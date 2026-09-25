// Package repository owns persistence and membership-scoped data access.
package repository

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"strings"
	"time"

	_ "github.com/jackc/pgx/v5/stdlib"
	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

type Config struct {
	DemoViewerID   string
	DemoChannelID  string
	DraftMediaTTL  time.Duration
	MinIOEndpoint  string
	MinIOAccessKey string
	MinIOSecretKey string
	MinIOBucket    string
}

type Store struct {
	db      *sql.DB
	cfg     Config
	objects *minio.Client
}

type Attachment struct {
	ID          string `json:"id"`
	ContentType string `json:"content_type"`
}

type Message struct {
	ID          string       `json:"id"`
	SenderID    string       `json:"sender_id"`
	SenderName  string       `json:"sender_name"`
	Text        string       `json:"text"`
	OccurredAt  time.Time    `json:"occurred_at"`
	FeedOrdinal int64        `json:"feed_ordinal"`
	Attachments []Attachment `json:"attachments"`
}

type Event struct {
	ID          int64   `json:"id"`
	FeedOrdinal int64   `json:"feed_ordinal"`
	Message     Message `json:"message"`
}

// SendRequest separates the general authorization boundary from the fixed demo route.
type SendRequest struct {
	CallerID  string
	ChannelID string
	Text      string
	MediaIDs  []string
}

// Draft describes an unbound upload. Its bytes are never exposed until binding.
type Draft struct {
	ID          string `json:"id"`
	ContentType string `json:"content_type"`
}

// Open connects to PostgreSQL and verifies the fixed demo identity before serving requests.
func Open(ctx context.Context, databaseURL string, cfg Config) (*Store, error) {
	if cfg.DemoViewerID == "" || cfg.DemoChannelID == "" {
		return nil, errors.New("demo viewer and channel configuration are required")
	}
	if cfg.DraftMediaTTL <= 0 {
		return nil, errors.New("draft media TTL must be positive")
	}
	if cfg.MinIOEndpoint == "" || cfg.MinIOAccessKey == "" || cfg.MinIOSecretKey == "" || cfg.MinIOBucket == "" {
		return nil, errors.New("MinIO configuration is required")
	}
	endpoint := strings.TrimPrefix(strings.TrimPrefix(cfg.MinIOEndpoint, "http://"), "https://")
	objects, err := minio.New(endpoint, &minio.Options{Creds: credentials.NewStaticV4(cfg.MinIOAccessKey, cfg.MinIOSecretKey, ""), Secure: strings.HasPrefix(cfg.MinIOEndpoint, "https://")})
	if err != nil {
		return nil, err
	}
	exists, err := objects.BucketExists(ctx, cfg.MinIOBucket)
	if err != nil {
		return nil, fmt.Errorf("MinIO bucket access %q: %w", cfg.MinIOBucket, err)
	}
	if !exists {
		if err := objects.MakeBucket(ctx, cfg.MinIOBucket, minio.MakeBucketOptions{}); err != nil {
			return nil, err
		}
	}
	db, err := sql.Open("pgx", databaseURL)
	if err != nil {
		return nil, err
	}
	if err := db.PingContext(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return &Store{db: db, cfg: cfg, objects: objects}, nil
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) DemoViewerID() string { return s.cfg.DemoViewerID }

// Migrate adds application tables without rewriting the canonical Phase 1 rows.
func (s *Store) Migrate(ctx context.Context) error {
	_, err := s.db.ExecContext(ctx, `
CREATE TABLE IF NOT EXISTS channels (
  channel_id TEXT PRIMARY KEY,
  source_family TEXT NOT NULL,
  source_dialogue TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS senders (
  sender_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS messages (
  message_id TEXT PRIMARY KEY,
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  sender_id TEXT NULL REFERENCES senders(sender_id),
  body TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  timestamp_source TEXT NOT NULL,
  source_turn_index INTEGER NOT NULL,
  session_index INTEGER NOT NULL,
  source_session_id TEXT NOT NULL,
  chronological_rank INTEGER NOT NULL,
  temporal_order_conflict BOOLEAN NOT NULL DEFAULT false,
  reply_to_message_id TEXT NULL,
  thread_id TEXT NULL
);
CREATE TABLE IF NOT EXISTS media (
  media_id TEXT PRIMARY KEY,
  parent_message_id TEXT NULL REFERENCES messages(message_id),
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  content_sha256 TEXT NOT NULL,
  storage_object_ref TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS channel_memberships (
  caller_id TEXT NOT NULL,
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  role TEXT NOT NULL,
  valid_from TIMESTAMPTZ NOT NULL,
  valid_to TIMESTAMPTZ NULL,
  PRIMARY KEY (caller_id, channel_id, valid_from)
);
ALTER TABLE senders ADD COLUMN IF NOT EXISTS is_corpus BOOLEAN NOT NULL DEFAULT true;
ALTER TABLE messages ALTER COLUMN sender_id DROP NOT NULL;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS feed_ordinal BIGINT;
ALTER TABLE messages ADD COLUMN IF NOT EXISTS ordinal BIGINT;
ALTER TABLE messages DROP CONSTRAINT IF EXISTS messages_timestamp_source_check;
ALTER TABLE media ADD COLUMN IF NOT EXISTS uploader_id TEXT;
ALTER TABLE media ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ;
ALTER TABLE media ADD COLUMN IF NOT EXISTS content_type TEXT NOT NULL DEFAULT 'application/octet-stream';
ALTER TABLE media ALTER COLUMN parent_message_id DROP NOT NULL;
ALTER TABLE channels ADD COLUMN IF NOT EXISTS last_ordinal BIGINT NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS feed_state (
  singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton),
  last_feed_ordinal BIGINT NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox (
  event_id BIGSERIAL PRIMARY KEY,
  message_id TEXT NOT NULL UNIQUE REFERENCES messages(message_id),
  feed_ordinal BIGINT NOT NULL UNIQUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  processed_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  first_message_id TEXT PRIMARY KEY REFERENCES messages(message_id),
  chunk_id TEXT NOT NULL UNIQUE,
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  chunk_index INTEGER NOT NULL,
  message_ids TEXT[] NOT NULL,
  media_ids TEXT[] NOT NULL,
  day DATE NOT NULL,
  text TEXT NOT NULL,
  token_count INTEGER NOT NULL,
  last_feed_ordinal BIGINT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  closed_at TIMESTAMPTZ NULL
);
CREATE TABLE IF NOT EXISTS chunk_embeddings (
  first_message_id TEXT PRIMARY KEY REFERENCES chunks(first_message_id),
  channel_id TEXT NOT NULL REFERENCES channels(channel_id),
  embedding public.vector(1024) NOT NULL,
  model_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS media_embeddings (
  media_id TEXT PRIMARY KEY REFERENCES media(media_id),
  embedding public.vector(768) NOT NULL,
  model_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'pending';
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS last_error TEXT NULL;
ALTER TABLE outbox ADD COLUMN IF NOT EXISTS lexical_chunk_id TEXT NULL;
-- Legacy development seeds used now() for the demo membership. Keep one active
-- grant before adding the invariant that prevents a future re-seed from doing so.
DELETE FROM channel_memberships duplicate
USING channel_memberships retained
WHERE duplicate.caller_id = retained.caller_id
  AND duplicate.channel_id = retained.channel_id
  AND duplicate.valid_to IS NULL AND retained.valid_to IS NULL
  AND duplicate.valid_from > retained.valid_from;
CREATE UNIQUE INDEX IF NOT EXISTS messages_feed_ordinal_key ON messages(feed_ordinal);
CREATE UNIQUE INDEX IF NOT EXISTS messages_channel_ordinal_key ON messages(channel_id, ordinal);
CREATE UNIQUE INDEX IF NOT EXISTS channel_memberships_one_open_grant_key
  ON channel_memberships(caller_id, channel_id) WHERE valid_to IS NULL;
CREATE INDEX IF NOT EXISTS messages_feed_ordinal_idx ON messages(feed_ordinal);
CREATE INDEX IF NOT EXISTS media_unbound_expiry_idx ON media(created_at) WHERE parent_message_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS chunks_one_open_per_channel ON chunks(channel_id) WHERE status = 'open';
CREATE INDEX IF NOT EXISTS chunk_embeddings_hnsw ON chunk_embeddings USING hnsw (embedding public.vector_cosine_ops);
CREATE INDEX IF NOT EXISTS media_embeddings_hnsw ON media_embeddings USING hnsw (embedding public.vector_cosine_ops);
`)
	return err
}

// ValidateDemoConfig fails closed when the fixed UI identity cannot see the whole demo corpus.
func (s *Store) ValidateDemoConfig(ctx context.Context) error {
	var viewerExists bool
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM senders WHERE sender_id = $1)`, s.cfg.DemoViewerID).Scan(&viewerExists); err != nil || !viewerExists {
		if err != nil {
			return err
		}
		return fmt.Errorf("demo viewer %q does not exist", s.cfg.DemoViewerID)
	}
	var validDemoChannel bool
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(
  SELECT 1 FROM channels c
  WHERE c.channel_id = $1 AND c.source_family = 'live'
)`, s.cfg.DemoChannelID).Scan(&validDemoChannel); err != nil || !validDemoChannel {
		if err != nil {
			return err
		}
		return fmt.Errorf("demo channel %q must be a real non-seeded live channel", s.cfg.DemoChannelID)
	}
	var missing int
	err := s.db.QueryRowContext(ctx, `
SELECT count(*) FROM channels c
WHERE NOT EXISTS (
  SELECT 1 FROM channel_memberships m
  WHERE m.caller_id = $1 AND m.channel_id = c.channel_id
    AND (m.valid_to IS NULL OR m.valid_to > now())
)`, s.cfg.DemoViewerID).Scan(&missing)
	if err != nil {
		return err
	}
	if missing != 0 {
		return fmt.Errorf("demo viewer is missing %d channel memberships", missing)
	}
	return nil
}

func (s *Store) Feed(ctx context.Context, after int64, limit int) ([]Message, error) {
	return s.FeedFor(ctx, s.cfg.DemoViewerID, after, limit)
}

// FeedFor applies the same default-deny membership check used by general callers.
func (s *Store) FeedFor(ctx context.Context, callerID string, after int64, limit int) ([]Message, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
SELECT m.message_id, COALESCE(m.sender_id, 'unknown'), COALESCE(s.display_name, 'Unknown'), m.body, m.occurred_at, m.feed_ordinal
FROM messages m
LEFT JOIN senders s ON s.sender_id = m.sender_id
WHERE EXISTS (
  SELECT 1 FROM channel_memberships membership
  WHERE membership.caller_id = $1 AND membership.channel_id = m.channel_id
    AND (membership.valid_to IS NULL OR membership.valid_to > now())
)
  AND m.feed_ordinal > $2
ORDER BY m.feed_ordinal, m.message_id
LIMIT $3`, callerID, after, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	messages := make([]Message, 0)
	for rows.Next() {
		var message Message
		if err := rows.Scan(&message.ID, &message.SenderID, &message.SenderName, &message.Text, &message.OccurredAt, &message.FeedOrdinal); err != nil {
			return nil, err
		}
		attachments, err := s.attachments(ctx, message.ID)
		if err != nil {
			return nil, err
		}
		message.Attachments = attachments
		messages = append(messages, message)
	}
	return messages, rows.Err()
}

// FeedBefore returns the newest accessible page strictly before a cursor while
// preserving ascending feed order for callers that prepend it to their view.
func (s *Store) FeedBefore(ctx context.Context, callerID string, before int64, limit int) ([]Message, error) {
	if limit <= 0 || limit > 500 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx, `
SELECT m.message_id, COALESCE(m.sender_id, 'unknown'), COALESCE(s.display_name, 'Unknown'), m.body, m.occurred_at, m.feed_ordinal
FROM messages m
LEFT JOIN senders s ON s.sender_id = m.sender_id
WHERE EXISTS (
  SELECT 1 FROM channel_memberships membership
  WHERE membership.caller_id = $1 AND membership.channel_id = m.channel_id
    AND (membership.valid_to IS NULL OR membership.valid_to > now())
)
  AND m.feed_ordinal < $2
ORDER BY m.feed_ordinal DESC, m.message_id DESC
LIMIT $3`, callerID, before, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	messages := make([]Message, 0)
	for rows.Next() {
		var message Message
		if err := rows.Scan(&message.ID, &message.SenderID, &message.SenderName, &message.Text, &message.OccurredAt, &message.FeedOrdinal); err != nil {
			return nil, err
		}
		attachments, err := s.attachments(ctx, message.ID)
		if err != nil {
			return nil, err
		}
		message.Attachments = attachments
		messages = append(messages, message)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for left, right := 0, len(messages)-1; left < right; left, right = left+1, right-1 {
		messages[left], messages[right] = messages[right], messages[left]
	}
	return messages, nil
}

func (s *Store) Send(ctx context.Context, request SendRequest) (Event, error) {
	if strings.TrimSpace(request.Text) == "" {
		return Event{}, errors.New("message text is required")
	}
	if request.CallerID == "" || request.ChannelID == "" {
		return Event{}, errors.New("caller and channel are required")
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return Event{}, err
	}
	defer tx.Rollback()
	var allowed bool
	if err := tx.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM channel_memberships WHERE caller_id = $1 AND channel_id = $2 AND (valid_to IS NULL OR valid_to > now()))`, request.CallerID, request.ChannelID).Scan(&allowed); err != nil || !allowed {
		if err != nil {
			return Event{}, err
		}
		return Event{}, errors.New("caller is not an active channel member")
	}
	var channelOrdinal, feedOrdinal int64
	if err := tx.QueryRowContext(ctx, `UPDATE channels SET last_ordinal = last_ordinal + 1 WHERE channel_id = $1 RETURNING last_ordinal`, request.ChannelID).Scan(&channelOrdinal); err != nil {
		return Event{}, err
	}
	if err := tx.QueryRowContext(ctx, `UPDATE feed_state SET last_feed_ordinal = last_feed_ordinal + 1 WHERE singleton = true RETURNING last_feed_ordinal`).Scan(&feedOrdinal); err != nil {
		return Event{}, err
	}
	now := time.Now().UTC()
	messageID := fmt.Sprintf("%s:live:%d", request.ChannelID, feedOrdinal)
	if _, err := tx.ExecContext(ctx, `INSERT INTO messages (message_id, channel_id, sender_id, body, occurred_at, timestamp_source, source_turn_index, session_index, source_session_id, chronological_rank, temporal_order_conflict, ordinal, feed_ordinal)
VALUES ($1, $2, $3, $4, $5, 'runtime', $6, 0, 'live', -1, false, $8, $7)`, messageID, request.ChannelID, request.CallerID, request.Text, now, channelOrdinal, feedOrdinal, channelOrdinal); err != nil {
		return Event{}, err
	}
	if err := s.bindDrafts(ctx, tx, request, messageID); err != nil {
		return Event{}, err
	}
	var eventID int64
	if err := tx.QueryRowContext(ctx, `INSERT INTO outbox (message_id, feed_ordinal) VALUES ($1, $2) RETURNING event_id`, messageID, feedOrdinal).Scan(&eventID); err != nil {
		return Event{}, err
	}
	if err := tx.Commit(); err != nil {
		return Event{}, err
	}
	attachments, err := s.attachments(ctx, messageID)
	if err != nil {
		return Event{}, err
	}
	return Event{ID: eventID, FeedOrdinal: feedOrdinal, Message: Message{ID: messageID, SenderID: request.CallerID, SenderName: request.CallerID, Text: request.Text, OccurredAt: now, FeedOrdinal: feedOrdinal, Attachments: attachments}}, nil
}

// SendDemo is intentionally the only convenience wrapper for the browser demo route.
func (s *Store) SendDemo(ctx context.Context, text string, mediaIDs []string) (Event, error) {
	return s.Send(ctx, SendRequest{CallerID: s.cfg.DemoViewerID, ChannelID: s.cfg.DemoChannelID, Text: text, MediaIDs: mediaIDs})
}

// EventsAfter is also the SSE resume source. Scope is re-evaluated for every row.
func (s *Store) EventsAfter(ctx context.Context, callerID string, afterEventID int64) ([]Event, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT o.event_id, o.feed_ordinal, m.message_id, COALESCE(m.sender_id, 'unknown'),
  COALESCE(sender.display_name, 'Unknown'), m.body, m.occurred_at
FROM outbox o JOIN messages m ON m.message_id=o.message_id
LEFT JOIN senders sender ON sender.sender_id=m.sender_id
WHERE o.event_id > $1 AND EXISTS (
  SELECT 1 FROM channel_memberships membership
  WHERE membership.caller_id=$2 AND membership.channel_id=m.channel_id
    AND (membership.valid_to IS NULL OR membership.valid_to > now())
)
ORDER BY o.event_id`, afterEventID, callerID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Event
	for rows.Next() {
		var event Event
		if err := rows.Scan(&event.ID, &event.FeedOrdinal, &event.Message.ID, &event.Message.SenderID, &event.Message.SenderName, &event.Message.Text, &event.Message.OccurredAt); err != nil {
			return nil, err
		}
		event.Message.FeedOrdinal = event.FeedOrdinal
		attachments, err := s.attachments(ctx, event.Message.ID)
		if err != nil {
			return nil, err
		}
		event.Message.Attachments = attachments
		result = append(result, event)
	}
	return result, rows.Err()
}

// EventVisible rechecks scope at the instant an SSE event is delivered.
func (s *Store) EventVisible(ctx context.Context, callerID string, eventID int64) (bool, error) {
	var visible bool
	err := s.db.QueryRowContext(ctx, `SELECT EXISTS(
 SELECT 1 FROM outbox o JOIN messages m ON m.message_id=o.message_id
 WHERE o.event_id=$1 AND EXISTS (
   SELECT 1 FROM channel_memberships membership WHERE membership.caller_id=$2
   AND membership.channel_id=m.channel_id AND (membership.valid_to IS NULL OR membership.valid_to > now())
 ))`, eventID, callerID).Scan(&visible)
	return visible, err
}

// CreateDraft persists content before composing; ownership is checked again during binding.
func (s *Store) CreateDraft(ctx context.Context, callerID, channelID, contentType string, content []byte) (Draft, error) {
	if callerID == "" || channelID == "" || len(content) == 0 {
		return Draft{}, errors.New("caller, channel, and media bytes are required")
	}
	var allowed bool
	if err := s.db.QueryRowContext(ctx, `SELECT EXISTS(SELECT 1 FROM channel_memberships WHERE caller_id=$1 AND channel_id=$2 AND (valid_to IS NULL OR valid_to > now()))`, callerID, channelID).Scan(&allowed); err != nil || !allowed {
		if err != nil {
			return Draft{}, err
		}
		return Draft{}, errors.New("caller is not an active channel member")
	}
	id, err := randomID()
	if err != nil {
		return Draft{}, err
	}
	digest := sha256.Sum256(content)
	ref := "drafts/" + id
	if _, err := s.objects.PutObject(ctx, s.cfg.MinIOBucket, ref, bytes.NewReader(content), int64(len(content)), minio.PutObjectOptions{ContentType: contentType}); err != nil {
		return Draft{}, err
	}
	_, err = s.db.ExecContext(ctx, `INSERT INTO media (media_id, parent_message_id, channel_id, content_sha256, storage_object_ref, uploader_id, created_at, content_type) VALUES ($1,NULL,$2,$3,$4,$5,now(),$6)`, id, channelID, hex.EncodeToString(digest[:]), ref, callerID, contentType)
	if err != nil {
		_ = s.objects.RemoveObject(ctx, s.cfg.MinIOBucket, ref, minio.RemoveObjectOptions{})
		return Draft{}, err
	}
	return Draft{ID: id, ContentType: contentType}, nil
}

func (s *Store) CreateDemoDraft(ctx context.Context, contentType string, content []byte) (Draft, error) {
	return s.CreateDraft(ctx, s.cfg.DemoViewerID, s.cfg.DemoChannelID, contentType, content)
}

func (s *Store) bindDrafts(ctx context.Context, tx *sql.Tx, request SendRequest, messageID string) error {
	for _, mediaID := range request.MediaIDs {
		result, err := tx.ExecContext(ctx, `UPDATE media SET parent_message_id=$1 WHERE media_id=$2 AND channel_id=$3 AND uploader_id=$4 AND parent_message_id IS NULL AND created_at + $5::interval > now()`, messageID, mediaID, request.ChannelID, request.CallerID, s.cfg.DraftMediaTTL.String())
		if err != nil {
			return err
		}
		updated, err := result.RowsAffected()
		if err != nil {
			return err
		}
		if updated != 1 {
			return fmt.Errorf("draft media %q is unavailable, expired, already bound, or not owned by caller", mediaID)
		}
	}
	return nil
}

func (s *Store) attachments(ctx context.Context, messageID string) ([]Attachment, error) {
	rows, err := s.db.QueryContext(ctx, `SELECT media_id, content_type FROM media WHERE parent_message_id=$1 ORDER BY media_id`, messageID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Attachment
	for rows.Next() {
		var item Attachment
		if err := rows.Scan(&item.ID, &item.ContentType); err != nil {
			return nil, err
		}
		result = append(result, item)
	}
	return result, rows.Err()
}

func (s *Store) GetMedia(ctx context.Context, callerID, mediaID string) (Media, error) {
	scoped := ScopedRepository{db: s.db, objects: s.objects, bucket: s.cfg.MinIOBucket}
	return scoped.GetMedia(ctx, callerID, mediaID)
}

func (s *Store) GetDemoMedia(ctx context.Context, mediaID string) (Media, error) {
	return s.GetMedia(ctx, s.cfg.DemoViewerID, mediaID)
}

func randomID() (string, error) {
	bytes := make([]byte, 16)
	if _, err := io.ReadFull(rand.Reader, bytes); err != nil {
		return "", err
	}
	return hex.EncodeToString(bytes), nil
}

func EncodeEvent(event Event) ([]byte, error) { return json.Marshal(event) }
