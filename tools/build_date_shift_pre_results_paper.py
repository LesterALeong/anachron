"""Build and verify the date-shift pre-results manuscript preview."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIRECTORY = REPOSITORY_ROOT / "paper" / "date_shift"
REFERENCES_PATH = PAPER_DIRECTORY / "references.bib"
REQUIRED_BANNER = "PRE-RESULTS MANUSCRIPT - NOT FOR SUBMISSION"
RASTER_SCALE = 1.5
TOP_BANNER_FRACTION = 0.08
MIN_RED_BANNER_PIXELS = 300
RAW_SOURCE_OVERLAP_NORMALIZED_CHARS = 80
TEX_MODEL_LINES_TOKEN = "@@DATE_SHIFT_MODEL_LINES@@"
TECTONIC_VERSION = "0.17.0"
TECTONIC_WINDOWS_ARCHIVE_SHA256 = "sha256:f61ce51f0b0ade1015b7de7ef368541c5424e9756ecbd0d7af97d6d48030845f"
EXPECTED_TECTONIC_EXECUTABLE_SHA256 = "sha256:99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d"
ALLOWED_CITATION_KEYS = {
    "chiang-lee-2024-metadata",
    "ding-etal-2026-temporal-critique",
    "dhingra-etal-2022-time",
    "el-lahib-etal-2026-temporal",
    "jang-etal-2022-temporalwiki",
    "liu-etal-2026-exante",
    "wallat-etal-2026-facts",
}
FORBIDDEN_PLACEHOLDER = re.compile(r"\b(?:TODO|TBD|INSERT|XXX)\b", re.IGNORECASE)
FORBIDDEN_OUTCOME_CLAIM = re.compile(
    r"\b(?:we find|we found|we observe|we observed|results show|our results|"
    r"significant effect|statistically significant|model outcomes show|"
    r"models? (?:accepted|answered|refused|leaked))\b",
    re.IGNORECASE,
)
FORBIDDEN_REQUEST = re.compile(
    r"\b(?:please|kindly|now)\s+(?:archive|upload|submit|contact|endorse)\b",
    re.IGNORECASE,
)
INLINE_CITATION = re.compile(r"\[\[cite:([a-z0-9-]+)\]\]")


class PreResultsPaperError(ValueError):
    """Raised when a preview source violates its no-results boundary."""


def canonical_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha256_bytes(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def load_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PreResultsPaperError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict) or canonical_bytes(value) != raw:
        raise PreResultsPaperError(f"JSON is not canonical: {path}")
    return value


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_bytes())
    except json.JSONDecodeError as error:
        raise PreResultsPaperError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise PreResultsPaperError(f"JSON root is not an object: {path}")
    return value


def input_summary(repository: Path) -> dict[str, Any]:
    frame = load_json(repository / "research/date-shift/proposed_frame.json")
    items = load_json(repository / "research/date-shift/proposed_items.json")
    plan = load_json(repository / "research/date-shift/execution_plan.json")
    candidates = frame.get("candidates")
    proposed_items = items.get("proposed_items")
    models = plan.get("models")
    if not all(isinstance(value, list) for value in (candidates, proposed_items, models)):
        raise PreResultsPaperError("date-shift canonical inputs have an unexpected schema")
    candidate_count = len(candidates)
    proposed_count = len(proposed_items)
    excluded_count = sum(row.get("status") == "excluded" for row in candidates)
    if (candidate_count, proposed_count, excluded_count) != (60, 54, 6):
        raise PreResultsPaperError("date-shift canonical cohort counts drifted")
    decoding = plan.get("decoding")
    analysis = plan.get("analysis")
    if not isinstance(decoding, dict) or not isinstance(analysis, dict):
        raise PreResultsPaperError("date-shift execution plan has an unexpected schema")
    model_rows = []
    for model in models:
        if not isinstance(model, dict) or not isinstance(model.get("id"), str) or not isinstance(
            model.get("digest"), str
        ):
            raise PreResultsPaperError("date-shift model row has an unexpected schema")
        model_rows.append({"id": model["id"], "digest": model["digest"]})
    if len(model_rows) != 2 or plan.get("think") is not False:
        raise PreResultsPaperError("date-shift model plan drifted")
    return {
        "candidate_count": candidate_count,
        "proposed_count": proposed_count,
        "excluded_count": excluded_count,
        "model_count": len(model_rows),
        "maximum_scientific_calls": proposed_count * len(model_rows) * 2,
        "models": model_rows,
        "temperature": decoding.get("temperature"),
        "seed": decoding.get("seed"),
        "context_tokens": decoding.get("num_ctx"),
        "prediction_limit": decoding.get("num_predict"),
        "bootstrap_replicates": analysis.get("bootstrap_replicates"),
        "bootstrap_seed": analysis.get("bootstrap_seed"),
        "input_hashes": {
            "proposed_frame": sha256_path(repository / "research/date-shift/proposed_frame.json"),
            "proposed_items": sha256_path(repository / "research/date-shift/proposed_items.json"),
            "execution_plan": sha256_path(repository / "research/date-shift/execution_plan.json"),
        },
    }


def _protected_source_strings(repository: Path) -> tuple[str, ...]:
    items = load_json(repository / "research/date-shift/proposed_items.json")
    protected: list[str] = []
    for item in items["proposed_items"]:
        for field in ("document_content", "audit_evidence"):
            value = item.get(field)
            if isinstance(value, dict):
                for candidate in value.values():
                    if isinstance(candidate, dict) and isinstance(candidate.get("text"), str):
                        protected.append(candidate["text"])
                    if isinstance(candidate, str):
                        protected.append(candidate)
    return tuple(dict.fromkeys(protected))


def normalize_overlap_text(text: str) -> str:
    """Normalize only case and whitespace before checking source-excerpt overlap."""
    return " ".join(unicodedata.normalize("NFC", text).casefold().split())


def has_protected_source_overlap(body: str, sources: tuple[str, ...]) -> bool:
    """Reject an 80-character contiguous overlap, long enough to avoid stock short phrases."""
    normalized_body = normalize_overlap_text(body)
    if len(normalized_body) < RAW_SOURCE_OVERLAP_NORMALIZED_CHARS:
        return False
    body_windows = {
        normalized_body[index : index + RAW_SOURCE_OVERLAP_NORMALIZED_CHARS]
        for index in range(len(normalized_body) - RAW_SOURCE_OVERLAP_NORMALIZED_CHARS + 1)
    }
    for source in sources:
        normalized_source = normalize_overlap_text(source)
        for index in range(len(normalized_source) - RAW_SOURCE_OVERLAP_NORMALIZED_CHARS + 1):
            if normalized_source[index : index + RAW_SOURCE_OVERLAP_NORMALIZED_CHARS] in body_windows:
                return True
    return False


def _citation_keys_from_bib(references: Path) -> set[str]:
    return set(re.findall(r"^@[A-Za-z]+\{([^,]+),", references.read_text(encoding="utf-8"), re.MULTILINE))


def bibliography_records(references: Path) -> dict[str, dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    source = references.read_text(encoding="utf-8")
    for match in re.finditer(r"^@[A-Za-z]+\{([^,]+),(.*?)(?=^@|\Z)", source, re.MULTILINE | re.DOTALL):
        key, entry = match.groups()
        fields = {
            field: re.sub(r"[{}]", "", value).replace("\\", "")
            for field, value in re.findall(
                r"^\s*(author|title|year)\s*=\s*\{(.*?)\},?\s*$", entry, re.MULTILINE
            )
        }
        records[key] = fields
    return records


def citation_label(fields: dict[str, str]) -> str:
    authors = fields["author"].split(" and ")
    families = [author.split(",")[0] for author in authors]
    if len(families) == 1:
        names = families[0]
    elif len(families) == 2:
        names = " and ".join(families)
    else:
        names = f"{families[0]} et al."
    return f"{names}, {fields['year']}"


def citation_labels(references: Path, citations: list[str]) -> dict[str, str]:
    records = bibliography_records(references)
    labels = {}
    for key in citations:
        fields = records.get(key, {})
        if {"author", "title", "year"} - set(fields):
            raise PreResultsPaperError(f"bibliography entry is incomplete: {key}")
        labels[key] = citation_label(fields)
    return labels


def bibliography_lines(references: Path, citations: list[str]) -> list[str]:
    records = bibliography_records(references)
    lines = []
    for key in citations:
        fields = records.get(key, {})
        if {"author", "title", "year"} - set(fields):
            raise PreResultsPaperError(f"bibliography entry is incomplete: {key}")
        author = fields["author"].split(" and ")[0]
        if " and " in fields["author"]:
            author += " et al."
        lines.append(f"{author} ({fields['year']}). {fields['title']}.")
    return lines


def validate_manuscript(manuscript: dict[str, Any], repository: Path) -> dict[str, Any]:
    if manuscript.get("schema_version") != "date-shift-pre-results-manuscript-v1":
        raise PreResultsPaperError("unexpected manuscript schema version")
    if manuscript.get("banner") != REQUIRED_BANNER:
        raise PreResultsPaperError("manuscript banner drifted")
    citations = manuscript.get("citations")
    sections = manuscript.get("sections")
    if not isinstance(citations, list) or not isinstance(sections, list) or not all(
        isinstance(citation, str) for citation in citations
    ):
        raise PreResultsPaperError("manuscript citations or sections have an unexpected schema")
    if set(citations) - ALLOWED_CITATION_KEYS:
        raise PreResultsPaperError("manuscript contains an unsupported citation")
    if set(citations) - _citation_keys_from_bib(repository / "paper/date_shift/references.bib"):
        raise PreResultsPaperError("manuscript cites a work absent from references.bib")
    headings: list[str] = []
    paragraphs: list[str] = []
    inline_citations: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            raise PreResultsPaperError("manuscript section has an unexpected schema")
        heading, text = section.get("heading"), section.get("text")
        if not isinstance(heading, str) or not isinstance(text, str):
            raise PreResultsPaperError("manuscript section is missing text")
        headings.append(heading)
        paragraphs.append(text)
        markers = re.findall(r"\[\[cite:.*?\]\]", text)
        keys = INLINE_CITATION.findall(text)
        if len(markers) != len(keys):
            raise PreResultsPaperError("manuscript contains a malformed inline citation")
        inline_citations.extend(keys)
    if "6. No-Results Status" not in headings:
        raise PreResultsPaperError("manuscript lacks a no-results status section")
    body = "\n".join(paragraphs)
    if set(inline_citations) - ALLOWED_CITATION_KEYS:
        raise PreResultsPaperError("manuscript contains an unsupported inline citation")
    if set(inline_citations) - _citation_keys_from_bib(repository / "paper/date_shift/references.bib"):
        raise PreResultsPaperError("manuscript contains an inline citation absent from references.bib")
    if set(citations) != set(inline_citations):
        raise PreResultsPaperError("top-level citation list does not match inline citation markers")
    if FORBIDDEN_PLACEHOLDER.search(body):
        raise PreResultsPaperError("manuscript contains a prohibited placeholder")
    if FORBIDDEN_OUTCOME_CLAIM.search(body):
        raise PreResultsPaperError("manuscript contains an empirical outcome claim")
    if FORBIDDEN_REQUEST.search(body):
        raise PreResultsPaperError("manuscript contains a prohibited external request")
    if "no scientific model call has been made" not in body.lower():
        raise PreResultsPaperError("manuscript does not state its no-results boundary")
    if has_protected_source_overlap(body, _protected_source_strings(repository)):
        raise PreResultsPaperError(
            "manuscript contains an 80-character protected raw-source overlap"
        )
    return input_summary(repository)


def render_reportlab_citations(text: str, labels: dict[str, str]) -> str:
    return INLINE_CITATION.sub(lambda match: f" ({labels[match.group(1)]})", text)


def render_tex_citations(text: str, replacements: dict[str, str] | None = None) -> str:
    chunks: list[str] = []
    position = 0
    for match in INLINE_CITATION.finditer(text):
        chunks.append(tex_escape(text[position : match.start()]))
        chunks.append(rf"\cite{{{match.group(1)}}}")
        position = match.end()
    chunks.append(tex_escape(text[position:]))
    rendered = "".join(chunks)
    for token, replacement in (replacements or {}).items():
        rendered = rendered.replace(tex_escape(token), replacement)
    return rendered


def tex_model_lines(models: list[dict[str, str]]) -> str:
    rendered = []
    for model in models:
        digest = model["digest"]
        prefix, separator, hexadecimal = digest.partition(":")
        if not separator:
            raise PreResultsPaperError("date-shift model digest has no algorithm prefix")
        digest_chunks = [hexadecimal[index : index + 8] for index in range(0, len(hexadecimal), 8)]
        rendered_digest = f"{tex_escape(prefix)}:" + r"\allowbreak ".join(digest_chunks)
        rendered.append(rf"{tex_escape(model['id'])} (\texttt{{{rendered_digest}}})")
    return "; ".join(rendered)


def render_sections(
    manuscript: dict[str, Any], summary: dict[str, Any], labels: dict[str, str]
) -> list[tuple[str, list[str]]]:
    substitutions = {
        **summary,
        "model_lines": "; ".join(
            f"{model['id']} ({model['digest']})" for model in summary["models"]
        ),
    }
    rendered = []
    for section in manuscript["sections"]:
        text = render_reportlab_citations(section["text"].format(**substitutions), labels)
        rendered.append((section["heading"], text.split("\n\n")))
    return rendered


def word_count(rendered_sections: list[tuple[str, list[str]]]) -> int:
    return sum(
        len(re.findall(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?", paragraph))
        for _, paragraphs in rendered_sections
        for paragraph in paragraphs
    )


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


def build_tex(manuscript: dict[str, Any], summary: dict[str, Any]) -> str:
    lines = [
        r"\documentclass[11pt]{article}",
        r"\usepackage[margin=1in]{geometry}",
        r"\usepackage[T1]{fontenc}",
        r"\usepackage{lmodern}",
        r"\usepackage{microtype}",
        r"\usepackage{hyperref}",
        r"\usepackage{fancyhdr}",
        r"\usepackage{xcolor}",
        r"\hypersetup{colorlinks=true,linkcolor=blue,urlcolor=blue}",
        r"\newcommand{\DateShiftPreResultsBanner}{\fcolorbox{red!65!black}{red!4}{\textcolor{red!65!black}{\textbf{PRE-RESULTS MANUSCRIPT - NOT FOR SUBMISSION}}}}",
        r"\pagestyle{fancy}",
        r"\fancyhf{}",
        r"\fancyhead[C]{\DateShiftPreResultsBanner}",
        r"\fancyfoot[L]{Date-shift pre-results manuscript}",
        r"\fancyfoot[R]{Page \thepage}",
        r"\renewcommand{\headrulewidth}{0pt}",
        r"\renewcommand{\footrulewidth}{0.4pt}",
        r"\setlength{\headheight}{18pt}",
        r"\setlength{\emergencystretch}{3em}",
        r"\fancypagestyle{plain}{\fancyhf{}\fancyhead[C]{\DateShiftPreResultsBanner}\fancyfoot[L]{Date-shift pre-results manuscript}\fancyfoot[R]{Page \thepage}\renewcommand{\headrulewidth}{0pt}\renewcommand{\footrulewidth}{0.4pt}}",
        rf"\title{{{tex_escape(manuscript['title'])}}}",
        rf"\author{{{tex_escape(manuscript['author'])}\\{tex_escape(manuscript['affiliation'])}}}",
        r"\date{}",
        r"\begin{document}",
        r"\maketitle",
    ]
    substitutions = {
        **summary,
        "model_lines": TEX_MODEL_LINES_TOKEN,
    }
    tex_replacements = {TEX_MODEL_LINES_TOKEN: tex_model_lines(summary["models"])}
    for section in manuscript["sections"]:
        heading = section["heading"]
        paragraphs = section["text"].format(**substitutions).split("\n\n")
        if heading == "Abstract":
            lines.extend(
                [
                    r"\begin{abstract}",
                    *[render_tex_citations(item, tex_replacements) for item in paragraphs],
                    r"\end{abstract}",
                ]
            )
            continue
        lines.append(rf"\section{{{tex_escape(heading)}}}")
        lines.extend(render_tex_citations(item, tex_replacements) for item in paragraphs)
    lines.extend(
        [
            r"\bibliographystyle{plain}",
            r"\bibliography{references}",
            r"\end{document}",
        ]
    )
    return "\n\n".join(lines) + "\n"


def _reportlab_story(
    manuscript: dict[str, Any], rendered_sections: list[tuple[str, list[str]]], references: list[str]
):
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import PageBreak, Paragraph, Spacer

    styles = getSampleStyleSheet()
    title = ParagraphStyle(
        "DateShiftTitle", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=17,
        leading=20, alignment=TA_CENTER, spaceAfter=7,
    )
    author = ParagraphStyle(
        "DateShiftAuthor", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
        leading=12, alignment=TA_CENTER, spaceAfter=15,
    )
    abstract_heading = ParagraphStyle(
        "DateShiftAbstractHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10,
        leading=12, spaceBefore=0, spaceAfter=4,
    )
    heading = ParagraphStyle(
        "DateShiftHeading", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=10,
        leading=12, spaceBefore=9, spaceAfter=4,
    )
    body = ParagraphStyle(
        "DateShiftBody", parent=styles["BodyText"], fontName="Helvetica", fontSize=8.7,
        leading=11.0, spaceAfter=6,
    )
    reference = ParagraphStyle(
        "DateShiftReference", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.6,
        leading=9.4, leftIndent=10, firstLineIndent=-10, spaceAfter=3,
    )
    story = [
        Spacer(1, 0.15 * inch),
        Paragraph(manuscript["title"], title),
        Paragraph(f"{manuscript['author']}<br/>{manuscript['affiliation']}", author),
    ]
    for index, (section_heading, paragraphs) in enumerate(rendered_sections):
        style = abstract_heading if section_heading == "Abstract" else heading
        story.append(Paragraph(section_heading, style))
        for paragraph in paragraphs:
            story.append(Paragraph(paragraph, body))
        if index in {2, 4}:
            story.append(PageBreak())
        if section_heading == "6. No-Results Status":
            story.append(PageBreak())
    story.append(Paragraph("References", heading))
    story.extend(Paragraph(item, reference) for item in references)
    return story


def build_pdf(
    output: Path,
    manuscript: dict[str, Any],
    rendered_sections: list[tuple[str, list[str]]],
    references: list[str],
) -> None:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen.canvas import Canvas
    from reportlab.platypus import SimpleDocTemplate

    def decorate(canvas):
        canvas.saveState()
        canvas.setFont("Helvetica-Bold", 8)
        banner_width = stringWidth(REQUIRED_BANNER, "Helvetica-Bold", 8)
        x = (letter[0] - banner_width) / 2
        canvas.setStrokeColor(colors.HexColor("#8B1E1E"))
        canvas.setFillColor(colors.HexColor("#FFF4F4"))
        canvas.roundRect(x - 7, letter[1] - 35, banner_width + 14, 15, 3, stroke=1, fill=1)
        canvas.setFillColor(colors.HexColor("#8B1E1E"))
        canvas.drawString(x, letter[1] - 30, REQUIRED_BANNER)
        canvas.setStrokeColor(colors.HexColor("#A0A0A0"))
        canvas.line(54, 35, letter[0] - 54, 35)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(54, 22, "Date-shift pre-results manuscript")
        canvas.drawRightString(letter[0] - 54, 22, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    class OverlayCanvas(Canvas):
        """Draw page furniture after every flowable has painted its page."""

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._saved_page_states: list[dict[str, Any]] = []

        def showPage(self):
            self._saved_page_states.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            for page_state in self._saved_page_states:
                self.__dict__.update(page_state)
                decorate(self)
                Canvas.showPage(self)
            Canvas.save(self)

    document = SimpleDocTemplate(
        str(output), pagesize=letter, leftMargin=0.72 * 72, rightMargin=0.72 * 72,
        topMargin=0.63 * 72, bottomMargin=0.58 * 72,
        title=manuscript["title"], author=manuscript["author"], invariant=1,
    )
    document.build(_reportlab_story(manuscript, rendered_sections, references), canvasmaker=OverlayCanvas)


def red_banner_pixel_count(pixmap: Any) -> int:
    """Count dark-red banner pixels in the expected top-page raster region."""
    top_rows = int(pixmap.height * TOP_BANNER_FRACTION)
    samples = pixmap.samples
    return sum(
        red >= 110 and green <= 100 and blue <= 100
        for offset in range(0, pixmap.width * top_rows * pixmap.n, pixmap.n)
        for red, green, blue in [samples[offset : offset + 3]]
    )


def verify_pdf(pdf_path: Path, render_directory: Path) -> dict[str, Any]:
    import fitz
    import pdfplumber

    with pdfplumber.open(pdf_path) as document:
        page_texts = [page.extract_text() or "" for page in document.pages]
    page_count = len(page_texts)
    if not 4 <= page_count <= 6:
        raise PreResultsPaperError(f"preview PDF must have 4 to 6 pages, found {page_count}")
    if any(REQUIRED_BANNER not in text for text in page_texts):
        raise PreResultsPaperError("preview PDF banner is not extractable on every page")
    if not any("6. No-Results Status" in text for text in page_texts):
        raise PreResultsPaperError("preview PDF lacks a visible no-results status section")
    if "References" not in page_texts[-1]:
        raise PreResultsPaperError("preview PDF lacks a visible references section")
    if render_directory.exists():
        shutil.rmtree(render_directory)
    render_directory.mkdir(parents=True)
    document = fitz.open(pdf_path)
    rendered = []
    red_pixels_by_page = []
    try:
        for index in range(page_count):
            path = render_directory / f"page-{index + 1:02d}.png"
            pixmap = document.load_page(index).get_pixmap(
                matrix=fitz.Matrix(RASTER_SCALE, RASTER_SCALE), alpha=False
            )
            red_pixels = red_banner_pixel_count(pixmap)
            if red_pixels < MIN_RED_BANNER_PIXELS:
                raise PreResultsPaperError(
                    f"preview PDF banner is not raster-visible on page {index + 1}"
                )
            pixmap.save(path)
            rendered.append(path)
            red_pixels_by_page.append(red_pixels)
    finally:
        document.close()
    return {
        "page_count": page_count,
        "banner_verified_every_page": True,
        "raster_banner_red_pixels_by_page": red_pixels_by_page,
        "rendered_pages": [path.name for path in rendered],
    }


def ensure_build_directory(build_directory: Path) -> None:
    expected = (PAPER_DIRECTORY / "build").resolve()
    if build_directory.resolve() != expected:
        raise PreResultsPaperError("refusing to write outside paper/date_shift/build")
    if build_directory.exists():
        shutil.rmtree(build_directory)
    build_directory.mkdir(parents=True)


def write_preview_tree(build_directory: Path, tex: str) -> Path:
    tree = build_directory / "arxiv_source"
    tree.mkdir()
    (tree / "main.tex").write_text(tex, encoding="utf-8", newline="\n")
    shutil.copyfile(REFERENCES_PATH, tree / "references.bib")
    (tree / "README.txt").write_text(
        "Pre-results preview source only. Not for submission. Compile main.tex with BibTeX after final scientific review.\n",
        encoding="ascii",
        newline="\n",
    )
    return tree


def verify_tex_source(
    source_tree: Path, tectonic: Path, *, build_directory: Path | None = None
) -> dict[str, Any]:
    """Compile the generated TeX only when the caller explicitly supplies Tectonic."""
    if not tectonic.is_file():
        raise FileNotFoundError(f"Tectonic executable not found: {tectonic}")
    executable_sha256 = sha256_path(tectonic)
    if executable_sha256 != EXPECTED_TECTONIC_EXECUTABLE_SHA256:
        raise PreResultsPaperError(
            "Tectonic executable SHA-256 mismatch: "
            f"expected {EXPECTED_TECTONIC_EXECUTABLE_SHA256}, found {executable_sha256}"
        )
    root = (build_directory or PAPER_DIRECTORY / "build").resolve()
    expected_source = (root / "arxiv_source").resolve()
    if source_tree.resolve() != expected_source:
        raise PreResultsPaperError("Tectonic source must be paper/date_shift/build/arxiv_source")
    output = (root / "tex_verification").resolve()
    if output.parent != root:
        raise PreResultsPaperError("Tectonic verification output escapes paper/date_shift/build")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir()
    completed = subprocess.run(
        [
            str(tectonic),
            "-Z",
            "deterministic-mode",
            "--keep-logs",
            "--keep-intermediates",
            "--outdir",
            str(output),
            str(expected_source / "main.tex"),
        ],
        cwd=expected_source,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        raise PreResultsPaperError(
            f"Tectonic failed with exit code {completed.returncode}: {completed.stderr.strip()}"
        )
    pdf = output / "main.pdf"
    if not pdf.is_file():
        raise PreResultsPaperError("Tectonic did not produce main.pdf")
    verification = verify_pdf(pdf, output / "render")
    return {
        "tectonic_version": TECTONIC_VERSION,
        "tectonic_executable_sha256": executable_sha256,
        "compiled_pdf_sha256": sha256_path(pdf),
        "pdf_verification": verification,
    }


def build(
    repository: Path, *, verify: bool, tectonic: Path | None = None
) -> dict[str, Any]:
    repository = repository.resolve()
    manuscript_path = repository / "paper/date_shift/manuscript.json"
    manuscript = load_canonical_json(manuscript_path)
    summary = validate_manuscript(manuscript, repository)
    labels = citation_labels(repository / "paper/date_shift/references.bib", manuscript["citations"])
    rendered_sections = render_sections(manuscript, summary, labels)
    references = bibliography_lines(repository / "paper/date_shift/references.bib", manuscript["citations"])
    count = word_count(rendered_sections)
    if not 1800 <= count <= 2400:
        raise PreResultsPaperError(f"preview prose must contain 1,800 to 2,400 words, found {count}")
    build_directory = repository / "paper/date_shift/build"
    ensure_build_directory(build_directory)
    tex = build_tex(manuscript, summary)
    tree = write_preview_tree(build_directory, tex)
    pdf = build_directory / "date_shift_pre_results.pdf"
    build_pdf(pdf, manuscript, rendered_sections, references)
    verification = verify_pdf(pdf, build_directory / "render") if verify else {}
    tex_compile_verification = (
        verify_tex_source(tree, tectonic.resolve(), build_directory=build_directory)
        if tectonic is not None
        else None
    )
    receipt = {
        "schema_version": "date-shift-pre-results-preview-receipt-v1",
        "manuscript_sha256": sha256_path(manuscript_path),
        "references_sha256": sha256_path(repository / "paper/date_shift/references.bib"),
        "input_summary": summary,
        "word_count": count,
        "pdf_sha256": sha256_path(pdf),
        "tex_sha256": sha256_path(tree / "main.tex"),
        "verification": verification,
        "tex_compile_verification": tex_compile_verification,
    }
    (build_directory / "build_receipt.json").write_bytes(canonical_bytes(receipt))
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the date-shift pre-results PDF preview.")
    parser.add_argument("--repository", type=Path, default=REPOSITORY_ROOT)
    parser.add_argument("--verify", action="store_true", help="verify PDF text and render key pages")
    parser.add_argument(
        "--tectonic",
        type=Path,
        help="explicit local Tectonic executable for isolated TeX compilation verification",
    )
    args = parser.parse_args(argv)
    receipt = build(args.repository, verify=args.verify, tectonic=args.tectonic)
    print(json.dumps(receipt, ensure_ascii=True, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
