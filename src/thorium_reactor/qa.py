from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import yaml


QA_DIR = "qa"
DOCS_DIR = "docs"
REQUIREMENTS_FILE = "requirements.yaml"
TRACEABILITY_MATRIX_FILE = "requirements_traceability_matrix.csv"
NONCONFORMANCE_LOG_FILE = "nonconformance-corrective-action-log.md"
REQUIREMENT_ID_PREFIX = "REQ-"
NONCONFORMANCE_ID_PREFIX = "NCA-"
SUPPORTED_REQUIREMENT_STATUSES = {"implemented", "active", "blocked", "planned"}

REQUIRED_REQUIREMENT_FIELDS = (
    "id",
    "title",
    "category",
    "statement",
    "implementation_area",
    "verification_tests",
    "validation_evidence",
    "acceptance_criteria",
    "status",
)

REQUIRED_MATRIX_COLUMNS = (
    "requirement_id",
    "title",
    "category",
    "implementation_area",
    "verification_test",
    "validation_evidence",
    "acceptance_criterion",
    "status",
)

REQUIRED_NONCONFORMANCE_FIELDS = (
    "Nonconformance ID",
    "Description",
    "Affected artifact",
    "Severity",
    "Disposition",
    "Corrective action",
    "Owner",
    "Closure evidence",
    "Status",
)


class QAArtifactError(ValueError):
    """Raised when a QA artifact cannot be loaded as structured data."""


def requirements_path(repo_root: Path) -> Path:
    return repo_root / QA_DIR / REQUIREMENTS_FILE


def traceability_matrix_path(repo_root: Path) -> Path:
    return repo_root / QA_DIR / TRACEABILITY_MATRIX_FILE


def nonconformance_log_path(repo_root: Path) -> Path:
    return repo_root / DOCS_DIR / NONCONFORMANCE_LOG_FILE


def load_requirements(repo_root: Path) -> dict[str, Any]:
    path = requirements_path(repo_root)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise QAArtifactError(f"Missing requirements artifact: {path}") from exc
    if not isinstance(raw, dict):
        raise QAArtifactError(f"Requirements artifact {path} must contain a mapping.")
    records = raw.get("requirements")
    if not isinstance(records, list):
        raise QAArtifactError(f"Requirements artifact {path} must define a requirements list.")
    return raw


def load_requirement_records(repo_root: Path) -> list[dict[str, Any]]:
    records = load_requirements(repo_root)["requirements"]
    return [dict(record) for record in records if isinstance(record, dict)]


def load_traceability_matrix(repo_root: Path) -> list[dict[str, str]]:
    path = traceability_matrix_path(repo_root)
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                raise QAArtifactError(f"Traceability matrix {path} must include a header row.")
            missing_columns = [column for column in REQUIRED_MATRIX_COLUMNS if column not in reader.fieldnames]
            if missing_columns:
                raise QAArtifactError(
                    f"Traceability matrix {path} is missing required columns: {', '.join(missing_columns)}"
                )
            return [{key: (value or "").strip() for key, value in row.items()} for row in reader]
    except FileNotFoundError as exc:
        raise QAArtifactError(f"Missing traceability matrix artifact: {path}") from exc


def build_requirements_traceability_index(repo_root: Path) -> dict[str, dict[str, Any]]:
    requirements = load_requirement_records(repo_root)
    matrix_rows = load_traceability_matrix(repo_root)
    index = {
        str(record["id"]): {
            "requirement": record,
            "matrix_rows": [],
        }
        for record in requirements
        if "id" in record
    }
    for row in matrix_rows:
        requirement_id = row.get("requirement_id", "")
        if requirement_id in index:
            index[requirement_id]["matrix_rows"].append(row)
    return index


def validate_requirements_traceability(repo_root: Path) -> dict[str, Any]:
    errors: list[str] = []

    try:
        raw_requirements = load_requirements(repo_root)
        requirements = [dict(record) for record in raw_requirements["requirements"] if isinstance(record, dict)]
    except QAArtifactError as exc:
        raw_requirements = {}
        requirements = []
        errors.append(str(exc))
    try:
        matrix_rows = load_traceability_matrix(repo_root)
    except QAArtifactError as exc:
        matrix_rows = []
        errors.append(str(exc))

    nonconformance_validation = validate_nonconformance_log(repo_root)
    errors.extend(nonconformance_validation["errors"])

    status_definitions = raw_requirements.get("status_definitions", {})
    if raw_requirements and not isinstance(raw_requirements.get("schema_version"), int):
        errors.append("Requirements artifact schema_version must be an integer.")
    if raw_requirements and not isinstance(status_definitions, dict):
        errors.append("Requirements artifact status_definitions must be a mapping.")

    requirement_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    requirements_by_id: dict[str, dict[str, Any]] = {}
    for index, record in enumerate(requirements, start=1):
        requirement_id = str(record.get("id", "")).strip()
        if not requirement_id:
            errors.append(f"Requirement record {index} is missing id.")
            continue
        if not requirement_id.startswith(REQUIREMENT_ID_PREFIX):
            errors.append(f"Requirement {requirement_id} must start with {REQUIREMENT_ID_PREFIX}.")
        if requirement_id in requirement_ids:
            duplicate_ids.add(requirement_id)
        requirement_ids.add(requirement_id)
        requirements_by_id[requirement_id] = record
        _validate_requirement_record(repo_root, record, errors)

    if duplicate_ids:
        errors.append("Duplicate requirement id(s): " + ", ".join(sorted(duplicate_ids)) + ".")

    matrix_requirement_ids: set[str] = set()
    for row_number, row in enumerate(matrix_rows, start=2):
        missing_values = [column for column in REQUIRED_MATRIX_COLUMNS if not row.get(column)]
        if missing_values:
            errors.append(f"Traceability matrix row {row_number} has empty values: {', '.join(missing_values)}.")
        requirement_id = row.get("requirement_id", "")
        if requirement_id:
            matrix_requirement_ids.add(requirement_id)
            if requirement_id not in requirement_ids:
                errors.append(f"Traceability matrix row {row_number} references unknown requirement {requirement_id}.")
            else:
                _validate_matrix_row_against_requirement(row_number, row, requirements_by_id[requirement_id], errors)

    uncovered = sorted(requirement_ids - matrix_requirement_ids)
    if uncovered:
        errors.append("Requirement(s) missing from traceability matrix: " + ", ".join(uncovered) + ".")

    checks = [
        {
            "name": "qa::requirements_required_fields",
            "status": "pass" if not any(error.startswith("Requirement") for error in errors) else "fail",
            "message": f"{len(requirements)} requirement record(s) inspected.",
        },
        {
            "name": "qa::traceability_matrix_links",
            "status": "pass" if not any(error.startswith("Traceability matrix") for error in errors) and not uncovered else "fail",
            "message": f"{len(matrix_rows)} matrix row(s) inspected.",
        },
        {
            "name": "qa::artifact_and_test_links_exist",
            "status": "pass"
            if not any("does not exist" in error or "does not define test" in error for error in errors)
            else "fail",
            "message": "Local artifact paths and verification test references were checked.",
        },
        {
            "name": "qa::matrix_metadata_matches_requirements",
            "status": "pass" if not any("does not match requirement" in error for error in errors) else "fail",
            "message": "Matrix title, category, status, and declared trace links were cross-checked.",
        },
        {
            "name": "qa::nonconformance_log_required_fields",
            "status": "pass" if nonconformance_validation["passed"] else "fail",
            "message": f"{nonconformance_validation['record_count']} nonconformance record(s) inspected.",
        },
    ]

    return {
        "passed": not errors,
        "errors": errors,
        "checks": checks,
        "requirement_count": len(requirements),
        "matrix_row_count": len(matrix_rows),
        "nonconformance_record_count": nonconformance_validation["record_count"],
        "requirement_ids": sorted(requirement_ids),
    }


def validate_nonconformance_log(repo_root: Path) -> dict[str, Any]:
    errors: list[str] = []
    path = nonconformance_log_path(repo_root)
    record_format_columns: list[str] = []
    open_log_columns: list[str] = []
    records: list[dict[str, str]] = []

    try:
        markdown = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        errors.append(f"Missing nonconformance log artifact: {path}")
        return {
            "passed": False,
            "errors": errors,
            "checks": [
                {
                    "name": "qa::nonconformance_log_required_fields",
                    "status": "fail",
                    "message": "0 nonconformance record(s) inspected.",
                }
            ],
            "columns": open_log_columns,
            "record_format_columns": record_format_columns,
            "record_count": 0,
            "required_fields": list(REQUIRED_NONCONFORMANCE_FIELDS),
        }

    try:
        record_format_rows, record_format_columns = _extract_markdown_table(markdown, "Record Format")
    except QAArtifactError as exc:
        errors.append(str(exc))
    else:
        missing_columns = [column for column in ("Field", "Required content") if column not in record_format_columns]
        if missing_columns:
            errors.append(
                f"Nonconformance log record format table is missing required columns: {', '.join(missing_columns)}."
            )
        else:
            declared_fields = [row.get("Field", "").strip() for row in record_format_rows]
            missing_fields = [field for field in REQUIRED_NONCONFORMANCE_FIELDS if field not in declared_fields]
            if missing_fields:
                errors.append(
                    "Nonconformance log record format is missing required field(s): "
                    + ", ".join(missing_fields)
                    + "."
                )

    try:
        records, open_log_columns = _extract_markdown_table(markdown, "Open Log")
    except QAArtifactError as exc:
        errors.append(str(exc))
    else:
        missing_columns = [field for field in REQUIRED_NONCONFORMANCE_FIELDS if field not in open_log_columns]
        if missing_columns:
            errors.append(
                f"Nonconformance open log table is missing required columns: {', '.join(missing_columns)}."
            )
        _validate_nonconformance_records(records, errors)

    return {
        "passed": not errors,
        "errors": errors,
        "checks": [
            {
                "name": "qa::nonconformance_log_required_fields",
                "status": "pass" if not errors else "fail",
                "message": f"{len(records)} nonconformance record(s) inspected.",
            }
        ],
        "columns": open_log_columns,
        "record_format_columns": record_format_columns,
        "record_count": len(records),
        "required_fields": list(REQUIRED_NONCONFORMANCE_FIELDS),
    }


def build_requirements_summary(repo_root: Path) -> dict[str, Any]:
    validation = validate_requirements_traceability(repo_root)
    records = load_requirement_records(repo_root)
    matrix_rows = load_traceability_matrix(repo_root)
    blocked = [
        {
            "id": str(record.get("id")),
            "title": str(record.get("title")),
            "related_issues": list(record.get("related_issues", [])),
        }
        for record in records
        if record.get("status") == "blocked"
    ]

    return {
        "passed": validation["passed"],
        "errors": validation["errors"],
        "checks": validation["checks"],
        "artifacts": {
            "requirements": str(requirements_path(repo_root)),
            "traceability_matrix": str(traceability_matrix_path(repo_root)),
            "nonconformance_log": str(nonconformance_log_path(repo_root)),
        },
        "requirements": {
            "total": len(records),
            "by_status": _count_by(records, "status"),
            "by_category": _count_by(records, "category"),
            "blocked": blocked,
        },
        "matrix": {
            "rows": len(matrix_rows),
            "columns": list(REQUIRED_MATRIX_COLUMNS),
            "requirement_ids": validation["requirement_ids"],
        },
        "nonconformance_log": {
            "rows": validation["nonconformance_record_count"],
            "columns": list(REQUIRED_NONCONFORMANCE_FIELDS),
        },
    }


def _validate_requirement_record(repo_root: Path, record: dict[str, Any], errors: list[str]) -> None:
    requirement_id = str(record.get("id", "<unknown>"))
    for field_name in REQUIRED_REQUIREMENT_FIELDS:
        if field_name not in record:
            errors.append(f"Requirement {requirement_id} is missing required field {field_name}.")
            continue
        if _is_empty(record[field_name]):
            errors.append(f"Requirement {requirement_id} has empty required field {field_name}.")

    status = str(record.get("status", "")).strip()
    if status not in SUPPORTED_REQUIREMENT_STATUSES:
        errors.append(
            f"Requirement {requirement_id} status '{status}' is unsupported. "
            f"Supported values: {', '.join(sorted(SUPPORTED_REQUIREMENT_STATUSES))}."
        )

    for list_field in ("implementation_area", "verification_tests", "validation_evidence", "acceptance_criteria"):
        value = record.get(list_field)
        if not isinstance(value, list):
            errors.append(f"Requirement {requirement_id} field {list_field} must be a non-empty list.")
        elif any(_is_empty(item) for item in value):
            errors.append(f"Requirement {requirement_id} field {list_field} contains an empty entry.")

    for field_name in ("implementation_area", "validation_evidence"):
        value = record.get(field_name)
        if isinstance(value, list):
            for artifact in value:
                _validate_artifact_reference(repo_root, requirement_id, field_name, str(artifact), errors)

    verification_tests = record.get("verification_tests")
    if isinstance(verification_tests, list):
        for test_ref in verification_tests:
            _validate_test_reference(repo_root, requirement_id, str(test_ref), errors)


def _validate_matrix_row_against_requirement(
    row_number: int,
    row: dict[str, str],
    requirement: dict[str, Any],
    errors: list[str],
) -> None:
    requirement_id = str(requirement.get("id", ""))
    for field_name in ("title", "category", "status"):
        if row.get(field_name) != str(requirement.get(field_name, "")):
            errors.append(
                f"Traceability matrix row {row_number} {field_name} '{row.get(field_name)}' "
                f"does not match requirement {requirement_id}."
            )
    _require_declared_matrix_value(row_number, row, requirement, "implementation_area", "implementation_area", errors)
    _require_declared_matrix_value(row_number, row, requirement, "verification_test", "verification_tests", errors)
    _require_declared_matrix_value(row_number, row, requirement, "validation_evidence", "validation_evidence", errors)
    _require_declared_matrix_value(row_number, row, requirement, "acceptance_criterion", "acceptance_criteria", errors)


def _validate_nonconformance_records(records: list[dict[str, str]], errors: list[str]) -> None:
    record_ids: set[str] = set()
    duplicate_ids: set[str] = set()
    for row_number, record in enumerate(records, start=1):
        record_id = record.get("Nonconformance ID", "").strip()
        display_id = record_id or f"row {row_number}"
        missing_values = [field for field in REQUIRED_NONCONFORMANCE_FIELDS if not record.get(field, "").strip()]
        if missing_values:
            errors.append(
                f"Nonconformance record {display_id} has empty required field(s): {', '.join(missing_values)}."
            )
        if record_id:
            if not record_id.startswith(NONCONFORMANCE_ID_PREFIX):
                errors.append(f"Nonconformance record {record_id} must start with {NONCONFORMANCE_ID_PREFIX}.")
            if record_id in record_ids:
                duplicate_ids.add(record_id)
            record_ids.add(record_id)

    if duplicate_ids:
        errors.append("Duplicate nonconformance id(s): " + ", ".join(sorted(duplicate_ids)) + ".")


def _require_declared_matrix_value(
    row_number: int,
    row: dict[str, str],
    requirement: dict[str, Any],
    row_field: str,
    requirement_field: str,
    errors: list[str],
) -> None:
    row_values = [value.strip() for value in row.get(row_field, "").split(";") if value.strip()]
    declared = {str(value).strip() for value in requirement.get(requirement_field, [])}
    missing = [value for value in row_values if value not in declared]
    if missing:
        requirement_id = str(requirement.get("id", ""))
        errors.append(
            f"Traceability matrix row {row_number} {row_field} value(s) are not declared by "
            f"{requirement_id}: {', '.join(missing)}."
        )


def _validate_artifact_reference(
    repo_root: Path,
    requirement_id: str,
    field_name: str,
    reference: str,
    errors: list[str],
) -> None:
    if _is_external_or_issue_reference(reference):
        return
    if "*" in reference:
        if not list(repo_root.glob(reference)):
            errors.append(f"Requirement {requirement_id} {field_name} glob '{reference}' does not match any files.")
        return
    if not (repo_root / reference).exists():
        errors.append(f"Requirement {requirement_id} {field_name} path '{reference}' does not exist.")


def _validate_test_reference(repo_root: Path, requirement_id: str, reference: str, errors: list[str]) -> None:
    if "::" not in reference:
        errors.append(f"Requirement {requirement_id} verification test '{reference}' must use path::test_name.")
        return
    path_text, test_name = reference.split("::", 1)
    path = repo_root / path_text
    if not path.exists():
        errors.append(f"Requirement {requirement_id} verification test path '{path_text}' does not exist.")
        return
    if f"def {test_name}" not in path.read_text(encoding="utf-8"):
        errors.append(f"Requirement {requirement_id} verification test '{path_text}' does not define test {test_name}.")


def _count_by(records: list[dict[str, Any]], field_name: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        key = str(record.get(field_name, "unspecified"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _is_external_or_issue_reference(reference: str) -> bool:
    return "://" in reference or reference.startswith("#")


def _extract_markdown_table(markdown: str, heading: str) -> tuple[list[dict[str, str]], list[str]]:
    lines = markdown.splitlines()
    heading_marker = f"## {heading}"
    try:
        heading_index = next(index for index, line in enumerate(lines) if line.strip() == heading_marker)
    except StopIteration as exc:
        raise QAArtifactError(f"Markdown artifact is missing {heading_marker} section.") from exc

    table_lines: list[str] = []
    for line in lines[heading_index + 1 :]:
        stripped = line.strip()
        if stripped.startswith("## "):
            break
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines.append(stripped)
        elif table_lines:
            break

    if len(table_lines) < 2:
        raise QAArtifactError(f"Markdown section {heading_marker} must include a table.")

    columns = _split_markdown_table_row(table_lines[0])
    if not _is_markdown_separator_row(_split_markdown_table_row(table_lines[1])):
        raise QAArtifactError(f"Markdown section {heading_marker} table must include a separator row.")

    rows: list[dict[str, str]] = []
    for table_line in table_lines[2:]:
        cells = _split_markdown_table_row(table_line)
        rows.append({column: cells[index] if index < len(cells) else "" for index, column in enumerate(columns)})

    return rows, columns


def _split_markdown_table_row(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _is_markdown_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(cell.replace("-", "").replace(":", "").strip() == "" and "-" in cell for cell in cells)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, dict)):
        return not value
    return False
