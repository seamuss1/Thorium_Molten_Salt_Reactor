from __future__ import annotations

from typing import Any, Mapping


MODEL_MATERIAL_REPRESENTATIONS = {"isotopic_explicit", "illustrative"}
FUEL_CYCLE_REPRESENTATIONS = {"proxy_breeding", "depletion_coupled", "illustrative"}
CORE_MODEL_KINDS = {"channelized_from_geometry", "homogenized_core"}
DEFAULT_ACTIVE_VARIANTS = ("fuel", "control_guides")
DEFAULT_STAGNANT_VARIANTS = ("instrumentation_wells",)


def get_model_representation(config: Any) -> dict[str, str]:
    data = _extract_data(config)
    raw = data.get("model_representation", {})
    if not isinstance(raw, Mapping):
        raw = {}
    materials = str(raw.get("materials", "illustrative"))
    fuel_cycle = str(raw.get("fuel_cycle", "illustrative"))
    if materials not in MODEL_MATERIAL_REPRESENTATIONS:
        materials = "illustrative"
    if fuel_cycle not in FUEL_CYCLE_REPRESENTATIONS:
        fuel_cycle = "illustrative"
    return {
        "materials": materials,
        "fuel_cycle": fuel_cycle,
    }


def get_core_model(config: Any) -> dict[str, Any]:
    data = _extract_data(config)
    flow = data.get("flow", {})
    if not isinstance(flow, Mapping):
        flow = {}
    raw = flow.get("core_model", {})
    if not isinstance(raw, Mapping):
        raw = {}

    kind = str(raw.get("kind", "channelized_from_geometry"))
    if kind not in CORE_MODEL_KINDS:
        kind = "channelized_from_geometry"

    if kind == "homogenized_core":
        return {
            "kind": kind,
            "effective_flow_area_cm2": float(raw.get("effective_flow_area_cm2", 0.0)),
            "active_salt_volume_cm3": float(raw.get("active_salt_volume_cm3", 0.0)),
            "hydraulic_diameter_cm": float(raw.get("hydraulic_diameter_cm", 0.0)),
        }

    active_variants = _normalize_name_list(raw.get("active_variants"), DEFAULT_ACTIVE_VARIANTS)
    stagnant_variants = _normalize_name_list(raw.get("stagnant_variants"), DEFAULT_STAGNANT_VARIANTS)
    family_split_weights = _normalize_number_map(raw.get("family_split_weights"))
    return {
        "kind": kind,
        "active_variants": active_variants,
        "stagnant_variants": stagnant_variants,
        "family_split_weights": family_split_weights,
    }


def case_uses_thorium_fuel(config: Any) -> bool:
    data = _extract_data(config)
    reactor = data.get("reactor", {})
    depletion = data.get("depletion", {})
    benchmark_name = str(reactor.get("benchmark", ""))
    haystacks = [
        str(reactor.get("name", "")),
        str(reactor.get("family", "")),
        str(depletion.get("chain", "")),
        benchmark_name,
    ]
    lowered = " ".join(haystacks).lower()
    return "thorium" in lowered or "tmsr" in lowered


def fuel_salt_has_thorium(config: Any) -> bool:
    data = _extract_data(config)
    materials = data.get("materials", {})
    if not isinstance(materials, Mapping):
        return False
    geometry = data.get("geometry", {})
    salt_material_name = "fuel_salt"
    if isinstance(geometry, Mapping):
        salt_material_name = str(geometry.get("salt_material", salt_material_name))
    fuel_salt = materials.get(salt_material_name, {})
    if not isinstance(fuel_salt, Mapping):
        return False
    for nuclide in fuel_salt.get("nuclides", []):
        if not isinstance(nuclide, Mapping):
            continue
        if str(nuclide.get("name", "")).lower() == "th232":
            return True
    return False


def _extract_data(config: Any) -> Mapping[str, Any]:
    if isinstance(config, Mapping):
        return config
    data = getattr(config, "data", None)
    if isinstance(data, Mapping):
        return data
    return {}


def _normalize_name_list(value: Any, fallback: tuple[str, ...]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    normalized: list[str] = []
    for item in value:
        name = str(item).strip()
        if name and name not in normalized:
            normalized.append(name)
    return normalized or list(fallback)


def _normalize_number_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return {}
    normalized: dict[str, float] = {}
    for key, raw in value.items():
        try:
            normalized[str(key)] = float(raw)
        except (TypeError, ValueError):
            continue
    return normalized
