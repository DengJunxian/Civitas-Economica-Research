"""Printable PDF for the Markdown handouts produced by lab.export.

Renders only the Markdown subset lab.export emits (headings, paragraphs,
bullets, pipe tables, code fences, block quotes, bold and inline code), so
the PDF says exactly what the Markdown says. Chinese text uses the standard
CID font STSong-Light, which needs no font file. Requires reportlab
(requirements-ui.txt).
"""
from __future__ import annotations

import io
import re
from xml.sax.saxutils import escape

FONT = "STSong-Light"
# Characters STSong-Light has no glyph for (they would print as empty boxes).
SUBSTITUTES = str.maketrans({"·": "｜", "−": "-"})


def _inline(text: str) -> str:
    text = escape(text.replace("\\|", "|").translate(SUBSTITUTES))
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    return re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def markdown_pdf(markdown: str, *, title: str = "") -> bytes:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.platypus import Paragraph, Preformatted, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise ImportError("PDF export needs reportlab (pip install -r requirements-ui.txt)") from exc

    if FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(FONT))
    base = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=base["BodyText"], fontName=FONT, fontSize=10, leading=15, wordWrap="CJK")
    styles = {
        "h1": ParagraphStyle("h1", parent=body, fontSize=16, leading=22, spaceAfter=6, keepWithNext=1),
        "h2": ParagraphStyle("h2", parent=body, fontSize=12.5, leading=18, spaceBefore=8, spaceAfter=4, keepWithNext=1),
        "body": body,
        "bullet": ParagraphStyle("bullet", parent=body, leftIndent=12, bulletIndent=2),
        "quote": ParagraphStyle("quote", parent=body, fontSize=9, textColor=colors.HexColor("#555555"), leftIndent=8),
        "cell": ParagraphStyle("cell", parent=body, fontSize=8.5, leading=12),
        "code": ParagraphStyle("code", parent=body, fontName="Courier", fontSize=8, leading=11),
    }
    width = A4[0] - 36 * mm
    story, lines, i = [], markdown.splitlines(), 0
    while i < len(lines):
        line = lines[i].rstrip()
        if not line.strip():
            i += 1
        elif line.startswith("```"):
            end = next((j for j in range(i + 1, len(lines)) if lines[j].startswith("```")), len(lines))
            story.append(Preformatted("\n".join(lines[i + 1:end]), styles["code"]))
            i = end + 1
        elif line.startswith("|"):
            block = []
            while i < len(lines) and lines[i].startswith("|"):
                if not re.fullmatch(r"\|(\s*-+\s*\|)+", lines[i].strip().replace("---|", "-|")):
                    block.append(_cells(lines[i]))
                i += 1
            columns = max(len(row) for row in block)
            rows = [[Paragraph(_inline(c), styles["cell"]) for c in row + [""] * (columns - len(row))] for row in block]
            weight = [max(6, min(40, max(len(row[c]) if c < len(row) else 0 for row in block))) for c in range(columns)]
            table = Table(rows, colWidths=[width * w / sum(weight) for w in weight], repeatRows=1)
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#999999")),
                                       ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story += [table, Spacer(1, 4)]
        elif line.startswith("## "):
            story.append(Paragraph(_inline(line[3:]), styles["h2"]))
            i += 1
        elif line.startswith("# "):
            story.append(Paragraph(_inline(line[2:]), styles["h1"]))
            i += 1
        elif line.startswith("- "):
            story.append(Paragraph(_inline(line[2:]), styles["bullet"], bulletText="•"))
            i += 1
        elif line.startswith("> "):
            story.append(Paragraph(_inline(line[2:]), styles["quote"]))
            i += 1
        elif line.strip() == "&nbsp;":
            story.append(Spacer(1, 18 * mm))  # answer space on worksheets
            i += 1
        else:
            story.append(Paragraph(_inline(line), styles["body"]))
            i += 1
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                      bottomMargin=16 * mm, title=title, author="Civitas Lab").build(story)
    return buffer.getvalue()
