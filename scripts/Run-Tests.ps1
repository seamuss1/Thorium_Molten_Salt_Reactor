param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $repoRoot

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
    $PytestArgs = @("tests")
}

& docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build app python -m pytest @PytestArgs
exit $LASTEXITCODE
