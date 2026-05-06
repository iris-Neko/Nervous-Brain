param(
    [string]$EnvName = "nervous-brain",
    [string]$ScriptPath = "scripts/run_telegram_bot_polling.py",
    [switch]$Debug,
    [switch]$DryRun,
    [switch]$DropPendingOnStart,
    [int]$StartupWaitSeconds = 3
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

$LogDir = Join-Path $Root "data/logs"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

$StdoutLog = Join-Path $LogDir "telegram_bot_polling.stdout.log"
$StderrLog = Join-Path $LogDir "telegram_bot_polling.stderr.log"

function Get-BotProcesses {
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match [regex]::Escape($ScriptPath)
        }
}

Write-Host "[1/3] Stopping existing Telegram bot processes..."
$Existing = @(Get-BotProcesses)
foreach ($Proc in $Existing) {
    Write-Host "  stop pid=$($Proc.ProcessId) name=$($Proc.Name)"
    Stop-Process -Id $Proc.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 800
$Remaining = @(Get-BotProcesses)
if ($Remaining.Count -gt 0) {
    $Pids = ($Remaining | ForEach-Object { $_.ProcessId }) -join ", "
    throw "Failed to stop bot process(es): $Pids"
}

Write-Host "[2/3] Starting Telegram bot..."
$BotArgs = @("run", "-n", $EnvName, "python", $ScriptPath)
if ($Debug) {
    $BotArgs += "--debug"
}
if ($DryRun) {
    $BotArgs += "--dry-run"
}
if ($DropPendingOnStart) {
    $BotArgs += "--drop-pending-on-start"
}

$Process = Start-Process `
    -FilePath "mamba" `
    -ArgumentList $BotArgs `
    -WorkingDirectory $Root `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -WindowStyle Hidden `
    -PassThru

Write-Host "  mamba pid=$($Process.Id)"
Write-Host "  stdout=$StdoutLog"
Write-Host "  stderr=$StderrLog"

Start-Sleep -Seconds ([Math]::Max(0, $StartupWaitSeconds))

Write-Host "[3/3] Current bot processes:"
$Now = @(Get-BotProcesses)
if ($Now.Count -eq 0) {
    Write-Warning "No bot process found after startup. Check $StderrLog"
    exit 1
}

$Now |
    Select-Object ProcessId, Name, CommandLine |
    Format-List

Write-Host "Telegram bot restarted."
