$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$launcherPath = Join-Path $projectRoot "run-rdp-api.cmd"
$taskName = "HindiContentFactoryApi"

if (-not (Test-Path $envPath)) {
    throw "Create .env from .env.example and enter settings privately before installation."
}
if (-not (Test-Path $launcherPath)) {
    throw "run-rdp-api.cmd is missing from the factory folder."
}

$envText = Get-Content -LiteralPath $envPath -Raw
foreach ($requiredSetting in @(
    "TELEGRAM_MODE=polling",
    "APP_PAUSED=true",
    "MOCK_PUBLISHING=true",
    "INSTAGRAM_ENABLED=false"
)) {
    if ($envText -notmatch "(?m)^\s*$([regex]::Escape($requiredSetting))\s*$") {
        throw "For safe private RDP setup, .env must contain $requiredSetting."
    }
}
$requiredValues = @{
    TELEGRAM_BOT_TOKEN = '(?m)^\s*TELEGRAM_BOT_TOKEN\s*=\s*\d{5,}:[A-Za-z0-9_-]{20,}\s*$'
    WORKER_TOKEN = '(?m)^\s*WORKER_TOKEN\s*=\s*[!-~]{20,}\s*$'
    TELEGRAM_OWNER_IDS = '(?m)^\s*TELEGRAM_OWNER_IDS\s*=\s*\d+(,\d+)*\s*$'
}
foreach ($key in $requiredValues.Keys) {
    if ($envText -notmatch $requiredValues[$key]) {
        throw ".env must contain a valid $key value before starting the private RDP service."
    }
}
Remove-Variable envText

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw "Python Launcher 'py' was not found. Install Python 3.11+ from python.org."
}
if (-not (Test-Path $venvPython)) {
    & py -3 -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the factory virtual environment."
    }
}
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $projectRoot "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Could not install factory dependencies."
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    Stop-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
}
if (Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue) {
    throw "Port 8000 is already in use. Stop the existing local API or another service on that port, then retry."
}
$action = New-ScheduledTaskAction `
    -Execute $env:ComSpec `
    -Argument "/d /c `"`"$launcherPath`"`"" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal `
    -UserId $identity `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Private localhost-only Hindi Content Factory API" `
    -Force | Out-Null

Start-ScheduledTask -TaskName $taskName
Write-Host "Factory API startup task installed. It listens only on 127.0.0.1:8000."
Write-Host "Telegram uses outbound polling; do not open inbound firewall ports."
Write-Host "API logs are written to $projectRoot\logs\api.log"
Start-Sleep -Seconds 8
try {
    $readiness = Invoke-WebRequest -Uri "http://127.0.0.1:8000/ready" -TimeoutSec 10
    Write-Host "Readiness check: HTTP $([int]$readiness.StatusCode)"
} catch {
    Write-Warning "Readiness is not confirmed yet. Check the scheduled task and logs\api.log; do not paste logs containing secrets into chat."
}
