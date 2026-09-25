package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"sort"
)

type mediaRecord struct {
	MediaID          string `json:"media_id"`
	ContentSHA256    string `json:"content_sha256"`
	StorageObjectRef string `json:"storage_object_ref"`
}

func mediaManifestSHA256(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()

	var records []mediaRecord
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		if len(scanner.Bytes()) == 0 {
			continue
		}
		var record mediaRecord
		if err := json.Unmarshal(scanner.Bytes(), &record); err != nil {
			return "", err
		}
		records = append(records, record)
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}

	sort.Slice(records, func(left, right int) bool {
		return records[left].MediaID < records[right].MediaID
	})
	tuples := make([][3]string, len(records))
	for index, record := range records {
		tuples[index] = [3]string{record.MediaID, record.ContentSHA256, record.StorageObjectRef}
	}
	payload, err := json.Marshal(tuples)
	if err != nil {
		return "", err
	}
	digest := sha256.Sum256(payload)
	return hex.EncodeToString(digest[:]), nil
}

// This standalone Y9 reader deliberately has no module or third-party dependency.
func main() {
	path := flag.String("file", "", "file whose bytes should be hashed")
	mediaManifest := flag.String("media-manifest", "", "media.jsonl used to compute media_input_manifest_sha256")
	flag.Parse()
	if *mediaManifest != "" {
		digest, err := mediaManifestSHA256(*mediaManifest)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Println(digest)
		return
	}
	if *path == "" {
		fmt.Fprintln(os.Stderr, "--file or --media-manifest is required")
		os.Exit(2)
	}
	payload, err := os.ReadFile(*path)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	digest := sha256.Sum256(payload)
	fmt.Println(hex.EncodeToString(digest[:]))
}
