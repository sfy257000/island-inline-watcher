# Destructive install/uninstall checks are restricted to disposable GitHub runners.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run this script only on a disposable GitHub Actions Windows runner.' }
$root = Split-Path $PSScriptRoot -Parent
$reports = Join-Path $root 'build\validation'
New-Item -ItemType Directory -Force $reports | Out-Null
$installer = @(Get-ChildItem (Join-Path $root 'release\*-setup.exe'))
if ($installer.Count -ne 1) { throw 'Expected exactly one installer.' }
$installDir = Join-Path $env:TEMP ('島語 安裝測試-' + [guid]::NewGuid().ToString('N'))
$dataDir = Join-Path $env:LOCALAPPDATA 'IslandInlineWatcher'
if (Test-Path $dataDir) { throw 'The runner already has application data; refusing to modify it.' }
function Run-Process {
    param([string]$File, [string[]]$ProcessArguments, [int]$Timeout = 120000)
    $process = Start-Process -FilePath $File -ArgumentList $ProcessArguments -PassThru
    if (-not $process.WaitForExit($Timeout)) { $process.Kill(); throw "Process timed out: $File" }
    if ($process.ExitCode -ne 0) { throw "$File exited with $($process.ExitCode)" }
}
function Test-InstalledApp {
    param([string]$Phase)
    $report = Join-Path $reports "$Phase-smoke.json"
    Run-Process (Join-Path $installDir 'IslandInlineWatcher.exe') @('--smoke-test', ('"' + $report + '"'))
    if (-not (Test-Path $report)) { throw 'The packaged executable did not create a report.' }
    $result = Get-Content $report -Raw | ConvertFrom-Json
    if (-not $result.ok) { throw 'The packaged-app smoke test failed.' }
}
try {
    $log = Join-Path $reports 'install.log'
    Run-Process $installer[0].FullName @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                                        ('/DIR="' + $installDir + '"'), ('/LOG="' + $log + '"'))
    $exe = Join-Path $installDir 'IslandInlineWatcher.exe'
    if (-not (Test-Path $exe)) { throw 'Installation did not create the executable.' }
    Test-InstalledApp 'install'
    # A regular launch also must stay open, without Python or uv in its directory.
    $gui = Start-Process -FilePath $exe -PassThru
    try {
        if ($gui.WaitForExit(3000)) { throw 'The installed GUI exited unexpectedly.' }
        $null = $gui.CloseMainWindow()
        if (-not $gui.WaitForExit(10000)) { throw 'The GUI did not close cleanly.' }
        if ($gui.ExitCode -ne 0) { throw 'The GUI exited with an error.' }
    } finally { if (-not $gui.HasExited) { $gui.Kill() } }
    $desktopLink = Join-Path ([Environment]::GetFolderPath('Desktop')) '島語 inline 空位監看.lnk'
    $startLink = Join-Path ([Environment]::GetFolderPath('Programs')) '島語 inline 空位監看.lnk'
    if (-not (Test-Path $desktopLink) -or -not (Test-Path $startLink)) { throw 'An application shortcut is missing.' }
    $uninstallKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{11C4C643-00CD-4D81-A998-6C75B0DC580D}_is1'
    if (-not (Test-Path $uninstallKey)) { throw 'The uninstaller was not registered for the current user.' }
    New-Item -ItemType Directory -Force (Join-Path $dataDir '.browser-native-profile') | Out-Null
    $settings = Join-Path $dataDir '.env'
    $profileMarker = Join-Path $dataDir '.browser-native-profile\preserve-test.txt'
    Set-Content -Path $settings -Value 'ISLAND_TEST_MARKER=preserved' -Encoding utf8
    Set-Content -Path $profileMarker -Value 'preserved' -Encoding utf8
    $before = (Get-FileHash $settings).Hash
    $log = Join-Path $reports 'update.log'
    Run-Process $installer[0].FullName @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/SP-',
                                        ('/DIR="' + $installDir + '"'), ('/LOG="' + $log + '"'))
    if ((Get-FileHash $settings).Hash -ne $before -or -not (Test-Path $profileMarker)) { throw 'Updating removed user data.' }
    Test-InstalledApp 'update'
    $log = Join-Path $reports 'uninstall.log'
    Run-Process (Join-Path $installDir 'unins000.exe') @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART',
                                                        ('/LOG="' + $log + '"'))
    if (Test-Path $exe) { throw 'Uninstall left the application executable behind.' }
    if (Test-Path $desktopLink) { throw 'Uninstall left the desktop shortcut behind.' }
    if (Test-Path $startLink) { throw 'Uninstall left the Start menu shortcut behind.' }
    if (Test-Path $uninstallKey) { throw 'Uninstall left its registration behind.' }
    if ((Get-FileHash $settings).Hash -ne $before -or -not (Test-Path $profileMarker)) { throw 'Uninstall removed user data.' }
    @{ok=$true; checks=@('install','gui','shortcuts','uninstall_registration','reinstall','uninstall','data_retention')} |
        ConvertTo-Json | Set-Content (Join-Path $reports 'installer.json') -Encoding utf8
    # Report the actual signing state; unsigned builds are expected without a certificate.
    Get-AuthenticodeSignature $installer[0].FullName | Select-Object Status, StatusMessage |
        ConvertTo-Json | Set-Content (Join-Path $reports 'signature.json') -Encoding utf8
} finally {
    if (Test-Path $dataDir) { Remove-Item -Recurse -Force $dataDir }
    if (Test-Path $installDir) { Remove-Item -Recurse -Force $installDir }
}
