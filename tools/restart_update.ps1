param(
  [int]$WaitPid = 0
)

$ErrorActionPreference = 'Continue'
# このスクリプトは tools/ に置く。更新対象と start.bat はその1つ上。
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tools = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $env:LOCALAPPDATA 'KaguyaAI'
$logPath = Join-Path $logDir 'update.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-UpdateLog([string]$Message) {
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -Path $logPath -Encoding UTF8 -Value "[$stamp] $Message"
}

Write-UpdateLog "update helper started; wait_pid=$WaitPid"

if ($WaitPid -gt 0) {
  # Normally Tauri exits immediately. Poll instead of waiting forever; if the
  # old instance is stuck, terminate only the exact app PID/tree passed by it.
  for ($i = 0; $i -lt 40; $i++) {
    if (-not (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 250
  }
  if (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) {
    Write-UpdateLog 'old app did not exit in 10 seconds; stopping its own process tree'
    & taskkill.exe /PID $WaitPid /T /F 2>&1 | ForEach-Object { Write-UpdateLog $_ }
  }
}

Start-Sleep -Milliseconds 500
Set-Location $root

$update = Join-Path $tools 'update_repo.bat'
$start = Join-Path $root 'start.bat'

if (Test-Path $update) {
  & $update 2>&1 | ForEach-Object { Write-UpdateLog $_ }
  $updateCode = $LASTEXITCODE
  Write-UpdateLog "update_repo.bat exit=$updateCode"
} else {
  Write-UpdateLog 'update_repo.bat was not found; starting current version'
}

# 更新失敗・オフライン・ローカル変更ありでも、現在の版は必ず起動を試みる。
# detached実行なのでstart.batの失敗時pauseは無効化する。
if (Test-Path $start) {
  $env:KAGUYA_NO_PAUSE = '1'
  & $start 2>&1 | ForEach-Object { Write-UpdateLog $_ }
  $startCode = $LASTEXITCODE
  Remove-Item Env:KAGUYA_NO_PAUSE -ErrorAction SilentlyContinue
  Write-UpdateLog "start.bat exit=$startCode"
} else {
  Write-UpdateLog 'start.bat was not found'
}
