# src/web/app.py
"""
Servidor Web e API RESTful - Sistema de Agendamento
Integrando a lógica médica com telemetria direta do Sistema Operacional.
"""

import os
import time
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import List, Dict, Any, Optional, Union
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.config.os_adapter import os_adapter, memory_monitor, consultas_cache, cleanup_daemon
from src.storage.storage import setup_logger, FileManager, AutoBackupManager
from src.concurrent.concurrency import lock_manager, worker_pool, run_multiprocess_tasks
from src.core.scheduler import (
    SchedulerService,
    Paciente,
    PacienteUpdate,
    Medico,
    MedicoUpdate,
    HorarioCreate,
    HorariosReplace,
    Consulta,
    ConsultaUpdate,
    ValidationError,
    NotFoundError,
    ConflictError,
    multiprocess_schedule_worker,
)
from src.reports.reports import generate_pdf, export_csv

# Inicialização dos serviços fundamentais de SO
app_logger = setup_logger(os_adapter.logs_dir, os_adapter.default_encoding)
file_manager = FileManager(default_encoding=os_adapter.default_encoding, backup_dir=os_adapter.backups_dir)
backup_manager = AutoBackupManager(source_dir=os_adapter.consultas_dir, backup_dir=os_adapter.backups_dir)
scheduler_service = SchedulerService(file_manager=file_manager)


# 🔹 SO: Ciclo de vida da aplicação via lifespan (substitui on_event depreciado)
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


app = FastAPI(title="MedScheduler SO", version="2.0.0", lifespan=lifespan)

templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))


# ==========================================
# EXCEPTION HANDLERS GLOBAIS (LOGGING DE ERROS)
# ==========================================
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    app_logger.error(f"[HTTP {exc.status_code}] {request.method} {request.url.path} -> {exc.detail}")
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    app_logger.error(f"[Validação HTTP 422] {request.method} {request.url.path} -> {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    app_logger.exception(f"[Exceção Não Tratada] {request.method} {request.url.path}: {exc}")
    return JSONResponse(status_code=500, content={"detail": "Erro interno do servidor."})


def _raise_domain_http_error(err: ValueError) -> None:
    if isinstance(err, NotFoundError):
        raise HTTPException(status_code=404, detail=str(err))
    if isinstance(err, ConflictError):
        raise HTTPException(status_code=409, detail=str(err))
    if isinstance(err, ValidationError):
        raise HTTPException(status_code=400, detail=str(err))
    raise HTTPException(status_code=400, detail=str(err))


# ==========================================
# ROTA PRINCIPAL DA INTERFACE WEB
# ==========================================
@app.get("/", response_class=HTMLResponse)
async def serve_index(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"so": os_adapter.get_telemetry()}
    )


# ==========================================
# ENDPOINTS CRUD - MÉDICOS E HORÁRIOS
# ==========================================
@app.get("/api/medicos")
async def list_medicos():
    return await scheduler_service.list_medicos_async(executor=worker_pool.executor)


@app.get("/api/medicos/{medico_id}")
async def get_medico(medico_id: str):
    medico = await scheduler_service.get_medico_async(medico_id, executor=worker_pool.executor)
    if not medico:
        raise HTTPException(status_code=404, detail="Médico não encontrado.")
    return medico


@app.post("/api/medicos")
def create_medico(medico: Medico):
    try:
        return scheduler_service.create_medico(medico)
    except ValueError as e:
        _raise_domain_http_error(e)


@app.put("/api/medicos/{medico_id}")
def update_medico(medico_id: str, dados: MedicoUpdate):
    try:
        return scheduler_service.update_medico(medico_id, dados)
    except ValueError as e:
        _raise_domain_http_error(e)


@app.delete("/api/medicos/{medico_id}")
def delete_medico(medico_id: str):
    try:
        if not scheduler_service.delete_medico(medico_id):
            raise HTTPException(status_code=404, detail="Médico não encontrado.")
        return {"message": "Médico removido com sucesso."}
    except ValueError as e:
        _raise_domain_http_error(e)


@app.get("/api/medicos/{medico_id}/horarios")
async def get_horarios_disponiveis(medico_id: str, data: Optional[str] = None):
    try:
        return await scheduler_service.get_horarios_disponiveis_async(
            medico_id, data, executor=worker_pool.executor
        )
    except ValueError as e:
        _raise_domain_http_error(e)


@app.post("/api/medicos/{medico_id}/horarios")
def add_horario_medico(medico_id: str, payload: HorarioCreate):
    try:
        horarios = scheduler_service.add_horario_medico(medico_id, payload.horario)
        return {"medico_id": medico_id, "horarios_disponiveis": horarios}
    except ValueError as e:
        _raise_domain_http_error(e)


@app.put("/api/medicos/{medico_id}/horarios")
def replace_horarios_medico(medico_id: str, payload: Union[HorariosReplace, List[str]]):
    try:
        lista = payload.horarios if isinstance(payload, HorariosReplace) else payload
        horarios = scheduler_service.replace_horarios_medico(medico_id, lista)
        return {"medico_id": medico_id, "horarios_disponiveis": horarios}
    except ValueError as e:
        _raise_domain_http_error(e)


@app.delete("/api/medicos/{medico_id}/horarios/{horario}")
def delete_horario_medico(medico_id: str, horario: str):
    try:
        horarios = scheduler_service.delete_horario_medico(medico_id, horario)
        return {"medico_id": medico_id, "horarios_disponiveis": horarios}
    except ValueError as e:
        _raise_domain_http_error(e)


# ==========================================
# ENDPOINTS CRUD - PACIENTES
# ==========================================
@app.get("/api/pacientes")
async def list_pacientes():
    return await scheduler_service.list_pacientes_async(executor=worker_pool.executor)


@app.get("/api/pacientes/{paciente_id}")
async def get_paciente(paciente_id: str):
    paciente = await scheduler_service.get_paciente_async(paciente_id, executor=worker_pool.executor)
    if not paciente:
        raise HTTPException(status_code=404, detail="Paciente não encontrado.")
    return paciente


@app.post("/api/pacientes")
def create_paciente(paciente: Paciente):
    try:
        return scheduler_service.create_paciente(paciente)
    except ValueError as e:
        _raise_domain_http_error(e)


@app.put("/api/pacientes/{paciente_id}")
def update_paciente(paciente_id: str, dados: PacienteUpdate):
    try:
        return scheduler_service.update_paciente(paciente_id, dados)
    except ValueError as e:
        _raise_domain_http_error(e)


@app.delete("/api/pacientes/{paciente_id}")
def delete_paciente(paciente_id: str):
    try:
        if not scheduler_service.delete_paciente(paciente_id):
            raise HTTPException(status_code=404, detail="Paciente não encontrado.")
        return {"message": "Paciente removido com sucesso."}
    except ValueError as e:
        _raise_domain_http_error(e)


# ==========================================
# ENDPOINTS CRUD - CONSULTAS
# ==========================================
@app.get("/api/consultas")
async def list_consultas():
    return await scheduler_service.list_consultas_async(executor=worker_pool.executor)


@app.get("/api/consultas/{consulta_id}")
async def get_consulta(consulta_id: str):
    consulta = await scheduler_service.get_consulta_async(consulta_id, executor=worker_pool.executor)
    if not consulta:
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return consulta


@app.post("/api/consultas")
def schedule_consulta(consulta: Consulta):
    try:
        res = scheduler_service.schedule_consulta(consulta)
        app_logger.info(f"[SO Agendamento] Consulta agendada: {res['id']} para {res['data']} às {res['horario']}")
        return res
    except ValueError as e:
        _raise_domain_http_error(e)


@app.put("/api/consultas/{consulta_id}")
def update_consulta(consulta_id: str, dados: ConsultaUpdate):
    try:
        return scheduler_service.update_consulta(consulta_id, dados)
    except ValueError as e:
        _raise_domain_http_error(e)


@app.put("/api/consultas/{consulta_id}/cancelar")
def cancel_consulta(consulta_id: str):
    if not scheduler_service.cancel_consulta(consulta_id):
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return {"message": "Consulta cancelada com sucesso."}


@app.put("/api/consultas/{consulta_id}/concluir")
def conclude_consulta(consulta_id: str):
    try:
        res = scheduler_service.conclude_consulta(consulta_id)
        return {"message": "Consulta concluída com sucesso.", "consulta": res}
    except ValueError as e:
        _raise_domain_http_error(e)


@app.delete("/api/consultas/{consulta_id}")
def delete_consulta(consulta_id: str):
    if not scheduler_service.delete_consulta(consulta_id):
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return {"message": "Consulta excluída com sucesso."}


# ==========================================
# ENDPOINTS DE RELATÓRIOS, BACKUP E JOBS ASSÍNCRONOS (WORKERPOOL)
# ==========================================
def _task_generate_pdf() -> Dict[str, Any]:
    consultas = scheduler_service.list_consultas()
    file_path = generate_pdf(consultas, os_adapter.relatorios_dir, os_adapter.system)
    filename = Path(file_path).name
    return {"filename": filename, "download_url": f"/api/reports/download/{filename}"}


def _task_export_csv() -> Dict[str, Any]:
    consultas = scheduler_service.list_consultas()
    file_path = export_csv(consultas, os_adapter.relatorios_dir, file_manager)
    filename = Path(file_path).name
    return {"filename": filename, "download_url": f"/api/reports/download/{filename}"}


def _task_create_backup() -> Dict[str, Any]:
    backup_path = backup_manager.create_backup()
    filename = Path(backup_path).name
    return {"file": filename, "path": backup_path}


@app.post("/api/reports/pdf")
def generate_pdf_report():
    """🔹 SO: Despacha geração de PDF para o WorkerPool e devolve job_id imediatamente."""
    job_id = worker_pool.submit_job("relatorio_pdf", _task_generate_pdf)
    app_logger.info(f"[SO WorkerPool] Job {job_id} (relatorio_pdf) enfileirado.")
    return {"job_id": job_id, "status": "pendente", "status_url": f"/api/jobs/{job_id}"}


@app.post("/api/reports/csv")
def generate_csv_report():
    """🔹 SO: Despacha exportação de CSV para o WorkerPool e devolve job_id imediatamente."""
    job_id = worker_pool.submit_job("relatorio_csv", _task_export_csv)
    app_logger.info(f"[SO WorkerPool] Job {job_id} (relatorio_csv) enfileirado.")
    return {"job_id": job_id, "status": "pendente", "status_url": f"/api/jobs/{job_id}"}


@app.post("/api/backups/create")
def trigger_backup():
    """🔹 SO: Despacha criação manual de snapshot ZIP para o WorkerPool."""
    job_id = worker_pool.submit_job("backup_manual", _task_create_backup)
    app_logger.info(f"[SO WorkerPool] Job {job_id} (backup_manual) enfileirado.")
    return {"job_id": job_id, "status": "pendente", "status_url": f"/api/jobs/{job_id}"}


@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    job = worker_pool.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' não encontrado.")
    return job


@app.get("/api/reports/download/{filename:path}")
def download_report(filename: str):
    """🔹 SO: Download seguro com bloqueio estrito de Path Traversal e Content-Type correto."""
    base_dir = os_adapter.relatorios_dir.resolve()
    if (
        not filename
        or "\x00" in filename
        or ".." in Path(filename).parts
        or Path(filename).name != filename
    ):
        raise HTTPException(status_code=400, detail="Caminho de arquivo inválido (path traversal bloqueado).")

    target_path = (base_dir / filename).resolve()
    if not target_path.is_relative_to(base_dir) or target_path == base_dir:
        raise HTTPException(status_code=400, detail="Acesso negado: caminho fora do diretório de relatórios.")

    if not target_path.exists() or not target_path.is_file():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")

    suffix = target_path.suffix.lower()
    if suffix == ".pdf":
        media_type = "application/pdf"
    elif suffix == ".csv":
        media_type = "text/csv; charset=utf-8"
    else:
        media_type = "application/octet-stream"

    return FileResponse(path=str(target_path), filename=target_path.name, media_type=media_type)


@app.get("/api/reports/list")
def list_reports():
    results = []
    if not os_adapter.relatorios_dir.exists():
        return results
    for f in sorted(os_adapter.relatorios_dir.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix.lower() in [".pdf", ".csv"]:
            results.append({
                "filename": f.name,
                "type": f.suffix.replace(".", "").upper(),
                "size_kb": round(f.stat().st_size / 1024, 2),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                "download_url": f"/api/reports/download/{f.name}"
            })
    return results


@app.get("/api/backups/list")
def list_backups():
    return backup_manager.list_backups()


# ==========================================
# ENDPOINTS DE TELEMETRIA, MEMÓRIA E LOGS
# ==========================================
@app.get("/api/os/telemetry")
def get_os_telemetry():
    return {
        "so": os_adapter.get_telemetry(),
        "memory": memory_monitor.get_info(),
        "concurrency": worker_pool.get_status()
    }


@app.get("/api/os/memory/snapshot")
def get_memory_snapshot(limit: int = 10):
    """🔹 SO: Captura as top N alocações de memória via tracemalloc."""
    return memory_monitor.take_tracemalloc_snapshot(limit=max(1, min(limit, 30)))


@app.post("/api/os/memory/compare")
def compare_memory_snapshots(limit: int = 10):
    """🔹 SO: Compara snapshots consecutivos do tracemalloc para detectar vazamentos."""
    return memory_monitor.compare_tracemalloc_snapshots(limit=max(1, min(limit, 30)))


@app.post("/api/os/gc")
def trigger_gc():
    res = memory_monitor.run_garbage_collection([
        os_adapter.consultas_dir,
        os_adapter.relatorios_dir,
        os_adapter.backups_dir,
        os_adapter.logs_dir,
    ])
    app_logger.info(
        f"[SO Memória] Garbage Collection executado. Liberado: {res['ram_reclaimed_mb']} MB | "
        f"Objetos: {res['unreachable_objects_collected']}"
    )
    return res


def _read_filtered_logs(log_file: Path, lines: int, level: Optional[str]) -> List[str]:
    with open(log_file, "r", encoding="utf-8-sig", errors="replace") as f:
        all_lines = f.readlines()
    if level and level.upper() in {"INFO", "ERROR", "DEBUG", "WARNING"}:
        token = f" {level.upper()}]"
        all_lines = [ln for ln in all_lines if token in ln]
    return all_lines[-lines:]


@app.get("/api/os/logs")
async def get_logs(lines: int = 50, level: Optional[str] = None):
    log_file = os_adapter.logs_dir / "sistema.log"
    if not log_file.exists():
        return {"logs": []}
    try:
        filtered = await asyncio.to_thread(_read_filtered_logs, log_file, max(1, min(lines, 500)), level)
        return {"logs": filtered}
    except Exception as e:
        app_logger.error(f"Erro ao ler arquivo de log: {e}")
        return {"logs": [f"Erro ao ler log: {e}"]}


# ==========================================
# DEMONSTRAÇÕES DE CONCORRÊNCIA (THREADS & MULTIPROCESSOS)
# ==========================================
@app.post("/api/test/concurrency-simulation")
def simulate_concurrency():
    """
    Dispara 10 threads simultâneas disputando o mesmo horário para o mesmo médico.
    Demonstração prática de exclusão mútua e bloqueio de race condition.
    """
    from concurrent.futures import ThreadPoolExecutor

    medicos = scheduler_service.list_medicos()
    pacientes = scheduler_service.list_pacientes()
    if not medicos or not pacientes:
        raise HTTPException(status_code=400, detail="Cadastros insuficientes para teste.")

    medico = medicos[0]
    horarios = medico.get("horarios_disponiveis") or ["10:00"]
    horario_teste = "10:00" if "10:00" in horarios else horarios[0]
    data_teste = "2026-12-01"

    # 🔹 SO: Reset da vaga protegido por mutex e file_lock para evitar race condition no setup
    with lock_manager.synchronized("schedule_lock"):
        with lock_manager.file_lock(scheduler_service.consultas_file):
            consultas = [
                c for c in file_manager.load_json(scheduler_service.consultas_file, default=[])
                if not (c["medico_id"] == medico["id"] and c["data"] == data_teste and c["horario"] == horario_teste)
            ]
            file_manager.save_json_atomic(scheduler_service.consultas_file, consultas)
            consultas_cache.invalidate("all_consultas")

    num_threads = 10
    sucessos = 0
    falhas = 0
    detalhes = []

    def tentar_agendar(thread_num: int):
        paciente = pacientes[thread_num % len(pacientes)]
        nova = Consulta(
            paciente_id=paciente["id"],
            paciente_nome=f"{paciente['nome']} (Thread #{thread_num})",
            medico_id=medico["id"],
            medico_nome=medico["nome"],
            data=data_teste,
            horario=horario_teste,
            observacoes="Disparado pelo teste de concorrência de SO"
        )
        try:
            scheduler_service.schedule_consulta(nova)
            return (True, thread_num, "Sucesso: Agendado com exclusividade pelo lock")
        except ValueError as err:
            return (False, thread_num, f"Bloqueado pelo SO: {err}")

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        futures = [pool.submit(tentar_agendar, i + 1) for i in range(num_threads)]
        for f in futures:
            ok, t_id, msg = f.result()
            if ok:
                sucessos += 1
            else:
                falhas += 1
            detalhes.append({"thread": t_id, "sucesso": ok, "mensagem": msg})

    app_logger.info(
        f"[SO Concorrência Threads] Simulação concluída: {sucessos} sucesso(s), {falhas} rejeição(ões)."
    )
    return {
        "total_requisicoes_simultaneas": num_threads,
        "agendamentos_efetuados": sucessos,
        "conflitos_prevenidos": falhas,
        "resultado_esperado": "Condição de corrida resolvida: Apenas 1 agendamento efetuado.",
        "detalhes": detalhes
    }


@app.post("/api/test/multiprocess-simulation")
def simulate_multiprocess_concurrency(num_processes: int = 5):
    """
    🔹 SO: Dispara N processos independentes do SO via multiprocessing (spawn)
    tentando agendar a mesma vaga simultaneamente. Demonstra a atuação real da
    trava de arquivo em nível de kernel (`msvcrt.locking` / `fcntl.flock`).
    """
    n_procs = max(2, min(num_processes, 10))
    medicos = scheduler_service.list_medicos()
    pacientes = scheduler_service.list_pacientes()
    if not medicos or not pacientes:
        raise HTTPException(status_code=400, detail="Cadastros insuficientes para teste.")

    medico = medicos[0]
    horarios = medico.get("horarios_disponiveis") or ["11:00"]
    horario_teste = "11:00" if "11:00" in horarios else horarios[0]
    data_teste = "2026-12-02"

    # Reset protegido da vaga antes da disputa multiprocesso
    with lock_manager.synchronized("schedule_lock"):
        with lock_manager.file_lock(scheduler_service.consultas_file):
            consultas = [
                c for c in file_manager.load_json(scheduler_service.consultas_file, default=[])
                if not (c["medico_id"] == medico["id"] and c["data"] == data_teste and c["horario"] == horario_teste)
            ]
            file_manager.save_json_atomic(scheduler_service.consultas_file, consultas)
            consultas_cache.invalidate("all_consultas")

    payloads = []
    for i in range(n_procs):
        pac = pacientes[i % len(pacientes)]
        payloads.append({
            "consultas_file": str(scheduler_service.consultas_file),
            "medicos_file": str(scheduler_service.medicos_file),
            "pacientes_file": str(scheduler_service.pacientes_file),
            "medico_id": medico["id"],
            "medico_nome": medico["nome"],
            "paciente_id": pac["id"],
            "paciente_nome": pac["nome"],
            "data": data_teste,
            "horario": horario_teste,
            "proc_num": i + 1,
            "encoding": os_adapter.default_encoding,
        })

    detalhes = run_multiprocess_tasks(multiprocess_schedule_worker, payloads, max_workers=n_procs)
    consultas_cache.invalidate("all_consultas")

    sucessos = sum(1 for d in detalhes if d["sucesso"])
    falhas = sum(1 for d in detalhes if not d["sucesso"])
    pids = [d["pid"] for d in detalhes]

    app_logger.info(
        f"[SO Multiprocessamento] {n_procs} processos (PIDs {pids}): "
        f"{sucessos} sucesso(s), {falhas} bloqueio(s) pelo Kernel File Lock."
    )
    return {
        "total_processos": n_procs,
        "agendamentos_efetuados": sucessos,
        "conflitos_prevenidos": falhas,
        "pids": pids,
        "resultado_esperado": "Condição de corrida entre processos resolvida: Apenas 1 agendamento efetuado.",
        "detalhes": detalhes,
    }
