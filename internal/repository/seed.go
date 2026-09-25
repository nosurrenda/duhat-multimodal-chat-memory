package repository

import (
	"bufio"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
)

type seedChannel struct {
	ChannelID      string `json:"channel_id"`
	SourceFamily   string `json:"source_family"`
	SourceDialogue string `json:"source_dialogue"`
}
type seedSender struct {
	SenderID    string `json:"sender_id"`
	DisplayName string `json:"display_name"`
}
type seedMembership struct {
	CallerID  string     `json:"caller_id"`
	ChannelID string     `json:"channel_id"`
	Role      string     `json:"role"`
	ValidFrom time.Time  `json:"valid_from"`
	ValidTo   *time.Time `json:"valid_to"`
}

type seedMessage struct {
	ChannelID         string `json:"channel_id"`
	ChronologicalRank int64  `json:"chronological_rank"`
	MessageID         string `json:"message_id"`
	SenderID          string `json:"sender_id"`
	SessionIndex      int    `json:"session_index"`
	SourceSessionID   string `json:"source_session_id"`
	SourceTurnIndex   int64  `json:"source_turn_index"`
	Text              string `json:"text"`
	Timestamp         string `json:"timestamp"`
	TimestampSource   string `json:"timestamp_source"`
	TemporalConflict  bool   `json:"temporal_order_conflict"`
}
type seedMedia struct {
	ChannelID        string `json:"channel_id"`
	ContentSHA256    string `json:"content_sha256"`
	MediaID          string `json:"media_id"`
	ParentMessageID  string `json:"parent_message_id"`
	StorageObjectRef string `json:"storage_object_ref"`
}

// SeedMessages imports every canonical turn into the one configured demo chat.
// Source identifiers remain provenance only; chronological_rank is the stable
// append order and must never be repurposed as a retrieval scope.
func (s *Store) SeedMessages(ctx context.Context, processedDir string) error {
	path := filepath.Join(processedDir, "messages.jsonl")
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// A demo may already contain live messages. Their feed ordinal is allocated
	// after the canonical range, so make it their channel ordinal too before the
	// canonical 0..N stream is attached to this same chat.
	if _, err := tx.ExecContext(ctx, `UPDATE messages SET ordinal = feed_ordinal
WHERE channel_id = $1 AND source_session_id = 'live'`, s.cfg.DemoChannelID); err != nil {
		return err
	}
	scanner := bufio.NewScanner(file)
	buffer := make([]byte, 0, 256*1024)
	scanner.Buffer(buffer, 4*1024*1024)
	maxRank := int64(-1)
	items := make([]seedMessage, 0)
	for scanner.Scan() {
		var item seedMessage
		if err := json.Unmarshal(scanner.Bytes(), &item); err != nil {
			return err
		}
		items = append(items, item)
		if item.ChronologicalRank > maxRank {
			maxRank = item.ChronologicalRank
		}
	}
	if err := scanner.Err(); err != nil {
		return err
	}
	sort.Slice(items, func(left, right int) bool {
		if items[left].ChronologicalRank != items[right].ChronologicalRank {
			return items[left].ChronologicalRank < items[right].ChronologicalRank
		}
		return items[left].MessageID < items[right].MessageID
	})
	for _, item := range items {
		_, err := tx.ExecContext(ctx, `INSERT INTO messages (message_id, channel_id, sender_id, body, occurred_at, timestamp_source, source_turn_index, session_index, source_session_id, chronological_rank, temporal_order_conflict, ordinal, feed_ordinal)
VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
ON CONFLICT (message_id) DO UPDATE SET channel_id = EXCLUDED.channel_id, ordinal = EXCLUDED.ordinal, feed_ordinal = EXCLUDED.feed_ordinal`, item.MessageID, s.cfg.DemoChannelID, nullIfEmpty(item.SenderID), item.Text, item.Timestamp, item.TimestampSource, item.SourceTurnIndex, item.SessionIndex, item.SourceSessionID, item.ChronologicalRank, item.TemporalConflict, item.ChronologicalRank, item.ChronologicalRank)
		if err != nil {
			return err
		}
	}
	if err := scanJSONL(filepath.Join(processedDir, "media.jsonl"), func(line []byte) error {
		var item seedMedia
		if err := json.Unmarshal(line, &item); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO media (media_id, parent_message_id, channel_id, content_sha256, storage_object_ref)
VALUES ($1,$2,$3,$4,$5) ON CONFLICT (media_id) DO UPDATE SET channel_id = EXCLUDED.channel_id`, item.MediaID, item.ParentMessageID, s.cfg.DemoChannelID, item.ContentSHA256, item.StorageObjectRef)
		return err
	}); err != nil {
		return err
	}
	if maxRank < 0 {
		return fmt.Errorf("no seed messages found in %s", path)
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO feed_state (singleton, last_feed_ordinal) VALUES (true, $1)
ON CONFLICT (singleton) DO UPDATE SET last_feed_ordinal = GREATEST(feed_state.last_feed_ordinal, EXCLUDED.last_feed_ordinal)`, maxRank)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `UPDATE channels c SET last_ordinal = source.maximum FROM (
	  SELECT channel_id, max(ordinal) AS maximum FROM messages GROUP BY channel_id
) source WHERE c.channel_id = source.channel_id AND c.last_ordinal < source.maximum`)
	if err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	return s.SeedObjects(ctx, processedDir)
}

// SeedObjects copies byte-verified canonical media into MinIO under the same
// sha256/<digest> reference stored in PostgreSQL. Existing objects are skipped.
func (s *Store) SeedObjects(ctx context.Context, processedDir string) error {
	objectsDir := filepath.Join(processedDir, "objects")
	entries, err := os.ReadDir(objectsDir)
	if err != nil {
		return err
	}
	for _, entry := range entries {
		if entry.IsDir() || strings.HasPrefix(entry.Name(), ".") {
			continue
		}
		digest := entry.Name()
		if len(digest) != 64 {
			return fmt.Errorf("canonical object %q does not have a sha256 filename", digest)
		}
		path := filepath.Join(objectsDir, digest)
		file, err := os.Open(path)
		if err != nil {
			return err
		}
		hash := sha256.New()
		_, copyErr := io.Copy(hash, file)
		if copyErr == nil && hex.EncodeToString(hash.Sum(nil)) != digest {
			copyErr = fmt.Errorf("canonical object %q content hash does not match filename", digest)
		}
		if copyErr != nil {
			file.Close()
			return copyErr
		}
		key := "sha256/" + digest
		_, statErr := s.objects.StatObject(ctx, s.cfg.MinIOBucket, key, minio.StatObjectOptions{})
		if statErr == nil {
			file.Close()
			continue
		}
		if _, err := file.Seek(0, io.SeekStart); err != nil {
			file.Close()
			return err
		}
		info, err := file.Stat()
		if err == nil {
			_, err = s.objects.PutObject(ctx, s.cfg.MinIOBucket, key, file, info.Size(), minio.PutObjectOptions{})
			if err != nil {
				err = fmt.Errorf("upload canonical object %q: %w", key, err)
			}
		}
		closeErr := file.Close()
		if err != nil {
			return err
		}
		if closeErr != nil {
			return closeErr
		}
	}
	return nil
}

func nullIfEmpty(value string) any {
	if value == "" {
		return nil
	}
	return value
}

// SeedIdentity imports the canonical identity tables and provisions the fixed local demo principal.
func (s *Store) SeedIdentity(ctx context.Context, processedDir string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := scanJSONL(filepath.Join(processedDir, "channels.jsonl"), func(line []byte) error {
		var row seedChannel
		if err := json.Unmarshal(line, &row); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO channels (channel_id, source_family, source_dialogue) VALUES ($1,$2,$3) ON CONFLICT (channel_id) DO NOTHING`, row.ChannelID, row.SourceFamily, row.SourceDialogue)
		return err
	}); err != nil {
		return err
	}
	if err := scanJSONL(filepath.Join(processedDir, "senders.jsonl"), func(line []byte) error {
		var row seedSender
		if err := json.Unmarshal(line, &row); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO senders (sender_id, display_name, is_corpus) VALUES ($1,$2,true) ON CONFLICT (sender_id) DO NOTHING`, row.SenderID, row.DisplayName)
		return err
	}); err != nil {
		return err
	}
	if err := scanJSONL(filepath.Join(processedDir, "memberships.jsonl"), func(line []byte) error {
		var row seedMembership
		if err := json.Unmarshal(line, &row); err != nil {
			return err
		}
		_, err := tx.ExecContext(ctx, `INSERT INTO channel_memberships (caller_id, channel_id, role, valid_from, valid_to) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING`, row.CallerID, row.ChannelID, row.Role, row.ValidFrom, row.ValidTo)
		return err
	}); err != nil {
		return err
	}
	// The demo identity is deliberately non-corpus and owns the only live-demo channel.
	_, err = tx.ExecContext(ctx, `INSERT INTO senders (sender_id, display_name, is_corpus) VALUES ($1, 'Demo', false) ON CONFLICT (sender_id) DO NOTHING`, s.cfg.DemoViewerID)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO channels (channel_id, source_family, source_dialogue) VALUES ($1, 'live', 'demo') ON CONFLICT (channel_id) DO NOTHING`, s.cfg.DemoChannelID)
	if err != nil {
		return err
	}
	_, err = tx.ExecContext(ctx, `INSERT INTO channel_memberships (caller_id, channel_id, role, valid_from)
SELECT $1, c.channel_id, 'owner', now() FROM channels c
WHERE NOT EXISTS (
  SELECT 1 FROM channel_memberships m
  WHERE m.caller_id = $1 AND m.channel_id = c.channel_id AND (m.valid_to IS NULL OR m.valid_to > now())
)`, s.cfg.DemoViewerID)
	if err != nil {
		return err
	}
	return tx.Commit()
}

func scanJSONL(path string, consume func([]byte) error) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	scanner := bufio.NewScanner(file)
	scanner.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for scanner.Scan() {
		if err := consume(scanner.Bytes()); err != nil {
			return err
		}
	}
	return scanner.Err()
}
