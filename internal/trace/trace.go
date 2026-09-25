// Package trace reads and writes the closed Phase 0.5 JSONL trace envelope.
package trace

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"os"
)

var actions = map[string]struct{}{"retrieve_lexical": {}, "retrieve_dense": {}, "retrieve_visual": {}, "filter_metadata": {}, "expand_context": {}, "extract_clues": {}, "resolve_bridge": {}, "jump": {}, "vlm_extract": {}, "stop": {}}

type Record struct {
	TraceSchemaVersion string         `json:"trace_schema_version"`
	RunID              string         `json:"run_id"`
	QueryID            string         `json:"query_id"`
	EventID            string         `json:"event_id"`
	ParentEventID      *string        `json:"parent_event_id"`
	RecordType         string         `json:"record_type"`
	Payload            map[string]any `json:"payload"`
}

func validate(record Record) error {
	if record.TraceSchemaVersion != "1.0.0" || record.RunID == "" || record.QueryID == "" || record.EventID == "" {
		return fmt.Errorf("trace record is missing required envelope fields")
	}
	if record.RecordType == "round" {
		action, ok := record.Payload["action"].(string)
		if !ok {
			return fmt.Errorf("round requires action")
		}
		if _, ok := actions[action]; !ok {
			return fmt.Errorf("round action is outside the closed enum")
		}
	}
	return nil
}

func Write(path string, record Record) error {
	if err := validate(record); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o600)
	if err != nil {
		return err
	}
	defer file.Close()
	encoded, err := json.Marshal(record)
	if err != nil {
		return err
	}
	_, err = file.Write(append(encoded, '\n'))
	return err
}

func Read(path string) ([]Record, error) {
	file, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer file.Close()
	var records []Record
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		var record Record
		decoder := json.NewDecoder(bytes.NewReader(scanner.Bytes()))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(&record); err != nil {
			return nil, err
		}
		// JSONL accepts exactly one closed-envelope object per physical line.
		if err := decoder.Decode(&struct{}{}); err != io.EOF {
			if err == nil {
				return nil, fmt.Errorf("trace line contains multiple JSON values")
			}
			return nil, err
		}
		if err := validate(record); err != nil {
			return nil, err
		}
		records = append(records, record)
	}
	return records, scanner.Err()
}
