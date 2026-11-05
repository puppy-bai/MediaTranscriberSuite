$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$workspace = Split-Path -Parent $root
$candidate = Join-Path $workspace "release\candidate"
Set-Location $root
$python = (Get-Command python -ErrorAction Stop).Source
& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) { throw "PyInstaller is missing. Install it with: $python -m pip install pyinstaller" }
& $python -m PyInstaller --noconfirm --windowed --onedir --name MediaTranscriberSuite --paths desktop --exclude-module PyQt5 --exclude-module PyQt6 --exclude-module matplotlib --distpath $candidate --workpath (Join-Path $root "build") --add-data "desktop\assets;assets" --hidden-import yt_dlp --hidden-import faster_whisper --hidden-import openai --collect-all yt_dlp --collect-all faster_whisper --collect-all ctranslate2 desktop\app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }
Write-Host "候选包已生成：$candidate\MediaTranscriberSuite\MediaTranscriberSuite.exe"
