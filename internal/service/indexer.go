package service

import (
	"context"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/repository"
)

type IndexMessage = repository.IndexMessage
type Chunk = repository.Chunk
type AssembledChunk = repository.AssembledChunk
type TextEmbedding = repository.TextEmbedding

func (s *Store) NextIndexMessage(ctx context.Context) (IndexMessage, string, error) {
	return s.repository.NextIndexMessage(ctx)
}
func (s *Store) SeedIndexMessages(ctx context.Context) ([]IndexMessage, error) {
	return s.repository.SeedIndexMessages(ctx)
}
func (s *Store) OpenChunk(ctx context.Context, channelID string) (*Chunk, error) {
	return s.repository.OpenChunk(ctx, channelID)
}
func (s *Store) LexicalChunkForEvent(ctx context.Context, eventID int64) (Chunk, error) {
	return s.repository.LexicalChunkForEvent(ctx, eventID)
}
func (s *Store) CompleteOpenAppend(ctx context.Context, message IndexMessage, assembled AssembledChunk) error {
	return s.repository.CompleteOpenAppend(ctx, message, assembled)
}
func (s *Store) CloseAndOpen(ctx context.Context, message IndexMessage, previous Chunk, next AssembledChunk, embedding TextEmbedding) error {
	return s.repository.CloseAndOpen(ctx, message, previous, next, embedding)
}
func (s *Store) MarkIndexed(ctx context.Context, eventID int64) error {
	return s.repository.MarkIndexed(ctx, eventID)
}
func (s *Store) RecordLexicalFailure(ctx context.Context, eventID int64, cause error) error {
	return s.repository.RecordLexicalFailure(ctx, eventID, cause)
}
func (s *Store) RescheduleIndex(ctx context.Context, eventID int64, cause error) error {
	return s.repository.Reschedule(ctx, eventID, cause)
}
func IsNoIndexWork(err error) bool { return repository.IsNoIndexWork(err) }
