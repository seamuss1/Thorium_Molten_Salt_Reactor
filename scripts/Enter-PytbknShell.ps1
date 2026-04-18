param(
    [switch]$Bootstrap
)
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($Bootstrap) {
    & docker compose build app
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
}

Write-Host "thorium reactor container shell ready"
Write-Host "  repo    $repoRoot"
Write-Host "  shell   docker compose run --rm app sh"
Write-Host "  tests   docker compose run --rm app python -m pytest"
Write-Host "  cli     docker compose run --rm app python -m thorium_reactor.cli"
Write-Host "  solver  docker compose run --rm openmc python -m thorium_reactor.cli benchmark <case>"

& docker compose run --rm --build app sh
