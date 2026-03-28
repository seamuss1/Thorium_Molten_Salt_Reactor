param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

. (Join-Path $PSScriptRoot "Enter-PytbknShell.ps1")

if (-not $CliArgs -or $CliArgs.Count -eq 0) {
    & python -m thorium_reactor.cli --help
    exit $LASTEXITCODE
}

& python -m thorium_reactor.cli @CliArgs
exit $LASTEXITCODE
