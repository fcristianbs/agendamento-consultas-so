# tests/test_concurrency.py
"""
Suite de Testes Automatizados para Validação dos Conceitos de Sistemas Operacionais:
1. Concorrência e Locks (Race Condition Prevention)
2. Persistência e Escrita Atômica no Sistema de Arquivos (fsync + replace)
3. Gerenciamento de Memória e Cache LRU
4. Geração de Relatórios em I/O (PDF e CSV)
5. Criação de Backups
"""

import sys
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.os_adapter import os_adapter, consultas_cache, memory_monitor, LRUCache
from src.storage.storage import FileManager, AutoBackupManager
from src.concurrent.concurrency import lock_manager, worker_pool
from src.core.scheduler import SchedulerService, Paciente, Medico, Consulta
from src.reports.reports import generate_pdf, export_csv

class TestSistemasOperacionais(unittest.TestCase):
    def setUp(self):
        self.file_manager = FileManager(os_adapter.default_encoding)
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
        """Valida política de evicção LRU e controle de memória."""
        cache = LRUCache(capacity=2, default_ttl_seconds=60)
        cache.put("item1", "valor1")
        cache.put("item2", "valor2")
        self.assertEqual(cache.get("item1"), "valor1")
        cache.put("item3", "valor3")  # Deve evictar item2
        self.assertIsNone(cache.get("item2"))
        self.assertEqual(cache.get("item1"), "valor1")
        self.assertEqual(cache.get("item3"), "valor3")

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

        # Limpar agendamento prévio
        consultas = [c for c in self.scheduler.list_consultas() if not (c["medico_id"] == medico["id"] and c["data"] == data_teste and c["horario"] == horario_teste)]
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

if __name__ == "__main__":
    unittest.main()
