function Build-WebUI {
    param([string]$RepoRoot)
    $ErrorActionPreference = 'Stop'
    $uiRoot = Join-Path $RepoRoot 'web\ui'
    Push-Location $uiRoot
    try {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
            throw 'npm.cmd was not found. Install Node.js before building the web UI.'
        }
        # Building every time is intentional: an old dist/index.html says nothing
        # about the current source, lockfile, or Node runtime.
        & npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw "npm ci failed (exit $LASTEXITCODE)." }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw "UI build failed (exit $LASTEXITCODE)." }
    }
    finally { Pop-Location }
}
