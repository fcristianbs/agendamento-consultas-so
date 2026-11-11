# docs/gerar_pdf.py
"""
Gerador do Relatório Técnico em PDF (`docs/relatorio_tecnico.pdf`) a partir de `docs/relatorio_tecnico.md`.
Preserva títulos, parágrafos, listas, tabelas, blocos de código e imagens/marcadores de captura.
"""

import re
from html import escape
from pathlib import Path
from typing import List

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    Preformatted,
    Image,
)


def _format_inline(text: str) -> str:
    """Converte formatação Markdown inline (**negrito**, *itálico*, `código`) para tags XML do ReportLab."""
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r'<font name="Courier" size="8.5" color="#1e3a8a">\1</font>', escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<i>\1</i>", escaped)
    return escaped


def _build_table(rows: List[List[str]], cell_style: ParagraphStyle, header_style: ParagraphStyle, max_width: float) -> Table:
    num_cols = max(len(r) for r in rows)
    col_width = max_width / num_cols
    data = []
    for r_idx, row in enumerate(rows):
        padded = row + [""] * (num_cols - len(row))
        style = header_style if r_idx == 0 else cell_style
        data.append([Paragraph(_format_inline(c.strip()), style) for c in padded])

    tbl = Table(data, colWidths=[col_width] * num_cols, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a8a")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f8fafc")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f8fafc"), colors.HexColor("#eef2ff")]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def _build_code_block(code_lines: List[str], code_style: ParagraphStyle, max_width: float) -> Table:
    content = "\n".join(code_lines)
    pre = Preformatted(content, code_style)
    box = Table([[pre]], colWidths=[max_width])
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
        ("BOX", (0, 0), (-1, -1), 0.75, colors.HexColor("#cbd5e1")),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def _build_image_or_placeholder(alt: str, img_rel_path: str, docs_dir: Path, caption_style: ParagraphStyle, max_width: float):
    img_path = (docs_dir / img_rel_path).resolve()
    if img_path.exists() and img_path.is_file():
        img = Image(str(img_path))
        if img.drawWidth > max_width:
            ratio = max_width / img.drawWidth
            img.drawWidth = max_width
            img.drawHeight *= ratio
        return [img, Spacer(1, 3), Paragraph(_format_inline(alt), caption_style), Spacer(1, 8)]

    placeholder = Table(
        [[Paragraph(f"<b>[Espaço reservado para captura]</b><br/>{_format_inline(alt)}<br/><i>Arquivo esperado: docs/{escape(img_rel_path)}</i>", caption_style)]],
        colWidths=[max_width],
    )
    placeholder.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
        ("BOX", (0, 0), (-1, -1), 1.0, colors.HexColor("#94a3b8")),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 14),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 14),
    ]))
    return [placeholder, Spacer(1, 8)]


def generate_technical_pdf(md_path: Path, pdf_path: Path) -> None:
    docs_dir = md_path.parent
    lines = md_path.read_text(encoding="utf-8").splitlines()

    margin = 42
    page_w, _ = A4
    usable_width = page_w - 2 * margin

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=margin,
        rightMargin=margin,
        topMargin=margin,
        bottomMargin=margin,
        title="Relatório Técnico - MedScheduler SO",
    )

    styles = getSampleStyleSheet()
    h1_style = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=17, textColor=colors.HexColor("#0f172a"), spaceAfter=6, spaceBefore=6)
    h2_style = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, textColor=colors.HexColor("#1e3a8a"), spaceAfter=6, spaceBefore=12)
    h3_style = ParagraphStyle("H3", parent=styles["Heading3"], fontSize=11, textColor=colors.HexColor("#1d4ed8"), spaceAfter=4, spaceBefore=8)
    h4_style = ParagraphStyle("H4", parent=styles["Heading4"], fontSize=10, textColor=colors.HexColor("#334155"), spaceAfter=4, spaceBefore=6)
    body_style = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9.5, leading=13.5, textColor=colors.HexColor("#1e293b"), spaceAfter=4)
    bullet_style = ParagraphStyle("Bullet", parent=body_style, leftIndent=14, firstLineIndent=-8, spaceAfter=3)
    code_style = ParagraphStyle("Code", parent=styles["Code"], fontName="Courier", fontSize=7.2, leading=9.5, textColor=colors.HexColor("#0f172a"))
    cell_style = ParagraphStyle("Cell", parent=styles["Normal"], fontSize=8.3, leading=11, textColor=colors.HexColor("#1e293b"))
    header_cell_style = ParagraphStyle("HeaderCell", parent=cell_style, fontName="Helvetica-Bold", textColor=colors.whitesmoke)
    caption_style = ParagraphStyle("Caption", parent=styles["Normal"], fontSize=8.5, leading=11, alignment=1, textColor=colors.HexColor("#475569"))

    story = []
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]
        stripped = line.strip()

        if not stripped or stripped == "---":
            i += 1
            continue

        # Bloco de código cercado
        if stripped.startswith("```"):
            i += 1
            code_lines = []
            while i < n and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            story.append(_build_code_block(code_lines, code_style, usable_width))
            story.append(Spacer(1, 6))
            continue

        # Tabela Markdown
        if stripped.startswith("|") and "|" in stripped[1:]:
            table_rows = []
            while i < n and lines[i].strip().startswith("|"):
                row_str = lines[i].strip().strip("|")
                cols = [c.strip() for c in row_str.split("|")]
                # Ignora linha separadora (ex: :--- | :---)
                if not all(re.match(r"^:?-{2,}:?$", c.replace(" ", "")) for c in cols):
                    table_rows.append(cols)
                i += 1
            if table_rows:
                story.append(_build_table(table_rows, cell_style, header_cell_style, usable_width))
                story.append(Spacer(1, 8))
            continue

        # Imagem ![alt](path)
        img_match = re.match(r"^!\[([^\]]*)\]\(([^)]+)\)$", stripped)
        if img_match:
            alt, rel_path = img_match.group(1), img_match.group(2)
            story.extend(_build_image_or_placeholder(alt, rel_path, docs_dir, caption_style, usable_width))
            i += 1
            continue

        # Títulos
        if stripped.startswith("#### "):
            story.append(Paragraph(_format_inline(stripped[5:]), h4_style))
        elif stripped.startswith("### "):
            story.append(Paragraph(_format_inline(stripped[4:]), h3_style))
        elif stripped.startswith("## "):
            story.append(Paragraph(_format_inline(stripped[3:]), h2_style))
        elif stripped.startswith("# "):
            story.append(Paragraph(_format_inline(stripped[2:]), h1_style))
        # Listas
        elif re.match(r"^[-*]\s+", stripped):
            content = re.sub(r"^[-*]\s+", "", stripped)
            story.append(Paragraph(f"&bull; {_format_inline(content)}", bullet_style))
        elif re.match(r"^\d+\.\s+", stripped):
            story.append(Paragraph(_format_inline(stripped), bullet_style))
        else:
            story.append(Paragraph(_format_inline(stripped), body_style))

        i += 1

    doc.build(story)


if __name__ == "__main__":
    base = Path(__file__).resolve().parent
    md_file = base / "relatorio_tecnico.md"
    pdf_file = base / "relatorio_tecnico.pdf"
    generate_technical_pdf(md_file, pdf_file)
    print(f"PDF gerado com sucesso em: {pdf_file}")

