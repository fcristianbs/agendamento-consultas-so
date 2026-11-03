# src/web/app.py
"""
Servidor Web e API RESTful - Sistema de Agendamento
Integrando a lógica médica com telemetria direta do Sistema Operacional.
"""

import os
import time
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from src.config.os_adapter import os_adapter, memory_monitor, consultas_cache
from src.storage.storage import setup_logger, FileManager, AutoBackupManager
from src.concurrent.concurrency import lock_manager, worker_pool
from src.core.scheduler import SchedulerService, Paciente, Medico, Consulta
from src.reports.reports import generate_pdf, export_csv

# Inicialização dos serviços fundamentais de SO
app_logger = setup_logger(os_adapter.logs_dir, os_adapter.default_encoding)
file_manager = FileManager(default_encoding=os_adapter.default_encoding)
backup_manager = AutoBackupManager(source_dir=os_adapter.consultas_dir, backup_dir=os_adapter.backups_dir)
scheduler_service = SchedulerService(file_manager=file_manager)

app = FastAPI(title="MedScheduler SO", version="1.0.0")

templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

@app.on_event("startup")
def on_startup():
    app_logger.info(f"Sistema inicializado no SO {os_adapter.system} (PID: {os.getpid()})")
    backup_manager.start()

@app.on_event("shutdown")
def on_shutdown():
    backup_manager.stop()
    worker_pool.shutdown()

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
# ENDPOINTS CRUD
# ==========================================
@app.get("/api/medicos")
def list_medicos():
    return scheduler_service.list_medicos()

@app.post("/api/medicos")
def create_medico(medico: Medico):
    try:
        return scheduler_service.create_medico(medico)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/medicos/{medico_id}")
def delete_medico(medico_id: str):
    if not scheduler_service.delete_medico(medico_id):
        raise HTTPException(status_code=404, detail="Médico não encontrado.")
    return {"message": "Médico removido com sucesso."}

@app.get("/api/pacientes")
def list_pacientes():
    return scheduler_service.list_pacientes()

@app.post("/api/pacientes")
def create_paciente(paciente: Paciente):
    try:
        return scheduler_service.create_paciente(paciente)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.delete("/api/pacientes/{paciente_id}")
def delete_paciente(paciente_id: str):
    if not scheduler_service.delete_paciente(paciente_id):
        raise HTTPException(status_code=404, detail="Paciente não encontrado.")
    return {"message": "Paciente removido com sucesso."}

@app.get("/api/consultas")
def list_consultas():
    return scheduler_service.list_consultas()

@app.get("/api/medicos/{medico_id}/horarios")
def get_horarios_disponiveis(medico_id: str, data: str):
    return scheduler_service.get_horarios_disponiveis(medico_id, data)

@app.post("/api/consultas")
def schedule_consulta(consulta: Consulta):
    try:
        res = scheduler_service.schedule_consulta(consulta)
        app_logger.info(f"[SO Agendamento] Consulta agendada: {res['id']} para {res['data']} às {res['horario']}")
        return res
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

@app.put("/api/consultas/{consulta_id}/cancelar")
def cancel_consulta(consulta_id: str):
    if not scheduler_service.cancel_consulta(consulta_id):
        raise HTTPException(status_code=404, detail="Consulta não encontrada.")
    return {"message": "Consulta cancelada com sucesso."}

# ==========================================
# ENDPOINTS DE RELATÓRIOS E BACKUP (I/O)
# ==========================================
@app.post("/api/reports/pdf")
def generate_pdf_report():
    consultas = scheduler_service.list_consultas()
    file_path = generate_pdf(consultas, os_adapter.relatorios_dir, os_adapter.system)
    filename = Path(file_path).name
    app_logger.info(f"[SO I/O] Relatório PDF gerado: {filename}")
    return {"filename": filename, "download_url": f"/api/reports/download/{filename}"}

@app.post("/api/reports/csv")
def generate_csv_report():
    consultas = scheduler_service.list_consultas()
    file_path = export_csv(consultas, os_adapter.relatorios_dir, file_manager)
    filename = Path(file_path).name
    app_logger.info(f"[SO I/O] Relatório CSV gerado: {filename}")
    return {"filename": filename, "download_url": f"/api/reports/download/{filename}"}

@app.get("/api/reports/download/{filename}")
def download_report(filename: str):
    file_path = os_adapter.relatorios_dir / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado.")
    return FileResponse(path=str(file_path), filename=filename, media_type="application/octet-stream")

@app.get("/api/reports/list")
def list_reports():
    results = []
    for f in sorted(os_adapter.relatorios_dir.glob("*.*"), key=lambda p: p.stat().st_mtime, reverse=True):
        if f.suffix in [".pdf", ".csv"]:
            results.append({
                "filename": f.name,
                "type": f.suffix.replace(".", "").upper(),
                "size_kb": round(f.stat().st_size / 1024, 2),
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f.stat().st_mtime)),
                "download_url": f"/api/reports/download/{f.name}"
            })
    return results

@app.post("/api/backups/create")
def trigger_backup():
    backup_path = backup_manager.create_backup()
    app_logger.info(f"[SO Backup] Snapshot manual criado: {Path(backup_path).name}")
    return {"message": "Backup criado com sucesso.", "file": Path(backup_path).name}

@app.get("/api/backups/list")
def list_backups():
    return backup_manager.list_backups()

# ==========================================
# ENDPOINTS DE TELEMETRIA E DEMONSTRAÇÃO DE SO
# ==========================================
@app.get("/api/os/telemetry")
def get_os_telemetry():
    return {
        "so": os_adapter.get_telemetry(),
        "memory": memory_monitor.get_info(),
        "concurrency": worker_pool.get_status()
    }

@app.post("/api/os/gc")
def trigger_gc():
    res = memory_monitor.run_garbage_collection([os_adapter.consultas_dir, os_adapter.relatorios_dir])
    app_logger.info(f"[SO Memória] Garbage Collection executado. Liberado: {res['ram_reclaimed_mb']} MB")
    return res

@app.get("/api/os/logs")
def get_logs(lines: int = 50):
    log_file = os_adapter.logs_dir / "sistema.log"
    if not log_file.exists():
        return {"logs": []}
    try:
        with open(log_file, "r", encoding=os_adapter.default_encoding, errors="replace") as f:
            return {"logs": f.readlines()[-lines:]}
    except Exception as e:
        return {"logs": [f"Erro ao ler log: {e}"]}

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
    data_teste = "2026-12-01"
    horario_teste = "10:00"

    # Reset da vaga para teste limpo
    consultas = [c for c in scheduler_service.list_consultas() if not (c["medico_id"] == medico["id"] and c["data"] == data_teste and c["horario"] == horario_teste)]
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

    return {
        "total_requisicoes_simultaneas": num_threads,
        "agendamentos_efetuados": sucessos,
        "conflitos_prevenidos": falhas,
        "resultado_esperado": "Condição de corrida resolvida: Apenas 1 agendamento efetuado.",
        "detalhes": detalhes
    }
