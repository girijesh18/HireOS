"""
Document generation — Markdown → PDF and DOCX.
Saves versioned files to the output directory.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from datetime import datetime

from loguru import logger


OUTPUT_DIR = Path(os.getenv("OUTPUT_DIR", "./output"))


def _job_dir(job_id: int) -> Path:
    d = OUTPUT_DIR / str(job_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _version_suffix(version: int) -> str:
    return f"_v{version}"


# ── PDF via WeasyPrint ─────────────────────────────────────────────────────────

def _md_to_html(md_text: str) -> str:
    """Convert markdown to HTML for PDF rendering."""
    import markdown as md_lib
    body = md_lib.markdown(md_text, extensions=["tables", "fenced_code", "nl2br"])
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<style>
  body {{
    font-family: 'Helvetica Neue', Arial, sans-serif;
    font-size: 11pt;
    line-height: 1.5;
    color: #111;
    max-width: 800px;
    margin: 0 auto;
    padding: 2cm;
  }}
  h1 {{ font-size: 20pt; margin-bottom: 4px; color: #1a1a2e; }}
  h2 {{ font-size: 13pt; border-bottom: 1px solid #ccc; padding-bottom: 4px; margin-top: 20px; color: #1a1a2e; }}
  h3 {{ font-size: 11pt; margin-top: 12px; margin-bottom: 0; }}
  ul {{ margin: 4px 0 10px 20px; padding: 0; }}
  li {{ margin-bottom: 3px; }}
  p {{ margin: 6px 0; }}
  hr {{ border: none; border-top: 1px solid #ddd; margin: 16px 0; }}
  strong {{ font-weight: 700; }}
  em {{ font-style: italic; }}
  code {{ background: #f4f4f4; padding: 0 4px; border-radius: 3px; font-size: 10pt; }}
  table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
  td, th {{ border: 1px solid #ddd; padding: 6px 10px; text-align: left; }}
  th {{ background: #f8f8f8; font-weight: 600; }}
  @page {{ margin: 1.5cm; }}
</style>
</head>
<body>{body}</body>
</html>"""


def save_resume(job_id: int, version: int, content_md: str) -> dict:
    """Save resume as .md, .pdf, and .docx. Returns dict of paths."""
    d = _job_dir(job_id)
    suffix = _version_suffix(version)
    paths = {}

    # Markdown
    md_path = d / f"resume{suffix}.md"
    md_path.write_text(content_md, encoding="utf-8")
    paths["md"] = str(md_path)

    # PDF
    try:
        from weasyprint import HTML
        pdf_path = d / f"resume{suffix}.pdf"
        HTML(string=_md_to_html(content_md)).write_pdf(str(pdf_path))
        paths["pdf"] = str(pdf_path)
        logger.info(f"[Docs] PDF saved: {pdf_path}")
    except Exception as e:
        logger.warning(f"[Docs] PDF failed: {e}")

    # DOCX
    try:
        from docx import Document
        from docx.shared import Pt, Inches
        from docx.enum.style import WD_STYLE_TYPE

        doc = Document()
        # Set margins
        for section in doc.sections:
            section.top_margin = Inches(0.75)
            section.bottom_margin = Inches(0.75)
            section.left_margin = Inches(0.85)
            section.right_margin = Inches(0.85)

        _md_to_docx(doc, content_md)

        docx_path = d / f"resume{suffix}.docx"
        doc.save(str(docx_path))
        paths["docx"] = str(docx_path)
        logger.info(f"[Docs] DOCX saved: {docx_path}")
    except Exception as e:
        logger.warning(f"[Docs] DOCX failed: {e}")

    return paths


def save_cover_letter(job_id: int, version: int, content_md: str) -> dict:
    """Save cover letter as .md, .pdf, and .docx."""
    d = _job_dir(job_id)
    suffix = _version_suffix(version)
    paths = {}

    md_path = d / f"cover_letter{suffix}.md"
    md_path.write_text(content_md, encoding="utf-8")
    paths["md"] = str(md_path)

    try:
        from weasyprint import HTML
        pdf_path = d / f"cover_letter{suffix}.pdf"
        HTML(string=_md_to_html(content_md)).write_pdf(str(pdf_path))
        paths["pdf"] = str(pdf_path)
    except Exception as e:
        logger.warning(f"[Docs] Cover letter PDF failed: {e}")

    try:
        from docx import Document
        from docx.shared import Inches
        doc = Document()
        for section in doc.sections:
            section.top_margin = Inches(1.0)
            section.bottom_margin = Inches(1.0)
            section.left_margin = Inches(1.1)
            section.right_margin = Inches(1.1)
        _md_to_docx(doc, content_md)
        docx_path = d / f"cover_letter{suffix}.docx"
        doc.save(str(docx_path))
        paths["docx"] = str(docx_path)
    except Exception as e:
        logger.warning(f"[Docs] Cover letter DOCX failed: {e}")

    return paths


def _md_to_docx(doc, md_text: str):
    """Simple markdown → python-docx converter."""
    from docx.shared import Pt, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    # Set default style
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(10.5)

    lines = md_text.split("\n")
    para = None

    for line in lines:
        line_stripped = line.rstrip()

        if line_stripped.startswith("### "):
            p = doc.add_heading(line_stripped[4:], level=3)
        elif line_stripped.startswith("## "):
            p = doc.add_heading(line_stripped[3:], level=2)
        elif line_stripped.startswith("# "):
            p = doc.add_heading(line_stripped[2:], level=1)
        elif line_stripped.startswith("- ") or line_stripped.startswith("* "):
            p = doc.add_paragraph(line_stripped[2:], style="List Bullet")
        elif line_stripped.startswith("---") or line_stripped.startswith("___"):
            # Horizontal rule
            p = doc.add_paragraph()
            p.paragraph_format.space_after = Pt(0)
            run = p.add_run("─" * 80)
            run.font.color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
        elif line_stripped == "":
            para = None
            continue
        else:
            # Normal paragraph — handle **bold** and *italic* inline
            p = doc.add_paragraph()
            _add_inline_formatting(p, line_stripped)

    return doc


def _add_inline_formatting(para, text: str):
    """Parse **bold** and *italic* inline markdown into docx runs."""
    import re
    parts = re.split(r"(\*\*[^*]+\*\*|\*[^*]+\*)", text)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            run = para.add_run(part[2:-2])
            run.bold = True
        elif part.startswith("*") and part.endswith("*"):
            run = para.add_run(part[1:-1])
            run.italic = True
        else:
            para.add_run(part)
