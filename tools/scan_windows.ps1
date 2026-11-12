# Scan final installer/source and the unpacked runtime when Defender is available.
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$reports = Join-Path $root 'build\validation'
New-Item -ItemType Directory -Force $reports | Out-Null
$report = Join-Path $reports 'defender.json'
try {
    $status = Get-MpComputerStatus -ErrorAction Stop
    if (-not $status.AntivirusEnabled -or -not $status.AMServiceEnabled) {
        throw 'Microsoft Defender Antivirus is not active on this runner.'
    }
    $scanner = Get-ChildItem "$env:ProgramData\Microsoft\Windows Defender\Platform\*\MpCmdRun.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1 -ExpandProperty FullName
    if (-not $scanner) { $scanner = "$env:ProgramFiles\Windows Defender\MpCmdRun.exe" }
    if (-not (Test-Path $scanner)) { throw 'MpCmdRun.exe is not installed.' }
} catch {
    @{status='unavailable'; reason=$_.Exception.Message} | ConvertTo-Json | Set-Content $report -Encoding utf8
    Write-Warning 'Defender scanning is unavailable; see defender.json. This is not a clean scan result.'
    return
}
$result = @{status='running'; signature_version=$status.AntivirusSignatureVersion;
            signature_updated=$status.AntivirusSignatureLastUpdated; scans=@()}
foreach ($relative in @('release', 'dist\IslandInlineWatcher')) {
    $folder = Join-Path $root $relative
    & $scanner -Scan -ScanType 3 -File $folder -DisableRemediation
    $code = $LASTEXITCODE
    $result.scans += @{path=$relative; exit_code=$code}
    if ($code -ne 0) {
        $result.status = 'failed'
        $result | ConvertTo-Json -Depth 4 | Set-Content $report -Encoding utf8
        throw "Defender scan failed or detected a threat (exit $code). Review the scan output before distributing."
    }
}
# Exit 0 can also mean a detection was remediated by real-time protection.
$detected = @(Get-MpThreatDetection -ErrorAction Stop | Where-Object {
    @($_.Resources | Where-Object { $_.IndexOf($root, [StringComparison]::OrdinalIgnoreCase) -ge 0 }).Count -gt 0
})
if ($detected.Count) {
    $result.status = 'detected'
    $result.threat_ids = @($detected | Select-Object -ExpandProperty ThreatID)
    $result | ConvertTo-Json -Depth 4 | Set-Content $report -Encoding utf8
    throw 'Defender recorded a detection in this build. Review it before distributing.'
}
# Ensure the final download files still exactly match the published checksums.
foreach ($line in Get-Content (Join-Path $root 'release\SHA256SUMS.txt')) {
    if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Invalid checksum manifest.' }
    $expected = $Matches[1]
    $asset = Join-Path $root ('release\' + $Matches[2])
    if ((Get-FileHash $asset -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        throw 'A release file changed or was quarantined after packaging.'
    }
}
$result.status = 'completed_no_detections'
$result | ConvertTo-Json -Depth 4 | Set-Content $report -Encoding utf8
