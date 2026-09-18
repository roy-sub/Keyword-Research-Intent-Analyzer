"""Render an intent report to a typeset PDF.

The report is Markdown produced by our own prompt, so the subset that has to
be supported is known and small: headings, paragraphs, bullet and numbered
lists, bold/italic/code spans, tables and rules. Parsing that subset here
keeps the only dependency ReportLab, and gives exact control over the page.

Typeface is Helvetica, a PDF base-14 font, so nothing has to be embedded and
the file stays a few kilobytes. Geist — what the interface uses — is served
by Google Fonts as woff2, which ReportLab cannot embed; matching it exactly
would mean vendoring a converted binary into the repo for a marginal gain on
a document that is mostly read on paper.
"""

from __future__ import annotations

import io
import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

# The same monochrome ramp as the interface, token for token, so a printed
# report and the screen it came from are recognisably the same document.
INK = colors.HexColor("#101113")     # --c-ink-1
INK_2 = colors.HexColor("#34363B")   # --c-ink-2
INK_3 = colors.HexColor("#5C5F66")   # --c-ink-3
INK_4 = colors.HexColor("#6B6E75")   # --c-ink-4
RULE = colors.HexColor("#E3E3E6")    # --c-line-solid
PANEL = colors.HexColor("#F4F4F5")   # --c-canvas

PAGE_W, PAGE_H = A4
MARGIN_X = 22 * mm
MARGIN_TOP = 24 * mm
MARGIN_BOTTOM = 20 * mm


def _styles() -> dict[str, ParagraphStyle]:
    base = ParagraphStyle(
        "body",
        fontName="Helvetica",
        fontSize=9.6,
        leading=15.4,
        textColor=INK_2,
        alignment=TA_LEFT,
        spaceAfter=7,
    )
    return {
        "body": base,
        "eyebrow": ParagraphStyle(
            "eyebrow", parent=base, fontName="Helvetica-Bold", fontSize=7.2,
            leading=9, textColor=INK_4, spaceAfter=5,
        ),
        "title": ParagraphStyle(
            "title", parent=base, fontName="Helvetica-Bold", fontSize=25,
            leading=28, textColor=INK, spaceAfter=7,
        ),
        "meta": ParagraphStyle(
            "meta", parent=base, fontSize=8.4, leading=12, textColor=INK_3, spaceAfter=0,
        ),
        "h1": ParagraphStyle(
            "h1", parent=base, fontName="Helvetica-Bold", fontSize=14.5,
            leading=19, textColor=INK, spaceBefore=17, spaceAfter=7,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base, fontName="Helvetica-Bold", fontSize=12.2,
            leading=16.5, textColor=INK, spaceBefore=14, spaceAfter=6,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base, fontName="Helvetica-Bold", fontSize=10.4,
            leading=14, textColor=INK, spaceBefore=11, spaceAfter=4,
        ),
        "li": ParagraphStyle(
            "li", parent=base, leftIndent=11, bulletIndent=1, spaceAfter=4.5,
        ),
        "th": ParagraphStyle(
            "th", parent=base, fontName="Helvetica-Bold", fontSize=7.4,
            leading=10, textColor=INK_3, spaceAfter=0,
        ),
        "td": ParagraphStyle(
            "td", parent=base, fontSize=8.6, leading=12, textColor=INK_2, spaceAfter=0,
        ),
    }


# ---------------------------------------------------------------------------
# Markdown subset -> ReportLab inline markup
# ---------------------------------------------------------------------------

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_ITALIC = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)")
_CODE = re.compile(r"`([^`]+)`")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")


def inline(text: str) -> str:
    """Escape, then apply the inline spans ReportLab's Paragraph understands."""
    out = escape(text)
    out = _CODE.sub(lambda m: f'<font face="Courier" size="8.6">{m.group(1)}</font>', out)
    out = _BOLD.sub(lambda m: f"<b>{m.group(1)}</b>", out)
    out = _ITALIC.sub(lambda m: f"<i>{m.group(1)}</i>", out)
    out = _LINK.sub(lambda m: f"<u>{m.group(1)}</u>", out)
    return out


def _table_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_divider(line: str) -> bool:
    return bool(re.fullmatch(r"\|?[\s:\-|]+\|?", line.strip())) and "-" in line


def markdown_flowables(md: str, st: dict[str, ParagraphStyle], width: float) -> list:
    """Convert the report's Markdown into a flat list of flowables."""
    flow: list = []
    lines = md.replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            i += 1
            continue

        # ---- table ----
        if line.startswith("|") and i + 1 < len(lines) and _is_divider(lines[i + 1]):
            header = _table_cells(line)
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(_table_cells(lines[i]))
                i += 1
            flow.append(_build_table(header, rows, st, width))
            continue

        # ---- rule ----
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", line):
            flow.append(Spacer(1, 5))
            flow.append(HRFlowable(width="100%", thickness=0.6, color=RULE,
                                   spaceBefore=0, spaceAfter=10))
            i += 1
            continue

        # ---- heading ----
        m = re.match(r"^(#{1,6})\s+(.*)$", line)
        if m:
            level = min(3, len(m.group(1)))
            flow.append(Paragraph(inline(m.group(2)), st[f"h{level}"]))
            i += 1
            continue

        # ---- bullet list ----
        if re.match(r"^[-*+]\s+", line):
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                item = re.sub(r"^\s*[-*+]\s+", "", lines[i])
                flow.append(Paragraph(inline(item), st["li"], bulletText="\u2022"))
                i += 1
            flow.append(Spacer(1, 3))
            continue

        # ---- numbered list ----
        if re.match(r"^\d+[.)]\s+", line):
            n = 1
            while i < len(lines) and re.match(r"^\s*\d+[.)]\s+", lines[i]):
                item = re.sub(r"^\s*\d+[.)]\s+", "", lines[i])
                flow.append(Paragraph(inline(item), st["li"], bulletText=f"{n}."))
                n += 1
                i += 1
            flow.append(Spacer(1, 3))
            continue

        # ---- blockquote ----
        if line.startswith(">"):
            quote = re.sub(r"^>\s?", "", line)
            qs = ParagraphStyle("q", parent=st["body"], leftIndent=10, textColor=INK_3)
            flow.append(Paragraph(inline(quote), qs))
            i += 1
            continue

        # ---- paragraph (join soft-wrapped lines) ----
        buf = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i].strip()
            if (not nxt or nxt.startswith(("#", "|", ">"))
                    or re.match(r"^([-*+]\s+|\d+[.)]\s+)", nxt)
                    or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", nxt)):
                break
            buf.append(nxt)
            i += 1
        flow.append(Paragraph(inline(" ".join(buf)), st["body"]))
    return flow


def _build_table(header: list[str], rows: list[list[str]],
                 st: dict[str, ParagraphStyle], width: float) -> Table:
    cols = max(len(header), *(len(r) for r in rows)) if rows else len(header)

    def pad(cells: list[str]) -> list[str]:
        return cells + [""] * (cols - len(cells))

    data = [[Paragraph(inline(c), st["th"]) for c in pad(header)]]
    for r in rows:
        data.append([Paragraph(inline(c), st["td"]) for c in pad(r)])

    # First column carries the label and gets the extra room.
    first = width * (0.34 if cols > 2 else 0.5)
    rest = (width - first) / max(1, cols - 1)
    table = Table(data, colWidths=[first] + [rest] * (cols - 1), hAlign="LEFT")
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.7, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.4, RULE),
    ]))
    return table


# ---------------------------------------------------------------------------
# Page furniture
# ---------------------------------------------------------------------------


def _decorate(topic: str):
    def draw(canvas, doc):
        canvas.saveState()
        # running header
        canvas.setFont("Helvetica-Bold", 7)
        canvas.setFillColor(INK_4)
        canvas.drawString(MARGIN_X, PAGE_H - 14 * mm, "KEYWORD ANALYZER")
        canvas.setFont("Helvetica", 7)
        label = topic if len(topic) <= 60 else topic[:57] + "..."
        canvas.drawRightString(PAGE_W - MARGIN_X, PAGE_H - 14 * mm, label)
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.6)
        canvas.line(MARGIN_X, PAGE_H - 16.5 * mm, PAGE_W - MARGIN_X, PAGE_H - 16.5 * mm)
        # running footer
        canvas.line(MARGIN_X, 14.5 * mm, PAGE_W - MARGIN_X, 14.5 * mm)
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(INK_4)
        canvas.drawString(MARGIN_X, 11 * mm, "Search intent analysis")
        canvas.drawRightString(PAGE_W - MARGIN_X, 11 * mm, str(canvas.getPageNumber()))
        canvas.restoreState()
    return draw


def _format_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").strftime("%d %B %Y, %H:%M UTC")
    except (ValueError, TypeError):
        return iso or ""


def render_report_pdf(
    *,
    topic: str,
    analysis_markdown: str,
    generated_at: str = "",
    provider_label: str = "",
    model: str = "",
    market_label: str = "",
    total_keywords: int | None = None,
    queries_succeeded: int | None = None,
    queries_attempted: int | None = None,
) -> bytes:
    """Return the report as PDF bytes."""
    st = _styles()
    buf = io.BytesIO()

    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=MARGIN_X, rightMargin=MARGIN_X,
        topMargin=MARGIN_TOP, bottomMargin=MARGIN_BOTTOM,
        title=f"Intent report — {topic}", author="Keyword Analyzer",
        subject="Search intent analysis",
    )
    frame = Frame(
        doc.leftMargin, doc.bottomMargin,
        doc.width, doc.height, id="body",
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
    )
    doc.addPageTemplates([
        PageTemplate(id="report", frames=[frame], onPage=_decorate(topic))
    ])

    story: list = [
        Paragraph("INTENT REPORT", st["eyebrow"]),
        Paragraph(escape(topic), st["title"]),
    ]

    # A compact facts strip rather than a wall of prose.
    facts = []
    if generated_at:
        facts.append(("Generated", _format_date(generated_at)))
    if total_keywords is not None:
        facts.append(("Unique keywords", f"{total_keywords:,}"))
    if queries_succeeded is not None and queries_attempted is not None:
        facts.append(("Queries answered", f"{queries_succeeded} of {queries_attempted}"))
    if market_label:
        # Which Google a set came from changes what it means, so the printed
        # report has to say it — a PDF outlives the screen it was made on.
        facts.append(("Market", market_label))
    if provider_label or model:
        facts.append(("Analysed by", " · ".join(p for p in (provider_label, model) if p)))

    if facts:
        cells = [
            [Paragraph(k.upper(), st["th"]) for k, _ in facts],
            [Paragraph(escape(v), st["td"]) for _, v in facts],
        ]
        strip = Table(cells, colWidths=[doc.width / len(facts)] * len(facts), hAlign="LEFT")
        strip.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, 0), 9),
            ("BOTTOMPADDING", (0, 0), (-1, 0), 2),
            ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
            ("BACKGROUND", (0, 0), (-1, -1), PANEL),
            ("LEFTPADDING", (0, 0), (0, -1), 10),
            ("RIGHTPADDING", (-1, 0), (-1, -1), 10),
        ]))
        story.append(Spacer(1, 4))
        story.append(strip)

    story.append(Spacer(1, 8))
    story.append(HRFlowable(width="100%", thickness=0.8, color=INK, spaceAfter=4))
    story.extend(markdown_flowables(analysis_markdown, st, doc.width))

    doc.build(story)
    return buf.getvalue()
