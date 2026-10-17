param(
    [string]$FactoryUrl = "https://factory-api-production-2fae.up.railway.app",
    [int]$PollSeconds = 30
)

$ErrorActionPreference = "Stop"
$installRoot = "C:\ProgramData\HindiContentFactory"
$workerPath = Join-Path $installRoot "worker.py"
$tokenPath = Join-Path $installRoot "worker-token.txt"
$venvPath = Join-Path $installRoot ".venv"
$taskName = "Hindi Content Factory Worker"
$sourceWorker = Join-Path $PSScriptRoot "worker.py"
$sourceRequirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run PowerShell as Administrator, then run this script again."
}

$python = Get-Command py -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python Launcher 'py' was not found. Install Python 3.11+ from python.org and enable the launcher."
}

$secureToken = Read-Host "Paste WORKER_TOKEN (input is hidden; never send it in chat)" -AsSecureString
$tokenPtr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
$token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($tokenPtr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($tokenPtr)
if ([string]::IsNullOrWhiteSpace($token)) {
    throw "WORKER_TOKEN is required."
}

New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
Copy-Item -LiteralPath $sourceWorker -Destination $workerPath -Force
Copy-Item -LiteralPath $sourceRequirements -Destination (Join-Path $installRoot "requirements.txt") -Force

Set-Content -LiteralPath $tokenPath -Value $token -NoNewline -Encoding utf8
$acl = Get-Acl $tokenPath
$acl.SetAccessRuleProtection($true, $false)
$rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "SYSTEM", "Read", "Allow")
$acl.SetAccessRule($rule)
$adminRule = New-Object System.Security.AccessControl.FileSystemAccessRule(
    "BUILTIN\Administrators", "Read", "Allow")
$acl.SetAccessRule($adminRule)
Set-Acl -LiteralPath $tokenPath -AclObject $acl
Remove-Variable token

if (-not (Test-Path (Join-Path $venvPath "Scripts\python.exe"))) {
    & py -3 -m venv $venvPath
}
& (Join-Path $venvPath "Scripts\python.exe") -m pip install --disable-pip-version-check -r (Join-Path $installRoot "requirements.txt")

$taskCommand = "`"$workerPath`" --url `"$FactoryUrl`" --token-file `"$tokenPath`" --poll-seconds $PollSeconds --output-dir `"$installRoot\media`""
& schtasks.exe /Create /TN $taskName /TR "`"$($venvPath)\Scripts\python.exe`" $taskCommand" /SC ONSTART /RU SYSTEM /RL HIGHEST /F | Out-Host
if ($LASTEXITCODE -ne 0) {
    throw "Failed to create the Windows startup task. schtasks exit code: $LASTEXITCODE"
}
& schtasks.exe /Run /TN $taskName | Out-Host

Write-Host "Installed and started: $taskName"
Write-Host "Factory URL: $FactoryUrl"
Write-Host "Worker files: $installRoot"
Write-Host "The worker uses outbound HTTPS only; no inbound port was opened."
