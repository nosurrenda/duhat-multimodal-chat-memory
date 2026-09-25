package repository

import (
	"go/parser"
	"go/token"
	"io/fs"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

const rawImport = "github.com/nosurrenda/duhat-multimodal-chat-memory/internal/repository/raw"

var postgresImports = map[string]struct{}{
	"database/sql":                   {},
	"github.com/jackc/pgx/v5/stdlib": {},
}

func rawImportOffenders(root string, allowScope bool) ([]string, error) {
	var offenders []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || !strings.HasSuffix(path, ".go") {
			return err
		}
		file, parseErr := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if parseErr != nil {
			return parseErr
		}
		for _, value := range file.Imports {
			if strings.Trim(value.Path.Value, "\"") == rawImport && !(allowScope && strings.Contains(path, "/internal/repository/")) {
				offenders = append(offenders, path)
			}
		}
		return nil
	})
	return offenders, err
}

func repositoryRoot(t *testing.T) string {
	t.Helper()
	_, file, _, ok := runtime.Caller(0)
	if !ok {
		t.Fatal("cannot find source root")
	}
	return filepath.Clean(filepath.Join(filepath.Dir(file), "../.."))
}

func TestK4aNoRawStorageImportsOutsideScopedRepository(t *testing.T) {
	offenders, err := rawImportOffenders(filepath.Join(repositoryRoot(t), "internal"), true)
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) != 0 {
		t.Fatalf("raw storage imports outside scope: %v", offenders)
	}
}

func TestK4bViolatingFixtureIsCaught(t *testing.T) {
	fixture := filepath.Join(repositoryRoot(t), "tests", "fixtures", "storage_boundary", "violating_go_storage")
	offenders, err := rawImportOffenders(fixture, false)
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) != 1 {
		t.Fatalf("expected one violating fixture, got %v", offenders)
	}
}

// Z4f prevents a second repository from quietly reopening PostgreSQL and
// reimplementing scope checks outside the one authorized package.
func TestZ4fOnlyScopeImportsPostgres(t *testing.T) {
	offenders, err := postgresImportOffenders(filepath.Join(repositoryRoot(t), "internal"))
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) != 0 {
		t.Fatalf("Postgres imports outside scope: %v", offenders)
	}
}

// Z4g proves Z4f's AST matcher can actually detect the forbidden import.
func TestZ4gPostgresImportViolatingFixtureIsCaught(t *testing.T) {
	fixture := filepath.Join(repositoryRoot(t), "tests", "fixtures", "storage_boundary", "violating_go_postgres")
	offenders, err := postgresImportOffenders(fixture)
	if err != nil {
		t.Fatal(err)
	}
	if len(offenders) != 1 {
		t.Fatalf("expected one forbidden Postgres import, got %v", offenders)
	}
}

func postgresImportOffenders(root string) ([]string, error) {
	var offenders []string
	err := filepath.WalkDir(root, func(path string, entry fs.DirEntry, err error) error {
		if err != nil || entry.IsDir() || !strings.HasSuffix(path, ".go") || strings.HasSuffix(path, "_test.go") {
			return err
		}
		file, parseErr := parser.ParseFile(token.NewFileSet(), path, nil, parser.ImportsOnly)
		if parseErr != nil {
			return parseErr
		}
		for _, value := range file.Imports {
			if _, found := postgresImports[strings.Trim(value.Path.Value, "\"")]; found && !strings.Contains(path, "/internal/repository/") {
				offenders = append(offenders, path)
			}
		}
		return nil
	})
	return offenders, err
}
