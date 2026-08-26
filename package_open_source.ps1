$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$versionMatch = [regex]::Match((Get-Content -Raw (Join-Path $root "app.py")), 'VERSION\s*=\s*"([^"]+)"')
if (-not $versionMatch.Success) { throw "VERSION not found in app.py." }
$version = $versionMatch.Groups[1].Value
$portable = Join-Path $root "dist\QuickTranslator"
if (-not (Test-Path (Join-Path $portable "QuickTranslator.exe"))) {
    throw "Portable build is missing. Run build_portable.bat first."
}
$portableManifest = Get-Content -Raw (Join-Path $portable "manifest.json") | ConvertFrom-Json
if ($portableManifest.version -ne $version) {
    throw "Portable version does not match source version. Rebuild first."
}

$stage = Join-Path $root "release\QuickTranslator-$version-open-source"
$zip = Join-Path $root "QuickTranslator-$version-open-source.zip"
if (Test-Path $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
New-Item -ItemType Directory -Path (Join-Path $stage "source") -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $stage "portable") -Force | Out-Null

$sourceFiles = @(
    ".gitignore", "app.py", "test_app.py", "requirements.txt", "README.md",
    "LICENSE", "NOTICE.md", "SECURITY.md", "CHANGELOG.md", "OPEN_SOURCE_RELEASE.md",
    ".github\workflows\tests.yml", "run.bat", "build_portable.bat",
    "package_release.ps1", "package_open_source.ps1", "install.bat",
    "install.ps1", "uninstall.ps1", "assets\app_icon.png"
)
foreach ($relative in $sourceFiles) {
    $from = Join-Path $root $relative
    if (-not (Test-Path -LiteralPath $from)) { throw "Required source file missing: $relative" }
    $destination = Join-Path $stage "source\$relative"
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $from -Destination $destination -Force
}
Copy-Item -LiteralPath (Join-Path $root "LICENSE") -Destination (Join-Path $stage "LICENSE")
Copy-Item -LiteralPath (Join-Path $root "OPEN_SOURCE_RELEASE.md") -Destination (Join-Path $stage "README.md")
Copy-Item -LiteralPath $portable -Destination (Join-Path $stage "portable") -Recurse

$forbiddenNames = Get-ChildItem -LiteralPath $stage -Recurse -File | Where-Object {
    $_.Name -match '^(config\.json|translation_memory\.json|\.env(?:\..*)?)$'
}
if ($forbiddenNames) { throw "Private runtime configuration was included." }

# Reject common real credential assignments while allowing documented empty values
# and the explicit unit-test dummy value.
$textFiles = Get-ChildItem -LiteralPath (Join-Path $stage "source") -Recurse -File |
    Where-Object { $_.Extension -in ".py", ".ps1", ".bat", ".md", ".txt", ".json" }
foreach ($file in $textFiles) {
    $content = Get-Content -Raw -LiteralPath $file.FullName
    if ($content -match '(?i)(api[_ -]?key|authorization)\s*[=:]\s*["''](?!test-key["''])[^"'']{12,}["'']') {
        throw "Possible embedded credential in $($file.FullName)"
    }
}

Compress-Archive -LiteralPath $stage -DestinationPath $zip -CompressionLevel Optimal
Write-Output "Open-source release: $zip"
