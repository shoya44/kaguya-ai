param(
  [int]$WaitPid = 0
)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$logDir = Join-Path $env:LOCALAPPDATA 'KaguyaAI'
$logPath = Join-Path $logDir 'update.log'
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

function Write-UpdateLog([string]$Message) {
  $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
  Add-Content -Path $logPath -Encoding UTF8 -Value "[$stamp] $Message"
}

Write-UpdateLog "update helper started; wait_pid=$WaitPid"

if ($WaitPid -gt 0) {
  try {
    Wait-Process -Id $WaitPid -ErrorAction SilentlyContinue
  } catch {
    Write-UpdateLog "wait failed: $($_.Exception.Message)"
  }
}

# app.exe終了直後のファイルロック解放を少し待つ。
Start-Sleep -Milliseconds 400
Set-Location $root

$update = Join-Path $root 'update_repo.bat'
$start = Join-Path $root 'start.bat'

if (Test-Path $update) {
  & $update *> $null
  $updateCode = $LASTEXITCODE
  Write-UpdateLog "update_repo.bat exit=$updateCode"
} else {
  Write-UpdateLog 'update_repo.bat was not found; starting current version'
}

# 更新失敗・オフライン・ローカル変更ありでも、現在の版は必ず起動を試みる。
if (Test-Path $start) {
  & $start *> $null
  $startCode = $LASTEXITCODE
  Write-UpdateLog "start.bat exit=$startCode"
} else {
  Write-UpdateLog 'start.bat was not found'
}
