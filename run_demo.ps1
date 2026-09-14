# run_demo.ps1 -- one-click toolchain-free demo on Windows.
#
#   Right-click > Run with PowerShell, or:  powershell -File run_demo.ps1
#
# Uses the Python launcher `py` if present, else `python`. Needs no EDA tools.

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$py = "py"
if (-not (Get-Command $py -ErrorAction SilentlyContinue)) { $py = "python" }

Write-Host "== GenRTL toolchain-free demo ==" -ForegroundColor Cyan
& $py demo.py

Write-Host ""
Write-Host "== Pure-Python tests ==" -ForegroundColor Cyan
& $py tests/run_all.py --fast

Write-Host ""
Write-Host "Try it on your own RTL:" -ForegroundColor Green
Write-Host "    py -m genrtl suggest path\to\your_design.sv"
