$ErrorActionPreference = "Stop"
$projectRoot = $PSScriptRoot
$railwayCmd = Get-Command railway.cmd -ErrorAction SilentlyContinue
$node = Get-Command node.exe -ErrorAction SilentlyContinue
if (-not $railwayCmd -or -not $node) {
    throw "Railway CLI's Node.js launcher was not found."
}
$railwayScript = Join-Path (Split-Path $railwayCmd.Source -Parent) "node_modules\@railway\cli\bin\railway.js"
if (-not (Test-Path -LiteralPath $railwayScript)) {
    throw "Could not locate the Railway CLI JavaScript entry point."
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
    $startInfo.Arguments = "`"$railwayScript`" variable set $Name --stdin --service factory-api --skip-deploys"
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

Set-RailwayVariableFromStdin "ADMIN_API_TOKEN" (New-UrlSafeSecret 32)
Set-RailwayVariableFromStdin "WORKER_TOKEN" (New-UrlSafeSecret 32)
Set-RailwayVariableFromStdin "TELEGRAM_WEBHOOK_SECRET" (New-UrlSafeSecret 32)
Set-RailwayVariableFromStdin "TOKEN_ENCRYPTION_KEY" (New-FernetKey)

Write-Host "Generated ADMIN_API_TOKEN, WORKER_TOKEN, TELEGRAM_WEBHOOK_SECRET, and TOKEN_ENCRYPTION_KEY directly in Railway."
Write-Host "Secret values were never printed and deployments were skipped."
