# src/concurrent/concurrency.py
"""
Módulo de Concorrência, Escalonamento de Threads e Sincronização
Demonstração de Conceitos de SO:
- Processos e Threads: Pool de threads gerenciadas (ThreadPoolExecutor)
- Exclusão Mútua (Mutex): threading.Lock para isolar seções críticas
- Travas de Kernel (File Locks): msvcrt.locking (Windows) e fcntl.flock (POSIX)
- Prevenção de Race Conditions: Bloqueio estrito no agendamento simultâneo
"""

import os
import sys
import threading
import contextlib
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Generator, Callable, Dict, Any, Optional

# Chamadas nativas de kernel para cada SO
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

# ==========================================
# 1. CONTROLE DE SINCRONIZAÇÃO E LOCKS
# ==========================================
class ConcurrencyLockManager:
    def __init__(self):
        self._memory_locks: Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def get_memory_lock(self, key: str) -> threading.Lock:
        with self._global_lock:
            if key not in self._memory_locks:
                self._memory_locks[key] = threading.Lock()
            return self._memory_locks[key]

    @contextlib.contextmanager
    def synchronized(self, resource_name: str) -> Generator[None, None, None]:
        """Mutex para garantir que duas threads não acessem o recurso ao mesmo tempo."""
        lock = self.get_memory_lock(resource_name)
        acquired = lock.acquire(timeout=8.0)
        if not acquired:
            raise TimeoutError(f"Timeout aguardando lock para: {resource_name}")
        try:
            yield
        finally:
            lock.release()

    @contextlib.contextmanager
    def file_lock(self, file_path: Path) -> Generator[None, None, None]:
        """
        Trava de arquivo em nível de kernel do SO.
        Garante integridade mesmo entre processos independentes.
        """
        lock_file_path = file_path.with_suffix(file_path.suffix + ".lock")
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
            yield
        finally:
            if f is not None:
                try:
                    if sys.platform == "win32":
                        f.seek(0)
                        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
                finally:
                    f.close()

# ==========================================
# 2. POOL DE THREADS DE TRABALHO (ESCALONADOR)
# ==========================================
class WorkerPool:
    def __init__(self, max_workers: int = 6):
        self.max_workers = max_workers
        self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SOWorker")
        self._active_jobs: Dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, name: str, fn: Callable, *args, **kwargs) -> Future:
        future = self.executor.submit(fn, *args, **kwargs)
        with self._lock:
            self._active_jobs[name] = future

        def _cleanup(fut):
            with self._lock:
                self._active_jobs.pop(name, None)
        future.add_done_callback(_cleanup)
        return future

    def get_status(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "max_workers": self.max_workers,
                "active_tasks_count": len(self._active_jobs),
                "active_tasks": list(self._active_jobs.keys()),
                "system_active_threads": threading.active_count()
            }

    def shutdown(self) -> None:
        self.executor.shutdown(wait=False)

lock_manager = ConcurrencyLockManager()
worker_pool = WorkerPool()

