param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 18488,
    [switch]$SkipUiBuild,
    [switch]$RequireAccessIdentity
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uiRoot = Join-Path $repoRoot "web\ui"
$distIndex = Join-Path $uiRoot "dist\index.html"

if (-not $SkipUiBuild -and -not (Test-Path $distIndex)) {
    Push-Location $uiRoot
    try {
        if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
            Write-Error "npm.cmd was not found. Install Node.js or run the backend API separately with uvicorn."
            exit 1
        }
        & npm.cmd install
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) {
            exit $LASTEXITCODE
        }
    }
    finally {
        Pop-Location
    }
}

$portArgs = @("--service-ports")
if ($Port -ne 18488) {
    $portArgs = @("-p", "${Port}:${Port}")
}

$accessEnvArgs = @()
if (-not $RequireAccessIdentity) {
    $accessEnvArgs = @(
        "-e", "THORIUM_REACTOR_ACCESS_REQUIRED=0",
        "-e", "THORIUM_REACTOR_LOCAL_DEV_EMAIL=seamusdgallagher@gmail.com"
    )
}

Push-Location $repoRoot
try {
    & docker compose run --rm --build @portArgs @accessEnvArgs web uvicorn thorium_reactor.web.app:create_app --factory --host $HostName --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
