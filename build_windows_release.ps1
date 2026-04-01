param(
    [string]$PythonExe = "python",
    [string]$Version = "0.0.0",
    [string]$OutputRoot = "output\release"
)

$ErrorActionPreference = "Stop"

function Get-NormalizedFileVersion {
    param([string]$RawVersion)

    $parts = $RawVersion -split "[^0-9]+"
    $numbers = @()
    foreach ($part in $parts) {
        if ($part -match "^\d+$") {
            $numbers += [int]$part
        }
    }

    while ($numbers.Count -lt 4) {
        $numbers += 0
    }

    return ($numbers[0..3] -join ".")
}

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

$distPath = Join-Path $repoRoot $OutputRoot
$bundleName = "WhatsAppStatusSaver"
$bundleDir = Join-Path $distPath $bundleName
$zipPath = Join-Path $distPath "WhatsAppStatusSaver-windows-x64.zip"
$fileVersion = Get-NormalizedFileVersion -RawVersion $Version
$pythonDir = Split-Path -Parent (Resolve-Path $PythonExe)
$fletExe = Join-Path $pythonDir "flet.exe"

if (-not (Test-Path $distPath)) {
    New-Item -ItemType Directory -Path $distPath | Out-Null
}

if (Test-Path $bundleDir) {
    Remove-Item -Recurse -Force $bundleDir
}

if (Test-Path $zipPath) {
    Remove-Item -Force $zipPath
}

& $PythonExe -m pip install --upgrade pip
& $PythonExe -m pip install -r requirements.txt
& $PythonExe -m pip install pyinstaller

if (-not (Test-Path $fletExe)) {
    throw "Could not find flet executable next to Python: $fletExe"
}

& $fletExe pack `
    main.py `
    -n $bundleName `
    -D `
    --yes `
    --icon assets/icon.ico `
    --distpath $distPath `
    --product-name "WhatsApp Status Saver" `
    --file-description "Browse and save WhatsApp Desktop statuses" `
    --product-version $Version `
    --file-version $fileVersion `
    --company-name "Alvin Murimi"

if (-not (Test-Path $bundleDir)) {
    throw "Expected bundle directory was not created: $bundleDir"
}

Compress-Archive -Path (Join-Path $bundleDir "*") -DestinationPath $zipPath -Force

Write-Output "Bundle directory: $bundleDir"
Write-Output "Release zip: $zipPath"
