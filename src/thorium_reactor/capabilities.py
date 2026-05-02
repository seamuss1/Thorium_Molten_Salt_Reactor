from __future__ import annotations

from typing import Any


NEUTRONICS_ONLY = "neutronics_only"
THERMAL_NETWORK = "thermal_network"
BALANCE_OF_PLANT = "balance_of_plant"
MSR_PRIMARY_SYSTEM = "msr_primary_system"
TRANSIENT_ANALYSIS = "transient_analysis"
COMMERCIAL_PLANNING = "commercial_planning"


class CapabilityConfigurationError(ValueError):
    """Raised when a case advertises a workflow capability but lacks required inputs."""


def get_case_capabilities(config: Any) -> set[str]:
    capabilities = {NEUTRONICS_ONLY}
    geometry = config.geometry
    render_layout = geometry.get("render_layout") or {}

    if geometry.get("kind") == "ring_lattice_core" and geometry.get("style") == "detailed_msr":
        capabilities.update({BALANCE_OF_PLANT, THERMAL_NETWORK})
    if render_layout.get("type") == "immersed_pool_reference":
        capabilities.add(MSR_PRIMARY_SYSTEM)
    if THERMAL_NETWORK in capabilities and BALANCE_OF_PLANT in capabilities:
        capabilities.add(TRANSIENT_ANALYSIS)
    if config.reactor.get("mode") == "commercial_grid":
        capabilities.add(COMMERCIAL_PLANNING)

    for capability, enabled in _capability_overrides(config).items():
        if enabled:
            capabilities.add(capability)
        else:
            capabilities.discard(capability)
    return capabilities


def case_supports_capability(config: Any, capability: str) -> bool:
    return capability in get_case_capabilities(config)


def resolve_primary_coolant_material_name(config: Any, capability: str | None = None) -> str:
    explicit_name = config.geometry.get("salt_material")
    if explicit_name:
        material_name = str(explicit_name)
        if material_name not in config.materials:
            raise CapabilityConfigurationError(
                _capability_message(
                    capability,
                    f"requires geometry.salt_material='{material_name}' to exist under materials.{material_name}.",
                )
            )
        return material_name
    if "fuel_salt" in config.materials:
        return "fuel_salt"
    raise CapabilityConfigurationError(
        _capability_message(
            capability,
            "requires geometry.salt_material to name the primary coolant material, "
            "or legacy materials.fuel_salt to be present.",
        )
    )


def validate_case_capability(config: Any, capability: str) -> None:
    if capability == BALANCE_OF_PLANT:
        _require_reactor_fields(
            config,
            capability,
            (
                "design_power_mwth",
                "hot_leg_temp_c",
                "cold_leg_temp_c",
                "steam_generator_effectiveness",
                "turbine_efficiency",
                "generator_efficiency",
            ),
        )
        material_name = resolve_primary_coolant_material_name(config, capability)
        material_spec = config.materials[material_name]
        if "cp" not in material_spec and "primary_cp_kj_kgk" not in config.reactor:
            raise CapabilityConfigurationError(
                _capability_message(
                    capability,
                    f"requires reactor.primary_cp_kj_kgk or materials.{material_name}.cp.",
                )
            )
        return

    if capability == THERMAL_NETWORK:
        material_name = resolve_primary_coolant_material_name(config, capability)
        material_spec = config.materials[material_name]
        if "density" not in material_spec:
            raise CapabilityConfigurationError(
                _capability_message(
                    capability,
                    f"requires materials.{material_name}.density for reduced-order flow calculations.",
                )
            )
        return

    if capability == MSR_PRIMARY_SYSTEM:
        primary_loop = (config.geometry.get("render_layout") or {}).get("primary_loop") or {}
        if not primary_loop.get("pipes"):
            raise CapabilityConfigurationError(
                _capability_message(
                    capability,
                    "requires geometry.render_layout.primary_loop.pipes for the reduced-order primary-loop model.",
                )
            )
        validate_case_capability(config, THERMAL_NETWORK)
        return

    if capability == TRANSIENT_ANALYSIS:
        validate_case_capability(config, BALANCE_OF_PLANT)
        validate_case_capability(config, THERMAL_NETWORK)
        return

    if capability == COMMERCIAL_PLANNING:
        if config.reactor.get("mode") != "commercial_grid":
            raise CapabilityConfigurationError(
                _capability_message(capability, "requires reactor.mode='commercial_grid'.")
            )
        if not config.economics:
            raise CapabilityConfigurationError(_capability_message(capability, "requires economics."))
        if not config.project_schedule:
            raise CapabilityConfigurationError(_capability_message(capability, "requires project_schedule."))


def _capability_overrides(config: Any) -> dict[str, bool]:
    workflow = config.data.get("workflow", {})
    if isinstance(workflow, dict):
        capabilities = workflow.get("capabilities", {})
        if isinstance(capabilities, dict):
            return {str(name): bool(enabled) for name, enabled in capabilities.items()}
    root_capabilities = config.data.get("capabilities", {})
    if isinstance(root_capabilities, dict):
        return {str(name): bool(enabled) for name, enabled in root_capabilities.items()}
    return {}


def _require_reactor_fields(config: Any, capability: str, names: tuple[str, ...]) -> None:
    for name in names:
        if config.reactor.get(name) is None:
            raise CapabilityConfigurationError(
                _capability_message(capability, f"requires reactor.{name}.")
            )


def _capability_message(capability: str | None, detail: str) -> str:
    if capability:
        return f"Capability '{capability}' {detail}"
    return detail
