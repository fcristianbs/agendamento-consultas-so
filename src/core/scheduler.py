# src/core/scheduler.py
"""
Núcleo da Aplicação: Modelos de Dados e Regras de Agendamento
Demonstração de Conceitos de SO:
- Sincronização em seções críticas para prevenção de condições de corrida (Race Conditions)
- Cache em memória RAM para aceleração de leituras e redução de I/O em disco
- Persistência com escrita atômica garantida pelo sistema de arquivos
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.config.os_adapter import os_adapter, consultas_cache
from src.storage.storage import FileManager
from src.concurrent.concurrency import lock_manager

# ==========================================
# 1. MODELOS DE DADOS
# ==========================================
class Paciente(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    nome: str
    cpf: str
    telefone: str
    email: Optional[str] = ""

class Medico(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    nome: str
    crm: str
    especialidade: str
    horarios_disponiveis: List[str] = ["08:00", "09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]

class Consulta(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    paciente_id: str
    paciente_nome: str
    medico_id: str
    medico_nome: str
    data: str  # YYYY-MM-DD
    horario: str  # HH:MM
    status: str = "Agendada"  # "Agendada", "Concluída", "Cancelada"
    observacoes: Optional[str] = ""
    criado_em: Optional[str] = None

# ==========================================
# 2. SERVIÇO DE AGENDAMENTO (CRUD & REGRAS DE CONFLITO)
# ==========================================
class SchedulerService:
    def __init__(self, file_manager: FileManager):
        self.file_manager = file_manager
        self.pacientes_file = os_adapter.consultas_dir / "pacientes.json"
        self.medicos_file = os_adapter.consultas_dir / "medicos.json"
        self.consultas_file = os_adapter.consultas_dir / "consultas.json"
        self._ensure_initial_data()

    def _ensure_initial_data(self) -> None:
        if not self.medicos_file.exists():
            medicos = [
                Medico(id="med-001", nome="Dra. Ana Paula Silveira", crm="12345-SP", especialidade="Cardiologia").model_dump(),
                Medico(id="med-002", nome="Dr. Carlos Eduardo Lima", crm="67890-SP", especialidade="Ortopedia").model_dump(),
                Medico(id="med-003", nome="Dra. Mariana Costa", crm="54321-RJ", especialidade="Dermatologia").model_dump(),
            ]
            self.file_manager.save_json_atomic(self.medicos_file, medicos)

        if not self.pacientes_file.exists():
            pacientes = [
                Paciente(id="pac-001", nome="João da Silva", cpf="111.222.333-44", telefone="(11) 98765-4321", email="joao@email.com").model_dump(),
                Paciente(id="pac-002", nome="Maria Santos", cpf="222.333.444-55", telefone="(11) 91234-5678", email="maria@email.com").model_dump(),
                Paciente(id="pac-003", nome="Roberto Fernandes", cpf="333.444.555-66", telefone="(21) 99887-7665", email="roberto@email.com").model_dump(),
            ]
            self.file_manager.save_json_atomic(self.pacientes_file, pacientes)

        if not self.consultas_file.exists():
            hoje = datetime.now().strftime("%Y-%m-%d")
            consultas = [
                Consulta(
                    id="con-001",
                    paciente_id="pac-001",
                    paciente_nome="João da Silva",
                    medico_id="med-001",
                    medico_nome="Dra. Ana Paula Silveira",
                    data=hoje,
                    horario="09:00",
                    status="Agendada",
                    observacoes="Consulta inicial de rotina",
                    criado_em=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                ).model_dump()
            ]
            self.file_manager.save_json_atomic(self.consultas_file, consultas)

    # --- MÉDICOS ---
    def list_medicos(self) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_medicos")
        if cached is not None:
            return cached
        medicos = self.file_manager.load_json(self.medicos_file, default=[])
        consultas_cache.put("all_medicos", medicos)
        return medicos

    def create_medico(self, medico: Medico) -> Dict[str, Any]:
        with lock_manager.synchronized("medicos_write"):
            medicos = self.file_manager.load_json(self.medicos_file, default=[])
            if any(m["crm"] == medico.crm for m in medicos):
                raise ValueError(f"CRM {medico.crm} já cadastrado.")
            novo = medico.model_dump()
            medicos.append(novo)
            self.file_manager.save_json_atomic(self.medicos_file, medicos)
            consultas_cache.invalidate("all_medicos")
            return novo

    def delete_medico(self, medico_id: str) -> bool:
        with lock_manager.synchronized("medicos_write"):
            medicos = self.file_manager.load_json(self.medicos_file, default=[])
            filtrados = [m for m in medicos if m["id"] != medico_id]
            if len(filtrados) == len(medicos):
                return False
            self.file_manager.save_json_atomic(self.medicos_file, filtrados)
            consultas_cache.invalidate("all_medicos")
            return True

    # --- PACIENTES ---
    def list_pacientes(self) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_pacientes")
        if cached is not None:
            return cached
        pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
        consultas_cache.put("all_pacientes", pacientes)
        return pacientes

    def create_paciente(self, paciente: Paciente) -> Dict[str, Any]:
        with lock_manager.synchronized("pacientes_write"):
            pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
            if any(p["cpf"] == paciente.cpf for p in pacientes):
                raise ValueError(f"CPF {paciente.cpf} já cadastrado.")
            novo = paciente.model_dump()
            pacientes.append(novo)
            self.file_manager.save_json_atomic(self.pacientes_file, pacientes)
            consultas_cache.invalidate("all_pacientes")
            return novo

    def delete_paciente(self, paciente_id: str) -> bool:
        with lock_manager.synchronized("pacientes_write"):
            pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
            filtrados = [p for p in pacientes if p["id"] != paciente_id]
            if len(filtrados) == len(pacientes):
                return False
            self.file_manager.save_json_atomic(self.pacientes_file, filtrados)
            consultas_cache.invalidate("all_pacientes")
            return True

    # --- CONSULTAS & CONFLITOS ---
    def list_consultas(self) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_consultas")
        if cached is not None:
            return cached
        consultas = self.file_manager.load_json(self.consultas_file, default=[])
        consultas_cache.put("all_consultas", consultas)
        return consultas

    def get_horarios_disponiveis(self, medico_id: str, data: str) -> List[str]:
        medicos = self.list_medicos()
        medico = next((m for m in medicos if m["id"] == medico_id), None)
        if not medico:
            return []
        todos = medico.get("horarios_disponiveis", ["08:00", "09:00", "10:00", "11:00", "14:00", "15:00", "16:00"])
        consultas = self.list_consultas()
        ocupados = {c["horario"] for c in consultas if c["medico_id"] == medico_id and c["data"] == data and c["status"] != "Cancelada"}
        return [h for h in todos if h not in ocupados]

    def schedule_consulta(self, consulta: Consulta) -> Dict[str, Any]:
        """
        Seção Crítica com Lock de Exclusão Mútua:
        Evita Race Conditions em requisições concorrentes.
        """
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])

                # Conflito 1: Médico já ocupado
                if any(c["medico_id"] == consulta.medico_id and c["data"] == consulta.data and c["horario"] == consulta.horario and c["status"] != "Cancelada" for c in consultas):
                    raise ValueError(f"Conflito de horário: O médico já possui consulta em {consulta.data} às {consulta.horario}.")

                # Conflito 2: Paciente já tem consulta no mesmo horário
                if any(c["paciente_id"] == consulta.paciente_id and c["data"] == consulta.data and c["horario"] == consulta.horario and c["status"] != "Cancelada" for c in consultas):
                    raise ValueError(f"O paciente já possui consulta agendada para {consulta.data} às {consulta.horario}.")

                if not consulta.criado_em:
                    consulta.criado_em = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                nova = consulta.model_dump()
                consultas.append(nova)
                self.file_manager.save_json_atomic(self.consultas_file, consultas)
                consultas_cache.invalidate("all_consultas")
                return nova

    def cancel_consulta(self, consulta_id: str) -> bool:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                for i, c in enumerate(consultas):
                    if c["id"] == consulta_id:
                        consultas[i]["status"] = "Cancelada"
                        self.file_manager.save_json_atomic(self.consultas_file, consultas)
                        consultas_cache.invalidate("all_consultas")
                        return True
                return False
