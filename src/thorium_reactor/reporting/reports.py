from __future__ import annotations

import json
from json import JSONDecodeError
from pathlib import Path
from typing import Any


def generate_report(
    case_name: str,
    config: dict[str, Any],
    summary_path: Path,
    validation_path: Path | None,
    geometry_assets: dict[str, str] | None,
    benchmark: dict[str, Any] | None = None,
    plot_assets: dict[str, str] | None = None,
) -> str:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    validation = {}
    benchmark = benchmark or {}
    if validation_path and validation_path.exists():
        try:
            validation = json.loads(validation_path.read_text(encoding="utf-8"))
        except JSONDecodeError:
            validation = {"checks": [], "passed": False}

    lines = [
        f"# {config['reactor']['name']}",
        "",
        f"- Case: `{case_name}`",
        f"- Family: `{config['reactor']['family']}`",
        f"- Stage: `{config['reactor']['stage']}`",
        f"- Result bundle: `{summary.get('result_dir', '')}`",
        f"- Neutronics status: `{summary.get('neutronics', {}).get('status', 'unknown')}`",
        "",
        "## Reactor Summary",
        "",
        f"- Design thermal power (MWth): `{config['reactor'].get('design_power_mwth', 'n/a')}`",
        f"- Benchmark source: `{config['reactor'].get('benchmark', 'n/a')}`",
        "",
    ]

    if benchmark:
        lines.extend(
            [
                "## Benchmark Context",
                "",
                f"- Benchmark title: `{benchmark.get('title', 'n/a')}`",
            ]
        )
        for reference in benchmark.get("references", []):
            lines.append(f"- Reference note: {reference}")
        for assumption in benchmark.get("assumptions", []):
            lines.append(f"- Assumption: {assumption}")
        lines.append("")

    lines.extend(
        [
            "## Key Metrics",
            "",
        ]
    )

    for key, value in summary.get("metrics", {}).items():
        lines.append(f"- {key}: `{value}`")

    if "bop" in summary:
        lines.extend(["", "## Balance Of Plant", ""])
        for key, value in summary["bop"].items():
            lines.append(f"- {key}: `{value}`")

    if validation:
        lines.extend(["", "## Validation", ""])
        for check in validation.get("checks", []):
            lines.append(
                f"- {check['name']}: `{check['status']}`"
                + (f" ({check['message']})" if check.get("message") else "")
            )

    if benchmark.get("evidence"):
        lines.extend(["", "## Evidence Trail", ""])
        for item in benchmark["evidence"]:
            lines.append(f"- {item.get('topic', 'evidence')}: {item.get('claim', 'n/a')}")
            if item.get("source"):
                lines.append(f"- Source: `{item['source']}`")
            if item.get("relevance"):
                lines.append(f"- Why it matters here: {item['relevance']}")

    if benchmark.get("novelty_tracks"):
        lines.extend(["", "## Novelty Tracks", ""])
        for track in benchmark["novelty_tracks"]:
            lines.append(f"- {track.get('name', 'untitled')}: {track.get('summary', '')}")

    if geometry_assets:
        lines.extend(["", "## Geometry Outputs", ""])
        for name, path in geometry_assets.items():
            lines.append(f"- {name}: `{path}`")

    if plot_assets:
        lines.extend(["", "## Plot Outputs", ""])
        for name, path in plot_assets.items():
            lines.append(f"- {name}: `{path}`")

    lines.extend(
        [
            "",
            "## Notes",
            "",
            "- This report is generated from the config-driven reactor workflow.",
            "- Validation targets can mix literature-derived bounds with explicitly labeled surrogate assumptions.",
        ]
    )

    return "\n".join(lines) + "\n"
