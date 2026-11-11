# Manual de Instalação e Execução

## Sistema de Agendamento de Consultas Médicas (Trabalho de Sistemas Operacionais)

Este documento orienta como instalar as dependências, configurar variáveis de ambiente e executar o sistema em **Windows, Linux ou macOS**.

---

## 1. Pré-requisitos

- **Python 3.10 ou superior** instalado no sistema operacional.
- Gerenciador de pacotes **pip**.

---

## 2. Passo a Passo de Instalação

### No Windows:
1. Abra o terminal (PowerShell ou Prompt de Comando) na pasta do projeto:
   ```powershell
   cd c:\git\agendamento-consultas-so
   ```

2. Crie e ative o ambiente virtual (opcional, mas recomendado):
   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

3. Instale as dependências:
   ```powershell
   pip install -r requirements.txt
   ```

### No Linux / macOS:
1. Abra o terminal na pasta do projeto:
   ```bash
   cd agendamento-consultas-so
   ```

2. Crie e ative o ambiente virtual:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Instale as dependências:
   ```bash
   pip install -r requirements.txt
   ```

---

## 3. Variáveis de Ambiente e Modos de Execução

O sistema suporta dois modos de diretórios e configuração de logging:

- **`AGENDAMENTO_MODE`** (`local` padrão ou `os`):
  - Em modo `local` (ou com `--local` / `AGENDAMENTO_DATA_DIR=./data`), todos os arquivos ficam em `./data/`.
  - Em modo `os` (ou com `--os-paths`), os dados e relatórios seguem o padrão do SO hospedeiro:
    - **Windows:** `%APPDATA%\AgendamentoSO` e `%USERPROFILE%\Documents\AgendamentoSO\relatorios`
    - **Linux:** `$XDG_DATA_HOME/agendamento-so` (ou `~/.local/share/agendamento-so`) e `$XDG_DOCUMENTS_DIR/AgendamentoSO/relatorios` (ou `~/Documents/AgendamentoSO/relatorios`)
    - **macOS:** `~/Library/Application Support/AgendamentoSO` e `~/Documents/AgendamentoSO/relatorios`
- **`AGENDAMENTO_DATA_DIR`**: quando definida, força o diretório raiz de dados (ex.: `./data`).
- **`LOG_LEVEL`** (`DEBUG`, `INFO`, `ERROR`; padrão `DEBUG`): define o nível gravado no arquivo `sistema.log`. O console permanece sempre em `INFO`.

Exemplos de execução:

```bash
# Modo local padrão
python run.py

# Modo explícito com diretórios nativos do SO
python run.py --os-paths

# Com LOG_LEVEL personalizado no Linux/macOS
LOG_LEVEL=INFO python3 run.py --local
```

No PowerShell (Windows):
```powershell
$env:LOG_LEVEL = "DEBUG"
python run.py --local
```

Acesse **[http://127.0.0.1:8000](http://127.0.0.1:8000)** no navegador.

---

## 4. Resumo dos Endpoints REST

- **Médicos:** `GET /api/medicos`, `GET /api/medicos/{id}`, `POST /api/medicos`, `PUT /api/medicos/{id}`, `DELETE /api/medicos/{id}`
- **Horários do Médico:** `GET /api/medicos/{id}/horarios[?data=YYYY-MM-DD]`, `POST /api/medicos/{id}/horarios`, `PUT /api/medicos/{id}/horarios`, `DELETE /api/medicos/{id}/horarios/{hh:mm}`
- **Pacientes:** `GET /api/pacientes`, `GET /api/pacientes/{id}`, `POST /api/pacientes`, `PUT /api/pacientes/{id}`, `DELETE /api/pacientes/{id}`
- **Consultas:** `GET /api/consultas`, `GET /api/consultas/{id}`, `POST /api/consultas`, `PUT /api/consultas/{id}`, `PUT /api/consultas/{id}/concluir`, `PUT /api/consultas/{id}/cancelar`, `DELETE /api/consultas/{id}`
- **Relatórios, Backups e Jobs:** `POST /api/reports/pdf`, `POST /api/reports/csv`, `POST /api/backups/create`, `GET /api/jobs/{job_id}`, `GET /api/reports/list`, `GET /api/reports/download/{filename}`, `GET /api/backups/list`
- **Telemetria, Memória e Testes:** `GET /api/os/telemetry`, `GET /api/os/memory/snapshot`, `POST /api/os/memory/compare`, `POST /api/os/gc`, `GET /api/os/logs?lines=80&level=INFO`, `POST /api/test/concurrency-simulation`, `POST /api/test/multiprocess-simulation`

---

## 5. Como Rodar os Testes Automatizados

```bash
python tests/test_concurrency.py -v
# ou
python -m unittest discover tests -v
```

Os 16 testes executam em diretório temporário (`tempfile`), garantindo que `./data/` não seja alterado, e validam:
1. Adaptação ao SO, paths, sanitização e case-sensitivity.
2. Escrita atômica (`tmp` + `fsync` + `os.replace`) e recuperação de JSON corrompido via backup `.zip`.
3. Cache LRU com cópia defensiva (`deepcopy`).
4. Concorrência entre 10 threads e entre múltiplos processos (`multiprocessing spawn`).
5. CRUD completo, horários, regras de negócio e bloqueio `409` na exclusão com consultas ativas.
6. Jobs assíncronos no `WorkerPool` com `job_id`, `TID` e download seguro contra Path Traversal.
7. Logging com fuso horário (`%z`) e níveis `INFO`, `ERROR` e `DEBUG`.
8. `SO-CleanupDaemon` e rastreamento de heap com `tracemalloc` + `WeakValueDictionary`.

---

## 6. Roteiro de Demonstração para Apresentação

1. **Inicialização (`run.py`)**: exiba o banner inicial com o SO detectado, modo ativo vs modo SO, `case-sensitivity` e encoding CSV.
2. **Consultas e CRUDs**: cadastre/edite médico, gerencie seus horários, edite um paciente (mostrando a propagação do nome nas consultas), tente agendar no passado ou excluir médico com consulta agendada (`409`).
3. **Relatórios Assíncronos (`WorkerPool`)**: gere PDF e CSV, mostrando o `job_id`, a thread `SOWorker` e o `TID`, e baixe os arquivos.
4. **Painel do SO & Concorrência**:
   - Execute **"Disparar 10 Threads Concorrentes"** e **"Disparar 5 Processos Independentes"** (destacando os PIDs distintos).
   - Capture e compare snapshots com **`tracemalloc`** e execute o **Garbage Collector**.
   - Filtre os logs por **`INFO`**, **`ERROR`** e **`DEBUG`** mostrando o carimbo com fuso horário.
