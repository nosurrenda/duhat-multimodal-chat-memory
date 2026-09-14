import ast
from pathlib import Path

ROOT = Path(__file__).parents[2]
SOURCE = ROOT / "src/vsf"
ALLOWED = SOURCE / "storage/scoped_repository.py"


def test_only_scoped_repository_may_import_raw_storage() -> None:
    offenders: list[Path] = []
    for path in SOURCE.rglob("*.py"):
        if path == ALLOWED:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and "storage._raw" in node.module:
                offenders.append(path)
            if isinstance(node, ast.Import) and any(
                alias.name.startswith("vsf.storage._raw") for alias in node.names
            ):
                offenders.append(path)
    assert not offenders, f"Unscoped raw-storage imports: {offenders}"
