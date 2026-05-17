from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
import xml.etree.ElementTree as ET

import yaml


@dataclass(frozen=True, slots=True)
class DepletionReaction:
    reaction_type: str
    target: str | None = None
    branching_ratio: float = 1.0
    default_rate_per_s: float = 0.0
    fission_yields: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DepletionNuclide:
    name: str
    half_life_s: float | None = None
    decay_modes: tuple[DepletionReaction, ...] = ()
    reactions: tuple[DepletionReaction, ...] = ()
    initial_atoms: float = 0.0


@dataclass(frozen=True, slots=True)
class DepletionChain:
    name: str
    source: str
    source_format: str
    nuclides: tuple[DepletionNuclide, ...]

    @property
    def nuclide_names(self) -> list[str]:
        return [nuclide.name for nuclide in self.nuclides]

    def to_json(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "source": self.source,
            "source_format": self.source_format,
            "nuclide_count": len(self.nuclides),
            "nuclides": [
                {
                    "name": nuclide.name,
                    "half_life_s": nuclide.half_life_s,
                    "initial_atoms": nuclide.initial_atoms,
                    "decay_modes": [_reaction_to_json(reaction) for reaction in nuclide.decay_modes],
                    "reactions": [_reaction_to_json(reaction) for reaction in nuclide.reactions],
                }
                for nuclide in self.nuclides
            ],
        }


def load_depletion_chain(path: Path, *, source_format: str | None = None) -> DepletionChain:
    resolved = path.resolve()
    fmt = (source_format or resolved.suffix.lstrip(".") or "yaml").lower()
    if fmt in {"yaml", "yml"}:
        return load_yaml_chain(resolved)
    if fmt in {"xml", "openmc"}:
        return load_openmc_xml_chain(resolved)
    raise ValueError(f"Unsupported depletion chain format '{fmt}'.")


def load_yaml_chain(path: Path) -> DepletionChain:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, Mapping):
        raise ValueError(f"Depletion chain {path} must contain a mapping.")
    raw_nuclides = payload.get("nuclides", [])
    if not isinstance(raw_nuclides, list) or not raw_nuclides:
        raise ValueError(f"Depletion chain {path} must contain at least one nuclide.")
    nuclides = []
    for index, raw in enumerate(raw_nuclides, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Depletion chain {path} nuclide {index} must be a mapping.")
        name = str(raw.get("name", "")).strip()
        if not name:
            raise ValueError(f"Depletion chain {path} nuclide {index} must declare a name.")
        nuclides.append(
            DepletionNuclide(
                name=name,
                half_life_s=_optional_float(raw.get("half_life_s")),
                decay_modes=tuple(_parse_reactions(raw.get("decay_modes", []), default_type="decay")),
                reactions=tuple(_parse_reactions(raw.get("reactions", []), default_type="reaction")),
                initial_atoms=float(raw.get("initial_atoms", 0.0) or 0.0),
            )
        )
    return DepletionChain(
        name=str(payload.get("name", path.stem)),
        source=str(path),
        source_format="yaml",
        nuclides=tuple(nuclides),
    )


def load_openmc_xml_chain(path: Path) -> DepletionChain:
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    nuclides = []
    for raw in root.findall(".//nuclide"):
        name = str(raw.get("name", "")).strip()
        if not name:
            continue
        half_life = _optional_float(raw.get("half_life"))
        if half_life is None:
            half_life_node = raw.find("half_life")
            if half_life_node is not None:
                half_life = _optional_float(half_life_node.text)
        decay_modes = []
        for decay in raw.findall("decay"):
            decay_modes.append(
                DepletionReaction(
                    reaction_type=str(decay.get("type", "decay")),
                    target=_empty_to_none(decay.get("target")),
                    branching_ratio=float(decay.get("branching_ratio", decay.get("branching", 1.0))),
                )
            )
        reactions = []
        for reaction in raw.findall("reaction"):
            reactions.append(
                DepletionReaction(
                    reaction_type=str(reaction.get("type", "reaction")),
                    target=_empty_to_none(reaction.get("target")),
                    branching_ratio=float(reaction.get("branching_ratio", reaction.get("branching", 1.0))),
                    default_rate_per_s=float(reaction.get("rate_per_s", reaction.get("default_rate_per_s", 0.0))),
                )
            )
        for fission in raw.findall("neutron_fission"):
            yields: dict[str, float] = {}
            for product in fission.findall("yield"):
                product_name = _empty_to_none(product.get("product") or product.get("name"))
                if product_name:
                    yields[product_name] = float(product.get("fraction", product.get("yield", 0.0)))
            reactions.append(
                DepletionReaction(
                    reaction_type="fission",
                    default_rate_per_s=float(fission.get("rate_per_s", 0.0)),
                    fission_yields=yields,
                )
            )
        nuclides.append(
            DepletionNuclide(
                name=name,
                half_life_s=half_life,
                decay_modes=tuple(decay_modes),
                reactions=tuple(reactions),
            )
        )
    if not nuclides:
        raise ValueError(f"OpenMC depletion chain {path} did not contain nuclide records.")
    return DepletionChain(
        name=str(root.get("name", path.stem)),
        source=str(path),
        source_format="openmc_xml",
        nuclides=tuple(nuclides),
    )


def _parse_reactions(raw_reactions: Any, *, default_type: str) -> list[DepletionReaction]:
    if raw_reactions is None:
        return []
    if not isinstance(raw_reactions, list):
        raise ValueError("Depletion chain reactions must be a list.")
    reactions = []
    for index, raw in enumerate(raw_reactions, start=1):
        if not isinstance(raw, Mapping):
            raise ValueError(f"Depletion chain reaction {index} must be a mapping.")
        yields = raw.get("yields", raw.get("fission_yields", {}))
        if yields is None:
            yields = {}
        if not isinstance(yields, Mapping):
            raise ValueError(f"Depletion chain reaction {index} yields must be a mapping.")
        reactions.append(
            DepletionReaction(
                reaction_type=str(raw.get("type", default_type)),
                target=_empty_to_none(raw.get("target")),
                branching_ratio=float(raw.get("branching_ratio", raw.get("branching", 1.0))),
                default_rate_per_s=float(raw.get("default_rate_per_s", raw.get("rate_per_s", 0.0)) or 0.0),
                fission_yields={str(name): float(value) for name, value in yields.items()},
            )
        )
    return reactions


def _reaction_to_json(reaction: DepletionReaction) -> dict[str, Any]:
    return {
        "type": reaction.reaction_type,
        "target": reaction.target,
        "branching_ratio": reaction.branching_ratio,
        "default_rate_per_s": reaction.default_rate_per_s,
        "fission_yields": dict(reaction.fission_yields),
    }


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    parsed = float(value)
    if parsed <= 0.0:
        return None
    return parsed


def _empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
