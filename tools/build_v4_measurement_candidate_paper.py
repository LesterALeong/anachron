"""Build a deterministic local v4 candidate paper from an answer-free projection."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from anachron.data.v4_registry import canonical_json_bytes, strict_json_loads
from anachron.v4_candidate_common import (
    generated_arxiv_metadata,
    pooled_tclr_direction,
    validate_candidate_projection,
)
from anachron.v4_contract import V4ContractError, validate_authority_contract
from anachron.v4_paths import (
    V4PathError,
    admit_create_only_external_output,
    admit_external_regular_input,
    admit_repository_root,
)
from tools.build_v4_source_manifest import V4SourceManifestError
from tools.build_v4_source_manifest import validate as validate_source_manifest

ARCHIVE_FILES = ("README.md", "figures/primary_tclr.tex", "main.tex", "references.bib")
CANDIDATE_COMPLETION = (
    "source",
    "source.zip",
    "candidate.pdf",
    "projection.json",
    "paper_source_manifest.json",
    "arxiv_metadata.json",
    "qa_renders",
    "qa_render_manifest.json",
    "candidate_receipt.json",
)
_AUTHORITY_CLOSURE_FIELDS = {
    "actual_go_sha256": "compatibility/conditional_go.json",
    "comparison_projection_sha256": "compatibility/comparison.json",
    "runtime_identity_sha256": "compatibility/runtime_identity.json",
    "source_audit_sha256": "compatibility/source_audit.json",
    "source_manifest_sha256": "compatibility/source_manifest.json",
}


class CandidatePaperError(ValueError):
    """Raised when a local v4 candidate paper cannot be safely built."""


def _paper_qa_dependencies() -> tuple[Any, Any]:
    try:
        import fitz
        from PIL import Image
    except ImportError as error:
        raise CandidatePaperError("PDF QA dependencies are unavailable") from error
    return fitz, Image


def sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            digest.update(chunk)
    return digest.hexdigest()


def _sha256_limited(path: Path, maximum: int, label: str) -> str:
    _file_size(path, maximum, label)
    try:
        digest = hashlib.sha256()
        consumed = 0
        with path.open("rb") as stream:
            while chunk := stream.read(65536):
                consumed += len(chunk)
                if consumed > maximum:
                    raise CandidatePaperError(f"{label} exceeds the contract byte cap")
                digest.update(chunk)
    except OSError as error:
        raise CandidatePaperError(f"{label} cannot be read") from error
    return digest.hexdigest()


def _read_limited(path: Path, maximum: int, label: str) -> bytes:
    try:
        with path.open("rb") as stream:
            value = stream.read(maximum + 1)
    except OSError as error:
        raise CandidatePaperError(f"{label} cannot be read") from error
    if len(value) > maximum:
        raise CandidatePaperError(f"{label} exceeds the contract byte cap")
    return value


def _file_size(path: Path, maximum: int, label: str) -> int:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise CandidatePaperError(f"{label} cannot be inspected") from error
    if size < 0 or size > maximum:
        raise CandidatePaperError(f"{label} exceeds the contract byte cap")
    return size


def _json(path: Path, label: str, *, canonical: bool = True) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
        value = strict_json_loads(raw, label)
    except (OSError, ValueError) as error:
        raise CandidatePaperError(f"{label} cannot be read") from error
    if type(value) is not dict or (canonical and raw != canonical_json_bytes(value)):
        raise CandidatePaperError(f"{label} is not canonical JSON")
    return value


def _mapping(value: object, keys: set[str], label: str) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise CandidatePaperError(f"{label} schema differs")
    return value


def _escape(value: str) -> str:
    return "".join({"\\": r"\textbackslash{}", "&": r"\&", "%": r"\%", "_": r"\_", "#": r"\#", "$": r"\$"}.get(character, character) for character in value)


def _bounded_command(command: list[str], cwd: Path, policy: dict[str, int], label: str) -> str:
    try:
        process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as error:
        raise CandidatePaperError(f"{label} cannot start") from error
    logs = [bytearray(), bytearray()]
    overflow = [False, False]

    def drain(index: int, stream: Any) -> None:
        while True:
            chunk = stream.read(8192)
            if not chunk:
                return
            remaining = policy["tectonic_log_max_bytes"] - len(logs[index])
            logs[index].extend(chunk[: max(remaining, 0)])
            if len(chunk) > remaining:
                overflow[index] = True
                try:
                    process.kill()
                except OSError:
                    pass

    threads = [
        threading.Thread(target=drain, args=(index, stream), daemon=False)
        for index, stream in enumerate((process.stdout, process.stderr))
    ]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=policy["tectonic_timeout_seconds"])
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()
        process.wait()
    finally:
        for thread in threads:
            thread.join()
        process.stdout.close()
        process.stderr.close()
    prefix = b"\n".join(bytes(value) for value in logs).decode("utf-8", errors="replace")[:256]
    if timed_out:
        raise CandidatePaperError(f"{label} timed out: {prefix}")
    if any(overflow):
        raise CandidatePaperError(f"{label} log exceeds the contract byte cap: {prefix}")
    if process.returncode != 0:
        raise CandidatePaperError(f"{label} failed: {prefix}")
    return prefix


def verify_tectonic(tectonic: Path, candidate: dict[str, Any]) -> None:
    identity = candidate["tectonic"]
    expected = identity["windows_executable_sha256"] if os.name == "nt" else identity["linux_executable_sha256"]
    if not tectonic.is_file():
        raise CandidatePaperError("pinned Tectonic platform SHA-256 differs")
    _file_size(tectonic, candidate["resource_policy"]["tectonic_executable_max_bytes"], "Tectonic executable")
    if sha256_path(tectonic) != expected:
        raise CandidatePaperError("pinned Tectonic platform SHA-256 differs")
    version = _bounded_command([str(tectonic), "--version"], tectonic.parent, candidate["resource_policy"], "Tectonic version check")
    if f"Tectonic {identity['version']}" not in version:
        raise CandidatePaperError("pinned Tectonic version differs")


def _template(root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    candidate = _json(root / "paper/v4_measurement/candidate_contract.json", "candidate contract")
    template = _json(
        root / "paper/v4_measurement/candidate_manuscript_template.json",
        "manuscript template",
        canonical=False,
    )
    required_template = {"affiliation", "ai_assistance_disclosure", "author", "citations", "generated_sections", "result_field_policy", "schema_version", "sections", "sentence_forms", "title", "v3_included_count"}
    if set(template) != required_template or template["schema_version"] != "anachron-v4-candidate-manuscript-template-pre-freeze-v1" or template["result_field_policy"] != "generated_only_from_verified_answer_free_projection":
        raise CandidatePaperError("manuscript template differs")
    if set(template["sentence_forms"]) != {"negative", "operational_completeness", "positive", "zero"}:
        raise CandidatePaperError("manuscript sentence forms differ")
    citations = (
        "cheng-etal-2024-dated-data",
        "el-lahib-2026-search-date-filter",
        "zhuang-etal-2023-toolqa",
    )
    if template["citations"] != list(citations):
        raise CandidatePaperError("manuscript citations differ")
    references = (root / "paper/v4_measurement/candidate_references.bib").read_text(encoding="utf-8")
    if any(f"@inproceedings{{{citation}," not in references for citation in citations):
        raise CandidatePaperError("candidate references differ")
    if candidate["source_archive_allowlist"] != list(ARCHIVE_FILES):
        raise CandidatePaperError("candidate archive allowlist differs")
    if type(candidate.get("resource_policy")) is not dict:
        raise CandidatePaperError("candidate resource policy differs")
    return candidate, template


def _projection_input(
    root: Path,
    source_manifest: Path,
    projection: Path,
    candidate_contract: dict[str, Any],
    *,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> tuple[dict[str, Any], bytes, dict[str, Any]]:
    try:
        source_manifest = admit_external_regular_input(source_manifest, root, "source manifest")
        projection = admit_external_regular_input(projection, root, "candidate projection")
        _file_size(source_manifest, candidate_contract["resource_policy"]["source_manifest_max_bytes"], "source manifest")
        source = validate_source_manifest(
            root,
            source_manifest,
            **({"expected_origin": expected_origin} if expected_origin else {}),
            **({"expected_v3": expected_v3} if expected_v3 else {}),
        )
    except (V4PathError, V4SourceManifestError) as error:
        raise CandidatePaperError("protocol source manifest differs") from error
    raw = _read_limited(
        projection,
        candidate_contract["resource_policy"]["candidate_projection_max_bytes"],
        "candidate projection",
    )
    try:
        value = strict_json_loads(raw, "candidate projection")
    except ValueError as error:
        raise CandidatePaperError("candidate projection cannot be read") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidatePaperError("candidate projection is not canonical JSON")
    try:
        value = validate_candidate_projection(value, root)
    except (V4ContractError, ValueError) as error:
        raise CandidatePaperError("candidate projection differs") from error
    authority = value["authority"]
    closure = {row["path"]: row["sha256"] for row in value["evidence_closure"]["files"]}
    authority_contract = sha256_path(root / "research/v4_measurement/authority_binding_contract.json")
    if authority["authority_contract_sha256"] != authority_contract:
        raise CandidatePaperError("candidate projection authority contract binding differs")
    if authority["source_manifest_sha256"] != sha256_path(source_manifest) or value["protocol"] != source["release"]:
        raise CandidatePaperError("candidate projection source binding differs")
    if any(closure.get(path) != authority[field] for field, path in _AUTHORITY_CLOSURE_FIELDS.items()):
        raise CandidatePaperError("candidate projection authority closure binding differs")
    return value, raw, source


def _cells(projection: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    result = {(row["model"], row["mode"]): row for row in projection["projection"]["cells"]}
    if len(result) != 6:
        raise CandidatePaperError("candidate projection cell topology differs")
    return result


def _direction(projection: dict[str, Any]) -> str:
    try:
        return pooled_tclr_direction(projection)
    except ValueError as error:
        raise CandidatePaperError("candidate projection direction differs") from error


def _diagnostics(projection: dict[str, Any]) -> dict[str, int]:
    rows = projection["projection"]["diagnostics"]
    return {
        "query_nonblank": sum(row["query_nonblank"] for row in rows),
        "restatement_returned": sum(row["restatement_returned"] for row in rows),
        "survivorship_case": sum(row["survivorship_case"] for row in rows),
    }


def _result_sentences(template: dict[str, Any], projection: dict[str, Any]) -> tuple[str, list[str]]:
    cells = _cells(projection)
    pooled = [cells[("pooled", mode)] for mode in ("unrestricted", "enforced")]
    rates = [f"pooled {row['mode']} TCLR was {row['numerator']}/{row['denominator']} ({row['rate_fixed_decimal']})" for row in pooled]
    return template["sentence_forms"][_direction(projection)], rates


def build_tex(template: dict[str, Any], projection: dict[str, Any]) -> tuple[str, str]:
    sentence, rates = _result_sentences(template, projection)
    cells = _cells(projection)
    diagnostics = _diagnostics(projection)
    table_rows = "\n".join(
        f"{_escape(model)} & {_escape(mode)} & {row['numerator']}/{row['denominator']} & {row['rate_fixed_decimal']} \\\\"
        for (model, mode), row in sorted(cells.items())
    )
    figure = "\n".join(
        [r"\begin{picture}(320,125)", r"\put(170,118){\line(1,0){120}}"]
        + [rf"\put(0,{98 - index * 18}){{\makebox(165,0)[l]{{\scriptsize {_escape(model)} {_escape(mode)}}}}}\put(170,{94 - index * 18}){{\rule{{{row['numerator'] * 120 // row['denominator']}pt}}{{6pt}}}}" for index, ((model, mode), row) in enumerate(sorted(cells.items()))]
        + [r"\end{picture}"]
    )
    sections = template["sections"]
    fixed = "\n\n".join(
        "\\section*{" + _escape(section["heading"]) + "}\n" + "\n\n".join(_escape(paragraph).replace("[[cite:cheng-etal-2024-dated-data]]", r"\cite{cheng-etal-2024-dated-data}").replace("[[cite:el-lahib-2026-search-date-filter]]", r"\cite{el-lahib-2026-search-date-filter}").replace("[[cite:zhuang-etal-2023-toolqa]]", r"\cite{zhuang-etal-2023-toolqa}") for paragraph in section["paragraphs"])
        for section in (sections[0], sections[1], sections[3], sections[4])
    )
    abstract = " ".join([
        "Tool-Call Leakage Rate (TCLR) is the share of scored tool interactions whose returned record was published after the case cutoff.",
        *rates,
        sentence,
        template["sentence_forms"]["operational_completeness"],
    ])
    generated = "\n".join([
        r"\section*{3. Generated primary results}",
        _escape(sentence),
        _escape(template["sentence_forms"]["operational_completeness"]),
        _escape("; ".join(rates)) + ".",
        r"\input{figures/primary_tclr.tex}",
        r"\noindent\textit{Figure 1.} Primary TCLR values generated only from the verified answer-free projection; finite descriptive panel only.",
        r"\begin{center}\begin{tabular}{llll}Model & Mode & TCLR & Decimal\\\hline",
        table_rows,
        r"\end{tabular}\end{center}",
        _escape(f"Diagnostics across 64 primary trajectories: nonblank queries {diagnostics['query_nonblank']}/64; restatement-returned {diagnostics['restatement_returned']}/64; survivorship cases {diagnostics['survivorship_case']}/64."),
        _escape("Topology: 8 cases, 2 models, 2 modes, 2 repetitions, 64 primary trajectories, 128 main chats, 4 excluded compatibility chats, 132 total chats, 0 development trajectories, and 0 v3 inclusions."),
    ])
    tex = "\n".join([
        r"\documentclass[11pt]{article}", r"\usepackage[margin=1in]{geometry}", r"\usepackage{graphicx}",
        r"\title{" + _escape(template["title"]) + "}", r"\author{" + _escape(template["author"]) + r"\\" + _escape(template["affiliation"]) + "}", r"\begin{document}", r"\maketitle", r"\begin{abstract}", _escape(abstract), r"\end{abstract}", fixed, generated, r"\begin{thebibliography}{9}", r"\bibitem{cheng-etal-2024-dated-data} Cheng et al. Dated data.", r"\bibitem{el-lahib-2026-search-date-filter} El Lahib et al. Search date filters.", r"\bibitem{zhuang-etal-2023-toolqa} Zhuang et al. ToolQA.", r"\end{thebibliography}", r"\end{document}", "",
    ])
    return tex, figure


def _write(path: Path, value: bytes, maximum: int | None = None) -> None:
    if maximum is not None and len(value) > maximum:
        raise CandidatePaperError("generated artifact exceeds the contract byte cap")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def _archive(source: Path, archive: Path, policy: dict[str, int]) -> str:
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for relative in ARCHIVE_FILES:
            item = source / relative
            raw = _read_limited(item, policy["source_file_max_bytes"], "source archive member")
            info = zipfile.ZipInfo(relative, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            target.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return _sha256_limited(archive, policy["source_archive_max_bytes"], "source archive")


def _extract(archive: Path, destination: Path, policy: dict[str, int]) -> str:
    archive_digest = _sha256_limited(
        archive, policy["source_archive_max_bytes"], "source archive"
    )
    with zipfile.ZipFile(archive) as source:
        if tuple(source.namelist()) != ARCHIVE_FILES:
            raise CandidatePaperError("source archive allowlist differs")
        for item in source.infolist():
            if (
                item.filename.startswith("/")
                or ".." in Path(item.filename).parts
                or stat.S_ISLNK(item.external_attr >> 16)
                or not stat.S_ISREG(item.external_attr >> 16)
                or item.file_size > policy["source_file_max_bytes"]
            ):
                raise CandidatePaperError("source archive contains unsafe member")
            _write(destination / item.filename, source.read(item.filename), policy["source_file_max_bytes"])
    return archive_digest


def _compile(tectonic: Path, source: Path, output: Path, policy: dict[str, int]) -> Path:
    output.mkdir()
    _bounded_command(
        [str(tectonic), "-Z", "deterministic-mode", "--outdir", str(output), "main.tex"],
        source,
        policy,
        "Tectonic compilation",
    )
    pdf = output / "main.pdf"
    if not pdf.is_file():
        raise CandidatePaperError("Tectonic compilation failed")
    _file_size(pdf, policy["pdf_max_bytes"], "candidate PDF")
    total = sum(
        _file_size(path, policy["tectonic_output_max_bytes"], "Tectonic output")
        for path in output.rglob("*")
        if path.is_file()
    )
    if total > policy["tectonic_output_max_bytes"]:
        raise CandidatePaperError("Tectonic output exceeds the contract byte cap")
    return pdf


def _pdf_qa(pdf: Path, renders: Path, title: str, policy: dict[str, int]) -> dict[str, Any]:
    fitz, image_module = _paper_qa_dependencies()
    _file_size(pdf, policy["pdf_max_bytes"], "candidate PDF")
    document = fitz.open(pdf)
    try:
        text = "\n".join(page.get_text() for page in document)
        page_count = len(document)
        if title not in " ".join(text.split()) or page_count < 1 or page_count > policy["pdf_max_pages"]:
            raise CandidatePaperError("PDF text or page layout differs")
        renders.mkdir()
        rows = []
        for index, page in enumerate(document, start=1):
            render = renders / f"page-{index}.png"
            page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False).save(render)
            with image_module.open(render) as image:
                if (
                    image.width < 500
                    or image.height < 500
                    or image.width * image.height > policy["render_max_pixels"]
                ):
                    raise CandidatePaperError("PDF render layout differs")
            size = _file_size(render, policy["render_max_bytes"], "PDF render")
            rows.append(
                {
                    "path": render.name,
                    "sha256": _sha256_limited(render, policy["render_max_bytes"], "PDF render"),
                    "size_bytes": size,
                }
            )
    finally:
        document.close()
    return {
        "page_count": page_count,
        "renders": rows,
        "schema_version": "anachron-v4-pdf-render-manifest-v1",
    }


def _manifest(source: Path) -> dict[str, Any]:
    rows = [{"path": relative, "sha256": sha256_path(source / relative)} for relative in ARCHIVE_FILES]
    return {"files": rows, "schema_version": "anachron-v4-paper-source-manifest-v1"}


def _publish_no_replace(staging: Path, output: Path) -> None:
    if os.name == "nt":
        os.rename(staging, output)
        return
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as error:
        raise CandidatePaperError("atomic no-replace publication is unavailable") from error
    renameat2.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    renameat2.restype = ctypes.c_int
    if renameat2(-100, os.fsencode(staging), -100, os.fsencode(output), 1) == 0:
        return
    error_number = ctypes.get_errno()
    if error_number == errno.EEXIST:
        raise FileExistsError(error_number, "candidate output already exists", str(output))
    raise CandidatePaperError(f"atomic no-replace publication failed: errno {error_number}")


def build_candidate(
    protocol_root: Path,
    source_manifest: Path,
    projection: Path,
    output: Path,
    tectonic: Path,
    *,
    expected_origin: str | None = None,
    expected_v3: dict[str, str] | None = None,
) -> dict[str, Any]:
    try:
        root = admit_repository_root(protocol_root)
        output = admit_create_only_external_output(output, root, "candidate output")
    except V4PathError as error:
        raise CandidatePaperError(str(error)) from error
    validate_authority_contract(root)
    contract, template = _template(root)
    verify_tectonic(tectonic, contract)
    candidate, projection_raw, source = _projection_input(
        root,
        source_manifest,
        projection,
        contract,
        expected_origin=expected_origin,
        expected_v3=expected_v3,
    )
    policy = contract["resource_policy"]
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.stage-", dir=output.parent))
    try:
        tree = staging / "source"
        tex, figure = build_tex(template, candidate)
        _write(tree / "main.tex", tex.encode("utf-8"), policy["source_file_max_bytes"])
        _write(tree / "figures/primary_tclr.tex", figure.encode("utf-8"), policy["source_file_max_bytes"])
        _write(tree / "references.bib", (root / "paper/v4_measurement/candidate_references.bib").read_bytes(), policy["source_file_max_bytes"])
        _write(tree / "README.md", b"Generated v4 candidate source. Compile main.tex with pinned Tectonic 0.17.0.\n", policy["source_file_max_bytes"])
        archive = staging / "source.zip"
        archive_digest = _archive(tree, archive, policy)
        extracted = staging / "extracted"
        if _extract(archive, extracted, policy) != archive_digest:
            raise CandidatePaperError("source archive changed during extraction")
        pdf = _compile(tectonic, tree, staging / "compile", policy)
        extracted_pdf = _compile(tectonic, extracted, staging / "extracted-compile", policy)
        if pdf.read_bytes() != extracted_pdf.read_bytes():
            raise CandidatePaperError("archive recompilation PDF differs")
        shutil.copyfile(pdf, staging / "candidate.pdf")
        _write(staging / "projection.json", projection_raw)
        source_manifest_value = _manifest(tree)
        _write(staging / "paper_source_manifest.json", canonical_json_bytes(source_manifest_value))
        try:
            metadata = generated_arxiv_metadata(template, candidate)
        except ValueError as error:
            raise CandidatePaperError("candidate metadata differs") from error
        _write(staging / "arxiv_metadata.json", canonical_json_bytes(metadata))
        qa = _pdf_qa(staging / "candidate.pdf", staging / "qa_renders", template["title"], policy)
        _write(staging / "qa_render_manifest.json", canonical_json_bytes(qa))
        receipt = {"actual_go_sha256": candidate["authority"]["actual_go_sha256"], "arxiv_metadata_sha256": sha256_path(staging / "arxiv_metadata.json"), "candidate_contract_sha256": sha256_path(root / "paper/v4_measurement/candidate_contract.json"), "evidence_manifest_sha256": candidate["evidence_closure"]["sha256"], "paper_pdf_sha256": sha256_path(staging / "candidate.pdf"), "paper_source_manifest_sha256": sha256_path(staging / "paper_source_manifest.json"), "projection_sha256": hashlib.sha256(projection_raw).hexdigest(), "qa_render_manifest_sha256": sha256_path(staging / "qa_render_manifest.json"), "schema_version": "anachron-v4-candidate-receipt-v1", "source_archive_sha256": archive_digest, "v3_included_count": 0}
        _write(staging / "candidate_receipt.json", canonical_json_bytes(receipt))
        if tuple(sorted(item.name for item in staging.iterdir() if item.name not in {"compile", "extracted", "extracted-compile"})) != tuple(sorted(CANDIDATE_COMPLETION)):
            raise CandidatePaperError("candidate completion set differs")
        for name in ("compile", "extracted", "extracted-compile"):
            shutil.rmtree(staging / name)
        _publish_no_replace(staging, output)
        return {"candidate_receipt": receipt, "pdf_verification": qa, "protocol_tag": source["release"]["tag"]}
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol-root", type=Path, required=True)
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--projection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--tectonic", type=Path, required=True)
    values = parser.parse_args(arguments)
    print(json.dumps(build_candidate(values.protocol_root, values.source_manifest, values.projection, values.output, values.tectonic), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
