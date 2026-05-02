$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uiRoot = Join-Path $repoRoot "web\ui"

Push-Location $uiRoot
try {
    if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        Write-Error "npm.cmd was not found. Install Node.js before building the web UI."
        exit 1
    }
    & npm.cmd install
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & npm.cmd run build
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
