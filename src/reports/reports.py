# src/reports/reports.py
"""
Módulo de Geração de Relatórios (PDF e CSV)
Demonstração de Conceitos de SO:
- Operações de I/O em disco com gravação de arquivos binários e tabulares
- Execução desacoplada via workers assíncronos
"""

import os
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from src.storage.storage import FileManager

def generate_pdf(consultas: List[Dict[str, Any]], output_dir: Path, os_name: str) -> str:
    """Gera um relatório profissional em PDF contendo as consultas e metadados do SO."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"relatorio_consultas_{timestamp}.pdf"
    file_path = output_dir / filename

    doc = SimpleDocTemplate(
        str(file_path),
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
    hora_str = datetime.now().strftime("%d/%m/%Y às %H:%M:%S")
    story.append(Paragraph(f"Emitido em: {hora_str} | SO: {os_name} | PID: {os.getpid()}", meta_style))
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
    return str(file_path)

def export_csv(consultas: List[Dict[str, Any]], output_dir: Path, file_manager: FileManager) -> str:
    """Exporta consultas em formato CSV."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"relatorio_consultas_{timestamp}.csv"
    file_path = output_dir / filename
    headers = ["id", "data", "horario", "paciente_id", "paciente_nome", "medico_id", "medico_nome", "status", "observacoes", "criado_em"]
    file_manager.save_csv(file_path, headers, consultas)
    return str(file_path)

