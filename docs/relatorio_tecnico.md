# RELATÓRIO TÉCNICO
## Sistema de Agendamento de Consultas Médicas com Foco em Sistemas Operacionais

**Disciplina:** Sistemas Operacionais  
**Data:** Novembro de 2026  
**Modalidade:** Trabalho Prático Avaliativo  

---

### Sumário
1. [Introdução e Objetivos do Projeto](#1-introdução-e-objetivos-do-projeto)
2. [Arquitetura Geral do Sistema](#2-arquitetura-geral-do-sistema)
3. [Demonstração dos Conceitos de Sistemas Operacionais](#3-demonstração-dos-conceitos-de-sistemas-operacionais)
   - [3.1 Processos e Threads](#31-processos-e-threads)
   - [3.2 Sistema de Arquivos e Persistência](#32-sistema-de-arquivos-e-persistência)
   - [3.3 Gerenciamento de Memória](#33-gerenciamento-de-memória)
   - [3.4 Concorrência e Sincronização](#34-concorrência-e-sincronização)
   - [3.5 Chamadas de Sistema e Portabilidade](#35-chamadas-de-sistema-e-portabilidade)
   - [3.6 Operações de Entrada/Saída (I/O)](#36-operações-de-entradasaída-io)
4. [Análise de Decisões Técnicas](#4-análise-de-decisões-técnicas)
5. [Demonstração de Funcionamento e Resultados](#5-demonstração-de-funcionamento-e-resultados)
6. [Dificuldades Encontradas e Soluções Adotadas](#6-dificuldades-encontradas-e-soluções-adotadas)
7. [Conclusão](#7-conclusão)

---

## 1. Introdução e Objetivos do Projeto

O presente projeto consiste no desenvolvimento de um **Sistema Completo de Agendamento de Consultas Médicas (CRUD)**, concebido para ilustrar e exercitar de maneira prática os principais conceitos teóricos da disciplina de Sistemas Operacionais.

Diferente de sistemas web ou desktop tradicionais que abstraem por completo a camada de infraestrutura, este software expõe, monitora e controla conscientemente a interação com o núcleo (*kernel*) do sistema operacional hospedeiro, abrangendo:
- Criação e escalonamento de **Processos e Threads**;
- Estruturação, permissões e sincronização física no **Sistema de Arquivos**;
- Alocação, controle de limites e liberação de **Memória Principal (RAM)** com algoritmos de substituição (LRU);
- Controle de **Concorrência** e prevenção de condições de corrida (*race conditions*);
- Realização de **Chamadas de Sistema (*System Calls*)** multiplataforma (Windows e POSIX);
- Gestão de operações de **Entrada e Saída (I/O)** síncronas e assíncronas.

---

## 2. Arquitetura Geral do Sistema

O projeto foi estruturado estritamente de acordo com o padrão modular especificado:

```text
sistema_agendamento/
├── src/
│   ├── core/                  # Lógica de negócio, validações e modelos de dados
│   │   ├── models.py          # Modelos de Médicos, Pacientes e Consultas
│   │   └── scheduler.py       # Regras de agendamento e checagem de conflitos
│   ├── storage/               # Operações com o Sistema de Arquivos
│   │   ├── file_manager.py    # Leitura e escrita atômica garantida em disco
│   │   └── backup.py          # Thread daemon de snapshot e rotação
│   ├── concurrent/            # Primitivas de Concorrência
│   │   ├── lock_manager.py    # Mutex e travas de arquivo no kernel (msvcrt / fcntl)
│   │   └── worker_pool.py     # Pool de threads do escalonador
│   ├── reports/               # Operações de I/O de Relatórios
│   │   ├── pdf_generator.py   # Geração desacoplada de relatórios binários em PDF
│   │   └── csv_generator.py   # Exportação em formato texto delimitado
│   ├── config/                # Camada de Adaptação ao SO
│   │   └── os_adapter.py      # Resolução de paths, encoding e permissões
│   ├── memory/                # Gerenciamento de Memória
│   │   ├── cache.py           # Cache em memória com política de evicção LRU
│   │   └── monitor.py         # Monitor de RSS/VMS e rotinas de Garbage Collection
│   ├── logging_app/           # Gerenciamento de Dispositivos e Logs
│   │   └── logger.py          # Logger rotativo com PID e Thread ID por linha
│   └── web/                   # Interface Web e Endpoints RESTful
│       ├── app.py             # Servidor de aplicação
│       ├── templates/         # Interface gráfica reativa (index.html)
│       └── static/            # Recursos estáticos
├── data/
│   ├── consultas/             # Persistência de pacientes, médicos e consultas (JSON)
│   ├── relatorios/            # Relatórios gerados (PDF / CSV)
│   ├── backups/               # Snapshots compactados (.zip)
│   └── logs/                  # Arquivo sistema.log
├── docs/                      # Documentação técnica e manual
├── tests/                     # Testes automatizados de concorrência e SO
└── run.py                     # Ponto de entrada do sistema
```

---

## 3. Demonstração dos Conceitos de Sistemas Operacionais

### 3.1 Processos e Threads
**Como o sistema lida com múltiplas operações simultâneas?**
- O sistema opera sob um processo principal identificado pelo seu **PID (*Process Identifier*)** único concedido pelo kernel do SO (`os.getpid()`).
- Para processamento assíncrono e suporte a operações em segundo plano, é utilizado um **Pool de Threads de Trabalho (`ThreadPoolExecutor`)** configurado no módulo `src/concurrent/worker_pool.py`.
- Cada thread no pool recebe um nome padronizado (`SOWorkerThread_X`) e tem seu identificador nativo de thread (**TID**) monitorado pelo sistema operacional.
- Tarefas de longa duração (como a geração de documentos PDF pesados ou criação de arquivos compactados de backup) são despachadas para threads secundárias assíncronas, impedindo o bloqueio do ciclo de requisições de I/O do servidor principal.
- Adicionalmente, o subsistema de backup mantém ativa uma **Thread Daemon** (`SO-BackupDaemon`) que executa um laço temporal em segundo plano com `threading.Event`, realizando backups periódicos a cada intervalo configurável.

### 3.2 Sistema de Arquivos e Persistência
**Como os dados são organizados e acessados?**
- Os dados do sistema residem no diretório estruturado `data/`, segregando:
  - `data/consultas/`: arquivos JSON estruturados contendo pacientes, médicos e consultas;
  - `data/relatorios/`: documentos PDF e CSV gerados pelo subsistema de I/O;
  - `data/backups/`: arquivos `.zip` versionados com carimbo de data/hora;
  - `data/logs/`: arquivos de log do sistema (`sistema.log`).
- **Garantia de Atomicidade e Integridade Física:** Para evitar que o arquivo seja corrompido caso ocorra uma queda de energia ou terminação abrupta do processo no meio de uma gravação, o módulo `file_manager.py` adota o padrão de **Escrita Atômica**:
  1. Os dados são gravados inicialmente em um arquivo temporário com sufixo `.tmp`;
  2. Executa-se `f.flush()` seguido da chamada de sistema `os.fsync(f.fileno())`, instruindo o controlador de disco a forçar a descarga imediata de todos os buffers voláteis do kernel para a mídia física;
  3. Realiza-se a chamada de sistema `os.replace(temp_file, final_file)`, que é uma operação garantidamente atômica na tabela de arquivos do sistema operacional (NTFS no Windows ou Ext4/APFS em Unix).

### 3.3 Gerenciamento de Memória
**Como a memória é alocada e liberada?**
- **Memória Virtual (VMS) vs Memória Física (RSS):** O módulo `src/memory/monitor.py` utiliza a biblioteca de interface de sistema `psutil` para consultar diretamente as estruturas do kernel, medindo:
  - **RSS (*Resident Set Size*):** Quantidade exata de memória RAM física ocupada pelas páginas do processo;
  - **VMS (*Virtual Memory Size*):** Espaço de endereçamento virtual alocado pelo subsistema de paginação do SO.
- **Cache LRU (*Least Recently Used*):** Implementado em `src/memory/cache.py`, o cache mantém em memória primária as consultas e listagens médicas mais frequentes para economizar operações caras de I/O em disco secundário. Quando o limite máximo de entradas é atingido, o algoritmo de evicção desaloca automaticamente o item menos recentemente utilizado (`popitem(last=False)`), emulando as políticas clássicas de substituição de páginas do SO.
- **Coleta de Lixo e Liberação:** O sistema disponibiliza um endpoint e botão na UI que executa `gc.collect()`, força a purga de entradas expiradas com tempo de vida (*TTL*) esgotado e varre o sistema de arquivos para deletar arquivos temporários órfãos (`.tmp`).

### 3.4 Concorrência e Sincronização
**Como são evitados conflitos no acesso aos recursos?**
- O principal desafio de sistemas concorrentes é a **Condição de Corrida (*Race Condition*)**, como no caso clássico onde dois pacientes tentam reservar a mesma cadeira de atendimento com o mesmo médico no mesmo segundo.
- O sistema implementa uma camada dupla de proteção:
  1. **Exclusão Mútua em Memória (Mutex):** Utilização de `threading.Lock()` para isolar seções críticas de código dentro do mesmo processo;
  2. **Trava em Nível de Kernel (*Kernel File Lock*):** Implementado no módulo `src/concurrent/lock_manager.py`, adquire um bloqueio de arquivo físico no kernel do SO. Mesmo que múltiplos processos ou nós concorrentes tentem acessar o arquivo ao mesmo tempo, o kernel impõe bloqueio obrigatório.
- **Validação Prática:** No teste de estresse contido em `tests/test_concurrency.py` e replicável na interface web, 10 threads disparam simultaneamente contra a mesma vaga. O lock garante que rigorosamente apenas 1 thread obtém o agendamento, enquanto as outras 9 recebem mensagem de conflito evitado, mantendo a integridade 100% preservada.

### 3.5 Chamadas de Sistema (System Calls) e Portabilidade
**Quais APIs do SO são utilizadas?**
O módulo `src/config/os_adapter.py` atua como uma camada de abstração de SO, identificando nativamente a plataforma:
- **No Windows:**
  - `msvcrt.locking(fileno, msvcrt.LK_LOCK, 1)`: chamada ao C-Runtime Win32 para travar descritores de arquivo em nível de kernel;
  - Resolução de caminhos com barra invertida `\` (`os.sep`);
  - Quebras de linha estilo Windows (`\r\n`);
  - Herança de listas de controle de acesso (ACLs).
- **No Linux / macOS (POSIX):**
  - `fcntl.flock(fileno, fcntl.LOCK_EX)`: chamada de sistema nativa para bloqueio exclusivo de arquivos;
  - `os.chmod(path, 0o600 / 0o700)`: atribuição de máscaras de permissão para isolamento de privilégios de usuário;
  - Quebras de linha estilo Unix (`\n`);
  - Resolução de caminhos com barra normal `/`.
- **APIs de Processo:** `os.getpid()`, `os.cpu_count()`, `os.replace()`, `os.fsync()`.

### 3.6 Entrada/Saída (I/O) e Gerenciamento de Dispositivos
**Como são realizadas as operações de leitura/escrita?**
- **Dispositivo de Armazenamento:** Operações de I/O em disco são executadas com tratamento de encodings (`utf-8`), buffers limpos via `flush()` e chamadas diretas de sincronismo do kernel (`fsync`).
- **Geração de Relatórios:** Módulo `reports/` exporta dados estruturados em **PDF** binário (via biblioteca `reportlab`) e **CSV** tabular. As operações são despachadas via worker threads assíncronas para não bloquear o atendimento da interface gráfica.
- **Sistema de Logging (Dispositivo de Log):** Implementado com `logging.handlers.RotatingFileHandler`. Cada registro grava no dispositivo de log uma linha enriquecida:
  ```text
  [2026-11-03 10:28:44 INFO] [PID:17980] [TID:17172|ThreadPoolExecutor-0_0] [scheduler.py:239] - [SO Agendamento] Consulta confirmada com sucesso: ID 757581df
  ```
  Isso evidencia a atuação real das threads e processos do SO.

---

## 4. Análise de Decisões Técnicas

| Requisito | Solução Técnica Adotada | Justificativa Baseada em SO |
| :--- | :--- | :--- |
| **Backend & Runtime** | Python 3 + FastAPI / Uvicorn | Excelente suporte nativo a chamadas de sistema (`os`, `sys`, `platform`, `msvcrt`, `fcntl`, `gc`, `psutil`) sem dependências pesadas. |
| **Armazenamento** | Arquivos JSON Atômicos + File Locks | Atende integralmente à exigência de persistência em arquivos com controle explícito de concorrência e filesystem. |
| **Interface de Usuário** | Interface Web Reativa com Dashboard de SO | Permite visualização imediata em tela dos conceitos de SO (PIDs, uso de RAM em tempo real, console de logs vivos e teste de corrida). |
| **Relatórios** | ReportLab (PDF) e Módulo CSV Nativo | Geração pura sem dependências externas de binários de terceiros, salvando em diretórios estruturados. |

---

## 5. Demonstração de Funcionamento e Resultados

### Resultado dos Testes Automatizados
A suíte de testes `tests/test_concurrency.py` foi executada com sucesso total:
```text
Ran 6 tests in 0.074s
OK
```
- **Teste de Concorrência Extrema:** 10 threads concorrentes tentaram agendar a mesma vaga; 1 agendamento obteve sucesso com exclusividade e 9 foram rejeitadas com segurança pelo lock de exclusão mútua.
- **Teste de Memória:** O algoritmo de evicção LRU removeu com precisão o item menos recentemente acessado ao estourar o limite de capacidade.
- **Teste de I/O e Backup:** Criação atômica de JSON, geração de relatório PDF com tabela estilizada e geração de snapshot ZIP verificadas.

---

## 6. Dificuldades Encontradas e Soluções Adotadas

1. **Diferença de Travas de Arquivo entre Windows e POSIX:**
   - *Problema:* No Windows, o runtime C (`msvcrt`) não suporta nativamente locks compartilhados de leitura (`LOCK_SH`), gerando erros em pacotes genéricos.
   - *Solução:* Foi desenvolvido um adaptador no módulo `lock_manager.py` que seleciona condicionalmente `msvcrt.locking` no Windows e `fcntl.flock` no Linux/macOS, demonstrando domínio das APIs de cada SO.

2. **Prevenção de Corrupção em Falhas Abruptas de Disco:**
   - *Problema:* Escritas convencionais de arquivos podem deixar arquivos corrompidos se o processo cair antes do término da gravação.
   - *Solução:* Adoção de escrita atômica em arquivo `.tmp` seguida de sincronização forçada com `os.fsync` e substituição atômica via `os.replace`.

---

## 7. Conclusão

O projeto atendeu com rigor a todos os requisitos funcionais e pedagógicos solicitados. Além de disponibilizar um sistema de agendamento médico intuitivo e estável, o projeto se destaca por tornar **visíveis, mensuráveis e auditáveis** os principais pilares de Sistemas Operacionais: escalonamento de threads, sincronização por exclusão mútua, integridade de sistemas de arquivos, gerenciamento de memória virtual e física, e uso direto de chamadas de sistema.

