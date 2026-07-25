import os
import shutil
import subprocess
from pathlib import Path

import pytest

from thorium_reactor import runtime_context


def test_build_runtime_context_routes_git_commands_to_explicit_cwd(tmp_path: Path, monkeypatch) -> None:
    git_cwds: list[Path] = []

    def fake_run(args, **kwargs):
        cwd = kwargs["cwd"]
        git_cwds.append(Path(cwd))
        if args[1:] == ["status", "--porcelain=v1", "-z", "--untracked-files=all"]:
            stdout = b""
        elif args[1:] == ["rev-parse", "--abbrev-ref", "HEAD"]:
            stdout = "feature/test\n"
        else:
            stdout = "abc123\n"
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(runtime_context.subprocess, "run", fake_run)

    context = runtime_context.build_runtime_context(command=["run", "example_pin"], cwd=tmp_path)

    assert context["git_branch"] == "feature/test"
    assert git_cwds
    assert set(git_cwds) == {tmp_path.resolve()}


@pytest.mark.slow
def test_git_worktree_status_hashes_untracked_only_changes(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    untracked = tmp_path / "untracked.txt"
    untracked.write_text("first payload\n", encoding="utf-8")

    status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["dirty"] is True
    assert status["modified"] == []
    assert status["untracked"] == ["untracked.txt"]
    assert status["diff_hash"] is not None

    untracked.write_text("changed payload\n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_large_untracked_file_contents(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    untracked = tmp_path / "large-untracked.bin"
    untracked.write_bytes(b"a" * (6 * 1024 * 1024))

    status = runtime_context.git_worktree_status(cwd=tmp_path)

    untracked.write_bytes(b"b" * (6 * 1024 * 1024))
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_untracked_paths_that_porcelain_would_quote(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    untracked = tmp_path / "unicodé-untracked.txt"
    untracked.write_text("first payload\n", encoding="utf-8")

    status = runtime_context.git_worktree_status(cwd=tmp_path)

    untracked.write_text("changed payload\n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["untracked"] == ["unicodé-untracked.txt"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_untracked_paths_from_subdirectory_cwd(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    nested = tmp_path / "nested"
    nested.mkdir()
    untracked = nested / "subdir-untracked.txt"
    untracked.write_text("same size payload A\n", encoding="utf-8")

    status = runtime_context.git_worktree_status(cwd=nested)

    untracked.write_text("same size payload B\n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=nested)

    assert status["dirty"] is True
    assert status["untracked"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_prefers_repo_root_for_untracked_status_paths(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    nested = tmp_path / "nested"
    nested.mkdir()
    root_file = tmp_path / "collision.txt"
    nested_collision = nested / "collision.txt"
    root_file.write_text("root payload A\n", encoding="utf-8")
    nested_collision.write_text("nested payload\n", encoding="utf-8")

    status = runtime_context.git_worktree_status(cwd=nested)

    root_file.write_text("root payload B\n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=nested)

    assert "collision.txt" in status["untracked"]
    assert "nested/collision.txt" in status["untracked"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_tracked_trailing_whitespace(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=tmp_path, capture_output=True, text=True, check=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )

    tracked.write_text("base\ntrailing space \n", encoding="utf-8")
    status = runtime_context.git_worktree_status(cwd=tmp_path)
    tracked.write_text("base\ntrailing space  \n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["modified"] == ["tracked.txt"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_untracked_embedded_repo_contents(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    embedded = tmp_path / "embedded"
    embedded.mkdir()
    subprocess.run(["git", "init"], cwd=embedded, capture_output=True, text=True, check=True)
    payload = embedded / "payload.txt"
    payload.write_text("first payload\n", encoding="utf-8")

    status = runtime_context.git_worktree_status(cwd=tmp_path)
    payload.write_text("changed payload\n", encoding="utf-8")
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["dirty"] is True
    assert status["untracked"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_untracked_symlink_targets(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    first_target = tmp_path.parent / f"{tmp_path.name}-outside-a.txt"
    second_target = tmp_path.parent / f"{tmp_path.name}-outside-b.txt"
    first_target.write_text("outside A\n", encoding="utf-8")
    second_target.write_text("outside B\n", encoding="utf-8")
    link = tmp_path / "outside-link.txt"
    try:
        os.symlink(first_target, link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    status = runtime_context.git_worktree_status(cwd=tmp_path)
    link.unlink()
    os.symlink(second_target, link)
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["untracked"] == ["outside-link.txt"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]


@pytest.mark.slow
def test_git_worktree_status_hashes_broken_untracked_symlink_targets(tmp_path: Path) -> None:
    if shutil.which("git") is None:
        pytest.skip("git executable is required for worktree status provenance")
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, text=True, check=True)
    link = tmp_path / "broken-link.txt"
    try:
        os.symlink(tmp_path / "missing-a.txt", link)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    status = runtime_context.git_worktree_status(cwd=tmp_path)
    link.unlink()
    os.symlink(tmp_path / "missing-b.txt", link)
    changed_status = runtime_context.git_worktree_status(cwd=tmp_path)

    assert status["untracked"] == ["broken-link.txt"]
    assert status["diff_hash"] is not None
    assert changed_status["diff_hash"] is not None
    assert changed_status["diff_hash"] != status["diff_hash"]
