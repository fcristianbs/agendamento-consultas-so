# src/reports/reports.py
"""
Módulo de Geração de Relatórios (PDF e CSV)
Demonstração de Conceitos de SO:
- Operações de I/O em disco com gravação atômica de arquivos binários e tabulares
- Encoding e terminadores de linha dependentes de SO (utf-8-sig / \\r\\n no Windows; utf-8 / \\n no POSIX)
- Execução desacoplada em threads do WorkerPool com registro de TID
"""

import os
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from src.config.os_adapter import os_adapter
from src.storage.storage import FileManager

_rep_logger = logging.getLogger("sistema_agendamento")


def generate_pdf(consultas: List[Dict[str, Any]], output_dir: Path, os_name: str) -> str:
    """Gera um relatório profissional em PDF contendo as consultas e metadados do SO."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os_adapter.sanitize_filename(f"relatorio_consultas_{timestamp}.pdf")
    file_path = os_adapter.safe_path(output_dir, filename)
    temp_path = file_path.with_suffix(".tmp")

    doc = SimpleDocTemplate(
        str(temp_path),
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    story = []

    # Título do Relatório
    title_style = ParagraphStyle(
        'RepTitle',
        parent=styles['Heading1'],
        fontSize=18,
        textColor=colors.HexColor("#1e3a8a"),
        alignment=1,
        spaceAfter=8
    )
    story.append(Paragraph("Sistema de Agendamento - Relatório Geral de Consultas", title_style))

    # Metadados de SO
    meta_style = ParagraphStyle(
        'RepMeta',
        parent=styles['Normal'],
        fontSize=8.5,
        textColor=colors.HexColor("#475569"),
        alignment=1,
        spaceAfter=14
    )
    hora_str = datetime.now().astimezone().strftime("%d/%m/%Y às %H:%M:%S %z")
    story.append(
        Paragraph(
            f"Emitido em: {hora_str} | SO: {os_name} | PID: {os.getpid()} | "
            f"Thread: {threading.current_thread().name} (TID: {threading.get_ident()})",
            meta_style,
        )
    )
    story.append(Spacer(1, 8))

    # Tabela com as consultas
    table_data = [["ID", "Data", "Hora", "Paciente", "Médico", "Status"]]
    for c in consultas:
        table_data.append([
            c.get("id", "-"),
            c.get("data", "-"),
            c.get("horario", "-"),
            c.get("paciente_nome", "-")[:20],
            c.get("medico_nome", "-")[:20],
            c.get("status", "-")
        ])

    table = Table(table_data, colWidths=[55, 65, 45, 150, 150, 75])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor("#2563eb")),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 9),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
        ('BACKGROUND', (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ('FONTSIZE', (0, 1), (-1, -1), 8),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
    ]))

    story.append(table)
    doc.build(story)
    os.replace(temp_path, file_path)
    os_adapter.apply_safe_permissions(file_path)
    _rep_logger.info(
        f"[SO I/O] Relatório PDF gerado: {file_path.name} "
        f"(Thread: {threading.current_thread().name}, TID: {threading.get_ident()})"
    )
    return str(file_path)


def export_csv(consultas: List[Dict[str, Any]], output_dir: Path, file_manager: FileManager) -> str:
    """Exporta consultas em formato CSV usando encoding e quebra de linha nativos do SO."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os_adapter.sanitize_filename(f"relatorio_consultas_{timestamp}.csv")
    file_path = os_adapter.safe_path(output_dir, filename)
    headers = [
        "id", "data", "horario", "paciente_id", "paciente_nome",
        "medico_id", "medico_nome", "status", "observacoes", "criado_em"
    ]
    file_manager.save_csv(
        file_path,
        headers,
        consultas,
        encoding=os_adapter.csv_encoding,
        lineterminator=os_adapter.line_ending,
    )
    _rep_logger.info(
        f"[SO I/O] Relatório CSV gerado: {file_path.name} "
        f"(Thread: {threading.current_thread().name}, TID: {threading.get_ident()})"
    )
    return str(file_path)
