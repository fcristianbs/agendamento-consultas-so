# src/storage/storage.py
"""
Módulo de Sistema de Arquivos, I/O e Armazenamento
Demonstração de Conceitos de SO:
- Sistema de Arquivos: Persistência estruturada em arquivos JSON/CSV
- Escrita Atômica: Garantia de integridade com buffers sincronizados via os.fsync e os.replace
- Backups Automáticos: Thread daemon periódica de snapshots compactados (.zip)
- Sistema de Logging: Dispositivo de saída com registro de fuso, PID e Thread ID
"""

import os
import sys
import json
import csv
import time
import zipfile
import threading
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional

# ==========================================
# 1. SISTEMA DE LOGGING (CONCEITO DE SO: DISPOSITIVOS)
# ==========================================
class ProcessThreadFormatter(logging.Formatter):
    """Formatador customizado que extrai PID e Thread ID atribuídos pelo kernel do SO."""
    def format(self, record):
        record.process_id = os.getpid()
        return super().format(record)

def setup_logger(logs_dir: Path, encoding: str = "utf-8") -> logging.Logger:
    logger = logging.getLogger("sistema_agendamento")
    if logger.hasHandlers():
        return logger

    logger.setLevel(logging.DEBUG)
    log_file_path = logs_dir / "sistema.log"

    log_format = (
        "[%(asctime)s %(levelname)s] [PID:%(process)d] [TID:%(thread)d|%(threadName)s] "
        "[%(filename)s:%(lineno)d] - %(message)s"
    )
    formatter = ProcessThreadFormatter(fmt=log_format, datefmt="%Y-%m-%d %H:%M:%S")

    # Rotação de logs em disco (max 2MB, até 5 cópias de backup)
    file_handler = RotatingFileHandler(
        filename=str(log_file_path),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding=encoding
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger

# ==========================================
# 2. GERENCIADOR DE ARQUIVOS (CONCEITO DE SO: ESCRITA ATÔMICA E I/O)
# ==========================================
class FileManager:
    def __init__(self, default_encoding: str = "utf-8"):
        self.default_encoding = default_encoding

    def save_json_atomic(self, file_path: Path, data: Any) -> None:
        """
        Escrita atômica garantida pelo SO:
        1. Escreve em arquivo temporário (.tmp)
        2. os.fsync(): força gravação física dos buffers do kernel no disco
        3. os.replace(): substituição atômica na tabela do sistema de arquivos
        """
        file_path = Path(file_path)
        temp_file_path = file_path.with_suffix(".tmp")

        with open(temp_file_path, "w", encoding=self.default_encoding) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Chamada de sistema: descarrega buffer de I/O

        os.replace(temp_file_path, file_path)

    def load_json(self, file_path: Path, default: Any = None) -> Any:
        file_path = Path(file_path)
        if not file_path.exists():
            return default if default is not None else {}
        with open(file_path, "r", encoding=self.default_encoding) as f:
            return json.load(f)

    def save_csv(self, file_path: Path, headers: List[str], rows: List[Dict[str, Any]]) -> None:
        file_path = Path(file_path)
        temp_file = file_path.with_suffix(".tmp")
        with open(temp_file, "w", newline="", encoding=self.default_encoding) as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_file, file_path)

# ==========================================
# 3. BACKUP AUTOMÁTICO (CONCEITO DE SO: THREAD DAEMON PERIÓDICA)
# ==========================================
class AutoBackupManager:
    def __init__(self, source_dir: Path, backup_dir: Path, interval_seconds: int = 300, max_backups: int = 5):
        self.source_dir = source_dir
        self.backup_dir = backup_dir
        self.interval_seconds = interval_seconds
        self.max_backups = max_backups
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def create_backup(self) -> str:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = f"backup_consultas_{timestamp}.zip"
        backup_path = self.backup_dir / backup_filename

        with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in self.source_dir.glob("*.*"):
                if file_path.suffix not in [".tmp", ".lock"]:
                    zip_file.write(file_path, arcname=file_path.name)

        self._rotate_old_backups()
        return str(backup_path)

    def _rotate_old_backups(self) -> None:
        backups = sorted(list(self.backup_dir.glob("backup_consultas_*.zip")), key=lambda p: p.stat().st_mtime)
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
            except Exception:
                pass

    def list_backups(self) -> List[Dict[str, Any]]:
        results = []
        for b in sorted(self.backup_dir.glob("backup_consultas_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = b.stat()
            results.append({
                "filename": b.name,
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "path": str(b)
            })
        return results

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval_seconds):
                break
            try:
                self.create_backup()
            except Exception:
                pass

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="SO-BackupDaemon", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)

