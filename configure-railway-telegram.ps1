param(
    [switch]$RotateTelegramToken
)

$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$projectId = "75733d46-5731-49e9-b47e-cf870f606127"
$environment = "production"
$serviceName = "contentfactory"
$railwayCmd = Get-Command railway.cmd -ErrorAction SilentlyContinue
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $railwayCmd -or -not $node) {
    throw "Railway CLI's Node.js launcher was not found. Install/sign in to Railway CLI before running this setup."
}
$railwayScript = Join-Path (Split-Path $railwayCmd.Source -Parent) "node_modules\@railway\cli\bin\railway.js"
if (-not (Test-Path -LiteralPath $railwayScript)) {
    throw "Could not locate the Railway CLI JavaScript entry point."
}

$variableJson = & $node.Source $railwayScript variable list --json --service $serviceName --environment $environment --project $projectId 2>$null | Out-String
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect Railway variable names and presence. No values were changed."
}
$variables = $variableJson | ConvertFrom-Json
Remove-Variable variableJson

function Test-RailwayVariable([string]$Name) {
    $property = $variables.PSObject.Properties[$Name]
    return $null -ne $property -and -not [string]::IsNullOrWhiteSpace([string]$property.Value)
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

function Set-RailwayVariableFromStdin([string]$Name, [string]$Value) {
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $node.Source
    $startInfo.Arguments = "`"$railwayScript`" variable set $Name --stdin --service $serviceName --environment $environment --project $projectId --skip-deploys"
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardInput = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (-not $process.Start()) {
        throw "Could not start Railway CLI for variable $Name."
    }
    $process.StandardInput.WriteLine($Value)
    $process.StandardInput.Close()
    $stdoutTask = $process.StandardOutput.ReadToEndAsync()
    $stderrTask = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    $null = $stdoutTask.Result
    $null = $stderrTask.Result
    if ($process.ExitCode -ne 0) {
        throw "Railway rejected variable $Name (exit code $($process.ExitCode)). Its value was not displayed."
    }
}

function Set-RailwayVariableIfMissing([string]$Name, [string]$Value) {
    if (Test-RailwayVariable $Name) {
        Write-Host "$Name already exists; preserving it."
        return
    }
    Set-RailwayVariableFromStdin $Name $Value
}

if (-not (Test-RailwayVariable "TELEGRAM_BOT_TOKEN") -or $RotateTelegramToken) {
    $botTokenSecure = Read-Host "Enter the Telegram BotFather token (hidden input; it is sent directly to Railway, never to chat)" -AsSecureString
    $botTokenPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($botTokenSecure)
    $botToken = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($botTokenPointer)
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($botTokenPointer)
    Remove-Variable botTokenSecure, botTokenPointer
    if ($botToken -notmatch '^\d{5,}:[A-Za-z0-9_-]{20,}$') {
        Remove-Variable botToken
        throw "The Telegram token format is invalid. No token was sent to Railway."
    }
    try {
        Set-RailwayVariableFromStdin "TELEGRAM_BOT_TOKEN" $botToken
    } finally {
        Remove-Variable botToken -ErrorAction SilentlyContinue
    }
} else {
    Write-Host "TELEGRAM_BOT_TOKEN already exists; preserving it."
}

if (-not (Test-RailwayVariable "TELEGRAM_OWNER_IDS")) {
    $ownerIds = Read-Host "Enter your Telegram numeric owner ID (digits only)"
    if ($ownerIds -notmatch '^\d{5,20}$') {
        throw "The Telegram owner ID must be numeric. No owner ID was sent to Railway."
    }
    Set-RailwayVariableFromStdin "TELEGRAM_OWNER_IDS" $ownerIds
    Remove-Variable ownerIds
} else {
    Write-Host "TELEGRAM_OWNER_IDS already exists; preserving the admin allowlist."
}

Set-RailwayVariableIfMissing "ADMIN_API_TOKEN" (New-UrlSafeSecret 32)
Set-RailwayVariableIfMissing "WORKER_TOKEN" (New-UrlSafeSecret 32)
Set-RailwayVariableIfMissing "TELEGRAM_WEBHOOK_SECRET" (New-UrlSafeSecret 32)
Set-RailwayVariableIfMissing "TOKEN_ENCRYPTION_KEY" (New-FernetKey)

Write-Host "Telegram configuration checked for Railway project serene-gentleness / service contentfactory."
Write-Host "Existing secrets and the admin allowlist were preserved. Values were not printed."
Write-Host "Deployments were skipped while variables were set. Redeploy contentfactory only if a value changed."
