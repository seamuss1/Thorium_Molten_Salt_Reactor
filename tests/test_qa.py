import json
from pathlib import Path

from thorium_reactor.cli import build_parser, main
from thorium_reactor.qa import (
    REQUIRED_MATRIX_COLUMNS,
    build_requirements_traceability_index,
    build_requirements_summary,
    load_requirement_records,
    load_traceability_matrix,
    validate_requirements_traceability,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_requirements_traceability_artifacts_validate() -> None:
    result = validate_requirements_traceability(REPO_ROOT)

    assert result["passed"] is True
    assert result["errors"] == []
    assert result["requirement_count"] >= 10
    assert result["matrix_row_count"] >= result["requirement_count"]
    assert {check["status"] for check in result["checks"]} == {"pass"}
    assert {check["name"] for check in result["checks"]} == {
        "qa::requirements_required_fields",
        "qa::traceability_matrix_links",
        "qa::artifact_and_test_links_exist",
        "qa::matrix_metadata_matches_requirements",
    }


def test_traceability_matrix_links_known_requirement_ids_and_columns() -> None:
    requirements = {record["id"] for record in load_requirement_records(REPO_ROOT)}
    rows = load_traceability_matrix(REPO_ROOT)

    assert rows
    assert all(set(REQUIRED_MATRIX_COLUMNS) <= set(row) for row in rows)
    assert {row["requirement_id"] for row in rows} == requirements


def test_msre_benchmark_requirements_are_explicit_and_blocked() -> None:
    index = build_requirements_traceability_index(REPO_ROOT)
    msre_ids = {"REQ-MSRE-GEOMETRY", "REQ-MSRE-MATERIALS", "REQ-MSRE-SOLVER-BUNDLE"}

    assert msre_ids <= set(index)
    for requirement_id in msre_ids:
        requirement = index[requirement_id]["requirement"]
        assert requirement["category"] == "msre_benchmark"
        assert requirement["status"] == "blocked"
        assert index[requirement_id]["matrix_rows"]
        trace_links = [*requirement["implementation_area"], *requirement["validation_evidence"]]
        assert any("msre_first_criticality" in link for link in trace_links)


def test_requirement_verification_references_existing_tests() -> None:
    for requirement in load_requirement_records(REPO_ROOT):
        for test_ref in requirement["verification_tests"]:
            path_text, test_name = test_ref.split("::", 1)
            test_path = REPO_ROOT / path_text

            assert test_path.exists(), test_ref
            assert f"def {test_name}" in test_path.read_text(encoding="utf-8"), test_ref


def test_requirements_summary_is_machine_readable_for_dossiers() -> None:
    summary = build_requirements_summary(REPO_ROOT)

    assert summary["passed"] is True
    assert summary["requirements"]["total"] >= 10
    assert summary["requirements"]["by_category"]["msre_benchmark"] == 3
    assert summary["requirements"]["by_status"]["blocked"] == 3
    assert {item["id"] for item in summary["requirements"]["blocked"]} == {
        "REQ-MSRE-GEOMETRY",
        "REQ-MSRE-MATERIALS",
        "REQ-MSRE-SOLVER-BUNDLE",
    }
    assert summary["matrix"]["rows"] == summary["requirements"]["total"]
    requirements_path = Path(summary["artifacts"]["requirements"])
    assert requirements_path.name == "requirements.yaml"
    assert requirements_path.parent.name == "qa"


def test_cli_qa_command_is_repo_level_and_machine_readable(capsys) -> None:
    parser = build_parser()
    namespace = parser.parse_args(["qa", "--format", "json"])

    assert namespace.command == "qa"
    assert namespace.format == "json"

    exit_code = main(["--repo-root", str(REPO_ROOT), "qa", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["passed"] is True
    assert payload["requirements"]["by_status"]["blocked"] == 3
