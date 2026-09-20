$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
. (Join-Path $PSScriptRoot 'Web-UI.ps1')
try {
    Build-WebUI -RepoRoot $repoRoot
    exit 0
}
catch {
    Write-Error $_
    exit 1
}
