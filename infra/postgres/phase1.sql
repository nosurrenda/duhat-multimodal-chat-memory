-- Phase 1 canonical corpus tables. Derived indexes deliberately remain out of this migration.
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
  sender_id TEXT REFERENCES senders(sender_id),
  body TEXT NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  timestamp_source TEXT NOT NULL CHECK (timestamp_source = 'synthetic'),
  source_turn_index INTEGER NOT NULL,
  session_index INTEGER NOT NULL,
  source_session_id TEXT NOT NULL,
  chronological_rank INTEGER NOT NULL,
  temporal_order_conflict BOOLEAN NOT NULL,
  reply_to_message_id TEXT NULL,
  thread_id TEXT NULL
);

-- Existing Phase 1 databases predate D15; preserve loader compatibility before truncation.
ALTER TABLE messages
  ADD COLUMN IF NOT EXISTS temporal_order_conflict BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS media (
  media_id TEXT PRIMARY KEY,
  parent_message_id TEXT NOT NULL REFERENCES messages(message_id),
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
