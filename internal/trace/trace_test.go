package trace

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestReadRejectsUnknownEnvelopeField(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	line := `{"trace_schema_version":"1.0.0","run_id":"run","query_id":"q","event_id":"e","record_type":"query_started","payload":{},"unexpected":true}`
	if err := os.WriteFile(path, []byte(line+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Read(path)
	if err == nil || !strings.Contains(err.Error(), "unknown field") {
		t.Fatalf("expected unknown field rejection, got %v", err)
	}
}

func TestReadRejectsUnknownAction(t *testing.T) {
	path := filepath.Join(t.TempDir(), "trace.jsonl")
	line := `{"trace_schema_version":"1.0.0","run_id":"run","query_id":"q","event_id":"e","record_type":"round","payload":{"action":"invented"}}`
	if err := os.WriteFile(path, []byte(line+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	_, err := Read(path)
	if err == nil || !strings.Contains(err.Error(), "closed enum") {
		t.Fatalf("expected action rejection, got %v", err)
	}
}
