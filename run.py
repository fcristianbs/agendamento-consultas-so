# run.py
"""
Script Principal de Inicialização do Sistema de Agendamento (Sistemas Operacionais)
Inicia o servidor web e demonstra os parâmetros de ambiente do SO.
"""

import uvicorn
from src.config.os_adapter import os_adapter

def main():
    telemetry = os_adapter.get_telemetry()
    print("=" * 70)
    print("  SISTEMA DE AGENDAMENTO DE CONSULTAS MÉDICAS - CRUD")
    print("  TRABALHO PRÁTICO DE SISTEMAS OPERACIONAIS")
    print("=" * 70)
    print(f"  * Sistema Operacional:  {telemetry['os_name']} {telemetry['os_release']} ({telemetry['architecture']})")
    print(f"  * Separador de Caminho: '{telemetry['path_separator']}'")
    print(f"  * Codificação Padrão:   {os_adapter.default_encoding.upper()}")
    print(f"  * Núcleos de CPU (SO):  {telemetry['cpu_cores']}")
    print(f"  * Usuário do Sistema:   {telemetry['current_user']}")
    print("=" * 70)
    print("  Acesse a aplicação no navegador em: http://127.0.0.1:8000")
    print("  Pressione CTRL+C para encerrar o processo.")
    print("=" * 70)

    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=False, log_level="info")

if __name__ == "__main__":
    main()
