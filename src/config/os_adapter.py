# src/config/os_adapter.py
"""
Módulo de Configuração Dependente de SO e Gerenciamento de Memória
Demonstração de Conceitos de SO:
- Chamadas de Sistema: Identificação de SO (Windows, Linux, macOS)
- Filesystem: Resolução de caminhos nativos (os.sep) e permissões de arquivos
- Gerenciamento de Memória: Cache LRU (RAM) e monitoramento de RSS/VMS via psutil
- Garbage Collection: Coleta de lixo e controle de vazamento de memória (Memory Leaks)
"""

import os
import gc
import sys
import time
import platform
import getpass
import psutil
import threading
from pathlib import Path
from collections import OrderedDict
from typing import Dict, Any, Optional

class OSAdapter:
    def __init__(self, base_dir: Optional[Path] = None):
        self.system = platform.system()  # 'Windows', 'Linux', 'Darwin'
        self.release = platform.release()
        self.version = platform.version()
        self.machine = platform.machine()
        self.python_version = platform.python_version()
        self.path_sep = os.sep
        self.line_ending = "\r\n" if self.system == "Windows" else "\n"
        self.default_encoding = "utf-8"

        # Diretórios do projeto
        if base_dir is None:
            self.base_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.base_dir = Path(base_dir).resolve()

        self.data_dir = self.base_dir / "data"
        self.consultas_dir = self.data_dir / "consultas"
        self.relatorios_dir = self.data_dir / "relatorios"
        self.logs_dir = self.data_dir / "logs"
        self.backups_dir = self.data_dir / "backups"

        # Criação dos diretórios com permissões adequadas
        for d in [self.consultas_dir, self.relatorios_dir, self.logs_dir, self.backups_dir]:
            d.mkdir(parents=True, exist_ok=True)
            self.apply_safe_permissions(d)

    def apply_safe_permissions(self, path: Path) -> None:
        """Aplica permissões de arquivo seguras específicas do SO (chmod em POSIX)."""
        try:
            if self.system in ["Linux", "Darwin"]:
                if path.is_dir():
                    os.chmod(path, 0o700)
                elif path.is_file():
                    os.chmod(path, 0o600)
        except Exception:
            pass

    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "os_name": self.system,
            "os_release": self.release,
            "os_version": self.version,
            "architecture": self.machine,
            "python_version": self.python_version,
            "path_separator": self.path_sep,
            "line_ending": repr(self.line_ending),
            "current_user": getpass.getuser(),
            "cpu_cores": os.cpu_count() or 1,
            "base_directory": str(self.base_dir)
        }

# ==========================================
# CACHE LRU EM MEMÓRIA RAM (CONCEITO DE SO: GERÊNCIA DE MEMÓRIA)
# ==========================================
class LRUCache:
    def __init__(self, capacity: int = 150, default_ttl_seconds: int = 300):
        self.capacity = capacity
        self.default_ttl = default_ttl_seconds
        self._cache: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            if key not in self._cache:
                self.misses += 1
                return None
            entry = self._cache[key]
            if time.time() > entry["expires_at"]:
                del self._cache[key]
                self.misses += 1
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            return entry["value"]

    def put(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.capacity:
                # Evicção LRU: remove o menos recentemente usado (substituição de páginas)
                self._cache.popitem(last=False)
            self._cache[key] = {
                "value": value,
                "expires_at": time.time() + ttl
            }

    def invalidate(self, key: str) -> bool:
        with self._lock:
            return bool(self._cache.pop(key, None))

    def purge_expired(self) -> int:
        now = time.time()
        purged = 0
        with self._lock:
            expired = [k for k, v in self._cache.items() if now > v["expires_at"]]
            for k in expired:
                del self._cache[k]
                purged += 1
        return purged

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total = self.hits + self.misses
            ratio = (self.hits / total * 100) if total > 0 else 0.0
            return {
                "capacity": self.capacity,
                "current_size": len(self._cache),
                "hits": self.hits,
                "misses": self.misses,
                "hit_ratio_percent": round(ratio, 2)
            }

# ==========================================
# MONITOR DE MEMÓRIA DO PROCESSO (RSS / VMS)
# ==========================================
class MemoryMonitor:
    def __init__(self, cache: LRUCache):
        self.process = psutil.Process(os.getpid())
        self.cache = cache

    def get_info(self) -> Dict[str, Any]:
        mem = self.process.memory_info()
        sys_mem = psutil.virtual_memory()
        return {
            "pid": self.process.pid,
            "process_rss_mb": round(mem.rss / (1024 * 1024), 2),  # RAM física real
            "process_vms_mb": round(mem.vms / (1024 * 1024), 2),  # Memória virtual alocada
            "system_total_ram_mb": round(sys_mem.total / (1024 * 1024), 2),
            "system_ram_percent": sys_mem.percent,
            "threads_count": self.process.num_threads(),
            "cache_stats": self.cache.get_stats()
        }

    def run_garbage_collection(self, search_dirs: list[Path]) -> Dict[str, Any]:
        """Liberação explícita de memória (gc.collect) e limpeza de arquivos temporários."""
        initial_mem = self.process.memory_info().rss / (1024 * 1024)
        purged_cache = self.cache.purge_expired()
        collected = gc.collect()
        final_mem = self.process.memory_info().rss / (1024 * 1024)

        # Deletar temporários (.tmp) órfãos
        deleted_tmp = 0
        now = time.time()
        for d in search_dirs:
            if d.exists():
                for f in d.glob("*.tmp"):
                    try:
                        if now - f.stat().st_mtime > 60:
                            f.unlink()
                            deleted_tmp += 1
                    except Exception:
                        pass

        return {
            "unreachable_objects_collected": collected,
            "cache_entries_purged": purged_cache,
            "temp_files_deleted": deleted_tmp,
            "ram_reclaimed_mb": round(max(0, initial_mem - final_mem), 2),
            "current_rss_mb": round(final_mem, 2)
        }

# Instâncias globais exportadas
os_adapter = OSAdapter()
consultas_cache = LRUCache()
memory_monitor = MemoryMonitor(cache=consultas_cache)
