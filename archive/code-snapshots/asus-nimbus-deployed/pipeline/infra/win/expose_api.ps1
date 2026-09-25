# Publishes the Nimbus API on the existing cloudflared tunnel. Adding a hostname never removes another,
# so the old lookcam.akvaithi.page keeps working for links already out there.
# Run elevated on the box: powershell -ExecutionPolicy Bypass -File expose_api.ps1 [-HostName x.akvaithi.page]
param([string]$HostName = "nimbus.akvaithi.page")
$ErrorActionPreference = "Stop"
$CfDir = "C:\Windows\System32\config\systemprofile\.cloudflared"
$Cfg = Join-Path $CfDir "config.yml"
$Cf = "C:\Program Files (x86)\cloudflared\cloudflared.exe"

$tunnel = ((Get-Content $Cfg | Select-String '^tunnel:') -split ':\s*')[1].Trim()
Write-Output "tunnel: $tunnel"

if (-not (Select-String -Path $Cfg -Pattern $HostName -Quiet)) {
    Copy-Item $Cfg "$Cfg.bak" -Force
    $lines = Get-Content $Cfg
    $out = foreach ($line in $lines) {
        if ($line -match 'service:\s*http_status:404') {
            "  - hostname: $HostName"
            "    service: http://localhost:8000"
        }
        $line
    }
    Set-Content $Cfg $out
    Write-Output "ingress added"
} else {
    Write-Output "ingress already present"
}

$env:TUNNEL_ORIGIN_CERT = Join-Path $CfDir "cert.pem"
# cloudflared logs to stderr; under "Stop" PowerShell treats that as a failure and skips the restart.
$ErrorActionPreference = "Continue"
& $Cf tunnel route dns --overwrite-dns $tunnel $HostName 2>&1 | ForEach-Object { "dns: $_" }
$ErrorActionPreference = "Stop"
Restart-Service cloudflared
Start-Sleep 4
Get-Service cloudflared | ForEach-Object { "service: $($_.Status)" }
Get-Content $Cfg
