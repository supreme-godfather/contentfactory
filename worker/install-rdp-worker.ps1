param(
    [string]$FactoryUrl = "https://contentfactory-production-511548c5.up.railway.app",
    [int]$PollSeconds = 30,
    [switch]$UseFactoryEnvToken
)

$ErrorActionPreference = "Stop"
trap {
    Write-Host ""
    Write-Host "INSTALLATION FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}
$installRoot = Join-Path $env:LOCALAPPDATA "HindiContentFactory"
$workerPath = Join-Path $installRoot "worker.py"
$tokenPath = Join-Path $installRoot "worker-token.txt"
$venvPath = Join-Path $installRoot ".venv"
$launcherPath = Join-Path $installRoot "run-worker.cmd"
$startupPath = [Environment]::GetFolderPath("Startup")
$startupShortcut = Join-Path $startupPath "Hindi Content Factory Worker.lnk"
$sourceWorker = Join-Path $PSScriptRoot "worker.py"
$sourceRequirements = Join-Path $PSScriptRoot "requirements.txt"

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python Launcher 'py' was not found. Install Python 3.11+ from python.org and enable the launcher."
}
$ffmpeg = Get-Command ffmpeg.exe -ErrorAction SilentlyContinue
if (-not $ffmpeg) {
    throw "ffmpeg.exe was not found on PATH. Install FFmpeg and restart PowerShell before installing the worker."
}
$ffprobe = Get-Command ffprobe.exe -ErrorAction SilentlyContinue
if (-not $ffprobe) {
    throw "ffprobe.exe was not found on PATH. Install an FFmpeg build that includes ffprobe before installing the worker."
}

if ($UseFactoryEnvToken) {
    $factoryEnvPath = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
    if (-not (Test-Path -LiteralPath $factoryEnvPath)) {
        throw "The factory .env file was not found. Run this switch only for a colocated local API."
    }
    $match = [regex]::Match(
        (Get-Content -LiteralPath $factoryEnvPath -Raw),
        "(?m)^\s*WORKER_TOKEN\s*=\s*([^\r\n#]+)\s*$"
    )
    if (-not $match.Success) {
        throw "WORKER_TOKEN is missing from the factory .env file."
    }
    $token = $match.Groups[1].Value.Trim()
    Remove-Variable match, factoryEnvPath
} else {
    $secureToken = Read-Host "Paste WORKER_TOKEN (input is hidden; never send it in chat)" -AsSecureString
    $tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
    $token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
    Remove-Variable secureToken, tokenPtr
}
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "WORKER_TOKEN is required."
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
Copy-Item -LiteralPath $sourceWorker -Destination $workerPath -Force
Copy-Item -LiteralPath $sourceRequirements -Destination (Join-Path $installRoot "requirements.txt") -Force

Set-Content -LiteralPath $tokenPath -Value $token -NoNewline -Encoding ascii
Remove-Variable token

if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & py -3 -m venv $venvPath
}
& (Join-Path $venvPath "Scripts\python.exe") -m pip install --disable-pip-version-check -r (Join-Path $installRoot "requirements.txt")

$launcherContent = @"
@echo off
"$($venvPath)\Scripts\python.exe" "$workerPath" --url "$FactoryUrl" --token-file "$tokenPath" --poll-seconds $PollSeconds --output-dir "$installRoot\media"
"@
Set-Content -LiteralPath $launcherPath -Value $launcherContent -Encoding ascii

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($startupShortcut)
$shortcut.TargetPath = $launcherPath
$shortcut.WorkingDirectory = $installRoot
$shortcut.WindowStyle = 7
$shortcut.Save()

$oldProcesses = Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($workerPath)
    }
foreach ($process in $oldProcesses) {
    Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Process -FilePath $launcherPath -WorkingDirectory $installRoot -WindowStyle Minimized

Write-Host "Installed and started the user Startup worker."
Write-Host "Factory URL: $FactoryUrl"
Write-Host "Worker files: $installRoot"
Write-Host "The worker makes outbound API requests only; no inbound port was opened."
Write-Host "Startup shortcut: $startupShortcut"
Read-Host "Installation complete. Press Enter to close"
