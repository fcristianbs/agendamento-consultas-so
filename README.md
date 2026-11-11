# Sistema de Agendamento de Consultas Médicas - CRUD
## Projeto Prático de Sistemas Operacionais

Sistema de agendamento de consultas médicas desenvolvido para demonstrar na prática os conceitos fundamentais de **Sistemas Operacionais** em Windows, Linux e macOS.

---

## 📂 Estrutura do Projeto (`1 Arquivo por Módulo`)

```text
agendamento-consultas-so/
├── src/
│   ├── config/
│   │   └── os_adapter.py    # 🔹 SO: Detecção de SO, paths por SO/local, permissões, sanitização, Cache LRU, MemoryMonitor (psutil + tracemalloc) e SO-CleanupDaemon
│   ├── storage/
│   │   └── storage.py       # 🔹 SO: Escrita atômica (tmp + fsync + os.replace), I/O async, recuperação de JSON corrompido, AutoBackupManager e logging com fuso/PID/TID
│   ├── concurrent/
│   │   └── concurrency.py   # 🔹 SO: ConcurrencyLockManager (Mutex WeakValueDictionary + msvcrt/fcntl), WorkerPool com job_id e run_multiprocess_tasks (spawn)
│   ├── reports/
│   │   └── reports.py       # 🔹 SO: Geração atômica de PDF (ReportLab) e CSV com encoding/EOL nativos do SO
│   ├── core/
│   │   └── scheduler.py     # Modelos Pydantic, validações, regras de negócio, worker multiprocesso e SchedulerService
│   └── web/
│       ├── app.py           # Servidor FastAPI (lifespan, exception handler global, endpoints RESTful)
│       └── templates/
│           └── index.html   # Interface web completa com CRUD, horários, polling de jobs e painel do SO
├── data/                    # Dados locais padrão (--local)
│   ├── consultas/           # médicos.json, pacientes.json, consultas.json
│   ├── relatorios/          # Relatórios PDF/CSV gerados
│   ├── backups/             # Snapshots compactados (.zip)
│   └── logs/                # Arquivo sistema.log
├── docs/
│   ├── manual_instalacao.md # Manual de instalação, variáveis e roteiro de apresentação
│   ├── relatorio_tecnico.md # Relatório técnico completo
│   ├── gerar_pdf.py         # Gerador do PDF técnico a partir do Markdown
│   └── relatorio_tecnico.pdf
├── tests/
│   └── test_concurrency.py  # 16 testes automatizados (isolados via tempfile)
├── requirements.txt
├── run.py                   # Ponto de entrada (--local ou --os-paths)
└── README.md
```

---

## ⚙️ Variáveis de Ambiente e Modos de Diretório

| Variável / Flag | Valores | Comportamento |
| :--- | :--- | :--- |
| `--local` / padrão | `AGENDAMENTO_DATA_DIR=./data` | Armazena dados e relatórios em `./data/` (ideal para demonstração). |
| `--os-paths` | `AGENDAMENTO_MODE=os` | Usa pastas nativas do SO: Windows (`%APPDATA%\AgendamentoSO`, `%USERPROFILE%\Documents\AgendamentoSO\relatorios`), Linux (`$XDG_DATA_HOME/agendamento-so`, `$XDG_DOCUMENTS_DIR/AgendamentoSO/relatorios`), macOS (`~/Library/Application Support/AgendamentoSO`, `~/Documents/AgendamentoSO/relatorios`). |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `ERROR` (padrão `DEBUG`) | Configura o nível gravado em `sistema.log` (o console permanece em `INFO`). |

---

## 🌐 Endpoints da API

- **Médicos e Horários:** `GET/POST /api/medicos`, `GET/PUT/DELETE /api/medicos/{id}`, `GET/POST/PUT /api/medicos/{id}/horarios`, `DELETE /api/medicos/{id}/horarios/{hh:mm}`
- **Pacientes:** `GET/POST /api/pacientes`, `GET/PUT/DELETE /api/pacientes/{id}`
- **Consultas:** `GET/POST /api/consultas`, `GET/PUT/DELETE /api/consultas/{id}`, `PUT /api/consultas/{id}/cancelar`, `PUT /api/consultas/{id}/concluir`
- **Relatórios, Backups e Jobs (`WorkerPool`):** `POST /api/reports/pdf`, `POST /api/reports/csv`, `POST /api/backups/create`, `GET /api/jobs/{job_id}`, `GET /api/reports/list`, `GET /api/reports/download/{filename}`, `GET /api/backups/list`
- **Telemetria, Memória e Testes de SO:** `GET /api/os/telemetry`, `GET /api/os/memory/snapshot`, `POST /api/os/memory/compare`, `POST /api/os/gc`, `GET /api/os/logs?level=...`, `POST /api/test/concurrency-simulation`, `POST /api/test/multiprocess-simulation`

---

## 🚀 Como Executar

```bash
# Modo local padrão (./data)
python run.py

# Modo caminhos nativos do SO
python run.py --os-paths

# Ou no Windows:
.\iniciar.bat
```

Acesse no navegador: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🧪 Como Rodar os Testes

```bash
python tests/test_concurrency.py -v
# ou
python -m unittest discover tests -v
```
*(Os 16 testes rodam em diretório temporário isolado sem alterar `./data/`).*
