"""Build a deterministic, outcome-neutral Anachron v3 candidate paper."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile
from fractions import Fraction
from pathlib import Path
from typing import Any

from tools.v3_candidate_common import (
    admitted_snapshot,
    answer_free_rows,
    build_projection,
    canonical_json,
    require_create_only_output,
    sha256_path,
    validate_candidate_contract,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIRECTORY = REPOSITORY_ROOT / "paper" / "v3_measurement"
EXPECTED_TECTONIC_SHA256 = (
    "99ffcfdbf1ebf8bdda9e791942e3d06aedb12463fddc33f07de6f5211c8bf08d"
    if os.name == "nt"
    else "2b3a86250906c92ed0a3ae8aaa454ec55bd6cede8593b3e549640177f6aecaa3"
)
ARCHIVE_FILES = ("README.md", "figures/primary_tclr.tex", "main.tex", "references.bib")
CANDIDATE_COMPLETION = (
    "source",
    "source.zip",
    "candidate.pdf",
    "projection.json",
    "paper_source_manifest.json",
    "arxiv_metadata.json",
    "qa_renders",
    "candidate_receipt.json",
)
INLINE_CITATION = re.compile(r"\[\[cite:([a-z0-9-]+)\]\]")


class CandidatePaperError(ValueError):
    """Raised when candidate paper construction would violate the frozen contract."""


def _load_canonical_json(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise CandidatePaperError(f"duplicate JSON key: {path}")
            result[key] = value
        return result

    def reject_nonfinite(value: str) -> object:
        raise CandidatePaperError(f"non-finite JSON value {value}: {path}")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=reject_duplicates, parse_constant=reject_nonfinite)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise CandidatePaperError(f"invalid JSON: {path}") from error
    if type(value) is not dict:
        raise CandidatePaperError(f"JSON object required: {path}")
    return value


def verify_tectonic(tectonic: Path) -> None:
    if not tectonic.is_file() or sha256_path(tectonic) != EXPECTED_TECTONIC_SHA256:
        raise CandidatePaperError("pinned Tectonic SHA-256 mismatch")
    result = subprocess.run([str(tectonic), "--version"], capture_output=True, text=True, check=False)
    if result.returncode or "Tectonic 0.17.0" not in result.stdout:
        raise CandidatePaperError("pinned Tectonic version mismatch")


def validate_template(repository: Path) -> dict[str, Any]:
    template = _load_canonical_json(repository / "paper/v3_measurement/candidate_manuscript_template.json")
    required = {
        "abstract_context", "affiliation", "ai_assistance_disclosure", "author", "citations", "generated_sections",
        "result_field_policy", "schema_version", "sections", "sentence_forms", "title",
    }
    if set(template) != required or template["schema_version"] != "anachron-v3-candidate-manuscript-template-v1":
        raise CandidatePaperError("candidate manuscript template schema differs")
    if template["result_field_policy"] != "generated_only_from_verified_projection" or len(template["sections"]) != 6:
        raise CandidatePaperError("candidate manuscript template result boundary differs")
    forms = template["sentence_forms"]
    required_forms = {"aggregate_status_false", "aggregate_status_true", "below_threshold", "gate_false", "gate_true", "mixed_direction", "negative", "positive", "threshold_met", "zero"}
    if type(forms) is not dict or set(forms) != required_forms or not all(type(value) is str for value in forms.values()):
        raise CandidatePaperError("candidate manuscript sentence forms differ")
    if "Tool-Call Leakage Rate" not in "\n".join(template["sections"][0]["paragraphs"]):
        raise CandidatePaperError("candidate abstract must define TCLR before results")
    return template


def _tex_escape(value: str) -> str:
    replacements = {"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}
    return "".join(replacements.get(character, character) for character in value)


def _render_tex(value: str, citations: set[str]) -> str:
    tokens: dict[str, str] = {}

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in citations:
            raise CandidatePaperError(f"unknown citation: {key}")
        token = f"CITETOKEN{len(tokens)}"
        tokens[token] = rf"\cite{{{key}}}"
        return token

    rendered = _tex_escape(INLINE_CITATION.sub(replace, value))
    for token, citation in tokens.items():
        rendered = rendered.replace(token, citation)
    if "[[cite:" in rendered:
        raise CandidatePaperError("raw citation marker reached TeX")
    return rendered


def _bibliography(repository: Path, citations: list[str]) -> str:
    source = (repository / "paper/v3_measurement/candidate_references.bib").read_text(encoding="utf-8")
    records = []
    for key in citations:
        match = re.search(rf"^@[A-Za-z]+\{{{re.escape(key)},(.*?)(?=^@|\Z)", source, re.MULTILINE | re.DOTALL)
        if not match:
            raise CandidatePaperError(f"unresolved citation: {key}")
        entry = match.group(1)
        fields = {name: re.search(rf"{name}\s*=\s*\{{(.*?)\}}", entry, re.DOTALL) for name in ("author", "title", "year", "journal", "booktitle", "doi", "url")}
        if any(fields[name] is None for name in ("author", "title", "year")):
            raise CandidatePaperError(f"incomplete citation: {key}")
        venue = fields["journal"] or fields["booktitle"]
        locator = fields["doi"] or fields["url"]
        author = fields["author"].group(1).replace(" and ", ", ")
        values = [author, fields["year"].group(1), fields["title"].group(1)]
        values.extend(value.group(1) for value in (venue, locator) if value is not None)
        records.append(". ".join(values))
    return "\n".join(rf"\bibitem{{{key}}} {_tex_escape(record)}" for key, record in zip(citations, records))


def _rate_text(rate: dict[str, Any]) -> str:
    if "undefined" in rate:
        return "undefined (no finance-returning interactions)"
    return f"{rate['numerator']}/{rate['denominator']}"


def _count_text(cell: dict[str, Any]) -> str:
    if cell["metric"] == "survivorship_leakage" and cell["denominator_count"] == 0:
        return "0 eligible interactions"
    return f"{cell['count']}/{cell['denominator_count']}"


def _primary_cells(projection: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        cell for cell in projection["cells"]
        if cell["split"] == "primary" and cell["metric"] == "tclr" and cell["model"] != "pooled"
    ]


def _cells(projection: dict[str, Any], split: str) -> list[dict[str, Any]]:
    return sorted(
        (cell for cell in projection["cells"] if cell["split"] == split),
        key=lambda cell: (cell["metric"], cell["model"], cell["mode"]),
    )


def validate_projection(projection: dict[str, Any]) -> None:
    expected_projection = {
        "analysis_go", "cells", "equinox_enforced_survivorship", "paired_tclr_reductions",
        "schema_version", "scientific_gates", "split_counts",
    }
    if (
        type(projection) is not dict
        or set(projection) != expected_projection
        or projection.get("schema_version") != "anachron-v3-candidate-projection-v1"
        or projection.get("split_counts") != {"development": 72, "primary": 264, "total": 336}
    ):
        raise CandidatePaperError("candidate projection topology differs")
    if type(projection.get("analysis_go")) is not bool or type(projection.get("scientific_gates")) is not dict:
        raise CandidatePaperError("candidate projection gate schema differs")
    expected_cells = {
        (split, model, mode, metric)
        for split in ("primary", "development")
        for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M", "pooled")
        for mode in ("unrestricted", "enforced")
        for metric in ("tclr", "query_leakage", "restatement_leakage", "survivorship_leakage")
    }
    cells = projection.get("cells")
    if type(cells) is not list or len(cells) != len(expected_cells):
        raise CandidatePaperError("candidate projection cell topology differs")
    observed = set()
    required = {"case_count", "count", "denominator_count", "denominator_text", "metric", "mode", "model", "model_count", "rate", "repetition_n", "scope_text", "split", "trajectory_count"}
    for cell in cells:
        if type(cell) is not dict or set(cell) != required:
            raise CandidatePaperError("candidate projection cell schema differs")
        identity = (cell["split"], cell["model"], cell["mode"], cell["metric"])
        if identity not in expected_cells or identity in observed:
            raise CandidatePaperError("candidate projection cell identity differs")
        observed.add(identity)
        expected_cases = 22 if cell["split"] == "primary" else 6
        expected_trajectories = 132 if cell["split"] == "primary" and cell["model"] == "pooled" else 66 if cell["split"] == "primary" else 36 if cell["model"] == "pooled" else 18
        expected_models = 2 if cell["model"] == "pooled" else 1
        for field in ("case_count", "count", "denominator_count", "model_count", "repetition_n", "trajectory_count"):
            if type(cell[field]) is not int or cell[field] < 0:
                raise CandidatePaperError("candidate projection count type differs")
        expected_denominator_text = "finance-returning tool interactions" if cell["metric"] == "survivorship_leakage" else "tool interactions"
        if (
            cell["case_count"] != expected_cases
            or cell["trajectory_count"] != expected_trajectories
            or cell["model_count"] != expected_models
            or cell["repetition_n"] != 3
            or cell["scope_text"] != "finite synthetic panel; descriptive only"
            or cell["denominator_text"] != expected_denominator_text
            or cell["denominator_count"] > expected_trajectories
            or cell["count"] > cell["denominator_count"]
            or (cell["metric"] != "survivorship_leakage" and cell["denominator_count"] != expected_trajectories)
        ):
            raise CandidatePaperError("candidate projection cell scope differs")
        rate = cell["rate"]
        undefined = (
            cell["metric"] == "survivorship_leakage"
            and cell["count"] == 0
            and cell["denominator_count"] == 0
            and rate == {"undefined": "no_finance_interactions"}
        )
        defined = (
            type(rate) is dict
            and set(rate) == {"numerator", "denominator"}
            and type(rate["numerator"]) is int
            and type(rate["denominator"]) is int
            and rate["numerator"] >= 0
            and rate["denominator"] > 0
            and cell["denominator_count"] > 0
            and {"numerator": Fraction(cell["count"], cell["denominator_count"]).numerator, "denominator": Fraction(cell["count"], cell["denominator_count"]).denominator} == rate
        )
        if not (undefined or defined):
            raise CandidatePaperError("candidate projection rate differs")
    if observed != expected_cells:
        raise CandidatePaperError("candidate projection cells are missing or extra")
    cell_index = {(cell["split"], cell["model"], cell["mode"], cell["metric"]): cell for cell in cells}
    for split in ("primary", "development"):
        for mode in ("unrestricted", "enforced"):
            for metric in ("tclr", "query_leakage", "restatement_leakage", "survivorship_leakage"):
                first = cell_index[(split, "qwen2.5:7b", mode, metric)]
                second = cell_index[(split, "qwen3:14b-q4_K_M", mode, metric)]
                pooled = cell_index[(split, "pooled", mode, metric)]
                if (
                    pooled["count"] != first["count"] + second["count"]
                    or pooled["denominator_count"] != first["denominator_count"] + second["denominator_count"]
                    or pooled["trajectory_count"] != first["trajectory_count"] + second["trajectory_count"]
                ):
                    raise CandidatePaperError("candidate projection pooled aggregate differs")
    paired = projection.get("paired_tclr_reductions")
    expected_pairs = {(split, model) for split in ("primary", "development") for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M", "pooled")}
    if type(paired) is not list or len(paired) != len(expected_pairs):
        raise CandidatePaperError("candidate projection pairing topology differs")
    observed_pairs = set()
    for row in paired:
        if type(row) is not dict or set(row) != {"model", "rate", "sign_class", "split", "trajectory_pair_count"}:
            raise CandidatePaperError("candidate projection pairing schema differs")
        rate = row["rate"]
        identity = (row["split"], row["model"])
        if identity not in expected_pairs or identity in observed_pairs or type(rate) is not dict or set(rate) != {"numerator", "denominator"} or type(rate["numerator"]) is not int or type(rate["denominator"]) is not int or rate["denominator"] <= 0 or row["sign_class"] not in {"positive", "zero", "negative"}:
            raise CandidatePaperError("candidate projection pairing differs")
        observed_pairs.add(identity)
        expected_pair_count = 132 if row["split"] == "primary" and row["model"] == "pooled" else 66 if row["split"] == "primary" else 36 if row["model"] == "pooled" else 18
        if type(row["trajectory_pair_count"]) is not int or row["trajectory_pair_count"] != expected_pair_count:
            raise CandidatePaperError("candidate projection pair scope differs")
        normalized = Fraction(rate["numerator"], rate["denominator"])
        if {"numerator": normalized.numerator, "denominator": normalized.denominator} != rate:
            raise CandidatePaperError("candidate projection pairing rate differs")
        unrestricted = cell_index[(row["split"], row["model"], "unrestricted", "tclr")]["rate"]
        enforced = cell_index[(row["split"], row["model"], "enforced", "tclr")]["rate"]
        expected_rate = Fraction(unrestricted["numerator"], unrestricted["denominator"]) - Fraction(enforced["numerator"], enforced["denominator"])
        if normalized != expected_rate:
            raise CandidatePaperError("candidate projection pairing does not equal TCLR reduction")
        sign = "positive" if rate["numerator"] > 0 else "negative" if rate["numerator"] < 0 else "zero"
        if row["sign_class"] != sign:
            raise CandidatePaperError("candidate projection sign differs")
    if observed_pairs != expected_pairs:
        raise CandidatePaperError("candidate projection pairs are missing or extra")
    gates = projection["scientific_gates"]
    required_gates = {"all_trajectories_valid", "minimum_primary_reduction", "no_model_negative", "enforced_equinox_survivorship_each_model"}
    if set(gates) != required_gates or any(type(value) is not bool for value in gates.values()):
        raise CandidatePaperError("candidate projection scientific gates differ")
    equinox = projection.get("equinox_enforced_survivorship")
    if type(equinox) is not dict or set(equinox) != {"qwen2.5:7b", "qwen3:14b-q4_K_M"} or any(type(value) is not bool for value in equinox.values()):
        raise CandidatePaperError("candidate projection Equinox status differs")
    model_rates = [
        Fraction(next(row for row in paired if row["split"] == "primary" and row["model"] == model)["rate"]["numerator"], next(row for row in paired if row["split"] == "primary" and row["model"] == model)["rate"]["denominator"])
        for model in ("qwen2.5:7b", "qwen3:14b-q4_K_M")
    ]
    pooled = next(row for row in paired if row["split"] == "primary" and row["model"] == "pooled")
    pooled_rate = Fraction(pooled["rate"]["numerator"], pooled["rate"]["denominator"])
    expected_gates = {
        "all_trajectories_valid": True,
        "minimum_primary_reduction": pooled_rate >= Fraction(1, 5),
        "no_model_negative": all(rate >= 0 for rate in model_rates),
        "enforced_equinox_survivorship_each_model": all(equinox.values()),
    }
    if gates != expected_gates or projection["analysis_go"] is not all(expected_gates.values()):
        raise CandidatePaperError("candidate projection scientific gates do not derive from results")


def _result_text(template: dict[str, Any], projection: dict[str, Any]) -> list[str]:
    forms = template["sentence_forms"]
    pooled = next(row for row in projection["paired_tclr_reductions"] if row["split"] == "primary" and row["model"] == "pooled")
    result = [forms[pooled["sign_class"]]]
    result.append(forms["threshold_met"] if pooled["rate"]["numerator"] * 5 >= pooled["rate"]["denominator"] else forms["below_threshold"])
    directions = {row["sign_class"] for row in projection["paired_tclr_reductions"] if row["split"] == "primary" and row["model"] != "pooled"}
    if len(directions) > 1:
        result.append(forms["mixed_direction"])
    result.append(forms["aggregate_status_true"] if projection["analysis_go"] else forms["aggregate_status_false"])
    result.extend(forms["gate_true"].format(gate_name=key) if value else forms["gate_false"].format(gate_name=key) for key, value in projection["scientific_gates"].items())
    return result


def _abstract_text(template: dict[str, Any], projection: dict[str, Any]) -> str:
    primary = [cell for cell in projection["cells"] if cell["split"] == "primary" and cell["metric"] == "tclr"]
    unrestricted = next(cell for cell in primary if cell["model"] == "pooled" and cell["mode"] == "unrestricted")
    enforced = next(cell for cell in primary if cell["model"] == "pooled" and cell["mode"] == "enforced")
    paired = next(row for row in projection["paired_tclr_reductions"] if row["split"] == "primary" and row["model"] == "pooled")
    threshold = "meets or exceeds" if paired["rate"]["numerator"] * 5 >= paired["rate"]["denominator"] else "is below"
    return " ".join([
        "Tool-Call Leakage Rate (TCLR) is the share of tool interactions whose returned items are published after the requested cutoff.",
        template["abstract_context"],
        f"In the 22-case primary panel with 264 repeated traces, pooled unrestricted TCLR was {_rate_text(unrestricted['rate'])} and pooled enforced TCLR was {_rate_text(enforced['rate'])}.",
        f"The paired unrestricted-minus-enforced difference was {_rate_text(paired['rate'])} ({paired['sign_class']}) and {threshold} the frozen threshold of 1/5.",
        "The aggregate protocol status was " + ("true" if projection["analysis_go"] else "false") + "; it does not enlarge or suppress this finite-panel result.",
    ])


def _figure_tex(projection: dict[str, Any]) -> str:
    rows = _primary_cells(projection)
    labels = []
    for index, row in enumerate(rows):
        rate = row["rate"]
        width = 0 if "undefined" in rate else rate["numerator"] * 120 // rate["denominator"]
        labels.append(rf"\put(0,{120 - index * 22}){{\makebox(170,0)[l]{{\scriptsize {_tex_escape(row['model'])} {_tex_escape(row['mode'])}}}}}\put(180,{115 - index * 22}){{\rule{{{width}pt}}{{7pt}}}}")
    return "\n".join([r"\begin{picture}(320,135)", r"\put(180,130){\line(1,0){120}}", r"\put(180,124){\makebox(0,0)[t]{\scriptsize 0}}", r"\put(300,124){\makebox(0,0)[t]{\scriptsize 1}}", *labels, r"\put(0,5){\makebox(0,0)[l]{\scriptsize Exact rational TCLR scale from 0 to 1.}}", r"\end{picture}", ""])


def build_tex(template: dict[str, Any], projection: dict[str, Any]) -> str:
    validate_projection(projection)
    sections = template["sections"]
    citations = set(template["citations"])
    page_one = "\n\n".join(_render_tex(item, citations) for item in sections[0]["paragraphs"])
    primary_rows = _cells(projection, "primary")
    development_rows = _cells(projection, "development")
    tclr_rows = [cell for cell in primary_rows if cell["metric"] == "tclr"]
    diagnostic_rows = [cell for cell in primary_rows if cell["metric"] != "tclr" and cell["model"] == "pooled"]
    primary_table = [r"\begin{center}\small\renewcommand{\arraystretch}{1.12}\begin{tabular}{llll}Model&Mode&Count&Exact TCLR\\\hline"]
    primary_table.extend(
        rf"{_tex_escape(cell['model'])} & {_tex_escape(cell['mode'])} & {_tex_escape(_count_text(cell))} & {_tex_escape(_rate_text(cell['rate']))}\\"
        for cell in tclr_rows
    )
    primary_table.append(r"\end{tabular}\end{center}")
    diagnostic_table = [r"\begin{center}\footnotesize\renewcommand{\arraystretch}{1.08}\begin{tabular}{llll}Diagnostic (pooled)&Mode&Count&Rate\\\hline"]
    diagnostic_table.extend(
        rf"{_tex_escape(cell['metric'])} & {_tex_escape(cell['mode'])} & {_tex_escape(_count_text(cell))} & {_tex_escape(_rate_text(cell['rate']))}\\"
        for cell in diagnostic_rows
    )
    diagnostic_table.append(r"\end{tabular}\end{center}")
    reductions = [
        rf"{_tex_escape(row['model'])}: {_tex_escape(_rate_text(row['rate']))} ({_tex_escape(row['sign_class'])})"
        for row in projection["paired_tclr_reductions"] if row["split"] == "primary"
    ]
    gate_table = [r"\begin{center}\scriptsize\begin{tabular}{ll}Frozen gate&Observed status\\\hline"]
    gate_table.extend(rf"{_tex_escape(name)} & {'true' if value else 'false'}\\" for name, value in projection["scientific_gates"].items())
    gate_table.append(r"\end{tabular}\end{center}")
    pages = [
        page_one,
        "\n\n".join(_render_tex(item, citations) for item in sections[1]["paragraphs"]),
        "\n\n".join([*(_render_tex(item, citations) for item in sections[2]["paragraphs"]), *(_render_tex(item, citations) for item in _result_text(template, projection)), r"\input{figures/primary_tclr.tex}", r"\small Figure 1. Primary TCLR values generated from the verified projection: 22 cases, 264 trajectories, two models, and three repeated traces per case-model-mode. The axis is an exact rational scale from 0 to 1; the scope is descriptive only.", *primary_table, r"\noindent\textit{Paired unrestricted-minus-enforced TCLR reductions:} " + "; ".join(reductions) + ".", *diagnostic_table]),
        "\n\n".join([*(_render_tex(item, citations) for item in sections[3]["paragraphs"]), *gate_table, r"\noindent\textit{Enforced Equinox survivorship diagnostic:} " + "; ".join(f"{_tex_escape(model)}: {'true' if value else 'false'}" for model, value in projection["equinox_enforced_survivorship"].items()) + "."]),
        "\n\n".join([*(_render_tex(item, citations) for item in sections[4]["paragraphs"]), _render_tex(template["ai_assistance_disclosure"], citations)]),
        "\n\n".join(_render_tex(item, citations) for item in sections[5]["paragraphs"]),
    ]
    body = []
    for section, content in zip(sections[1:], pages[1:]):
        body.extend([rf"\section*{{{_tex_escape(section['heading'])}}}", content])
    development_table = [r"\begin{center}\footnotesize\renewcommand{\arraystretch}{1.1}\begin{tabular}{lllll}Metric&Model&Mode&Count&Rate\\\hline"]
    development_table.extend(
        rf"{_tex_escape(cell['metric'])} & {_tex_escape(cell['model'])} & {_tex_escape(cell['mode'])} & {_tex_escape(_count_text(cell))} & {_tex_escape(_rate_text(cell['rate']))}\\"
        for cell in development_rows
    )
    development_table.append(r"\end{tabular}\end{center}")
    references = _bibliography(REPOSITORY_ROOT, template["citations"])
    return "\n".join([
        r"\documentclass[10pt]{article}", r"\usepackage[margin=0.8in]{geometry}", r"\usepackage[T1]{fontenc}", r"\usepackage{xcolor}", r"\pagestyle{plain}", r"\begin{document}", r"\small",
        rf"\begin{{center}}\Large\textbf{{{_tex_escape(template['title'])}}}\\\normalsize {_tex_escape(template['author'])}\\{_tex_escape(template['affiliation'])}\end{{center}}", r"\section*{Abstract}", _tex_escape(_abstract_text(template, projection)), r"\section*{1. Question and measurement}", page_one,
        *body, r"\clearpage", r"\appendix", r"\section*{Appendix A. Development results}",
        r"Development traces are disclosed separately and do not enter any primary estimate or gate. The following complete table is descriptive; repetitions are repeated traces, not inferential observations.", *development_table,
        r"\begin{thebibliography}{9}", references, r"\end{thebibliography}", r"\end{document}", "",
    ])


def _write_text(path: Path, content: str) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as output:
        output.write(content)
        output.flush()
        os.fsync(output.fileno())


def _write_tree(root: Path, template: dict[str, Any], projection: dict[str, Any]) -> Path:
    tree = root / "source"
    tree.mkdir()
    (tree / "figures").mkdir()
    _write_text(tree / "main.tex", build_tex(template, projection))
    _write_text(tree / "figures/primary_tclr.tex", _figure_tex(projection))
    _write_text(tree / "README.md", "Local Anachron v3 candidate source only. NOT AUTHORIZED FOR SUBMISSION. Compile main.tex with pinned Tectonic 0.17.0.\n")
    with (PAPER_DIRECTORY / "candidate_references.bib").open("rb") as reader, (tree / "references.bib").open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())
    if tuple(sorted(path.relative_to(tree).as_posix() for path in tree.rglob("*") if path.is_file())) != ARCHIVE_FILES:
        raise CandidatePaperError("candidate source tree violates the archive allowlist")
    return tree


def _archive(tree: Path, output: Path) -> None:
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in ARCHIVE_FILES:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (tree / name).read_bytes())
    with zipfile.ZipFile(output) as archive:
        if tuple(sorted(archive.namelist())) != ARCHIVE_FILES:
            raise CandidatePaperError("candidate archive violates the archive allowlist")


def _extract_archive(archive: Path, destination: Path) -> None:
    if destination.exists():
        raise CandidatePaperError("archive extraction destination must not already exist")
    with zipfile.ZipFile(archive) as source:
        members = tuple(sorted(item.filename for item in source.infolist()))
        if members != ARCHIVE_FILES:
            raise CandidatePaperError("archive extraction allowlist differs")
        destination.mkdir()
        for item in source.infolist():
            name = Path(item.filename)
            if item.is_dir() or item.filename != name.as_posix() or name.is_absolute() or ".." in name.parts or item.external_attr >> 16 & 0o170000 == 0o120000:
                raise CandidatePaperError("archive contains an unsafe member")
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with source.open(item) as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())


def _compile(tectonic: Path, source: Path, output: Path) -> Path:
    output.mkdir()
    result = subprocess.run([str(tectonic), "-Z", "deterministic-mode", "--outdir", str(output), "main.tex"], cwd=source, capture_output=True, text=True, check=False)
    if result.returncode:
        raise CandidatePaperError(f"Tectonic compilation failed: {result.stderr.strip()}")
    pdf = output / "main.pdf"
    if not pdf.is_file():
        raise CandidatePaperError("Tectonic did not create a PDF")
    return pdf


def verify_pdf(pdf: Path, render: Path, projection: dict[str, Any]) -> dict[str, Any]:
    import fitz
    from PIL import Image

    document = fitz.open(pdf)
    try:
        if not 4 <= document.page_count <= 6:
            raise CandidatePaperError(f"candidate PDF must contain four to six balanced research-note pages, found {document.page_count}")
        text = [page.get_text() for page in document]
        required = ("Abstract", "Primary results", "Gate status and interpretation boundary", "Limitations and AI assistance", "References", "AI systems assisted")
        appendix_pages = [index for index, page_text in enumerate(text) if "Appendix A. Development results" in page_text]
        if any(token not in "\n".join(text) for token in required) or len(appendix_pages) != 1 or appendix_pages[0] == 0:
            raise CandidatePaperError("candidate PDF lacks required headings or appendix placement")
        full_text = "\n".join(text).replace("ﬁ", "fi").replace("ﬀ", "ff")
        if any(token in full_text for token in ("PRE-FULL-RESULTS", "[[cite:", "CITETOKEN", "TODO", "TBD", "Dummy bibliography")):
            raise CandidatePaperError("candidate PDF contains a preliminary banner")
        if any(not page.get_text("words") for page in document):
            raise CandidatePaperError("candidate PDF contains a blank page")
        displayed_cells = [cell for cell in _cells(projection, "primary") if cell["metric"] == "tclr" or cell["model"] == "pooled"]
        for cell in displayed_cells:
            if _rate_text(cell["rate"]) not in full_text:
                raise CandidatePaperError("candidate PDF result block differs from the projection")
        for name, value in projection["scientific_gates"].items():
            marker = f"The frozen gate {name} is {'true' if value else 'false'}."
            if marker not in re.sub(r"\s+", " ", full_text):
                raise CandidatePaperError("candidate PDF gate block differs from the projection")
        for page in document:
            rectangle = page.rect
            blocks = [block for block in page.get_text("blocks") if len(block[4].strip()) >= 4]
            for block in page.get_text("blocks"):
                if block[0] < rectangle.x0 or block[1] < rectangle.y0 or block[2] > rectangle.x1 or block[3] > rectangle.y1:
                    raise CandidatePaperError("candidate PDF text exceeds a page boundary")
            for index, first in enumerate(blocks):
                first_area = max((first[2] - first[0]) * (first[3] - first[1]), 1.0)
                for second in blocks[index + 1:]:
                    overlap_width = max(0.0, min(first[2], second[2]) - max(first[0], second[0]))
                    overlap_height = max(0.0, min(first[3], second[3]) - max(first[1], second[1]))
                    if overlap_width * overlap_height > 0.2 * min(first_area, max((second[2] - second[0]) * (second[3] - second[1]), 1.0)):
                        raise CandidatePaperError("candidate PDF contains materially overlapping text blocks")
            spans = [span for block in page.get_text("dict")["blocks"] if block.get("type") == 0 for line in block["lines"] for span in line["spans"] if span["text"].strip()]
            if any(span["size"] < 5.5 for span in spans):
                raise CandidatePaperError("candidate PDF contains illegibly small text")
            for drawing in page.get_drawings():
                if not rectangle.contains(drawing["rect"]):
                    raise CandidatePaperError("candidate PDF drawing exceeds a page boundary")
        if render.exists():
            raise CandidatePaperError("QA render output must not already exist")
        render.mkdir()
        images = []
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(matrix=fitz.Matrix(1.2, 1.2), alpha=False)
            target = render / f"candidate-page-{index + 1:02d}.png"
            pixmap.save(target)
            images.append(Image.open(target).convert("RGB"))
        columns = 2
        rows = (len(images) + columns - 1) // columns
        contact = Image.new("RGB", (images[0].width * columns, images[0].height * rows), "white")
        for index, image in enumerate(images):
            contact.paste(image, ((index % 2) * image.width, (index // 2) * image.height))
        contact.save(render / "contact-sheet.png")
    finally:
        document.close()
    return {"page_count": len(text), "rendered_pages": len(text), "projection_sha256": hashlib.sha256(canonical_json(projection)).hexdigest()}


def _metadata(template: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    return {
        "abstract": _abstract_text(template, projection),
        "ai_assistance_disclosure": template["ai_assistance_disclosure"],
        "analysis_go": projection["analysis_go"],
        "authors": [{"name": template["author"], "affiliation": template["affiliation"]}],
        "comments": "Local candidate draft only; not authorized for submission.",
        "cross_list": [],
        "external_authorization_status": "none; local candidate only; no outreach, upload, or submission authorized",
        "license_proposal": "CC BY 4.0 proposed; not granted or submitted",
        "primary_category": "cs.AI",
        "title": template["title"],
    }


def _manifest(tree: Path) -> dict[str, Any]:
    return {"files": [{"path": name, "sha256": sha256_path(tree / name)} for name in ARCHIVE_FILES], "schema_version": "anachron-v3-paper-source-manifest-v1"}


def _build_once(tectonic: Path, template: dict[str, Any], projection: dict[str, Any], root: Path) -> dict[str, Any]:
    tree = _write_tree(root, template, projection)
    archive = root / "source.zip"
    _archive(tree, archive)
    pdf = _compile(tectonic, tree, root / "compile")
    extracted = root / "extracted"
    _extract_archive(archive, extracted)
    extracted_pdf = _compile(tectonic, extracted, root / "extracted-compile")
    if sha256_path(pdf) != sha256_path(extracted_pdf):
        raise CandidatePaperError("extracted archive does not compile to the canonical PDF")
    return {"archive": archive, "pdf": pdf, "source": tree, "source_manifest": _manifest(tree)}


def _copy_create_only(source: Path, destination: Path) -> None:
    with source.open("rb") as reader, destination.open("xb") as writer:
        shutil.copyfileobj(reader, writer)
        writer.flush()
        os.fsync(writer.fileno())


def _copy_tree_create_only(source: Path, destination: Path) -> None:
    if destination.exists() or source.is_symlink() or not source.is_dir():
        raise CandidatePaperError("candidate tree copy requires a fresh real directory")
    destination.mkdir()
    for entry in sorted(source.iterdir(), key=lambda item: item.name):
        target = destination / entry.name
        if entry.is_symlink():
            raise CandidatePaperError("candidate staging tree contains a link")
        if entry.is_dir():
            _copy_tree_create_only(entry, target)
        elif entry.is_file():
            _copy_create_only(entry, target)
        else:
            raise CandidatePaperError("candidate staging tree contains a non-regular entry")


def _tree_snapshot(root: Path, top_level: tuple[str, ...], expected_top_level: tuple[str, ...] | None = None) -> tuple[tuple[str, str], ...]:
    expected = top_level if expected_top_level is None else expected_top_level
    if tuple(sorted(path.name for path in root.iterdir())) != tuple(sorted(expected)):
        raise CandidatePaperError("candidate completion set differs")
    entries: list[tuple[str, str]] = []
    for name in top_level:
        path = root / name
        metadata = os.lstat(path)
        if stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & 0x400):
            raise CandidatePaperError("candidate completion contains a link or reparse point")
        if stat.S_ISREG(metadata.st_mode):
            entries.append((f"file:{name}", sha256_path(path)))
            continue
        if not stat.S_ISDIR(metadata.st_mode):
            raise CandidatePaperError("candidate completion contains a non-regular entry")
        entries.append((f"directory:{name}", ""))
        for descendant in sorted(path.rglob("*"), key=lambda item: item.as_posix()):
            relative = descendant.relative_to(root).as_posix()
            descendant_metadata = os.lstat(descendant)
            if stat.S_ISLNK(descendant_metadata.st_mode) or bool(getattr(descendant_metadata, "st_file_attributes", 0) & 0x400):
                raise CandidatePaperError("candidate completion contains a link or reparse point")
            if stat.S_ISDIR(descendant_metadata.st_mode):
                entries.append((f"directory:{relative}", ""))
            elif stat.S_ISREG(descendant_metadata.st_mode):
                entries.append((f"file:{relative}", sha256_path(descendant)))
            else:
                raise CandidatePaperError("candidate completion contains a non-regular entry")
    return tuple(entries)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    data = canonical_json(value)
    with path.open("xb") as output:
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def _receipt(contract: dict[str, Any], snapshot: Path, candidate: Path, metadata: dict[str, Any], projection: dict[str, Any]) -> dict[str, Any]:
    receipt = {
        "abstract_sha256": hashlib.sha256(metadata["abstract"].encode("utf-8")).hexdigest(),
        "ai_assistance_disclosure_sha256": hashlib.sha256(metadata["ai_assistance_disclosure"].encode("utf-8")).hexdigest(),
        "archive_sha256": sha256_path(candidate / "source.zip"),
        "arxiv_metadata_sha256": sha256_path(candidate / "arxiv_metadata.json"),
        "candidate_acceptance_matrix_sha256": contract["candidate_acceptance_matrix_sha256"],
        "candidate_claim_evidence_map_sha256": contract["candidate_claim_evidence_map_sha256"],
        "candidate_contract_sha256": sha256_path(PAPER_DIRECTORY / "candidate_contract.json"),
        "candidate_manuscript_template_sha256": contract["candidate_manuscript_template_sha256"],
        "candidate_pdf_sha256": sha256_path(candidate / "candidate.pdf"),
        "evidence_manifest_sha256": sha256_path(snapshot / "manifest.json"),
        "evidence_manifest_sidecar_sha256": sha256_path(snapshot / "manifest.sha256"),
        "falsifier_receipt_sha256": sha256_path(snapshot / "prerequisites/falsifier_receipt.json"),
        "frozen_protocol_matrix_sha256": contract["frozen_protocol_matrix_sha256"],
        "full_go_sha256": sha256_path(snapshot / "prerequisites/full_go.json"),
        "full_plan_sha256": contract["full_plan_sha256"],
        "paper_source_manifest_sha256": sha256_path(candidate / "paper_source_manifest.json"),
        "projection_sha256": sha256_path(candidate / "projection.json"),
        "protocol_commit": contract["frozen_protocol_commit"],
        "protocol_tag": contract["frozen_protocol_tag"],
        "protocol_tag_object": contract["frozen_protocol_tag_object"],
        "schema_version": "anachron-v3-candidate-receipt-v1",
        "scientific_gates": projection["scientific_gates"],
        "source_admission_sha256": sha256_path(snapshot / "source_admission.json"),
        "split_counts": projection["split_counts"],
    }
    return receipt


def validate_receipt(receipt: dict[str, Any], contract: dict[str, Any]) -> None:
    hashes = {
        "abstract_sha256", "ai_assistance_disclosure_sha256", "archive_sha256", "arxiv_metadata_sha256",
        "candidate_acceptance_matrix_sha256", "candidate_claim_evidence_map_sha256", "candidate_contract_sha256",
        "candidate_manuscript_template_sha256", "candidate_pdf_sha256", "evidence_manifest_sha256",
        "evidence_manifest_sidecar_sha256", "falsifier_receipt_sha256", "frozen_protocol_matrix_sha256",
        "full_go_sha256", "full_plan_sha256", "paper_source_manifest_sha256", "projection_sha256", "source_admission_sha256",
    }
    expected = hashes | {"protocol_commit", "protocol_tag", "protocol_tag_object", "schema_version", "scientific_gates", "split_counts"}
    if type(receipt) is not dict or set(receipt) != expected or any(type(receipt[key]) is not str or len(receipt[key]) != 64 for key in hashes):
        raise CandidatePaperError("candidate receipt schema differs")
    if receipt["schema_version"] != "anachron-v3-candidate-receipt-v1" or receipt["protocol_commit"] != contract["frozen_protocol_commit"] or receipt["protocol_tag"] != contract["frozen_protocol_tag"] or receipt["protocol_tag_object"] != contract["frozen_protocol_tag_object"] or receipt["split_counts"] != {"development": 72, "primary": 264, "total": 336} or type(receipt["scientific_gates"]) is not dict or any(type(value) is not bool for value in receipt["scientific_gates"].values()):
        raise CandidatePaperError("candidate receipt bindings differ")
    frozen_bindings = {
        "candidate_acceptance_matrix_sha256": contract["candidate_acceptance_matrix_sha256"],
        "candidate_claim_evidence_map_sha256": contract["candidate_claim_evidence_map_sha256"],
        "candidate_contract_sha256": sha256_path(PAPER_DIRECTORY / "candidate_contract.json"),
        "candidate_manuscript_template_sha256": contract["candidate_manuscript_template_sha256"],
        "frozen_protocol_matrix_sha256": contract["frozen_protocol_matrix_sha256"],
        "full_plan_sha256": contract["full_plan_sha256"],
    }
    if any(receipt[name] != value for name, value in frozen_bindings.items()):
        raise CandidatePaperError("candidate receipt frozen hash binding differs")


def _publish_staging(staging: Path, output: Path) -> None:
    if os.path.lexists(output):
        raise FileExistsError("candidate output appeared before publication")
    pre_receipt = tuple(name for name in CANDIDATE_COMPLETION if name != "candidate_receipt.json")
    _tree_snapshot(staging, CANDIDATE_COMPLETION)
    output.mkdir()
    for name in pre_receipt:
        source = staging / name
        destination = output / source.name
        if source.is_dir():
            _copy_tree_create_only(source, destination)
        else:
            _copy_create_only(source, destination)
    if _tree_snapshot(staging, pre_receipt, CANDIDATE_COMPLETION) != _tree_snapshot(output, pre_receipt):
        raise CandidatePaperError("candidate pre-receipt closure differs after publication")
    _copy_create_only(staging / "candidate_receipt.json", output / "candidate_receipt.json")
    if _tree_snapshot(staging, CANDIDATE_COMPLETION) != _tree_snapshot(output, CANDIDATE_COMPLETION):
        raise CandidatePaperError("candidate post-receipt closure differs after publication")


def build_candidate(protocol_root: Path, evidence: Path, output: Path, tectonic: Path) -> dict[str, Any]:
    require_create_only_output(output, (protocol_root, evidence, tectonic))
    contract = validate_candidate_contract(REPOSITORY_ROOT)
    template = validate_template(REPOSITORY_ROOT)
    verify_tectonic(tectonic)
    with admitted_snapshot(protocol_root, evidence) as (snapshot, analysis):
        projection = build_projection(answer_free_rows(protocol_root, snapshot), analysis)
        with tempfile.TemporaryDirectory(prefix="anachron-v3-candidate-paper-") as first_root:
            first = _build_once(tectonic, template, projection, Path(first_root))
            staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
            try:
                _copy_tree_create_only(first["source"], staging / "source")
                _copy_create_only(first["archive"], staging / "source.zip")
                _copy_create_only(first["pdf"], staging / "candidate.pdf")
                _write_json(staging / "projection.json", projection)
                _write_json(staging / "paper_source_manifest.json", first["source_manifest"])
                metadata = _metadata(template, projection)
                _write_json(staging / "arxiv_metadata.json", metadata)
                verification = verify_pdf(staging / "candidate.pdf", staging / "qa_renders", projection)
                receipt = _receipt(contract, snapshot, staging, metadata, projection)
                validate_receipt(receipt, contract)
                _write_json(staging / "candidate_receipt.json", receipt)
                _publish_staging(staging, output)
                shutil.rmtree(staging)
            except Exception:
                if staging.exists():
                    shutil.rmtree(staging)
                raise
            return {"candidate_receipt": receipt, "pdf_verification": verification}


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tectonic", type=Path, required=True)
    values = parser.parse_args(arguments)
    print(json.dumps(build_candidate(values.protocol_root, values.evidence, values.output, values.tectonic), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
