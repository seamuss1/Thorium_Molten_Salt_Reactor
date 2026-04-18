param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$CliArgs
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

function Resolve-DockerService {
    param(
        [string[]]$Args
    )

    if (-not $Args -or $Args.Count -eq 0) {
        return "app"
    }

    $command = $Args[0].ToLowerInvariant()
    if ($command -eq "benchmark") {
        return "openmc"
    }
    if ($command -eq "run") {
        if ($Args -contains "--no-solver") {
            return "app"
        }
        return "openmc"
    }
    if ($command -in @("thermochimica", "saltproc", "moltres")) {
        return $command
    }
    return "app"
}

$service = Resolve-DockerService -Args $CliArgs
$composeArgs = @("compose", "run", "--rm", "--build", $service, "python", "-m", "thorium_reactor.cli")

if (-not $CliArgs -or $CliArgs.Count -eq 0) {
    & docker @composeArgs --help
    exit $LASTEXITCODE
}

& docker @composeArgs @CliArgs
exit $LASTEXITCODE
