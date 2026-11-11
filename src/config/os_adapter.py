# src/config/os_adapter.py
"""
Módulo de Configuração Dependente de SO e Gerenciamento de Memória
Demonstração de Conceitos de SO:
- Chamadas de Sistema: Identificação de SO (Windows, Linux, macOS) e atributos de arquivo (os.stat / os.chmod)
- Filesystem: Resolução de caminhos por SO, sanitização de nomes, limite MAX_PATH e case-sensitivity
- Gerenciamento de Memória: Cache LRU com cópia defensiva, monitoramento RSS/VMS (psutil) e tracemalloc
- Garbage Collection & Limpeza Automática: Coleta de lixo e daemon SO-CleanupDaemon para órfãos (.tmp/.lock)
"""

import os
import re
import gc
import stat
import copy
import time
import logging
import platform
import getpass
import psutil
import threading
import tracemalloc
from pathlib import Path
from collections import OrderedDict
from typing import Dict, Any, Optional, List

# Logger compartilhado do sistema (evita importação circular com storage.py)
_so_logger = logging.getLogger("sistema_agendamento")

# Caracteres proibidos em nomes de arquivo no Windows e caracteres de controle ASCII
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Nomes reservados pelo subsistema DOS/Win32 (independente de extensão ou caixa)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


class OSAdapter:
    """
    🔹 SO: Camada de Abstração de Plataforma e Sistema de Arquivos.
    Detecta características do kernel e adapta caminhos, permissões, encodings e quebras de linha.
    """

    def __init__(self, base_dir: Optional[Path] = None):
        # 🔹 SO: Chamadas de sistema para identificação de plataforma e hardware
        self.system = platform.system()  # 'Windows', 'Linux', 'Darwin'
        self.release = platform.release()
        self.version = platform.version()
        self.machine = platform.machine()
        self.python_version = platform.python_version()
        self.path_sep = os.sep
        self.line_ending = "\r\n" if self.system == "Windows" else "\n"
        self.default_encoding = "utf-8"
        # 🔹 SO: Encoding de CSV — utf-8-sig no Windows (BOM para Excel reconhecer acentos) e utf-8 em POSIX
        self.csv_encoding = "utf-8-sig" if self.system == "Windows" else "utf-8"
        # 🔹 SO: Limite clássico de comprimento de caminho (MAX_PATH no Windows = 260)
        self.max_path_length = 260 if self.system == "Windows" else (1024 if self.system == "Darwin" else 4096)

        if base_dir is None:
            self.base_dir = Path(__file__).resolve().parent.parent.parent
        else:
            self.base_dir = Path(base_dir).resolve()

        self._local_paths = self._build_local_paths(self.base_dir / "data")
        self._os_paths = self._build_os_paths()

        # 🔹 SO: Seleção do modo de diretórios (padrão local ./data; modo 'os' via AGENDAMENTO_MODE=os)
        env_data_dir = os.environ.get("AGENDAMENTO_DATA_DIR", "").strip()
        env_mode = os.environ.get("AGENDAMENTO_MODE", "").strip().lower()

        if base_dir is not None:
            self.path_mode = "local"
            active = self._local_paths
        elif env_data_dir:
            self.path_mode = "local"
            active = self._build_local_paths(Path(env_data_dir).resolve())
        elif env_mode == "os":
            self.path_mode = "os"
            active = self._os_paths
        else:
            self.path_mode = "local"
            active = self._local_paths

        self.data_dir: Path = active["data_dir"]
        self.consultas_dir: Path = active["consultas_dir"]
        self.relatorios_dir: Path = active["relatorios_dir"]
        self.logs_dir: Path = active["logs_dir"]
        self.backups_dir: Path = active["backups_dir"]

        # Criação dos diretórios com permissões seguras específicas do SO
        for d in [self.data_dir, self.consultas_dir, self.relatorios_dir, self.logs_dir, self.backups_dir]:
            d.mkdir(parents=True, exist_ok=True)
            self.apply_safe_permissions(d)

        # 🔹 SO: Detecção empírica de case-sensitivity do filesystem montado
        self.is_case_sensitive = self.detect_case_sensitive_fs(self.data_dir)
        _so_logger.debug(
            f"[SO Filesystem] Modo de caminhos='{self.path_mode}' | data_dir='{self.data_dir}' | "
            f"relatorios_dir='{self.relatorios_dir}' | case_sensitive={self.is_case_sensitive}"
        )

    def _build_local_paths(self, data_root: Path) -> Dict[str, Path]:
        return {
            "data_dir": data_root,
            "consultas_dir": data_root / "consultas",
            "relatorios_dir": data_root / "relatorios",
            "logs_dir": data_root / "logs",
            "backups_dir": data_root / "backups",
        }

    def _build_os_paths(self) -> Dict[str, Path]:
        """Resolve os diretórios nativos padrão de dados e documentos do usuário para cada SO."""
        if self.system == "Windows":
            appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
            userprofile = Path(os.environ.get("USERPROFILE") or Path.home())
            data_root = appdata / "AgendamentoSO"
            relatorios = userprofile / "Documents" / "AgendamentoSO" / "relatorios"
        elif self.system == "Darwin":
            data_root = Path.home() / "Library" / "Application Support" / "AgendamentoSO"
            relatorios = Path.home() / "Documents" / "AgendamentoSO" / "relatorios"
        else:
            # Linux / POSIX seguindo especificação XDG Base Directory
            xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
            xdg_docs = os.environ.get("XDG_DOCUMENTS_DIR", "").strip()
            data_base = Path(xdg_data) if xdg_data else (Path.home() / ".local" / "share")
            docs_base = Path(xdg_docs) if xdg_docs else (Path.home() / "Documents")
            data_root = data_base / "agendamento-so"
            relatorios = docs_base / "AgendamentoSO" / "relatorios"

        return {
            "data_dir": data_root,
            "consultas_dir": data_root / "consultas",
            "relatorios_dir": relatorios,
            "logs_dir": data_root / "logs",
            "backups_dir": data_root / "backups",
        }

    def detect_case_sensitive_fs(self, test_dir: Path) -> bool:
        """
        🔹 SO: Filesystem — Testa na prática se o sistema de arquivos diferencia maiúsculas e minúsculas.
        Cria um arquivo temporário em caixa mista e verifica se o caminho em minúsculas aponta para o mesmo inode/entrada.
        (NTFS/Windows e APFS padrão/macOS costumam ser case-insensitive; ext4/Linux é case-sensitive).
        """
        probe_upper = test_dir / f".SO_CaseProbe_{os.getpid()}_{threading.get_ident()}.tmp"
        probe_lower = test_dir / probe_upper.name.lower()
        try:
            probe_upper.write_text("probe", encoding="ascii")
            # Se o caminho em minúsculas existir mesmo tendo criado em caixa mista, o FS é case-insensitive
            return not probe_lower.exists()
        except Exception:
            return self.system == "Linux"
        finally:
            try:
                if probe_upper.exists():
                    probe_upper.unlink()
            except Exception:
                pass

    def apply_safe_permissions(self, path: Path) -> Dict[str, Any]:
        """
        🔹 SO: Segurança e Permissões no Filesystem (POSIX vs Windows).
        - POSIX (Linux/macOS): aplica `os.chmod` com `0o700` (diretórios) ou `0o600` (arquivos),
          restringindo leitura/escrita apenas ao UID proprietário (controle discricionário rwx------ / rw-------).
        - Windows (NTFS): o controle de acesso real depende de ACLs/SIDs da conta autenticada (não há bits rwx POSIX).
          `os.chmod` no Windows altera apenas o atributo legado READONLY (`FILE_ATTRIBUTE_READONLY`), o que impediria
          `os.replace()` em arquivos JSON mutáveis; por isso, no Windows inspecionamos e registramos `os.stat().st_file_attributes`.
        """
        info: Dict[str, Any] = {"path": str(path), "system": self.system}
        try:
            if self.system in ("Linux", "Darwin"):
                if path.is_dir():
                    os.chmod(path, 0o700)
                    info["posix_mode"] = "0o700"
                elif path.is_file():
                    os.chmod(path, 0o600)
                    info["posix_mode"] = "0o600"
            if path.exists():
                st = os.stat(path)
                info["mode_oct"] = oct(stat.S_IMODE(st.st_mode))
                if hasattr(st, "st_file_attributes"):
                    info["st_file_attributes"] = st.st_file_attributes
                    info["windows_attributes"] = self._decode_windows_attributes(st.st_file_attributes)
            _so_logger.debug(f"[SO Permissões] {info}")
        except Exception as e:
            info["error"] = str(e)
        return info

    @staticmethod
    def _decode_windows_attributes(attrs: int) -> List[str]:
        flags = []
        mapping = [
            ("FILE_ATTRIBUTE_READONLY", "READONLY"),
            ("FILE_ATTRIBUTE_HIDDEN", "HIDDEN"),
            ("FILE_ATTRIBUTE_SYSTEM", "SYSTEM"),
            ("FILE_ATTRIBUTE_DIRECTORY", "DIRECTORY"),
            ("FILE_ATTRIBUTE_ARCHIVE", "ARCHIVE"),
            ("FILE_ATTRIBUTE_NORMAL", "NORMAL"),
            ("FILE_ATTRIBUTE_TEMPORARY", "TEMPORARY"),
        ]
        for attr_const, label in mapping:
            val = getattr(stat, attr_const, None)
            if val is not None and (attrs & val):
                flags.append(label)
        return flags

    def get_file_security_info(self, path: Path) -> Dict[str, Any]:
        """Consulta metadados de segurança e atributos do arquivo via chamada de sistema `os.stat`."""
        if not path.exists():
            return {"path": str(path), "exists": False}
        st = os.stat(path)
        info: Dict[str, Any] = {
            "path": str(path),
            "exists": True,
            "mode_oct": oct(stat.S_IMODE(st.st_mode)),
            "uid": getattr(st, "st_uid", None),
            "size_bytes": st.st_size,
        }
        if hasattr(st, "st_file_attributes"):
            info["st_file_attributes"] = st.st_file_attributes
            info["windows_attributes"] = self._decode_windows_attributes(st.st_file_attributes)
        return info

    def sanitize_filename(self, filename: str, replacement: str = "_") -> str:
        """
        🔹 SO: Sanitização de nome de arquivo multiplataforma.
        Remove caracteres proibidos no Windows (<>:"/\\|?* e controle), pontos/espaços finais
        e neutraliza nomes reservados do kernel DOS/Win32 (CON, PRN, AUX, NUL, COM1..9, LPT1..9).
        """
        name = Path(filename).name.strip()
        name = _INVALID_FILENAME_CHARS_RE.sub(replacement, name)
        # Windows remove pontos e espaços finais silenciosamente; normalizamos aqui
        name = name.rstrip(" .")
        if not name:
            return "arquivo"

        stem, dot, ext = name.partition(".")
        if stem.upper() in _WINDOWS_RESERVED_NAMES:
            stem = f"_{stem}"
            name = f"{stem}{dot}{ext}" if dot else stem

        return name

    def normalize_name_for_comparison(self, name: str) -> str:
        """
        🔹 SO: Normalização de nomes respeitando case-insensitivity (Windows/macOS) ou sensibilidade (Linux).
        """
        if not self.is_case_sensitive or self.system in ("Windows", "Darwin"):
            return name.casefold()
        return name

    def names_equal(self, name_a: str, name_b: str) -> bool:
        return self.normalize_name_for_comparison(name_a) == self.normalize_name_for_comparison(name_b)

    def safe_path(self, directory: Path, filename: str) -> Path:
        """
        🔹 SO: Resolve caminho seguro respeitando sanitização e limite MAX_PATH (260 chars no Windows).
        """
        clean_name = self.sanitize_filename(filename)
        directory = Path(directory).resolve()
        # Reserva margem para sufixo .tmp na escrita atômica e terminador NUL do Win32
        effective_limit = (self.max_path_length - 6) if self.system == "Windows" else self.max_path_length
        candidate = directory / clean_name

        if len(str(candidate)) > effective_limit:
            max_name_len = effective_limit - len(str(directory)) - 1
            if max_name_len < 8:
                raise ValueError(
                    f"Caminho base excede o limite de comprimento do SO ({self.max_path_length} caracteres): {directory}"
                )
            p = Path(clean_name)
            ext = p.suffix[:max(0, max_name_len // 2)]
            stem_limit = max_name_len - len(ext)
            clean_name = f"{p.stem[:stem_limit]}{ext}"
            candidate = directory / clean_name

        _so_logger.debug(f"[SO Path] Caminho resolvido: {candidate}")
        return candidate

    def get_paths_summary(self) -> Dict[str, Any]:
        return {
            "active_mode": self.path_mode,
            "active": {
                "data_dir": str(self.data_dir),
                "consultas_dir": str(self.consultas_dir),
                "relatorios_dir": str(self.relatorios_dir),
                "logs_dir": str(self.logs_dir),
                "backups_dir": str(self.backups_dir),
            },
            "local_mode": {k: str(v) for k, v in self._local_paths.items()},
            "os_mode": {k: str(v) for k, v in self._os_paths.items()},
        }

    def get_telemetry(self) -> Dict[str, Any]:
        return {
            "os_name": self.system,
            "os_release": self.release,
            "os_version": self.version,
            "architecture": self.machine,
            "python_version": self.python_version,
            "path_separator": self.path_sep,
            "line_ending": repr(self.line_ending),
            "default_encoding": self.default_encoding,
            "csv_encoding": self.csv_encoding,
            "current_user": getpass.getuser(),
            "cpu_cores": os.cpu_count() or 1,
            "base_directory": str(self.base_dir),
            "path_mode": self.path_mode,
            "paths": self.get_paths_summary(),
            "filesystem_case_sensitive": self.is_case_sensitive,
            "max_path_length": self.max_path_length,
            "data_dir_security": self.get_file_security_info(self.data_dir),
        }


# ==========================================
# CACHE LRU EM MEMÓRIA RAM (CONCEITO DE SO: GERÊNCIA DE MEMÓRIA)
# ==========================================
class LRUCache:
    """
    🔹 SO: Cache em memória RAM com política de substituição LRU (Least Recently Used) e TTL.
    Retorna cópias profundas (deepcopy) para impedir mutação acidental dos dados em cache por referência.
    """

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
                _so_logger.debug(f"[SO Cache] MISS para chave '{key}'")
                return None
            entry = self._cache[key]
            if time.time() > entry["expires_at"]:
                del self._cache[key]
                self.misses += 1
                _so_logger.debug(f"[SO Cache] MISS (TTL expirado) para chave '{key}'")
                return None
            self._cache.move_to_end(key)
            self.hits += 1
            _so_logger.debug(f"[SO Cache] HIT para chave '{key}'")
            # 🔹 SO: Cópia defensiva protege a integridade da memória em cache
            return copy.deepcopy(entry["value"])

    def put(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            elif len(self._cache) >= self.capacity:
                # Evicção LRU: remove o menos recentemente usado (substituição de páginas)
                evicted_key, _ = self._cache.popitem(last=False)
                _so_logger.debug(f"[SO Cache] EVICÇÃO LRU da chave '{evicted_key}'")
            self._cache[key] = {
                "value": copy.deepcopy(value),
                "expires_at": time.time() + ttl
            }

    def invalidate(self, key: str) -> bool:
        with self._lock:
            removed = bool(self._cache.pop(key, None))
            if removed:
                _so_logger.debug(f"[SO Cache] Invalidação da chave '{key}'")
            return removed

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
# MONITOR DE MEMÓRIA DO PROCESSO (RSS / VMS / TRACEMALLOC)
# ==========================================
class MemoryMonitor:
    """
    🔹 SO: Monitoramento do espaço de endereçamento do processo (RSS físico e VMS virtual via psutil)
    e rastreamento fino de alocações no heap Python via `tracemalloc`.
    """

    def __init__(self, cache: LRUCache):
        self.process = psutil.Process(os.getpid())
        self.cache = cache
        self._snapshot_lock = threading.Lock()
        self._last_snapshot: Optional[tracemalloc.Snapshot] = None

    def ensure_tracemalloc(self) -> None:
        if not tracemalloc.is_tracing():
            tracemalloc.start()

    def get_info(self) -> Dict[str, Any]:
        mem = self.process.memory_info()
        sys_mem = psutil.virtual_memory()
        traced_current_kb = 0.0
        traced_peak_kb = 0.0
        if tracemalloc.is_tracing():
            cur, peak = tracemalloc.get_traced_memory()
            traced_current_kb = round(cur / 1024, 2)
            traced_peak_kb = round(peak / 1024, 2)
        return {
            "pid": self.process.pid,
            "process_rss_mb": round(mem.rss / (1024 * 1024), 2),  # RAM física real
            "process_vms_mb": round(mem.vms / (1024 * 1024), 2),  # Memória virtual alocada
            "system_total_ram_mb": round(sys_mem.total / (1024 * 1024), 2),
            "system_ram_percent": sys_mem.percent,
            "threads_count": self.process.num_threads(),
            "cache_stats": self.cache.get_stats(),
            "tracemalloc_active": tracemalloc.is_tracing(),
            "traced_current_kb": traced_current_kb,
            "traced_peak_kb": traced_peak_kb,
        }

    def take_tracemalloc_snapshot(self, limit: int = 10) -> Dict[str, Any]:
        """Captura um snapshot do heap via `tracemalloc` retornando as top N alocações."""
        self.ensure_tracemalloc()
        with self._snapshot_lock:
            snapshot = tracemalloc.take_snapshot()
            self._last_snapshot = snapshot
            cur, peak = tracemalloc.get_traced_memory()

        stats = snapshot.statistics("lineno")[:limit]
        top_allocations = []
        for s in stats:
            frame = s.traceback[0]
            top_allocations.append({
                "file": frame.filename,
                "line": frame.lineno,
                "size_kb": round(s.size / 1024, 2),
                "count": s.count,
            })

        return {
            "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "traced_current_kb": round(cur / 1024, 2),
            "traced_peak_kb": round(peak / 1024, 2),
            "top_allocations": top_allocations,
        }

    def compare_tracemalloc_snapshots(self, limit: int = 10) -> Dict[str, Any]:
        """Compara o snapshot atual com o anterior para evidenciar possíveis vazamentos de memória."""
        self.ensure_tracemalloc()
        with self._snapshot_lock:
            previous = self._last_snapshot
            current = tracemalloc.take_snapshot()
            self._last_snapshot = current

        if previous is None:
            return {
                "has_previous": False,
                "message": "Snapshot inicial de referência capturado. Execute novamente para comparar variações.",
                "total_diff_kb": 0.0,
                "leak_suspected": False,
                "diffs": [],
            }

        diffs = current.compare_to(previous, "lineno")
        total_diff_bytes = sum(d.size_diff for d in diffs)
        top_diffs = []
        for d in diffs[:limit]:
            frame = d.traceback[0]
            top_diffs.append({
                "file": frame.filename,
                "line": frame.lineno,
                "size_diff_kb": round(d.size_diff / 1024, 2),
                "size_kb": round(d.size / 1024, 2),
                "count_diff": d.count_diff,
                "count": d.count,
            })

        total_diff_kb = round(total_diff_bytes / 1024, 2)
        return {
            "has_previous": True,
            "message": (
                "Crescimento significativo detectado; verifique alocações positivas."
                if total_diff_kb > 512
                else "Memória estável entre snapshots (sem evidência de vazamento)."
            ),
            "total_diff_kb": total_diff_kb,
            "leak_suspected": total_diff_kb > 512,
            "diffs": top_diffs,
        }

    def run_garbage_collection(self, search_dirs: List[Path], max_orphan_age_seconds: int = 60) -> Dict[str, Any]:
        """Liberação explícita de memória (gc.collect) e limpeza de arquivos temporários (.tmp e .lock) órfãos."""
        initial_mem = self.process.memory_info().rss / (1024 * 1024)
        purged_cache = self.cache.purge_expired()
        collected = gc.collect()
        final_mem = self.process.memory_info().rss / (1024 * 1024)

        deleted_tmp = 0
        deleted_lock = 0
        now = time.time()
        for d in search_dirs:
            if d.exists():
                for pattern, kind in [("*.tmp", "tmp"), ("*.lock", "lock")]:
                    for f in d.glob(pattern):
                        try:
                            if now - f.stat().st_mtime > max_orphan_age_seconds:
                                f.unlink()
                                if kind == "tmp":
                                    deleted_tmp += 1
                                else:
                                    deleted_lock += 1
                        except Exception:
                            # No Windows, arquivo .lock em uso ativo gera PermissionError e é ignorado
                            pass

        return {
            "unreachable_objects_collected": collected,
            "cache_entries_purged": purged_cache,
            "temp_files_deleted": deleted_tmp,
            "lock_files_deleted": deleted_lock,
            "ram_reclaimed_mb": round(max(0, initial_mem - final_mem), 2),
            "current_rss_mb": round(final_mem, 2)
        }


# ==========================================
# DAEMON DE LIMPEZA AUTOMÁTICA (CONCEITO DE SO: THREAD DAEMON)
# ==========================================
class CleanupDaemon:
    """
    🔹 SO: Thread daemon periódica (`SO-CleanupDaemon`) que a cada 60 s purga entradas expiradas
    do cache LRU e remove arquivos órfãos `.tmp` e `.lock` com mais de 60 s no disco.
    """

    def __init__(
        self,
        cache: LRUCache,
        search_dirs: List[Path],
        interval_seconds: int = 60,
        max_orphan_age_seconds: int = 60,
    ):
        self.cache = cache
        self.search_dirs = search_dirs
        self.interval_seconds = interval_seconds
        self.max_orphan_age_seconds = max_orphan_age_seconds
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def run_cleanup_once(self) -> Dict[str, int]:
        purged = self.cache.purge_expired()
        deleted_tmp = 0
        deleted_lock = 0
        now = time.time()

        for d in self.search_dirs:
            if not d.exists():
                continue
            for pattern, is_tmp in [("*.tmp", True), ("*.lock", False)]:
                for f in d.glob(pattern):
                    try:
                        if now - f.stat().st_mtime > self.max_orphan_age_seconds:
                            f.unlink()
                            if is_tmp:
                                deleted_tmp += 1
                            else:
                                deleted_lock += 1
                    except Exception:
                        pass

        _so_logger.debug(
            f"[SO CleanupDaemon] Purga periódica: {purged} entradas de cache expiradas, "
            f"{deleted_tmp} .tmp e {deleted_lock} .lock órfãos removidos."
        )
        return {
            "cache_entries_purged": purged,
            "temp_files_deleted": deleted_tmp,
            "lock_files_deleted": deleted_lock,
        }

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            if self._stop_event.wait(timeout=self.interval_seconds):
                break
            try:
                self.run_cleanup_once()
            except Exception as exc:
                _so_logger.debug(f"[SO CleanupDaemon] Falha no ciclo de limpeza: {exc}")

    def start(self) -> None:
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._loop, name="SO-CleanupDaemon", daemon=True)
            self._thread.start()
            _so_logger.debug("[SO CleanupDaemon] Thread daemon iniciada.")

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2.0)
            _so_logger.debug("[SO CleanupDaemon] Thread daemon encerrada.")


# Instâncias globais exportadas
os_adapter = OSAdapter()
consultas_cache = LRUCache()
memory_monitor = MemoryMonitor(cache=consultas_cache)
cleanup_daemon = CleanupDaemon(
    cache=consultas_cache,
    search_dirs=[
        os_adapter.consultas_dir,
        os_adapter.relatorios_dir,
        os_adapter.backups_dir,
        os_adapter.logs_dir,
    ],
)
