from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

from thorium_reactor.web.repository import TERMINAL_STATUSES, WebRepository, read_json, utc_now
from thorium_reactor.web.schemas import RunEvent, RunRecord, SimulationDraft, copy_model, model_to_dict


ALLOWED_PHASES = ("build", "run", "transient", "transient-sweep", "validate", "render", "report")
DEFAULT_PHASE_TIMEOUT_SECONDS = 900.0


class JobManager:
    def __init__(self, repository: WebRepository, *, max_workers: int = 2) -> None:
        self.repository = repository
        self._semaphore = threading.Semaphore(max_workers)

    def submit(self, draft: SimulationDraft) -> RunRecord:
        phases = normalize_phases(draft.phases)
        bundle = self.repository.prepare_run_bundle(draft)
        draft = copy_model(draft, update={"run_id": bundle.run_id})
        status = {
            "case_name": bundle.case_name,
            "run_id": bundle.run_id,
            "status": "queued",
            "phase": None,
            "command_plan": phases,
            "created_at": utc_now(),
            "started_at": None,
            "finished_at": None,
            "progress": 0.0,
        }
        write_status(bundle.root, status)
        append_event(bundle.root, "info", None, "Run queued.", progress=0.0)
        thread = threading.Thread(target=self._run_job, args=(bundle.root, draft, phases), daemon=True)
        thread.start()
        return self.repository.get_run(bundle.case_name, bundle.run_id)

    def _run_job(self, run_dir: Path, draft: SimulationDraft, phases: list[str]) -> None:
        with self._semaphore:
            if os.environ.get("THORIUM_REACTOR_WEB_FAKE_JOBS") == "1":
                self._run_fake_job(run_dir, draft, phases)
                return
            status = read_json(run_dir / "job_status.json", {})
            status.update({"status": "running", "started_at": utc_now(), "progress": 0.01})
            write_status(run_dir, status)
            append_event(run_dir, "info", None, "Run started.", progress=0.01)
            try:
                for index, phase in enumerate(phases, start=1):
                    progress = (index - 1) / max(len(phases), 1)
                    status.update({"status": "running", "phase": phase, "progress": progress})
                    write_status(run_dir, status)
                    append_event(run_dir, "info", phase, f"Starting {phase}.", progress=progress)
                    self._run_phase(run_dir, draft, phase)
                    append_event(run_dir, "info", phase, f"Completed {phase}.", progress=index / len(phases))
                status.update({"status": "completed", "phase": "completed", "finished_at": utc_now(), "progress": 1.0})
                write_status(run_dir, status)
                append_event(run_dir, "info", "completed", "Run completed.", progress=1.0)
            except Exception as exc:  # noqa: BLE001 - job failures are persisted for the browser.
                status.update({"status": "failed", "finished_at": utc_now(), "error": str(exc)})
                write_status(run_dir, status)
                append_event(run_dir, "error", status.get("phase"), str(exc), progress=status.get("progress"))

    def _run_phase(self, run_dir: Path, draft: SimulationDraft, phase: str) -> None:
        command = build_cli_command(draft, phase)
        env = os.environ.copy()
        src_path = str(self.repository.repo_root / "src")
        env["PYTHONPATH"] = src_path + os.pathsep + env.get("PYTHONPATH", "")
        process = subprocess.Popen(
            command,
            cwd=str(self.repository.repo_root),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        timed_out = {"value": False}

        def kill_on_timeout() -> None:
            timed_out["value"] = True
            append_event(run_dir, "error", phase, f"Phase '{phase}' exceeded the {phase_timeout_seconds(phase):.0f} second job budget.")
            try:
                process.kill()
            except OSError:
                pass

        timer = threading.Timer(phase_timeout_seconds(phase), kill_on_timeout)
        timer.daemon = True
        timer.start()
        try:
            for line in process.stdout:
                message = line.strip()
                if message:
                    append_event(run_dir, "log", phase, message)
            return_code = process.wait()
        finally:
            timer.cancel()
        if timed_out["value"]:
            raise TimeoutError(f"Phase '{phase}' exceeded the {phase_timeout_seconds(phase):.0f} second job budget.")
        if return_code != 0:
            raise RuntimeError(f"Phase '{phase}' failed with exit code {return_code}.")

    def _run_fake_job(self, run_dir: Path, draft: SimulationDraft, phases: list[str]) -> None:
        status = read_json(run_dir / "job_status.json", {})
        status.update({"status": "running", "started_at": utc_now()})
        write_status(run_dir, status)
        for index, phase in enumerate(phases, start=1):
            progress = index / max(len(phases), 1)
            status.update({"phase": phase, "progress": progress})
            write_status(run_dir, status)
            append_event(run_dir, "info", phase, f"Fake {phase} completed.", progress=progress)
            if phase == "run":
                write_json(
                    run_dir / "summary.json",
                    {
                        "case": draft.case_name,
                        "result_dir": str(run_dir),
                        "metrics": {"expected_cells": 4, "keff": 1.0},
                        "neutronics": {"status": "dry-run"},
                        "workflow_capabilities": ["neutronics_only"],
                    },
                )
            if phase == "validate":
                write_json(run_dir / "validation.json", {"case": draft.case_name, "passed": True, "checks": []})
            if phase == "report":
                (run_dir / "report.md").write_text(f"# {draft.case_name}\n\nFake web report.\n", encoding="utf-8")
            time.sleep(0.01)
        status.update({"status": "completed", "phase": "completed", "finished_at": utc_now(), "progress": 1.0})
        write_status(run_dir, status)
        append_event(run_dir, "info", "completed", "Run completed.", progress=1.0)


def normalize_phases(phases: list[str]) -> list[str]:
    requested = [phase for phase in phases if phase in ALLOWED_PHASES]
    if not requested:
        requested = ["run", "validate", "report"]
    if any(phase in requested for phase in ("validate", "render", "report", "transient", "transient-sweep")) and "run" not in requested:
        requested.append("run")
    ordered = [phase for phase in ALLOWED_PHASES if phase in requested]
    if "transient-sweep" in ordered and "transient" in ordered:
        ordered.remove("transient-sweep")
        transient_index = ordered.index("transient")
        ordered.insert(transient_index + 1, "transient-sweep")
    return ordered


def build_cli_command(draft: SimulationDraft, phase: str) -> list[str]:
    command = [sys.executable, "-m", "thorium_reactor.cli", phase, draft.case_name]
    if draft.run_id:
        command.extend(["--run-id", draft.run_id, "--reuse-run-id"])
    if phase == "run":
        command.append("--no-solver")
    if phase == "transient" and draft.scenario:
        command.extend(["--scenario", draft.scenario])
    if phase == "transient-sweep":
        if draft.scenario:
            command.extend(["--scenario", draft.scenario])
        command.extend(["--samples", str(draft.sweep_samples), "--seed", str(draft.sweep_seed)])
        if draft.prefer_gpu:
            command.append("--prefer-gpu")
    return command


def phase_timeout_seconds(phase: str) -> float:
    configured = os.environ.get("THORIUM_REACTOR_WEB_PHASE_TIMEOUT_S")
    if configured:
        try:
            value = float(configured)
        except ValueError:
            value = DEFAULT_PHASE_TIMEOUT_SECONDS
        return max(value, 1.0)
    if phase == "transient-sweep":
        return DEFAULT_PHASE_TIMEOUT_SECONDS * 2.0
    return DEFAULT_PHASE_TIMEOUT_SECONDS


def write_status(run_dir: Path, payload: dict[str, Any]) -> None:
    write_json(run_dir / "job_status.json", payload)


def append_event(run_dir: Path, level: str, phase: str | None, message: str, *, progress: float | None = None) -> RunEvent:
    path = run_dir / "job_events.ndjson"
    sequence = 1
    if path.exists():
        sequence = len(path.read_text(encoding="utf-8").splitlines()) + 1
    event = RunEvent(sequence=sequence, timestamp=utc_now(), level=level, phase=phase, message=message, progress=progress)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(model_to_dict(event), sort_keys=True) + "\n")
    return event


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def is_terminal(status: str) -> bool:
    return status in TERMINAL_STATUSES
