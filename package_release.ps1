$ErrorActionPreference = "Stop"

function Get-Sha256([string]$Path) {
    $stream = [System.IO.File]::OpenRead($Path)
    try {
        $sha256 = [System.Security.Cryptography.SHA256]::Create()
        try {
            return ([System.BitConverter]::ToString($sha256.ComputeHash($stream))).Replace("-", "")
        }
        finally { $sha256.Dispose() }
    }
    finally { $stream.Dispose() }
}

$distDir = Join-Path $PSScriptRoot "dist\QuickTranslator"
$exePath = Join-Path $distDir "QuickTranslator.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "Portable build not found."
}

$source = Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot "app.py")
$match = [regex]::Match($source, 'VERSION\s*=\s*"([^"]+)"')
if (-not $match.Success) {
    throw "VERSION not found in app.py."
}
$version = $match.Groups[1].Value

Copy-Item -LiteralPath (Join-Path $PSScriptRoot "README.md") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install.ps1") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "uninstall.ps1") -Destination $distDir -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot "install.bat") -Destination $distDir -Force

$files = [ordered]@{}
Get-ChildItem -LiteralPath $distDir -Recurse -File | Where-Object { $_.Name -ne "manifest.json" } | ForEach-Object {
    $relative = $_.FullName.Substring($distDir.Length + 1).Replace("\", "/")
    $files[$relative] = Get-Sha256 $_.FullName
}
$manifest = [ordered]@{
    product = "QuickTranslator"
    version = $version
    architecture = "windows-x64"
    files = $files
}
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $distDir "manifest.json") -Encoding UTF8

$versionedZip = Join-Path $PSScriptRoot "QuickTranslator-$version-portable.zip"
$stableZip = Join-Path $PSScriptRoot "QuickTranslator-portable.zip"
Compress-Archive -LiteralPath $distDir -DestinationPath $versionedZip -CompressionLevel Optimal -Force
Copy-Item -LiteralPath $versionedZip -Destination $stableZip -Force

Write-Output "Release: $versionedZip"
