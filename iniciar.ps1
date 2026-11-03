# Script PowerShell para inicializacao direta
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host "  Iniciando Sistema de Agendamento Medico (Sistemas Operacionais)" -ForegroundColor Green
Write-Host "======================================================================" -ForegroundColor Cyan
Write-Host ""

$pythonExe = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

if (Test-Path $pythonExe) {
    & $pythonExe run.py
} else {
    Write-Host "[ERRO] Ambiente virtual .venv nao encontrado!" -ForegroundColor Red
    Write-Host "Execute: py -m venv .venv; .\.venv\Scripts\pip.exe install -r requirements.txt" -ForegroundColor Yellow
}

