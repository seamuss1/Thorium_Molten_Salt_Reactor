import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell wrapper contract")
REPO = Path(__file__).resolve().parents[1]


def invoke(tmp_path, args="", *, existing=True, fail_npm=False):
    shutil.copytree(REPO / "scripts", tmp_path / "scripts")
    ui = tmp_path / "web/ui"
    (ui / "src").mkdir(parents=True)
    (ui / "src/App.tsx").write_text("changed input")
    if existing:
        (ui / "dist").mkdir()
        (ui / "dist/index.html").write_text("stale bundle")
    capture = tmp_path / "calls.ndjson"
    harness = tmp_path / "harness.ps1"
    harness.write_text(
        """
$ErrorActionPreference = 'Stop'
function global:npm.cmd {
    ConvertTo-Json -Compress -InputObject @('npm', $args) | Add-Content $env:AUDIT_CAPTURE
    $global:LASTEXITCODE = [int]$env:AUDIT_FAIL_NPM
}
function global:docker {
    ConvertTo-Json -Compress -InputObject @('docker', $args) | Add-Content $env:AUDIT_CAPTURE
    $global:LASTEXITCODE = 0
}
try { & (Join-Path $PSScriptRoot 'scripts/Run-Web.ps1') """
        + args
        + "; exit $LASTEXITCODE } catch { Write-Host $_; exit 1 }"
    )
    env = {**os.environ, "AUDIT_CAPTURE": str(capture), "AUDIT_FAIL_NPM": "1" if fail_npm else "0"}
    env.pop("THORIUM_REACTOR_PROXY_SHARED_SECRET", None)
    env.pop("THORIUM_REACTOR_ADMIN_EMAILS", None)
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
        capture_output=True,
        text=True,
        env=env,
    )
    calls = [json.loads(line) for line in capture.read_text().splitlines()] if capture.exists() else []
    return result, calls


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("port", [18488, 18489])
def test_default_and_stale_builds_finish_before_loopback_start(tmp_path, existing, port):
    result, calls = invoke(tmp_path, f"-Port {port}", existing=existing)
    assert result.returncode == 0, result.stderr
    assert calls[:2] == [["npm", ["ci"]], ["npm", ["run", "build"]]]
    assert calls[2][0] == "docker"
    assert f"127.0.0.1:{port}:{port}" in calls[2][1]


def test_skip_is_explicit_and_requires_existing_bundle(tmp_path):
    result, calls = invoke(tmp_path, "-SkipUiBuild")
    assert result.returncode == 0
    assert [call[0] for call in calls] == ["docker"]


@pytest.mark.parametrize(
    "args,existing,fail",
    [
        ("-SkipUiBuild", False, False),
        ("", True, True),
        ("-PublishAddress 0.0.0.0", True, False),
        ("-PublishAddress 0.0.0.0 -RequireAccessIdentity", True, False),
    ],
)
def test_invalid_or_failed_start_never_launches_docker(tmp_path, args, existing, fail):
    result, calls = invoke(tmp_path, args, existing=existing, fail_npm=fail)
    assert result.returncode != 0
    assert all(call[0] != "docker" for call in calls)
