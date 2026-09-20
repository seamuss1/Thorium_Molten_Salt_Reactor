param(
    [string]$HostName = "0.0.0.0",
    [string]$PublishAddress = "127.0.0.1",
    [ValidateRange(1, 65535)]
    [int]$Port = 18488,
    [switch]$SkipUiBuild,
    [switch]$RequireAccessIdentity
)

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$uiRoot = Join-Path $repoRoot "web\ui"
$distIndex = Join-Path $uiRoot "dist\index.html"
. (Join-Path $PSScriptRoot 'Web-UI.ps1')

$publishIp = $null
if (-not [System.Net.IPAddress]::TryParse($PublishAddress, [ref]$publishIp)) {
    Write-Error 'PublishAddress must be an IP address.'
    exit 1
}
if (-not [System.Net.IPAddress]::IsLoopback($publishIp)) {
    if (-not $RequireAccessIdentity -or [string]::IsNullOrWhiteSpace($env:THORIUM_REACTOR_PROXY_SHARED_SECRET) -or [string]::IsNullOrWhiteSpace($env:THORIUM_REACTOR_ADMIN_EMAILS)) {
        Write-Error 'Remote publishing requires -RequireAccessIdentity, THORIUM_REACTOR_PROXY_SHARED_SECRET, and THORIUM_REACTOR_ADMIN_EMAILS.'
        exit 1
    }
}
try {
    if (-not $SkipUiBuild) { Build-WebUI -RepoRoot $repoRoot }
    elseif (-not (Test-Path $distIndex)) { throw '-SkipUiBuild requires an existing web/ui/dist/index.html.' }
}
catch { Write-Error $_; exit 1 }
$publishHost = $PublishAddress
if ($publishIp.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetworkV6) {
    $publishHost = "[$PublishAddress]"
}
$portArgs = @('-p', "${publishHost}:${Port}:${Port}")

$accessEnvArgs = @('-e', 'THORIUM_REACTOR_ACCESS_REQUIRED=1')
if (-not $RequireAccessIdentity) {
    $accessEnvArgs = @(
        "-e", "THORIUM_REACTOR_ACCESS_REQUIRED=0",
        "-e", "THORIUM_REACTOR_LOCAL_DEV_EMAIL=developer@localhost",
        "-e", "THORIUM_REACTOR_ADMIN_EMAILS=developer@localhost"
    )
}

Push-Location $repoRoot
try {
    & docker compose -f docker-compose.yml -f docker-compose.dev.yml run --rm --build @portArgs @accessEnvArgs web uvicorn thorium_reactor.web.app:create_app --factory --host $HostName --port $Port
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
