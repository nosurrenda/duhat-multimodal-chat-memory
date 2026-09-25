package main

import (
	"context"
	"log"
	"net/http"
	"os"
	"time"

	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/client"
	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/config"
	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/handler"
	"github.com/nosurrenda/duhat-multimodal-chat-memory/internal/service"
)

func main() {
	startupTimeout := 20 * time.Second
	// Bulk canonical-media import is intentionally slower than ordinary startup.
	if os.Getenv("SEED_PROCESSED") != "" {
		startupTimeout = 10 * time.Minute
	}
	ctx, cancel := context.WithTimeout(context.Background(), startupTimeout)
	defer cancel()
	document, err := config.Load(env("CONFIG_CANONICAL_PATH", "configs/config_canonical.json"))
	if err != nil {
		log.Fatal(err)
	}
	runtime, err := document.DemoRuntime()
	if err != nil {
		log.Fatal(err)
	}
	chunking, err := document.ChunkingConfig()
	if err != nil {
		log.Fatal(err)
	}
	draftTTL, err := time.ParseDuration(runtime.DraftMediaTTL)
	if err != nil {
		log.Fatalf("invalid configured draft_media_ttl: %v", err)
	}
	store, err := service.Open(ctx, env("DATABASE_URL", "postgres://vsf:vsf_local_only@localhost:5433/vsf?sslmode=disable"), service.Config{DemoViewerID: runtime.ViewerID, DemoChannelID: runtime.ChannelID, DraftMediaTTL: draftTTL, MinIOEndpoint: env("MINIO_ENDPOINT", "http://localhost:9000"), MinIOAccessKey: env("MINIO_ROOT_USER", "vsf_minio"), MinIOSecretKey: env("MINIO_ROOT_PASSWORD", "vsf_minio_local_only"), MinIOBucket: env("MINIO_BUCKET", "vsf-media")})
	if err != nil {
		log.Fatal(err)
	}
	defer store.Close()
	if err := store.Migrate(ctx); err != nil {
		log.Fatal(err)
	}
	if os.Getenv("SEED_PROCESSED") != "" {
		if err := store.SeedIdentity(ctx, os.Getenv("SEED_PROCESSED")); err != nil {
			log.Fatal(err)
		}
		if err := store.SeedMessages(ctx, os.Getenv("SEED_PROCESSED")); err != nil {
			log.Fatal(err)
		}
	}
	if err := store.ValidateDemoConfig(ctx); err != nil {
		log.Fatal(err)
	}
	worker := service.NewIndexWorker(store, client.NewModelService(env("MODEL_SERVICE_URL", "http://127.0.0.1:8090")), chunking.MaxMessages, chunking.MaxTokens)
	if os.Getenv("PHASE3_SEED_BUILD") == "1" {
		// The seed resumes from durable ordinals and may take longer than startup's
		// migration/import budget on CPU inference. It must not inherit that timeout.
		if err := worker.BuildSeed(context.Background()); err != nil {
			log.Fatal(err)
		}
	}
	workerContext, stopWorker := context.WithCancel(context.Background())
	defer stopWorker()
	// Indexing is asynchronous: an unavailable local model service delays search,
	// never persistence, acknowledgement, or SSE delivery of a chat message.
	go worker.Run(workerContext)
	log.Printf("VSF demo server listening on %s", env("ADDR", ":8080"))
	log.Fatal(http.ListenAndServe(env("ADDR", ":8080"), handler.New(store).Handler()))
}

func env(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
