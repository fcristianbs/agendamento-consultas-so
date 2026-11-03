# Manual de Instalação e Execução

## Sistema de Agendamento de Consultas Médicas (Trabalho de Sistemas Operacionais)

Este documento orienta como instalar as dependências e executar o sistema em qualquer sistema operacional (Windows, Linux ou macOS).

---

## 1. Pré-requisitos

- **Python 3.10 ou superior** instalado no sistema operacional.
- Gerenciador de pacotes **pip**.

---

## 2. Passo a Passo de Instalação

### No Windows:
1. Abra o terminal (PowerShell ou Prompt de Comando) na pasta do projeto:
   ```powershell
   cd "c:\git\Agendamento TRAB S.O"
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
   cd sistema_agendamento
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

## 3. Como Executar a Aplicação

Para iniciar o servidor com o painel web e os serviços de SO:

```bash
# Com o ambiente virtual ativado:
python run.py

# Ou no Windows com o launcher:
py run.py
```

Após iniciar, o terminal exibirá as métricas do SO hospedeiro e o endereço local:
```text
Acesse a aplicação no navegador em: http://127.0.0.1:8000
```

Abra o seu navegador web favorito e acesse [http://127.0.0.1:8000](http://127.0.0.1:8000).

---

## 4. Como Rodar os Testes Automatizados de SO

O projeto conta com uma suíte de testes de estresse de concorrência e chamadas de sistema:

```bash
python tests/test_concurrency.py
```

O teste valida automaticamente:
1. Adaptação e identificação do SO hospedeiro.
2. Escrita atômica em disco com sincronização física de buffers (`fsync` e `replace`).
3. Algoritmo de substituição de páginas do Cache LRU.
4. **Resolução de Condição de Corrida**: 10 threads simultâneas disparadas contra o mesmo horário, comprovando a eficácia do Lock de Exclusão Mútua.
5. Geração assíncrona de relatórios PDF, CSV e snapshots de backup compactados.
6. Coleta de lixo (`gc.collect`) e desalocação de memória RAM física.

---

## 5. Roteiro de Demonstração para a Apresentação (5-10 min)

1. **Abertura e Visão Geral**:
   - Mostre o banner inicial do terminal gerado por `python run.py`, destacando o SO hospedeiro detectado, arquitetura, PID e diretórios.
2. **Interface do Usuário (CRUD & Agendamento)**:
   - Cadastre uma consulta na aba **Consultas & Agendamento**. Mostre o cálculo dinâmico de horários livres baseado na agenda do médico.
3. **Painel do SO & Concorrência (Destaque Avaliativo)**:
   - Acesse a aba **Painel do SO & Concorrência**.
   - Mostre os cards com o **PID**, quantidade de **Threads ativas**, uso real de memória **RSS (física) e VMS (virtual)**, e métricas do **Cache LRU**.
   - Clique em **"Disparar 10 Threads Concorrentes"**: mostre que o Lock de SO permitiu apenas 1 agendamento e rejeitou 9 tentativas de sobreposição simultânea.
   - Clique em **"Executar Coleta de Lixo & Purga de Memória"** para demonstrar a devolução de memória e remoção de arquivos temporários do disco.
   - Mostre o console de logs na própria tela com os PIDs, Thread IDs e carimbos de data/hora com fuso do SO.
4. **Operações de I/O de Arquivo**:
   - Acesse a aba **Relatórios & Backups**: gere um relatório PDF e um CSV. Baixe os arquivos gerados.
   - Mostre o snapshot compactado gerado em `data/backups/`.

