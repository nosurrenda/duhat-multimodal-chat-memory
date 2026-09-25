package manifest

import (
	"path/filepath"
	"testing"
)

func TestWriteVisualRoundTrip(t *testing.T) {
	path := filepath.Join(t.TempDir(), "visual.json")
	want := Visual{
		ArtifactType: "visual_embeddings", ModelName: "model", ModelVersion: "revision",
		Preprocessing: map[string]any{"image_size": []any{384.0, 384.0}}, Device: "cpu",
		MatrixSHA256: "matrix", MediaInputManifestSHA256: "media", MediaCount: 1,
	}
	if err := WriteVisual(path, want); err != nil {
		t.Fatal(err)
	}
	got, err := ReadVisual(path)
	if err != nil {
		t.Fatal(err)
	}
	if got.ModelName != want.ModelName || got.MatrixSHA256 != want.MatrixSHA256 || got.MediaCount != want.MediaCount {
		t.Fatalf("round trip mismatch: %#v", got)
	}
}
