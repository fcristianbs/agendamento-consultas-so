# Documentação Técnica: Implementações de Sistemas Operacionais (SO)

Este documento descreve detalhadamente todos os conceitos de **Sistemas Operacionais** implementados no código-fonte, explicando o que cada trecho faz, por que foi feito assim e quais chamadas de sistema ou mecanismos de kernel estão envolvidos.

---

## 1. Chamadas de Sistema e Abstração de Plataforma
**Arquivo:** `src/config/os_adapter.py`  
**Classe:** `OSAdapter`

### O que o código faz:
O sistema operacional hospedeiro (Windows, Linux ou macOS) é identificado dinamicamente no momento da inicialização para garantir portabilidade nativa sem erros de caminho ou permissão.

### Conceitos de SO aplicados:
1. **Identificação de Plataforma e Kernel:**
   - `platform.system()`: Identifica o kernel (`Windows`, `Linux` ou `Darwin`).
   - `platform.release()` / `platform.version()`: Obtém a versão de compilação do SO.
   - `platform.machine()`: Retorna a arquitetura de processador (`x86_64`, `AMD64`, `ARM64`).
   - `os.cpu_count()`: Consulta ao kernel sobre o número de núcleos lógicos disponíveis para escalonamento.
   - `getpass.getuser()`: Consulta o usuário dono do processo atual na tabela de sessões do SO.

2. **Diferenças de Filesystem e Normalização de Caminhos:**
   - `os.sep`: No Windows o separador de diretórios é barra invertida (`\`), enquanto em sistemas POSIX (Linux/macOS) é barra normal (`/`). O uso de `Path` (`pathlib`) e `os.sep` garante que os caminhos sejam resolvidos na sintaxe nativa do filesystem hospedeiro (NTFS, Ext4, APFS).
   - **Line Endings:** O código detecta `\r\n` (CRLF padrão Windows) versus `\n` (LF padrão Unix) para garantir que arquivos de log e exportações de texto respeitem as convenções do SO.

3. **Gerenciamento de Permissões de Acesso (POSIX vs Windows ACLs):**
   - Método `apply_safe_permissions(path)`:
     - No Linux/macOS: Utiliza a chamada de sistema `os.chmod(path, 0o700)` para diretórios e `0o600` para arquivos de dados de pacientes. Isso assegura no subsistema de segurança POSIX que **somente o UID do processo proprietário** tem permissão de leitura e escrita (`rwx------`), impedindo que outros usuários do SO leiam dados médicos.
     - No Windows: Delega ao modelo de segurança baseado em SID/ACLs herdadas do usuário logado.

---

## 2. Gerenciamento de Memória Principal (RAM)
**Arquivo:** `src/config/os_adapter.py`  
**Classes:** `LRUCache` e `MemoryMonitor`

### O que o código faz:
Demonstra na prática a hierarquia de memória do computador: acesso ultrarrápido em **Memória Primária (RAM)** versus operações lentas de I/O em **Memória Secundária (Disco)**, implementando algoritmos clássicos de substituição de páginas e monitoramento do espaço de endereçamento do processo.

### Conceitos de SO aplicados:

1. **Cache LRU (*Least Recently Used* - Substituição de Páginas):**
   - Em Sistemas Operacionais, a política **LRU** é o algoritmo de referência para substituição de páginas na memória virtual quando os quadros (*frames*) de memória física estão esgotados.
   - O `LRUCache` mantém as consultas e listagens médicas em RAM. Quando a capacidade limite é atingida (`len(cache) >= capacity`), o algoritmo executa:
     ```python
     self._cache.popitem(last=False)
     ```
     Isso remove da memória exatamente o bloco de dados que ficou mais tempo sem receber acesso (*menos recentemente utilizado*), liberando espaço para os novos dados.
   - **TTL (*Time to Live*):** Entradas possuem timestamp de validade. Se expiradas, são descartadas, evitando que memória desatualizada persista em RAM.

2. **Espaço de Endereçamento do Processo (RSS vs VMS):**
   - O método `memory_monitor.get_info()` consulta o kernel através de `psutil`:
     - **RSS (*Resident Set Size*):** Quantidade exata de **memória física (RAM real)** que as páginas do processo ocupam no momento.
     - **VMS (*Virtual Memory Size*):** Tamanho total do **espaço de endereçamento virtual** alocado pelo sistema operacional para o processo (incluindo bibliotecas compartilhadas, pilha, heap e páginas mapeadas ainda não carregadas fisicamente na RAM).

3. **Garbage Collection e Prevenção de Vazamento de Memória (*Memory Leaks*):**
   - Método `run_garbage_collection()`:
     - Invoca explicitamente `gc.collect()`, forçando o runtime a identificar e desalocar referências circulares e objetos inalcançáveis da heap.
     - Purga entradas de cache cujo TTL expirou.
     - Faz varredura no filesystem para remover arquivos temporários órfãos (`.tmp`) que tenham sido deixados para trás por falhas de escrita.

---

## 3. Processos, Threads e Concorrência
**Arquivo:** `src/concurrent/concurrency.py`  
**Classes:** `ConcurrencyLockManager` e `WorkerPool`

### O que o código faz:
Gerencia a execução simultânea de múltiplas tarefas, controlando o escalonamento de threads e impedindo **Condições de Corrida (*Race Conditions*)** através de primitivas de sincronização em memória e no kernel.

### Conceitos de SO aplicados:

1. **Process Identifier (PID) e Thread Identifier (TID):**
   - O sistema opera sob um processo pai único com PID atribuído pelo escalonador do SO (`os.getpid()`).
   - Cada linha de log e cada tarefa executada registra explicitamente o identificador numérico da thread (`TID`) e o nome atribuído pelo SO.

2. **Pool de Threads e Escalonamento Assíncrono (`WorkerPool`):**
   - Utiliza `ThreadPoolExecutor` com limite fixo de trabalhadores (`max_workers=6`), gerando threads nomeadas (`SOWorker_0`, `SOWorker_1`, etc.).
   - Tarefas que exigem muito tempo de CPU ou espera de I/O (ex.: geração de documentos PDF pesados ou criação de arquivos ZIP) são despachadas via `submit()` para trabalhadores em background. Isso evita que a thread principal bloqueie o atendimento de novas requisições.

3. **Thread Daemon de Segundo Plano (`SO-BackupDaemon`):**
   - Implementada no `src/storage/storage.py`, opera como uma thread com flag `daemon=True`.
   - Utiliza um objeto de sincronização `threading.Event()` com espera temporizada (`self._stop_event.wait(timeout)`). Isso permite que a thread durma no escalonador sem consumir ciclos de CPU até que o intervalo expire ou o processo receba sinal de encerramento (`SIGINT`/`SIGTERM`).

4. **Exclusão Mútua em Memória (Mutex Locks):**
   - Utiliza `threading.Lock()` no método `synchronized(resource_name)`.
   - Se duas threads tentarem executar simultaneamente a seção crítica de agendamento, uma adquire o lock (`lock.acquire()`) e a outra entra em estado de espera (*Blocked/Waiting*), garantindo que apenas 1 thread modifique a agenda por vez.

5. **Travas de Arquivo em Nível de Kernel (*Kernel File Locks*):**
   - O método `file_lock(file_path)` atua diretamente nas APIs de baixo nível do Sistema Operacional:
     - **No Windows:**
       ```python
       import msvcrt
       msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)    # Trava a região
       msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)  # Destrava
       ```
       Invoca diretamente a função `_locking()` do C-Runtime do Windows (`msvcrt`), aplicando um bloqueio exclusivo direto na tabela de arquivos abertos do kernel do Windows.
     - **No Linux/macOS:**
       ```python
       import fcntl
       fcntl.flock(f.fileno(), fcntl.LOCK_EX)  # Trava exclusiva POSIX
       fcntl.flock(f.fileno(), fcntl.LOCK_UN)  # Destrava
       ```
       Invoca a chamada de sistema `flock()` do kernel Linux/Unix.
   - **Por que isso é fundamental?** Enquanto o Mutex protege concorrência entre threads do *mesmo* processo, o **Kernel File Lock** protege o arquivo físico mesmo que existam *múltiplos processos independentes* rodando no computador.

---

## 4. Sistema de Arquivos e Integridade de I/O
**Arquivo:** `src/storage/storage.py`  
**Classes:** `FileManager`, `setup_logger`, `AutoBackupManager`

### O que o código faz:
Realiza operações seguras de leitura e escrita física no disco, garantindo que falhas repentinas (como falta de energia ou travamento de processo) não deixem os arquivos de dados em estado corrompido.

### Conceitos de SO aplicados:

1. **Escrita Atômica (*Atomic Write*) com `fsync` e `replace`:**
   - Em operações normais de escrita com `f.write()`, o sistema operacional armazena os bytes apenas em **buffers de página na memória RAM** (*Page Cache do kernel*) para otimizar velocidade, demorando segundos até descarregar para o disco físico. Se o computador for desligado nesse intervalo, o arquivo é corrompido.
   - Para impedir isso, o método `save_json_atomic()` implementa:
     ```python
     temp_file_path = file_path.with_suffix(".tmp")
     with open(temp_file_path, "w", encoding=self.default_encoding) as f:
         json.dump(data, f, ensure_ascii=False, indent=2)
         f.flush()
         os.fsync(f.fileno())  # <--- CHAMADA DE SISTEMA CRÍTICA
     os.replace(temp_file_path, file_path)  # <--- OPERAÇÃO ATÔMICA DO FILESYSTEM
     ```
     - `os.fsync(f.fileno())`: Dispara uma interrupção de sistema que força o controlador de disco a esvaziar todos os buffers do kernel e gravar os dados fisicamente na mídia magnética/SSD antes de prosseguir.
     - `os.replace()`: Substitui o arquivo antigo pelo novo através de uma atualização atômica de ponteiros de inode/metadados no sistema de arquivos. Para o SO, o arquivo nunca fica corrompido pela metade: ou existe a versão anterior intacta, ou a versão nova intacta.

2. **Gerenciamento de Dispositivos e Rotação de Logs:**
   - Classe `setup_logger()` com `RotatingFileHandler`.
   - Limita o tamanho do arquivo a 2 MB com até 5 gerações de backup (`sistema.log.1`, `sistema.log.2`). Quando o arquivo atinge o limite de blocos de disco, o SO rotaciona os arquivos, evitando esgotamento de espaço na partição de armazenamento.
   - Cada linha de log é enriquecida com metadados do SO:
     ```text
     [Data/Hora Fuso] [PID:XXXX] [TID:YYYY|ThreadName] [arquivo:linha] - Mensagem
     ```

3. **Snapshots Compactados e Rotação:**
   - `AutoBackupManager` gera arquivos `.zip` agregando os arquivos da pasta de consultas.
   - Mantém um número fixo de snapshots (`max_backups=5`), excluindo a versão mais antiga (`oldest.unlink()`) a cada novo ciclo para preservar inodes e blocos do filesystem.

---

## 5. Entrada e Saída de Dados (I/O)
**Arquivo:** `src/reports/reports.py`  
**Funções:** `generate_pdf()` e `export_csv()`

### O que o código faz:
Processa a conversão de estruturas de dados residentes em memória para arquivos em formato de stream binário (PDF) e arquivos tabulares delimitados (CSV).

### Conceitos de SO aplicados:
1. **Manipulação de Streams de I/O Binários vs Texto:**
   - **PDF:** Requer escrita em formato binário estrito (`application/pdf`) contendo dicionários de objetos, tabelas de fontes e referências cruzadas de bytes (*cross-reference tables*). O módulo utiliza a biblioteca `reportlab` para construir o arquivo diretamente no caminho resolvido pelo SO.
   - **CSV:** Opera como stream textual delimitado, respeitando a codificação `UTF-8` e a convenção de fim de linha do SO hospedeiro.
2. **I/O Não-Bloqueante:**
   - No servidor (`src/web/app.py`), as rotas que geram relatórios acionam o subsistema de relatórios gravando os arquivos no diretório `data/relatorios/`, permitindo que downloads subsequentes sejam atendidos pelo SO via `FileResponse` de forma desacoplada.

---

## 6. Prevenção de Conflitos e Condições de Corrida (Race Conditions)
**Arquivo:** `src/core/scheduler.py`  
**Método:** `schedule_consulta(consulta)`

### O que o código faz:
Resolve o problema clássico de Sistemas Operacionais conhecido como **Condição de Corrida**: o que acontece quando dois clientes tentam reservar exatamente o mesmo recurso (mesmo médico, mesma data e mesmo horário) no exato mesmo milissegundo?

### Mecanismo de Defesa Passo a Passo:
1. **Entrada na Seção Crítica com Duplo Bloqueio:**
   ```python
   with lock_manager.synchronized("schedule_lock"):      # 1. Mutex de Threads
       with lock_manager.file_lock(self.consultas_file): # 2. Kernel File Lock
           consultas = self.file_manager.load_json(...)
           # Verificação de disponibilidade...
           # Gravação atômica...
   ```
2. **Como a barreira funciona:**
   - A Thread A obtém o `schedule_lock` e o lock de arquivo do kernel.
   - A Thread B tenta obter o mesmo lock e é imediatamente colocada em estado de bloqueio (*waiting*) pelo escalonador do SO.
   - A Thread A lê os dados, verifica que o horário está livre, efetua o agendamento com escrita atômica, invalida o cache em RAM e libera o lock.
   - A Thread B agora é acordada pelo SO e obtém o lock. Ao reler os dados recém-gravados pela Thread A, ela detecta que o horário já foi ocupado e rejeita a operação com `ValueError("Conflito de horário")`.
   - **Resultado:** Apenas 1 agendamento é efetuado, nenhum dado é sobrescrito e a consistência do sistema de arquivos permanece 100% íntegra.

---

## 7. Como Provar Esses Conceitos na Apresentação

| Pergunta Típica do Professor | O que mostrar no código | O que mostrar em execução |
| :--- | :--- | :--- |
| **"Onde estão as chamadas de sistema?"** | `src/concurrent/concurrency.py` linhas com `msvcrt.locking` e `fcntl.flock`, e `src/storage/storage.py` linha com `os.fsync()`. | Abra o terminal ou a UI e mostre o nome do SO e o separador de caminhos detectado. |
| **"Como você lida com concorrência?"** | `src/concurrent/concurrency.py` (`ThreadPoolExecutor` e `threading.Lock`) e `src/core/scheduler.py` (`with lock_manager.synchronized`). | Clique no botão **"Disparar 10 Threads Concorrentes"** na UI: mostre que 1 foi aprovada e 9 rejeitadas pelo lock. |
| **"Como você gerencia memória?"** | `src/config/os_adapter.py` na classe `LRUCache` (método `popitem`) e `memory_monitor.get_info()` (uso de `psutil` para RSS e VMS). | Mostre os cartões de **RSS (RAM física)** e **VMS (Virtual)** atualizando em tempo real no dashboard. |
| **"Como garante que o arquivo não corrompe?"** | `src/storage/storage.py` no método `save_json_atomic()`, destacando `os.fsync(f.fileno())` e `os.replace()`. | Mostre a criação do arquivo temporário `.tmp` e a substituição instantânea. |
| **"Onde está a identificação das threads?"** | `src/storage/storage.py` no formatador `ProcessThreadFormatter`. | Abra o console de logs na UI e mostre cada linha com `[PID:XXXX]` e `[TID:YYYY|NomeDaThread]`. |

