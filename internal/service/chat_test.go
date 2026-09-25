package service

import (
	"context"
	"testing"
	"time"
)

func TestOpenRejectsMissingRuntimeContract(t *testing.T) {
	if _, err := Open(context.Background(), "", Config{DraftMediaTTL: time.Hour}); err == nil {
		t.Fatal("missing demo identities must fail before opening a database")
	}
}
