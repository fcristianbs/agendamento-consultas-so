# Documentação do Código — Conceitos de SO

**Projeto:** `agendamento-consultas-so` (MedScheduler SO v2.0.0) · **Atualizado em:** 11/11/2026

---

## Introdução

Este documento explica, trecho a trecho, as funções do sistema que interagem com o Sistema Operacional: processos, threads, arquivos, travas, memória, chamadas de sistema e I/O. Cada seção mostra o código real do repositório, indica o arquivo e as linhas e explica o que o SO faz por baixo.

Os números de linha valem para o código atual da branch `main`. Se o código mudar, atualize as referências.

| Conceito de SO | Arquivo | Classes / funções principais |
|---|---|---|
| Processos e Threads | `concurrency.py`, `scheduler.py`, `app.py` | `WorkerPool.submit_job`, `run_multiprocess_tasks`, `multiprocess_schedule_worker`, `lifespan` |
| Sistema de Arquivos | `storage.py`, `os_adapter.py` | `save_json_atomic`, `load_json` (com restauração), `OSAdapter.__init__`, `safe_path`, `sanitize_filename` |
| Concorrência e Sincronização | `concurrency.py`, `scheduler.py` | `synchronized`, `file_lock`, `schedule_consulta`, CRUD com dupla trava |
| Gerência de Memória | `os_adapter.py` | `LRUCache` (deepcopy), `MemoryMonitor` (tracemalloc), `CleanupDaemon` |
| Chamadas de Sistema | `os_adapter.py`, `run.py` | `OSAdapter`, `apply_safe_permissions`, `detect_case_sensitive_fs`, `get_telemetry` |
| Entrada/Saída e Dispositivos | `storage.py`, `reports.py`, `app.py` | `setup_logger`, I/O assíncrono, `generate_pdf`, `export_csv`, `download_report` |
| Backup automático | `storage.py` | `AutoBackupManager` |

### O que mudou nesta versão do código

- **Multiprocessamento real:** novo endpoint `POST /api/test/multiprocess-simulation`, que dispara de 2 a 10 processos filhos (`spawn`) disputando a mesma vaga. Só a trava de arquivo do kernel os separa.
- **Jobs assíncronos rastreáveis:** relatórios e backup manual agora vão para o `WorkerPool` via `submit_job` e devolvem um `job_id`, consultado em `GET /api/jobs/{job_id}`.
- **I/O assíncrono:** leituras (`GET`) usam `load_json_async`, que roda a leitura do disco numa thread do pool sem bloquear o event loop.
- **Dupla trava em todo o CRUD:** médicos, pacientes, horários e consultas usam mutex em memória **e** trava de arquivo. Exclusões verificam integridade referencial.
- **Limpeza automática:** a nova thread daemon `SO-CleanupDaemon` purga o cache e apaga `.tmp`/`.lock` órfãos a cada 60 s. O botão de GC continua existindo.
- **Memória:** cache com cópia defensiva (`deepcopy`), locks em `WeakValueDictionary` e detecção de vazamento com `tracemalloc` (snapshot e comparação).
- **Filesystem por SO:** modo de diretórios nativos (`--os-paths`), sanitização de nomes reservados do Windows, limite `MAX_PATH`, teste empírico de case-sensitivity e CSV com `utf-8-sig`/`\r\n` no Windows.
- **Robustez:** restauração automática de JSON corrompido a partir do último backup, escrita atômica também no PDF e no ZIP, download protegido contra path traversal, log com fuso horário e nível configurável (`LOG_LEVEL`).

---

## 1. Processos e Threads

O sistema roda como um processo (um PID dado pelo kernel) que cria várias threads: as do servidor web, um pool de 6 threads de trabalho (`SOWorker_N`) e duas threads daemon (`SO-BackupDaemon` e `SO-CleanupDaemon`). Na demonstração de multiprocessamento, ele também cria processos filhos com PIDs próprios.

### 1.1 Ciclo de vida do processo (lifespan)

`src/web/app.py`, linhas 47–63:

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    worker_pool.start()
    memory_monitor.ensure_tracemalloc()
    app_logger.info(
        f"Sistema inicializado no SO {os_adapter.system} (PID: {os.getpid()}) | "
        f"Modo caminhos: {os_adapter.path_mode} | Dados: {os_adapter.data_dir}"
    )
    backup_manager.start()
    cleanup_daemon.start()
    try:
        yield
    finally:
        cleanup_daemon.stop()
        backup_manager.stop()
        worker_pool.shutdown()
        app_logger.info(f"Sistema encerrado com segurança (PID: {os.getpid()}).")
```

- `os.getpid()` faz a syscall `getpid()` (Linux/macOS) ou `GetCurrentProcessId()` (Windows). O PID muda a cada reinício, como mostra o log: `PID:8944` e depois `PID:2232`.
- Na subida, o processo liga o pool de threads, o `tracemalloc` e as duas threads daemon. No encerramento (`finally`), elas são paradas na ordem inversa, o que evita threads órfãs escrevendo em disco.
- O `lifespan` substitui o antigo `@app.on_event("startup")`, que foi depreciado no FastAPI.

### 1.2 Pool de threads com jobs rastreáveis (WorkerPool)

`src/concurrent/concurrency.py`, linhas 125–132 e 154–226 (trechos):

```python
self.executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="SOWorker")
...
def submit_job(self, name: str, fn: Callable, *args, **kwargs) -> str:
    job_id = str(uuid.uuid4())[:8]
    with self._lock:
        while len(self._jobs) >= self.max_history:
            self._jobs.popitem(last=False)
        self._jobs[job_id] = {"job_id": job_id, "name": name, "status": "pendente", ...}

    def _runner():
        thread_name = threading.current_thread().name
        tid = threading.get_ident()
        with self._lock:
            self._jobs[job_id]["status"] = "executando"
            self._jobs[job_id]["thread_name"] = thread_name
            self._jobs[job_id]["tid"] = tid
        try:
            result = fn(*args, **kwargs)
            with self._lock:
                self._jobs[job_id]["status"] = "concluído"
                self._jobs[job_id]["result"] = result
            return result
        except Exception as exc:
            with self._lock:
                self._jobs[job_id]["status"] = "erro"
                self._jobs[job_id]["error"] = str(exc)
            raise

    future = self.executor.submit(_runner)
    ...
    return job_id
```

- `ThreadPoolExecutor` cria até 6 threads reais do SO (`pthread_create` no Linux, `CreateThread` no Windows), nomeadas `SOWorker_0`, `SOWorker_1`... Elas são reaproveitadas, evitando o custo de criar uma thread por tarefa.
- `submit_job` devolve o `job_id` **na hora**. A requisição HTTP não espera o PDF ficar pronto: a tarefa entra numa fila e uma thread livre a executa quando o escalonador do SO der CPU a ela.
- Cada job passa pelos estados `pendente → executando → concluído/erro`, como o ciclo de vida de um processo (pronto → executando → terminado). O TID e o nome da thread que executou ficam registrados.
- O histórico é limitado a `max_history = 200` jobs. Ao passar disso, `popitem(last=False)` remove o mais antigo, para o dicionário não crescer sem limite.
- `_jobs` e `_active_jobs` são compartilhados entre threads, por isso todo acesso passa por `self._lock`. `get_job` (linha 228) devolve uma cópia (`deepcopy`).
- `start()` (linha 134) recria o executor se ele já tiver sido encerrado, o que permite reiniciar a aplicação no mesmo processo (ex.: nos testes).

Os endpoints que usam o pool estão em `src/web/app.py`, linhas 317–346:

```python
@app.post("/api/reports/pdf")
def generate_pdf_report():
    job_id = worker_pool.submit_job("relatorio_pdf", _task_generate_pdf)
    return {"job_id": job_id, "status": "pendente", "status_url": f"/api/jobs/{job_id}"}
```

A interface consulta `GET /api/jobs/{job_id}` até o status virar `concluído` e então mostra o link de download. O mesmo vale para `/api/reports/csv` e `/api/backups/create`.

### 1.3 Simulação de 10 threads simultâneas

`src/web/app.py`, linhas 465–534, endpoint `POST /api/test/concurrency-simulation`:

```python
with lock_manager.synchronized("schedule_lock"):
    with lock_manager.file_lock(scheduler_service.consultas_file):
        consultas = [c for c in file_manager.load_json(...) if not (<mesma vaga>)]
        file_manager.save_json_atomic(scheduler_service.consultas_file, consultas)
        consultas_cache.invalidate("all_consultas")

with ThreadPoolExecutor(max_workers=num_threads) as pool:
    futures = [pool.submit(tentar_agendar, i + 1) for i in range(num_threads)]
    for f in futures:
        ok, t_id, msg = f.result()
```

- Antes da disputa, a vaga de teste (médico 1, `2026-12-01`, 10:00) é limpa **dentro das duas travas**. Assim o próprio reset não cria uma condição de corrida com outra requisição.
- Dez threads tentam marcar a mesma vaga ao mesmo tempo. Com as travas da seção 3, o resultado é sempre 1 sucesso e 9 conflitos.
- `f.result()` faz a thread principal bloquear até cada thread terminar.

### 1.4 Simulação com processos independentes (multiprocessamento)

`src/concurrent/concurrency.py`, linhas 250–258:

```python
def run_multiprocess_tasks(worker_fn, items, max_workers=None):
    ctx = multiprocessing.get_context("spawn")
    workers = max_workers or len(items)
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
        return list(pool.map(worker_fn, items))
```

`src/core/scheduler.py`, linhas 147–195 (trecho):

```python
def multiprocess_schedule_worker(payload):
    pid = os.getpid()
    consultas_file = Path(payload["consultas_file"])
    fm = FileManager(default_encoding=payload.get("encoding", "utf-8"))
    time.sleep(0.03)  # aumenta a sobreposição entre os processos filhos

    with lock_manager.file_lock(consultas_file):
        consultas = fm.load_json(consultas_file, default=[])
        conflito_medico = any(<mesmo médico, data e horário, não cancelada>)
        if conflito_medico:
            return {"pid": pid, "sucesso": False, ...}
        consultas.append(nova)
        fm.save_json_atomic(consultas_file, consultas)
        return {"pid": pid, "sucesso": True, ...}
```

Endpoint: `POST /api/test/multiprocess-simulation?num_processes=N` (`app.py`, linhas 537–600).

- `ProcessPoolExecutor` cria processos filhos de verdade, cada um com seu próprio PID, espaço de endereçamento e interpretador Python. No Linux isso usa `fork`/`exec`; no Windows, `CreateProcess`.
- O contexto `spawn` inicia cada filho do zero (novo interpretador), em vez de copiar o processo pai. É o único modo disponível no Windows e evita problemas de `fork` dentro de um servidor com várias threads.
- A função do worker fica no nível do módulo porque o `spawn` precisa importá-la no processo filho (funções locais não podem ser serializadas com `pickle`).
- **Ponto central:** um `threading.Lock` não serve aqui, porque cada processo tem a própria memória. Por isso o worker usa **só** `file_lock`, a trava de arquivo do kernel, que é compartilhada entre processos (seção 3.2).
- O número de processos é limitado entre 2 e 10. O resultado devolve a lista de PIDs e, como no teste de threads, sempre 1 sucesso. Exemplo real do log: `5 processos (PIDs [21252, 3932, 15392, 10784, 19892]): 1 sucesso(s), 4 bloqueio(s) pelo Kernel File Lock.`

### 1.5 Threads do servidor web

- Rotas `def` (síncronas, como as de escrita) são executadas pelo FastAPI numa thread do pool interno do AnyIO. Por isso o log mostra `AnyIO worker thread` nas gravações.
- Rotas `async def` (as de leitura) rodam no event loop da `MainThread` e mandam a leitura do disco para o `WorkerPool` (seção 6.2).
- Assim, no mesmo PID aparecem `MainThread`, `AnyIO worker thread`, `SOWorker_N`, `SO-BackupDaemon` e `SO-CleanupDaemon`.

---

## 2. Sistema de Arquivos

Os dados ficam em arquivos JSON. A gravação é atômica: o arquivo nunca fica pela metade, mesmo se o processo cair no meio da escrita. Se mesmo assim um JSON estiver corrompido, ele é restaurado do backup mais recente.

### 2.1 Escolha e criação dos diretórios

`src/config/os_adapter.py`, linhas 61–95 e 113–138 (trechos):

```python
self._local_paths = self._build_local_paths(self.base_dir / "data")
self._os_paths = self._build_os_paths()

env_data_dir = os.environ.get("AGENDAMENTO_DATA_DIR", "").strip()
env_mode = os.environ.get("AGENDAMENTO_MODE", "").strip().lower()
...
for d in [self.data_dir, self.consultas_dir, self.relatorios_dir, self.logs_dir, self.backups_dir]:
    d.mkdir(parents=True, exist_ok=True)
    self.apply_safe_permissions(d)
```

```python
def _build_os_paths(self):
    if self.system == "Windows":
        appdata = Path(os.environ.get("APPDATA") or (Path.home() / "AppData" / "Roaming"))
        userprofile = Path(os.environ.get("USERPROFILE") or Path.home())
        data_root = appdata / "AgendamentoSO"
        relatorios = userprofile / "Documents" / "AgendamentoSO" / "relatorios"
    elif self.system == "Darwin":
        data_root = Path.home() / "Library" / "Application Support" / "AgendamentoSO"
        relatorios = Path.home() / "Documents" / "AgendamentoSO" / "relatorios"
    else:
        xdg_data = os.environ.get("XDG_DATA_HOME", "").strip()
        ...
        data_root = data_base / "agendamento-so"
```

Há dois modos de diretório, escolhidos no `run.py` (linhas 33–40):

| Modo | Como ativar | Dados | Relatórios |
|---|---|---|---|
| Local (padrão) | `python run.py` ou `--local` | `./data/` | `./data/relatorios/` |
| Nativo do SO | `python run.py --os-paths` | Windows: `%APPDATA%\AgendamentoSO`; macOS: `~/Library/Application Support/AgendamentoSO`; Linux: `$XDG_DATA_HOME/agendamento-so` (ou `~/.local/share`) | `Documents/AgendamentoSO/relatorios` do usuário |

- As variáveis de ambiente (`APPDATA`, `USERPROFILE`, `XDG_DATA_HOME`) são lidas do bloco de ambiente que o SO entrega ao processo.
- O `run.py` define `AGENDAMENTO_MODE` **antes** de importar o `os_adapter`, porque o adaptador decide os caminhos no momento em que é criado.
- `mkdir(parents=True, exist_ok=True)` chama `mkdir()` (POSIX) ou `CreateDirectoryW()` (Windows), criando as pastas intermediárias.
- O operador `/` do `pathlib` usa o separador certo do SO: `\` no Windows e `/` no Linux/macOS.

### 2.2 Escrita atômica (save_json_atomic)

`src/storage/storage.py`, linhas 110–129:

```python
def save_json_atomic(self, file_path: Path, data: Any) -> None:
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_file_path = file_path.with_suffix(".tmp")

    with open(temp_file_path, "w", encoding=self.default_encoding) as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())  # Chamada de sistema: descarrega buffer de I/O

    os.replace(temp_file_path, file_path)
    os_adapter.apply_safe_permissions(file_path)
```

Passo a passo:

1. `open(..., "w")` chama `open()`/`CreateFileW()` no kernel e recebe um descritor de arquivo.
2. `json.dump` escreve num arquivo temporário `.tmp`, nunca no arquivo real.
3. `f.flush()` entrega os bytes do buffer do Python ao kernel. Eles ainda podem estar só no cache de páginas, na RAM.
4. `os.fsync(f.fileno())` obriga o kernel a gravar no disco físico (`fsync()` no POSIX, `FlushFileBuffers()` no Windows).
5. `os.replace(tmp, final)` troca o arquivo antigo pelo novo numa operação atômica (`rename()` no POSIX, `MoveFileExW` no Windows). Quem lê vê a versão antiga inteira ou a nova inteira.
6. **Novo:** `apply_safe_permissions` aplica `0o600` ao arquivo final no Linux/macOS (seção 5.2).

O mesmo padrão `.tmp` + `os.replace` agora também é usado no CSV (`save_csv`, linhas 206–237), no PDF (`reports.py`, linhas 34 e 106) e no ZIP de backup (`storage.py`, linhas 257 e 264). Se o processo morrer no meio, sobra só um `.tmp` órfão, apagado pela `SO-CleanupDaemon` (seção 4.4).

### 2.3 Leitura com recuperação de corrupção (load_json)

`src/storage/storage.py`, linhas 159–178 e 131–157:

```python
def load_json(self, file_path, default=None, backup_dir=None):
    file_path = Path(file_path)
    if not file_path.exists():
        return copy.deepcopy(default) if default is not None else {}
    try:
        with open(file_path, "r", encoding="utf-8-sig") as f:
            return json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError) as err:
        _storage_logger.error(f"[SO Storage] JSON corrompido detectado em '{file_path}': {err}. ...")
        restored = self._restore_from_latest_backup(file_path, backup_dir=backup_dir)
        if restored is not None:
            return restored
        return copy.deepcopy(default) if default is not None else {}
```

- `exists()` consulta os metadados do arquivo (`stat()`).
- A leitura usa `utf-8-sig`, que aceita o arquivo com ou sem BOM. Isso evita erro quando o JSON foi editado no Bloco de Notas do Windows, que pode gravar BOM.
- Se o JSON estiver corrompido, `_restore_from_latest_backup` lista os `.zip` de `data/backups/` ordenados por `st_mtime` (do mais novo ao mais velho), abre o primeiro que contém o arquivo, regrava-o com escrita atômica e registra no log.
- `default` é devolvido como cópia (`deepcopy`), para que quem chamou não altere a lista padrão compartilhada.

### 2.4 Nomes de arquivo seguros (sanitize_filename e safe_path)

`src/config/os_adapter.py`, linhas 30–37 e 225–279:

```python
_INVALID_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" ...), *(f"LPT{i}" ...)}

def sanitize_filename(self, filename, replacement="_"):
    name = Path(filename).name.strip()
    name = _INVALID_FILENAME_CHARS_RE.sub(replacement, name)
    name = name.rstrip(" .")
    ...
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
```

```python
def safe_path(self, directory, filename):
    clean_name = self.sanitize_filename(filename)
    directory = Path(directory).resolve()
    effective_limit = (self.max_path_length - 6) if self.system == "Windows" else self.max_path_length
    candidate = directory / clean_name
    if len(str(candidate)) > effective_limit:
        ...  # encurta o nome preservando a extensão
```

- O Windows proíbe `< > : " / \ | ? *` em nomes de arquivo e trata `CON`, `NUL`, `COM1`... como dispositivos, mesmo com extensão (`CON.pdf` abriria o console). O código troca esses caracteres e prefixa nomes reservados com `_`.
- O Windows também remove sem aviso pontos e espaços no fim do nome; o código já os remove antes.
- `max_path_length` (linha 59) é 260 no Windows (`MAX_PATH`), 1024 no macOS e 4096 no Linux (`PATH_MAX`). No Windows, sobra margem de 6 caracteres para o sufixo `.tmp` e o terminador NUL.
- Relatórios e backups passam por `safe_path` antes de serem gravados.

### 2.5 Organização em disco (modo local)

| Pasta | Conteúdo | Quem escreve |
|---|---|---|
| `data/consultas/` | `pacientes.json`, `medicos.json`, `consultas.json` e os arquivos de trava `*.json.lock` | `save_json_atomic`, `file_lock` |
| `data/relatorios/` | `relatorio_consultas_AAAAMMDD_HHMMSS.pdf` / `.csv` | `generate_pdf`, `export_csv` |
| `data/backups/` | `backup_consultas_AAAAMMDD_HHMMSS.zip` (máx. 5) | `AutoBackupManager` |
| `data/logs/` | `sistema.log` e rotações `.1` a `.5` | `RotatingFileHandler` |

---

## 3. Concorrência e Sincronização

As operações de escrita usam duas camadas de trava: um mutex em memória, que sincroniza threads do mesmo processo, e uma trava de arquivo do kernel, que sincroniza processos diferentes.

### 3.1 Mutex em memória (synchronized)

`src/concurrent/concurrency.py`, linhas 45–74:

```python
def __init__(self):
    self._memory_locks: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()
    self._global_lock = threading.Lock()

def get_memory_lock(self, key: str) -> threading.Lock:
    with self._global_lock:
        lock = self._memory_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            self._memory_locks[key] = lock
        return lock

@contextlib.contextmanager
def synchronized(self, resource_name: str):
    lock = self.get_memory_lock(resource_name)
    acquired = lock.acquire(timeout=8.0)
    if not acquired:
        raise TimeoutError(f"Timeout aguardando lock para: {resource_name}")
    try:
        yield
    finally:
        lock.release()
```

- Cada recurso (`schedule_lock`, `medicos_write`, `pacientes_write`) tem seu próprio `threading.Lock`, um mutex do SO (futex no Linux, objetos de sincronização no Windows).
- **Novo:** os locks ficam num `WeakValueDictionary`. Quando nenhuma thread está usando um lock, ele pode ser coletado pelo GC, então a tabela de mutexes não cresce para sempre. Enquanto uma thread o segura, a variável local `lock` mantém a referência viva.
- `_global_lock` impede que duas threads criem dois locks diferentes para o mesmo nome.
- `acquire(timeout=8.0)`: se o lock estiver ocupado, o SO tira a thread da CPU e a deixa esperando até ele ser liberado ou passarem 8 s, o que evita espera infinita.
- O `finally` garante o `release()` mesmo com exceção. Aquisição e liberação aparecem no log em nível DEBUG (`[SO Mutex] Lock adquirido para 'schedule_lock'`).

### 3.2 Trava de arquivo do kernel (file_lock)

`src/concurrent/concurrency.py`, linhas 26–30 e 76–113:

```python
if sys.platform == "win32":
    import msvcrt
else:
    import fcntl
...
lock_file_path = file_path.with_suffix(file_path.suffix + ".lock")
f = open(lock_file_path, "a+b")
if sys.platform == "win32":
    f.seek(0)
    if f.tell() == 0 and os.path.getsize(lock_file_path) == 0:
        f.write(b"0")
        f.flush()
    f.seek(0)
    msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
else:
    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
yield
...
finally:
    if sys.platform == "win32":
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    f.close()
```

- O módulo importado depende do SO: `msvcrt` só existe no Windows e `fcntl` só em sistemas POSIX.
- **Agora há um `.lock` por arquivo de dados:** `consultas.json.lock`, `medicos.json.lock` e `pacientes.json.lock`. A trava é aplicada a esse arquivo auxiliar, e não ao JSON, porque o JSON é substituído por `os.replace` a cada gravação.
- **Windows:** `msvcrt.locking(fd, LK_LOCK, 1)` trava 1 byte (por isso o `seek(0)` e o byte `b"0"` num arquivo vazio). Internamente chama `LockFile` da API Win32 e tenta de novo por até ~10 s.
- **Linux/macOS:** `fcntl.flock(fd, LOCK_EX)` pede uma trava exclusiva. Se outro processo a tiver, o kernel deixa este processo esperando.
- A trava pertence ao descritor de arquivo, por isso funciona entre processos diferentes, o que é exatamente o que o teste multiprocesso (seção 1.4) demonstra. Se o processo morrer, o SO libera a trava ao fechar o descritor.

### 3.3 Seção crítica do agendamento

`src/core/scheduler.py`, linhas 583–648, em `schedule_consulta` (trecho):

```python
data_val = validate_data_str(consulta.data)
horario_val = validate_horario_str(consulta.horario)
validate_not_in_past(data_val, horario_val)

with lock_manager.synchronized("schedule_lock"):
    with lock_manager.file_lock(self.consultas_file):
        medicos = self.file_manager.load_json(self.medicos_file, default=[])
        ...  # médico e paciente existem? horário está na agenda do médico?
        consultas = self.file_manager.load_json(self.consultas_file, default=[])
        # Conflito 1: médico já ocupado
        # Conflito 2: paciente já tem consulta no mesmo horário
        consultas.append(nova)
        self.file_manager.save_json_atomic(self.consultas_file, consultas)
        consultas_cache.invalidate("all_consultas")
        return nova
```

- A sequência ler → verificar conflito → gravar precisa ser indivisível. Sem as travas, duas threads poderiam ver o horário livre e as duas marcarem (condição de corrida).
- As validações de formato e de "não agendar no passado" ficam **fora** da seção crítica, porque não dependem do arquivo. Assim a trava fica segura pelo menor tempo possível.
- Dentro da seção crítica, o código lê médicos e pacientes direto do disco (não do cache), para validar com o dado mais recente.
- Agora são verificados dois conflitos: médico ocupado e paciente com outra consulta no mesmo horário.
- `update_consulta` (remarcação), `cancel_consulta`, `conclude_consulta` e `delete_consulta` (linhas 650–752) usam as mesmas duas travas.

### 3.4 CRUD de médicos, pacientes e horários com dupla trava

`src/core/scheduler.py`, linhas 276–288 (create_medico) e 340–364 (delete_medico):

```python
def create_medico(self, medico: Medico):
    medico.horarios_disponiveis = normalize_horarios_list(medico.horarios_disponiveis)
    with lock_manager.synchronized("medicos_write"):
        with lock_manager.file_lock(self.medicos_file):
            medicos = self.file_manager.load_json(self.medicos_file, default=[])
            if any(os_adapter.names_equal(m["crm"], medico.crm) for m in medicos):
                raise ConflictError(f"CRM {medico.crm} já cadastrado.")
            ...

def delete_medico(self, medico_id: str):
    with lock_manager.synchronized("medicos_write"):
        with lock_manager.file_lock(self.medicos_file):
            ...
            with lock_manager.synchronized("schedule_lock"):
                with lock_manager.file_lock(self.consultas_file):
                    ativas = [c for c in consultas if c["medico_id"] == medico_id and c["status"] == "Agendada"]
                    if ativas:
                        raise ConflictError(...)
```

- **Mudança em relação à versão anterior:** todas as escritas de médicos, pacientes e horários (`create`, `update`, `delete`, `add_horario_medico`, `replace_horarios_medico`, `delete_horario_medico`) agora usam mutex **e** trava de arquivo, não só o mutex.
- **Integridade referencial:** médico ou paciente com consulta `Agendada` não pode ser excluído (HTTP 409).
- **Ordem fixa das travas:** quando duas travas são necessárias, a ordem é sempre `medicos_write`/`pacientes_write` → `schedule_lock`. Como nenhum caminho pega na ordem inversa, não há espera circular, uma das quatro condições de deadlock.
- Ao renomear um médico ou paciente, `_propagate_medico_nome` / `_propagate_paciente_nome` (linhas 327 e 520) atualizam o nome nas consultas, também dentro de `schedule_lock`.
- A comparação de CRM usa `os_adapter.names_equal`, que respeita a sensibilidade a maiúsculas do SO (seção 5.4).
- `_ensure_initial_data` (linhas 211–250) cria os JSON iniciais com o padrão "checar → travar → checar de novo", para dois processos que sobem juntos não criarem os dados duas vezes.

### 3.5 Mapeamento de erros para HTTP

`scheduler.py` (linhas 34–43) define `ValidationError`, `NotFoundError` e `ConflictError`, todas filhas de `ValueError`. `app.py` (linhas 93–100) converte cada uma em 400, 404 e 409. Os handlers globais (linhas 75–90) registram todo erro HTTP no log em nível ERROR, por exemplo: `[HTTP 400] PUT /api/consultas/con-001 -> Não é permitido agendar ou remarcar no passado`.

---

## 4. Gerência de Memória

A memória é tratada em quatro frentes: um cache LRU com limite de tamanho, um monitor que lê do SO quanto o processo ocupa, o `tracemalloc` para detectar vazamentos e a liberação de memória e temporários (manual e automática).

### 4.1 Cache LRU com TTL e cópia defensiva (LRUCache)

`src/config/os_adapter.py`, linhas 320–393 (trechos):

```python
def get(self, key: str):
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
        return copy.deepcopy(entry["value"])

def put(self, key: str, value: Any, ttl_seconds=None):
    with self._lock:
        if key in self._cache:
            self._cache.move_to_end(key)
        elif len(self._cache) >= self.capacity:
            evicted_key, _ = self._cache.popitem(last=False)
        self._cache[key] = {"value": copy.deepcopy(value), "expires_at": time.time() + ttl}
```

- **Objetivo:** evitar ler o disco a cada listagem. `list_medicos`, `list_pacientes` e `list_consultas` (e suas versões `_async`) consultam o cache antes de abrir o JSON (`scheduler.py`, linhas 253, 452 e 560).
- **LRU:** `move_to_end` marca a entrada como usada agora. Com o cache cheio (`capacity = 150`), `popitem(last=False)` remove a usada há mais tempo, a mesma ideia do algoritmo LRU de substituição de páginas.
- **TTL (300 s):** cada entrada expira, então dados velhos não ficam na RAM.
- **Novo, cópia defensiva:** `put` guarda uma cópia e `get` devolve outra (`deepcopy`). Sem isso, quem recebesse a lista poderia alterá-la e mudar o conteúdo do cache para todas as threads. O custo é mais alocação, trocado por isolamento entre threads.
- **Coerência:** toda gravação chama `consultas_cache.invalidate(...)` dentro da seção crítica.
- `get_stats()` mostra o tamanho e a taxa de acertos no painel.

### 4.2 Monitor de memória do processo (MemoryMonitor.get_info)

`src/config/os_adapter.py`, linhas 405–435:

```python
self.process = psutil.Process(os.getpid())
...
mem = self.process.memory_info()
sys_mem = psutil.virtual_memory()
if tracemalloc.is_tracing():
    cur, peak = tracemalloc.get_traced_memory()
return {
    "process_rss_mb": round(mem.rss / (1024 * 1024), 2),
    "process_vms_mb": round(mem.vms / (1024 * 1024), 2),
    "system_total_ram_mb": ..., "system_ram_percent": sys_mem.percent,
    "threads_count": self.process.num_threads(),
    "traced_current_kb": ..., "traced_peak_kb": ...,
}
```

- `memory_info()` lê do kernel (Linux: `/proc/<pid>/statm`; Windows: `GetProcessMemoryInfo`):
  - **RSS**: memória física (RAM) ocupada agora pelas páginas do processo.
  - **VMS**: tamanho do espaço de endereçamento virtual. É maior que o RSS, porque nem toda página virtual está carregada na RAM.
- `psutil.virtual_memory()` dá a RAM total do computador e o percentual em uso.
- `num_threads()` pergunta ao SO quantas threads o processo tem.
- **Novo:** `traced_current_kb` e `traced_peak_kb` mostram quanta memória o Python alocou no heap, medida pelo `tracemalloc`.
- Os dados saem em `GET /api/os/telemetry` (`app.py`, linha 404).

### 4.3 Detecção de vazamento com tracemalloc

`src/config/os_adapter.py`, linhas 437–505 (trecho):

```python
def compare_tracemalloc_snapshots(self, limit=10):
    with self._snapshot_lock:
        previous = self._last_snapshot
        current = tracemalloc.take_snapshot()
        self._last_snapshot = current
    ...
    diffs = current.compare_to(previous, "lineno")
    total_diff_bytes = sum(d.size_diff for d in diffs)
    ...
    "leak_suspected": total_diff_kb > 512,
```

- `tracemalloc` é ligado na subida do servidor (`ensure_tracemalloc`, chamado no `lifespan`). Ele registra cada bloco de memória que o Python aloca e a linha de código que o alocou.
- `GET /api/os/memory/snapshot` (`app.py`, linha 413) mostra as N linhas que mais ocupam memória.
- `POST /api/os/memory/compare` (linha 419) compara o snapshot atual com o anterior. Se o heap cresceu mais de 512 KB entre os dois, o sistema sinaliza suspeita de vazamento e mostra as linhas que mais cresceram.
- É a forma prática de cumprir o requisito "controle de vazamento de memória": em vez de só rodar o GC, mede-se se a memória volta a crescer.

### 4.4 Limpeza automática (CleanupDaemon) e GC manual

`src/config/os_adapter.py`, linhas 545–616 (trecho):

```python
def run_cleanup_once(self):
    purged = self.cache.purge_expired()
    now = time.time()
    for d in self.search_dirs:
        for pattern, is_tmp in [("*.tmp", True), ("*.lock", False)]:
            for f in d.glob(pattern):
                try:
                    if now - f.stat().st_mtime > self.max_orphan_age_seconds:
                        f.unlink()
                except Exception:
                    pass

def start(self):
    self._thread = threading.Thread(target=self._loop, name="SO-CleanupDaemon", daemon=True)
    self._thread.start()
```

- **Mudança em relação à versão anterior:** a limpeza agora é automática. A thread daemon `SO-CleanupDaemon` acorda a cada 60 s (mesmo padrão `Event.wait` do backup, seção 7.1), purga o cache vencido e apaga `.tmp` e `.lock` com mais de 60 s em `consultas/`, `relatorios/`, `backups/` e `logs/`.
- `f.stat().st_mtime` lê a data de modificação nos metadados; `f.unlink()` chama `unlink()`/`DeleteFileW()`.
- No Windows, um `.lock` aberto por outro processo não pode ser apagado (`PermissionError`). A exceção é ignorada e o arquivo fica.
- O log mostra o ciclo: `[TID:20796|SO-CleanupDaemon] ... Purga periódica: 1 entradas de cache expiradas, 0 .tmp e 0 .lock órfãos removidos.`

O botão de GC continua existindo, em `POST /api/os/gc` (`app.py`, linha 425), chamando `run_garbage_collection` (`os_adapter.py`, linhas 507–539):

1. Mede o RSS antes.
2. `purge_expired()` remove do cache as entradas vencidas.
3. `gc.collect()` encontra objetos com referências circulares inalcançáveis e devolve a memória.
4. Mede o RSS depois. A diferença pode ser 0, porque o alocador do Python costuma guardar a memória liberada para reuso em vez de devolvê-la ao SO.
5. Apaga `.tmp` e `.lock` órfãos, como a daemon.

> **Observação:** apagar um `.lock` só é seguro quando nenhum processo o está usando. No Windows o SO impede isso. No Linux/macOS o `unlink` funciona mesmo com o arquivo aberto, e o `.lock` nunca recebe escrita, então seu `st_mtime` fica antigo. Se a limpeza o apagar no instante em que outro processo segura a trava, um terceiro processo criaria um `.lock` novo e travaria outro inode. A chance é pequena, mas uma melhoria simples seria não apagar `*.json.lock`.

---

## 5. Chamadas de Sistema e Configuração por SO

A classe `OSAdapter` descobre em qual SO o programa está rodando e adapta caminhos, permissões, encoding, quebras de linha e comparação de nomes. É a camada que isola o resto do código das diferenças entre Windows, Linux e macOS.

### 5.1 Detecção do SO e parâmetros dependentes

`src/config/os_adapter.py`, linhas 46–59:

```python
self.system = platform.system()  # 'Windows', 'Linux', 'Darwin'
self.release = platform.release()
self.version = platform.version()
self.machine = platform.machine()
self.path_sep = os.sep
self.line_ending = "\r\n" if self.system == "Windows" else "\n"
self.default_encoding = "utf-8"
self.csv_encoding = "utf-8-sig" if self.system == "Windows" else "utf-8"
self.max_path_length = 260 if self.system == "Windows" else (1024 if self.system == "Darwin" else 4096)
```

- `platform.system()` usa `uname()` no Linux/macOS (Darwin é o kernel do macOS) e a API de versão do Windows.
- `os.sep`: `\` no Windows, `/` nos demais.
- `line_ending`: o Windows termina linhas com `\r\n` (CR+LF) e o Unix com `\n` (LF). Agora esse valor é usado de fato na exportação CSV.
- `default_encoding` fixa UTF-8 para JSON e log, porque o padrão do Windows (cp1252) é diferente.
- **Novo:** `csv_encoding` é `utf-8-sig` no Windows. O BOM no início do CSV faz o Excel reconhecer os acentos.
- **Novo:** `max_path_length` guarda o limite de caminho de cada SO (seção 2.4).

### 5.2 Permissões de arquivo (apply_safe_permissions)

`src/config/os_adapter.py`, linhas 161–188:

```python
if self.system in ("Linux", "Darwin"):
    if path.is_dir():
        os.chmod(path, 0o700)
    elif path.is_file():
        os.chmod(path, 0o600)
if path.exists():
    st = os.stat(path)
    info["mode_oct"] = oct(stat.S_IMODE(st.st_mode))
    if hasattr(st, "st_file_attributes"):
        info["st_file_attributes"] = st.st_file_attributes
        info["windows_attributes"] = self._decode_windows_attributes(st.st_file_attributes)
```

- `os.chmod` faz a syscall `chmod()`, que altera os bits de permissão no inode.
- `0o700` em pastas = `rwx------`; `0o600` em arquivos = `rw-------`. Só o dono lê e grava.
- **Novo:** a função agora é aplicada também a cada JSON gravado, ao log, aos relatórios e aos backups, não só às pastas.
- **Windows:** o NTFS usa ACLs, e `os.chmod` só liga ou desliga o atributo "somente leitura", o que impediria o `os.replace` dos JSON. Por isso, no Windows o código não altera nada: só lê `os.stat().st_file_attributes` (`GetFileAttributesExW`) e traduz os bits (`ARCHIVE`, `READONLY`, `HIDDEN`...) para o log e a telemetria.

### 5.3 Teste de case-sensitivity do filesystem

`src/config/os_adapter.py`, linhas 140–159 e 245–254:

```python
probe_upper = test_dir / f".SO_CaseProbe_{os.getpid()}_{threading.get_ident()}.tmp"
probe_lower = test_dir / probe_upper.name.lower()
probe_upper.write_text("probe", encoding="ascii")
return not probe_lower.exists()
```

- O código cria um arquivo com letras maiúsculas e pergunta ao SO se o mesmo nome em minúsculas existe. Se existir, o filesystem não diferencia maiúsculas (NTFS e APFS padrão). No ext4 do Linux, não existe.
- O nome inclui PID e TID para dois processos não usarem o mesmo arquivo de teste.
- `names_equal` usa o resultado para comparar CRMs: no Windows/macOS, `casefold()` faz `12345-sp` e `12345-SP` serem o mesmo.

### 5.4 Telemetria do SO (get_telemetry) e tela inicial

`src/config/os_adapter.py`, linhas 295–314:

```python
return {
    "os_name": self.system, "os_release": self.release, "architecture": self.machine,
    "path_separator": self.path_sep, "line_ending": repr(self.line_ending),
    "default_encoding": self.default_encoding, "csv_encoding": self.csv_encoding,
    "current_user": getpass.getuser(), "cpu_cores": os.cpu_count() or 1,
    "path_mode": self.path_mode, "paths": self.get_paths_summary(),
    "filesystem_case_sensitive": self.is_case_sensitive,
    "max_path_length": self.max_path_length,
    "data_dir_security": self.get_file_security_info(self.data_dir),
}
```

- `getpass.getuser()` lê o usuário logado (variáveis `USERNAME`/`USER` ou `getpwuid(getuid())` no Unix).
- `os.cpu_count()` pergunta ao SO quantos núcleos lógicos existem.
- `get_file_security_info` usa `os.stat` para mostrar modo, UID e atributos da pasta de dados.
- `run.py` (linhas 46–67) imprime esses valores na inicialização, incluindo o modo de diretórios ativo e os caminhos dos dois modos.

---

## 6. Entrada/Saída e Dispositivos

O sistema faz I/O em quatro pontos além dos JSON: o log, a leitura assíncrona, os relatórios PDF/CSV e o download desses relatórios pela rede.

### 6.1 Sistema de logging (setup_logger)

`src/storage/storage.py`, linhas 34–94 (trechos):

```python
class ProcessThreadFormatter(logging.Formatter):
    def formatTime(self, record, datefmt=None):
        dt = datetime.fromtimestamp(record.created).astimezone()
        ...

env_level_name = os.environ.get("LOG_LEVEL", "DEBUG").strip().upper()
configured_level = getattr(logging, env_level_name, logging.DEBUG)
...
log_format = (
    "[%(asctime)s %(levelname)s] [PID:%(process)d] [TID:%(thread)d|%(threadName)s] "
    "[%(filename)s:%(lineno)d] - %(message)s"
)
formatter = ProcessThreadFormatter(fmt=log_format, datefmt="%Y-%m-%d %H:%M:%S %z")
file_handler = RotatingFileHandler(filename=str(log_file_path), maxBytes=2 * 1024 * 1024,
                                   backupCount=5, encoding=encoding)
os_adapter.apply_safe_permissions(log_file_path)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.INFO)
```

- **Dois dispositivos de saída:** o arquivo `sistema.log` e o terminal (`sys.stdout`, descritor 1).
- **Novo, fuso horário do SO:** `astimezone()` aplica o fuso configurado no sistema e `%z` o imprime (`-0300`).
- **Novo, nível configurável:** a variável de ambiente `LOG_LEVEL` (DEBUG, INFO, ERROR...) define o nível do arquivo. O console fica sempre em INFO.
- **PID e TID:** `%(process)d` e `%(thread)d` mostram qual processo e qual thread fez cada operação.
- **Rotação:** acima de 2 MB, `sistema.log` vira `.1` (até `.5`) e um arquivo novo é aberto.
- O logging do Python é thread-safe: cada handler tem um lock interno.
- `GET /api/os/logs?level=ERROR` (`app.py`, linhas 440–459) filtra as linhas pelo nível, lendo o arquivo numa thread separada (`asyncio.to_thread`).

Exemplo real de `data/logs/sistema.log`:

```
[2026-11-11 20:07:05 -0300 INFO] [PID:8944] [TID:17248|AnyIO worker thread] [app.py:321] - [SO WorkerPool] Job 02e2be69 (relatorio_pdf) enfileirado.
[2026-11-11 20:07:05 -0300 INFO] [PID:8944] [TID:13704|SOWorker_0] [reports.py:108] - [SO I/O] Relatório PDF gerado: relatorio_consultas_20261111_200705.pdf
[2026-11-11 20:10:45 -0300 INFO] [PID:8944] [TID:3028|SO-BackupDaemon] [storage.py:267] - [SO Backup] Snapshot ZIP criado: backup_consultas_20261111_201045.zip
[2026-11-11 20:21:04 -0300 DEBUG] [PID:8944] [TID:6712|AnyIO worker thread] [concurrency.py:99] - [SO FileLock] Trava de kernel adquirida em 'consultas.json.lock'
[2026-11-11 20:21:05 -0300 INFO] [PID:8944] [TID:6712|AnyIO worker thread] [app.py:589] - [SO Multiprocessamento] 5 processos (PIDs [21252, 3932, 15392, 10784, 19892]): 1 sucesso(s), 4 bloqueio(s) pelo Kernel File Lock.
```

O mesmo PID (8944) aparece com quatro threads diferentes: a requisição enfileira o PDF (TID 17248), uma thread do pool o gera (TID 13704, `SOWorker_0`), a daemon faz o backup (TID 3028) e a simulação multiprocesso mostra PIDs filhos distintos.

### 6.2 Leitura e escrita assíncronas

`src/storage/storage.py`, linhas 180–204, e `src/web/app.py`, linha 120:

```python
async def load_json_async(self, file_path, default=None, backup_dir=None, executor=None):
    if executor is not None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(executor, self.load_json, file_path, default, backup_dir)
    return await asyncio.to_thread(self.load_json, file_path, default, backup_dir)
```

```python
@app.get("/api/medicos")
async def list_medicos():
    return await scheduler_service.list_medicos_async(executor=worker_pool.executor)
```

- A leitura de arquivo é uma chamada bloqueante: a thread fica parada até o disco responder. Se fosse feita direto no event loop, travaria todas as outras requisições.
- `run_in_executor` manda a leitura para uma thread do `WorkerPool`. O event loop fica livre e retoma a rota quando o resultado chega.
- Todas as rotas `GET` de médicos, pacientes, consultas e horários usam esse caminho. `save_json_async` existe para gravação com o mesmo mecanismo.

### 6.3 Relatório PDF (generate_pdf)

`src/reports/reports.py`, linhas 27–112 (trechos):

```python
filename = os_adapter.sanitize_filename(f"relatorio_consultas_{timestamp}.pdf")
file_path = os_adapter.safe_path(output_dir, filename)
temp_path = file_path.with_suffix(".tmp")
doc = SimpleDocTemplate(str(temp_path), pagesize=letter, ...)
...
f"Emitido em: {hora_str} | SO: {os_name} | PID: {os.getpid()} | "
f"Thread: {threading.current_thread().name} (TID: {threading.get_ident()})"
...
doc.build(story)
os.replace(temp_path, file_path)
os_adapter.apply_safe_permissions(file_path)
```

- O nome passa por `sanitize_filename` e `safe_path` (seção 2.4).
- **Novo:** o PDF é montado num `.tmp` e só depois movido com `os.replace`, então um download nunca pega um PDF pela metade.
- O cabeçalho mostra o SO, o PID e a thread que gerou o arquivo; como ele roda no `WorkerPool`, aparece `SOWorker_N`.

### 6.4 Relatório CSV (export_csv e save_csv)

`src/reports/reports.py`, linhas 115–137, e `src/storage/storage.py`, linhas 206–237:

```python
target_encoding = encoding or os_adapter.csv_encoding
target_terminator = lineterminator or os_adapter.line_ending
with open(temp_file, "w", newline="", encoding=target_encoding) as f:
    writer = csv.DictWriter(f, fieldnames=headers, lineterminator=target_terminator)
    ...
    f.flush()
    os.fsync(f.fileno())
os.replace(temp_file, file_path)
```

- **Novo:** o CSV usa o encoding e a quebra de linha do SO: `utf-8-sig` + `\r\n` no Windows e `utf-8` + `\n` no Linux/macOS.
- `newline=""` impede que o Python converta as quebras de novo; quem decide é o `lineterminator`.
- A gravação é atômica, como na seção 2.2.

### 6.5 Download do relatório (download_report)

`src/web/app.py`, linhas 349–376:

```python
base_dir = os_adapter.relatorios_dir.resolve()
if (not filename or "\x00" in filename or ".." in Path(filename).parts
        or Path(filename).name != filename):
    raise HTTPException(status_code=400, detail="Caminho de arquivo inválido (path traversal bloqueado).")
target_path = (base_dir / filename).resolve()
if not target_path.is_relative_to(base_dir) or target_path == base_dir:
    raise HTTPException(status_code=400, detail="Acesso negado: caminho fora do diretório de relatórios.")
...
return FileResponse(path=str(target_path), filename=target_path.name, media_type=media_type)
```

- **Novo, proteção contra path traversal:** um nome como `../consultas/pacientes.json` poderia ler qualquer arquivo do disco. O código recusa `..`, byte nulo e barras, resolve o caminho real (`realpath`, seguindo links) e confere se ele continua dentro da pasta de relatórios.
- O tipo do conteúdo agora é definido pela extensão (`application/pdf` ou `text/csv`).
- `FileResponse` lê o arquivo em blocos e envia pelo socket, sem carregar tudo na memória.
- `list_reports` (linhas 379–393) usa `stat()` para obter tamanho (`st_size`) e data (`st_mtime`).

---

## 7. Backup Automático

Uma thread daemon acorda a cada 300 s (5 min), compacta os JSON de `data/consultas/` num `.zip` e mantém só os 5 backups mais recentes. Esses backups também são usados para restaurar JSON corrompido (seção 2.3).

### 7.1 A thread daemon (start, _loop, stop)

`src/storage/storage.py`, linhas 294–314:

```python
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
```

- `threading.Thread(...).start()` cria uma thread do SO com o nome `SO-BackupDaemon`.
- `daemon=True`: a thread não impede o processo de terminar.
- `_stop_event.wait(timeout=300)` deixa a thread dormindo sem gastar CPU. Ela acorda pelo tempo (faz o backup) ou por `stop()` (encerra na hora).
- **Mudança:** erros no backup agora são registrados no log em nível ERROR, em vez de ignorados.
- `start()` e `stop()` são chamados no `lifespan` (seção 1.1).

### 7.2 Criação do backup e rotação

`src/storage/storage.py`, linhas 252–278:

```python
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
```

- `glob("*.*")` lista o diretório (`opendir`/`readdir` no POSIX, `FindFirstFile`/`FindNextFile` no Windows). `.tmp` e `.lock` são ignorados.
- **Novo:** o ZIP também é gravado com `.tmp` + `os.replace`, então a rotina de restauração nunca encontra um ZIP incompleto.
- `ZIP_DEFLATED` comprime os dados.
- `_rotate_old_backups` ordena por `st_mtime` e apaga com `unlink()` os mais antigos até restarem 5.
- O backup manual (`POST /api/backups/create`) agora vai para o `WorkerPool` como job (seção 1.2), e `GET /api/backups/list` lista os existentes.

---

## 8. Resumo das Chamadas de Sistema

| Função Python | Linux / macOS | Windows | Onde é usada | Para quê |
|---|---|---|---|---|
| `os.getpid()` | `getpid()` | `GetCurrentProcessId()` | app.py 52; scheduler.py 153; os_adapter.py 146, 406; reports.py 71 | Identificar o processo |
| `ProcessPoolExecutor` (spawn) | `fork()`/`exec()` | `CreateProcess()` | concurrency.py 255–258 | Criar processos filhos |
| `threading.Thread().start()` / `ThreadPoolExecutor` | `pthread_create()` | `CreateThread()` | storage.py 306; os_adapter.py 608; concurrency.py 128 | Criar threads |
| `threading.get_ident()` | `pthread_self()` | `GetCurrentThreadId()` | concurrency.py 179; reports.py 72 | Identificar a thread (TID) |
| `threading.Lock.acquire()` | futex / `pthread_mutex` | objetos de sincronização Win32 | concurrency.py 66; os_adapter.py (cache) | Exclusão mútua entre threads |
| `threading.Event.wait()` | futex / variável de condição | `WaitForSingleObject` | storage.py 296; os_adapter.py 598 | Thread dormindo sem gastar CPU |
| `fcntl.flock(fd, LOCK_EX)` | `flock()` | — | concurrency.py 98, 108 | Trava de arquivo entre processos |
| `msvcrt.locking(fd, LK_LOCK, 1)` | — | `LockFile()` / `UnlockFile()` | concurrency.py 95, 106 | Trava de arquivo entre processos |
| `open()` / `close()` | `open()` / `close()` | `CreateFileW()` / `CloseHandle()` | storage.py 122, 168, 225; concurrency.py 87 | Abrir e fechar descritores |
| `os.fsync(fd)` | `fsync()` | `FlushFileBuffers()` | storage.py 125, 231 | Forçar gravação no disco |
| `os.replace(a, b)` | `rename()` | `MoveFileExW()` | storage.py 127, 233, 264; reports.py 106 | Substituição atômica |
| `Path.mkdir()` | `mkdir()` | `CreateDirectoryW()` | os_adapter.py 94; storage.py 119 | Criar diretórios |
| `os.chmod(path, mode)` | `chmod()` | atributo somente leitura | os_adapter.py 174, 177 | Permissões |
| `os.stat()` / `Path.stat()` / `exists()` | `stat()` | `GetFileAttributesExW()` | os_adapter.py 180, 212; storage.py 139, 162; app.py 389 | Metadados, modo e atributos |
| `Path.unlink()` | `unlink()` | `DeleteFileW()` | os_adapter.py 157, 523, 578; storage.py 275 | Apagar temporários, locks e backups antigos |
| `Path.glob()` | `opendir()` / `readdir()` | `FindFirstFileW()` / `FindNextFileW()` | storage.py 138, 260; os_adapter.py 520, 575 | Listar diretórios |
| `Path.resolve()` | `realpath()` | `GetFinalPathNameByHandleW()` | os_adapter.py 62; app.py 352, 361 | Caminho real (anti path traversal) |
| `os.environ.get()` | bloco de ambiente (`environ`) | `GetEnvironmentVariableW()` | os_adapter.py 70–71, 116–126; storage.py 56 | Modo de diretórios, pastas do usuário, LOG_LEVEL |
| `platform.system()` / `release()` | `uname()` | API de versão / registro | os_adapter.py 48–51 | Detectar o SO |
| `os.cpu_count()` | `sysconf(_SC_NPROCESSORS_ONLN)` | `GetSystemInfo()` | os_adapter.py 307 | Núcleos de CPU |
| `psutil.Process().memory_info()` | `/proc/<pid>/statm` (Linux), `task_info` (macOS) | `GetProcessMemoryInfo()` | os_adapter.py 416 | RSS e VMS do processo |
| `psutil.virtual_memory()` | `/proc/meminfo` (Linux), `sysctl` (macOS) | `GlobalMemoryStatusEx()` | os_adapter.py 417 | RAM do sistema |
| `getpass.getuser()` | `getpwuid(getuid())` | `GetUserNameW()` / `USERNAME` | os_adapter.py 306 | Usuário logado |
| `datetime.astimezone()` | `localtime()` / `TZ` | `GetTimeZoneInformation()` | storage.py 42 | Fuso horário do log |
