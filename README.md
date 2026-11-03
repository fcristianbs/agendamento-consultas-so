# Sistema de Agendamento de Consultas Médicas - CRUD
## Projeto Prático de Sistemas Operacionais

Sistema de agendamento de consultas médicas desenvolvido para demonstrar na prática os conceitos fundamentais de **Sistemas Operacionais**.

---

## 📂 Estrutura Simplificada (1 Arquivo por Módulo)

O projeto foi estruturado com exatamente **1 arquivo por módulo**, facilitando a leitura, manutenção e apresentação:

```text
Agendamento TRAB S.O/
├── src/
│   ├── config/
│   │   └── os_adapter.py    # 🔹 SO: Chamadas de Sistema, detecção de SO, Cache LRU (RAM) e psutil
│   ├── storage/
│   │   └── storage.py       # 🔹 SO: Sistema de Arquivos, I/O atômico (fsync), backups e logs (PID/TID)
│   ├── concurrent/
│   │   └── concurrency.py   # 🔹 SO: Concorrência, ThreadPool, Mutex e Travas de Kernel (msvcrt/fcntl)
│   ├── reports/
│   │   └── reports.py       # 🔹 SO: Operações de I/O, geração de PDF (ReportLab) e CSV
│   ├── core/
│   │   └── scheduler.py     # Lógica de negócio, modelos de dados e verificação de conflitos
│   └── web/
│       ├── app.py           # Servidor web FastAPI e endpoints RESTful
│       └── templates/
│           └── index.html   # Interface responsiva com painel de telemetria do SO
├── data/
│   ├── consultas/           # Arquivos JSON de dados
│   ├── relatorios/          # Relatórios PDF/CSV gerados
│   ├── backups/             # Snapshots periódicos compactados (.zip)
│   └── logs/                # Arquivo sistema.log
├── docs/
│   ├── relatorio_tecnico.pdf # Relatório técnico formal para entrega
│   └── manual_instalacao.md  # Manual de instalação e roteiro de apresentação
├── tests/
│   └── test_concurrency.py  # Testes automatizados de SO e corrida concorrente
├── requirements.txt         # Dependências do projeto
├── run.py                   # Script de inicialização
└── README.md
```

---

## 🎯 Mapa Rápido para a Apresentação

Caso o professor pergunte onde está cada conceito de SO:
- **Concorrência e Locks:** `src/concurrent/concurrency.py`
- **Sistema de Arquivos e Escrita Segura:** `src/storage/storage.py`
- **Chamadas de Sistema e Memória RAM:** `src/config/os_adapter.py`
- **Geração de Relatórios (I/O):** `src/reports/reports.py`
- **Lógica e Conflitos de Agenda:** `src/core/scheduler.py`
- **Interface e Dashboard:** `src/web/app.py`

---

## 🚀 Como Executar

```bash
# Iniciar o servidor
python run.py

# Ou no Windows:
.\iniciar.bat
```

Acesse no navegador: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🧪 Como Rodar os Testes de SO

```bash
python tests/test_concurrency.py
```
*(Todos os 6 testes passam em menos de 0.05s, validando locks, I/O atômico, cache e memória).*
