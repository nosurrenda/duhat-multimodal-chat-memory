// Package repository is the Go default-deny repository boundary.
package repository

import (
	"context"
	"database/sql"
	"errors"
	"io"
	"strings"
	"time"

	"github.com/minio/minio-go/v7"
	"github.com/minio/minio-go/v7/pkg/credentials"
)

// Membership grants a caller access to a channel until an optional expiry.
type Membership struct {
	CallerID  string
	ChannelID string
	ValidTo   *time.Time
}

// Repository centralizes access checks before any storage-backed lookup is exposed.
type Repository struct{ memberships []Membership }

// Media is returned only after ScopedRepository has checked the media's channel.
type Media struct {
	ID              string
	ParentMessageID string
	ContentType     string
	Bytes           []byte
}

// ScopedRepository is the data-backed direct-by-ID boundary required by Z4d/Z4e.
type ScopedRepository struct {
	db      *sql.DB
	objects *minio.Client
	bucket  string
}

func New(memberships []Membership) Repository { return Repository{memberships: memberships} }

// AccessibleChannels returns an empty set for missing, unknown, or expired identities.
func (r Repository) AccessibleChannels(callerID string, now time.Time) map[string]struct{} {
	channels := make(map[string]struct{})
	if callerID == "" {
		return channels
	}
	for _, membership := range r.memberships {
		if membership.CallerID == callerID && (membership.ValidTo == nil || membership.ValidTo.After(now)) {
			channels[membership.ChannelID] = struct{}{}
		}
	}
	return channels
}

// Allows makes callers perform the scope decision before handing objects to a response or trace.
func (r Repository) Allows(callerID, channelID string, now time.Time) bool {
	_, allowed := r.AccessibleChannels(callerID, now)[channelID]
	return allowed
}

func NewMinIOPostgres(db *sql.DB, endpoint, accessKey, secretKey, bucket string) (ScopedRepository, error) {
	if endpoint == "" || accessKey == "" || secretKey == "" || bucket == "" {
		return ScopedRepository{}, errors.New("MinIO configuration is required")
	}
	client, err := minio.New(strings.TrimPrefix(strings.TrimPrefix(endpoint, "http://"), "https://"), &minio.Options{Creds: credentials.NewStaticV4(accessKey, secretKey, ""), Secure: strings.HasPrefix(endpoint, "https://")})
	if err != nil {
		return ScopedRepository{}, err
	}
	return ScopedRepository{db: db, objects: client, bucket: bucket}, nil
}

// GetMedia denies unknown, expired, unbound, and inaccessible media identically.
func (r ScopedRepository) GetMedia(ctx context.Context, callerID, mediaID string) (Media, error) {
	if callerID == "" || mediaID == "" {
		return Media{}, errors.New("media not found")
	}
	var media Media
	var objectRef string
	err := r.db.QueryRowContext(ctx, `
SELECT m.media_id, m.parent_message_id, m.content_type, m.storage_object_ref
FROM media m
WHERE m.media_id=$1 AND m.parent_message_id IS NOT NULL
  AND EXISTS (SELECT 1 FROM channel_memberships membership
    WHERE membership.caller_id=$2 AND membership.channel_id=m.channel_id
      AND (membership.valid_to IS NULL OR membership.valid_to > now()))`, mediaID, callerID).
		Scan(&media.ID, &media.ParentMessageID, &media.ContentType, &objectRef)
	if errors.Is(err, sql.ErrNoRows) {
		return Media{}, errors.New("media not found")
	}
	if err != nil {
		return Media{}, err
	}
	object, err := r.objects.GetObject(ctx, r.bucket, objectRef, minio.GetObjectOptions{})
	if err != nil {
		return Media{}, err
	}
	defer object.Close()
	media.Bytes, err = io.ReadAll(object)
	if err != nil {
		return Media{}, err
	}
	return media, nil
}
