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

$sourceDir = if (Test-Path -LiteralPath (Join-Path $PSScriptRoot "QuickTranslator.exe")) { $PSScriptRoot } else { Join-Path $PSScriptRoot "dist\QuickTranslator" }
$programsRoot = Join-Path $env:LOCALAPPDATA "Programs"
$installDir = Join-Path $programsRoot "QuickTranslator"
$stagingDir = Join-Path $programsRoot "QuickTranslator.staging"
$backupDir = Join-Path $programsRoot "QuickTranslator.previous"
$exePath = Join-Path $installDir "QuickTranslator.exe"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\QuickTranslator.lnk"
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\QuickTranslator.lnk"

function Assert-SafeProgramPath([string]$path) {
    $root = [IO.Path]::GetFullPath($programsRoot).TrimEnd('\') + '\'
    $full = [IO.Path]::GetFullPath($path)
    if (-not $full.StartsWith($root, [StringComparison]::OrdinalIgnoreCase)) { throw "Unsafe install path: $full" }
}

function Remove-SafeDirectory([string]$path) {
    Assert-SafeProgramPath $path
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
}

function Verify-Package([string]$directory) {
    $manifestPath = Join-Path $directory "manifest.json"
    if (-not (Test-Path -LiteralPath $manifestPath)) { throw "manifest.json is missing." }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.product -ne "QuickTranslator") { throw "Invalid product manifest." }
    foreach ($property in $manifest.files.PSObject.Properties) {
        $filePath = Join-Path $directory ($property.Name.Replace('/', '\'))
        if (-not (Test-Path -LiteralPath $filePath)) { throw "Package file missing: $($property.Name)" }
        $actual = Get-Sha256 $filePath
        if ($actual -ne $property.Value) { throw "Package integrity check failed: $($property.Name)" }
    }
    return $manifest.version
}

Assert-SafeProgramPath $installDir
Assert-SafeProgramPath $stagingDir
Assert-SafeProgramPath $backupDir
$version = Verify-Package $sourceDir

Get-Process -Name "QuickTranslator" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 600
Remove-SafeDirectory $stagingDir
New-Item -ItemType Directory -Path $stagingDir -Force | Out-Null
Copy-Item -Path (Join-Path $sourceDir "*") -Destination $stagingDir -Recurse -Force

$selfTest = Start-Process -FilePath (Join-Path $stagingDir "QuickTranslator.exe") -ArgumentList "--self-test" -Wait -PassThru
if ($selfTest.ExitCode -ne 0) {
    Remove-SafeDirectory $stagingDir
    throw "New version self-test failed. Existing installation was not changed."
}

Remove-SafeDirectory $backupDir
if (Test-Path -LiteralPath $installDir) { Move-Item -LiteralPath $installDir -Destination $backupDir }

try {
    Move-Item -LiteralPath $stagingDir -Destination $installDir
    $shell = New-Object -ComObject WScript.Shell
    foreach ($shortcutPath in @($startMenu, $startup)) {
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $exePath
        $shortcut.WorkingDirectory = $installDir
        $shortcut.IconLocation = "$exePath,0"
        $shortcut.Description = "Quick scientific translator"
        $shortcut.Save()
    }
    Start-Process -FilePath $exePath -WorkingDirectory $installDir
    Start-Sleep -Seconds 2
    if (-not (Get-Process -Name "QuickTranslator" -ErrorAction SilentlyContinue)) { throw "New version did not stay running." }
} catch {
    Get-Process -Name "QuickTranslator" -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-SafeDirectory $installDir
    if (Test-Path -LiteralPath $backupDir) {
        Move-Item -LiteralPath $backupDir -Destination $installDir
        Start-Process -FilePath $exePath -WorkingDirectory $installDir
    }
    throw
}

Write-Output "Installed QuickTranslator $version"
Write-Output "Previous version retained at: $backupDir"
