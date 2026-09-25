# Installs the nimbus pipeline API on the GPU box and runs it behind cloudflared.
# Source is copied to %USERPROFILE%\hackmit\pipeline by deploy_api.sh; this builds the venv and
# registers the "NimbusAPI" scheduled task. Idempotent.
$ErrorActionPreference = "Stop"
$Root = "$env:USERPROFILE\hackmit\pipeline"
$Py = "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\python.exe"
if (-not (Test-Path "$Root\.venv\Scripts\python.exe")) { & $Py -m venv "$Root\.venv" }
& "$Root\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& "$Root\.venv\Scripts\python.exe" -m pip install -e $Root --quiet
# Free the port before (re)starting, or the new process exits and the task looks fine.
# The project was called lookcam before it was Nimbus: retire that task, or it would restart the old API
# at logon and hold the port.
if (Get-ScheduledTask -TaskName "LookcamAPI" -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName "LookcamAPI" -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName "LookcamAPI" -Confirm:$false
}
Stop-ScheduledTask -TaskName "NimbusAPI" -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*nimbus.api*' -or $_.CommandLine -like '*lookcam.api*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep 2
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c $env:USERPROFILE\hackmit\start_api.bat"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "NimbusAPI" -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
Start-ScheduledTask -TaskName "NimbusAPI"
"NimbusAPI registered and started"
