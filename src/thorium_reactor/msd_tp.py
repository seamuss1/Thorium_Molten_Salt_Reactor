from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any


GAS_CONSTANT_J_MOL_K = 8.31446261815324
DIRECT_DATA_FILE = "Molten_Salt_Thermophysical_Properties.csv"
RK_DENSITY_FILE = "Molten_Salt_Thermophysical_Properties_rho_RK.csv"
RK_VISCOSITY_FILE = "Molten_Salt_Thermophysical_Properties_mu_RK.csv"
DATA_PACKAGE = "thorium_reactor.data.msd_tp"
DATA_DIR_ENV_VAR = "THORIUM_REACTOR_MSD_TP_DATA_DIR"
_DATABASE_CACHE: dict[str, "MsdTpDatabase"] = {}


@dataclass(frozen=True)
class PropertyEvaluation:
    value: float
    metadata: dict[str, Any]


@dataclass(frozen=True)
class PropertyModel:
    kind: str
    coefficients: tuple[float, ...]
    source_units: str
    valid_temperature_range_k: tuple[float, float] | None
    uncertainty_fraction_95: float | None
    reference: str


@dataclass(frozen=True)
class DirectRecord:
    formula: str
    components: tuple[str, ...]
    mole_fractions: tuple[float, ...]
    molecular_weight_g_mol: float
    row_id: str
    properties: dict[str, PropertyModel]


@dataclass(frozen=True)
class RkModel:
    components: tuple[str, str]
    property_name: str
    a: tuple[float, ...]
    b: tuple[float, ...]
    c: tuple[float, ...]
    valid_temperature_range_k: tuple[float, float]
    reference: str


class MsdTpDatabase:
    def __init__(self, base_path: Path | None = None) -> None:
        self.base_path = base_path
        self.records = self._load_direct_records()
        self._records_by_key = {_record_key(record.components, record.mole_fractions): record for record in self.records}
        self.rk_density = self._load_rk_models("density")
        self.rk_viscosity = self._load_rk_models("dynamic_viscosity")

    def evaluate(
        self,
        spec: dict[str, Any],
        *,
        temperature_c: float,
        expected_quantity: str,
    ) -> PropertyEvaluation:
        provider = str(spec.get("provider", "msd_tp"))
        if provider == "msd_tp_redlich_kister":
            return self._evaluate_rk(spec, temperature_c=temperature_c, expected_quantity=expected_quantity)
        return self._evaluate_direct(spec, temperature_c=temperature_c, expected_quantity=expected_quantity)

    def describe_record(
        self,
        spec: dict[str, Any],
        *,
        temperature_c: float | None = None,
        expected_quantity: str | None = None,
    ) -> dict[str, Any]:
        quantity = expected_quantity or _quantity_from_spec(spec)
        if temperature_c is None:
            metadata = {
                "provider": str(spec.get("provider", "msd_tp")),
                "source": "ORNL MSD-TP",
                "formula": spec.get("formula"),
                "composition": spec.get("composition"),
                "status": "not_evaluated",
            }
            return metadata
        return self.evaluate(spec, temperature_c=temperature_c, expected_quantity=quantity).metadata

    def _evaluate_direct(
        self,
        spec: dict[str, Any],
        *,
        temperature_c: float,
        expected_quantity: str,
    ) -> PropertyEvaluation:
        property_name = _property_name(expected_quantity)
        record = self._record_for_spec(spec)
        model = record.properties.get(property_name)
        if model is None:
            raise ValueError(
                f"MSD-TP record {record.formula} {composition_label(record.mole_fractions)} "
                f"does not provide {property_name}."
            )

        temperature_k = float(temperature_c) + 273.15
        range_status = _range_status(model.valid_temperature_range_k, temperature_k)
        if range_status == "out_of_range" and not bool(spec.get("allow_extrapolation", False)):
            lo, hi = model.valid_temperature_range_k or (0.0, 0.0)
            raise ValueError(
                f"MSD-TP {property_name} record {record.formula} {composition_label(record.mole_fractions)} "
                f"is valid for {lo:g}-{hi:g} K; requested {temperature_k:g} K."
            )

        source_value = _evaluate_direct_model(model, temperature_k)
        output_value, output_units = _convert_msd_tp_value(
            source_value,
            property_name=property_name,
            model=model,
            record=record,
            requested_units=str(spec.get("units", "")),
        )
        return PropertyEvaluation(
            value=output_value,
            metadata=_direct_metadata(
                record=record,
                property_name=property_name,
                model=model,
                temperature_k=temperature_k,
                range_status=range_status,
                output_units=output_units,
                output_value=output_value,
                allow_extrapolation=bool(spec.get("allow_extrapolation", False)),
            ),
        )

    def _evaluate_rk(
        self,
        spec: dict[str, Any],
        *,
        temperature_c: float,
        expected_quantity: str,
    ) -> PropertyEvaluation:
        property_name = _property_name(expected_quantity)
        if property_name not in {"density", "dynamic_viscosity"}:
            raise ValueError(f"MSD-TP Redlich-Kister estimates do not support {property_name}.")

        components, mole_fractions = components_from_spec(spec)
        if len(components) != 2:
            raise ValueError("MSD-TP Redlich-Kister support is intentionally limited to binary mixtures.")

        pairs = sorted(zip(components, mole_fractions), key=lambda item: item[0])
        sorted_components = tuple(item[0] for item in pairs)
        sorted_fractions = tuple(float(item[1]) for item in pairs)
        temperature_k = float(temperature_c) + 273.15
        models = self.rk_density if property_name == "density" else self.rk_viscosity
        rk_model = models.get(sorted_components)
        if rk_model is None:
            raise ValueError(f"MSD-TP Redlich-Kister {property_name} model is missing for {'-'.join(sorted_components)}.")
        range_status = _range_status(rk_model.valid_temperature_range_k, temperature_k)
        if range_status == "out_of_range" and not bool(spec.get("allow_extrapolation", False)):
            lo, hi = rk_model.valid_temperature_range_k
            raise ValueError(
                f"MSD-TP Redlich-Kister {property_name} model for {'-'.join(sorted_components)} "
                f"is valid for {lo:g}-{hi:g} K; requested {temperature_k:g} K."
            )

        end_members = [self._pure_record(component) for component in sorted_components]
        if property_name == "density":
            source_value = self._rk_density_value(rk_model, end_members, sorted_fractions, temperature_k)
        else:
            source_value = self._rk_viscosity_value(rk_model, end_members, sorted_fractions, temperature_k)
        output_value, output_units = _convert_msd_tp_value(
            source_value,
            property_name=property_name,
            model=PropertyModel(
                kind="redlich_kister",
                coefficients=(),
                source_units="g/cm3" if property_name == "density" else "mN*s/m2",
                valid_temperature_range_k=rk_model.valid_temperature_range_k,
                uncertainty_fraction_95=None,
                reference=rk_model.reference,
            ),
            record=end_members[0],
            requested_units=str(spec.get("units", "")),
        )
        return PropertyEvaluation(
            value=output_value,
            metadata={
                "provider": "msd_tp_redlich_kister",
                "source": "ORNL MSD-TP",
                "source_kind": "redlich_kister_estimate",
                "property": property_name,
                "formula": "-".join(sorted_components),
                "composition": composition_label(sorted_fractions),
                "temperature_k": _round(temperature_k),
                "valid_temperature_range_k": _range_dict(rk_model.valid_temperature_range_k),
                "range_status": range_status,
                "allow_extrapolation": bool(spec.get("allow_extrapolation", False)),
                "output_value": _round(output_value),
                "output_units": output_units,
                "source_units": "g/cm3" if property_name == "density" else "mN*s/m2",
                "reference": rk_model.reference,
                "component_records": [member.row_id for member in end_members],
            },
        )

    def _record_for_spec(self, spec: dict[str, Any]) -> DirectRecord:
        components, mole_fractions = components_from_spec(spec)
        key = _record_key(components, mole_fractions)
        record = self._records_by_key.get(key)
        if record is not None:
            return record
        formula = "-".join(components)
        candidates = [
            composition_label(record.mole_fractions)
            for record in self.records
            if tuple(sorted(record.components)) == tuple(sorted(components))
        ]
        available = ", ".join(candidates[:8])
        suffix = f" Available compositions include: {available}." if available else ""
        raise ValueError(f"MSD-TP record is missing for {formula} {composition_label(mole_fractions)}.{suffix}")

    def _pure_record(self, component: str) -> DirectRecord:
        key = _record_key((component,), (1.0,))
        record = self._records_by_key.get(key)
        if record is None:
            raise ValueError(f"MSD-TP pure-component record is missing for {component}.")
        return record

    def _rk_density_value(
        self,
        model: RkModel,
        end_members: list[DirectRecord],
        mole_fractions: tuple[float, float],
        temperature_k: float,
    ) -> float:
        masses = []
        volumes = []
        for record, fraction in zip(end_members, mole_fractions):
            direct = record.properties.get("density")
            if direct is None:
                raise ValueError(f"MSD-TP pure-component density is missing for {record.formula}.")
            rho = _evaluate_direct_model(direct, temperature_k)
            mass = fraction * record.molecular_weight_g_mol
            masses.append(mass)
            volumes.append(mass / rho)
        rho_ideal = sum(masses) / sum(volumes)
        return rho_ideal + _rk_excess(model, mole_fractions[0], mole_fractions[1], temperature_k)

    def _rk_viscosity_value(
        self,
        model: RkModel,
        end_members: list[DirectRecord],
        mole_fractions: tuple[float, float],
        temperature_k: float,
    ) -> float:
        mu_ideal = 0.0
        for record, fraction in zip(end_members, mole_fractions):
            direct = record.properties.get("dynamic_viscosity")
            if direct is None:
                raise ValueError(f"MSD-TP pure-component viscosity is missing for {record.formula}.")
            mu_ideal += math.log(_evaluate_direct_model(direct, temperature_k)) * fraction
        return math.exp(mu_ideal + _rk_excess(model, mole_fractions[0], mole_fractions[1], temperature_k))

    def _load_direct_records(self) -> list[DirectRecord]:
        rows = _read_csv_rows(self._data_path(DIRECT_DATA_FILE))
        records: list[DirectRecord] = []
        for row in rows[2:]:
            if not row or not row[0].strip():
                continue
            formula = row[0].strip()
            composition = _parse_composition(row[3])
            components = tuple(formula.split("-"))
            molecular_weight = _parse_required_number(row[2], f"{formula} molecular weight")
            properties: dict[str, PropertyModel] = {}
            density = _property_model(
                kind="density_linear",
                coefficients=(row[10], row[11]),
                source_units="g/cm3",
                range_token=row[12],
                uncertainty_token=row[13],
                reference=row[14],
            )
            if density is not None:
                properties["density"] = density
            viscosity = _viscosity_model(row)
            if viscosity is not None:
                properties["dynamic_viscosity"] = viscosity
            conductivity = _property_model(
                kind="thermal_conductivity_linear",
                coefficients=(row[23], row[24]),
                source_units="W/m-K",
                range_token=row[25],
                uncertainty_token=row[26],
                reference=row[27],
            )
            if conductivity is not None:
                properties["thermal_conductivity"] = conductivity
            heat_capacity = _property_model(
                kind="heat_capacity_molar_polynomial",
                coefficients=(row[28], row[29], row[30], row[31]),
                source_units="J/K-mol",
                range_token="",
                uncertainty_token=row[32],
                reference=row[33],
            )
            if heat_capacity is not None:
                properties["cp"] = heat_capacity
            records.append(
                DirectRecord(
                    formula=formula,
                    components=components,
                    mole_fractions=composition,
                    molecular_weight_g_mol=molecular_weight,
                    row_id=str(row[1]).strip(),
                    properties=properties,
                )
            )
        return records

    def _load_rk_models(self, property_name: str) -> dict[tuple[str, str], RkModel]:
        filename = RK_DENSITY_FILE if property_name == "density" else RK_VISCOSITY_FILE
        rows = _read_csv_rows(self._data_path(filename))
        models: dict[tuple[str, str], RkModel] = {}
        for row in rows[1:]:
            if not row or not row[0].strip():
                continue
            c1 = row[0].strip()
            c2 = row[1].strip()
            sort_factor = 1.0 if c1 < c2 else -1.0
            components = tuple(sorted((c1, c2)))
            if property_name == "density":
                a = (_parse_required_number(row[2], "RK density A1"), sort_factor * _parse_required_number(row[4], "RK density A2"), _parse_required_number(row[6], "RK density A3"))
                b = (_parse_required_number(row[3], "RK density B1"), sort_factor * _parse_required_number(row[5], "RK density B2"), _parse_required_number(row[7], "RK density B3"))
                c = (0.0, 0.0, 0.0)
                t_min = _parse_required_number(row[8], "RK density T min")
                t_max = _parse_required_number(row[9], "RK density T max")
                reference = ",".join(row[10:]).strip().strip('"')
            else:
                a = (_parse_required_number(row[2], "RK viscosity A1"), sort_factor * _parse_required_number(row[5], "RK viscosity A2"), _parse_required_number(row[8], "RK viscosity A3"))
                b = (_parse_required_number(row[3], "RK viscosity B1"), sort_factor * _parse_required_number(row[6], "RK viscosity B2"), _parse_required_number(row[9], "RK viscosity B3"))
                c = (_parse_required_number(row[4], "RK viscosity C1"), sort_factor * _parse_required_number(row[7], "RK viscosity C2"), _parse_required_number(row[10], "RK viscosity C3"))
                t_min = _parse_required_number(row[11], "RK viscosity T min")
                t_max = _parse_required_number(row[12], "RK viscosity T max")
                reference = ",".join(row[13:]).strip().strip('"')
            models[components] = RkModel(
                components=components,
                property_name=property_name,
                a=a,
                b=b,
                c=c,
                valid_temperature_range_k=(t_min, t_max),
                reference=reference,
            )
        return models

    def _data_path(self, filename: str) -> Path:
        if self.base_path is not None:
            path = self.base_path / filename
            if path.exists():
                return path
            raise FileNotFoundError(f"MSD-TP data file '{path}' does not exist.")
        try:
            path = Path(str(resources.files(DATA_PACKAGE).joinpath(filename)))
        except ModuleNotFoundError as exc:
            raise FileNotFoundError(_missing_data_message(filename)) from exc
        if path.exists():
            return path
        raise FileNotFoundError(_missing_data_message(filename))


def database_for_spec(spec: dict[str, Any] | None = None) -> MsdTpDatabase:
    data_dir = _resolve_data_dir(spec or {})
    cache_key = str(data_dir.resolve()) if data_dir else "__package__"
    database = _DATABASE_CACHE.get(cache_key)
    if database is None:
        database = MsdTpDatabase(base_path=data_dir)
        _DATABASE_CACHE[cache_key] = database
    return database


def clear_database_cache() -> None:
    _DATABASE_CACHE.clear()


def _resolve_data_dir(spec: dict[str, Any]) -> Path | None:
    configured = spec.get("data_dir") or os.environ.get(DATA_DIR_ENV_VAR)
    if configured:
        return Path(str(configured))
    return None


def _missing_data_message(filename: str) -> str:
    return (
        f"MSD-TP data file '{filename}' is not bundled in the public-safe repository. "
        f"Set {DATA_DIR_ENV_VAR} to a local MSD-TP CSV directory, or add data_dir to the property spec."
    )


def evaluate_msd_tp_property(
    spec: dict[str, Any],
    *,
    temperature_c: float,
    expected_quantity: str,
) -> PropertyEvaluation:
    return database_for_spec(spec).evaluate(spec, temperature_c=temperature_c, expected_quantity=expected_quantity)


def describe_msd_tp_property(
    spec: dict[str, Any],
    *,
    temperature_c: float | None = None,
    expected_quantity: str | None = None,
) -> dict[str, Any]:
    return database_for_spec(spec).describe_record(spec, temperature_c=temperature_c, expected_quantity=expected_quantity)


def components_from_spec(spec: dict[str, Any]) -> tuple[tuple[str, ...], tuple[float, ...]]:
    formula = str(spec.get("formula", "")).strip()
    if not formula:
        raise ValueError("MSD-TP property specs require formula.")
    components = tuple(part.strip() for part in formula.split("-") if part.strip())
    if not components:
        raise ValueError("MSD-TP property specs require at least one formula component.")
    composition_raw = spec.get("composition")
    if composition_raw is None:
        if len(components) == 1:
            return components, (1.0,)
        raise ValueError("MSD-TP mixture property specs require composition.")
    if isinstance(composition_raw, str):
        mole_fractions = tuple(float(item) for item in composition_raw.split("-"))
    elif isinstance(composition_raw, list):
        mole_fractions = tuple(float(item) for item in composition_raw)
    else:
        raise ValueError("MSD-TP composition must be a mole-fraction string or list.")
    if len(mole_fractions) != len(components):
        raise ValueError("MSD-TP composition length must match formula component count.")
    total = sum(mole_fractions)
    if total <= 0.0:
        raise ValueError("MSD-TP composition mole fractions must sum to a positive value.")
    normalized = tuple(value / total for value in mole_fractions)
    return components, normalized


def composition_label(mole_fractions: tuple[float, ...]) -> str:
    if len(mole_fractions) == 1:
        return "Pure Salt"
    return "-".join(f"{value:.9g}" for value in mole_fractions)


def _read_csv_rows(path: Path) -> list[list[str]]:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                return list(csv.reader(handle))
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Could not decode MSD-TP data file {path}.")


def _parse_composition(token: str) -> tuple[float, ...]:
    cleaned = token.strip()
    if cleaned.lower() == "pure salt":
        return (1.0,)
    values = tuple(float(item) for item in cleaned.split("-"))
    total = sum(values)
    return tuple(value / total for value in values)


def _parse_number(token: str) -> float | None:
    cleaned = token.strip().strip('"')
    if not cleaned or set(cleaned) <= {"-"}:
        return None
    if cleaned.lower() == "synthetic":
        return None
    for suffix in ("LG", "^", "s", "*"):
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)]
    if cleaned.startswith(("<", ">")):
        cleaned = cleaned[1:]
    cleaned = cleaned.strip()
    if not cleaned or set(cleaned) <= {"-"}:
        return None
    return float(cleaned)


def _parse_required_number(token: str, field_name: str) -> float:
    value = _parse_number(token)
    if value is None:
        raise ValueError(f"MSD-TP required numeric field is missing: {field_name}.")
    return value


def _parse_range(token: str) -> tuple[float, float] | None:
    cleaned = token.strip()
    if not cleaned or set(cleaned) <= {"-"}:
        return None
    parts = cleaned.split("-")
    if len(parts) != 2:
        return None
    return (float(parts[0]), float(parts[1]))


def _parse_uncertainty_fraction(token: str) -> float | None:
    value = _parse_number(token)
    if value is None:
        return None
    return value / 100.0


def _property_model(
    *,
    kind: str,
    coefficients: tuple[str, ...],
    source_units: str,
    range_token: str,
    uncertainty_token: str,
    reference: str,
) -> PropertyModel | None:
    parsed = tuple(_parse_number(item) for item in coefficients)
    if not any(value is not None and value != 0.0 for value in parsed):
        return None
    return PropertyModel(
        kind=kind,
        coefficients=tuple(float(value or 0.0) for value in parsed),
        source_units=source_units,
        valid_temperature_range_k=_parse_range(range_token),
        uncertainty_fraction_95=_parse_uncertainty_fraction(uncertainty_token),
        reference=reference.strip(),
    )


def _viscosity_model(row: list[str]) -> PropertyModel | None:
    exp_a = _parse_number(row[15])
    exp_b = _parse_number(row[16])
    log_a = _parse_number(row[17])
    log_b = _parse_number(row[18])
    log_c = _parse_number(row[19])
    if exp_a is not None and exp_a != 0.0:
        return PropertyModel(
            kind="viscosity_arrhenius",
            coefficients=(exp_a, float(exp_b or 0.0)),
            source_units="mN*s/m2",
            valid_temperature_range_k=_parse_range(row[20]),
            uncertainty_fraction_95=_parse_uncertainty_fraction(row[21]),
            reference=row[22].strip(),
        )
    if any(value is not None and value != 0.0 for value in (log_a, log_b, log_c)):
        return PropertyModel(
            kind="viscosity_log10_polynomial",
            coefficients=(float(log_a or 0.0), float(log_b or 0.0), float(log_c or 0.0)),
            source_units="mN*s/m2",
            valid_temperature_range_k=_parse_range(row[20]),
            uncertainty_fraction_95=_parse_uncertainty_fraction(row[21]),
            reference=row[22].strip(),
        )
    return None


def _evaluate_direct_model(model: PropertyModel, temperature_k: float) -> float:
    if model.kind == "density_linear":
        a, b = model.coefficients
        return a - b * temperature_k
    if model.kind == "viscosity_arrhenius":
        a, b = model.coefficients
        return a * math.exp((b / GAS_CONSTANT_J_MOL_K) / temperature_k)
    if model.kind == "viscosity_log10_polynomial":
        a, b, c = model.coefficients
        return math.pow(10.0, a + b / temperature_k + c / (temperature_k * temperature_k))
    if model.kind == "thermal_conductivity_linear":
        a, b = model.coefficients
        return a + b * temperature_k
    if model.kind == "heat_capacity_molar_polynomial":
        a, b, c, d = model.coefficients
        return a + b * temperature_k + c / (temperature_k * temperature_k) + d * temperature_k * temperature_k
    raise ValueError(f"Unsupported MSD-TP model kind: {model.kind}.")


def _convert_msd_tp_value(
    value: float,
    *,
    property_name: str,
    model: PropertyModel,
    record: DirectRecord,
    requested_units: str,
) -> tuple[float, str]:
    if property_name == "density":
        if requested_units != "g/cm3":
            raise ValueError("MSD-TP density specs must use units: g/cm3.")
        return value, "g/cm3"
    if property_name == "dynamic_viscosity":
        if requested_units != "pa-s":
            raise ValueError("MSD-TP dynamic_viscosity specs must use units: pa-s.")
        return value * 1.0e-3, "pa-s"
    if property_name == "thermal_conductivity":
        if requested_units != "w/m-k":
            raise ValueError("MSD-TP thermal_conductivity specs must use units: w/m-k.")
        return value, "w/m-k"
    if property_name == "cp":
        cp_j_kgk = value * 1000.0 / record.molecular_weight_g_mol
        if requested_units == "j/kg-k":
            return cp_j_kgk, "j/kg-k"
        if requested_units == "kj/kg-k":
            return cp_j_kgk / 1000.0, "kj/kg-k"
        raise ValueError("MSD-TP cp specs must use units: j/kg-k or kj/kg-k.")
    raise ValueError(f"Unsupported MSD-TP property: {property_name}.")


def _rk_excess(model: RkModel, x: float, y: float, temperature_k: float) -> float:
    terms = [
        model.a[index] + model.b[index] * temperature_k + model.c[index] * temperature_k * temperature_k
        for index in range(len(model.a))
    ]
    diff = x - y
    total = terms[0]
    for index, term in enumerate(terms[1:], start=1):
        total += term * math.pow(diff, index)
    return x * y * total


def _direct_metadata(
    *,
    record: DirectRecord,
    property_name: str,
    model: PropertyModel,
    temperature_k: float,
    range_status: str,
    output_units: str,
    output_value: float,
    allow_extrapolation: bool,
) -> dict[str, Any]:
    return {
        "provider": "msd_tp",
        "source": "ORNL MSD-TP",
        "source_kind": "direct_record",
        "property": property_name,
        "formula": record.formula,
        "composition": composition_label(record.mole_fractions),
        "record_id": record.row_id,
        "model": model.kind,
        "temperature_k": _round(temperature_k),
        "valid_temperature_range_k": _range_dict(model.valid_temperature_range_k),
        "range_status": range_status,
        "allow_extrapolation": allow_extrapolation,
        "source_units": model.source_units,
        "output_value": _round(output_value),
        "output_units": output_units,
        "uncertainty_95_fraction": model.uncertainty_fraction_95,
        "reference": model.reference,
    }


def _range_status(valid_range: tuple[float, float] | None, temperature_k: float) -> str:
    if valid_range is None:
        return "range_not_reported"
    lo, hi = valid_range
    return "in_range" if lo <= temperature_k <= hi else "out_of_range"


def _range_dict(valid_range: tuple[float, float] | None) -> dict[str, float] | None:
    if valid_range is None:
        return None
    return {"min": _round(valid_range[0]), "max": _round(valid_range[1])}


def _property_name(expected_quantity: str) -> str:
    if expected_quantity == "specific_heat":
        return "cp"
    return expected_quantity


def _quantity_from_spec(spec: dict[str, Any]) -> str:
    quantity = spec.get("quantity")
    if quantity:
        return _property_name(str(quantity))
    return "density"


def _record_key(components: tuple[str, ...], mole_fractions: tuple[float, ...]) -> tuple[tuple[str, float], ...]:
    pairs = sorted(zip(components, mole_fractions), key=lambda item: item[0])
    return tuple((component, round(float(fraction), 9)) for component, fraction in pairs)


def _round(value: float) -> float:
    return round(float(value), 9)
