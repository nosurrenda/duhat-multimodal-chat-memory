// Package manifest validates checksummed build artifacts without reserializing them.
package manifest

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
)

// Visual is the portable subset of the Phase 1.5 artifact contract.
type Visual struct {
	ArtifactType             string         `json:"artifact_type"`
	ModelName                string         `json:"model_name"`
	ModelVersion             string         `json:"model_version"`
	Preprocessing            map[string]any `json:"preprocessing"`
	Device                   string         `json:"device"`
	MatrixSHA256             string         `json:"matrix_sha256"`
	MediaInputManifestSHA256 string         `json:"media_input_manifest_sha256"`
	MediaCount               int            `json:"media_count"`
}

// ReadVisual parses a Python-written visual manifest using only its on-disk bytes.
func ReadVisual(path string) (Visual, error) {
	bytes, err := os.ReadFile(path)
	if err != nil {
		return Visual{}, err
	}
	var value Visual
	if err := json.Unmarshal(bytes, &value); err != nil {
		return Visual{}, fmt.Errorf("visual manifest: %w", err)
	}
	if value.ArtifactType != "visual_embeddings" || value.ModelName == "" || value.ModelVersion == "" || value.Device == "" || value.MatrixSHA256 == "" || value.MediaInputManifestSHA256 == "" || value.MediaCount <= 0 {
		return Visual{}, fmt.Errorf("visual manifest is missing required fields")
	}
	return value, nil
}

// WriteVisual emits a portable visual manifest. Checksums always bind external files,
// so this function only serializes validated metadata and never recomputes an artifact.
func WriteVisual(path string, value Visual) error {
	if value.ArtifactType != "visual_embeddings" || value.ModelName == "" || value.ModelVersion == "" || value.Device == "" || value.MatrixSHA256 == "" || value.MediaInputManifestSHA256 == "" || value.MediaCount <= 0 {
		return fmt.Errorf("visual manifest is missing required fields")
	}
	encoded, err := json.MarshalIndent(value, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, append(encoded, '\n'), 0o600)
}

// FileSHA256 hashes bytes on disk; parsed JSON is never reserialized for a checksum.
func FileSHA256(path string) (string, error) {
	bytes, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(bytes)
	return hex.EncodeToString(sum[:]), nil
}

// VerifyMatrix ensures a matrix still matches the manifest it was shipped with.
func VerifyMatrix(matrixPath string, value Visual) error {
	actual, err := FileSHA256(matrixPath)
	if err != nil {
		return err
	}
	if actual != value.MatrixSHA256 {
		return fmt.Errorf("matrix checksum mismatch")
	}
	return nil
}
