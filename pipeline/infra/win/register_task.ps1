# Run once from an ELEVATED PowerShell on the box.
# 1. Starts ComfyUI at logon so a reboot does not take the demo down.
# 2. Admits port 8188 from the ZeroTier network only.
$bat = "$env:USERPROFILE\hackmit\start_comfyui.bat"

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "ComfyUI" -Action $action -Trigger $trigger -Settings $settings -Force

New-NetFirewallRule -DisplayName "ComfyUI (ZeroTier only)" -Direction Inbound -Protocol TCP `
    -LocalPort 8188 -RemoteAddress 172.25.0.0/16 -Action Allow -ErrorAction SilentlyContinue

Start-ScheduledTask -TaskName "ComfyUI"
