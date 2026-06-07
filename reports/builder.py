"""
reports/builder.py
CPOI Platform -- Report File Generation (docx and PDF)

Produces the two deliverable file formats from a finalized report context
dict. Both formats carry the per-report watermark on every page as
specified in cpoi-scoring-and-platform-spec.md Part 11.

Public interface:
  generate_report_files(
      context: dict,
      report_type: str,
      output_dir: Path,
  ) -> tuple[Path, Path]
      Returns (docx_path, pdf_path).

Internal functions:
  build_docx(context, output_path) -> Path
  build_pdf(context, output_path) -> Path

Watermark specification (scoring spec Part 11):
  Visible on every page footer:
    "Prepared exclusively for [Sponsor Name] -- Confidential |
     [Client] | [Date] | Report ID: [UUID]"
  PDF metadata: author, subject, keywords fields.
  Generated at creation time; immutable after generation.

docx construction strategy:
  Renders the Jinja2 template to HTML, then walks the HTML parse tree
  with Python's built-in html.parser and translates elements to
  python-docx calls. This keeps the template as the single source of
  truth for document structure.

PDF construction strategy:
  Built directly from the context dict using ReportLab Platypus
  (Paragraph, Table, Spacer, Image). Does not depend on the docx file.
  The watermark footer is drawn on every page via a canvas callback.
"""

import base64
import io
import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.oxml import OxmlElement
from docx.shared import Inches, Pt, RGBColor, Cm
from docx.enum.table import WD_TABLE_ALIGNMENT

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph, Spacer, Table, TableStyle, SimpleDocTemplate,
    HRFlowable, Image as RLImage, KeepTogether,
)
from reportlab.platypus.flowables import HRFlowable
from reportlab.pdfgen import canvas as rl_canvas

from reports.validator import validate_no_unfilled_placeholders

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.reports.builder")

# ---------------------------------------------------------------------------
# Templates directory
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# ---------------------------------------------------------------------------
# Classification band colour map
# ---------------------------------------------------------------------------

# Docx RGBColor values per classification band.
_BAND_COLORS_DOCX: dict[str, RGBColor] = {
    "Low Risk":    RGBColor(0x2e, 0x86, 0x2e),   # green
    "Moderate":    RGBColor(0x85, 0x6a, 0x00),   # amber-brown
    "Elevated":    RGBColor(0xcc, 0x77, 0x00),   # amber
    "High Risk":   RGBColor(0xb5, 0x2a, 0x1c),   # dark red
    "Critical":    RGBColor(0x7b, 0x0a, 0x02),   # deep red
}

# ReportLab colour values per classification band.
_BAND_COLORS_RL: dict[str, colors.Color] = {
    "Low Risk":    colors.HexColor("#2e862e"),
    "Moderate":    colors.HexColor("#856a00"),
    "Elevated":    colors.HexColor("#cc7700"),
    "High Risk":   colors.HexColor("#b52a1c"),
    "Critical":    colors.HexColor("#7b0a02"),
}

_SEVERITY_COLORS_RL: dict[str, colors.Color] = {
    "critical": colors.HexColor("#7b0a02"),
    "elevated": colors.HexColor("#b52a1c"),
    "watch":    colors.HexColor("#cc7700"),
    "clear":    colors.HexColor("#2e862e"),
}

_SEVERITY_HEX: dict[str, str] = {
    "critical": "#7b0a02",
    "elevated": "#b52a1c",
    "watch":    "#cc7700",
    "clear":    "#2e862e",
}

# ---------------------------------------------------------------------------
# Filename helper
# ---------------------------------------------------------------------------


def _make_slug(text: str) -> str:
    """
    Convert a string to a safe filename slug.

    Strips accents, replaces spaces and non-alphanumeric characters with
    underscores, collapses consecutive underscores, and lowercases.

    Args:
        text: Input string.

    Returns:
        str: Filesystem-safe slug.
    """
    nfkd = unicodedata.normalize("NFKD", text)
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", ascii_text).strip("_").lower()
    return slug or "report"


def _output_filename(context: dict[str, Any], report_type: str, ext: str) -> str:
    """
    Build the output filename from report metadata.

    Pattern: CP_OEI_{type}_{client_slug}_{date}_{report_id_prefix}.{ext}
    Example: CP_OEI_snapshot_acme_corp_2026-05-31_3f8a1b2c.docx

    Args:
        context:     Report context dict.
        report_type: 'snapshot' or 'monthly_brief'.
        ext:         File extension without dot, e.g. 'docx' or 'pdf'.

    Returns:
        str: Filename string.
    """
    client_slug = _make_slug(context.get("client_name", "client"))
    report_date = context.get("report_date", "undated")
    report_id = context.get("report_id", "00000000")
    type_slug = report_type.replace("_", "-")
    return (
        f"CP_OEI_{type_slug}_{client_slug}_{report_date}"
        f"_{report_id}.{ext}"
    )


# ---------------------------------------------------------------------------
# Watermark text builder
# ---------------------------------------------------------------------------


def _watermark_text(context: dict[str, Any]) -> str:
    """
    Build the watermark line from context fields.

    Format matches scoring spec Part 11 exactly:
      "Prepared exclusively for [Sponsor] -- Confidential |
       [Client] | [Date] | Report ID: [UUID]"

    Args:
        context: Report context dict.

    Returns:
        str: Single-line watermark string.
    """
    sponsor = context.get("sponsor_name", "")
    client = context.get("client_name", "")
    date = context.get("report_date", "")
    report_id = context.get("report_id", "")
    return (
        f"Prepared exclusively for {sponsor} -- Confidential"
        f" | {client} | {date} | Report ID: {report_id}"
    )


# ---------------------------------------------------------------------------
# Jinja2 render helper
# ---------------------------------------------------------------------------


def _render_template(context: dict[str, Any], report_type: str) -> str:
    """
    Render the appropriate Jinja2 template with the given context.

    Calls validate_no_unfilled_placeholders() on the rendered output
    before returning. Raises ValueError if any placeholder remains.

    Args:
        context:     Finalized report context dict.
        report_type: 'snapshot' or 'monthly_brief'.

    Returns:
        str: Rendered HTML string.

    Raises:
        ValueError: if any unfilled placeholder is found in the output.
    """
    template_name = (
        "oei_snapshot.html.j2"
        if report_type == "snapshot"
        else "oeil_monthly_brief.html.j2"
    )

    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        undefined=StrictUndefined,
        autoescape=False,
    )

    template = env.get_template(template_name)
    rendered = template.render(**context)
    validate_no_unfilled_placeholders(rendered)
    return rendered


# ---------------------------------------------------------------------------
# HTML -> docx walker
# ---------------------------------------------------------------------------


class _DocxHTMLParser(HTMLParser):
    """
    Walks a rendered HTML string and translates elements to python-docx
    calls on a Document object.

    Handles: h1, h2, h3, p, table/thead/tbody/tr/th/td, ul/li,
             strong/em/span, section, div, footer.

    Ignores: html, head, meta, title, style, script.
    """

    def __init__(self, doc: Document) -> None:
        super().__init__()
        self._doc = doc
        self._current_para = None
        self._current_run = None
        self._bold = False
        self._italic = False
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell_text = ""
        self._in_cell = False
        self._current_section_class = ""
        self._in_footer = False
        self._in_head = False
        self._skip_tags = {"html", "head", "meta", "title", "style", "script", "body"}
        self._block_tags = {"section", "div", "footer", "header"}
        self._table_header_row = False

    # ------------------------------------------------------------------
    # Tag handlers
    # ------------------------------------------------------------------

    def handle_starttag(self, tag: str, attrs: list) -> None:
        attr_dict = dict(attrs)
        css_class = attr_dict.get("class", "")

        if tag in self._skip_tags or tag == "head":
            if tag == "head":
                self._in_head = True
            return
        if self._in_head:
            return

        if tag == "footer":
            self._in_footer = True
            return

        if tag in ("h1", "h2", "h3"):
            level_map = {"h1": "Heading 1", "h2": "Heading 2", "h3": "Heading 3"}
            self._current_para = self._doc.add_paragraph(style=level_map[tag])
            self._current_run = None
            return

        if tag == "p":
            style = "Normal"
            if "narrative" in css_class or "section-note" in css_class:
                style = "Normal"
            elif "override-footnote" in css_class:
                style = "Normal"
            self._current_para = self._doc.add_paragraph(style=style)
            self._current_run = None
            return

        if tag in ("ul", "ol"):
            return

        if tag == "li":
            self._current_para = self._doc.add_paragraph(style="List Bullet")
            self._current_run = None
            return

        if tag == "strong":
            self._bold = True
            return

        if tag == "em":
            self._italic = True
            return

        if tag == "table":
            self._in_table = True
            self._table_rows = []
            return

        if tag == "thead":
            self._table_header_row = True
            return

        if tag == "tbody":
            return

        if tag == "tr":
            self._current_row = []
            return

        if tag in ("th", "td"):
            self._in_cell = True
            self._current_cell_text = ""
            return

        if tag in self._block_tags:
            return

        if tag == "img":
            src = attr_dict.get("src", "")
            if src.startswith("data:image/png;base64,"):
                b64_data = src.split(",", 1)[1]
                try:
                    img_bytes = base64.b64decode(b64_data)
                    img_stream = io.BytesIO(img_bytes)
                    self._doc.add_picture(img_stream, width=Inches(6.0))
                except Exception:
                    pass
            return

        if tag == "br":
            if self._current_para is not None:
                self._current_para.add_run("\n")
            return

    def handle_endtag(self, tag: str) -> None:
        if tag == "head":
            self._in_head = False
            return
        if self._in_head:
            return
        if tag == "footer":
            self._in_footer = False
            return

        if tag == "strong":
            self._bold = False
            return
        if tag == "em":
            self._italic = False
            return

        if tag in ("th", "td"):
            self._current_row.append(self._current_cell_text.strip())
            self._in_cell = False
            self._current_cell_text = ""
            return

        if tag == "tr":
            if self._current_row:
                self._table_rows.append(self._current_row)
            self._current_row = []
            return

        if tag == "thead":
            self._table_header_row = False
            return

        if tag == "table":
            self._in_table = False
            if self._table_rows:
                self._write_table(self._table_rows)
            self._table_rows = []
            return

    def handle_data(self, data: str) -> None:
        if self._in_head or self._in_footer:
            return

        text = data
        if not text.strip():
            return

        if self._in_cell:
            self._current_cell_text += text
            return

        if self._current_para is not None:
            run = self._current_para.add_run(text)
            run.bold = self._bold
            run.italic = self._italic

    # ------------------------------------------------------------------
    # Table writer
    # ------------------------------------------------------------------

    def _write_table(self, rows: list[list[str]]) -> None:
        """Write rows as a python-docx Table."""
        if not rows:
            return

        col_count = max(len(r) for r in rows)
        if col_count == 0:
            return

        # Normalise row lengths.
        normalised = [r + [""] * (col_count - len(r)) for r in rows]

        table = self._doc.add_table(rows=len(normalised), cols=col_count)
        table.style = "Table Grid"
        table.alignment = WD_TABLE_ALIGNMENT.LEFT

        for row_idx, row_data in enumerate(normalised):
            row = table.rows[row_idx]
            is_header = row_idx == 0
            for col_idx, cell_text in enumerate(row_data):
                cell = row.cells[col_idx]
                cell.text = cell_text
                para = cell.paragraphs[0]
                para.style = self._doc.styles["Normal"]
                if is_header:
                    for run in para.runs:
                        run.bold = True
                        run.font.size = Pt(9)
                else:
                    for run in para.runs:
                        run.font.size = Pt(9)

        self._doc.add_paragraph()


# ---------------------------------------------------------------------------
# docx style setup
# ---------------------------------------------------------------------------


def _configure_docx_styles(doc: Document) -> None:
    """
    Apply Criterion Partners visual style to all built-in docx styles.

    Uses a monochrome, professional palette: black body text, dark grey
    headings, no colour except in score tables.

    Args:
        doc: The Document object to modify in place.
    """
    styles = doc.styles

    # Normal (body text)
    normal = styles["Normal"]
    normal.font.name = "Calibri"
    normal.font.size = Pt(11)
    normal.font.color.rgb = RGBColor(0x1a, 0x1a, 0x1a)
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.line_spacing = Pt(14)

    # Heading 1 (report title / section)
    h1 = styles["Heading 1"]
    h1.font.name = "Calibri"
    h1.font.size = Pt(18)
    h1.font.bold = True
    h1.font.color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
    h1.paragraph_format.space_before = Pt(12)
    h1.paragraph_format.space_after = Pt(6)

    # Heading 2 (section heading)
    h2 = styles["Heading 2"]
    h2.font.name = "Calibri"
    h2.font.size = Pt(14)
    h2.font.bold = True
    h2.font.color.rgb = RGBColor(0x1a, 0x1a, 0x2e)
    h2.paragraph_format.space_before = Pt(14)
    h2.paragraph_format.space_after = Pt(4)

    # Heading 3 (sub-section heading)
    h3 = styles["Heading 3"]
    h3.font.name = "Calibri"
    h3.font.size = Pt(12)
    h3.font.bold = True
    h3.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    h3.paragraph_format.space_before = Pt(10)
    h3.paragraph_format.space_after = Pt(3)


def _add_docx_footer(doc: Document, watermark: str) -> None:
    """
    Add the watermark text to the footer of every section in the document.

    Args:
        doc:       The Document object.
        watermark: The watermark line string.
    """
    section = doc.sections[0]
    section.different_first_page_header_footer = False
    footer = section.footer
    footer_para = footer.paragraphs[0] if footer.paragraphs else footer.add_paragraph()
    footer_para.clear()
    footer_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    run = footer_para.add_run(watermark)
    run.font.name = "Calibri"
    run.font.size = Pt(7)
    run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
    run.font.italic = True


# ---------------------------------------------------------------------------
# docx builder
# ---------------------------------------------------------------------------


def build_docx(context: dict[str, Any], output_path: Path) -> Path:
    """
    Build a .docx report file from a finalized context dict.

    Renders the Jinja2 template to HTML, validates no unfilled placeholders,
    walks the HTML with _DocxHTMLParser, applies CP styles, and adds the
    watermark footer on every page.

    Args:
        context:     Finalized report context dict (from finalize_report()).
        output_path: Absolute path where the .docx file will be written.
                     Parent directory must exist.

    Returns:
        Path: output_path after successful write.

    Raises:
        ValueError: if any unfilled placeholder is found in the rendered HTML.
        Exception:  any file I/O error propagates to the caller.
    """
    report_type = context.get("report_type", "snapshot")
    watermark = _watermark_text(context)

    log.info(
        "Building docx: report_id=%s report_type=%s path=%s",
        context.get("report_id"),
        report_type,
        output_path,
    )

    rendered_html = _render_template(context, report_type)

    doc = Document()
    _configure_docx_styles(doc)

    # Cover page content (drawn manually for clean visual).
    doc.add_paragraph("CRITERION PARTNERS", style="Heading 1").alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle = "OEI Snapshot Report" if report_type == "snapshot" else "OEIL Monthly Intelligence Brief"
    p = doc.add_paragraph(subtitle, style="Heading 2")
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Metadata table.
    meta_rows = [
        ["Prepared For", context.get("sponsor_name", "")],
        ["Organization", context.get("client_name", "")],
        ["Reporting Period",
         f"{context.get('reporting_period_start', '')} to "
         f"{context.get('reporting_period_end', '')}"],
        ["Report Date", context.get("report_date", "")],
        ["Report ID", context.get("report_id", "")],
    ]
    if report_type == "monthly_brief":
        meta_rows.insert(2, ["Period", context.get("period_label", "")])

    meta_table = doc.add_table(rows=len(meta_rows), cols=2)
    meta_table.style = "Table Grid"
    for i, (label, value) in enumerate(meta_rows):
        row = meta_table.rows[i]
        row.cells[0].text = label
        row.cells[1].text = value
        for run in row.cells[0].paragraphs[0].runs:
            run.bold = True
            run.font.size = Pt(10)
        for run in row.cells[1].paragraphs[0].runs:
            run.font.size = Pt(10)

    conf = doc.add_paragraph()
    conf.alignment = WD_ALIGN_PARAGRAPH.CENTER
    conf_run = conf.add_run(
        f"Prepared exclusively for {context.get('sponsor_name', '')} -- Confidential"
    )
    conf_run.italic = True
    conf_run.font.size = Pt(9)
    conf_run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)

    doc.add_page_break()

    # Walk the rest of the HTML (skips cover which is handled above).
    parser = _DocxHTMLParser(doc)
    parser.feed(rendered_html)

    _add_docx_footer(doc, watermark)

    doc.save(str(output_path))
    log.info("docx written: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


# ---------------------------------------------------------------------------
# PDF builder
# ---------------------------------------------------------------------------


def _make_watermark_canvas_class(watermark_text: str) -> type:
    """
    Return a ReportLab canvas subclass that draws the watermark footer on
    every page. The subclass is created dynamically so the watermark text
    is captured in its closure without requiring a class-level global.

    ReportLab's canvasmaker protocol expects a callable that accepts
    (filename, **kwargs) and returns a canvas-like object. Passing a
    subclass of canvas.Canvas satisfies this contract.

    Args:
        watermark_text: The full watermark line to draw on every page.

    Returns:
        type: A canvas.Canvas subclass with watermark drawing behaviour.
    """

    class _WatermarkCanvas(rl_canvas.Canvas):
        def __init__(self, filename: str, **kwargs: Any) -> None:
            super().__init__(filename, **kwargs)
            self._wm_text = watermark_text

        def showPage(self) -> None:
            self.saveState()
            self.setFont("Helvetica-Oblique", 6.5)
            self.setFillColor(colors.HexColor("#888888"))
            page_width = self._pagesize[0]
            self.drawCentredString(
                page_width / 2,
                0.35 * inch,
                self._wm_text,
            )
            self.restoreState()
            super().showPage()

    return _WatermarkCanvas


def _make_rl_styles() -> dict[str, ParagraphStyle]:
    """Return a dict of ReportLab ParagraphStyle objects for the report."""
    base = getSampleStyleSheet()
    styles: dict[str, ParagraphStyle] = {}

    styles["firm"] = ParagraphStyle(
        "firm", parent=base["Normal"],
        fontSize=18, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_CENTER, spaceAfter=6,
    )
    styles["report_title"] = ParagraphStyle(
        "report_title", parent=base["Normal"],
        fontSize=14, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_CENTER, spaceAfter=4,
    )
    styles["heading2"] = ParagraphStyle(
        "heading2", parent=base["Normal"],
        fontSize=13, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        spaceBefore=14, spaceAfter=4,
    )
    styles["heading3"] = ParagraphStyle(
        "heading3", parent=base["Normal"],
        fontSize=11, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#333333"),
        spaceBefore=10, spaceAfter=3,
    )
    styles["body"] = ParagraphStyle(
        "body", parent=base["Normal"],
        fontSize=10, fontName="Helvetica",
        textColor=colors.HexColor("#1a1a1a"),
        leading=14, spaceAfter=8,
    )
    styles["body_italic"] = ParagraphStyle(
        "body_italic", parent=base["Normal"],
        fontSize=9, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#555555"),
        leading=12, spaceAfter=6,
    )
    styles["note"] = ParagraphStyle(
        "note", parent=base["Normal"],
        fontSize=9, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#555555"),
        leading=12, spaceAfter=6,
    )
    styles["table_header"] = ParagraphStyle(
        "table_header", parent=base["Normal"],
        fontSize=9, fontName="Helvetica-Bold",
        textColor=colors.white,
    )
    styles["table_cell"] = ParagraphStyle(
        "table_cell", parent=base["Normal"],
        fontSize=9, fontName="Helvetica",
        textColor=colors.HexColor("#1a1a1a"),
    )
    styles["score_large"] = ParagraphStyle(
        "score_large", parent=base["Normal"],
        fontSize=28, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
        alignment=TA_LEFT,
        leading=34, spaceAfter=2,
    )
    styles["cover_meta_label"] = ParagraphStyle(
        "cover_meta_label", parent=base["Normal"],
        fontSize=10, fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1a1a2e"),
    )
    styles["cover_meta_value"] = ParagraphStyle(
        "cover_meta_value", parent=base["Normal"],
        fontSize=10, fontName="Helvetica",
        textColor=colors.HexColor("#1a1a1a"),
    )
    styles["confidential"] = ParagraphStyle(
        "confidential", parent=base["Normal"],
        fontSize=9, fontName="Helvetica-Oblique",
        textColor=colors.HexColor("#666666"),
        alignment=TA_CENTER, spaceAfter=0,
    )
    return styles


def _score_color_rl(classification: str) -> colors.Color:
    return _BAND_COLORS_RL.get(classification, colors.HexColor("#1a1a2e"))


def _build_pdf_story(
    context: dict[str, Any],
    report_type: str,
    styles: dict[str, ParagraphStyle],
) -> list:
    """
    Build the full ReportLab Platypus story list from the context dict.

    Args:
        context:     Finalized report context dict.
        report_type: 'snapshot' or 'monthly_brief'.
        styles:      Dict of ParagraphStyle objects from _make_rl_styles().

    Returns:
        list: Platypus flowable list ready for SimpleDocTemplate.build().
    """
    story = []
    S = styles

    client_name = context.get("client_name", "")
    sponsor_name = context.get("sponsor_name", "")
    report_date = context.get("report_date", "")
    report_id = context.get("report_id", "")
    period_start = context.get("reporting_period_start", "")
    period_end = context.get("reporting_period_end", "")
    composite = context.get("oei_composite_score", 0)
    composite_class = context.get("composite_class", "")
    composite_desc = context.get("composite_description", "")

    # ------------------------------------------------------------------
    # Cover page
    # ------------------------------------------------------------------
    story.append(Spacer(1, 0.6 * inch))
    story.append(Paragraph("CRITERION PARTNERS", S["firm"]))
    title = (
        "OEI Snapshot Report"
        if report_type == "snapshot"
        else "OEIL Monthly Intelligence Brief"
    )
    story.append(Paragraph(title, S["report_title"]))
    story.append(HRFlowable(width="80%", thickness=1.5, color=colors.HexColor("#1a1a2e"), spaceAfter=12))
    story.append(Spacer(1, 0.2 * inch))

    cover_data = [
        [Paragraph("Prepared For", S["cover_meta_label"]), Paragraph(sponsor_name, S["cover_meta_value"])],
        [Paragraph("Organization", S["cover_meta_label"]), Paragraph(client_name, S["cover_meta_value"])],
        [Paragraph("Reporting Period", S["cover_meta_label"]),
         Paragraph(f"{period_start} to {period_end}", S["cover_meta_value"])],
        [Paragraph("Report Date", S["cover_meta_label"]), Paragraph(report_date, S["cover_meta_value"])],
        [Paragraph("Report ID", S["cover_meta_label"]), Paragraph(report_id, S["cover_meta_value"])],
    ]
    if report_type == "monthly_brief":
        period_label = context.get("period_label", "")
        cover_data.insert(2, [
            Paragraph("Period", S["cover_meta_label"]),
            Paragraph(period_label, S["cover_meta_value"]),
        ])

    cover_table = Table(cover_data, colWidths=[2 * inch, 4.5 * inch])
    cover_table.setStyle(TableStyle([
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.HexColor("#f7f7f7"), colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(cover_table)
    story.append(Spacer(1, 0.3 * inch))
    story.append(Paragraph(
        f"Prepared exclusively for {sponsor_name} -- Confidential",
        S["confidential"],
    ))

    # ------------------------------------------------------------------
    # Section: OEI Composite Score
    # ------------------------------------------------------------------
    story.append(Spacer(1, 0.4 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
    story.append(Paragraph("OEI Composite Score", S["heading2"]))

    score_color = _score_color_rl(composite_class)
    score_hex = score_color.hexval() if hasattr(score_color, "hexval") else "#1a1a2e"
    score_block = KeepTogether([
        Paragraph(
            f'<font color="{score_hex}"><b>{composite}</b></font>',
            S["score_large"],
        ),
        Paragraph(f"<b>{composite_class}</b>", S["heading3"]),
        Spacer(1, 4),
    ])
    story.append(score_block)
    if composite_desc:
        story.append(Paragraph(composite_desc, S["body"]))

    # Prior period delta (monthly brief and snapshot if available).
    prior_composite = context.get("prior_composite_score")
    score_delta = context.get("score_delta")
    score_direction = context.get("score_direction")
    if prior_composite is not None and score_delta is not None:
        delta_sign = "+" if score_delta > 0 else ""
        story.append(Paragraph(
            f"Prior Period: {prior_composite}  |  "
            f"Movement: {delta_sign}{score_delta} ({score_direction})",
            S["note"],
        ))

    if context.get("has_override"):
        story.append(Paragraph(context.get("override_footnote", ""), S["note"]))

    # ------------------------------------------------------------------
    # Score trajectory chart (monthly brief only).
    # ------------------------------------------------------------------
    chart_b64 = context.get("trajectory_chart_b64", "")
    if chart_b64:
        story.append(Spacer(1, 0.2 * inch))
        story.append(Paragraph("Score Trajectory", S["heading2"]))
        try:
            img_bytes = base64.b64decode(chart_b64)
            img_stream = io.BytesIO(img_bytes)
            rl_img = RLImage(img_stream, width=6.5 * inch, height=3.2 * inch)
            story.append(rl_img)
        except Exception:
            story.append(Paragraph("Score trajectory chart could not be embedded.", S["note"]))

    # ------------------------------------------------------------------
    # Section: OEI Scorecard (dimension table).
    # ------------------------------------------------------------------
    dimensions = context.get("dimensions", [])
    if dimensions:
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
        story.append(Paragraph("OEI Scorecard", S["heading2"]))

        has_prior = any(d.get("prior_score") is not None for d in dimensions)
        if has_prior:
            headers = ["Dimension", "Score", "Classification", "Prior", "Movement"]
            col_widths = [2.5 * inch, 0.7 * inch, 1.3 * inch, 0.7 * inch, 1.0 * inch]
        else:
            headers = ["Dimension", "Score", "Classification"]
            col_widths = [3.0 * inch, 0.9 * inch, 2.0 * inch]

        scorecard_data = [[Paragraph(h, S["table_header"]) for h in headers]]
        for dim in dimensions:
            score_str = str(dim.get("score", ""))
            row = [
                Paragraph(dim.get("name", ""), S["table_cell"]),
                Paragraph(score_str, S["table_cell"]),
                Paragraph(dim.get("classification", ""), S["table_cell"]),
            ]
            if has_prior:
                prior_val = dim.get("prior_score")
                delta_val = dim.get("delta")
                row.append(Paragraph(str(prior_val) if prior_val is not None else "--", S["table_cell"]))
                if delta_val is not None and delta_val != 0:
                    sign = "+" if delta_val > 0 else ""
                    row.append(Paragraph(f"{sign}{delta_val}", S["table_cell"]))
                else:
                    row.append(Paragraph("--", S["table_cell"]))
            scorecard_data.append(row)

        # Composite row.
        composite_row = [
            Paragraph("<b>OEI Composite</b>", S["table_cell"]),
            Paragraph(f"<b>{composite}</b>", S["table_cell"]),
            Paragraph(f"<b>{composite_class}</b>", S["table_cell"]),
        ]
        if has_prior:
            composite_row.append(Paragraph(
                str(prior_composite) if prior_composite is not None else "--",
                S["table_cell"],
            ))
            if score_delta is not None:
                sign = "+" if score_delta > 0 else ""
                composite_row.append(Paragraph(f"<b>{sign}{score_delta}</b>", S["table_cell"]))
            else:
                composite_row.append(Paragraph("--", S["table_cell"]))
        scorecard_data.append(composite_row)

        sc_table = Table(scorecard_data, colWidths=col_widths)
        sc_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#e8e8e8")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.HexColor("#f7f7f7"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(sc_table)

    # ------------------------------------------------------------------
    # Dimension cards (sub-category impact indicators).
    # ------------------------------------------------------------------
    if dimensions:
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
        story.append(Paragraph("Dimension Analysis", S["heading2"]))

        for dim in dimensions:
            dim_score = dim.get("score", 0)
            dim_class = dim.get("classification", "")
            story.append(Paragraph(
                f"Dimension {dim.get('dimension_id', '')}: {dim.get('name', '')}  "
                f"-- Score: {dim_score}  ({dim_class})",
                S["heading3"],
            ))
            if dim.get("description"):
                story.append(Paragraph(dim["description"], S["note"]))

            sub_cats = dim.get("sub_categories", [])
            if sub_cats:
                sc_headers = ["Sub-Category", "Impact", "Score"]
                sc_data = [[Paragraph(h, S["table_header"]) for h in sc_headers]]
                for sc in sub_cats:
                    missing_flag = " [Missing Data Default]" if sc.get("is_missing_data") else ""
                    sc_data.append([
                        Paragraph(sc.get("name", "") + missing_flag, S["table_cell"]),
                        Paragraph(sc.get("impact", ""), S["table_cell"]),
                        Paragraph(str(sc.get("score", "")), S["table_cell"]),
                    ])
                    if sc.get("interpretation"):
                        sc_data.append([
                            Paragraph(f"  {sc['interpretation']}", S["note"]),
                            Paragraph("", S["note"]),
                            Paragraph("", S["note"]),
                        ])

                sc_table = Table(sc_data, colWidths=[3.0 * inch, 1.3 * inch, 0.7 * inch])
                sc_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
                    ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f7f7"), colors.white]),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ]))
                story.append(sc_table)
            story.append(Spacer(1, 0.1 * inch))

    # ------------------------------------------------------------------
    # Signal readings table.
    # ------------------------------------------------------------------
    signal_readings = context.get("signal_readings", [])
    if signal_readings:
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
        story.append(Paragraph("Signal Readings", S["heading2"]))

        sig_headers = ["Signal", "Value", "Threshold", "Breach", "Severity"]
        sig_data = [[Paragraph(h, S["table_header"]) for h in sig_headers]]
        for sig in signal_readings:
            severity = sig.get("severity", "clear")
            sev_hex = _SEVERITY_HEX.get(severity, "#1a1a1a")
            sig_data.append([
                Paragraph(sig.get("display_name", sig.get("signal_name", "")), S["table_cell"]),
                Paragraph(f"{sig.get('signal_value', 0):.4f}", S["table_cell"]),
                Paragraph(f"{sig.get('threshold_value', 0):.4f}", S["table_cell"]),
                Paragraph("Yes" if sig.get("threshold_breached") else "No", S["table_cell"]),
                Paragraph(
                    f'<font color="{sev_hex}"><b>{severity.upper()}</b></font>',
                    S["table_cell"],
                ),
            ])

        sig_table = Table(
            sig_data,
            colWidths=[2.2 * inch, 1.0 * inch, 1.0 * inch, 0.7 * inch, 1.0 * inch],
        )
        sig_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f7f7"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(sig_table)

    # ------------------------------------------------------------------
    # Narrative sections.
    # ------------------------------------------------------------------
    narrative_sections: list[tuple[str, str]] = []

    if report_type == "snapshot":
        narrative_sections = [
            ("Executive Summary", context.get("narrative_executive_summary", "")),
            ("Key Findings", context.get("narrative_key_findings", "")),
            ("Recommended Focus Areas", context.get("narrative_recommended_focus", "")),
        ]
    else:
        narrative_sections = [
            ("Executive Summary", context.get("narrative_executive_summary", "")),
        ]
        deep_dives = context.get("narrative_dimension_deep_dives", {})
        dim_names = {
            "1": "Strategic Saturation",
            "2": "Governance Responsiveness",
            "3": "Execution Visibility",
            "4": "Reporting Integrity",
            "5": "Organizational Sustainability",
        }
        for dim_id in ("1", "2", "3", "4", "5"):
            text = deep_dives.get(dim_id, "")
            if text:
                narrative_sections.append(
                    (f"Dimension Analysis: {dim_names[dim_id]}", text)
                )
        narrative_sections.append(
            ("Forward Focus", context.get("narrative_forward_focus", ""))
        )

    for heading, text in narrative_sections:
        if not text:
            continue
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
        story.append(Paragraph(heading, S["heading2"]))
        for para_text in text.split("\n\n"):
            stripped = para_text.strip()
            if stripped:
                story.append(Paragraph(stripped, S["body"]))

    # ------------------------------------------------------------------
    # Active alerts (monthly brief).
    # ------------------------------------------------------------------
    active_alerts = context.get("active_alerts", [])
    if active_alerts:
        story.append(Spacer(1, 0.2 * inch))
        story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#cccccc"), spaceAfter=8))
        story.append(Paragraph("Active Alerts", S["heading2"]))

        alert_headers = ["Signal", "Severity", "Triggered", "Acknowledged"]
        alert_data = [[Paragraph(h, S["table_header"]) for h in alert_headers]]
        for alert in active_alerts:
            severity = alert.get("severity", "watch")
            sev_hex = _SEVERITY_HEX.get(severity, "#1a1a1a")
            alert_data.append([
                Paragraph(alert.get("display_name", alert.get("signal_name", "")), S["table_cell"]),
                Paragraph(f'<font color="{sev_hex}"><b>{severity.upper()}</b></font>', S["table_cell"]),
                Paragraph(alert.get("triggered_at", ""), S["table_cell"]),
                Paragraph("Yes" if alert.get("acknowledged") else "No", S["table_cell"]),
            ])

        alert_table = Table(
            alert_data,
            colWidths=[2.2 * inch, 1.0 * inch, 2.0 * inch, 1.0 * inch],
        )
        alert_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1a1a2e")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.HexColor("#f7f7f7"), colors.white]),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#dddddd")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ]))
        story.append(alert_table)

    # ------------------------------------------------------------------
    # Qualitative intelligence notes (conditional).
    # ------------------------------------------------------------------
    qual_notes = context.get("qualitative_notes", [])
    if qual_notes:
        for i, note in enumerate(qual_notes):
            meta = (
                f"{note.get('entry_date', '')} | {note.get('entry_type', '')} | "
                f"Materiality: {note.get('materiality', '')}"
            )
            if note.get("program_reference") and note["program_reference"] != "General":
                meta += f" | Re: {note['program_reference']}"

            block_items = []
            if i == 0:
                # Section heading and intro only on the first entry block.
                block_items += [
                    Spacer(1, 0.2 * inch),
                    HRFlowable(width="100%", thickness=0.5,
                                color=colors.HexColor("#cccccc"), spaceAfter=8),
                    Paragraph("Engagement Intelligence Notes", S["heading2"]),
                    Paragraph(
                        "The following observations were recorded during the engagement "
                        "period and inform the findings presented in this report.",
                        S["note"],
                    ),
                ]
            block_items += [
                Paragraph(meta, S["note"]),
                Paragraph(note.get("intelligence_note", ""), S["body"]),
            ]
            story.append(KeepTogether(block_items))

    return story


def build_pdf(context: dict[str, Any], output_path: Path) -> Path:
    """
    Build a .pdf report file from a finalized context dict.

    Uses ReportLab Platypus. Watermark footer is drawn on every page via
    a canvas callback. Watermark text is also embedded in PDF metadata
    (author, subject, keywords) as specified in scoring spec Part 11.

    Args:
        context:     Finalized report context dict.
        output_path: Absolute path where the .pdf file will be written.

    Returns:
        Path: output_path after successful write.

    Raises:
        Exception: any file I/O or ReportLab error propagates.
    """
    report_type = context.get("report_type", "snapshot")
    watermark = _watermark_text(context)
    report_id = context.get("report_id", "")
    client_name = context.get("client_name", "")
    report_date = context.get("report_date", "")
    sponsor_name = context.get("sponsor_name", "")

    log.info(
        "Building pdf: report_id=%s report_type=%s path=%s",
        report_id, report_type, output_path,
    )

    styles = _make_rl_styles()
    story = _build_pdf_story(context, report_type, styles)

    wm_canvas_cls = _make_watermark_canvas_class(watermark)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=LETTER,
        leftMargin=1.0 * inch,
        rightMargin=1.0 * inch,
        topMargin=1.0 * inch,
        bottomMargin=0.75 * inch,
        title=f"Criterion Partners OEI Report -- {client_name} -- {report_date}",
        author="Criterion Partners",
        subject=f"Prepared exclusively for {sponsor_name} -- Confidential",
        keywords=(
            f"report_id={report_id} client={client_name} date={report_date}"
        ),
    )

    doc.build(story, canvasmaker=wm_canvas_cls)

    log.info("pdf written: %s (%d bytes)", output_path, output_path.stat().st_size)
    return output_path


# ---------------------------------------------------------------------------
# Public orchestration
# ---------------------------------------------------------------------------


def generate_report_files(
    context: dict[str, Any],
    report_type: str,
    output_dir: Path,
) -> tuple[Path, Path]:
    """
    Generate both .docx and .pdf report files from a finalized context dict.

    This is the single entry point called by the report generation pipeline
    after finalize_report() returns an approved context. Both files are
    written to output_dir. The caller is responsible for storing the
    returned paths in the reports table.

    Args:
        context:     Finalized, Managing-Partner-approved context dict.
        report_type: 'snapshot' or 'monthly_brief'.
        output_dir:  Directory where output files will be written.
                     Must exist before calling this function.

    Returns:
        tuple[Path, Path]: (docx_path, pdf_path).

    Raises:
        NotADirectoryError: if output_dir does not exist.
        ValueError:         if any unfilled placeholder is found in the
                            rendered template.
        Exception:          any file I/O or document generation error.
    """
    if not output_dir.is_dir():
        raise NotADirectoryError(
            f"output_dir '{output_dir}' does not exist or is not a directory. "
            "Create the directory before calling generate_report_files()."
        )

    docx_name = _output_filename(context, report_type, "docx")
    pdf_name = _output_filename(context, report_type, "pdf")
    docx_path = output_dir / docx_name
    pdf_path = output_dir / pdf_name

    log.info(
        "Generating report files: report_id=%s type=%s dir=%s",
        context.get("report_id"),
        report_type,
        output_dir,
    )

    build_docx(context, docx_path)
    build_pdf(context, pdf_path)

    log.info(
        "Report files generated: docx=%s pdf=%s",
        docx_path.name,
        pdf_path.name,
    )

    return docx_path, pdf_path
