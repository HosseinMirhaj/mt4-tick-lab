[CmdletBinding()]
param(
    [ValidateSet('install','check','build','all')]
    [string]$Action = 'check',
    [string]$DataFolder,
    [string]$InputFolder,
    [string]$OutputFolder,
    [string]$TesterFolder,
    [string]$ReferenceFxt,
    [string]$SpecCsv,
    [string]$TesterSymbol,
    [switch]$SkipFxt,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Project = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Get-Command python -ErrorAction SilentlyContinue
if (-not $Python) { throw 'Python 3 was not found. Install Python 3.11+ and reopen PowerShell.' }

function Info([string]$Message) { Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Pass([string]$Message) { Write-Host "[PASS] $Message" -ForegroundColor Green }
function Warn([string]$Message) { Write-Host "[WARN] $Message" -ForegroundColor Yellow }
function Fail([string]$Message) { throw "[FAIL] $Message" }
function Run-Python([string[]]$Arguments) {
    $pythonOutput = & $Python.Source @Arguments 2>&1
    $pythonExitCode = $LASTEXITCODE
    $pythonOutput | ForEach-Object { Write-Host $_ }
    if ($pythonExitCode -ne 0) { throw "Python command failed (exit $pythonExitCode): $($Arguments -join ' ')" }
}
function Resolve-PathRequired([string]$Value, [string]$Label) {
    if (-not $Value) { Fail "$Label is required for this action." }
    if (-not (Test-Path -LiteralPath $Value)) { Fail "$Label was not found: $Value" }
    return (Resolve-Path -LiteralPath $Value).Path
}
function Find-Mt4DataFolders {
    $roots = @(
        (Join-Path ${env:APPDATA} 'MetaQuotes\Terminal'),
        (Join-Path ${env:PROGRAMDATA} 'MetaQuotes\Terminal')
    ) | Where-Object { Test-Path $_ }
    $folders = foreach ($root in $roots) {
        Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'MQL4') } |
            Select-Object -ExpandProperty FullName
    }
    @($folders | Sort-Object -Unique)
}
function Select-Mt4DataFolder {
    if ($DataFolder) { return Resolve-PathRequired $DataFolder 'DataFolder' }
    $found = @(Find-Mt4DataFolders)
    if ($found.Count -eq 0) { Fail 'No MT4 Data Folder was found. Pass -DataFolder explicitly.' }
    if ($found.Count -eq 1) { return $found[0] }
    Write-Host 'Detected MT4 Data Folders:'
    for ($i = 0; $i -lt $found.Count; $i++) { Write-Host "[$($i + 1)] $($found[$i])" }
    $choice = Read-Host 'Choose a Data Folder number'
    $number = 0
    if (-not [int]::TryParse($choice, [ref]$number) -or $number -lt 1 -or $number -gt $found.Count) { Fail 'Invalid Data Folder selection.' }
    return $found[$number - 1]
}
function Confirm-LiveSafety([string]$Folder) {
    $terminals = @(Get-CimInstance Win32_Process -Filter "name = 'terminal.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like "$(Split-Path $Folder -Parent)*" })
    if ($terminals.Count -gt 0) {
        Warn "A terminal process appears to use this area. The script will not write history/tester files."
    }
}
function Install-Mql4Files([string]$Folder) {
    $indicatorDir = Join-Path $Folder 'MQL4\Indicators'
    $scriptDir = Join-Path $Folder 'MQL4\Scripts'
    New-Item -ItemType Directory -Path $indicatorDir,$scriptDir -Force | Out-Null
    Copy-Item (Join-Path $Project 'MT4_TickCollector.mq4') (Join-Path $indicatorDir 'MT4_TickCollector.mq4') -Force
    Copy-Item (Join-Path $Project 'MT4_SymbolSpecSnapshot.mq4') (Join-Path $scriptDir 'MT4_SymbolSpecSnapshot.mq4') -Force
    Pass "MQL4 source files installed in $Folder"
    Warn 'Open MetaEditor and Compile both files. This tool does not silently alter or replace EX4 files.'
}
function Get-LatestTickFiles([string]$Folder) {
    $base = Join-Path $Folder 'MQL4\Files\MT4TickLab'
    if (-not (Test-Path $base)) { Fail "Collector output was not found: $base" }
    @(Get-ChildItem $base -Recurse -File -Filter 'ticks_*.csv' |
        Where-Object { $_.Length -gt 200 } | Sort-Object LastWriteTime -Descending)
}
function Check-Growth([string]$Folder) {
    $file = @(Get-LatestTickFiles $Folder) | Select-Object -First 1
    if (-not $file) { Fail 'No non-empty tick CSV found. Attach the Collector to a live chart first.' }
    $before = $file.Length
    Info "Watching $($file.FullName) for 60 seconds..."
    Start-Sleep -Seconds 60
    $after = (Get-Item -LiteralPath $file.FullName).Length
    if ($after -le $before) { Fail "CSV did not grow (before=$before, after=$after). Check chart, connection and compiled indicator." }
    Pass "CSV is growing (before=$before, after=$after)"
    Get-Content -LiteralPath $file.FullName -Tail 2
}
function Build-Data([string]$SourceFolder) {
    $inputPath = Resolve-PathRequired $SourceFolder 'InputFolder'
    $output = if ($OutputFolder) { $OutputFolder } else { Join-Path $Project 'output' }
    $output = [IO.Path]::GetFullPath($output)
    New-Item -ItemType Directory -Path $output -Force | Out-Null
    $data = Join-Path $output 'data'
    $datasets = Join-Path $output 'datasets'
    $bars = Join-Path $output 'bars'
    $hst = Join-Path $output 'hst'
    $reportDir = Join-Path $output 'reports'
    # These folders are generated by this workflow. Clear them so an old empty
    # session or old broker cannot make a fresh build fail.
    foreach ($generated in @($data, $datasets, $bars, $hst, $reportDir)) {
        if (Test-Path $generated) { Remove-Item $generated -Recurse -Force }
    }
    New-Item -ItemType Directory -Path $data -Force | Out-Null
    $metadataFiles = @(Get-ChildItem $inputPath -Recurse -File -Filter 'metadata_*.csv')
    $tickFiles = @(Get-ChildItem $inputPath -Recurse -File -Filter 'ticks_*.csv' | Where-Object { $_.Length -gt 200 })
    if ($metadataFiles.Count -eq 0 -or $tickFiles.Count -eq 0) {
        Fail 'InputFolder must contain at least one non-empty metadata_*.csv and one non-empty ticks_*.csv file.'
    }
    $sessions = @{}
    foreach ($tickFile in $tickFiles) {
        $rows = @(Get-Content -LiteralPath $tickFile.FullName -TotalCount 2)
        if ($rows.Count -gt 1 -and $rows[1] -match '^[^,]*,([^,]+),') {
            $sessions[$Matches[1]] = $true
        }
    }
    if ($sessions.Count -eq 0) { Fail 'No tick rows found in InputFolder.' }
    $selectedMetadata = @($metadataFiles | Where-Object {
        $text = Get-Content -LiteralPath $_.FullName -Raw
        $session = [regex]::Match($text, '(?m)^session_id,([^\r\n]+)').Groups[1].Value
        $sessions.ContainsKey($session)
    })
    $skipped = $metadataFiles.Count - $selectedMetadata.Count
    if ($skipped -gt 0) { Warn "Skipping $skipped metadata file(s) with no matching non-empty tick CSV." }
    $selectedMetadata | ForEach-Object { Copy-Item $_.FullName $data -Force }
    $tickFiles | ForEach-Object { Copy-Item $_.FullName $data -Force }
    $reportDir = Join-Path $output 'reports'
    New-Item -ItemType Directory -Path $reportDir -Force | Out-Null
    Run-Python @((Join-Path $Project 'validate_ticks.py'), $data, '--json', (Join-Path $reportDir 'validation.json'))
    Run-Python @((Join-Path $Project 'build_dataset.py'), $data, '--output', $datasets)
    Run-Python @((Join-Path $Project 'build_bars.py'), $datasets, '--output', $bars)
    $hstArgs = @((Join-Path $Project 'export_hst.py'), $bars, '--output', $hst)
    if ($TesterSymbol) { $hstArgs += @('--symbol', $TesterSymbol) }
    Run-Python $hstArgs
    Pass "Data, datasets, M1 bars and HST are ready in $output"
    return @{ Root = $output; Datasets = $datasets; Bars = $bars; Hst = $hst }
}
function Build-Fxt([hashtable]$Build) {
    if ($SkipFxt) { Warn 'FXT build skipped by -SkipFxt.'; return }
    if (-not $ReferenceFxt -or -not $SpecCsv) {
        Warn 'ReferenceFxt and SpecCsv were not supplied; HST was built, FXT was skipped safely.'
        Warn 'For FXT, rerun build with a same-Build/same-symbol reference FXT and the same-server symbol spec.'
        return
    }
    $reference = Resolve-PathRequired $ReferenceFxt 'ReferenceFxt'
    $spec = Resolve-PathRequired $SpecCsv 'SpecCsv'
    $fxt = Join-Path $Build.Root 'fxt'
    $args = @((Join-Path $Project 'build_fxt.py'), $Build.Datasets, '--reference', $reference, '--spec', $spec, '--output', $fxt)
    if ($TesterSymbol) { $args += @('--symbol', $TesterSymbol) }
    Run-Python $args
    Pass "FXT is ready in $fxt"
}

switch ($Action) {
    'install' {
        $folder = Select-Mt4DataFolder
        Confirm-LiveSafety $folder
        Install-Mql4Files $folder
        Info 'Next: compile in MetaEditor, attach the indicator to the target chart, then run: .\mt4-tick-lab.ps1 check -DataFolder "..."'
    }
    'check' {
        $folder = Select-Mt4DataFolder
        Check-Growth $folder
    }
    'build' {
        $build = Build-Data $InputFolder
        Build-Fxt $build
    }
    'all' {
        $folder = Select-Mt4DataFolder
        Confirm-LiveSafety $folder
        Install-Mql4Files $folder
        Warn 'Pause here: compile/attach Collector and run the 60-second growth check before collecting for hours.'
        if (-not $Force) { Read-Host 'Press Enter after CSV has grown and collection is complete, or Ctrl+C to stop' }
        $sourceInput = if ($InputFolder) { $InputFolder } else { Join-Path $folder 'MQL4\Files\MT4TickLab' }
        $build = Build-Data $sourceInput
        Build-Fxt $build
    }
}
