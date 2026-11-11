# tests/test_concurrency.py
"""
Suite de Testes Automatizados para Validação dos Conceitos de Sistemas Operacionais:
1-6: Testes originais (SO Adapter, Escrita Atômica, Cache LRU, Concorrência Threads, Relatórios/Backup, GC)
7-16: CRUD completo & horários, Regras de negócio, Bloqueio de exclusão, Multiprocessamento,
      Jobs assíncronos no WorkerPool, Formato de Log (fuso + 3 níveis), Path Traversal,
      Sanitização e recuperação de JSON corrompido, CleanupDaemon e tracemalloc/WeakRef.
"""

import os
import re
import sys
import gc
import time
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

# Isolamento total em diretório temporário para nunca sujar ./data durante os testes
# Em processos filhos (Windows spawn), AGENDAMENTO_DATA_DIR já vem herdada do processo pai.
_TEMP_ROOT = None
if "AGENDAMENTO_DATA_DIR" not in os.environ:
    _TEMP_ROOT = tempfile.TemporaryDirectory(prefix="agendamento_so_test_")
    os.environ["AGENDAMENTO_DATA_DIR"] = str(Path(_TEMP_ROOT.name) / "data")
os.environ.setdefault("LOG_LEVEL", "DEBUG")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402
from src.config.os_adapter import os_adapter, consultas_cache, memory_monitor, LRUCache, CleanupDaemon  # noqa: E402
from src.storage.storage import FileManager, AutoBackupManager  # noqa: E402
from src.concurrent.concurrency import lock_manager, worker_pool  # noqa: E402
from src.core.scheduler import SchedulerService, Paciente, Medico, Consulta  # noqa: E402
from src.reports.reports import generate_pdf, export_csv  # noqa: E402
from src.web.app import app  # noqa: E402


def tearDownModule():
    # Fecha handlers de log antes de limpar o diretório temporário no Windows
    import logging
    logger = logging.getLogger("sistema_agendamento")
    for h in list(logger.handlers):
        h.close()
        logger.removeHandler(h)
    if _TEMP_ROOT is not None:
        try:
            _TEMP_ROOT.cleanup()
        except Exception:
            pass


class TestSistemasOperacionais(unittest.TestCase):
    def setUp(self):
        self.file_manager = FileManager(os_adapter.default_encoding, backup_dir=os_adapter.backups_dir)
        self.scheduler = SchedulerService(self.file_manager)

    def test_01_os_adapter(self):
        """Valida detecção de SO e diretórios do filesystem."""
        telemetry = os_adapter.get_telemetry()
        self.assertIn(telemetry["os_name"], ["Windows", "Linux", "Darwin"])
        self.assertTrue(os_adapter.consultas_dir.exists())
        self.assertTrue(os_adapter.relatorios_dir.exists())
        self.assertTrue(os_adapter.logs_dir.exists())

    def test_02_atomic_file_io(self):
        """Valida que a escrita atômica com fsync e replace do SO funciona sem falhas."""
        test_file = os_adapter.data_dir / "teste_atomic.json"
        data = {"mensagem": "teste de I/O atômico do SO", "status": "ok"}
        self.file_manager.save_json_atomic(test_file, data)
        self.assertTrue(test_file.exists())
        loaded = self.file_manager.load_json(test_file)
        self.assertEqual(loaded["mensagem"], data["mensagem"])
        if test_file.exists():
            test_file.unlink()

    def test_03_lru_cache_memory(self):
        """Valida política de evicção LRU e cópia defensiva de memória."""
        cache = LRUCache(capacity=2, default_ttl_seconds=60)
        cache.put("item1", ["valor1"])
        cache.put("item2", ["valor2"])
        val1 = cache.get("item1")
        self.assertEqual(val1, ["valor1"])
        val1.append("mutacao_externa")
        self.assertEqual(cache.get("item1"), ["valor1"], "Cache não deve sofrer mutação por referência.")

        cache.put("item3", ["valor3"])  # Deve evictar item2
        self.assertIsNone(cache.get("item2"))
        self.assertEqual(cache.get("item1"), ["valor1"])
        self.assertEqual(cache.get("item3"), ["valor3"])

    def test_04_concurrency_race_condition(self):
        """
        Teste de Estresse Concorrente:
        10 threads simultâneas tentam agendar a MESMA vaga para o mesmo médico.
        Apenas 1 deve obter sucesso; as outras 9 devem ser rejeitadas pelo Lock de SO.
        """
        medicos = self.scheduler.list_medicos()
        pacientes = self.scheduler.list_pacientes()
        self.assertTrue(len(medicos) > 0)
        self.assertTrue(len(pacientes) > 0)

        medico = medicos[0]
        paciente = pacientes[0]
        data_teste = "2029-01-01"
        horario_teste = "15:00"

        consultas = [
            c for c in self.scheduler.list_consultas()
            if not (c["medico_id"] == medico["id"] and c["data"] == data_teste and c["horario"] == horario_teste)
        ]
        self.file_manager.save_json_atomic(self.scheduler.consultas_file, consultas)
        consultas_cache.invalidate("all_consultas")

        num_threads = 10

        def tentar_agendar(idx: int):
            c = Consulta(
                paciente_id=paciente["id"],
                paciente_nome=f"{paciente['nome']} {idx}",
                medico_id=medico["id"],
                medico_nome=medico["nome"],
                data=data_teste,
                horario=horario_teste,
                observacoes="Teste de concorrência"
            )
            try:
                self.scheduler.schedule_consulta(c)
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=num_threads) as pool:
            resultados = list(pool.map(tentar_agendar, range(num_threads)))

        sucessos = sum(1 for r in resultados if r is True)
        falhas = sum(1 for r in resultados if r is False)

        self.assertEqual(sucessos, 1, "Apenas 1 thread deveria obter a vaga com exclusividade.")
        self.assertEqual(falhas, num_threads - 1, "As outras threads deveriam ser bloqueadas pelo Lock de SO.")

    def test_05_reports_and_backup(self):
        """Valida operações de I/O na criação de PDFs, CSVs e Backups compactados."""
        consultas = self.scheduler.list_consultas()
        pdf_path = generate_pdf(consultas, os_adapter.relatorios_dir, os_adapter.system)
        self.assertTrue(Path(pdf_path).exists())

        csv_path = export_csv(consultas, os_adapter.relatorios_dir, self.file_manager)
        self.assertTrue(Path(csv_path).exists())

        backup = AutoBackupManager(os_adapter.consultas_dir, os_adapter.backups_dir)
        backup_path = backup.create_backup()
        self.assertTrue(Path(backup_path).exists())

    def test_06_memory_monitor_gc(self):
        """Valida métricas de memória física (RSS) e coleta de lixo."""
        info = memory_monitor.get_info()
        self.assertGreater(info["process_rss_mb"], 0)
        self.assertGreater(info["system_total_ram_mb"], 0)
        gc_result = memory_monitor.run_garbage_collection([os_adapter.consultas_dir])
        self.assertIn("ram_reclaimed_mb", gc_result)

    def test_07_crud_updates_horarios_and_name_propagation(self):
        """Valida updates de médico/paciente/consulta, CRUD de horários e propagação de nomes."""
        with TestClient(app) as client:
            # Criar médico e paciente dedicados
            r_med = client.post("/api/medicos", json={
                "nome": "Dr. Teste Original",
                "crm": "99991-SP",
                "especialidade": "Clínica Geral"
            })
            self.assertEqual(r_med.status_code, 200)
            med_id = r_med.json()["id"]

            r_pac = client.post("/api/pacientes", json={
                "nome": "Paciente Original",
                "cpf": "999.888.777-66",
                "telefone": "(11) 90000-0000",
                "email": "orig@teste.com"
            })
            self.assertEqual(r_pac.status_code, 200)
            pac_id = r_pac.json()["id"]

            # CRUD de horários
            self.assertEqual(client.get(f"/api/medicos/{med_id}/horarios").status_code, 200)
            r_add = client.post(f"/api/medicos/{med_id}/horarios", json={"horario": "07:30"})
            self.assertEqual(r_add.status_code, 200)
            self.assertEqual(r_add.json()["horarios_disponiveis"][0], "07:30")

            # Duplicado recusado e formato inválido recusado
            self.assertEqual(client.post(f"/api/medicos/{med_id}/horarios", json={"horario": "07:30"}).status_code, 400)
            self.assertEqual(client.post(f"/api/medicos/{med_id}/horarios", json={"horario": "25:99"}).status_code, 400)

            r_put_h = client.put(f"/api/medicos/{med_id}/horarios", json={"horarios": ["16:00", "08:00", "10:00"]})
            self.assertEqual(r_put_h.status_code, 200)
            self.assertEqual(r_put_h.json()["horarios_disponiveis"], ["08:00", "10:00", "16:00"])

            r_del_h = client.delete(f"/api/medicos/{med_id}/horarios/16:00")
            self.assertEqual(r_del_h.status_code, 200)
            self.assertEqual(r_del_h.json()["horarios_disponiveis"], ["08:00", "10:00"])

            # Agendar consulta futura
            amanha = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
            r_con = client.post("/api/consultas", json={
                "paciente_id": pac_id,
                "paciente_nome": "Paciente Original",
                "medico_id": med_id,
                "medico_nome": "Dr. Teste Original",
                "data": amanha,
                "horario": "08:00",
                "observacoes": "Inicial"
            })
            self.assertEqual(r_con.status_code, 200)
            con_id = r_con.json()["id"]

            # Get por ID e Update de médico/paciente propagando nome
            self.assertEqual(client.get(f"/api/medicos/{med_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/pacientes/{pac_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/consultas/{con_id}").status_code, 200)

            r_upd_med = client.put(f"/api/medicos/{med_id}", json={"nome": "Dr. Nome Atualizado", "crm": "99991-SP"})
            self.assertEqual(r_upd_med.status_code, 200)
            r_upd_pac = client.put(f"/api/pacientes/{pac_id}", json={"nome": "Paciente Atualizado", "cpf": "999.888.777-66"})
            self.assertEqual(r_upd_pac.status_code, 200)

            con_atualizada = client.get(f"/api/consultas/{con_id}").json()
            self.assertEqual(con_atualizada["medico_nome"], "Dr. Nome Atualizado")
            self.assertEqual(con_atualizada["paciente_nome"], "Paciente Atualizado")

            # Remarcar e concluir consulta
            r_rem = client.put(f"/api/consultas/{con_id}", json={"horario": "10:00", "observacoes": "Remarcada"})
            self.assertEqual(r_rem.status_code, 200)
            self.assertEqual(r_rem.json()["horario"], "10:00")

            r_conc = client.put(f"/api/consultas/{con_id}/concluir")
            self.assertEqual(r_conc.status_code, 200)
            self.assertEqual(r_conc.json()["consulta"]["status"], "Concluída")

            # Excluir consulta
            self.assertEqual(client.delete(f"/api/consultas/{con_id}").status_code, 200)
            self.assertEqual(client.get(f"/api/consultas/{con_id}").status_code, 404)

    def test_08_scheduler_business_rules(self):
        """Valida rejeição de horário fora da lista, data passada, formato inválido e médico inexistente."""
        amanha = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        ontem = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        # Médico inexistente
        with self.assertRaises(ValueError):
            self.scheduler.schedule_consulta(Consulta(
                paciente_id="pac-001", paciente_nome="João",
                medico_id="med-inexistente", medico_nome="Ninguém",
                data=amanha, horario="08:00"
            ))

        # Horário fora da lista do médico
        with self.assertRaises(ValueError):
            self.scheduler.schedule_consulta(Consulta(
                paciente_id="pac-001", paciente_nome="João",
                medico_id="med-001", medico_nome="Dra. Ana",
                data=amanha, horario="23:45"
            ))

        # Data no passado
        with self.assertRaises(ValueError):
            self.scheduler.schedule_consulta(Consulta(
                paciente_id="pac-001", paciente_nome="João",
                medico_id="med-001", medico_nome="Dra. Ana",
                data=ontem, horario="08:00"
            ))

        # Formato inválido de data
        with self.assertRaises(ValueError):
            self.scheduler.schedule_consulta(Consulta(
                paciente_id="pac-001", paciente_nome="João",
                medico_id="med-001", medico_nome="Dra. Ana",
                data="11/11/2026", horario="08:00"
            ))

    def test_09_delete_blocked_with_active_consultas(self):
        """Bloqueia com HTTP 409 a exclusão de médico/paciente com consulta 'Agendada'."""
        with TestClient(app) as client:
            r_med = client.post("/api/medicos", json={
                "nome": "Dr. Bloqueio", "crm": "77771-RJ", "especialidade": "Pediatria"
            })
            med_id = r_med.json()["id"]
            r_pac = client.post("/api/pacientes", json={
                "nome": "Pac Bloqueio", "cpf": "777.666.555-44", "telefone": "(21) 91111-2222"
            })
            pac_id = r_pac.json()["id"]

            futuro = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
            r_con = client.post("/api/consultas", json={
                "paciente_id": pac_id, "medico_id": med_id, "data": futuro, "horario": "09:00"
            })
            self.assertEqual(r_con.status_code, 200)
            con_id = r_con.json()["id"]

            self.assertEqual(client.delete(f"/api/medicos/{med_id}").status_code, 409)
            self.assertEqual(client.delete(f"/api/pacientes/{pac_id}").status_code, 409)

            # Após cancelar a consulta, exclusão é permitida
            client.put(f"/api/consultas/{con_id}/cancelar")
            self.assertEqual(client.delete(f"/api/medicos/{med_id}").status_code, 200)
            self.assertEqual(client.delete(f"/api/pacientes/{pac_id}").status_code, 200)
            client.delete(f"/api/consultas/{con_id}")

    def test_10_multiprocess_concurrency(self):
        """Valida exclusão mútua entre processos reais (multiprocessing spawn) via Kernel File Lock."""
        with TestClient(app) as client:
            res = client.post("/api/test/multiprocess-simulation?num_processes=4")
            self.assertEqual(res.status_code, 200)
            data = res.json()
            self.assertEqual(data["agendamentos_efetuados"], 1)
            self.assertEqual(data["conflitos_prevenidos"], 3)
            self.assertEqual(len(data["pids"]), 4)

    def test_11_async_report_job_and_download(self):
        """Valida despacho de relatórios no WorkerPool, polling em /api/jobs/{id} e download seguro."""
        with TestClient(app) as client:
            for endpoint, expected_mime in [
                ("/api/reports/pdf", "application/pdf"),
                ("/api/reports/csv", "text/csv"),
            ]:
                r = client.post(endpoint)
                self.assertEqual(r.status_code, 200)
                job_id = r.json()["job_id"]

                job = None
                for _ in range(40):
                    jr = client.get(f"/api/jobs/{job_id}")
                    self.assertEqual(jr.status_code, 200)
                    job = jr.json()
                    if job["status"] in ("concluído", "erro"):
                        break
                    time.sleep(0.05)

                self.assertEqual(job["status"], "concluído")
                self.assertIsNotNone(job["thread_name"])
                self.assertIsNotNone(job["tid"])
                dl_url = job["result"]["download_url"]
                dl_res = client.get(dl_url)
                self.assertEqual(dl_res.status_code, 200)
                self.assertIn(expected_mime, dl_res.headers.get("content-type", ""))

    def test_12_logging_timezone_and_levels(self):
        """Valida formato do log com fuso horário e ocorrência real de INFO, ERROR e DEBUG."""
        with TestClient(app) as client:
            # INFO (list/create), ERROR (404), DEBUG (cache/lock)
            client.get("/api/medicos")
            client.get("/api/medicos/nao-existe-404")

        log_path = os_adapter.logs_dir / "sistema.log"
        self.assertTrue(log_path.exists())
        content = log_path.read_text(encoding="utf-8-sig")

        # Timestamp com fuso, ex: [2026-11-11 19:42:55 -0300 INFO]
        self.assertRegex(content, r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} [+-]\d{4} (INFO|ERROR|DEBUG)\]")
        self.assertIn(" INFO]", content)
        self.assertIn(" ERROR]", content)
        self.assertIn(" DEBUG]", content)

    def test_13_path_traversal_blocked(self):
        """Valida bloqueio de Path Traversal no download de relatórios."""
        with TestClient(app) as client:
            r1 = client.get("/api/reports/download/..%2Fconsultas%2Fpacientes.json")
            self.assertEqual(r1.status_code, 400)
            r2 = client.get("/api/reports/download/..%5Cconsultas%5Cmedicos.json")
            self.assertEqual(r2.status_code, 400)

    def test_14_filename_sanitization_and_corrupted_json_recovery(self):
        """Valida sanitização de nomes no Windows/POSIX e restauração de JSON corrompido via backup."""
        self.assertEqual(os_adapter.sanitize_filename('relatorio:<teste>?.pdf'), "relatorio__teste__.pdf")
        self.assertEqual(os_adapter.sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(os_adapter.sanitize_filename("nul"), "_nul")

        # Teste de recuperação de JSON corrompido
        backup_mgr = AutoBackupManager(os_adapter.consultas_dir, os_adapter.backups_dir)
        backup_mgr.create_backup()

        medicos_before = self.file_manager.load_json(self.scheduler.medicos_file, default=[])
        self.assertTrue(len(medicos_before) >= 1)

        # Corromper medicos.json propositalmente
        self.scheduler.medicos_file.write_text("{corrompido!!!", encoding="utf-8")
        restored = self.file_manager.load_json(self.scheduler.medicos_file, default=[])
        self.assertEqual(len(restored), len(medicos_before))

    def test_15_cleanup_daemon_removes_old_tmp(self):
        """Valida que a thread/rotina CleanupDaemon remove .tmp órfãos antigos e preserva recentes."""
        old_tmp = os_adapter.consultas_dir / "orphan_old.tmp"
        fresh_tmp = os_adapter.consultas_dir / "active_fresh.tmp"
        old_tmp.write_text("old", encoding="utf-8")
        fresh_tmp.write_text("fresh", encoding="utf-8")

        old_time = time.time() - 120
        os.utime(old_tmp, (old_time, old_time))

        daemon = CleanupDaemon(
            cache=consultas_cache,
            search_dirs=[os_adapter.consultas_dir],
            interval_seconds=60,
            max_orphan_age_seconds=60,
        )
        res = daemon.run_cleanup_once()
        self.assertGreaterEqual(res["temp_files_deleted"], 1)
        self.assertFalse(old_tmp.exists())
        self.assertTrue(fresh_tmp.exists())
        fresh_tmp.unlink()

    def test_16_tracemalloc_and_weakref_locks(self):
        """Valida endpoints tracemalloc e coleta de mutexes dinâmicos via WeakValueDictionary."""
        def _use_dynamic_lock():
            with lock_manager.synchronized("chave_dinamica_temporaria"):
                self.assertGreaterEqual(lock_manager.get_memory_locks_count(), 1)

        _use_dynamic_lock()
        gc.collect()
        # Após sair do escopo, lock dinâmico não referenciado é removido da tabela
        self.assertEqual(lock_manager.get_memory_locks_count(), 0)

        with TestClient(app) as client:
            snap = client.get("/api/os/memory/snapshot")
            self.assertEqual(snap.status_code, 200)
            self.assertIn("top_allocations", snap.json())

            comp = client.post("/api/os/memory/compare")
            self.assertEqual(comp.status_code, 200)
            self.assertTrue(comp.json()["has_previous"])


if __name__ == "__main__":
    unittest.main()
