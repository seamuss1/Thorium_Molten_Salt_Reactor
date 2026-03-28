param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$PytestArgs
)

. (Join-Path $PSScriptRoot "Enter-PytbknShell.ps1")

if (-not $PytestArgs -or $PytestArgs.Count -eq 0) {
    $PytestArgs = @("tests")
}

& python -m pytest @PytestArgs
exit $LASTEXITCODE
