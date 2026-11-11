# run.py
"""
Script Principal de Inicialização do Sistema de Agendamento (Sistemas Operacionais)
Inicia o servidor web e demonstra os parâmetros de ambiente do SO.
Suporta os modos de diretório:
  --local     Usa ./data (padrão para demonstração local; equivalente a AGENDAMENTO_DATA_DIR=./data)
  --os-paths  Usa caminhos nativos do SO (%APPDATA% / XDG / Library; equivalente a AGENDAMENTO_MODE=os)
"""

import os
import sys
import argparse


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MedScheduler SO - Sistema de Agendamento de Consultas Médicas"
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--local",
        action="store_true",
        help="Força armazenamento local em ./data (padrão).",
    )
    group.add_argument(
        "--os-paths",
        action="store_true",
        help="Usa os diretórios padrão do sistema operacional (%APPDATA%, XDG, ~/Library).",
    )
    return parser.parse_args()


def main():
    args = _parse_args()
    if args.os_paths:
        os.environ.pop("AGENDAMENTO_DATA_DIR", None)
        os.environ["AGENDAMENTO_MODE"] = "os"
    elif args.local:
        os.environ["AGENDAMENTO_MODE"] = "local"
        os.environ["AGENDAMENTO_DATA_DIR"] = "./data"

    # Importação após definir variáveis de ambiente para que os_adapter resolva o modo escolhido
    import uvicorn
    from src.config.os_adapter import os_adapter

    telemetry = os_adapter.get_telemetry()
    paths = telemetry["paths"]
    print("=" * 74)
    print("  SISTEMA DE AGENDAMENTO DE CONSULTAS MÉDICAS - CRUD")
    print("  TRABALHO PRÁTICO DE SISTEMAS OPERACIONAIS")
    print("=" * 74)
    print(f"  * Sistema Operacional:  {telemetry['os_name']} {telemetry['os_release']} ({telemetry['architecture']})")
    print(f"  * Separador de Caminho: '{telemetry['path_separator']}' | EOL: {telemetry['line_ending']}")
    print(f"  * Codificação / CSV:    {telemetry['default_encoding'].upper()} / {telemetry['csv_encoding'].upper()}")
    print(f"  * FS Case-Sensitive:    {'Sim' if telemetry['filesystem_case_sensitive'] else 'Não (Case-Insensitive)'}")
    print(f"  * Núcleos de CPU (SO):  {telemetry['cpu_cores']} | Usuário: {telemetry['current_user']}")
    print("-" * 74)
    print(f"  * Modo de Diretórios ATIVO: [{paths['active_mode'].upper()}]")
    print(f"    -> Dados ativos:      {paths['active']['data_dir']}")
    print(f"    -> Relatórios ativos: {paths['active']['relatorios_dir']}")
    print(f"  * Modo Local (--local): data={paths['local_mode']['data_dir']}")
    print(f"  * Modo SO (--os-paths): data={paths['os_mode']['data_dir']}")
    print(f"                          relatorios={paths['os_mode']['relatorios_dir']}")
    print("=" * 74)
    print("  Acesse a aplicação no navegador em: http://127.0.0.1:8000")
    print("  Pressione CTRL+C para encerrar o processo.")
    print("=" * 74)

    uvicorn.run("src.web.app:app", host="127.0.0.1", port=8000, reload=False, log_level="info")


if __name__ == "__main__":
    main()
