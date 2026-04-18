from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any


def build_runtime_context(*, command: list[str] | None = None) -> dict[str, Any]:
    service = os.environ.get("THORIUM_REACTOR_RUNTIME_SERVICE", "host")
    image = os.environ.get("THORIUM_REACTOR_RUNTIME_IMAGE")
    tool_runtime = os.environ.get("THORIUM_REACTOR_TOOL_RUNTIME")
    tool_version = os.environ.get("THORIUM_REACTOR_TOOL_VERSION")
    resolved_command = list(command or [])
    return {
        "service": service,
        "image": image,
        "image_ref": image,
        "tool_runtime": tool_runtime,
        "tool_version": tool_version,
        "containerized": service != "host",
        "command": resolved_command,
        "container_command": resolved_command,
        "git_commit": _git_output(["rev-parse", "HEAD"]),
        "git_branch": _git_output(["rev-parse", "--abbrev-ref", "HEAD"]),
    }


def _git_output(args: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_discover_cwd()),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _discover_cwd() -> Path:
    return Path.cwd()
