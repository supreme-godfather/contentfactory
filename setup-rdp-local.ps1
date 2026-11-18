$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$examplePath = Join-Path $projectRoot ".env.example"

if (Test-Path -LiteralPath $envPath) {
    throw ".env already exists. To protect existing credentials, this setup will not overwrite it. Run install-rdp-api.ps1 to install or restart the local API."
}
if (-not (Test-Path -LiteralPath $examplePath)) {
    throw ".env.example is missing from the factory folder."
}

$botTokenSecure = Read-Host "Enter the Telegram bot token (input is hidden; it stays on this RDP)" -AsSecureString
$botTokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($botTokenSecure)
$botToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($botTokenPointer)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($botTokenPointer)
if ($botToken -notmatch '^\d{5,}:[A-Za-z0-9_-]{20,}$') {
    Remove-Variable botToken
    throw "The Telegram bot token format did not validate. Nothing was saved."
}

$ownerIds = Read-Host "Enter your numeric Telegram user ID (comma-separated only if you have multiple owner accounts)"
if ($ownerIds -notmatch '^\d+(,\d+)*$') {
    Remove-Variable botToken
    throw "Telegram owner IDs must be digits separated by commas. Nothing was saved."
}

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    Remove-Variable botToken
    throw "Python Launcher 'py' was not found. Install Python 3.11+ from python.org before setup."
}

function New-UrlSafeSecret([int]$byteCount) {
    $bytes = New-Object byte[] $byteCount
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function New-FernetKey {
    $bytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $generator.GetBytes($bytes)
    } finally {
        $generator.Dispose()
    }
    return [Convert]::ToBase64String($bytes).Replace('+', '-').Replace('/', '_')
}

$values = [ordered]@{
    APP_ENV = "development"
    APP_PAUSED = "true"
    DATABASE_URL = "sqlite:///./data/factory.db"
    PUBLIC_BASE_URL = ""
    ADMIN_API_TOKEN = (New-UrlSafeSecret 32)
    WORKER_TOKEN = (New-UrlSafeSecret 32)
    TELEGRAM_BOT_TOKEN = $botToken
    TELEGRAM_MODE = "polling"
    TELEGRAM_WEBHOOK_SECRET = ""
    TELEGRAM_OWNER_IDS = $ownerIds
    TOKEN_ENCRYPTION_KEY = (New-FernetKey)
    MEDIA_ROOT = "data/media"
    YOUTUBE_ENABLED = "false"
    MEDIA_GENERATION_ENABLED = "false"
    INSTAGRAM_ENABLED = "false"
    AUTOMATION_ENABLED = "false"
    MOCK_PUBLISHING = "true"
}
Remove-Variable botToken

$content = Get-Content -LiteralPath $examplePath -Raw
foreach ($key in $values.Keys) {
    $pattern = "(?m)^$([regex]::Escape($key))=.*$"
    if ($content -notmatch $pattern) {
        throw "The example configuration is missing the $key setting. No .env file was written."
    }
    $content = [regex]::Replace(
        $content,
        $pattern,
        "$key=$($values[$key])",
        [System.Text.RegularExpressions.RegexOptions]::None
    )
}
[System.IO.File]::WriteAllText(
    $envPath,
    $content,
    (New-Object System.Text.UTF8Encoding($false))
)
Remove-Variable values, content

Write-Host "Private RDP configuration created. Credentials and generated secrets were not displayed."
Write-Host "Installing dependencies and starting the localhost-only API."
& (Join-Path $projectRoot "install-rdp-api.ps1")
if ($LASTEXITCODE -ne 0) {
    throw "API setup did not complete. The private .env is preserved; rerun install-rdp-api.ps1 after correcting the reported issue."
}
Write-Host "Next install the RDP worker from the worker folder with:"
Write-Host '.\install-rdp-worker.ps1 -FactoryUrl "http://127.0.0.1:8000" -UseFactoryEnvToken'
