param(
    [string]$Name = "ReleasePublisher"
)

$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$python = (Get-Command python -ErrorAction Stop).Source

Write-Host "Using Python: $python"

& $python -c "import PyInstaller" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing PyInstaller..."
    & $python -m pip install --upgrade pyinstaller
    if ($LASTEXITCODE -ne 0) { throw "Failed to install PyInstaller" }
}

$dist = Join-Path $here "dist"
$work = Join-Path $here "build"

& $python -m PyInstaller --noconfirm --clean --windowed --onefile `
    --name $Name `
    --distpath $dist `
    --workpath $work `
    --specpath $work `
    (Join-Path $here "release_publisher.py")

if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed with exit code $LASTEXITCODE" }

$exe = Join-Path $dist "$Name.exe"
if (-not (Test-Path $exe)) { throw "Build output missing: $exe" }
$size = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Built: $exe ($size MB)"
