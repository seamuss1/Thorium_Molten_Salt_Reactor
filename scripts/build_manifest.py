"""Record immutable build inputs and resolved packages without requiring git."""

import hashlib
import importlib.metadata
import json
import os
import platform
from pathlib import Path

root = Path(__file__).resolve().parents[1]
inputs = [root / "requirements.lock", root / "web/ui/package-lock.json"]
inputs += sorted((root / "docker").glob("*.Dockerfile"))
source_files = sorted(
    p
    for folder in ("src", "configs", "benchmarks", "docs", "resources", "qa", "web/ui/dist")
    for p in (root / folder).rglob("*")
    if p.is_file() and "__pycache__" not in p.parts
)
source_hash = hashlib.sha256()
for path in source_files:
    source_hash.update(str(path.relative_to(root)).encode())
    source_hash.update(path.read_bytes())
nuclear_index = Path(os.environ.get("OPENMC_CROSS_SECTIONS", "/nonexistent"))
print(
    json.dumps(
        {
            "python": platform.python_version(),
            "source_sha256": source_hash.hexdigest(),
            "nuclear_data_index": {
                "path": str(nuclear_index),
                "sha256": hashlib.sha256(nuclear_index.read_bytes()).hexdigest(),
            }
            if nuclear_index.is_file()
            else None,
            "packages": {d.metadata["Name"]: d.version for d in importlib.metadata.distributions()},
            "inputs": {
                str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs if p.exists()
            },
        },
        indent=2,
        sort_keys=True,
    )
)
