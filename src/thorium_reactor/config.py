from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


REQUIRED_CASE_KEYS = (
    "reactor",
    "materials",
    "geometry",
    "simulation",
    "reporting",
    "validation_targets",
)


class ConfigError(ValueError):
    """Raised when a case configuration is invalid."""


@dataclass(slots=True)
class CaseConfig:
    name: str
    path: Path
    data: dict[str, Any]

    @property
    def reactor(self) -> dict[str, Any]:
        return self.data["reactor"]

    @property
    def materials(self) -> dict[str, Any]:
        return self.data["materials"]

    @property
    def geometry(self) -> dict[str, Any]:
        return self.data["geometry"]

    @property
    def simulation(self) -> dict[str, Any]:
        return self.data["simulation"]

    @property
    def reporting(self) -> dict[str, Any]:
        return self.data["reporting"]

    @property
    def validation_targets(self) -> dict[str, Any]:
        return self.data["validation_targets"]

    @property
    def benchmark_file(self) -> Path | None:
        raw_path = self.reactor.get("benchmark")
        if not raw_path:
            return None
        return (self.path.parents[3] / raw_path).resolve()


def load_case_config(path: Path) -> CaseConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    missing = [key for key in REQUIRED_CASE_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"Case config {path} is missing required keys: {', '.join(missing)}")

    name = raw.get("name") or path.parent.name
    return CaseConfig(name=name, path=path, data=raw)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}
