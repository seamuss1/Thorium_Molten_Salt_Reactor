param(
    [switch]$Bootstrap
)

function Add-PathEntries {
    param(
        [string[]]$Entries
    )

    $currentEntries = @()
    if (-not [string]::IsNullOrWhiteSpace($env:PATH)) {
        $currentEntries = $env:PATH.Split(";") | Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    }

    $combined = @()
    foreach ($entry in @($Entries + $currentEntries)) {
        if ([string]::IsNullOrWhiteSpace($entry)) {
            continue
        }
        if (-not ($combined | Where-Object { $_ -eq $entry })) {
            $combined += $entry
        }
    }
    $env:PATH = ($combined -join ";")
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$runtimePrefix = Join-Path $repoRoot ".runtime-env"
$pythonExe = Join-Path $runtimePrefix "python.exe"
$micromambaExe = Join-Path $repoRoot ".tools\\Library\\bin\\micromamba.exe"
$mambaRoot = Join-Path $repoRoot ".mamba"
$tempRoot = Join-Path $repoRoot ".tmp"
$pipCacheRoot = Join-Path $repoRoot ".pip-cache"
$srcRoot = Join-Path $repoRoot "src"

foreach ($path in @($mambaRoot, $tempRoot, $pipCacheRoot)) {
    New-Item -ItemType Directory -Force -Path $path | Out-Null
}

if (-not (Test-Path $pythonExe)) {
    if (-not $Bootstrap) {
        throw "Missing repo runtime at '$runtimePrefix'. Re-run with -Bootstrap to create it."
    }
    if (-not (Test-Path $micromambaExe)) {
        throw "Missing micromamba executable at '$micromambaExe'."
    }

    & $micromambaExe create --yes --root-prefix $mambaRoot --prefix $runtimePrefix --file (Join-Path $repoRoot "environment.yml")
    if ($LASTEXITCODE -ne 0) {
        throw "micromamba failed to create the repo runtime."
    }
}

$env:REPO_ROOT = $repoRoot
$env:PYTBKN_ENV = $runtimePrefix
$env:MAMBA_ROOT_PREFIX = $mambaRoot
$env:TEMP = $tempRoot
$env:TMP = $tempRoot
$env:PIP_CACHE_DIR = $pipCacheRoot
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $srcRoot
}
else {
    "$srcRoot;$($env:PYTHONPATH)"
}

Add-PathEntries -Entries @(
    $runtimePrefix,
    (Join-Path $runtimePrefix "Scripts"),
    (Join-Path $repoRoot ".tools\\Library\\bin")
)

Set-Alias python $pythonExe -Scope Global

function global:pytest {
    & $pythonExe -m pytest @Args
}

function global:reactor {
    & $pythonExe -m thorium_reactor.cli @Args
}

Write-Host "pytbkn shell ready"
Write-Host "  repo   $repoRoot"
Write-Host "  python $pythonExe"
Write-Host "  tests  pytest"
Write-Host "  cli    reactor"
