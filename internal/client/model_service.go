// Package client owns adapters for external service boundaries.
package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/service"
)

// ModelService is intentionally small: Python owns model numerics while Go owns all durable state.
type ModelService struct {
	baseURL string
	http    *http.Client
}

func NewModelService(baseURL string) *ModelService {
	// Local CPU inference can occasionally exceed the ordinary API timeout while
	// loading or contending for the BGE model. Durable state is written only by
	// the caller after an RPC succeeds, so retrying this pure model boundary is safe.
	return &ModelService{baseURL: strings.TrimRight(baseURL, "/"), http: &http.Client{Timeout: 5 * time.Minute}}
}

type appendRequest struct {
	ChannelID     string `json:"channel_id"`
	Day           string `json:"day"`
	ExistingChunk *struct {
		MessageIDs []string `json:"message_ids"`
		Text       string   `json:"text"`
	} `json:"existing_chunk"`
	NewMessage struct {
		MessageID string   `json:"message_id"`
		SenderID  string   `json:"sender_id"`
		Text      string   `json:"text"`
		MediaIDs  []string `json:"media_ids"`
	} `json:"new_message"`
}

// Append delegates only deterministic assembly/tokenization across the model boundary.
func (c *ModelService) Append(ctx context.Context, message service.IndexMessage, open *service.Chunk) (service.AssembledChunk, error) {
	request := appendRequest{ChannelID: message.ChannelID, Day: message.OccurredAt.UTC().Format("2006-01-02")}
	if open != nil {
		request.ExistingChunk = &struct {
			MessageIDs []string `json:"message_ids"`
			Text       string   `json:"text"`
		}{MessageIDs: open.MessageIDs, Text: open.Text}
	}
	request.NewMessage.MessageID = message.MessageID
	request.NewMessage.SenderID = message.SenderID
	request.NewMessage.Text = message.Text
	request.NewMessage.MediaIDs = message.MediaIDs
	var response struct {
		AssembledText       string   `json:"assembled_text"`
		AssembledMessageIDs []string `json:"assembled_message_ids"`
		AssembledMediaIDs   []string `json:"assembled_media_ids"`
		TokenCount          int      `json:"token_count"`
	}
	if err := c.post(ctx, "/v1/chunk/append", request, &response); err != nil {
		return service.AssembledChunk{}, err
	}
	mediaIDs := response.AssembledMediaIDs
	if open != nil {
		// The RPC deliberately receives only prior text/message ids; preserve Go's
		// authoritative media list while adding inline media from this new message.
		mediaIDs = append(append([]string{}, open.MediaIDs...), mediaIDs...)
	}
	return service.AssembledChunk{Text: response.AssembledText, MessageIDs: response.AssembledMessageIDs, MediaIDs: deduplicate(mediaIDs), TokenCount: response.TokenCount}, nil
}

func deduplicate(values []string) []string {
	seen := make(map[string]struct{}, len(values))
	result := make([]string, 0, len(values))
	for _, value := range values {
		if _, exists := seen[value]; !exists {
			seen[value] = struct{}{}
			result = append(result, value)
		}
	}
	return result
}

func (c *ModelService) EmbedText(ctx context.Context, firstMessageID, text string) (service.TextEmbedding, error) {
	var response struct {
		ModelVersion string    `json:"model_version"`
		Vector       []float32 `json:"vector"`
	}
	if err := c.post(ctx, "/v1/embed/text", map[string]string{"first_message_id": firstMessageID, "text": text}, &response); err != nil {
		return service.TextEmbedding{}, err
	}
	return service.TextEmbedding{ModelVersion: response.ModelVersion, Vector: response.Vector}, nil
}

func (c *ModelService) EmbedImage(ctx context.Context, mediaID, storageObjectRef string) (service.ImageEmbedding, error) {
	var response struct {
		ModelVersion string    `json:"model_version"`
		Vector       []float32 `json:"vector"`
	}
	if err := c.post(ctx, "/v1/embed/image", map[string]string{"media_id": mediaID, "storage_object_ref": storageObjectRef}, &response); err != nil {
		return service.ImageEmbedding{}, err
	}
	return service.ImageEmbedding{ModelVersion: response.ModelVersion, Vector: response.Vector}, nil
}

func (c *ModelService) post(ctx context.Context, path string, request, response any) error {
	payload, err := json.Marshal(request)
	if err != nil {
		return err
	}
	var lastErr error
	for attempt := 0; attempt < 3; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(attempt) * time.Second):
			}
		}
		httpRequest, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(payload))
		if err != nil {
			return err
		}
		httpRequest.Header.Set("Content-Type", "application/json")
		httpResponse, err := c.http.Do(httpRequest)
		if err != nil {
			lastErr = err
			continue
		}
		body, readErr := io.ReadAll(httpResponse.Body)
		httpResponse.Body.Close()
		if readErr != nil {
			lastErr = readErr
			continue
		}
		if httpResponse.StatusCode/100 == 2 {
			return json.Unmarshal(body, response)
		}
		if httpResponse.StatusCode < http.StatusInternalServerError {
			return fmt.Errorf("model service %s: %s", httpResponse.Status, strings.TrimSpace(string(body)))
		}
		lastErr = fmt.Errorf("model service %s: %s", httpResponse.Status, strings.TrimSpace(string(body)))
	}
	return fmt.Errorf("model service %s failed after 3 attempts: %w", path, lastErr)
}
