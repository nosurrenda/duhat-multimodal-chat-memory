// Package config reads the canonical JSON artifact emitted by the Python build.
package config

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"reflect"
)

// Document keeps the parsed configuration while retaining its exact bytes for hashing.
type Document struct {
	Bytes []byte
	Value map[string]any
}

// DemoRuntime is the complete Phase 2.7 runtime contract hashed by Python.
type DemoRuntime struct {
	ViewerID           string
	ChannelID          string
	DraftMediaTTL      string
	IndexVisibilitySLA *string
}

// Chunking is the Go-owned limit contract used to decide whether the Python
// service's assembled candidate remains open or starts a new immutable chunk.
type Chunking struct {
	MaxMessages int
	MaxTokens   int
}

// VisualExpectations is the configured D37 identity of a visual artifact.
// It is separate from a manifest so an artifact can be checked against its
// declared build expectations rather than accepting self-reported metadata.
type VisualExpectations struct {
	ExpectedDevice string
	ModelName      string
	ModelVersion   string
	Preprocessing  map[string]any
}

// VisualArtifact is the manifest subset required by the D37 expectation gate.
type VisualArtifact struct {
	Device        string
	ModelName     string
	ModelVersion  string
	Preprocessing map[string]any
}

// Load reads canonical JSON only. YAML remains exclusively a Python build concern.
func Load(path string) (Document, error) {
	bytes, err := os.ReadFile(path)
	if err != nil {
		return Document{}, err
	}
	var value map[string]any
	if err := json.Unmarshal(bytes, &value); err != nil {
		return Document{}, fmt.Errorf("canonical config: %w", err)
	}
	return Document{Bytes: bytes, Value: value}, nil
}

// Hash returns the D38 contract: SHA-256 of the exact canonical JSON bytes.
func (d Document) Hash() string {
	sum := sha256.Sum256(d.Bytes)
	return hex.EncodeToString(sum[:])
}

// DemoRuntime reads only canonical configuration, avoiding identity fallbacks.
func (d Document) DemoRuntime() (DemoRuntime, error) {
	demo, ok := d.Value["demo"].(map[string]any)
	if !ok {
		return DemoRuntime{}, fmt.Errorf("demo configuration is missing")
	}
	runtime, ok := d.Value["runtime"].(map[string]any)
	if !ok {
		return DemoRuntime{}, fmt.Errorf("runtime configuration is missing")
	}
	viewerID, err := requiredString(demo, "viewer_id")
	if err != nil {
		return DemoRuntime{}, err
	}
	channelID, err := requiredString(demo, "channel_id")
	if err != nil {
		return DemoRuntime{}, err
	}
	ttl, err := requiredString(runtime, "draft_media_ttl")
	if err != nil {
		return DemoRuntime{}, err
	}
	result := DemoRuntime{ViewerID: viewerID, ChannelID: channelID, DraftMediaTTL: ttl}
	if value, present := runtime["index_visibility_sla"]; present && value != nil {
		sla, ok := value.(string)
		if !ok || sla == "" {
			return DemoRuntime{}, fmt.Errorf("runtime index_visibility_sla must be null or a non-empty string")
		}
		result.IndexVisibilitySLA = &sla
	}
	return result, nil
}

// ChunkingConfig reads limits from canonical JSON so Go never creates a
// second YAML interpretation of the chunk-boundary contract.
func (d Document) ChunkingConfig() (Chunking, error) {
	value, ok := d.Value["chunking"].(map[string]any)
	if !ok {
		return Chunking{}, fmt.Errorf("chunking configuration is missing")
	}
	maxMessages, ok := value["max_messages"].(float64)
	if !ok || maxMessages < 1 || maxMessages != float64(int(maxMessages)) {
		return Chunking{}, fmt.Errorf("chunking max_messages must be a positive integer")
	}
	maxTokens, ok := value["max_tokens"].(float64)
	if !ok || maxTokens < 1 || maxTokens != float64(int(maxTokens)) {
		return Chunking{}, fmt.Errorf("chunking max_tokens must be a positive integer")
	}
	return Chunking{MaxMessages: int(maxMessages), MaxTokens: int(maxTokens)}, nil
}

// VisualExpectations requires all configured D37 expectations; it never
// supplies a default because a missing pin must stop artifact consumption.
func (d Document) VisualExpectations() (VisualExpectations, error) {
	value, ok := d.Value["visual_expectations"].(map[string]any)
	if !ok {
		return VisualExpectations{}, fmt.Errorf("visual expectations are missing")
	}
	expectations := VisualExpectations{}
	var err error
	if expectations.ExpectedDevice, err = requiredString(value, "expected_device"); err != nil {
		return VisualExpectations{}, err
	}
	if expectations.ModelName, err = requiredString(value, "model_name"); err != nil {
		return VisualExpectations{}, err
	}
	if expectations.ModelVersion, err = requiredString(value, "model_version"); err != nil {
		return VisualExpectations{}, err
	}
	preprocessing, ok := value["preprocessing"].(map[string]any)
	if !ok || len(preprocessing) == 0 {
		return VisualExpectations{}, fmt.Errorf("visual expectations preprocessing is missing")
	}
	expectations.Preprocessing = preprocessing
	return expectations, nil
}

// ValidateVisualArtifact rejects a manifest that differs from the hashed D37
// configuration, including preprocessing rather than only model identifiers.
func (d Document) ValidateVisualArtifact(artifact VisualArtifact) error {
	expectations, err := d.VisualExpectations()
	if err != nil {
		return err
	}
	if artifact.Device != expectations.ExpectedDevice {
		return fmt.Errorf("visual artifact device %q does not match expected device %q", artifact.Device, expectations.ExpectedDevice)
	}
	if artifact.ModelName != expectations.ModelName {
		return fmt.Errorf("visual artifact model name %q does not match configured value %q", artifact.ModelName, expectations.ModelName)
	}
	if artifact.ModelVersion != expectations.ModelVersion {
		return fmt.Errorf("visual artifact model version %q does not match configured value %q", artifact.ModelVersion, expectations.ModelVersion)
	}
	if !reflect.DeepEqual(artifact.Preprocessing, expectations.Preprocessing) {
		return fmt.Errorf("visual artifact preprocessing does not match configured value")
	}
	return nil
}

func requiredString(value map[string]any, key string) (string, error) {
	field, ok := value[key].(string)
	if !ok || field == "" {
		return "", fmt.Errorf("visual expectations %s is missing", key)
	}
	return field, nil
}
