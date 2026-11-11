# RELATÓRIO TÉCNICO
## Sistema de Agendamento de Consultas Médicas com Foco em Sistemas Operacionais

**Disciplina:** Sistemas Operacionais  
**Data:** Novembro de 2026  
**Entrega:** 18/11/2026  
**Modalidade:** Trabalho Prático Avaliativo  

---

### Sumário
1. Introdução e Objetivos do Projeto
2. Arquitetura Geral do Sistema
3. Demonstração dos Conceitos de Sistemas Operacionais
4. Análise de Decisões Técnicas
5. Demonstração de Funcionamento, Capturas e Resultados
6. Dificuldades Encontradas e Soluções Adotadas
7. Conclusão

---

## 1. Introdução e Objetivos do Projeto

O presente projeto consiste no desenvolvimento de um **Sistema Completo de Agendamento de Consultas Médicas (CRUD)** em Python 3.10+, FastAPI, Jinja2, ReportLab e `psutil`, concebido para ilustrar e exercitar na prática os conceitos fundamentais de **Sistemas Operacionais** em ambientes **Windows, Linux e macOS**.

Ao invés de delegar toda a infraestrutura a um banco de dados externo opaco, a aplicação interage diretamente com mecanismos do sistema operacional hospedeiro, tornando visíveis e auditáveis:
- Criação, identificação e escalonamento de **Processos e Threads** (com `WorkerPool`, threads daemon e `multiprocessing`);
- Organização multiplataforma, permissões, sanitização e integridade atômica no **Sistema de Arquivos**;
- Gestão de **Memória Principal (RAM)** com Cache LRU defensivo, métricas RSS/VMS (`psutil`), rastreamento de alocações (`tracemalloc`), `WeakValueDictionary` e coleta de lixo;
- Controle de **Concorrência** entre threads e entre processos distintos via exclusão mútua e travas de arquivo no kernel;
- Uso explícito de **Chamadas de Sistema (*System Calls*)** portáveis entre Win32 e POSIX;
- Operações de **Entrada e Saída (I/O)** síncronas e assíncronas em disco e dispositivo de log.

---

## 2. Arquitetura Geral do Sistema

O projeto segue rigorosamente a organização **1 arquivo por módulo**, separando responsabilidades de negócio, concorrência, armazenamento, relatórios, interface web e adaptação ao SO:

```text
+-----------------------------------------------------------------------+
|                CAMADA DE APRESENTAÇÃO (src/web/templates/index.html)  |
|   Abas: Consultas | Médicos & Pacientes | Relatórios/Backups | SO     |
+-----------------------------------+-----------------------------------+
                                    | HTTP / JSON (REST + Polling Jobs)
                                    v
+-----------------------------------------------------------------------+
|                  SERVIDOR WEB & API (src/web/app.py)                  |
|   FastAPI + Lifespan + Exception Handlers Globais + Path Traversal    |
+-----+--------------------+--------------------+-------------------+---+
      |                    |                    |                   |
      v                    v                    v                   v
+-------------+   +------------------+   +---------------+   +----------+
|  NÚCLEO     |   |  CONCORRÊNCIA    |   |  RELATÓRIOS   |   |  DAEMONS |
| scheduler.py|   |  concurrency.py  |   |  reports.py   |   |  DE SO   |
| - CRUDs     |   | - Mutex WeakRef  |   | - PDF binario |   | - Backup |
| - Conflitos |   | - File Lock SO   |   | - CSV nativo  |   | - Cleanup|
| - Horarios  |   | - WorkerPool     |   +-------+-------+   +----+-----+
| - MP Worker |   | - Spawn Process  |           |                |
+------+------+   +--------+---------+           |                |
       |                   |                     |                |
       +-------------------+---------------------+----------------+
                           |
                           v
+-----------------------------------------------------------------------+
|               PERSISTÊNCIA E LOGGING (src/storage/storage.py)         |
|  FileManager (fsync + os.replace + recovery) | RotatingFileHandler    |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|         ADAPTAÇÃO AO SO E MEMÓRIA (src/config/os_adapter.py)          |
|  OSAdapter (paths/perms/FS) | LRUCache (deepcopy) | MemoryMonitor     |
+-----------------------------------+-----------------------------------+
                                    |
                                    v
+-----------------------------------------------------------------------+
|         KERNEL DO SO (Windows NT / Linux POSIX / macOS Darwin)        |
|  msvcrt.locking | fcntl.flock | os.fsync | os.replace | chmod | stat  |
+-----------------------------------------------------------------------+
```

### Estrutura Real de Arquivos do Repositório

```text
agendamento-consultas-so/
├── run.py                          # Inicialização (--local / --os-paths) e resumo de SO
├── requirements.txt                # Dependências Python
├── README.md                       # Visão geral e guia rápido
├── src/
│   ├── config/os_adapter.py        # OSAdapter, LRUCache, MemoryMonitor, CleanupDaemon
│   ├── storage/storage.py          # ProcessThreadFormatter, setup_logger, FileManager, AutoBackupManager
│   ├── concurrent/concurrency.py   # ConcurrencyLockManager, WorkerPool, run_multiprocess_tasks
│   ├── core/scheduler.py           # Modelos Pydantic, SchedulerService, multiprocess_schedule_worker
│   ├── reports/reports.py          # generate_pdf, export_csv
│   └── web/
│       ├── app.py                  # Servidor FastAPI, lifespan, endpoints CRUD, jobs e telemetria
│       └── templates/index.html    # Interface web completa
├── data/                           # Diretório local padrão (--local)
│   ├── consultas/                  # medicos.json, pacientes.json, consultas.json
│   ├── relatorios/                 # PDFs e CSVs gerados
│   ├── backups/                    # Snapshots .zip rotacionados
│   └── logs/sistema.log            # Log rotativo do sistema
├── docs/
│   ├── manual_instalacao.md
│   ├── relatorio_tecnico.md
│   ├── gerar_pdf.py
│   └── relatorio_tecnico.pdf
└── tests/
    └── test_concurrency.py         # 16 testes automatizados isolados em tempfile
```

---

## 3. Demonstração dos Conceitos de Sistemas Operacionais

### 3.1 Processos e Threads
**Como o sistema lida com múltiplas operações simultâneas?**
- **Processo Principal e PID:** Ao iniciar por `run.py`, o servidor Uvicorn/FastAPI executa em um processo identificado pelo PID fornecido pelo kernel (`os.getpid()`).
- **Pool de Threads (`WorkerPool` em `src/concurrent/concurrency.py`):** Utiliza `ThreadPoolExecutor(max_workers=6, thread_name_prefix="SOWorker")`. A geração de relatórios PDF/CSV e os backups manuais (`generate_pdf_report`, `generate_csv_report`, `trigger_backup` em `src/web/app.py`) são submetidos via `WorkerPool.submit_job()`, devolvendo um `job_id` imediato enquanto a tarefa roda em background e registra o nome da thread (`SOWorker_X`) e o `TID` (`threading.get_ident()`), consultáveis por `GET /api/jobs/{job_id}`.
- **Threads Daemon Periódicas:**
  - `AutoBackupManager` (`src/storage/storage.py`, thread `SO-BackupDaemon`): gera snapshots `.zip` periodicamente sem bloquear requisições.
  - `CleanupDaemon` (`src/config/os_adapter.py`, thread `SO-CleanupDaemon`): purga entradas expiradas do cache e remove `.tmp`/`.lock` órfãos com mais de 60 segundos a cada ciclo de 60 segundos.
- **Múltiplos Processos (`multiprocessing`):** `run_multiprocess_tasks()` (`src/concurrent/concurrency.py`) utiliza `multiprocessing.get_context("spawn")` com `ProcessPoolExecutor` para disparar processos independentes executando `multiprocess_schedule_worker()` (`src/core/scheduler.py`), exibindo PIDs distintos na rota `POST /api/test/multiprocess-simulation`.

### 3.2 Sistema de Arquivos
**Como os dados são organizados, protegidos e acessados?**
- **Resolução Dependente de SO (`OSAdapter._build_os_paths` e `_build_local_paths` em `src/config/os_adapter.py`):** Por padrão usa `./data` (`--local`), podendo alternar para os diretórios padrão do sistema via `AGENDAMENTO_MODE=os` ou `--os-paths`:
  - Windows: `%APPDATA%\AgendamentoSO` e `%USERPROFILE%\Documents\AgendamentoSO\relatorios`;
  - Linux: `$XDG_DATA_HOME/agendamento-so` (ou `~/.local/share/agendamento-so`) e `$XDG_DOCUMENTS_DIR/AgendamentoSO/relatorios`;
  - macOS: `~/Library/Application Support/AgendamentoSO` e `~/Documents/AgendamentoSO/relatorios`.
- **Escrita Atômica e Recuperação (`FileManager.save_json_atomic` e `load_json` em `src/storage/storage.py`):** Grava primeiro em `.tmp`, força descarga física via `os.fsync(f.fileno())` e substitui atomicamente com `os.replace()`. Em caso de JSON corrompido (`JSONDecodeError`), `FileManager._restore_from_latest_backup()` registra `ERROR` e restaura a partir do `.zip` mais recente.
- **Portabilidade de Nomes e Case-Sensitivity (`OSAdapter` em `src/config/os_adapter.py`):**
  - `sanitize_filename()` remove caracteres inválidos no Windows (`<>:"/\|?*`) e neutraliza nomes reservados (`CON`, `PRN`, `AUX`, `NUL`, `COM1`..`COM9`, `LPT1`..`LPT9`);
  - `safe_path()` respeita o limite `MAX_PATH` (260 caracteres no Windows);
  - `detect_case_sensitive_fs()` testa empiricamente com arquivo temporário se o filesystem diferencia maiúsculas/minúsculas;
  - `apply_safe_permissions()` aplica `0o700`/`0o600` no POSIX e inspeciona `st_file_attributes` via `os.stat()` no Windows.
- **Segurança no Download (`download_report` em `src/web/app.py`):** Bloqueia tentativas de *Path Traversal* validando `Path.is_relative_to()` contra `relatorios_dir`.

### 3.3 Gerenciamento de Memória
**Como a memória é alocada, monitorada e liberada?**
- **RSS vs VMS (`MemoryMonitor.get_info` em `src/config/os_adapter.py`):** Consulta o kernel via `psutil.Process(os.getpid()).memory_info()`, expondo memória física residente (**RSS**) e espaço de endereçamento virtual (**VMS**).
- **Cache LRU Defensivo (`LRUCache` em `src/config/os_adapter.py`):** Baseado em `OrderedDict` com capacidade máxima (150), TTL (300 s) e evicção LRU (`popitem(last=False)`). Os métodos `get()` e `put()` utilizam `copy.deepcopy()`, impedindo mutação acidental do cache por referência.
- **Rastreamento de Alocações (`tracemalloc`):** `MemoryMonitor.take_tracemalloc_snapshot()` (`GET /api/os/memory/snapshot`) lista as top 10 alocações por arquivo e linha; `MemoryMonitor.compare_tracemalloc_snapshots()` (`POST /api/os/memory/compare`) compara snapshots para detectar vazamentos.
- **Controle de Locks e Coleta de Lixo:** `ConcurrencyLockManager` (`src/concurrent/concurrency.py`) usa `weakref.WeakValueDictionary` em `_memory_locks` para liberar mutexes não referenciados; `MemoryMonitor.run_garbage_collection()` (`POST /api/os/gc`) executa `gc.collect()`, purga cache expirado e limpa temporários órfãos.

### 3.4 Concorrência e Sincronização
**Como são evitados conflitos no acesso aos recursos?**
- **Sincronização em Dois Níveis (`ConcurrencyLockManager` em `src/concurrent/concurrency.py`):**
  1. `synchronized(resource_name)`: mutex em memória (`threading.Lock` com timeout de 8 s) para isolar threads dentro do mesmo processo;
  2. `file_lock(file_path)`: trava exclusiva em nível de kernel (`msvcrt.locking` no Windows, `fcntl.flock` no POSIX) aplicada em **todas** as escritas de `consultas.json`, `medicos.json` e `pacientes.json` em `SchedulerService` (`src/core/scheduler.py`), além do reset da vaga em `simulate_concurrency()` (`src/web/app.py`).
- **Prevenção de Condições de Corrida:** Em `SchedulerService.schedule_consulta()` e `update_consulta()`, a verificação de conflito de médico e paciente e a gravação atômica ocorrem dentro da seção crítica protegida por ambos os locks. Na disputa entre processos (`multiprocess_schedule_worker`), processos distintos sem memória compartilhada são serializados pelo `file_lock` do kernel, garantindo apenas 1 agendamento bem-sucedido.

### 3.5 Chamadas de Sistema (System Calls)
**Quais APIs e syscalls do SO são utilizadas?**
- **Controle de Arquivos e Travas:** `os.fsync()`, `os.replace()`, `os.stat()` (incluindo `st_file_attributes` no Windows), `os.chmod()` (`0o700`/`0o600` no POSIX), `msvcrt.locking()` (`LK_LOCK`/`LK_UNLCK` no Win32) e `fcntl.flock()` (`LOCK_EX`/`LOCK_UN` no POSIX).
- **Processos, Threads e Ambiente:** `os.getpid()`, `os.cpu_count()`, `threading.get_ident()`, `platform.system()`, `platform.release()`, `platform.machine()`, `getpass.getuser()`, além de criação de processos filhos via `multiprocessing.get_context("spawn")`.

### 3.6 Operações de Entrada e Saída (I/O)
**Como são realizadas as operações de leitura e escrita?**
- **I/O Assíncrono (`FileManager.load_json_async` e `save_json_async` em `src/storage/storage.py`):** Executam I/O de disco fora do *event loop* principal usando `run_in_executor` no `WorkerPool` (ou `asyncio.to_thread`), utilizados nas rotas `async` de leitura em `src/web/app.py`.
- **Relatórios Dependentes de SO (`src/reports/reports.py` e `FileManager.save_csv`):**
  - `generate_pdf()`: grava stream binário PDF via ReportLab em `.tmp` e substitui atomicamente com `os.replace()`;
  - `export_csv()`: grava texto tabular com `lineterminator` nativo (`\r\n` no Windows, `\n` em POSIX) e encoding `utf-8-sig` no Windows (compatível com Excel) ou `utf-8` em Linux/macOS. Leitura de JSON e logs usa `utf-8-sig` tolerante a BOM.
- **Dispositivo de Log (`setup_logger` e `ProcessThreadFormatter` em `src/storage/storage.py`):** `RotatingFileHandler` (2 MB, 5 rotações) com nível configurável por `LOG_LEVEL`, carimbo temporal com fuso horário do SO (`datetime.fromtimestamp(...).astimezone().strftime("%Y-%m-%d %H:%M:%S %z")`), PID, TID, nome da thread e uso efetivo dos níveis `INFO`, `ERROR` e `DEBUG`.

#### Exemplo Real de Linhas de Log Produzidas pelo Sistema
```text
[2026-11-11 20:03:46 -0300 INFO] [PID:17000] [TID:20920|AnyIO worker thread] [app.py:257] - [SO Agendamento] Consulta agendada: bc83af49 para 2026-11-13 às 08:00
[2026-11-11 20:03:46 -0300 ERROR] [PID:17000] [TID:21900|asyncio-portal-2ef8e80ca30] [app.py:77] - [HTTP 404] GET /api/consultas/bc83af49 -> Consulta não encontrada.
[2026-11-11 20:03:46 -0300 DEBUG] [PID:17000] [TID:20920|AnyIO worker thread] [concurrency.py:88] - [SO FileLock] Trava de kernel adquirida em 'consultas.json.lock'
```

---

## 4. Análise de Decisões Técnicas

| Decisão | Alternativa | Motivo |
| :--- | :--- | :--- |
| Persistência em arquivos JSON com `os.fsync` + `os.replace` | Banco SQLite ou PostgreSQL | O objetivo acadêmico exige interação direta e visível com o sistema de arquivos, buffers de I/O, escrita atômica e recuperação de corrupção em disco. |
| Lock duplo (`threading.Lock` + `msvcrt.locking`/`fcntl.flock`) | Apenas `threading.Lock` ou biblioteca `portalocker` opaca | O mutex em RAM resolve threads do mesmo processo, enquanto as chamadas nativas `msvcrt`/`fcntl` demonstram travas de kernel entre processos distintos em cada SO. |
| `WeakValueDictionary` para `_memory_locks` | Dicionário comum (`dict`) acumulativo | Impede crescimento ilimitado de objetos `Lock` em RAM quando chaves de recursos dinâmicas deixam de ser referenciadas. |
| `multiprocessing` com contexto `spawn` e função de nível de módulo | `fork` padrão no Linux ou apenas `ThreadPoolExecutor` | `spawn` funciona uniformemente em Windows, Linux e macOS e evita deadlocks ao criar processos a partir de um servidor web multithread. |
| `WorkerPool` com `job_id` e polling via `GET /api/jobs/{job_id}` | Geração síncrona bloqueante na rota POST | Desacopla operações pesadas de I/O (PDF, CSV, ZIP), libera a requisição HTTP imediatamente e permite auditar `thread_name` e `TID` reais. |
| `utf-8-sig` e `lineterminator` por SO no CSV | `utf-8` fixo com `\n` | Garante abertura correta de acentos em português no Excel no Windows mantendo padrão POSIX limpo no Linux e macOS. |
| `lifespan` no FastAPI e `tracemalloc` integrado | `@app.on_event` (depreciado) e apenas RSS total | `lifespan` segue a API atual do FastAPI para iniciar/parar daemons, e `tracemalloc` permite comparar snapshots para provar ausência de memory leaks. |

---

## 5. Demonstração de Funcionamento, Capturas e Resultados

### 5.1 Resultados dos Testes Automatizados
A suíte `tests/test_concurrency.py` foi executada em diretório temporário isolado (`tempfile`), aprovando todos os **16 testes**:
```text
Ran 16 tests in 3.898s
OK
```
Validando: adaptação de SO, escrita atômica, recuperação de JSON corrompido, cache LRU defensivo, corrida com 10 threads (1 sucesso / 9 bloqueios), corrida com 4 processos (1 sucesso / 3 bloqueios com PIDs distintos), CRUD completo e horários, regras de negócio, bloqueio `409` na exclusão com consulta ativa, jobs assíncronos no `WorkerPool`, bloqueio de *Path Traversal*, sanitização de nomes, `SO-CleanupDaemon`, `tracemalloc` e `WeakValueDictionary`.

### 5.2 Roteiro Visual (Capturas de Tela)

![Captura 1 - Tela de Agendamento](capturas/01_agendamento.png)

![Captura 2 - CRUDs de Médicos, Horários, Pacientes e Consultas](capturas/02_cruds.png)

![Captura 3 - Conflito de Horário ou Exclusão Recusada](capturas/03_conflito_recusado.png)

![Captura 4 - Simulação de Concorrência com 10 Threads](capturas/04_simulacao_threads.png)

![Captura 5 - Simulação de Concorrência com 5 Processos (PIDs)](capturas/05_simulacao_processos.png)

![Captura 6 - Painel de Telemetria e Memória (tracemalloc)](capturas/06_telemetria_memoria.png)

![Captura 7 - Console de Logs com Fuso Horário e Filtro por Nível](capturas/07_logs.png)

![Captura 8 - Relatório PDF Gerado via WorkerPool](capturas/08_relatorio_pdf.png)

![Captura 9 - Pasta de Backups Compactados (.zip)](capturas/09_pasta_backups.png)

---

## 6. Dificuldades Encontradas e Soluções Adotadas

1. **Diferenças de File Locks entre Windows e POSIX:**
   - *Problema:* O Windows (`msvcrt.locking`) exige um arquivo com pelo menos 1 byte e ponteiro posicionado no byte 0 antes de travar, enquanto sistemas POSIX (`fcntl.flock`) travam o descritor inteiro.
   - *Solução:* Implementado tratamento condicional em `ConcurrencyLockManager.file_lock` (`src/concurrent/concurrency.py`) garantindo inicialização de 1 byte no arquivo `.lock` no Win32.
2. **Multiprocessamento `spawn` no Windows com FastAPI e Testes:**
   - *Problema:* No Windows, `multiprocessing` usa `spawn`, exigindo que a função alvo seja importável no nível do módulo e não dependa de estado global não serializável.
   - *Solução:* Extraída a função `multiprocess_schedule_worker` no nível do módulo `src/core/scheduler.py`, recebendo apenas caminhos e dicionários serializáveis.
3. **Permissões e `os.replace` no Windows:**
   - *Problema:* Marcar arquivos JSON mutáveis como `READONLY` via `os.chmod` no Windows faz `os.replace()` falhar com `PermissionError`.
   - *Solução:* Aplicado `0o700`/`0o600` no POSIX e inspeção/registro de `st_file_attributes` via `os.stat()` no Windows (`OSAdapter.apply_safe_permissions`).

---

## 7. Conclusão

O projeto **MedScheduler SO** atingiu todos os objetivos funcionais e pedagógicos propostos, integrando um sistema completo de agendamento médico com visualização e controle direto dos mecanismos do sistema operacional: escalonamento de threads e processos, exclusão mútua em memória e no kernel, persistência atômica tolerante a falhas, gestão de memória com `psutil`/`tracemalloc`/`WeakValueDictionary` e portabilidade multiplataforma entre Windows, Linux e macOS.
