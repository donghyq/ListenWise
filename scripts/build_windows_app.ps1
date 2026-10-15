# Build the Shengxi (LawFlow) desktop app for Windows.
# Prerequisites:
#   - Python 3.10+ on PATH
#   - FFmpeg + ffprobe on PATH (runtime requirement for MP3 / M4B)
# Run:  powershell -ExecutionPolicy Bypass -File scripts\build_windows_app.ps1
$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $RootDir

$Version = (python -c "from app.version import __version__; print(__version__)").Trim()
if (-not $Version) { throw "Cannot read app.version.__version__" }

Write-Host "Installing dependencies..."
python -m pip install -r requirements.txt pyinstaller

Write-Host "Building version $Version ..."
pyinstaller `
  --noconfirm `
  --windowed `
  --name Shengxi `
  --paths "$RootDir" `
  --add-data "app/static;app/static" `
  --collect-data setuptools `
  --collect-all mcp `
  --collect-all edge_tts `
  --collect-all webview `
  --hidden-import qrcode `
  --hidden-import "qrcode.image.svg" `
  scripts/lawflow_desktop.py

$Dist = Join-Path $RootDir "dist\Shengxi"
if (-not (Test-Path $Dist)) { throw "Build output not found: $Dist" }

$ZipPath = Join-Path $RootDir ("dist\Shengxi-" + $Version + "-win64.zip")
if (Test-Path $ZipPath) { Remove-Item $ZipPath }
Compress-Archive -Path (Join-Path $Dist "*") -DestinationPath $ZipPath

Write-Host "Built folder: $Dist"
Write-Host "Distributable zip: $ZipPath"
Write-Host "Note: the end user still needs FFmpeg on PATH for MP3/M4B export."
