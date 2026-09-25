package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"testing"
)

func TestLoadHashesExactBytes(t *testing.T) {
	path := filepath.Join(t.TempDir(), "canonical.json")
	if err := os.WriteFile(path, []byte(`{"name":"café"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	document, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if got, want := document.Hash(), "645fa443126a8954fc6d871912b8fc67bc2ee8feae417efe55546251962ca74d"; got != want {
		t.Fatalf("hash = %s, want %s", got, want)
	}
}

func TestDemoRuntimeRequiresHashedIdentityAndTTL(t *testing.T) {
	document := Document{Value: map[string]any{
		"demo":    map[string]any{"viewer_id": "viewer", "channel_id": "channel"},
		"runtime": map[string]any{"index_visibility_sla": nil, "draft_media_ttl": "24h"},
	}}
	runtime, err := document.DemoRuntime()
	if err != nil || runtime.ViewerID != "viewer" || runtime.DraftMediaTTL != "24h" {
		t.Fatalf("runtime = %#v, %v", runtime, err)
	}
	delete(document.Value["runtime"].(map[string]any), "draft_media_ttl")
	if _, err := document.DemoRuntime(); err == nil {
		t.Fatal("missing TTL must fail")
	}
}

func TestVisualExpectationsRequireEveryConfiguredValue(t *testing.T) {
	path := filepath.Join(t.TempDir(), "canonical.json")
	value := map[string]any{
		"visual_expectations": map[string]any{
			"expected_device": "cpu",
			"model_name":      "model",
			"model_version":   "revision",
			"preprocessing":   map[string]any{"normalization": "siglip"},
		},
	}
	bytes, err := json.Marshal(value)
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, bytes, 0o600); err != nil {
		t.Fatal(err)
	}
	document, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := document.VisualExpectations(); err != nil {
		t.Fatalf("expected complete expectations to load: %v", err)
	}

	delete(document.Value["visual_expectations"].(map[string]any), "model_version")
	if _, err := document.VisualExpectations(); err == nil {
		t.Fatal("missing expectation must fail loudly")
	}
}

func TestValidateVisualArtifactMatchesAllD37Expectations(t *testing.T) {
	document := Document{Value: map[string]any{
		"visual_expectations": map[string]any{
			"expected_device": "cpu",
			"model_name":      "model",
			"model_version":   "revision",
			"preprocessing":   map[string]any{"image_size": []any{384.0, 384.0}},
		},
	}}
	valid := VisualArtifact{
		Device: "cpu", ModelName: "model", ModelVersion: "revision",
		Preprocessing: map[string]any{"image_size": []any{384.0, 384.0}},
	}
	if err := document.ValidateVisualArtifact(valid); err != nil {
		t.Fatalf("valid artifact rejected: %v", err)
	}
	for name, artifact := range map[string]VisualArtifact{
		"device":        {Device: "gpu", ModelName: "model", ModelVersion: "revision", Preprocessing: valid.Preprocessing},
		"model name":    {Device: "cpu", ModelName: "other", ModelVersion: "revision", Preprocessing: valid.Preprocessing},
		"model version": {Device: "cpu", ModelName: "model", ModelVersion: "different", Preprocessing: valid.Preprocessing},
		"preprocessing": {Device: "cpu", ModelName: "model", ModelVersion: "revision", Preprocessing: map[string]any{"image_size": []any{224.0, 224.0}}},
	} {
		if err := document.ValidateVisualArtifact(artifact); err == nil {
			t.Fatalf("%s mismatch must fail", name)
		}
	}
}
