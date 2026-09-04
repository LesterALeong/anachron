"""Build the deterministic, pre-full-results Anachron v3 paper preview."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIRECTORY = REPOSITORY_ROOT / "paper" / "v3_measurement"
BUILD_DIRECTORY = PAPER_DIRECTORY / "build"
REQUIRED_BANNER = "PRE-FULL-RESULTS MANUSCRIPT - NO EMPIRICAL CLAIMS - NOT FOR SUBMISSION"
EXACT_STATUS_SENTENCE = "FULL STUDY NOT YET AUTHORIZED OR RUN."
EXPECTED_TECTONIC_SHA256 = "99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d"
EXPECTED_PROTOCOL_TAG = "v3-measurement-protocol-v1"
EXPECTED_PROTOCOL_TAG_OBJECT = "1f4b5088f6dda5134fe3d21176b9274c0165d94a"
EXPECTED_PROTOCOL_COMMIT = "cc28c9890455c5bde09ee5710c411ee229e0f9e5"
EXPECTED_FULL_PLAN_SHA256 = "23b2dfc70437826579b4c97c8caf5bff59540dfb41ce2c15aa852c39f6888b90"
EXPECTED_FROZEN_MATRIX_SHA256 = "b99b2b07386900d7d47f2fb7c89a96cae460ae45e449d4522073f0fc5ab89f88"
EXPECTED_MANUSCRIPT_SHA256 = "898c2f0910c0bab70fba1f3ba50b06602bce17c0ac636ed479c628680860dd1b"
EXPECTED_PAPER_MATRIX_SHA256 = "c304b51bf486d5dd2380a6de9b613b5d2a08d183b2323d1b8ee2a51c0e807653"
PREVIEW_TREE_FILES = ("README.txt", "main.tex", "references.bib")
BANNER_TOP_BAND_HEIGHT = 60
BANNER_WORDS = tuple(word for word in REQUIRED_BANNER.split() if word != "-")
FORBIDDEN_ARGUMENT_PREFIXES = (
    "--evidence",
    "--receipt",
    "--go",
    "--review",
    "--final",
    "--submission",
    "--archive",
    "--upload",
)
FORBIDDEN_MANUSCRIPT_PATTERNS = (
    re.compile(r"\bwe (?:find|found|observe|observed|measured)\b", re.IGNORECASE),
    re.compile(r"\b(?:observed|empirical) (?:rate|value|result|outcome|reduction)\b", re.IGNORECASE),
    re.compile(r"\bTCLR\s+(?:was|is)\s+\d", re.IGNORECASE),
    re.compile(r"\bfalsifier\s+(?:was\s+)?(?:positive|negative)\b", re.IGNORECASE),
    re.compile(r"\b(?:falsifier|calibration) (?:result|outcome|response)\b", re.IGNORECASE),
    re.compile(r"\b(?:model|Qwen) (?:answer|answers|answered|output|outputs|leaked|returned)\b", re.IGNORECASE),
    re.compile(r"\b(?:submit|upload|contact|endorsement request)\b", re.IGNORECASE),
)
INLINE_CITATION = re.compile(r"\[\[cite:([a-z0-9-]+)\]\]")


class PreliminaryPaperError(ValueError):
    """Raised when a preliminary build would cross the no-results boundary."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_path(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PreliminaryPaperError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw.replace(b"\r\n", b"\n"):
        raise PreliminaryPaperError(f"JSON is not canonical: {path}")
    return value


def git_output(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise PreliminaryPaperError(f"git {' '.join(arguments)} failed: {completed.stderr.strip()}")
    return completed.stdout.strip()


def parse_bibliography_keys(path: Path) -> set[str]:
    return set(re.findall(r"^@[A-Za-z]+\{([^,]+),", path.read_text(encoding="utf-8"), re.MULTILINE))


def citation_label(key: str) -> str:
    families = key.split("-")
    return families[0].capitalize() + " et al."


def validate_contract(repository: Path) -> dict[str, Any]:
    contract_path = repository / "paper/v3_measurement/paper_contract.json"
    contract = load_canonical_json(contract_path)
    expected = {
        "schema_version": "anachron-v3-preliminary-paper-contract-v1",
        "state": "preliminary",
        "protocol_tag": EXPECTED_PROTOCOL_TAG,
        "protocol_tag_object": EXPECTED_PROTOCOL_TAG_OBJECT,
        "protocol_commit": EXPECTED_PROTOCOL_COMMIT,
        "full_plan_sha256": EXPECTED_FULL_PLAN_SHA256,
        "frozen_protocol_matrix_sha256": EXPECTED_FROZEN_MATRIX_SHA256,
        "manuscript_sha256": EXPECTED_MANUSCRIPT_SHA256,
        "paper_acceptance_matrix_sha256": EXPECTED_PAPER_MATRIX_SHA256,
    }
    for field, value in expected.items():
        if contract.get(field) != value:
            raise PreliminaryPaperError(f"paper contract drifted at {field}")
    tectonic = contract.get("tectonic")
    if tectonic != {"sha256": EXPECTED_TECTONIC_SHA256, "version": "0.17.0"}:
        raise PreliminaryPaperError("paper contract Tectonic identity drifted")
    bound_paths = {
        "full_plan_sha256": repository / "research/v3_measurement/full_plan.json",
        "frozen_protocol_matrix_sha256": repository / "research/v3_measurement/ACCEPTANCE_MATRIX.md",
        "manuscript_sha256": repository / "paper/v3_measurement/manuscript.json",
        "paper_acceptance_matrix_sha256": repository / "paper/v3_measurement/PAPER_ACCEPTANCE_MATRIX.md",
    }
    for field, path in bound_paths.items():
        if sha256_path(path) != contract[field]:
            raise PreliminaryPaperError(f"bound file hash drifted: {path.as_posix()}")
    if git_output(repository, "cat-file", "-t", EXPECTED_PROTOCOL_TAG) != "tag":
        raise PreliminaryPaperError("protocol reference is not an annotated tag")
    if git_output(repository, "rev-parse", EXPECTED_PROTOCOL_TAG) != EXPECTED_PROTOCOL_TAG_OBJECT:
        raise PreliminaryPaperError("protocol tag object drifted")
    if git_output(repository, "rev-parse", f"{EXPECTED_PROTOCOL_TAG}^{{}}") != EXPECTED_PROTOCOL_COMMIT:
        raise PreliminaryPaperError("protocol tag commit drifted")
    return contract


def validate_manuscript(repository: Path, *, manuscript: dict[str, Any] | None = None) -> dict[str, Any]:
    manuscript = manuscript or load_canonical_json(repository / "paper/v3_measurement/manuscript.json")
    if manuscript.get("schema_version") != "anachron-v3-preliminary-manuscript-v1":
        raise PreliminaryPaperError("unexpected manuscript schema version")
    if manuscript.get("banner") != REQUIRED_BANNER:
        raise PreliminaryPaperError("manuscript banner drifted")
    pages = manuscript.get("pages")
    appendix = manuscript.get("appendix")
    citations = manuscript.get("citations")
    if not isinstance(pages, list) or len(pages) != 6 or not isinstance(appendix, list):
        raise PreliminaryPaperError("manuscript must define six body pages and an appendix")
    if not isinstance(citations, list) or not all(isinstance(key, str) for key in citations):
        raise PreliminaryPaperError("manuscript citations have an unexpected schema")
    body_parts: list[str] = []
    seen_markers: list[str] = []
    for page in pages:
        if not isinstance(page, dict) or not isinstance(page.get("heading"), str):
            raise PreliminaryPaperError("manuscript page has an unexpected schema")
        paragraphs = page.get("paragraphs")
        if not isinstance(paragraphs, list) or not all(isinstance(item, str) for item in paragraphs):
            raise PreliminaryPaperError("manuscript page paragraphs have an unexpected schema")
        body_parts.extend(paragraphs)
        seen_markers.extend(INLINE_CITATION.findall("\n".join(paragraphs)))
    if not all(isinstance(item, str) for item in appendix):
        raise PreliminaryPaperError("manuscript appendix has an unexpected schema")
    body = "\n".join(body_parts + appendix)
    if EXACT_STATUS_SENTENCE not in body:
        raise PreliminaryPaperError("manuscript lacks the exact preliminary status sentence")
    if {"336", "264", "72"} - set(re.findall(r"\b\d+\b", body)):
        raise PreliminaryPaperError("manuscript lacks planned cohort accounting")
    for pattern in FORBIDDEN_MANUSCRIPT_PATTERNS:
        if pattern.search(body):
            raise PreliminaryPaperError("manuscript contains a forbidden empirical or external-action claim")
    bibliography_keys = parse_bibliography_keys(repository / "paper/v3_measurement/references.bib")
    if set(citations) != set(seen_markers):
        raise PreliminaryPaperError("top-level citations do not match inline citation markers")
    if set(citations) - bibliography_keys:
        raise PreliminaryPaperError("manuscript cites a source absent from references.bib")
    return manuscript


def render_citations(text: str) -> str:
    return INLINE_CITATION.sub(lambda match: f" ({citation_label(match.group(1))})", text)


def tex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def render_tex_citations(text: str) -> str:
    replacements: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        token = f"CITATIONTOKEN{len(replacements)}"
        replacements[token] = rf"\cite{{{match.group(1)}}}"
        return token

    rendered = tex_escape(INLINE_CITATION.sub(replace, text))
    for token, citation in replacements.items():
        rendered = rendered.replace(token, citation)
    return rendered


def provenance_lines(contract: dict[str, Any]) -> list[str]:
    return [
        f"Protocol tag: {contract['protocol_tag']}",
        f"Tag object: {contract['protocol_tag_object']}",
        f"Commit: {contract['protocol_commit']}",
        f"Full plan SHA-256: {contract['full_plan_sha256']}",
        f"Frozen matrix SHA-256: {contract['frozen_protocol_matrix_sha256']}",
        f"Preview matrix SHA-256: {contract['paper_acceptance_matrix_sha256']}",
    ]


def _styles():
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    styles = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("V3Title", parent=styles["Title"], alignment=TA_CENTER, fontName="Helvetica-Bold", fontSize=17, leading=21, spaceAfter=7),
        "author": ParagraphStyle("V3Author", parent=styles["Normal"], alignment=TA_CENTER, fontSize=9, leading=12, spaceAfter=10),
        "heading": ParagraphStyle("V3Heading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=13, spaceBefore=3, spaceAfter=5),
        "body": ParagraphStyle("V3Body", parent=styles["BodyText"], fontName="Helvetica", fontSize=9, leading=12, spaceAfter=7),
        "status": ParagraphStyle("V3Status", parent=styles["BodyText"], alignment=TA_CENTER, fontName="Helvetica-Bold", textColor="#8B1E1E", fontSize=10, leading=13, spaceAfter=9),
        "caption": ParagraphStyle("V3Caption", parent=styles["BodyText"], fontName="Helvetica-Oblique", fontSize=8, leading=10, spaceBefore=5, spaceAfter=7),
        "small": ParagraphStyle("V3Small", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.5, leading=9.2, spaceAfter=3),
        "reference": ParagraphStyle("V3Reference", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.2, leading=8.8, leftIndent=10, firstLineIndent=-10, spaceAfter=2),
    }


def table_one():
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    rows = [
        ["Component", "Planned design"],
        ["Panel", "336 trajectories: 264 primary; 72 disclosed development"],
        ["Models", "qwen2.5:7b and qwen3:14b-q4_K_M"],
        ["Modes", "Unrestricted trace and point-in-time-enforced trace"],
        ["Scientific requests", "Tool-enabled first request; tool-free terminal request"],
        ["Calibration", "Two tool-less planned requests, excluded from scoring"],
        ["Journal", "Append-only, no retries, raw-byte replay boundary"],
    ]
    table = Table(rows, colWidths=[115, 350], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#17365D")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#AAB7C4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F4F7FA")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    return table


def flow_figure(steps: list[str]):
    from reportlab.lib import colors
    from reportlab.platypus import Table, TableStyle

    cells: list[Any] = []
    for index, step in enumerate(steps):
        cells.append(step)
        if index < len(steps) - 1:
            cells.append("→")
    table = Table([cells], colWidths=[108 if cell != "→" else 22 for cell in cells], hAlign="CENTER")
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("LEADING", (0, 0), (-1, -1), 10),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 12),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
    ]
    for index, cell in enumerate(cells):
        if cell != "→":
            style.extend([
                ("BACKGROUND", (index, 0), (index, 0), colors.HexColor("#E9F0F7")),
                ("BOX", (index, 0), (index, 0), 0.7, colors.HexColor("#17365D")),
            ])
    table.setStyle(TableStyle(style))
    return table


def bibliography_lines(path: Path, citations: list[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    lines = []
    for key in citations:
        match = re.search(rf"^@[A-Za-z]+\{{{re.escape(key)},(.*?)(?=^@|\Z)", text, re.MULTILINE | re.DOTALL)
        if not match:
            raise PreliminaryPaperError(f"missing bibliography record: {key}")
        entry = match.group(1)
        title = re.search(r"title\s*=\s*\{(.*?)\}", entry, re.DOTALL)
        year = re.search(r"year\s*=\s*\{(.*?)\}", entry, re.DOTALL)
        author = re.search(r"author\s*=\s*\{(.*?)\}", entry, re.DOTALL)
        if not title or not year or not author:
            raise PreliminaryPaperError(f"incomplete bibliography record: {key}")
        venue = re.search(r"(?:booktitle|journal)\s*=\s*\{(.*?)\}", entry, re.DOTALL)
        locator = re.search(r"(?:doi|url)\s*=\s*\{(.*?)\}", entry, re.DOTALL)
        suffix = ". ".join(
            value.group(1)
            for value in (venue, locator)
            if value is not None
        )
        lines.append(f"{author.group(1)} ({year.group(1)}). {title.group(1)}. {suffix}.")
    return lines


def build_pdf(pdf_path: Path, manuscript: dict[str, Any], contract: dict[str, Any]) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer

    styles = _styles()
    story: list[Any] = []
    for index, page in enumerate(manuscript["pages"]):
        if index == 0:
            story.extend([
                Spacer(1, 10),
                Paragraph(manuscript["title"], styles["title"]),
                Paragraph(f"{manuscript['author']}<br/>{manuscript['affiliation']}", styles["author"]),
            ])
        story.append(Paragraph(page["heading"], styles["heading"]))
        for paragraph in page["paragraphs"]:
            style = styles["status"] if paragraph == EXACT_STATUS_SENTENCE else styles["body"]
            story.append(Paragraph(render_citations(paragraph), style))
        if index == 2:
            story.append(table_one())
            story.append(Paragraph("Table 1. Planned design only. This table contains no measured values.", styles["caption"]))
        if index == 3:
            story.append(flow_figure(["Request 1\n(one tool schema)", "Recorded tool trace", "Terminal request\n(no tools)"]))
            story.append(Paragraph("Figure 1. Planned two-request topology. The terminal request has no tool declaration.", styles["caption"]))
        if index == 4:
            story.append(flow_figure(["Frozen protocol", "Personal GO", "Preflight and calibration", "Journal and replay"]))
            story.append(Paragraph("Figure 2. Planned authority and evidence flow. Each gate precedes the next stage.", styles["caption"]))
        story.append(PageBreak())
    story.append(Paragraph("Appendix A. Frozen design provenance", styles["heading"]))
    for paragraph in manuscript["appendix"]:
        story.append(Paragraph(paragraph, styles["body"]))
    story.append(Paragraph("Bound identifiers", styles["heading"]))
    for line in provenance_lines(contract):
        story.append(Paragraph(line, styles["small"]))
    story.append(Paragraph("References", styles["heading"]))
    for line in bibliography_lines(PAPER_DIRECTORY / "references.bib", manuscript["citations"]):
        story.append(Paragraph(line, styles["reference"]))

    def decorate(canvas: Canvas, _document: BaseDocTemplate) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 7.2)
        width = stringWidth(REQUIRED_BANNER, "Helvetica-Bold", 7.2)
        x = (letter[0] - width) / 2
        canvas.setStrokeColor(colors.HexColor("#8B1E1E"))
        canvas.setFillColor(colors.HexColor("#FFF4F4"))
        canvas.roundRect(x - 7, letter[1] - 35, width + 14, 15, 3, stroke=1, fill=1)
        canvas.setFillColor(colors.HexColor("#8B1E1E"))
        canvas.drawString(x, letter[1] - 30, REQUIRED_BANNER)
        canvas.setStrokeColor(colors.HexColor("#A0A0A0"))
        canvas.line(54, 35, letter[0] - 54, 35)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.setFont("Helvetica", 7.5)
        canvas.drawString(54, 21, "Anachron v3 preliminary manuscript")
        canvas.drawRightString(letter[0] - 54, 21, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    left = 0.72 * 72
    bottom = 0.56 * 72
    frame = Frame(left, bottom, letter[0] - 2 * left, letter[1] - bottom - 0.92 * 72, id="body")
    document = BaseDocTemplate(
        str(pdf_path), pagesize=letter, leftMargin=left, rightMargin=left,
        topMargin=0.92 * 72, bottomMargin=bottom, title=manuscript["title"],
        author=manuscript["author"], invariant=1,
    )
    document.addPageTemplates(PageTemplate(id="preliminary", frames=[frame], onPageEnd=decorate))
    document.build(story)


def _contains_rectangle(outer, inner, *, tolerance: float = 0.75) -> bool:
    return (
        outer.x0 <= inner.x0 + tolerance
        and outer.y0 <= inner.y0 + tolerance
        and outer.x1 + tolerance >= inner.x1
        and outer.y1 + tolerance >= inner.y1
    )


def _is_banner_red(color: tuple[float, float, float] | None) -> bool:
    return color is not None and color[0] >= 0.45 and color[1] <= 0.2 and color[2] <= 0.2


def _is_banner_fill(color: tuple[float, float, float] | None) -> bool:
    return color is not None and color[0] >= 0.95 and 0.9 <= color[1] <= 0.99 and 0.9 <= color[2] <= 0.99


def _has_complete_banner_border(drawings: list[dict[str, Any]], background) -> bool:
    for drawing in drawings:
        if _is_banner_red(drawing["color"]) and _contains_rectangle(drawing["rect"], background):
            return True
    horizontal_edges = [False, False]
    vertical_edges = [False, False]
    for drawing in drawings:
        if not _is_banner_red(drawing["color"]):
            continue
        rectangle = drawing["rect"]
        if rectangle.x0 <= background.x0 + 1 and rectangle.x1 + 1 >= background.x1:
            if abs(rectangle.y0 - background.y0) <= 1:
                horizontal_edges[0] = True
            if abs(rectangle.y1 - background.y1) <= 1:
                horizontal_edges[1] = True
        if rectangle.y0 <= background.y0 + 1 and rectangle.y1 + 1 >= background.y1:
            if abs(rectangle.x0 - background.x0) <= 1:
                vertical_edges[0] = True
            if abs(rectangle.x1 - background.x1) <= 1:
                vertical_edges[1] = True
    return all(horizontal_edges + vertical_edges)


def _red_pixels_in_rectangle(pixmap, rectangle, page_rectangle) -> int:
    x_scale = pixmap.width / page_rectangle.width
    y_scale = pixmap.height / page_rectangle.height
    x0 = max(0, int(rectangle.x0 * x_scale))
    y0 = max(0, int(rectangle.y0 * y_scale))
    x1 = min(pixmap.width, int(rectangle.x1 * x_scale) + 1)
    y1 = min(pixmap.height, int(rectangle.y1 * y_scale) + 1)
    samples = memoryview(pixmap.samples)
    count = 0
    for y in range(y0, y1):
        offset = (y * pixmap.width + x0) * pixmap.n
        for _ in range(x0, x1):
            if samples[offset] >= 110 and samples[offset + 1] <= 100 and samples[offset + 2] <= 100:
                count += 1
            offset += pixmap.n
    return count


def verify_pdf(pdf_path: Path, render_directory: Path) -> dict[str, Any]:
    import fitz
    import pdfplumber

    with pdfplumber.open(pdf_path) as document:
        text_by_page = [page.extract_text() or "" for page in document.pages]
        word_boxes_by_page = [page.extract_words() for page in document.pages]
    if len(text_by_page) != 7:
        raise PreliminaryPaperError(f"preliminary PDF must contain exactly seven pages, found {len(text_by_page)}")
    if any(REQUIRED_BANNER not in text for text in text_by_page):
        raise PreliminaryPaperError("preliminary banner is not extractable on every page")
    if "Appendix A." in "\n".join(text_by_page[:6]) or "Appendix A." not in text_by_page[6]:
        raise PreliminaryPaperError("appendix must begin on page 7")
    required = ("Table 1.", "Figure 1.", "Figure 2.", EXACT_STATUS_SENTENCE)
    if any(token not in "\n".join(text_by_page) for token in required):
        raise PreliminaryPaperError("preliminary PDF lacks a required visible design artifact")
    if len(re.findall(r"(?m)^References$", text_by_page[6])) != 1:
        raise PreliminaryPaperError("preliminary PDF must contain exactly one References heading on page 7")
    if render_directory.exists():
        shutil.rmtree(render_directory)
    render_directory.mkdir(parents=True)
    document = fitz.open(pdf_path)
    rendered: list[str] = []
    red_pixels: list[int] = []
    banner_glyph_pixels: list[list[int]] = []
    banner_rectangles: list[dict[str, Any]] = []
    try:
        for index in range(document.page_count):
            page = document.load_page(index)
            phrase_rectangles = page.search_for(REQUIRED_BANNER)
            if len(phrase_rectangles) != 1:
                raise PreliminaryPaperError(
                    f"preliminary banner phrase must be one complete rectangle on page {index + 1}"
                )
            phrase_rectangle = phrase_rectangles[0]
            if not _contains_rectangle(page.rect, phrase_rectangle, tolerance=0):
                raise PreliminaryPaperError(f"preliminary banner phrase exceeds the page on page {index + 1}")
            if phrase_rectangle.y0 < 0 or phrase_rectangle.y1 > BANNER_TOP_BAND_HEIGHT:
                raise PreliminaryPaperError(f"preliminary banner phrase escapes the reserved top band on page {index + 1}")
            drawings = page.get_drawings()
            backgrounds = [
                drawing["rect"]
                for drawing in drawings
                if _is_banner_fill(drawing["fill"])
                and drawing["rect"].y0 >= 0
                and drawing["rect"].y1 <= BANNER_TOP_BAND_HEIGHT
                and _contains_rectangle(drawing["rect"], phrase_rectangle)
            ]
            if len(backgrounds) != 1:
                raise PreliminaryPaperError(
                    f"preliminary banner lacks one containing background rectangle on page {index + 1}"
                )
            background = backgrounds[0]
            if not _has_complete_banner_border(drawings, background):
                raise PreliminaryPaperError(f"preliminary banner border is incomplete on page {index + 1}")
            pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            top_rows = int(pixmap.height * 0.08)
            limit = pixmap.width * top_rows * pixmap.n
            stride = pixmap.n * 4
            count = sum(
                red >= 110 and green <= 100 and blue <= 100
                for red, green, blue in zip(
                    pixmap.samples[0:limit:stride],
                    pixmap.samples[1:limit:stride],
                    pixmap.samples[2:limit:stride],
                )
            )
            if count < 1:
                raise PreliminaryPaperError(f"preliminary banner is not raster-visible on page {index + 1}")
            phrase_pixels = _red_pixels_in_rectangle(pixmap, phrase_rectangle, page.rect)
            if phrase_pixels < 20:
                raise PreliminaryPaperError(
                    f"preliminary banner phrase lacks visible red glyphs on page {index + 1}"
                )
            word_boxes = word_boxes_by_page[index]
            banner_pixels: list[int] = []
            for word in dict.fromkeys(BANNER_WORDS):
                candidates = [
                    item
                    for item in word_boxes
                    if item["text"] == word
                    and 0 <= item["x0"] < item["x1"] <= page.rect.width
                    and 0 <= item["top"] < item["bottom"] <= BANNER_TOP_BAND_HEIGHT
                ]
                expected_count = BANNER_WORDS.count(word)
                if len(candidates) != expected_count:
                    raise PreliminaryPaperError(
                        f"banner word {word!r} lacks bounded top-band glyph boxes on page {index + 1}"
                    )
                for box in candidates:
                    glyph_count = _red_pixels_in_rectangle(
                        pixmap,
                        fitz.Rect(box["x0"], box["top"], box["x1"], box["bottom"]),
                        page.rect,
                    )
                    if glyph_count < 1:
                        raise PreliminaryPaperError(
                            f"banner word {word!r} has no raster-visible glyphs on page {index + 1}"
                        )
                    banner_pixels.append(glyph_count)
            output = render_directory / f"page-{index + 1:02d}.png"
            pixmap.save(output)
            rendered.append(output.name)
            red_pixels.append(count)
            banner_glyph_pixels.append(banner_pixels)
            banner_rectangles.append(
                {
                    "background": [round(value, 3) for value in background],
                    "intact": True,
                    "phrase": [round(value, 3) for value in phrase_rectangle],
                    "visible_red_phrase_pixels": phrase_pixels,
                }
            )
    finally:
        document.close()
    return {
        "banner_glyph_pixels": banner_glyph_pixels,
        "banner_rectangles": banner_rectangles,
        "page_count": 7,
        "red_banner_pixels": red_pixels,
        "references_heading_count": len(re.findall(r"(?m)^References$", text_by_page[6])),
        "rendered_pages": rendered,
    }


def build_tex(manuscript: dict[str, Any], contract: dict[str, Any]) -> str:
    references = "\n".join(
        rf"\bibitem{{{key}}} {tex_escape(line)}"
        for key, line in zip(manuscript["citations"], bibliography_lines(PAPER_DIRECTORY / "references.bib", manuscript["citations"]))
    )
    body_pages = []
    for index, page in enumerate(manuscript["pages"]):
        paragraphs = "\n\n".join(
            render_tex_citations(paragraph)
            for paragraph in page["paragraphs"]
        )
        extra = ""
        if index == 2:
            extra = r"\begin{center}\fbox{\parbox{0.85\linewidth}{\textbf{Table 1. Planned design only.} 336 planned trajectories: 264 primary and 72 disclosed development. Two local Qwen builds; two scientific requests per trajectory; two excluded planned calibration requests.}}\end{center}"
        if index == 3:
            extra = r"\begin{center}\fbox{Request 1 (one tool schema) $\rightarrow$ recorded trace $\rightarrow$ terminal request (no tools)}\\\small Figure 1. Planned two-request topology.\end{center}"
        if index == 4:
            extra = r"\begin{center}\fbox{Frozen protocol $\rightarrow$ Personal GO $\rightarrow$ Preflight and calibration $\rightarrow$ Journal and replay}\\\small Figure 2. Planned authority and evidence flow.\end{center}"
        body_pages.append(
            "\n".join(
                [
                    rf"\section*{{{tex_escape(page['heading'])}}}",
                    paragraphs,
                    extra,
                    r"\newpage",
                ]
            )
        )
    appendix = "\n\n".join(tex_escape(item) for item in manuscript["appendix"])
    provenance = (r"\\" + "\n").join(
        tex_escape(item) for item in provenance_lines(contract)
    )
    return "\n".join([
        r"\documentclass[10pt]{article}",
        r"\usepackage[margin=0.78in,headheight=18pt,headsep=12pt]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{xcolor}",
        r"\usepackage{fancyhdr}",
        r"\newcommand{\PreliminaryBanner}{\fcolorbox{red!60!black}{red!4}{\textcolor{red!60!black}{\textbf{PRE-FULL-RESULTS MANUSCRIPT - NO EMPIRICAL CLAIMS - NOT FOR SUBMISSION}}}}",
        r"\newcommand{\PreliminaryHeader}{\makebox[0pt][c]{\PreliminaryBanner}}",
        r"\pagestyle{fancy}\fancyhf{}\fancyhead[C]{\PreliminaryHeader}\fancyfoot[L]{Anachron v3 preliminary manuscript}\fancyfoot[R]{Page \thepage}\renewcommand{\headrulewidth}{0pt}",
        r"\fancypagestyle{plain}{\fancyhf{}\fancyhead[C]{\PreliminaryHeader}\fancyfoot[L]{Anachron v3 preliminary manuscript}\fancyfoot[R]{Page \thepage}\renewcommand{\headrulewidth}{0pt}}",
        r"\begin{document}",
        rf"\begin{{center}}{{\Large\textbf{{{tex_escape(manuscript['title'])}}}}}\\{tex_escape(manuscript['author'])}\\{tex_escape(manuscript['affiliation'])}\end{{center}}",
        "\n".join(body_pages),
        r"\section*{Appendix A. Frozen design provenance}",
        appendix,
        r"\subsection*{Bound identifiers}",
        provenance,
        r"\begin{thebibliography}{9}",
        references,
        r"\end{thebibliography}",
        r"\end{document}",
        "",
    ])


def write_preview_tree(root: Path, manuscript: dict[str, Any], contract: dict[str, Any]) -> Path:
    tree = root / "preview_source"
    tree.mkdir()
    (tree / "main.tex").write_text(build_tex(manuscript, contract), encoding="utf-8", newline="\n")
    shutil.copyfile(PAPER_DIRECTORY / "references.bib", tree / "references.bib")
    (tree / "README.txt").write_text(
        "PRE-FULL-RESULTS preview source only. NOT FOR SUBMISSION. This tree contains no experiment evidence, receipt, GO, review, or final manuscript.\n",
        encoding="ascii",
        newline="\n",
    )
    names = tuple(sorted(path.name for path in tree.iterdir()))
    if names != PREVIEW_TREE_FILES:
        raise PreliminaryPaperError("preview TeX tree violates the archive allowlist")
    return tree


def deterministic_archive(tree: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in PREVIEW_TREE_FILES:
            source = tree / name
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, source.read_bytes())
    with zipfile.ZipFile(output) as archive:
        if tuple(sorted(archive.namelist())) != PREVIEW_TREE_FILES:
            raise PreliminaryPaperError("preview archive violates the allowlist")


def verify_tectonic(tectonic: Path) -> None:
    if not tectonic.is_file():
        raise PreliminaryPaperError(f"Tectonic executable not found: {tectonic}")
    actual = sha256_path(tectonic)
    if actual != EXPECTED_TECTONIC_SHA256:
        raise PreliminaryPaperError(f"Tectonic SHA-256 mismatch: expected {EXPECTED_TECTONIC_SHA256}, found {actual}")


def compile_preview_tex(tectonic: Path, tree: Path, root: Path) -> dict[str, Any]:
    output = root / "tectonic"
    output.mkdir()
    for pass_number in range(2):
        completed = subprocess.run(
            [str(tectonic), "-Z", "deterministic-mode", "--outdir", str(output), "main.tex"],
            cwd=tree,
            capture_output=True,
            text=True,
            check=False,
        )
        if completed.returncode:
            raise PreliminaryPaperError(
                f"Tectonic compilation pass {pass_number + 1} failed: {completed.stderr.strip()}"
            )
    pdf = output / "main.pdf"
    if not pdf.is_file():
        raise PreliminaryPaperError("Tectonic did not produce main.pdf")
    return {"compiled_pdf_sha256": sha256_path(pdf), "verification": verify_pdf(pdf, output / "render")}


def validate_preliminary_inputs(repository: Path, tectonic: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    contract = validate_contract(repository)
    manuscript = validate_manuscript(repository)
    verify_tectonic(tectonic)
    return contract, manuscript


def build_once(
    repository: Path,
    tectonic: Path,
    root: Path,
    contract: dict[str, Any],
    manuscript: dict[str, Any],
    *,
    verify_outputs: bool,
) -> dict[str, Any]:
    pdf = root / "anachron_v3_preliminary.pdf"
    build_pdf(pdf, manuscript, contract)
    pdf_verification = verify_pdf(pdf, root / "render") if verify_outputs else None
    tree = write_preview_tree(root, manuscript, contract)
    archive = root / "preview_source.zip"
    deterministic_archive(tree, archive)
    tex_verification = compile_preview_tex(tectonic, tree, root) if verify_outputs else None
    return {
        "archive_sha256": sha256_path(archive),
        "pdf_sha256": sha256_path(pdf),
        "pdf_verification": pdf_verification,
        "source_files": list(PREVIEW_TREE_FILES),
        "tex_sha256": sha256_path(tree / "main.tex"),
        "tex_verification": tex_verification,
    }


def ensure_build_directory(repository: Path) -> Path:
    output = repository / "paper/v3_measurement/build"
    if output.resolve() != BUILD_DIRECTORY.resolve():
        raise PreliminaryPaperError("refusing to write outside paper/v3_measurement/build")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    return output


def build_preliminary(repository: Path, tectonic: Path) -> dict[str, Any]:
    repository = repository.resolve()
    contract, manuscript = validate_preliminary_inputs(repository, tectonic.resolve())
    with tempfile.TemporaryDirectory(prefix="anachron-v3-preliminary-") as first_root, tempfile.TemporaryDirectory(prefix="anachron-v3-preliminary-") as second_root:
        first = build_once(
            repository, tectonic.resolve(), Path(first_root), contract, manuscript, verify_outputs=True
        )
        second = build_once(
            repository, tectonic.resolve(), Path(second_root), contract, manuscript, verify_outputs=False
        )
        comparable = ("archive_sha256", "pdf_sha256", "tex_sha256")
        if any(first[field] != second[field] for field in comparable):
            raise PreliminaryPaperError("fresh preliminary builds are not byte-deterministic")
        output = ensure_build_directory(repository)
        shutil.copyfile(Path(first_root) / "anachron_v3_preliminary.pdf", output / "anachron_v3_preliminary.pdf")
        shutil.copytree(Path(first_root) / "preview_source", output / "preview_source")
        shutil.copyfile(Path(first_root) / "preview_source.zip", output / "preview_source.zip")
        receipt = {
            "contract": contract,
            "deterministic_double_build": True,
            "preview": first,
            "schema_version": "anachron-v3-preliminary-paper-receipt-v1",
            "state": "preliminary",
        }
        (output / "build_receipt.json").write_bytes(canonical_bytes(receipt))
    return receipt


def reject_final_state_arguments(arguments: list[str]) -> None:
    for argument in arguments:
        if any(argument == prefix or argument.startswith(prefix + "=") for prefix in FORBIDDEN_ARGUMENT_PREFIXES):
            raise PreliminaryPaperError(f"preliminary builder rejects final-state input: {argument}")


def main(argv: list[str] | None = None) -> int:
    arguments = list(argv) if argv is not None else None
    reject_final_state_arguments(arguments if arguments is not None else __import__("sys").argv[1:])
    parser = argparse.ArgumentParser(description="Build the Anachron v3 preliminary-only paper preview.")
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--state", choices=("preliminary",), default="preliminary")
    parser.add_argument("--tectonic", type=Path, required=True)
    args = parser.parse_args(arguments)
    if args.state != "preliminary":
        raise PreliminaryPaperError("preliminary builder refuses non-preliminary state")
    print(json.dumps(build_preliminary(args.repository, args.tectonic), ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
