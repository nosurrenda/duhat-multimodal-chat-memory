// Package mediahash implements the one cross-language logical-media checksum.
package mediahash

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
)

type row struct {
	MediaID          string `json:"media_id"`
	ContentSHA256    string `json:"content_sha256"`
	StorageObjectRef string `json:"storage_object_ref"`
}

// AssertPortable rejects data that the Python and Go encoders cannot serialize identically.
func AssertPortable(value string) error {
	if !isASCII(value) || strings.ContainsAny(value, "<>&") {
		return fmt.Errorf("media identity contains non-portable JSON characters")
	}
	return nil
}

func isASCII(value string) bool {
	for _, character := range value {
		if character > 0x7f {
			return false
		}
	}
	return true
}

// HashFile matches Python's sorted compact JSON tuple serialization under K2f's domain.
func HashFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	var rows []row
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		if len(scanner.Bytes()) == 0 {
			continue
		}
		var item row
		if err := json.Unmarshal(scanner.Bytes(), &item); err != nil {
			return "", err
		}
		if err := AssertPortable(item.MediaID); err != nil {
			return "", err
		}
		if err := AssertPortable(item.ContentSHA256); err != nil {
			return "", err
		}
		if err := AssertPortable(item.StorageObjectRef); err != nil {
			return "", err
		}
		rows = append(rows, item)
	}
	if err := scanner.Err(); err != nil {
		return "", err
	}
	sort.Slice(rows, func(i, j int) bool { return rows[i].MediaID < rows[j].MediaID })
	tuples := make([][3]string, len(rows))
	for i, item := range rows {
		tuples[i] = [3]string{item.MediaID, item.ContentSHA256, item.StorageObjectRef}
	}
	payload, err := json.Marshal(tuples)
	if err != nil {
		return "", err
	}
	sum := sha256.Sum256(payload)
	return hex.EncodeToString(sum[:]), nil
}
