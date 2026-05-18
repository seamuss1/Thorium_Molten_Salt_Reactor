from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def build_runtime_context(*, command: list[str] | None = None, cwd: Path | str | None = None) -> dict[str, Any]:
    service = os.environ.get("THORIUM_REACTOR_RUNTIME_SERVICE", "host")
    image = os.environ.get("THORIUM_REACTOR_RUNTIME_IMAGE")
    tool_runtime = os.environ.get("THORIUM_REACTOR_TOOL_RUNTIME")
    tool_version = os.environ.get("THORIUM_REACTOR_TOOL_VERSION")
    resolved_command = list(command or [])
    git_cwd = _discover_cwd(cwd)
    dependency_summary = _dependency_summary()
    return {
        "service": service,
        "image": image,
        "image_ref": image,
        "tool_runtime": tool_runtime,
        "tool_version": tool_version,
        "containerized": service != "host",
        "command": resolved_command,
        "container_command": resolved_command,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "backend": {
            "service": service,
            "device": os.environ.get("THORIUM_REACTOR_DEVICE", "host-cpu"),
        },
        "dependencies": dependency_summary,
        "dependency_hash": _stable_hash(dependency_summary),
        "git_commit": _git_output(["rev-parse", "HEAD"], cwd=git_cwd),
        "git_branch": _git_output(["rev-parse", "--abbrev-ref", "HEAD"], cwd=git_cwd),
        "git": git_worktree_status(cwd=git_cwd),
    }


def git_worktree_status(*, cwd: Path | str | None = None) -> dict[str, Any]:
    git_cwd = _discover_cwd(cwd)
    status_entries = _git_status_porcelain(cwd=git_cwd)
    if status_entries is None:
        return {
            "available": False,
            "dirty": False,
            "modified": [],
            "untracked": [],
            "diff_hash": None,
        }
    modified: list[str] = []
    untracked: list[str] = []
    for code, path in status_entries:
        if code == "??":
            untracked.append(path)
        else:
            modified.append(path)
    diff_hash = _git_diff_hash(cwd=git_cwd, untracked_paths=untracked) if modified or untracked else None
    return {
        "available": True,
        "dirty": bool(modified or untracked),
        "modified": sorted(modified),
        "untracked": sorted(untracked),
        "diff_hash": diff_hash,
    }


def _git_status_porcelain(*, cwd: Path) -> list[tuple[str, str]] | None:
    try:
        completed = subprocess.run(
            ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
            capture_output=True,
            check=False,
            cwd=str(cwd),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return _parse_porcelain_z(completed.stdout)


def _parse_porcelain_z(payload: bytes | str) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    delimiter: bytes | str = b"\0" if isinstance(payload, bytes) else "\0"
    parts = [part for part in payload.split(delimiter) if part]
    index = 0
    while index < len(parts):
        entry = parts[index]
        if len(entry) < 4:
            index += 1
            continue
        if isinstance(entry, bytes):
            code = entry[:2].decode("ascii", errors="replace")
            path = os.fsdecode(entry[3:])
        else:
            code = entry[:2]
            path = entry[3:]
        entries.append((code, path))
        index += 1
        if code.startswith("R") or code.startswith("C"):
            index += 1
    return entries


def _git_output(args: list[str], *, cwd: Path | str | None = None) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            text=True,
            check=False,
            cwd=str(_discover_cwd(cwd)),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    value = completed.stdout.strip()
    return value or None


def _git_diff_hash(*, cwd: Path, untracked_paths: list[str] | None = None) -> str | None:
    chunks: list[dict[str, Any]] = []
    for args in (["diff", "--binary"], ["diff", "--cached", "--binary"]):
        value = _git_binary_output(args, cwd=cwd)
        if value:
            chunks.append({"args": args, "payload_sha256": hashlib.sha256(value).hexdigest(), "size": len(value)})
    repo_root = _git_repository_root(cwd) or cwd
    untracked = _untracked_hash_records(cwd=cwd, repo_root=repo_root, paths=untracked_paths or [])
    if not chunks and not untracked:
        return None
    payload = {"tracked_diffs": chunks, "untracked": untracked}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8", errors="replace")
    return hashlib.sha256(encoded).hexdigest()


def _git_binary_output(args: list[str], *, cwd: Path | str | None = None) -> bytes | None:
    try:
        completed = subprocess.run(
            ["git", *args],
            capture_output=True,
            check=False,
            cwd=str(_discover_cwd(cwd)),
        )
    except OSError:
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout or None


def _git_repository_root(cwd: Path) -> Path | None:
    root = _git_output(["rev-parse", "--show-toplevel"], cwd=cwd)
    return Path(root).resolve() if root else None


def _untracked_hash_records(*, cwd: Path, repo_root: Path, paths: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    resolved_repo_root = repo_root.resolve()
    for path in sorted(set(paths)):
        file_path = _resolve_untracked_status_path(cwd=cwd, repo_root=resolved_repo_root, status_path=path)
        if file_path is None:
            records.append({"path": path, "type": "outside_cwd"})
            continue
        if file_path.is_symlink():
            records.append({"path": path, "type": "symlink", "target": _readlink_text(file_path)})
            continue
        if not file_path.exists():
            records.append({"path": path, "type": "missing"})
            continue
        if file_path.is_dir():
            directory_hash, entry_count = _directory_sha256(file_path)
            records.append({"path": path, "type": "directory", "entries": entry_count, "sha256": directory_hash})
            continue
        try:
            stat = file_path.stat()
        except OSError:
            records.append({"path": path, "type": "unreadable"})
            continue
        record: dict[str, Any] = {"path": path, "type": "file", "size": stat.st_size, "sha256": _file_sha256(file_path)}
        records.append(record)
    return records


def _resolve_untracked_status_path(*, cwd: Path, repo_root: Path, status_path: str) -> Path | None:
    resolved_cwd = cwd.resolve()
    candidates = [repo_root / status_path, resolved_cwd / status_path]
    for candidate in candidates:
        lexical_candidate = _normalize_path(candidate)
        if _is_relative_to(lexical_candidate, repo_root) and (lexical_candidate.exists() or lexical_candidate.is_symlink()):
            return candidate
    fallback = _normalize_path(candidates[0])
    if _is_relative_to(fallback, repo_root):
        return fallback
    return None


def _normalize_path(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _file_sha256(path: Path) -> str | None:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _directory_sha256(path: Path) -> tuple[str | None, int]:
    digest = hashlib.sha256()
    entries = 0
    try:
        children = sorted(path.rglob("*"), key=lambda child: child.relative_to(path).as_posix())
    except OSError:
        return None, entries
    for child in children:
        entries += 1
        relative = child.relative_to(path).as_posix()
        digest.update(relative.encode("utf-8", errors="surrogateescape"))
        digest.update(b"\0")
        if child.is_symlink():
            digest.update(b"symlink\0")
            digest.update((_readlink_text(child) or "").encode("utf-8", errors="surrogateescape"))
            digest.update(b"\0")
        elif child.is_dir():
            digest.update(b"dir\0")
        elif child.is_file():
            digest.update(b"file\0")
            file_hash = _file_sha256(child)
            digest.update((file_hash or "unreadable").encode("ascii"))
            digest.update(b"\0")
        else:
            digest.update(b"other\0")
    return digest.hexdigest(), entries


def _readlink_text(path: Path) -> str | None:
    try:
        return os.readlink(path)
    except OSError:
        return None


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _dependency_summary() -> dict[str, str]:
    package_names = (
        "numpy",
        "scipy",
        "pyyaml",
        "openmc",
        "fastapi",
        "pydantic",
        "matplotlib",
    )
    summary: dict[str, str] = {}
    for name in package_names:
        try:
            summary[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return summary


def _stable_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _discover_cwd(cwd: Path | str | None = None) -> Path:
    return Path(cwd).resolve() if cwd is not None else Path.cwd()
