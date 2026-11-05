$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$python = "py -3.13"
try {
    & py -3.13 --version
} catch {
    $python = "python"
}

Write-Host "使用 Python: $python"
Invoke-Expression "$python -m pip install --upgrade pip"
Invoke-Expression "$python -m pip install -r requirements.txt"

Write-Host ""
Write-Host "如需 CUDA 加速，可在 NVIDIA 驱动正常时额外执行："
Write-Host "  $python -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12"
