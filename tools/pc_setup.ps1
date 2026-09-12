$ErrorActionPreference = 'Stop'
$repoPath = Split-Path $PSScriptRoot -Parent
$configPath = Join-Path $repoPath 'backend\pc_access.json'
$utf8 = [System.Text.UTF8Encoding]::new($false)
function Read-Config {
    if (Test-Path -LiteralPath $configPath) { return Get-Content -LiteralPath $configPath -Raw -Encoding UTF8 | ConvertFrom-Json }
    return [pscustomobject]@{ video_folders = [pscustomobject]@{}; commands = [pscustomobject]@{}; tailscale_origin = ''; tailscale_login = '' }
}
function Save-Config($config) {
    [IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 6), $utf8)
}
function Tailscale-Exe {
    $found = Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($found) { return $found.Source }
    $installed = Join-Path $env:ProgramFiles 'Tailscale\tailscale.exe'
    if (Test-Path -LiteralPath $installed) { return $installed }
    throw 'Tailscale is not installed. Choose 1 to install it first.'
}
Write-Host 'Kaguya AI - PC connection setup'
Write-Host '1 Install Tailscale (Windows permission dialog may appear)'
Write-Host '2 Enable private HTTPS access for your Tailscale account'
Write-Host '3 Add a video folder'
Write-Host '4 Register one BAT file'
Write-Host '5 Edit registrations / remove entries'
Write-Host '6 Disable HTTPS serving on port 443'
Write-Host '0 Exit'
$choice = Read-Host 'Select'
switch ($choice) {
    '1' {
        & winget.exe install --id Tailscale.Tailscale --exact --source winget
        if ($LASTEXITCODE -ne 0) { throw 'Tailscale installation failed. Install from https://tailscale.com/download/windows' }
        Write-Host 'Installed. Run this file again and choose 2.'
    }
    '2' {
        $tailscaleExe = Tailscale-Exe
        & $tailscaleExe up
        if ($LASTEXITCODE -ne 0) { throw 'Tailscale login was not completed.' }
        $statusText = & $tailscaleExe status --json
        if ($LASTEXITCODE -ne 0) { throw 'Could not read Tailscale status.' }
        $status = $statusText | ConvertFrom-Json
        $dnsName = ([string]$status.Self.DNSName).TrimEnd('.')
        $user = $status.User.PSObject.Properties[[string]$status.Self.UserID].Value
        if (-not $dnsName.EndsWith('.ts.net') -or -not $user.LoginName) { throw 'Use a personal, untagged Tailscale device/account.' }
        $serveText = & $tailscaleExe serve status --json
        if ($LASTEXITCODE -ne 0) { throw 'Could not inspect current Serve settings.' }
        $serveConfig = ($serveText -join "`n") | ConvertFrom-Json
        if ($serveConfig.TCP -or $serveConfig.Web) {
            Write-Host 'Serve is already configured. Existing settings were kept. Check them before adding Kaguya.'
            Write-Host 'If Kaguya is already configured, continue using its URL.'
            exit 0
        }
        $config = Read-Config
        $config.tailscale_origin = 'https://' + $dnsName
        $config.tailscale_login = [string]$user.LoginName
        Save-Config $config
        & $tailscaleExe serve --bg --https=443 http://127.0.0.1:8765
        if ($LASTEXITCODE -ne 0) { throw 'Serve setup failed. Follow the HTTPS enablement link shown by Tailscale, then choose 2 again.' }
        Write-Host 'Private URL:' $config.tailscale_origin
        Write-Host 'Install Tailscale on iPhone, sign into the same account, then open this URL in Safari.'
        Write-Host 'Keep this PC awake and Kaguya running. Funnel is not used.'
    }
    '3' {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.FolderBrowserDialog
        $dialog.Description = 'Choose the folder containing MP4 videos'
        if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
        $label = Read-Host 'Folder label'
        if (-not $label -or $label.Length -gt 80) { throw 'Use a label between 1 and 80 characters.' }
        $config = Read-Config
        if ($config.video_folders.PSObject.Properties[$label]) { throw 'This label already exists. Use menu 5 to edit it.' }
        $config.video_folders | Add-Member -NotePropertyName $label -NotePropertyValue $dialog.SelectedPath
        Save-Config $config
        Write-Host 'Saved. Refresh the PC tab.'
    }
    '4' {
        Add-Type -AssemblyName System.Windows.Forms
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Filter = 'Windows batch file (*.bat)|*.bat'
        $dialog.InitialDirectory = [Environment]::GetFolderPath('Desktop')
        if ($dialog.ShowDialog() -ne 'OK') { exit 0 }
        Write-Host 'Register only trusted, non-interactive BAT files. No pause, prompts, or admin elevation.'
        $name = Read-Host 'Display name'
        $description = Read-Host 'What this BAT does (shown before every execution)'
        if (-not $name -or $name.Length -gt 80 -or $description.Length -gt 300) { throw 'Name or description is invalid.' }
        $config = Read-Config
        $id = 'bat-' + [Guid]::NewGuid().ToString('N')
        $item = [pscustomobject]@{ name = $name; path = $dialog.FileName; description = $description; timeout_seconds = 300 }
        $config.commands | Add-Member -NotePropertyName $id -NotePropertyValue $item
        Save-Config $config
        Write-Host 'Registered. Refresh the PC tab. Execution still requires confirmation.'
    }
    '5' {
        if (-not (Test-Path -LiteralPath $configPath)) { Save-Config (Read-Config) }
        Start-Process notepad.exe -ArgumentList ('"' + $configPath + '"')
    }
    '6' {
        $tailscaleExe = Tailscale-Exe
        $config = Read-Config
        if (-not $config.tailscale_origin) { throw 'No Kaguya Serve registration was found.' }
        & $tailscaleExe serve --https=443 off
        if ($LASTEXITCODE -ne 0) { throw 'Could not disable serving.' }
        $config.tailscale_origin = ''; $config.tailscale_login = ''; Save-Config $config
        Write-Host 'Private HTTPS access disabled. Local Kaguya is unchanged.'
    }
    '0' { exit 0 }
    default { throw 'Unknown selection.' }
}
