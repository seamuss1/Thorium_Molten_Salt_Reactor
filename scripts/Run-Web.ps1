param(
    [string]$HostName = "0.0.0.0",
    [int]$Port = 8000
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Push-Location $repoRoot
try {
    & docker compose run --rm --build --service-ports web uvicorn thorium_reactor.web.app:create_app --factory --host $HostName --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
