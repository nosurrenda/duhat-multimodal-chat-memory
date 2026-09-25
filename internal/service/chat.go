// Package service applies application policy to repositories.
package service

import (
	"context"
	"encoding/json"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/repository"
)

type Config = repository.Config
type Attachment = repository.Attachment
type Message = repository.Message
type Event = repository.Event
type SendRequest = repository.SendRequest
type Draft = repository.Draft
type Media = repository.Media

// Store has no database handle: scope is the sole SQL and driver owner.
type Store struct{ repository *repository.Store }

func Open(ctx context.Context, databaseURL string, cfg Config) (*Store, error) {
	repository, err := repository.Open(ctx, databaseURL, cfg)
	if err != nil {
		return nil, err
	}
	return &Store{repository: repository}, nil
}

func (s *Store) Close() error                      { return s.repository.Close() }
func (s *Store) DemoViewerID() string              { return s.repository.DemoViewerID() }
func (s *Store) Migrate(ctx context.Context) error { return s.repository.Migrate(ctx) }
func (s *Store) ValidateDemoConfig(ctx context.Context) error {
	return s.repository.ValidateDemoConfig(ctx)
}
func (s *Store) SeedIdentity(ctx context.Context, processedDir string) error {
	return s.repository.SeedIdentity(ctx, processedDir)
}
func (s *Store) SeedMessages(ctx context.Context, processedDir string) error {
	return s.repository.SeedMessages(ctx, processedDir)
}
func (s *Store) Feed(ctx context.Context, after int64, limit int) ([]Message, error) {
	return s.repository.Feed(ctx, after, limit)
}
func (s *Store) FeedFor(ctx context.Context, callerID string, after int64, limit int) ([]Message, error) {
	return s.repository.FeedFor(ctx, callerID, after, limit)
}
func (s *Store) FeedBefore(ctx context.Context, callerID string, before int64, limit int) ([]Message, error) {
	return s.repository.FeedBefore(ctx, callerID, before, limit)
}
func (s *Store) Send(ctx context.Context, request SendRequest) (Event, error) {
	return s.repository.Send(ctx, request)
}
func (s *Store) SendDemo(ctx context.Context, text string, mediaIDs []string) (Event, error) {
	return s.repository.SendDemo(ctx, text, mediaIDs)
}
func (s *Store) CreateDraft(ctx context.Context, callerID, channelID, contentType string, content []byte) (Draft, error) {
	return s.repository.CreateDraft(ctx, callerID, channelID, contentType, content)
}
func (s *Store) CreateDemoDraft(ctx context.Context, contentType string, content []byte) (Draft, error) {
	return s.repository.CreateDemoDraft(ctx, contentType, content)
}
func (s *Store) EventsAfter(ctx context.Context, callerID string, lastEventID int64) ([]Event, error) {
	return s.repository.EventsAfter(ctx, callerID, lastEventID)
}
func (s *Store) EventVisible(ctx context.Context, callerID string, eventID int64) (bool, error) {
	return s.repository.EventVisible(ctx, callerID, eventID)
}
func (s *Store) GetDemoMedia(ctx context.Context, mediaID string) (Media, error) {
	return s.repository.GetDemoMedia(ctx, mediaID)
}
func EncodeEvent(event Event) ([]byte, error) { return json.Marshal(event) }
