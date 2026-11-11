# src/concurrent/concurrency.py
"""
Módulo de Concorrência, Escalonamento de Threads e Sincronização
Demonstração de Conceitos de SO:
- Processos e Threads: Pool de threads gerenciadas (ThreadPoolExecutor) e rastreamento de jobs (TID)
- Exclusão Mútua (Mutex): threading.Lock com WeakValueDictionary para evitar crescimento ilimitado
- Travas de Kernel (File Locks): msvcrt.locking (Windows) e fcntl.flock (POSIX) entre processos
- Prevenção de Race Conditions: Bloqueio estrito no agendamento simultâneo entre threads e processos
"""

import os
import sys
import copy
import uuid
import weakref
import logging
import threading
import contextlib
import multiprocessing
from datetime import datetime
from pathlib import Path
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, Future
from typing import Generator, Callable, Dict, Any, Optional, List

# Chamadas nativas de kernel para cada SO
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

_conc_logger = logging.getLogger("sistema_agendamento")


# ==========================================
# 1. CONTROLE DE SINCRONIZAÇÃO E LOCKS
# ==========================================
class ConcurrencyLockManager:
    """
    🔹 SO: Gerenciador de exclusão mútua em memória (Mutex) e travas de kernel em disco.
    Usa WeakValueDictionary para que locks não utilizados sejam coletados automaticamente
    pelo coletor de lixo, evitando vazamento de memória quando há chaves dinâmicas.
    """

    def __init__(self):
        # 🔹 SO: WeakValueDictionary evita crescimento infinito da tabela de mutexes em RAM
        self._memory_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
        self._global_lock = threading.Lock()

    def get_memory_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            lock = self._memory_locks.get(key)
            if lock is None:
                lock = threading.Lock()
                self._memory_locks[key] = lock
            return lock

    def get_memory_locks_count(self) -> int:
        with self._global_lock:
            return len(self._memory_locks)

    @contextlib.contextmanager
    def synchronized(self, resource_name: str) -> Generator[None, None, None]:
        """Mutex para garantir que duas threads não acessem o recurso ao mesmo tempo."""
        lock = self.get_memory_lock(resource_name)
        acquired = lock.acquire(timeout=8.0)
        if not acquired:
            raise TimeoutError(f"Timeout aguardando lock para: {resource_name}")
        _conc_logger.debug(f"[SO Mutex] Lock adquirido para '{resource_name}'")
        try:
            yield
        finally:
            lock.release()
            _conc_logger.debug(f"[SO Mutex] Lock liberado para '{resource_name}'")

    @contextlib.contextmanager
    def file_lock(self, file_path: Path) -> Generator[None, None, None]:
        """
        🔹 SO: Trava de arquivo em nível de kernel do SO.
        Garante integridade mesmo entre processos independentes (Win32 msvcrt.locking / POSIX fcntl.flock).
        """
        file_path = Path(file_path)
        lock_file_path = file_path.with_suffix(file_path.suffix + ".lock")
        lock_file_path.parent.mkdir(parents=True, exist_ok=True)
        f = None
        try:
            f = open(lock_file_path, "a+b")
            if sys.platform == "win32":
                f.seek(0)
                if f.tell() == 0 and os.path.getsize(lock_file_path) == 0:
                    f.write(b"0")
                    f.flush()
                f.seek(0)
                # Chamada de sistema Win32 LK_LOCK
                msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            else:
                # Chamada de sistema POSIX flock
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            _conc_logger.debug(f"[SO FileLock] Trava de kernel adquirida em '{lock_file_path.name}'")
            yield
        finally:
            if f is not None:
                try:
                    if sys.platform == "win32":
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    _conc_logger.debug(f"[SO FileLock] Trava de kernel liberada em '{lock_file_path.name}'")
                except Exception:
                    pass
                finally:
                    f.close()


# ==========================================
# 2. POOL DE THREADS DE TRABALHO (ESCALONADOR)
# ==========================================
class WorkerPool:
    """
    🔹 SO: Pool de threads (`ThreadPoolExecutor`) com rastreamento de ciclo de vida de jobs
    (pendente -> executando -> concluído/erro), registrando TID e nome da thread executora.
    """

    def __init__(self, max_workers: int = 6, max_history: int = 200):
        self.max_workers = max_workers
        self.max_history = max_history
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SOWorker")
        self._is_shutdown = False
        self._active_jobs: Dict[str, Future] = {}
        self._jobs: OrderedDict[str, Dict[str, Any]] = OrderedDict()
        self._lock = threading.Lock()

    def start(self) -> None:
        """Reinicializa o ThreadPoolExecutor caso tenha sido encerrado em um ciclo anterior."""
        with self._lock:
            if self._is_shutdown or getattr(self.executor, "_shutdown", False):
                self.executor = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="SOWorker")
                self._is_shutdown = False

    def submit(self, name: str, fn: Callable, *args, **kwargs) -> Future:
        self.start()
        future = self.executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._active_jobs[name] = future

        def _cleanup(fut):
            with self._lock:
                self._active_jobs.pop(name, None)

        future.add_done_callback(_cleanup)
        return future

    def submit_job(self, name: str, fn: Callable, *args, **kwargs) -> str:
        """
        Submete uma tarefa assíncrona rastreável ao pool de threads e devolve `job_id` imediatamente.
        """
        job_id = str(uuid.uuid4())[:8]
        created_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")

        with self._lock:
            while len(self._jobs) >= self.max_history:
                self._jobs.popitem(last=False)
            self._jobs[job_id] = {
                "job_id": job_id,
                "name": name,
                "status": "pendente",
                "result": None,
                "error": None,
                "thread_name": None,
                "tid": None,
                "created_at": created_at,
                "started_at": None,
                "finished_at": None,
            }

        def _runner():
            thread_name = threading.current_thread().name
            tid = threading.get_ident()
            started_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
            with self._lock:
                if job_id in self._jobs:
                    self._jobs[job_id]["status"] = "executando"
                    self._jobs[job_id]["thread_name"] = thread_name
                    self._jobs[job_id]["tid"] = tid
                    self._jobs[job_id]["started_at"] = started_at

            _conc_logger.debug(
                f"[SO WorkerPool] Início do job {job_id} ({name}) na thread {thread_name} (TID: {tid})"
            )
            try:
                result = fn(*args, **kwargs)
                finished_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                with self._lock:
                    if job_id in self._jobs:
                        self._jobs[job_id]["status"] = "concluído"
                        self._jobs[job_id]["result"] = result
                        self._jobs[job_id]["finished_at"] = finished_at
                _conc_logger.debug(
                    f"[SO WorkerPool] Fim do job {job_id} ({name}) com sucesso na thread {thread_name} (TID: {tid})"
                )
                return result
            except Exception as exc:
                finished_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
                with self._lock:
                    if job_id in self._jobs:
                        self._jobs[job_id]["status"] = "erro"
                        self._jobs[job_id]["error"] = str(exc)
                        self._jobs[job_id]["finished_at"] = finished_at
                _conc_logger.error(
                    f"[SO WorkerPool] Erro no job {job_id} ({name}) na thread {thread_name} (TID: {tid}): {exc}"
                )
                raise

        self.start()
        future = self.executor.submit(_runner)
        job_key = f"{name}:{job_id}"
        with self._lock:
            self._active_jobs[job_key] = future

        def _cleanup(fut):
            with self._lock:
                self._active_jobs.pop(job_key, None)

        future.add_done_callback(_cleanup)
        return job_id

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job is not None else None

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "max_workers": self.max_workers,
                "active_tasks_count": len(self._active_jobs),
                "active_tasks": list(self._active_jobs.keys()),
                "system_active_threads": threading.active_count(),
                "memory_locks_count": lock_manager.get_memory_locks_count(),
                "tracked_jobs_count": len(self._jobs),
            }

    def shutdown(self) -> None:
        with self._lock:
            self.executor.shutdown(wait=False)
            self._is_shutdown = True


def run_multiprocess_tasks(worker_fn: Callable[[Any], Any], items: List[Any], max_workers: Optional[int] = None) -> List[Any]:
    """
    🔹 SO: Executa tarefas em múltiplos processos independentes usando contexto 'spawn'
    (compatível com Windows, Linux e macOS mesmo dentro de servidor multithread).
    """
    ctx = multiprocessing.get_context("spawn")
    workers = max_workers or len(items)
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        return list(pool.map(worker_fn, items))


lock_manager = ConcurrencyLockManager()
worker_pool = WorkerPool()
