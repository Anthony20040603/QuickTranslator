$ErrorActionPreference = "SilentlyContinue"

$installDir = Join-Path $env:LOCALAPPDATA "Programs\QuickTranslator"
$startMenu = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\QuickTranslator.lnk"
$startup = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\QuickTranslator.lnk"

Get-Process -Name "QuickTranslator" -ErrorAction SilentlyContinue | Stop-Process -Force
Remove-Item -LiteralPath $startMenu -Force
Remove-Item -LiteralPath $startup -Force
Start-Sleep -Milliseconds 500
Remove-Item -LiteralPath $installDir -Recurse -Force

Write-Output "QuickTranslator uninstalled. User settings and translation memory were preserved."
