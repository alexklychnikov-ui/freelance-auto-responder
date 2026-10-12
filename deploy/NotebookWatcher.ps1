# Supervised Bridge A: VPS /notebook job -> local Journal -> Google Sheet.
# Single-instance + auto-restart. Log: logs/notebook_watcher.log
$ErrorActionPreference = "Continue"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

$LogDir = Join-Path $RepoRoot "logs"
$LogFile = Join-Path $LogDir "notebook_watcher.log"
$OutLog = Join-Path $LogDir "notebook_watcher.out.log"
$ErrLog = Join-Path $LogDir "notebook_watcher.err.log"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log([string]$Message) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $line -Encoding UTF8
}

$mutex = New-Object System.Threading.Mutex($false, "Local\FreelanceNotebookWatcher")
if (-not $mutex.WaitOne(0)) {
    Write-Log "already running - exit"
    exit 0
}

try {
    $envFile = Join-Path $RepoRoot ".env"
    if (-not (Test-Path $envFile)) {
        Write-Log "FATAL: .env missing"
        exit 1
    }
    $saLine = Select-String -Path $envFile -Pattern "^\s*GOOGLE_SERVICE_ACCOUNT_FILE\s*=" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $saLine) {
        Write-Log "FATAL: GOOGLE_SERVICE_ACCOUNT_FILE not in .env"
        exit 1
    }
    $saPath = ($saLine.Line -split "=", 2)[1].Trim().Trim('"').Trim("'")
    if (-not (Test-Path -LiteralPath $saPath)) {
        Write-Log "FATAL: SA file missing: $saPath"
        exit 1
    }

    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        $python = $pyCmd.Source
    } else {
        $python = "C:\Users\User\AppData\Local\Programs\Python\Python313\python.exe"
    }
    if (-not (Test-Path -LiteralPath $python)) {
        Write-Log "FATAL: python not found"
        exit 1
    }

    $env:PYTHONUNBUFFERED = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Write-Log "start supervisor pid=$PID python=$python"

    while ($true) {
        Write-Log "launch: python -m src.journal.notebook_cli watch --poll 15"
        foreach ($f in @($OutLog, $ErrLog)) {
            if (Test-Path -LiteralPath $f) {
                Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue
            }
        }
        # stdout/stderr MUST be different files (Windows Start-Process limitation)
        $p = Start-Process -FilePath $python `
            -ArgumentList "-m","src.journal.notebook_cli","watch","--poll","15" `
            -WorkingDirectory $RepoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $OutLog `
            -RedirectStandardError $ErrLog `
            -PassThru `
            -Wait
        $tail = ""
        if (Test-Path -LiteralPath $ErrLog) {
            $tail = (Get-Content -LiteralPath $ErrLog -Tail 8 -ErrorAction SilentlyContinue) -join " | "
        }
        if (-not $tail -and (Test-Path -LiteralPath $OutLog)) {
            $tail = (Get-Content -LiteralPath $OutLog -Tail 3 -ErrorAction SilentlyContinue) -join " | "
        }
        Write-Log "watch exited code=$($p.ExitCode) - restart in 10s last=$tail"
        Start-Sleep -Seconds 10
    }
}
finally {
    [void]$mutex.ReleaseMutex()
    $mutex.Dispose()
}
