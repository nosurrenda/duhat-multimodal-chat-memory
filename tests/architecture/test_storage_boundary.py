import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "src/vsf"
ALLOWED = SOURCE / "storage/scoped_repository.py"


def _raw_import_offenders(source: Path) -> list[Path]:
    offenders: list[Path] = []
    for path in source.rglob("*.py"):
        if path == ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "storage._raw" in node.module:
                offenders.append(path)
            if isinstance(node, ast.ImportFrom) and node.level and node.module == "_raw":
                offenders.append(path)
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("vsf.storage._raw") for alias in node.names
            ):
                offenders.append(path)
    return offenders


def test_only_scoped_repository_may_import_raw_storage() -> None:
    assert not _raw_import_offenders(SOURCE)


def test_relative_raw_import_fixture_is_rejected() -> None:
    fixture_root = ROOT / "tests/fixtures/storage_boundary"
    assert _raw_import_offenders(fixture_root) == [fixture_root / "relative_raw_import.py"]
