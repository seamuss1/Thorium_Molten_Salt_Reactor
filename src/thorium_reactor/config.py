from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from thorium_reactor.modeling import (
    CORE_MODEL_KINDS,
    FUEL_CYCLE_REPRESENTATIONS,
    MODEL_MATERIAL_REPRESENTATIONS,
)
from thorium_reactor.precursors import SUPPORTED_PRECURSOR_TRANSPORT_MODELS


REQUIRED_CASE_KEYS = (
    "reactor",
    "materials",
    "geometry",
    "simulation",
    "reporting",
    "validation_targets",
)

SUPPORTED_PROPERTY_UNITS: dict[str, set[str]] = {
    "density": {"g/cm3", "kg/m3"},
    "cp": {"j/kg-k", "kj/kg-k"},
    "thermal_conductivity": {"w/m-k"},
    "dynamic_viscosity": {"pa-s"},
}
PROPERTY_MODEL_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "constant": ("value",),
    "linear": ("reference_value", "reference_temperature_c", "slope_per_c"),
    "arrhenius": ("pre_exponential", "activation_temperature_k"),
}
SUPPORTED_BOUNDARY_TYPES = {"reflective", "vacuum", "periodic", "transmission", "white"}
SUPPORTED_SOURCE_TYPES = {"point"}
SUPPORTED_REACTOR_MODES = {"historic_benchmark", "modern_test_reactor", "aspirational_breeder"}
SUPPORTED_PROPERTY_PROVIDERS = {"legacy_correlation", "evaluated_table", "thermochemical_equilibrium"}
SUPPORTED_INTEGRATIONS = ("moose", "scale", "thermochimica", "saltproc", "moltres")


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
    def flow(self) -> dict[str, Any]:
        return self.data.get("flow", {})

    @property
    def model_representation(self) -> dict[str, Any]:
        return self.data.get("model_representation", {})

    @property
    def validation_targets(self) -> dict[str, Any]:
        return self.data["validation_targets"]

    @property
    def benchmark_file(self) -> Path | None:
        return resolve_benchmark_path(self.path.parents[3], self.data)


def load_case_config(path: Path) -> CaseConfig:
    with path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    missing = [key for key in REQUIRED_CASE_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"Case config {path} is missing required keys: {', '.join(missing)}")
    _validate_case_schema(path, raw)

    name = raw.get("name") or path.parent.name
    return CaseConfig(name=name, path=path, data=raw)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def resolve_benchmark_path(repo_root: Path, data: Mapping[str, Any]) -> Path | None:
    reactor = data.get("reactor")
    if not isinstance(reactor, Mapping):
        return None
    raw_path = reactor.get("benchmark")
    if not raw_path:
        return None
    return (repo_root / str(raw_path)).resolve()


def _validate_case_schema(path: Path, raw: Mapping[str, Any]) -> None:
    _validate_material_properties(path, raw.get("materials"))
    _validate_reactor_settings(path, raw.get("reactor"))
    _validate_geometry_settings(path, raw.get("geometry"))
    _validate_simulation_settings(path, raw.get("simulation"))
    _validate_optional_transient_settings(path, raw.get("transient"))
    _validate_optional_depletion_settings(path, raw.get("depletion"))
    _validate_optional_chemistry_settings(path, raw.get("chemistry"))
    _validate_optional_properties_settings(path, raw.get("properties"))
    _validate_optional_property_uncertainty_settings(path, raw.get("property_uncertainty"))
    _validate_optional_tritium_settings(path, raw.get("tritium"))
    _validate_optional_graphite_lifetime_settings(path, raw.get("graphite_lifetime"))
    _validate_optional_loop_segments(path, raw.get("loop_segments"))
    _validate_validation_targets_settings(path, raw.get("validation_targets"))
    _validate_optional_flow_settings(path, raw.get("flow"))
    _validate_optional_model_representation(path, raw.get("model_representation"))
    _validate_optional_integrations(path, raw.get("integrations"))


def _validate_reactor_settings(path: Path, reactor: Any) -> None:
    if not isinstance(reactor, Mapping):
        raise ConfigError(f"Case config {path} has invalid 'reactor'; expected a mapping.")
    mode = reactor.get("mode")
    if mode is not None and mode not in SUPPORTED_REACTOR_MODES:
        supported = ", ".join(sorted(SUPPORTED_REACTOR_MODES))
        raise ConfigError(
            f"Case config {path} reactor.mode '{mode}' is unsupported. Supported values: {supported}."
        )


def _validate_material_properties(path: Path, materials: Any) -> None:
    if not isinstance(materials, Mapping):
        raise ConfigError(f"Case config {path} has invalid 'materials'; expected a mapping.")
    for material_name, material_spec in materials.items():
        if not isinstance(material_spec, Mapping):
            raise ConfigError(f"Case config {path} material '{material_name}' must be a mapping.")
        for quantity, supported_units in SUPPORTED_PROPERTY_UNITS.items():
            property_spec = material_spec.get(quantity)
            if property_spec is None:
                continue
            if not isinstance(property_spec, Mapping):
                raise ConfigError(
                    f"Case config {path} material '{material_name}' property '{quantity}' must be a mapping."
                )
            units = property_spec.get("units")
            if units not in supported_units:
                supported = ", ".join(sorted(supported_units))
                raise ConfigError(
                    f"Case config {path} material '{material_name}' property '{quantity}' has unsupported units "
                    f"'{units}'. Supported units: {supported}."
                )
            provider = str(property_spec.get("provider", "legacy_correlation"))
            if provider not in SUPPORTED_PROPERTY_PROVIDERS:
                supported = ", ".join(sorted(SUPPORTED_PROPERTY_PROVIDERS))
                raise ConfigError(
                    f"Case config {path} material '{material_name}' property '{quantity}' has unsupported provider "
                    f"'{provider}'. Supported values: {supported}."
                )
            if provider == "evaluated_table":
                temperatures = property_spec.get("temperatures_c")
                values = property_spec.get("values")
                if "table_path" not in property_spec and (
                    not isinstance(temperatures, list)
                    or not isinstance(values, list)
                    or len(temperatures) != len(values)
                    or len(temperatures) < 2
                ):
                    raise ConfigError(
                        f"Case config {path} material '{material_name}' property '{quantity}' must provide either "
                        "table_path or matching temperatures_c and values arrays with at least two points."
                    )
                continue
            if provider == "thermochemical_equilibrium":
                if not any(field in property_spec for field in ("fallback_value", "reference_value", "value")):
                    raise ConfigError(
                        f"Case config {path} material '{material_name}' property '{quantity}' must declare "
                        "fallback_value, reference_value, or value for thermochemical_equilibrium mode."
                    )
                continue

            model = str(property_spec.get("model", "constant"))
            required_fields = PROPERTY_MODEL_REQUIRED_FIELDS.get(model)
            if required_fields is None:
                supported_models = ", ".join(sorted(PROPERTY_MODEL_REQUIRED_FIELDS))
                raise ConfigError(
                    f"Case config {path} material '{material_name}' property '{quantity}' has unsupported model "
                    f"'{model}'. Supported models: {supported_models}."
                )
            missing_fields = [field for field in required_fields if field not in property_spec]
            if missing_fields:
                raise ConfigError(
                    f"Case config {path} material '{material_name}' property '{quantity}' is missing required "
                    f"fields for model '{model}': {', '.join(missing_fields)}."
                )


def _validate_geometry_settings(path: Path, geometry: Any) -> None:
    if not isinstance(geometry, Mapping):
        raise ConfigError(f"Case config {path} has invalid 'geometry'; expected a mapping.")
    boundary = geometry.get("boundary", "reflective")
    if boundary not in SUPPORTED_BOUNDARY_TYPES:
        supported = ", ".join(sorted(SUPPORTED_BOUNDARY_TYPES))
        raise ConfigError(
            f"Case config {path} geometry.boundary '{boundary}' is unsupported. Supported values: {supported}."
        )
    axial_boundary = geometry.get("axial_boundary")
    if axial_boundary is not None and axial_boundary not in SUPPORTED_BOUNDARY_TYPES:
        supported = ", ".join(sorted(SUPPORTED_BOUNDARY_TYPES))
        raise ConfigError(
            f"Case config {path} geometry.axial_boundary '{axial_boundary}' is unsupported. Supported values: {supported}."
        )


def _validate_simulation_settings(path: Path, simulation: Any) -> None:
    if not isinstance(simulation, Mapping):
        raise ConfigError(f"Case config {path} has invalid 'simulation'; expected a mapping.")
    particles = _require_positive_int(path, "simulation.particles", simulation.get("particles"))
    batches = _require_positive_int(path, "simulation.batches", simulation.get("batches"))
    inactive = _require_non_negative_int(path, "simulation.inactive", simulation.get("inactive", 0))
    if inactive >= batches:
        raise ConfigError(
            f"Case config {path} must keep simulation.inactive ({inactive}) below simulation.batches ({batches})."
        )
    source = simulation.get("source", {"type": "point", "parameters": [0.0, 0.0, 0.0]})
    if not isinstance(source, Mapping):
        raise ConfigError(f"Case config {path} simulation.source must be a mapping.")
    source_type = source.get("type", "point")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_TYPES))
        raise ConfigError(
            f"Case config {path} simulation.source.type '{source_type}' is unsupported. Supported values: {supported}."
        )
    if source_type == "point":
        parameters = source.get("parameters", [])
        if not isinstance(parameters, list) or len(parameters) != 3:
            raise ConfigError(
                f"Case config {path} simulation.source.parameters must contain exactly three coordinates for a point source."
            )
    tallies = simulation.get("tallies", [])
    if tallies is None:
        tallies = []
    if not isinstance(tallies, list):
        raise ConfigError(f"Case config {path} simulation.tallies must be a list when provided.")
    for index, tally in enumerate(tallies, start=1):
        if not isinstance(tally, Mapping):
            raise ConfigError(f"Case config {path} simulation.tallies[{index}] must be a mapping.")
        if not tally.get("cell"):
            raise ConfigError(f"Case config {path} simulation.tallies[{index}] must declare a target cell.")
        scores = tally.get("scores")
        if not isinstance(scores, list) or not scores:
            raise ConfigError(f"Case config {path} simulation.tallies[{index}] must declare at least one score.")


def _require_positive_int(path: Path, field_name: str, value: Any) -> int:
    parsed = _coerce_int(path, field_name, value)
    if parsed <= 0:
        raise ConfigError(f"Case config {path} field '{field_name}' must be a positive integer.")
    return parsed


def _require_non_negative_int(path: Path, field_name: str, value: Any) -> int:
    parsed = _coerce_int(path, field_name, value)
    if parsed < 0:
        raise ConfigError(f"Case config {path} field '{field_name}' must be a non-negative integer.")
    return parsed


def _coerce_int(path: Path, field_name: str, value: Any) -> int:
    if isinstance(value, bool) or value is None:
        raise ConfigError(f"Case config {path} field '{field_name}' must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    raise ConfigError(f"Case config {path} field '{field_name}' must be an integer.")


def _validate_optional_transient_settings(path: Path, transient: Any) -> None:
    if transient is None:
        return
    if not isinstance(transient, Mapping):
        raise ConfigError(f"Case config {path} optional 'transient' section must be a mapping.")
    for field_name in (
        "duration_s",
        "time_step_s",
        "power_response_time_s",
        "fuel_temperature_response_time_s",
        "graphite_temperature_response_time_s",
        "coolant_temperature_response_time_s",
        "precursor_inventory_response_time_s",
        "precursor_transport_response_time_s",
        "xenon_response_time_s",
        "reactivity_to_power_scale_pcm",
        "fuel_temperature_feedback_pcm_per_c",
        "graphite_temperature_feedback_pcm_per_c",
        "coolant_temperature_feedback_pcm_per_c",
        "precursor_worth_pcm",
        "xenon_worth_pcm_per_fraction",
        "max_power_fraction",
    ):
        if field_name in transient:
            _require_number(path, f"transient.{field_name}", transient[field_name])
    precursor_groups = transient.get("delayed_neutron_precursor_groups")
    precursor_transport_model = transient.get("precursor_transport_model")
    if precursor_transport_model is not None and precursor_transport_model not in SUPPORTED_PRECURSOR_TRANSPORT_MODELS:
        supported = ", ".join(sorted(SUPPORTED_PRECURSOR_TRANSPORT_MODELS))
        raise ConfigError(
            f"Case config {path} transient.precursor_transport_model '{precursor_transport_model}' is unsupported. "
            f"Supported values: {supported}."
        )
    if precursor_groups is not None:
        if not isinstance(precursor_groups, list):
            raise ConfigError(f"Case config {path} transient.delayed_neutron_precursor_groups must be a list.")
        if not precursor_groups:
            raise ConfigError(
                f"Case config {path} transient.delayed_neutron_precursor_groups must contain at least one group."
            )
        total_yield = 0.0
        for index, group in enumerate(precursor_groups, start=1):
            if not isinstance(group, Mapping):
                raise ConfigError(
                    f"Case config {path} transient.delayed_neutron_precursor_groups[{index}] must be a mapping."
                )
            decay_constant = _require_number(
                path,
                f"transient.delayed_neutron_precursor_groups[{index}].decay_constant_s",
                group.get("decay_constant_s"),
            )
            if decay_constant <= 0.0:
                raise ConfigError(
                    f"Case config {path} field "
                    f"'transient.delayed_neutron_precursor_groups[{index}].decay_constant_s' must be positive."
                )
            yield_fraction = _require_number(
                path,
                f"transient.delayed_neutron_precursor_groups[{index}].yield_fraction",
                group.get("yield_fraction"),
            )
            if yield_fraction < 0.0:
                raise ConfigError(
                    f"Case config {path} field "
                    f"'transient.delayed_neutron_precursor_groups[{index}].yield_fraction' must be non-negative."
                )
            total_yield += yield_fraction
        if total_yield <= 0.0:
            raise ConfigError(
                f"Case config {path} transient.delayed_neutron_precursor_groups total yield_fraction must be positive."
            )
    scenarios = transient.get("scenarios")
    if scenarios is not None:
        if not isinstance(scenarios, list):
            raise ConfigError(f"Case config {path} transient.scenarios must be a list.")
        for index, scenario in enumerate(scenarios, start=1):
            if not isinstance(scenario, Mapping):
                raise ConfigError(f"Case config {path} transient.scenarios[{index}] must be a mapping.")
            if "duration_s" in scenario:
                _require_number(path, f"transient.scenarios[{index}].duration_s", scenario["duration_s"])
            if "time_step_s" in scenario:
                _require_number(path, f"transient.scenarios[{index}].time_step_s", scenario["time_step_s"])
            events = scenario.get("events")
            if events is not None:
                if not isinstance(events, list):
                    raise ConfigError(f"Case config {path} transient.scenarios[{index}].events must be a list.")
                for event_index, event in enumerate(events, start=1):
                    if not isinstance(event, Mapping):
                        raise ConfigError(
                            f"Case config {path} transient.scenarios[{index}].events[{event_index}] must be a mapping."
                        )
                    _require_number(
                        path,
                        f"transient.scenarios[{index}].events[{event_index}].time_s",
                        event.get("time_s", 0.0),
                    )


def _validate_optional_depletion_settings(path: Path, depletion: Any) -> None:
    if depletion is None:
        return
    if not isinstance(depletion, Mapping):
        raise ConfigError(f"Case config {path} optional 'depletion' section must be a mapping.")
    for field_name in (
        "volatile_removal_efficiency",
        "xenon_removal_fraction",
        "protactinium_holdup_days",
        "initial_fissile_inventory_fraction",
        "fissile_burn_fraction_per_day_full_power",
        "breeding_gain_fraction_per_day",
        "minor_actinide_sink_fraction_per_day",
    ):
        if field_name in depletion:
            _require_number(path, f"depletion.{field_name}", depletion[field_name])


def _validate_optional_chemistry_settings(path: Path, chemistry: Any) -> None:
    if chemistry is None:
        return
    if not isinstance(chemistry, Mapping):
        raise ConfigError(f"Case config {path} optional 'chemistry' section must be a mapping.")
    for field_name in (
        "target_redox_state_ev",
        "initial_redox_state_ev",
        "redox_control_time_days",
        "oxidant_ingress_fraction_per_day",
        "impurity_capture_efficiency",
        "gas_stripping_efficiency",
        "noble_metal_plateout_fraction",
        "corrosion_acceleration_per_ev",
        "tritium_release_fraction",
    ):
        if field_name in chemistry:
            _require_number(path, f"chemistry.{field_name}", chemistry[field_name])


def _validate_optional_properties_settings(path: Path, properties: Any) -> None:
    if properties is None:
        return
    if not isinstance(properties, Mapping):
        raise ConfigError(f"Case config {path} optional 'properties' section must be a mapping.")
    provider = properties.get("provider")
    if provider is not None and provider not in SUPPORTED_PROPERTY_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROPERTY_PROVIDERS))
        raise ConfigError(
            f"Case config {path} properties.provider '{provider}' is unsupported. Supported values: {supported}."
        )


def _validate_optional_property_uncertainty_settings(path: Path, property_uncertainty: Any) -> None:
    if property_uncertainty is None:
        return
    if not isinstance(property_uncertainty, Mapping):
        raise ConfigError(f"Case config {path} optional 'property_uncertainty' section must be a mapping.")
    for field_name in (
        "confidence_level",
        "density_uncertainty_95_fraction",
        "cp_uncertainty_95_fraction",
        "thermal_conductivity_uncertainty_95_fraction",
        "dynamic_viscosity_uncertainty_95_fraction",
        "core_outlet_temperature_uncertainty_95_c",
    ):
        if field_name in property_uncertainty:
            _require_number(path, f"property_uncertainty.{field_name}", property_uncertainty[field_name])


def _validate_optional_tritium_settings(path: Path, tritium: Any) -> None:
    if tritium is None:
        return
    if not isinstance(tritium, Mapping):
        raise ConfigError(f"Case config {path} optional 'tritium' section must be a mapping.")
    for field_name in (
        "lithium6_atom_fraction",
        "reference_lithium6_atom_fraction",
        "reference_power_mwth",
        "gas_stripping_efficiency",
        "reference_gas_stripping_efficiency",
        "unmitigated_environment_fraction",
        "mitigated_environment_fraction",
        "spray_gas_removal_fraction",
        "screening_operation_years",
        "graphite_saturation_years",
        "graphite_saturation_release_penalty",
        "graphite_retention_fraction",
    ):
        if field_name in tritium:
            _require_number(path, f"tritium.{field_name}", tritium[field_name])


def _validate_optional_graphite_lifetime_settings(path: Path, graphite_lifetime: Any) -> None:
    if graphite_lifetime is None:
        return
    if not isinstance(graphite_lifetime, Mapping):
        raise ConfigError(f"Case config {path} optional 'graphite_lifetime' section must be a mapping.")
    for field_name in (
        "target_fuel_volume_fraction",
        "core_zoning_flattening_credit",
        "hexagonal_prism_assembly_credit",
        "fast_flux_peaking_factor",
        "nominal_max_fast_flux_n_cm2_s",
        "fast_fluence_limit_n_cm2",
        "capacity_factor",
        "target_lifespan_years",
        "reference_power_density_mw_m3",
        "reference_fast_flux_n_cm2_s",
    ):
        if field_name in graphite_lifetime:
            _require_number(path, f"graphite_lifetime.{field_name}", graphite_lifetime[field_name])


def _validate_optional_loop_segments(path: Path, loop_segments: Any) -> None:
    if loop_segments is None:
        return
    if not isinstance(loop_segments, list):
        raise ConfigError(f"Case config {path} optional 'loop_segments' section must be a list.")
    for index, segment in enumerate(loop_segments, start=1):
        if not isinstance(segment, Mapping):
            raise ConfigError(f"Case config {path} loop_segments[{index}] must be a mapping.")
        if not segment.get("id"):
            raise ConfigError(f"Case config {path} loop_segments[{index}] must define an id.")
        for field_name in ("residence_fraction", "volume_fraction", "decay_heat_fraction", "cleanup_weight", "cleanup_fraction"):
            if field_name in segment:
                _require_number(path, f"loop_segments[{index}].{field_name}", segment[field_name])


def _validate_validation_targets_settings(path: Path, validation_targets: Any) -> None:
    if not isinstance(validation_targets, Mapping):
        raise ConfigError(f"Case config {path} has invalid 'validation_targets'; expected a mapping.")
    for name, target in validation_targets.items():
        if not isinstance(target, Mapping):
            raise ConfigError(f"Case config {path} validation_targets.{name} must be a mapping.")
        benchmark_target_ids = target.get("benchmark_target_ids")
        if benchmark_target_ids is not None and not isinstance(benchmark_target_ids, list):
            raise ConfigError(
                f"Case config {path} validation_targets.{name}.benchmark_target_ids must be a list."
            )
        if isinstance(benchmark_target_ids, list):
            for index, value in enumerate(benchmark_target_ids, start=1):
                if not str(value).strip():
                    raise ConfigError(
                        f"Case config {path} validation_targets.{name}.benchmark_target_ids[{index}] must be a non-empty string."
                    )


def _validate_optional_flow_settings(path: Path, flow: Any) -> None:
    if flow is None:
        return
    if not isinstance(flow, Mapping):
        raise ConfigError(f"Case config {path} optional 'flow' section must be a mapping.")
    core_model = flow.get("core_model")
    if core_model is None:
        return
    if not isinstance(core_model, Mapping):
        raise ConfigError(f"Case config {path} flow.core_model must be a mapping.")
    kind = str(core_model.get("kind", "channelized_from_geometry"))
    if kind not in CORE_MODEL_KINDS:
        supported = ", ".join(sorted(CORE_MODEL_KINDS))
        raise ConfigError(
            f"Case config {path} flow.core_model.kind '{kind}' is unsupported. Supported values: {supported}."
        )
    if kind == "channelized_from_geometry":
        for field_name in ("active_variants", "stagnant_variants"):
            if field_name in core_model and not isinstance(core_model[field_name], list):
                raise ConfigError(f"Case config {path} flow.core_model.{field_name} must be a list.")
        family_split_weights = core_model.get("family_split_weights")
        if family_split_weights is not None and not isinstance(family_split_weights, Mapping):
            raise ConfigError(f"Case config {path} flow.core_model.family_split_weights must be a mapping.")
        if isinstance(family_split_weights, Mapping):
            for key, value in family_split_weights.items():
                _require_number(path, f"flow.core_model.family_split_weights.{key}", value)
        return
    for field_name in ("effective_flow_area_cm2", "active_salt_volume_cm3", "hydraulic_diameter_cm"):
        _require_number(path, f"flow.core_model.{field_name}", core_model.get(field_name))


def _validate_optional_model_representation(path: Path, model_representation: Any) -> None:
    if model_representation is None:
        return
    if not isinstance(model_representation, Mapping):
        raise ConfigError(f"Case config {path} optional 'model_representation' section must be a mapping.")
    materials = model_representation.get("materials")
    if materials is not None and materials not in MODEL_MATERIAL_REPRESENTATIONS:
        supported = ", ".join(sorted(MODEL_MATERIAL_REPRESENTATIONS))
        raise ConfigError(
            f"Case config {path} model_representation.materials '{materials}' is unsupported. Supported values: {supported}."
        )
    fuel_cycle = model_representation.get("fuel_cycle")
    if fuel_cycle is not None and fuel_cycle not in FUEL_CYCLE_REPRESENTATIONS:
        supported = ", ".join(sorted(FUEL_CYCLE_REPRESENTATIONS))
        raise ConfigError(
            f"Case config {path} model_representation.fuel_cycle '{fuel_cycle}' is unsupported. Supported values: {supported}."
        )


def _require_number(path: Path, field_name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ConfigError(f"Case config {path} field '{field_name}' must be numeric.")
    return float(value)


def _validate_optional_integrations(path: Path, integrations: Any) -> None:
    if integrations is None:
        return
    if not isinstance(integrations, Mapping):
        raise ConfigError(f"Case config {path} optional 'integrations' section must be a mapping.")
    for integration_name in SUPPORTED_INTEGRATIONS:
        settings = integrations.get(integration_name)
        if settings is None:
            continue
        if not isinstance(settings, Mapping):
            raise ConfigError(f"Case config {path} integrations.{integration_name} must be a mapping.")
        args = settings.get("args")
        if args is not None and not isinstance(args, list):
            raise ConfigError(f"Case config {path} integrations.{integration_name}.args must be a list.")
