from __future__ import annotations

import argparse
from pathlib import Path

from artifacts.visual.pipeline import (
    build_early_distractor_sanity,
    build_visual_embeddings,
    write_canonical_config_fixture,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Phase 1.5 visual embedding artifacts")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--output-dir")
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--write-config-fixture", action="store_true")
    args = parser.parse_args()
    root = Path(args.repo_root)
    if args.write_config_fixture:
        print(write_canonical_config_fixture(root / "configs/base.yaml", root / "configs/config_canonical.json"))
        return
    output = build_visual_embeddings(
        root / "data/processed",
        args.output_dir or root / "data/visual_embeddings",
        root / "configs/base.yaml",
        batch_size=args.batch_size,
    )
    build_early_distractor_sanity(root / "data/processed", output)
    print(output)


if __name__ == "__main__":
    main()
