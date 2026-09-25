package repository

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"time"
)

// IndexMessage is the internal projection consumed by the one sequential
// Phase 3 worker. It is never exposed to HTTP callers.
type IndexMessage struct {
	EventID     int64
	MessageID   string
	ChannelID   string
	SenderID    string
	Text        string
	OccurredAt  time.Time
	FeedOrdinal int64
	MediaIDs    []string
}

// Chunk is durable state. Open chunks are directly readable later, but never indexed.
type Chunk struct {
	FirstMessageID  string
	ChunkID         string
	ChannelID       string
	ChunkIndex      int
	MessageIDs      []string
	MediaIDs        []string
	Day             time.Time
	Text            string
	TokenCount      int
	LastFeedOrdinal int64
	Status          string
}

// AssembledChunk is the pure result from the Python chunk service.
type AssembledChunk struct {
	Text       string
	MessageIDs []string
	MediaIDs   []string
	TokenCount int
}

// TextEmbedding is committed together with the immutable closed chunk.
type TextEmbedding struct {
	ModelVersion string
	Vector       []float32
}

// ImageEmbedding is committed independently from text so a vision failure
// cannot delay a message's acknowledgement or native lexical indexing.
type ImageEmbedding struct {
	ModelVersion string
	Vector       []float32
}

// PendingMedia is a bound live upload without a durable SigLIP vector.
type PendingMedia struct {
	MediaID          string
	StorageObjectRef string
}

// IsNoIndexWork keeps sql.ErrNoRows inside the repository ownership boundary.
func IsNoIndexWork(err error) bool { return errors.Is(err, sql.ErrNoRows) }

// NextIndexMessage resumes unfinished work before new events, matching the
// single-worker restart contract without introducing worker leases.
func (s *Store) NextIndexMessage(ctx context.Context) (IndexMessage, string, error) {
	for _, status := range []string{"processing", "pending"} {
		var message IndexMessage
		var mediaJSON []byte
		err := s.db.QueryRowContext(ctx, `
SELECT o.event_id, m.message_id, m.channel_id, COALESCE(m.sender_id, 'unknown'),
       m.body, m.occurred_at, m.feed_ordinal,
       COALESCE((SELECT json_agg(media_id ORDER BY media_id) FROM media WHERE parent_message_id=m.message_id), '[]'::json)
FROM outbox o
JOIN messages m ON m.message_id = o.message_id
WHERE o.status = $1 AND (o.next_attempt_at <= now() OR $1 <> 'pending')
ORDER BY o.event_id
LIMIT 1`, status).Scan(&message.EventID, &message.MessageID, &message.ChannelID, &message.SenderID, &message.Text, &message.OccurredAt, &message.FeedOrdinal, &mediaJSON)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return IndexMessage{}, "", err
		}
		if err := json.Unmarshal(mediaJSON, &message.MediaIDs); err != nil {
			return IndexMessage{}, "", err
		}
		if status == "pending" {
			if _, err := s.db.ExecContext(ctx, `UPDATE outbox SET status='processing', attempt_count=attempt_count+1 WHERE event_id=$1`, message.EventID); err != nil {
				return IndexMessage{}, "", err
			}
		}
		return message, status, nil
	}
	return IndexMessage{}, "", sql.ErrNoRows
}

// SeedIndexMessages resumes the one canonical demo stream without inserting
// historical rows into the live outbox (which would replay them as SSE events).
func (s *Store) SeedIndexMessages(ctx context.Context) ([]IndexMessage, error) {
	rows, err := s.db.QueryContext(ctx, `
SELECT m.message_id, m.channel_id, COALESCE(m.sender_id, 'unknown'), m.body,
       m.occurred_at, m.feed_ordinal,
       COALESCE((SELECT json_agg(media_id ORDER BY media_id) FROM media WHERE parent_message_id=m.message_id), '[]'::json)
FROM messages m
WHERE m.channel_id = $1
  AND NOT EXISTS (
    SELECT 1 FROM chunks c WHERE c.channel_id=m.channel_id
      AND c.last_feed_ordinal >= m.feed_ordinal
  )
ORDER BY m.feed_ordinal, m.message_id`, s.cfg.DemoChannelID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var messages []IndexMessage
	for rows.Next() {
		var message IndexMessage
		var mediaJSON []byte
		if err := rows.Scan(&message.MessageID, &message.ChannelID, &message.SenderID, &message.Text, &message.OccurredAt, &message.FeedOrdinal, &mediaJSON); err != nil {
			return nil, err
		}
		if err := json.Unmarshal(mediaJSON, &message.MediaIDs); err != nil {
			return nil, err
		}
		messages = append(messages, message)
	}
	return messages, rows.Err()
}

// OpenChunk obtains the only mutable chunk for a conversation.
func (s *Store) OpenChunk(ctx context.Context, channelID string) (*Chunk, error) {
	chunk := Chunk{}
	var messageJSON, mediaJSON []byte
	err := s.db.QueryRowContext(ctx, `
SELECT first_message_id, chunk_id, channel_id, chunk_index, to_json(message_ids), to_json(media_ids),
       day, text, token_count, last_feed_ordinal, status
FROM chunks WHERE channel_id=$1 AND status='open'`, channelID).Scan(
		&chunk.FirstMessageID, &chunk.ChunkID, &chunk.ChannelID, &chunk.ChunkIndex,
		&messageJSON, &mediaJSON, &chunk.Day, &chunk.Text, &chunk.TokenCount,
		&chunk.LastFeedOrdinal, &chunk.Status)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	if err := json.Unmarshal(messageJSON, &chunk.MessageIDs); err != nil {
		return nil, err
	}
	if err := json.Unmarshal(mediaJSON, &chunk.MediaIDs); err != nil {
		return nil, err
	}
	return &chunk, nil
}

// CompleteOpenAppend atomically records an ordinary append and its completion.
func (s *Store) CompleteOpenAppend(ctx context.Context, message IndexMessage, assembled AssembledChunk) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	var existingOrdinal int64
	err = tx.QueryRowContext(ctx, `SELECT last_feed_ordinal FROM chunks WHERE channel_id=$1 AND status='open'`, message.ChannelID).Scan(&existingOrdinal)
	if errors.Is(err, sql.ErrNoRows) {
		_, err = tx.ExecContext(ctx, `
INSERT INTO chunks (first_message_id, chunk_id, channel_id, chunk_index, message_ids, media_ids, day, text, token_count, last_feed_ordinal, status)
VALUES ($1,$2,$3,0,$4,$5,$6,$7,$8,$9,'open')`, message.MessageID, chunkID(message.ChannelID, assembled.MessageIDs), message.ChannelID, assembled.MessageIDs, assembled.MediaIDs, message.OccurredAt.UTC().Truncate(24*time.Hour), assembled.Text, assembled.TokenCount, message.FeedOrdinal)
	} else if err == nil && existingOrdinal < message.FeedOrdinal {
		_, err = tx.ExecContext(ctx, `
UPDATE chunks SET chunk_id=$1, message_ids=$2, media_ids=$3, text=$4, token_count=$5, last_feed_ordinal=$6
WHERE channel_id=$7 AND status='open'`, chunkID(message.ChannelID, assembled.MessageIDs), assembled.MessageIDs, assembled.MediaIDs, assembled.Text, assembled.TokenCount, message.FeedOrdinal, message.ChannelID)
	}
	if err != nil {
		return err
	}
	if message.EventID != 0 {
		if _, err = tx.ExecContext(ctx, `UPDATE outbox SET status='done', processed_at=now(), last_error=NULL WHERE event_id=$1`, message.EventID); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// CloseAndOpen atomically freezes the old chunk and opens the triggering message's new tail.
func (s *Store) CloseAndOpen(ctx context.Context, message IndexMessage, previous Chunk, next AssembledChunk, embedding TextEmbedding) error {
	if len(embedding.Vector) != 1024 {
		return fmt.Errorf("text embedding dimension is %d, want 1024", len(embedding.Vector))
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if _, err = tx.ExecContext(ctx, `UPDATE chunks SET status='closed', closed_at=now() WHERE first_message_id=$1 AND status='open'`, previous.FirstMessageID); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `INSERT INTO chunk_embeddings (first_message_id, channel_id, embedding, model_version) VALUES ($1,$2,$3::public.vector,$4) ON CONFLICT (first_message_id) DO NOTHING`, previous.FirstMessageID, previous.ChannelID, vectorLiteral(embedding.Vector), embedding.ModelVersion); err != nil {
		return err
	}
	if _, err = tx.ExecContext(ctx, `
INSERT INTO chunks (first_message_id, chunk_id, channel_id, chunk_index, message_ids, media_ids, day, text, token_count, last_feed_ordinal, status)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,'open')`, message.MessageID, chunkID(message.ChannelID, next.MessageIDs), message.ChannelID, previous.ChunkIndex+1, next.MessageIDs, next.MediaIDs, message.OccurredAt.UTC().Truncate(24*time.Hour), next.Text, next.TokenCount, message.FeedOrdinal); err != nil {
		return err
	}
	if message.EventID != 0 {
		// pg_search updates its index from this same committed row change, so there
		// is no external lexical publish state between durable steps.
		if _, err = tx.ExecContext(ctx, `UPDATE outbox SET status='done', processed_at=now(), last_error=NULL WHERE event_id=$1`, message.EventID); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// NextPendingMedia selects only live uploads. Seed media are imported from the
// frozen Phase 1.5 artifact and must never be recomputed by the runtime worker.
func (s *Store) NextPendingMedia(ctx context.Context) (PendingMedia, error) {
	media := PendingMedia{}
	err := s.db.QueryRowContext(ctx, `
SELECT m.media_id, m.storage_object_ref
FROM media m
WHERE m.parent_message_id IS NOT NULL AND m.uploader_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM media_embeddings e WHERE e.media_id=m.media_id)
ORDER BY m.created_at, m.media_id
LIMIT 1`).Scan(&media.MediaID, &media.StorageObjectRef)
	return media, err
}

// SaveMediaEmbedding is idempotent so the sequential worker can retry an image
// after an RPC interruption without duplicating its primary-keyed vector row.
func (s *Store) SaveMediaEmbedding(ctx context.Context, media PendingMedia, embedding ImageEmbedding) error {
	if len(embedding.Vector) != 768 {
		return fmt.Errorf("image embedding dimension is %d, want 768", len(embedding.Vector))
	}
	_, err := s.db.ExecContext(ctx, `
INSERT INTO media_embeddings (media_id, embedding, model_version)
VALUES ($1,$2::public.vector,$3)
ON CONFLICT (media_id) DO UPDATE SET embedding=EXCLUDED.embedding, model_version=EXCLUDED.model_version`,
		media.MediaID, vectorLiteral(embedding.Vector), embedding.ModelVersion)
	return err
}

// Reschedule preserves failed work for the same worker after a bounded backoff.
func (s *Store) Reschedule(ctx context.Context, eventID int64, cause error) error {
	_, err := s.db.ExecContext(ctx, `UPDATE outbox SET status=CASE WHEN attempt_count >= 5 THEN 'dead_letter' ELSE 'pending' END, next_attempt_at=now() + interval '2 seconds', last_error=$2 WHERE event_id=$1`, eventID, cause.Error())
	return err
}

func chunkID(channelID string, messageIDs []string) string {
	if len(messageIDs) == 0 {
		return channelID + ":empty"
	}
	// Canonical message ids already start with the channel id. Remove that common
	// prefix before composing the display id so it remains readable and stable.
	prefix := channelID + ":"
	first := strings.TrimPrefix(messageIDs[0], prefix)
	last := strings.TrimPrefix(messageIDs[len(messageIDs)-1], prefix)
	return channelID + ":" + first + "-" + last
}

func vectorLiteral(vector []float32) string {
	values := make([]string, len(vector))
	for index, value := range vector {
		values[index] = fmt.Sprintf("%g", value)
	}
	return "[" + strings.Join(values, ",") + "]"
}
