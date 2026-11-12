[CmdletBinding()]
param(
    [string]$InnoSetupCompiler,
    [string]$CertificateThumbprint,
    [string]$SignToolPath,
    [string]$TimestampUrl = 'http://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
if (-not [Environment]::Is64BitOperatingSystem -or $env:OS -ne 'Windows_NT') {
    throw 'Build this installer on 64-bit Windows.'
}
function Invoke-Checked {
    param([string]$Command, [string[]]$Arguments)
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed with exit code $LASTEXITCODE" }
}
Push-Location $PSScriptRoot
try {
    $uv = (Get-Command uv -ErrorAction Stop).Source
    if (-not $InnoSetupCompiler) {
        $candidate = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if ($candidate) { $InnoSetupCompiler = $candidate.Source }
        else {
            $InnoSetupCompiler = @(
                "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
                "$env:ProgramFiles\Inno Setup 7\ISCC.exe",
                "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe",
                "$env:LOCALAPPDATA\Programs\Inno Setup 7\ISCC.exe"
            ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        }
    }
    if (-not $InnoSetupCompiler -or -not (Test-Path $InnoSetupCompiler)) {
        throw 'Install Inno Setup 6/7, or specify -InnoSetupCompiler with the full ISCC.exe path.'
    }
    Invoke-Checked $uv @('sync', '--locked', '--group', 'build', '--python', '3.12')
    $python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    $version = & $python -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])"
    if ($LASTEXITCODE -ne 0 -or $version -notmatch '^\d+(\.\d+){0,3}$') { throw 'Invalid app version.' }
    $versionParts = @($version.Split('.') | ForEach-Object { [int]$_ })
    while ($versionParts.Count -lt 4) { $versionParts += 0 }
    if (@($versionParts | Where-Object { $_ -gt 65535 }).Count) { throw 'Version component exceeds 65535.' }
    $fileVersion = $versionParts -join '.'
    Invoke-Checked $python @('-m', 'PyInstaller', '--clean', '--noconfirm', 'packaging/IslandInlineWatcher.spec')
    Invoke-Checked $python @('tools/check_bundle.py', 'dist/IslandInlineWatcher')
    $compilerArgs = @("/DAppVersion=$version", "/DAppFileVersion=$fileVersion")
    if ($CertificateThumbprint) {
        $CertificateThumbprint = $CertificateThumbprint -replace '\s', ''
        if ($CertificateThumbprint -notmatch '^[a-fA-F0-9]{40}$') { throw 'Invalid certificate thumbprint.' }
        if ($TimestampUrl -notmatch '^https?://[a-zA-Z0-9./:_-]+$') { throw 'Invalid timestamp URL.' }
        if (-not $SignToolPath) {
            $candidate = Get-Command signtool.exe -ErrorAction SilentlyContinue
            if ($candidate) { $SignToolPath = $candidate.Source }
            else {
                $SignToolPath = Get-ChildItem "${env:ProgramFiles(x86)}\Windows Kits\10\bin\*\x64\signtool.exe" |
                    Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
            }
        }
        if (-not $SignToolPath) { throw 'SignTool from the Windows SDK is required for signing.' }
        $signArgs = @('sign', '/sha1', $CertificateThumbprint, '/s', 'My', '/fd', 'SHA256',
                      '/tr', $TimestampUrl, '/td', 'SHA256')
        $appExe = Join-Path $PSScriptRoot 'dist\IslandInlineWatcher\IslandInlineWatcher.exe'
        Invoke-Checked $SignToolPath ($signArgs + @($appExe))
        Invoke-Checked $SignToolPath @('verify', '/pa', $appExe)
        $signCommand = '$q' + $SignToolPath + '$q sign /sha1 ' + $CertificateThumbprint +
            ' /s My /fd SHA256 /tr ' + $TimestampUrl + ' /td SHA256 $f'
        $compilerArgs += @('/DSignEnabled', "/Sislandsign=$signCommand")
    }
    $compilerArgs += 'packaging/installer.iss'
    Invoke-Checked $InnoSetupCompiler $compilerArgs
    if ($CertificateThumbprint) {
        $setupExe = Join-Path $PSScriptRoot "release\island-inline-watcher-v$version-windows-x64-setup.exe"
        Invoke-Checked $SignToolPath @('verify', '/pa', $setupExe)
    }
    Invoke-Checked $python @('tools/package_source.py')
    Write-Host 'Build complete. See release/ for the installer, source ZIP, and SHA256SUMS.txt.'
} finally { Pop-Location }
