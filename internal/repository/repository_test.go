package repository

import (
	"testing"
	"time"
)

func TestDefaultDenyAndValidMembership(t *testing.T) {
	now := time.Now().UTC()
	expired := now.Add(-time.Minute)
	repo := New([]Membership{{CallerID: "u_1", ChannelID: "c_ok"}, {CallerID: "u_1", ChannelID: "c_old", ValidTo: &expired}})
	if repo.Allows("", "c_ok", now) || repo.Allows("unknown", "c_ok", now) || repo.Allows("u_1", "c_old", now) {
		t.Fatal("invalid scope must default deny")
	}
	if !repo.Allows("u_1", "c_ok", now) {
		t.Fatal("valid membership must be allowed")
	}
}

func TestChunkIDAvoidsRepeatingCanonicalChannelPrefix(t *testing.T) {
	got := chunkID("dyadic_d1", []string{"dyadic_d1:session1:0", "dyadic_d1:session1:5"})
	if want := "dyadic_d1:session1:0-session1:5"; got != want {
		t.Fatalf("chunkID() = %q, want %q", got, want)
	}
}
