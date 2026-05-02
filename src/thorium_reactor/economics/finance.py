from __future__ import annotations

import json
import math
from calendar import monthrange
from datetime import date
from pathlib import Path
from typing import Any, Mapping

import yaml

from thorium_reactor.capabilities import COMMERCIAL_PLANNING, CapabilityConfigurationError, validate_case_capability
from thorium_reactor.config import CaseConfig


DEFAULT_PROJECT_START = "2026-05-02"


def capital_recovery_factor(discount_rate: float, periods: int) -> float:
    if periods <= 0:
        raise ValueError("Capital recovery periods must be positive.")
    if discount_rate < 0:
        raise ValueError("Discount rate must be non-negative.")
    if math.isclose(discount_rate, 0.0):
        return 1.0 / periods
    factor = (1.0 + discount_rate) ** periods
    return discount_rate * factor / (factor - 1.0)


def annual_generation_mwh(net_capacity_mwe: float, capacity_factor: float) -> float:
    return net_capacity_mwe * 8760.0 * capacity_factor


def adjusted_overnight_cost_usd(
    reference_overnight_cost_usd: float,
    *,
    overnight_cost_uplift: float,
    learning_cost_multiplier: float = 1.0,
    cost_escalation_factor: float = 1.0,
) -> float:
    return reference_overnight_cost_usd * overnight_cost_uplift * learning_cost_multiplier * cost_escalation_factor


def load_cost_basis_data() -> dict[str, Any]:
    path = Path(__file__).with_name("cost_basis.yaml")
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_commercial_plan(
    config: CaseConfig,
    *,
    scenario_name: str | None = None,
    project_start: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    mode = str(config.reactor.get("mode", "modern_test_reactor"))
    if mode != "commercial_grid" and not force:
        return _build_not_applicable_plan(config, mode=mode)

    if mode == "commercial_grid":
        validate_case_capability(config, COMMERCIAL_PLANNING)
    elif not config.economics or not config.project_schedule:
        raise CapabilityConfigurationError(
            "Forced commercial economics requires economics and project_schedule sections on the case."
        )
    cost_basis_data = load_cost_basis_data()
    economics = config.economics
    selected_scenario = scenario_name or str(economics["default_scenario"])
    scenario = _resolve_scenario(config, selected_scenario)
    cost_basis_id = str(economics["cost_basis"])
    cost_basis = _resolve_cost_basis(cost_basis_data, cost_basis_id)
    characteristics = config.reactor.get("characteristics", {})

    net_capacity_mwe = float(characteristics.get("net_electric_power_mwe", cost_basis["net_capacity_mwe"]))
    capacity_kw = net_capacity_mwe * 1000.0
    capacity_factor = float(scenario.get("capacity_factor", cost_basis["capacity_factor"]))
    source_occ_usd_per_kwe = float(
        scenario.get(
            "overnight_capital_cost_usd_per_kwe",
            cost_basis["overnight_capital_cost_usd_per_kwe"],
        )
    )
    fixed_om_usd_per_kw_year = float(
        scenario.get("fixed_om_usd_per_kw_year", cost_basis["fixed_om_usd_per_kw_year"])
    )
    variable_om_usd_per_mwh = float(
        scenario.get("variable_om_usd_per_mwh", cost_basis["variable_om_usd_per_mwh"])
    )
    fuel_usd_per_mwh = float(scenario.get("fuel_usd_per_mwh", cost_basis["fuel_usd_per_mwh"]))
    source_construction_months = int(cost_basis["construction_months"])
    construction_duration_multiplier = float(scenario.get("construction_duration_multiplier", 1.0))
    construction_months = int(math.ceil(source_construction_months * construction_duration_multiplier))
    real_wacc = float(scenario.get("real_wacc", 0.08))
    analysis_life_years = int(round(float(scenario.get("analysis_life_years", 60))))
    overnight_cost_uplift = float(scenario.get("overnight_cost_uplift", 1.0))
    learning_cost_multiplier = float(scenario.get("learning_cost_multiplier", 1.0))
    cost_escalation_factor = float(scenario.get("cost_escalation_factor", economics.get("cost_escalation_factor", 1.0)))
    tax_credit_fraction = float(scenario.get("tax_credit_fraction", 0.0))

    requested_start = project_start or str(config.project_schedule.get("project_start", DEFAULT_PROJECT_START))
    schedule = build_schedule(
        config,
        project_start=requested_start,
        construction_months=construction_months,
        scenario_name=selected_scenario,
    )
    construction_start = date.fromisoformat(schedule["construction_start_date"])

    reference_overnight_cost_usd = capacity_kw * source_occ_usd_per_kwe
    adjusted_overnight = adjusted_overnight_cost_usd(
        reference_overnight_cost_usd,
        overnight_cost_uplift=overnight_cost_uplift,
        learning_cost_multiplier=learning_cost_multiplier,
        cost_escalation_factor=cost_escalation_factor,
    )
    tax_credit_value = adjusted_overnight * tax_credit_fraction
    net_overnight_cost_usd = adjusted_overnight - tax_credit_value
    cash_flow = build_construction_cash_flow(
        net_overnight_cost_usd,
        construction_months=construction_months,
        annual_discount_rate=real_wacc,
        construction_start=construction_start,
    )
    interest_during_construction_usd = round(sum(row["idc_accrued_usd"] for row in cash_flow), 2)
    total_capitalized_cost_usd = round(net_overnight_cost_usd + interest_during_construction_usd, 2)
    annual_mwh = annual_generation_mwh(net_capacity_mwe, capacity_factor)
    annual_fixed_om_usd = capacity_kw * fixed_om_usd_per_kw_year
    annual_variable_om_usd = annual_mwh * variable_om_usd_per_mwh
    annual_fuel_usd = annual_mwh * fuel_usd_per_mwh
    crf = capital_recovery_factor(real_wacc, analysis_life_years)
    annualized_capital_cost_usd = total_capitalized_cost_usd * crf
    annual_non_capital_cost_usd = annual_fixed_om_usd + annual_variable_om_usd + annual_fuel_usd
    lcoe_usd_per_mwh = (annualized_capital_cost_usd + annual_non_capital_cost_usd) / annual_mwh

    source_year = int(cost_basis["source_year_usd"])
    target_year = int(economics.get("target_year_usd", source_year))
    finance = {
        "status": "completed",
        "case": config.name,
        "scenario": selected_scenario,
        "planning_basis": "planning_grade_not_vendor_quote",
        "currency": "USD",
        "source_year_usd": source_year,
        "target_year_usd": target_year,
        "cost_escalation_factor": round(cost_escalation_factor, 6),
        "cost_basis": {
            "id": cost_basis_id,
            "reactor_class": cost_basis.get("reactor_class", "n/a"),
            "maturity": cost_basis.get("maturity", "n/a"),
            "source_refs": list(cost_basis.get("source_refs", [])),
        },
        "inputs": {
            "net_capacity_mwe": round(net_capacity_mwe, 6),
            "capacity_factor": round(capacity_factor, 6),
            "source_occ_usd_per_kwe": round(source_occ_usd_per_kwe, 2),
            "fixed_om_usd_per_kw_year": round(fixed_om_usd_per_kw_year, 2),
            "variable_om_usd_per_mwh": round(variable_om_usd_per_mwh, 2),
            "fuel_usd_per_mwh": round(fuel_usd_per_mwh, 2),
            "source_construction_months": source_construction_months,
            "construction_duration_multiplier": round(construction_duration_multiplier, 6),
            "construction_months": construction_months,
            "overnight_cost_uplift": round(overnight_cost_uplift, 6),
            "learning_cost_multiplier": round(learning_cost_multiplier, 6),
            "real_wacc": round(real_wacc, 6),
            "analysis_life_years": analysis_life_years,
            "tax_credit_fraction": round(tax_credit_fraction, 6),
        },
        "cost_breakdown_usd": {
            "reference_overnight_cost": round(reference_overnight_cost_usd, 2),
            "foak_and_maturity_adjustment": round(adjusted_overnight - reference_overnight_cost_usd, 2),
            "tax_credit_adjustment": round(-tax_credit_value, 2),
            "net_overnight_cost": round(net_overnight_cost_usd, 2),
            "interest_during_construction": interest_during_construction_usd,
            "total_capitalized_cost": total_capitalized_cost_usd,
        },
        "annual_costs_usd_per_year": {
            "annualized_capital": round(annualized_capital_cost_usd, 2),
            "fixed_om": round(annual_fixed_om_usd, 2),
            "variable_om": round(annual_variable_om_usd, 2),
            "fuel": round(annual_fuel_usd, 2),
            "total": round(annualized_capital_cost_usd + annual_non_capital_cost_usd, 2),
        },
        "outputs": {
            "annual_generation_mwh": round(annual_mwh, 2),
            "capital_recovery_factor": round(crf, 8),
            "lcoe_usd_per_mwh": round(lcoe_usd_per_mwh, 2),
            "lcoe_cents_per_kwh": round(lcoe_usd_per_mwh / 10.0, 3),
            "overnight_cost_usd_per_kwe_after_adjustments": round(net_overnight_cost_usd / capacity_kw, 2),
            "capitalized_cost_usd_per_kwe": round(total_capitalized_cost_usd / capacity_kw, 2),
        },
        "cash_flow": cash_flow,
        "provenance": {
            "sources": cost_basis_data.get("sources", []),
            "caveat": (
                "Planning-grade model for repository scenario comparison. "
                "Not a vendor quote, EPC bid, licensing cost estimate, or investment recommendation."
            ),
        },
    }
    project_plan = {
        "case": config.name,
        "status": "completed",
        "reactor_characteristics": dict(characteristics),
        "finance": _finance_summary(finance),
        "schedule": _schedule_summary(schedule),
        "assumptions": {
            "flagship": "300 MWe net U.S. NRC grid-connected thorium molten-salt SMR",
            "scenario": selected_scenario,
            "planning_grade": True,
            "tax_credits_included": tax_credit_fraction > 0.0,
        },
    }
    return {
        "status": "completed",
        "finance": finance,
        "schedule": schedule,
        "project_plan": project_plan,
    }


def build_schedule(
    config: CaseConfig,
    *,
    project_start: str,
    construction_months: int,
    scenario_name: str,
) -> dict[str, Any]:
    start_date = date.fromisoformat(project_start)
    raw_phases = list(config.project_schedule.get("phases", []))
    resolved: dict[str, dict[str, Any]] = {}
    pending = {str(phase["id"]): phase for phase in raw_phases}
    while pending:
        progressed = False
        for phase_id, phase in list(pending.items()):
            dependencies = [str(item) for item in phase.get("depends_on", [])]
            if any(dependency not in resolved for dependency in dependencies):
                continue
            if dependencies:
                phase_start = max(date.fromisoformat(resolved[dependency]["end_date"]) for dependency in dependencies)
            else:
                phase_start = start_date
            duration_months = (
                construction_months
                if phase.get("duration_source") == "construction_months"
                else int(phase["duration_months"])
            )
            phase_end = _add_months(phase_start, duration_months)
            resolved[phase_id] = {
                "id": phase_id,
                "name": str(phase.get("name", phase_id)),
                "category": str(phase.get("category", "project")),
                "duration_months": duration_months,
                "start_date": phase_start.isoformat(),
                "end_date": phase_end.isoformat(),
                "depends_on": dependencies,
                "notes": str(phase.get("notes", "")),
            }
            del pending[phase_id]
            progressed = True
        if not progressed:
            unresolved = ", ".join(sorted(pending))
            raise ValueError(f"Project schedule contains unknown dependencies or a cycle: {unresolved}.")

    phases = [resolved[str(phase["id"])] for phase in raw_phases]
    commercial_operation = max(date.fromisoformat(phase["end_date"]) for phase in phases)
    construction_phase = next(
        (phase for phase in phases if phase["id"] == "nuclear_construction"),
        next(
            (phase for phase in phases if phase["category"] == "construction" and phase["duration_months"] == construction_months),
            phases[-1],
        ),
    )
    total_months = _months_between(start_date, commercial_operation)
    return {
        "status": "completed",
        "case": config.name,
        "scenario": scenario_name,
        "project_start_date": start_date.isoformat(),
        "commercial_operation_date": commercial_operation.isoformat(),
        "construction_start_date": construction_phase["start_date"],
        "construction_end_date": construction_phase["end_date"],
        "total_months_to_commercial_operation": total_months,
        "total_years_to_commercial_operation": round(total_months / 12.0, 2),
        "phases": phases,
        "planning_basis": str(config.project_schedule.get("planning_basis", "U.S. NRC Part 52 grid project")),
    }


def build_construction_cash_flow(
    overnight_cost_usd: float,
    *,
    construction_months: int,
    annual_discount_rate: float,
    construction_start: date,
) -> list[dict[str, Any]]:
    if construction_months <= 0:
        raise ValueError("Construction months must be positive.")
    monthly_rate = (1.0 + annual_discount_rate) ** (1.0 / 12.0) - 1.0
    weights = [
        max(math.sin(math.pi * (index + 0.5) / construction_months), 0.01)
        for index in range(construction_months)
    ]
    total_weight = sum(weights)
    rows: list[dict[str, Any]] = []
    cumulative_spend = 0.0
    cumulative_idc = 0.0
    for index, weight in enumerate(weights, start=1):
        spend = overnight_cost_usd * weight / total_weight
        months_to_cod = construction_months - index + 0.5
        idc = spend * ((1.0 + monthly_rate) ** months_to_cod - 1.0)
        cumulative_spend += spend
        cumulative_idc += idc
        rows.append(
            {
                "month": index,
                "date": _add_months(construction_start, index - 1).isoformat(),
                "overnight_spend_usd": round(spend, 2),
                "cumulative_overnight_spend_usd": round(cumulative_spend, 2),
                "idc_accrued_usd": round(idc, 2),
                "cumulative_idc_usd": round(cumulative_idc, 2),
                "cumulative_capitalized_cost_usd": round(cumulative_spend + cumulative_idc, 2),
            }
        )
    return rows


def run_economics_case(
    config: CaseConfig,
    bundle,
    *,
    scenario_name: str | None = None,
    project_start: str | None = None,
    force: bool = False,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = build_commercial_plan(
        config,
        scenario_name=scenario_name,
        project_start=project_start,
        force=force,
    )
    finance = plan["finance"]
    schedule = plan["schedule"]
    project_plan = plan["project_plan"]
    bundle.write_json("finance.json", finance)
    bundle.write_json("schedule.json", schedule)
    bundle.write_json("project_plan.json", project_plan)
    bundle.write_text("cash_flow.csv", cash_flow_csv(finance))
    bundle.write_text("cost_breakdown.csv", cost_breakdown_csv(finance))

    summary_path = bundle.root / "summary.json"
    if summary is None and summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            summary = None
    if summary is None:
        summary = {
            "case": config.name,
            "result_dir": str(bundle.root),
            "neutronics": {"status": "not_run"},
            "metrics": {},
        }
    summary.setdefault("case", config.name)
    summary.setdefault("result_dir", str(bundle.root))
    summary.setdefault("neutronics", {"status": "not_run"})
    metrics = summary.setdefault("metrics", {})
    summary["finance"] = finance
    summary["schedule"] = schedule
    if finance.get("status") == "completed":
        metrics["finance_lcoe_usd_per_mwh"] = finance["outputs"]["lcoe_usd_per_mwh"]
        metrics["finance_lcoe_cents_per_kwh"] = finance["outputs"]["lcoe_cents_per_kwh"]
        metrics["finance_total_capitalized_cost_usd"] = finance["cost_breakdown_usd"]["total_capitalized_cost"]
        metrics["finance_overnight_cost_usd_per_kwe"] = finance["outputs"][
            "overnight_cost_usd_per_kwe_after_adjustments"
        ]
        metrics["schedule_total_months_to_cod"] = schedule["total_months_to_commercial_operation"]
        metrics["schedule_total_years_to_cod"] = schedule["total_years_to_commercial_operation"]
    bundle.write_json("summary.json", summary)
    if metrics:
        bundle.write_metrics(metrics)
    return plan


def cash_flow_csv(finance: Mapping[str, Any]) -> str:
    headers = [
        "month",
        "date",
        "overnight_spend_usd",
        "cumulative_overnight_spend_usd",
        "idc_accrued_usd",
        "cumulative_idc_usd",
        "cumulative_capitalized_cost_usd",
    ]
    lines = [",".join(headers)]
    for row in finance.get("cash_flow", []):
        lines.append(",".join(str(row.get(header, "")) for header in headers))
    return "\n".join(lines) + "\n"


def cost_breakdown_csv(finance: Mapping[str, Any]) -> str:
    lines = ["item,amount_usd"]
    for key, value in finance.get("cost_breakdown_usd", {}).items():
        lines.append(f"{key},{value}")
    for key, value in finance.get("annual_costs_usd_per_year", {}).items():
        lines.append(f"annual_{key},{value}")
    return "\n".join(lines) + "\n"


def _resolve_scenario(config: CaseConfig, scenario_name: str) -> dict[str, Any]:
    scenarios = config.economics.get("scenarios", {})
    if scenario_name not in scenarios:
        available = ", ".join(sorted(str(name) for name in scenarios))
        raise ValueError(f"Unknown economics scenario '{scenario_name}'. Available scenarios: {available}.")
    return dict(scenarios[scenario_name])


def _resolve_cost_basis(cost_basis_data: Mapping[str, Any], cost_basis_id: str) -> dict[str, Any]:
    cost_bases = cost_basis_data.get("cost_bases", {})
    if cost_basis_id not in cost_bases:
        available = ", ".join(sorted(str(name) for name in cost_bases))
        raise ValueError(f"Unknown economics cost basis '{cost_basis_id}'. Available cost bases: {available}.")
    return dict(cost_bases[cost_basis_id])


def _build_not_applicable_plan(config: CaseConfig, *, mode: str) -> dict[str, Any]:
    finance = {
        "status": "not_applicable",
        "case": config.name,
        "reason": "Commercial finance is only calculated for reactor.mode='commercial_grid'.",
        "case_mode": mode,
    }
    schedule = {
        "status": "not_applicable",
        "case": config.name,
        "reason": "Commercial build schedule is only calculated for reactor.mode='commercial_grid'.",
        "case_mode": mode,
    }
    project_plan = {
        "case": config.name,
        "status": "not_applicable",
        "reason": "Non-commercial model cases are benchmark, research, submodel, or smoke-test artifacts.",
    }
    return {
        "status": "not_applicable",
        "finance": finance,
        "schedule": schedule,
        "project_plan": project_plan,
    }


def _finance_summary(finance: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "scenario": finance.get("scenario"),
        "total_capitalized_cost_usd": finance.get("cost_breakdown_usd", {}).get("total_capitalized_cost"),
        "lcoe_usd_per_mwh": finance.get("outputs", {}).get("lcoe_usd_per_mwh"),
        "annual_generation_mwh": finance.get("outputs", {}).get("annual_generation_mwh"),
    }


def _schedule_summary(schedule: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "project_start_date": schedule.get("project_start_date"),
        "commercial_operation_date": schedule.get("commercial_operation_date"),
        "total_years_to_commercial_operation": schedule.get("total_years_to_commercial_operation"),
    }


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, monthrange(year, month)[1])
    return date(year, month, day)


def _months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months
