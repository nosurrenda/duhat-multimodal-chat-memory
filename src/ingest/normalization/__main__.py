from __future__ import annotations

import argparse
from pathlib import Path

from ingest.normalization.pipeline import build_corpus
from ingest.normalization.postgres import load_postgres


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the deterministic H2HMEM canonical corpus")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir")
    parser.add_argument("--postgres-dsn")
    args = parser.parse_args()
    output = build_corpus(args.repo_root, args.output_dir)
    if args.postgres_dsn:
        load_postgres(output, args.postgres_dsn, Path(args.repo_root) / "infra/postgres/phase1.sql")
    print(output)


if __name__ == "__main__":
    main()
