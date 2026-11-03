@echo off
title Sistema de Agendamento Medico - Sistemas Operacionais
cd /d "%~dp0"

echo ======================================================================
echo   Iniciando Sistema de Agendamento Medico (Sistemas Operacionais)
echo ======================================================================
echo.

if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" run.py
) else (
    echo [ERRO] Ambiente virtual .venv nao encontrado!
    echo Execute: py -m venv .venv ^&^& .\.venv\Scripts\pip.exe install -r requirements.txt
    pause
)

