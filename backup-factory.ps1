$ErrorActionPreference = "Stop"

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw "Python was not found. Install Python 3.11+ and the project requirements first."
}

& $python.Source (Join-Path $PSScriptRoot "scripts\factory_backup.py")
if ($LASTEXITCODE -ne 0) {
    throw "Factory backup operation failed with exit code $LASTEXITCODE."
}
