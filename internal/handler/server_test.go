package handler

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestParseAfterPreservesZeroRankOnInitialFeed(t *testing.T) {
	after, err := parseAfter("")
	if err != nil || after != -1 {
		t.Fatalf("initial after = %d, %v; want -1, nil", after, err)
	}
	if after, err = parseAfter("0"); err != nil || after != 0 {
		t.Fatalf("explicit after = %d, %v; want 0, nil", after, err)
	}
}

func TestParseLastEventIDRejectsInvalidCursor(t *testing.T) {
	if _, err := parseLastEventID("not-an-event"); err == nil {
		t.Fatal("invalid Last-Event-ID must fail")
	}
}

func TestGeneralEventsFailClosedWithoutAuthenticator(t *testing.T) {
	server := New(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/events", nil)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusUnauthorized {
		t.Fatalf("general events status = %d, want %d", recorder.Code, http.StatusUnauthorized)
	}
}

func TestFeedRejectsMixedPaginationCursors(t *testing.T) {
	server := New(nil)
	recorder := httptest.NewRecorder()
	request := httptest.NewRequest(http.MethodGet, "/api/demo/feed?after=1&before=2", nil)
	server.Handler().ServeHTTP(recorder, request)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("mixed cursor status = %d, want %d", recorder.Code, http.StatusBadRequest)
	}
}
