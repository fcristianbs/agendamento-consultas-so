# src/core/scheduler.py
"""
Núcleo da Aplicação: Modelos de Dados e Regras de Agendamento
Demonstração de Conceitos de SO:
- Sincronização em seções críticas para prevenção de condições de corrida (Race Conditions)
- Travas de kernel entre processos independentes (multiprocess_schedule_worker)
- Cache em memória RAM para aceleração de leituras e redução de I/O em disco
- Persistência com escrita atômica garantida pelo sistema de arquivos
"""

import os
import re
import time
import uuid
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any, Optional
from pydantic import BaseModel, Field

from src.config.os_adapter import os_adapter, consultas_cache
from src.storage.storage import FileManager
from src.concurrent.concurrency import lock_manager

_sched_logger = logging.getLogger("sistema_agendamento")

_DATA_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_HORARIO_RE = re.compile(r"^\d{2}:\d{2}$")


# ==========================================
# EXCEÇÕES DE DOMÍNIO (COMPATÍVEIS COM ValueError)
# ==========================================
class ValidationError(ValueError):
    """Erro de validação de formato ou regra de negócio (HTTP 400)."""


class NotFoundError(ValueError):
    """Recurso não encontrado (HTTP 404)."""


class ConflictError(ValueError):
    """Conflito de concorrência, duplicidade ou integridade referencial (HTTP 409)."""


# ==========================================
# FUNÇÕES AUXILIARES DE VALIDAÇÃO
# ==========================================
def validate_data_str(data: str) -> str:
    if not isinstance(data, str) or not _DATA_RE.match(data.strip()):
        raise ValidationError(f"Formato de data inválido: '{data}'. Use YYYY-MM-DD.")
    data = data.strip()
    try:
        datetime.strptime(data, "%Y-%m-%d")
    except ValueError:
        raise ValidationError(f"Data inexistente no calendário: '{data}'.")
    return data


def validate_horario_str(horario: str) -> str:
    if not isinstance(horario, str) or not _HORARIO_RE.match(horario.strip()):
        raise ValidationError(f"Formato de horário inválido: '{horario}'. Use HH:MM.")
    horario = horario.strip()
    try:
        datetime.strptime(horario, "%H:%M")
    except ValueError:
        raise ValidationError(f"Horário inválido: '{horario}'. Use intervalo de 00:00 a 23:59.")
    return horario


def validate_not_in_past(data: str, horario: str) -> None:
    dt_consulta = datetime.strptime(f"{data} {horario}", "%Y-%m-%d %H:%M")
    if dt_consulta < datetime.now():
        raise ValidationError(f"Não é permitido agendar ou remarcar no passado ({data} às {horario}).")


def normalize_horarios_list(horarios: List[str]) -> List[str]:
    validados = [validate_horario_str(h) for h in horarios]
    if len(set(validados)) != len(validados):
        raise ValidationError("A lista de horários não pode conter valores duplicados.")
    return sorted(validados)


# ==========================================
# 1. MODELOS DE DADOS
# ==========================================
class Paciente(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    nome: str
    cpf: str
    telefone: str
    email: Optional[str] = ""


class PacienteUpdate(BaseModel):
    nome: Optional[str] = None
    cpf: Optional[str] = None
    telefone: Optional[str] = None
    email: Optional[str] = None


class Medico(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    nome: str
    crm: str
    especialidade: str
    horarios_disponiveis: List[str] = ["08:00", "09:00", "10:00", "11:00", "14:00", "15:00", "16:00"]


class MedicoUpdate(BaseModel):
    nome: Optional[str] = None
    crm: Optional[str] = None
    especialidade: Optional[str] = None
    horarios_disponiveis: Optional[List[str]] = None


class HorarioCreate(BaseModel):
    horario: str


class HorariosReplace(BaseModel):
    horarios: List[str]


class Consulta(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    paciente_id: str
    paciente_nome: str = ""
    medico_id: str
    medico_nome: str = ""
    data: str  # YYYY-MM-DD
    horario: str  # HH:MM
    status: str = "Agendada"  # "Agendada", "Concluída", "Cancelada"
    observacoes: Optional[str] = ""
    criado_em: Optional[str] = None


class ConsultaUpdate(BaseModel):
    data: Optional[str] = None
    horario: Optional[str] = None
    observacoes: Optional[str] = None


# ==========================================
# 2. WORKER MULTIPROCESSAMENTO (NÍVEL DE MÓDULO PARA SPAWN NO WINDOWS)
# ==========================================
def multiprocess_schedule_worker(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    🔹 SO: Função de nível de módulo executada em um processo independente (multiprocessing spawn).
    Não depende de estado global em RAM nem de mutex em memória: a exclusão mútua é garantida
    exclusivamente pela trava de arquivo no kernel do SO (`file_lock`).
    """
    pid = os.getpid()
    proc_num = payload["proc_num"]
    consultas_file = Path(payload["consultas_file"])
    fm = FileManager(default_encoding=payload.get("encoding", "utf-8"))

    # Pequeno atraso para aumentar sobreposição simultânea entre processos filhos
    time.sleep(0.03)

    with lock_manager.file_lock(consultas_file):
        consultas = fm.load_json(consultas_file, default=[])
        conflito_medico = any(
            c["medico_id"] == payload["medico_id"]
            and c["data"] == payload["data"]
            and c["horario"] == payload["horario"]
            and c["status"] != "Cancelada"
            for c in consultas
        )
        if conflito_medico:
            return {
                "processo": proc_num,
                "pid": pid,
                "sucesso": False,
                "mensagem": f"Bloqueado pelo Kernel File Lock (PID {pid}): vaga já ocupada.",
            }

        nova = Consulta(
            paciente_id=payload["paciente_id"],
            paciente_nome=f"{payload['paciente_nome']} (Proc #{proc_num} PID:{pid})",
            medico_id=payload["medico_id"],
            medico_nome=payload["medico_nome"],
            data=payload["data"],
            horario=payload["horario"],
            observacoes="Disparado pelo teste multiprocesso de SO",
            criado_em=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ).model_dump()
        consultas.append(nova)
        fm.save_json_atomic(consultas_file, consultas)
        return {
            "processo": proc_num,
            "pid": pid,
            "sucesso": True,
            "mensagem": f"Sucesso (PID {pid}): vaga reservada com exclusividade no kernel.",
        }


# ==========================================
# 3. SERVIÇO DE AGENDAMENTO (CRUD & REGRAS DE CONFLITO)
# ==========================================
class SchedulerService:
    def __init__(self, file_manager: FileManager, data_dir: Optional[Path] = None):
        self.file_manager = file_manager
        base = Path(data_dir) if data_dir is not None else os_adapter.consultas_dir
        base.mkdir(parents=True, exist_ok=True)
        self.pacientes_file = base / "pacientes.json"
        self.medicos_file = base / "medicos.json"
        self.consultas_file = base / "consultas.json"
        self._ensure_initial_data()

    def _ensure_initial_data(self) -> None:
        if not self.medicos_file.exists():
            with lock_manager.file_lock(self.medicos_file):
                if not self.medicos_file.exists():
                    medicos = [
                        Medico(id="med-001", nome="Dra. Ana Paula Silveira", crm="12345-SP", especialidade="Cardiologia").model_dump(),
                        Medico(id="med-002", nome="Dr. Carlos Eduardo Lima", crm="67890-SP", especialidade="Ortopedia").model_dump(),
                        Medico(id="med-003", nome="Dra. Mariana Costa", crm="54321-RJ", especialidade="Dermatologia").model_dump(),
                    ]
                    self.file_manager.save_json_atomic(self.medicos_file, medicos)

        if not self.pacientes_file.exists():
            with lock_manager.file_lock(self.pacientes_file):
                if not self.pacientes_file.exists():
                    pacientes = [
                        Paciente(id="pac-001", nome="João da Silva", cpf="111.222.333-44", telefone="(11) 98765-4321", email="joao@email.com").model_dump(),
                        Paciente(id="pac-002", nome="Maria Santos", cpf="222.333.444-55", telefone="(11) 91234-5678", email="maria@email.com").model_dump(),
                        Paciente(id="pac-003", nome="Roberto Fernandes", cpf="333.444.555-66", telefone="(21) 99887-7665", email="roberto@email.com").model_dump(),
                    ]
                    self.file_manager.save_json_atomic(self.pacientes_file, pacientes)

        if not self.consultas_file.exists():
            with lock_manager.file_lock(self.consultas_file):
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

    async def list_medicos_async(self, executor: Any = None) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_medicos")
        if cached is not None:
            return cached
        medicos = await self.file_manager.load_json_async(self.medicos_file, default=[], executor=executor)
        consultas_cache.put("all_medicos", medicos)
        return medicos

    def get_medico(self, medico_id: str) -> Optional[Dict[str, Any]]:
        return next((m for m in self.list_medicos() if m["id"] == medico_id), None)

    async def get_medico_async(self, medico_id: str, executor: Any = None) -> Optional[Dict[str, Any]]:
        medicos = await self.list_medicos_async(executor=executor)
        return next((m for m in medicos if m["id"] == medico_id), None)

    def create_medico(self, medico: Medico) -> Dict[str, Any]:
        medico.horarios_disponiveis = normalize_horarios_list(medico.horarios_disponiveis)
        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                if any(os_adapter.names_equal(m["crm"], medico.crm) for m in medicos):
                    raise ConflictError(f"CRM {medico.crm} já cadastrado.")
                novo = medico.model_dump()
                medicos.append(novo)
                self.file_manager.save_json_atomic(self.medicos_file, medicos)
                consultas_cache.invalidate("all_medicos")
                _sched_logger.info(f"[SO CRUD] Médico cadastrado: {novo['id']} ({novo['nome']})")
                return novo

    def update_medico(self, medico_id: str, dados: MedicoUpdate) -> Dict[str, Any]:
        if dados.horarios_disponiveis is not None:
            horarios_validados = normalize_horarios_list(dados.horarios_disponiveis)
        else:
            horarios_validados = None

        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                idx = next((i for i, m in enumerate(medicos) if m["id"] == medico_id), None)
                if idx is None:
                    raise NotFoundError(f"Médico '{medico_id}' não encontrado.")

                atual = medicos[idx]
                novo_crm = dados.crm.strip() if dados.crm is not None else atual["crm"]
                if any(m["id"] != medico_id and os_adapter.names_equal(m["crm"], novo_crm) for m in medicos):
                    raise ConflictError(f"CRM {novo_crm} já cadastrado para outro médico.")

                nome_antigo = atual["nome"]
                if dados.nome is not None:
                    atual["nome"] = dados.nome.strip()
                atual["crm"] = novo_crm
                if dados.especialidade is not None:
                    atual["especialidade"] = dados.especialidade.strip()
                if horarios_validados is not None:
                    atual["horarios_disponiveis"] = horarios_validados

                medicos[idx] = atual
                self.file_manager.save_json_atomic(self.medicos_file, medicos)
                consultas_cache.invalidate("all_medicos")

                if atual["nome"] != nome_antigo:
                    self._propagate_medico_nome(medico_id, atual["nome"])

                _sched_logger.info(f"[SO CRUD] Médico atualizado: {medico_id} ({atual['nome']})")
                return atual

    def _propagate_medico_nome(self, medico_id: str, novo_nome: str) -> None:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                changed = False
                for c in consultas:
                    if c["medico_id"] == medico_id and c.get("medico_nome") != novo_nome:
                        c["medico_nome"] = novo_nome
                        changed = True
                if changed:
                    self.file_manager.save_json_atomic(self.consultas_file, consultas)
                    consultas_cache.invalidate("all_consultas")

    def delete_medico(self, medico_id: str) -> bool:
        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                if not any(m["id"] == medico_id for m in medicos):
                    return False

                with lock_manager.synchronized("schedule_lock"):
                    with lock_manager.file_lock(self.consultas_file):
                        consultas = self.file_manager.load_json(self.consultas_file, default=[])
                        ativas = [
                            c for c in consultas
                            if c["medico_id"] == medico_id and c["status"] == "Agendada"
                        ]
                        if ativas:
                            raise ConflictError(
                                f"Não é possível excluir o médico '{medico_id}': "
                                f"existem {len(ativas)} consulta(s) com status 'Agendada'."
                            )

                filtrados = [m for m in medicos if m["id"] != medico_id]
                self.file_manager.save_json_atomic(self.medicos_file, filtrados)
                consultas_cache.invalidate("all_medicos")
                _sched_logger.info(f"[SO CRUD] Médico excluído: {medico_id}")
                return True

    # --- HORÁRIOS DE MÉDICOS ---
    def get_horarios_disponiveis(self, medico_id: str, data: Optional[str] = None) -> List[str]:
        medico = self.get_medico(medico_id)
        if not medico:
            raise NotFoundError(f"Médico '{medico_id}' não encontrado.")
        todos = sorted(medico.get("horarios_disponiveis", []))
        if data is None:
            return todos
        data_validada = validate_data_str(data)
        consultas = self.list_consultas()
        ocupados = {
            c["horario"]
            for c in consultas
            if c["medico_id"] == medico_id and c["data"] == data_validada and c["status"] != "Cancelada"
        }
        return [h for h in todos if h not in ocupados]

    async def get_horarios_disponiveis_async(
        self, medico_id: str, data: Optional[str] = None, executor: Any = None
    ) -> List[str]:
        medico = await self.get_medico_async(medico_id, executor=executor)
        if not medico:
            raise NotFoundError(f"Médico '{medico_id}' não encontrado.")
        todos = sorted(medico.get("horarios_disponiveis", []))
        if data is None:
            return todos
        data_validada = validate_data_str(data)
        consultas = await self.list_consultas_async(executor=executor)
        ocupados = {
            c["horario"]
            for c in consultas
            if c["medico_id"] == medico_id and c["data"] == data_validada and c["status"] != "Cancelada"
        }
        return [h for h in todos if h not in ocupados]

    def add_horario_medico(self, medico_id: str, horario: str) -> List[str]:
        horario_val = validate_horario_str(horario)
        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                idx = next((i for i, m in enumerate(medicos) if m["id"] == medico_id), None)
                if idx is None:
                    raise NotFoundError(f"Médico '{medico_id}' não encontrado.")
                horarios = list(medicos[idx].get("horarios_disponiveis", []))
                if horario_val in horarios:
                    raise ValidationError(f"Horário {horario_val} já existe na agenda do médico.")
                horarios.append(horario_val)
                medicos[idx]["horarios_disponiveis"] = sorted(horarios)
                self.file_manager.save_json_atomic(self.medicos_file, medicos)
                consultas_cache.invalidate("all_medicos")
                _sched_logger.info(f"[SO CRUD] Horário {horario_val} adicionado ao médico {medico_id}")
                return medicos[idx]["horarios_disponiveis"]

    def replace_horarios_medico(self, medico_id: str, horarios: List[str]) -> List[str]:
        validados = normalize_horarios_list(horarios)
        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                idx = next((i for i, m in enumerate(medicos) if m["id"] == medico_id), None)
                if idx is None:
                    raise NotFoundError(f"Médico '{medico_id}' não encontrado.")
                medicos[idx]["horarios_disponiveis"] = validados
                self.file_manager.save_json_atomic(self.medicos_file, medicos)
                consultas_cache.invalidate("all_medicos")
                _sched_logger.info(f"[SO CRUD] Horários substituídos para o médico {medico_id}: {validados}")
                return validados

    def delete_horario_medico(self, medico_id: str, horario: str) -> List[str]:
        horario_val = validate_horario_str(horario)
        with lock_manager.synchronized("medicos_write"):
            with lock_manager.file_lock(self.medicos_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                idx = next((i for i, m in enumerate(medicos) if m["id"] == medico_id), None)
                if idx is None:
                    raise NotFoundError(f"Médico '{medico_id}' não encontrado.")
                horarios = list(medicos[idx].get("horarios_disponiveis", []))
                if horario_val not in horarios:
                    raise NotFoundError(f"Horário {horario_val} não encontrado para o médico '{medico_id}'.")
                horarios.remove(horario_val)
                medicos[idx]["horarios_disponiveis"] = sorted(horarios)
                self.file_manager.save_json_atomic(self.medicos_file, medicos)
                consultas_cache.invalidate("all_medicos")
                _sched_logger.info(f"[SO CRUD] Horário {horario_val} removido do médico {medico_id}")
                return medicos[idx]["horarios_disponiveis"]

    # --- PACIENTES ---
    def list_pacientes(self) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_pacientes")
        if cached is not None:
            return cached
        pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
        consultas_cache.put("all_pacientes", pacientes)
        return pacientes

    async def list_pacientes_async(self, executor: Any = None) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_pacientes")
        if cached is not None:
            return cached
        pacientes = await self.file_manager.load_json_async(self.pacientes_file, default=[], executor=executor)
        consultas_cache.put("all_pacientes", pacientes)
        return pacientes

    def get_paciente(self, paciente_id: str) -> Optional[Dict[str, Any]]:
        return next((p for p in self.list_pacientes() if p["id"] == paciente_id), None)

    async def get_paciente_async(self, paciente_id: str, executor: Any = None) -> Optional[Dict[str, Any]]:
        pacientes = await self.list_pacientes_async(executor=executor)
        return next((p for p in pacientes if p["id"] == paciente_id), None)

    def create_paciente(self, paciente: Paciente) -> Dict[str, Any]:
        with lock_manager.synchronized("pacientes_write"):
            with lock_manager.file_lock(self.pacientes_file):
                pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
                if any(p["cpf"] == paciente.cpf for p in pacientes):
                    raise ConflictError(f"CPF {paciente.cpf} já cadastrado.")
                novo = paciente.model_dump()
                pacientes.append(novo)
                self.file_manager.save_json_atomic(self.pacientes_file, pacientes)
                consultas_cache.invalidate("all_pacientes")
                _sched_logger.info(f"[SO CRUD] Paciente cadastrado: {novo['id']} ({novo['nome']})")
                return novo

    def update_paciente(self, paciente_id: str, dados: PacienteUpdate) -> Dict[str, Any]:
        with lock_manager.synchronized("pacientes_write"):
            with lock_manager.file_lock(self.pacientes_file):
                pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
                idx = next((i for i, p in enumerate(pacientes) if p["id"] == paciente_id), None)
                if idx is None:
                    raise NotFoundError(f"Paciente '{paciente_id}' não encontrado.")

                atual = pacientes[idx]
                novo_cpf = dados.cpf.strip() if dados.cpf is not None else atual["cpf"]
                if any(p["id"] != paciente_id and p["cpf"] == novo_cpf for p in pacientes):
                    raise ConflictError(f"CPF {novo_cpf} já cadastrado para outro paciente.")

                nome_antigo = atual["nome"]
                if dados.nome is not None:
                    atual["nome"] = dados.nome.strip()
                atual["cpf"] = novo_cpf
                if dados.telefone is not None:
                    atual["telefone"] = dados.telefone.strip()
                if dados.email is not None:
                    atual["email"] = dados.email.strip()

                pacientes[idx] = atual
                self.file_manager.save_json_atomic(self.pacientes_file, pacientes)
                consultas_cache.invalidate("all_pacientes")

                if atual["nome"] != nome_antigo:
                    self._propagate_paciente_nome(paciente_id, atual["nome"])

                _sched_logger.info(f"[SO CRUD] Paciente atualizado: {paciente_id} ({atual['nome']})")
                return atual

    def _propagate_paciente_nome(self, paciente_id: str, novo_nome: str) -> None:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                changed = False
                for c in consultas:
                    if c["paciente_id"] == paciente_id and c.get("paciente_nome") != novo_nome:
                        c["paciente_nome"] = novo_nome
                        changed = True
                if changed:
                    self.file_manager.save_json_atomic(self.consultas_file, consultas)
                    consultas_cache.invalidate("all_consultas")

    def delete_paciente(self, paciente_id: str) -> bool:
        with lock_manager.synchronized("pacientes_write"):
            with lock_manager.file_lock(self.pacientes_file):
                pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
                if not any(p["id"] == paciente_id for p in pacientes):
                    return False

                with lock_manager.synchronized("schedule_lock"):
                    with lock_manager.file_lock(self.consultas_file):
                        consultas = self.file_manager.load_json(self.consultas_file, default=[])
                        ativas = [
                            c for c in consultas
                            if c["paciente_id"] == paciente_id and c["status"] == "Agendada"
                        ]
                        if ativas:
                            raise ConflictError(
                                f"Não é possível excluir o paciente '{paciente_id}': "
                                f"existem {len(ativas)} consulta(s) com status 'Agendada'."
                            )

                filtrados = [p for p in pacientes if p["id"] != paciente_id]
                self.file_manager.save_json_atomic(self.pacientes_file, filtrados)
                consultas_cache.invalidate("all_pacientes")
                _sched_logger.info(f"[SO CRUD] Paciente excluído: {paciente_id}")
                return True

    # --- CONSULTAS & CONFLITOS ---
    def list_consultas(self) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_consultas")
        if cached is not None:
            return cached
        consultas = self.file_manager.load_json(self.consultas_file, default=[])
        consultas_cache.put("all_consultas", consultas)
        return consultas

    async def list_consultas_async(self, executor: Any = None) -> List[Dict[str, Any]]:
        cached = consultas_cache.get("all_consultas")
        if cached is not None:
            return cached
        consultas = await self.file_manager.load_json_async(self.consultas_file, default=[], executor=executor)
        consultas_cache.put("all_consultas", consultas)
        return consultas

    def get_consulta(self, consulta_id: str) -> Optional[Dict[str, Any]]:
        return next((c for c in self.list_consultas() if c["id"] == consulta_id), None)

    async def get_consulta_async(self, consulta_id: str, executor: Any = None) -> Optional[Dict[str, Any]]:
        consultas = await self.list_consultas_async(executor=executor)
        return next((c for c in consultas if c["id"] == consulta_id), None)

    def schedule_consulta(self, consulta: Consulta) -> Dict[str, Any]:
        """
        🔹 SO: Seção Crítica com Duplo Lock (Mutex em memória + Trava de Kernel em arquivo):
        Valida regras de negócio e evita Race Conditions em requisições concorrentes.
        """
        data_val = validate_data_str(consulta.data)
        horario_val = validate_horario_str(consulta.horario)
        validate_not_in_past(data_val, horario_val)
        consulta.data = data_val
        consulta.horario = horario_val

        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                medicos = self.file_manager.load_json(self.medicos_file, default=[])
                medico = next((m for m in medicos if m["id"] == consulta.medico_id), None)
                if not medico:
                    raise NotFoundError(f"Médico '{consulta.medico_id}' não encontrado.")

                pacientes = self.file_manager.load_json(self.pacientes_file, default=[])
                paciente = next((p for p in pacientes if p["id"] == consulta.paciente_id), None)
                if not paciente:
                    raise NotFoundError(f"Paciente '{consulta.paciente_id}' não encontrado.")

                if horario_val not in medico.get("horarios_disponiveis", []):
                    raise ValidationError(
                        f"O horário {horario_val} não faz parte dos horários disponíveis de {medico['nome']}."
                    )

                consultas = self.file_manager.load_json(self.consultas_file, default=[])

                # Conflito 1: Médico já ocupado
                if any(
                    c["medico_id"] == consulta.medico_id
                    and c["data"] == data_val
                    and c["horario"] == horario_val
                    and c["status"] != "Cancelada"
                    for c in consultas
                ):
                    raise ConflictError(
                        f"Conflito de horário: O médico já possui consulta em {data_val} às {horario_val}."
                    )

                # Conflito 2: Paciente já tem consulta no mesmo horário
                if any(
                    c["paciente_id"] == consulta.paciente_id
                    and c["data"] == data_val
                    and c["horario"] == horario_val
                    and c["status"] != "Cancelada"
                    for c in consultas
                ):
                    raise ConflictError(
                        f"O paciente já possui consulta agendada para {data_val} às {horario_val}."
                    )

                if not consulta.paciente_nome:
                    consulta.paciente_nome = paciente["nome"]
                if not consulta.medico_nome:
                    consulta.medico_nome = medico["nome"]
                if not consulta.criado_em:
                    consulta.criado_em = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                nova = consulta.model_dump()
                consultas.append(nova)
                self.file_manager.save_json_atomic(self.consultas_file, consultas)
                consultas_cache.invalidate("all_consultas")
                return nova

    def update_consulta(self, consulta_id: str, dados: ConsultaUpdate) -> Dict[str, Any]:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                idx = next((i for i, c in enumerate(consultas) if c["id"] == consulta_id), None)
                if idx is None:
                    raise NotFoundError(f"Consulta '{consulta_id}' não encontrada.")

                atual = consultas[idx]
                nova_data = validate_data_str(dados.data) if dados.data is not None else atual["data"]
                novo_horario = validate_horario_str(dados.horario) if dados.horario is not None else atual["horario"]
                alterou_agenda = (nova_data != atual["data"]) or (novo_horario != atual["horario"])

                if alterou_agenda:
                    if atual["status"] != "Agendada":
                        raise ConflictError("Apenas consultas com status 'Agendada' podem ser remarcadas.")
                    validate_not_in_past(nova_data, novo_horario)

                    medicos = self.file_manager.load_json(self.medicos_file, default=[])
                    medico = next((m for m in medicos if m["id"] == atual["medico_id"]), None)
                    if not medico:
                        raise NotFoundError(f"Médico '{atual['medico_id']}' não encontrado.")
                    if novo_horario not in medico.get("horarios_disponiveis", []):
                        raise ValidationError(
                            f"O horário {novo_horario} não está disponível na agenda de {medico['nome']}."
                        )

                    if any(
                        c["id"] != consulta_id
                        and c["medico_id"] == atual["medico_id"]
                        and c["data"] == nova_data
                        and c["horario"] == novo_horario
                        and c["status"] != "Cancelada"
                        for c in consultas
                    ):
                        raise ConflictError(
                            f"Conflito de horário: O médico já possui consulta em {nova_data} às {novo_horario}."
                        )

                    if any(
                        c["id"] != consulta_id
                        and c["paciente_id"] == atual["paciente_id"]
                        and c["data"] == nova_data
                        and c["horario"] == novo_horario
                        and c["status"] != "Cancelada"
                        for c in consultas
                    ):
                        raise ConflictError(
                            f"O paciente já possui consulta agendada para {nova_data} às {novo_horario}."
                        )

                atual["data"] = nova_data
                atual["horario"] = novo_horario
                if dados.observacoes is not None:
                    atual["observacoes"] = dados.observacoes

                consultas[idx] = atual
                self.file_manager.save_json_atomic(self.consultas_file, consultas)
                consultas_cache.invalidate("all_consultas")
                _sched_logger.info(
                    f"[SO CRUD] Consulta atualizada: {consulta_id} ({atual['data']} {atual['horario']})"
                )
                return atual

    def cancel_consulta(self, consulta_id: str) -> bool:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                for i, c in enumerate(consultas):
                    if c["id"] == consulta_id:
                        consultas[i]["status"] = "Cancelada"
                        self.file_manager.save_json_atomic(self.consultas_file, consultas)
                        consultas_cache.invalidate("all_consultas")
                        _sched_logger.info(f"[SO CRUD] Consulta cancelada: {consulta_id}")
                        return True
                return False

    def conclude_consulta(self, consulta_id: str) -> Dict[str, Any]:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                for i, c in enumerate(consultas):
                    if c["id"] == consulta_id:
                        if c["status"] == "Cancelada":
                            raise ConflictError("Não é possível concluir uma consulta já cancelada.")
                        consultas[i]["status"] = "Concluída"
                        self.file_manager.save_json_atomic(self.consultas_file, consultas)
                        consultas_cache.invalidate("all_consultas")
                        _sched_logger.info(f"[SO CRUD] Consulta concluída: {consulta_id}")
                        return consultas[i]
                raise NotFoundError(f"Consulta '{consulta_id}' não encontrada.")

    def delete_consulta(self, consulta_id: str) -> bool:
        with lock_manager.synchronized("schedule_lock"):
            with lock_manager.file_lock(self.consultas_file):
                consultas = self.file_manager.load_json(self.consultas_file, default=[])
                filtradas = [c for c in consultas if c["id"] != consulta_id]
                if len(filtradas) == len(consultas):
                    return False
                self.file_manager.save_json_atomic(self.consultas_file, filtradas)
                consultas_cache.invalidate("all_consultas")
                _sched_logger.info(f"[SO CRUD] Consulta removida: {consulta_id}")
                return True
