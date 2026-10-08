[CmdletBinding()]
param(
    [string]$DataFolder,
    [Parameter(Mandatory=$true)][string]$OfflineFolder,
    [string]$InputFolder,
    [string]$SourceSymbol,
    [string]$TesterSymbol,
    [string]$ReferenceFxt,
    [string]$SpecCsv,
    [string]$OutputRoot = "C:\MT4-Tick-Lab\backtests",
    [switch]$LaunchOffline
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw 'Python 3 was not found.' }

function Info([string]$m) { Write-Host "[INFO] $m" -ForegroundColor Cyan }
function Pass([string]$m) { Write-Host "[PASS] $m" -ForegroundColor Green }
function Warn([string]$m) { Write-Host "[WARN] $m" -ForegroundColor Yellow }
function Fail([string]$m) { throw "[FAIL] $m" }
function Run-Python([string[]]$a) {
    $o = & $Python.Source @a 2>&1
    $code = $LASTEXITCODE
    $o | ForEach-Object { Write-Host $_ }
    if ($code -ne 0) { throw "Python failed ($code): $($a -join ' ')" }
}
function Required([string]$p, [string]$label) {
    if (-not $p -or -not (Test-Path -LiteralPath $p)) { Fail "$label not found: $p" }
    (Resolve-Path -LiteralPath $p).Path
}
function Find-CompatibleSessions([string]$root, [string]$wantedSymbol) {
    $tickFiles = @(Get-ChildItem $root -Recurse -File -Filter 'ticks_*.csv' |
        Where-Object { $_.Length -gt 200 } | Sort-Object LastWriteTime -Descending)
    if ($tickFiles.Count -eq 0) { Fail 'No non-empty ticks_*.csv was found.' }

    $metadataFiles = @(Get-ChildItem $root -Recurse -File -Filter 'metadata_*.csv')
    $candidates = @()
    foreach ($tick in $tickFiles) {
        $head = @(Get-Content -LiteralPath $tick.FullName -TotalCount 2)
        if ($head.Count -lt 2) { continue }
        $parts = $head[1].Split(',')
        if ($parts.Count -lt 2) { continue }
        $sessionId = $parts[1]
        $meta = $metadataFiles |
            Where-Object { (Get-Content -LiteralPath $_.FullName -Raw) -match "(?m)^session_id,$([regex]::Escape($sessionId))\s*$" } |
            Select-Object -First 1
        if (-not $meta) { continue }
        $values = @{}
        Import-Csv -LiteralPath $meta.FullName | ForEach-Object { $values[$_.key] = $_.value }
        if (-not $values['broker_company'] -or -not $values['broker_server'] -or
            -not $values['terminal_id'] -or -not $values['symbol'] -or
            ($wantedSymbol -and $values['symbol'] -ne $wantedSymbol)) {
            Warn "Skipping session with incomplete or unwanted identity: $sessionId"
            continue
        }
        $candidates += [pscustomobject]@{ Tick = $tick; Metadata = $meta; Session = $sessionId; Values = $values }
    }
    if ($candidates.Count -eq 0) { Fail 'No non-empty session with complete broker identity was found.' }

    $anchor = $candidates[0].Values
    $selected = @($candidates | Where-Object {
        $_.Values['broker_company'] -eq $anchor['broker_company'] -and
        $_.Values['broker_server'] -eq $anchor['broker_server'] -and
        $_.Values['terminal_id'] -eq $anchor['terminal_id'] -and
        $_.Values['symbol'] -eq $anchor['symbol'] -and
        $_.Values['terminal_build'] -eq $anchor['terminal_build']
    })
    if ($selected.Count -gt 1) {
        Info "Combining $($selected.Count) compatible sessions for one backtest package."
    }
    return $selected
}

function DateRange([string]$barsRoot) {
    $dates = @(Get-ChildItem $barsRoot -Recurse -File -Filter 'bars_M1_*.csv' |
        ForEach-Object { if ($_.Name -match 'bars_M1_(\d{8})\.csv') { $Matches[1] } } |
        Sort-Object -Unique)
    if ($dates.Count -eq 0) { Fail 'No generated M1 bar files found.' }
    @{ Start = $dates[0]; End = $dates[$dates.Count - 1] }
}

$offline = Required $OfflineFolder 'OfflineFolder'
if (-not (Test-Path (Join-Path $offline 'terminal.exe'))) { Fail 'OfflineFolder must contain terminal.exe.' }
if (-not $InputFolder) {
    $live = Required $DataFolder 'DataFolder'
    $InputFolder = Join-Path $live 'MQL4\Files\MT4TickLab'
}
$input = Required $InputFolder 'InputFolder'
if (-not $SourceSymbol) { $SourceSymbol = 'XAUUSD' }
if (-not $TesterSymbol) { $TesterSymbol = $SourceSymbol }
New-Item -ItemType Directory -Path $OutputRoot -Force | Out-Null

$sessions = @(Find-CompatibleSessions $input $SourceSymbol)
$bundleId = $sessions[0].Session + "_bundle_" + $sessions.Count
$stage = Join-Path $OutputRoot ("staging_" + $bundleId)
if (Test-Path $stage) { Remove-Item $stage -Recurse -Force }
New-Item -ItemType Directory -Path $stage | Out-Null
foreach ($session in $sessions) {
    Copy-Item $session.Tick.FullName (Join-Path $stage $session.Tick.Name)
    Copy-Item $session.Metadata.FullName (Join-Path $stage $session.Metadata.Name)
}
Info "Selected $($sessions.Count) compatible session(s)"
Info "Tick files: $($sessions.Count) (one or more per session)"

$report = Join-Path $OutputRoot 'reports'
New-Item -ItemType Directory -Path $report -Force | Out-Null
Run-Python @((Join-Path $Project 'validate_ticks.py'), $stage, '--json', (Join-Path $report "$bundleId.validation.json"))

$datasets = Join-Path $stage 'datasets'
$bars = Join-Path $stage 'bars'
$hst = Join-Path $stage 'hst'
$fxt = Join-Path $stage 'fxt'
Run-Python @((Join-Path $Project 'build_dataset.py'), $stage, '--output', $datasets)
Run-Python @((Join-Path $Project 'build_bars.py'), $datasets, '--output', $bars)
$hstArgs = @((Join-Path $Project 'export_hst.py'), $bars, '--output', $hst, '--symbol', $TesterSymbol)
Run-Python $hstArgs
$range = DateRange $bars
$label = "${TesterSymbol}_M1_$($range.Start)_$($range.End)"
$final = Join-Path $OutputRoot $label
if (Test-Path $final) { Remove-Item $final -Recurse -Force }
Move-Item $stage $final
$stage = $final
$bars = Join-Path $stage 'bars'
$hst = Join-Path $stage 'hst'
$fxtReady = $false
if ($ReferenceFxt -and $SpecCsv) {
    $reference = Required $ReferenceFxt 'ReferenceFxt'
    $spec = Required $SpecCsv 'SpecCsv'
    $fxt = Join-Path $stage 'fxt'
    $fxtArgs = @((Join-Path $Project 'build_fxt.py'), (Join-Path $stage 'datasets'), '--reference', $reference, '--spec', $spec, '--output', $fxt, '--symbol', $TesterSymbol)
    Run-Python $fxtArgs
    $fxtReady = $true
} else {
    Warn 'ReferenceFxt and SpecCsv were not supplied. HST is ready; FXT was not guessed.'
}

$running = @(Get-CimInstance Win32_Process -Filter "name = 'terminal.exe'" |
    Where-Object { $_.ExecutablePath -and $_.ExecutablePath -ieq (Join-Path $offline 'terminal.exe') })
if ($running.Count -gt 0) { Fail 'Close offline MT4 before installing generated files. Live MT4 is not touched.' }
$history = Join-Path $offline 'history'
$hstFiles = @(Get-ChildItem $hst -Recurse -File -Filter '*.hst')
$historyDirs = @(Get-ChildItem $history -Directory)
foreach ($dir in $historyDirs) {
    foreach ($file in $hstFiles) { Copy-Item $file.FullName (Join-Path $dir.FullName $file.Name) -Force }
}
if ($fxtReady) {
    $fxtFile = Get-ChildItem (Join-Path $stage 'fxt') -File -Filter '*.fxt' | Select-Object -First 1
    $testerHistory = Join-Path $offline 'tester\history'
    New-Item -ItemType Directory -Path $testerHistory -Force | Out-Null
    Copy-Item $fxtFile.FullName (Join-Path $testerHistory $fxtFile.Name) -Force
    attrib -R (Join-Path $testerHistory $fxtFile.Name)
}
$manifest = [ordered]@{
    status = if ($fxtReady) { 'READY_HST_FXT' } else { 'READY_HST_ONLY' }
    tester_symbol = $TesterSymbol
    source_symbol = $SourceSymbol
    date_start = $range.Start
    date_end = $range.End
    session_ids = @($sessions | ForEach-Object { $_.Session })
    output = $stage
    offline_terminal = $offline
    hst_files = @($hstFiles | ForEach-Object { $_.Name })
    fxt_installed = $fxtReady
    note = 'Generated files were installed only in the offline terminal.'
}
$manifest | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $stage 'BACKTEST_READY.json') -Encoding UTF8
Pass "Backtest package ready: $label"
Pass "HST files installed in offline MT4: $($historyDirs.Count) history folder(s)"
if ($fxtReady) { Pass 'FXT installed in offline tester\history.' } else { Warn 'Only HST was installed; Every tick FXT test is not ready.' }
if ($LaunchOffline) { Start-Process (Join-Path $offline 'terminal.exe') -ArgumentList '/portable'; Pass 'Offline MT4 launched.' }
