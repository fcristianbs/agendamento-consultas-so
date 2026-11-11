# src/storage/storage.py
"""
Módulo de Sistema de Arquivos, I/O e Armazenamento
Demonstração de Conceitos de SO:
- Sistema de Arquivos: Persistência estruturada em arquivos JSON/CSV com recuperação de corrupção
- Escrita Atômica: Garantia de integridade com buffers sincronizados via os.fsync e os.replace
- I/O Assíncrono: Leituras e escritas em thread pool / asyncio.to_thread sem bloquear o event loop
- Backups Automáticos: Thread daemon periódica de snapshots compactados (.zip)
- Sistema de Logging: Dispositivo de saída com fuso horário do SO, nível configurável, PID e Thread ID
"""

import os
import sys
import copy
import json
import csv
import asyncio
import zipfile
import threading
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Any, List, Dict, Optional

from src.config.os_adapter import os_adapter

_storage_logger = logging.getLogger("sistema_agendamento")


# ==========================================
# 1. SISTEMA DE LOGGING (CONCEITO DE SO: DISPOSITIVOS)
# ==========================================
class ProcessThreadFormatter(logging.Formatter):
    """
    🔹 SO: Formatador customizado que extrai PID, Thread ID (TID) do kernel
    e formata o carimbo temporal incluindo o fuso horário local do SO (%z).
    """

    def formatTime(self, record: logging.LogRecord, datefmt: Optional[str] = None) -> str:
        # 🔹 SO: Consulta o relógio local e fuso horário configurados no SO hospedeiro
        dt = datetime.fromtimestamp(record.created).astimezone()
        if datefmt:
            return dt.strftime(datefmt)
        return dt.strftime("%Y-%m-%d %H:%M:%S %z")

    def format(self, record: logging.LogRecord) -> str:
        record.process_id = os.getpid()
        return super().format(record)


def setup_logger(logs_dir: Path, encoding: str = "utf-8") -> logging.Logger:
    """Configura dispositivo de log rotativo com nível de arquivo via LOG_LEVEL e console em INFO."""
    logger = logging.getLogger("sistema_agendamento")

    env_level_name = os.environ.get("LOG_LEVEL", "DEBUG").strip().upper()
    configured_level = getattr(logging, env_level_name, logging.DEBUG)
    logger.setLevel(configured_level)

    if logger.hasHandlers():
        for h in logger.handlers:
            if isinstance(h, RotatingFileHandler):
                h.setLevel(configured_level)
        return logger

    logs_dir.mkdir(parents=True, exist_ok=True)
    log_file_path = logs_dir / "sistema.log"

    log_format = (
        "[%(asctime)s %(levelname)s] [PID:%(process)d] [TID:%(thread)d|%(threadName)s] "
        "[%(filename)s:%(lineno)d] - %(message)s"
    )
    formatter = ProcessThreadFormatter(fmt=log_format, datefmt="%Y-%m-%d %H:%M:%S %z")

    # Rotação de logs em disco (max 2MB, até 5 cópias de backup)
    file_handler = RotatingFileHandler(
        filename=str(log_file_path),
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding=encoding,
    )
    file_handler.setLevel(configured_level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 🔹 SO: Aplica permissões restritas (0o600 no POSIX) também ao arquivo de log
    os_adapter.apply_safe_permissions(log_file_path)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger


# ==========================================
# 2. GERENCIADOR DE ARQUIVOS (CONCEITO DE SO: ESCRITA ATÔMICA E I/O)
# ==========================================
class FileManager:
    """
    🔹 SO: Gerenciador de persistência síncrona e assíncrona com escrita atômica e
    recuperação automática em caso de JSON corrompido.
    """

    def __init__(self, default_encoding: str = "utf-8", backup_dir: Optional[Path] = None):
        self.default_encoding = default_encoding
        self.backup_dir = backup_dir

    def save_json_atomic(self, file_path: Path, data: Any) -> None:
        """
        Escrita atômica garantida pelo SO:
        1. Escreve em arquivo temporário (.tmp)
        2. os.fsync(): força gravação física dos buffers do kernel no disco
        3. os.replace(): substituição atômica na tabela do sistema de arquivos
        4. Aplica permissões seguras específicas do SO (0o600 no POSIX / atributos via os.stat no Windows)
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file_path = file_path.with_suffix(".tmp")

        with open(temp_file_path, "w", encoding=self.default_encoding) as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())  # Chamada de sistema: descarrega buffer de I/O

        os.replace(temp_file_path, file_path)
        os_adapter.apply_safe_permissions(file_path)
        _storage_logger.debug(f"[SO Storage] Escrita atômica concluída em: {file_path}")

    def _restore_from_latest_backup(self, file_path: Path, backup_dir: Optional[Path] = None) -> Optional[Any]:
        """Tenta restaurar um arquivo JSON corrompido a partir do snapshot .zip mais recente."""
        target_backup_dir = backup_dir or self.backup_dir or os_adapter.backups_dir
        if not target_backup_dir or not Path(target_backup_dir).exists():
            return None

        backups = sorted(
            Path(target_backup_dir).glob("backup_consultas_*.zip"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for zip_path in backups:
            try:
                with zipfile.ZipFile(zip_path, "r") as zf:
                    if file_path.name in zf.namelist():
                        raw = zf.read(file_path.name).decode("utf-8-sig")
                        restored = json.loads(raw)
                        self.save_json_atomic(file_path, restored)
                        _storage_logger.info(
                            f"[SO Storage] Arquivo '{file_path.name}' restaurado com sucesso do backup '{zip_path.name}'."
                        )
                        return restored
            except Exception as exc:
                _storage_logger.error(
                    f"[SO Storage] Falha ao tentar restaurar '{file_path.name}' de '{zip_path.name}': {exc}"
                )
        return None

    def load_json(self, file_path: Path, default: Any = None, backup_dir: Optional[Path] = None) -> Any:
        """Lê JSON de forma tolerante a BOM (utf-8-sig) e restaura de backup se corrompido."""
        file_path = Path(file_path)
        if not file_path.exists():
            return copy.deepcopy(default) if default is not None else {}

        _storage_logger.debug(f"[SO Storage] Leitura de JSON em: {file_path}")
        try:
            # 🔹 SO: Leitura tolerante a BOM (UTF-8 com ou sem assinatura)
            with open(file_path, "r", encoding="utf-8-sig") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            _storage_logger.error(
                f"[SO Storage] JSON corrompido detectado em '{file_path}': {err}. "
                f"Tentando restaurar do backup mais recente."
            )
            restored = self._restore_from_latest_backup(file_path, backup_dir=backup_dir)
            if restored is not None:
                return restored
            return copy.deepcopy(default) if default is not None else {}

    async def load_json_async(
        self,
        file_path: Path,
        default: Any = None,
        backup_dir: Optional[Path] = None,
        executor: Any = None,
    ) -> Any:
        """
        🔹 SO: Leitura assíncrona de JSON executada fora do event loop principal
        (via ThreadPoolExecutor ou asyncio.to_thread).
        """
        if executor is not None:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(executor, self.load_json, file_path, default, backup_dir)
        return await asyncio.to_thread(self.load_json, file_path, default, backup_dir)

    async def save_json_async(self, file_path: Path, data: Any, executor: Any = None) -> None:
        """
        🔹 SO: Escrita atômica assíncrona (tmp + fsync + os.replace) executada em worker thread.
        """
        if executor is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(executor, self.save_json_atomic, file_path, data)
            return
        await asyncio.to_thread(self.save_json_atomic, file_path, data)

    def save_csv(
        self,
        file_path: Path,
        headers: List[str],
        rows: List[Dict[str, Any]],
        encoding: Optional[str] = None,
        lineterminator: Optional[str] = None,
    ) -> None:
        """
        🔹 SO: Gravação atômica de CSV respeitando encoding (`utf-8-sig` no Windows, `utf-8` em POSIX)
        e quebra de linha nativa (`\\r\\n` no Windows, `\\n` em Linux/macOS).
        """
        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = file_path.with_suffix(".tmp")

        target_encoding = encoding or os_adapter.csv_encoding
        target_terminator = lineterminator or os_adapter.line_ending

        with open(temp_file, "w", newline="", encoding=target_encoding) as f:
            writer = csv.DictWriter(f, fieldnames=headers, lineterminator=target_terminator)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
            f.flush()
            os.fsync(f.fileno())

        os.replace(temp_file, file_path)
        os_adapter.apply_safe_permissions(file_path)
        _storage_logger.debug(
            f"[SO Storage] CSV salvo em '{file_path}' (encoding={target_encoding}, eol={repr(target_terminator)})"
        )


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
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_filename = os_adapter.sanitize_filename(f"backup_consultas_{timestamp}.zip")
        backup_path = os_adapter.safe_path(self.backup_dir, backup_filename)
        temp_zip = backup_path.with_suffix(".tmp")

        with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for file_path in self.source_dir.glob("*.*"):
                if file_path.suffix not in [".tmp", ".lock"]:
                    zip_file.write(file_path, arcname=file_path.name)

        os.replace(temp_zip, backup_path)
        os_adapter.apply_safe_permissions(backup_path)
        self._rotate_old_backups()
        _storage_logger.info(f"[SO Backup] Snapshot ZIP criado: {backup_path.name}")
        return str(backup_path)

    def _rotate_old_backups(self) -> None:
        backups = sorted(list(self.backup_dir.glob("backup_consultas_*.zip")), key=lambda p: p.stat().st_mtime)
        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            try:
                oldest.unlink()
                _storage_logger.debug(f"[SO Backup] Rotação de backup: removido '{oldest.name}'")
            except Exception:
                pass

    def list_backups(self) -> List[Dict[str, Any]]:
        results = []
        if not self.backup_dir.exists():
            return results
        for b in sorted(self.backup_dir.glob("backup_consultas_*.zip"), key=lambda p: p.stat().st_mtime, reverse=True):
            stat = b.stat()
            results.append({
                "filename": b.name,
                "size_kb": round(stat.st_size / 1024, 2),
                "created_at": datetime.fromtimestamp(stat.st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
                "path": str(b)
            })
        return results

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval_seconds):
                break
            try:
                self.create_backup()
            except Exception as exc:
                _storage_logger.error(f"[SO BackupDaemon] Erro no backup automático: {exc}")

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="SO-BackupDaemon", daemon=True)
            self._thread.start()
            _storage_logger.debug("[SO BackupDaemon] Thread daemon iniciada.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            _storage_logger.debug("[SO BackupDaemon] Thread daemon encerrada.")
