"""Build a deterministic, local-only v5 finite-panel candidate paper."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from anachron.v5_candidate_common import (
    CandidateProjectionError,
    generated_arxiv_metadata,
    sha256_bytes,
    validate_candidate_projection,
)
from anachron.v5_candidate_release_common import candidate_root_profile, sha256
from anachron.v5_contract import presentation_source_closure
from anachron.v5_custody import (
    ByteBudget,
    V5CustodyError,
    capture_regular,
    discard_staging_root,
    publish_staging_root,
    scandir_exact,
    write_create_only,
)
from anachron.v5_paths import (
    V5PathError,
    admit_create_only_external_output,
    admit_external_regular_input,
    admit_repository_root,
    fsync_directory,
)
from anachron.v5_registry import canonical_json_bytes, strict_json_loads


class CandidatePaperError(ValueError):
    """Raised when a v5 candidate cannot be rendered deterministically."""


_CANDIDATE_FILES = (
    "arxiv_metadata.json", "candidate.pdf", "candidate_receipt.json",
    "paper_source_manifest.json", "projection.json", "qa_render_manifest.json",
    "qa_renders", "source.zip",
)


def _json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = capture_regular(path, label, 1_048_576).raw
        value = strict_json_loads(raw, label)
    except (OSError, V5CustodyError, ValueError) as error:
        raise CandidatePaperError(f"{label} cannot be read") from error
    if type(value) is not dict or raw != canonical_json_bytes(value):
        raise CandidatePaperError(f"{label} is not canonical JSON")
    return value, raw


def _escape(value: str) -> str:
    return value.replace("\\", r"\textbackslash{}").replace("&", r"\&").replace("%", r"\%").replace("_", r"\_").replace("#", r"\#")


def _table(projection: dict[str, Any]) -> str:
    rows = []
    for row in projection["groups"]:
        exposure = "undefined" if row["conditional_exposure_rate_fixed_decimal"] is None else row["conditional_exposure_rate_fixed_decimal"]
        rows.append(f"{_escape(row['model'])} & {_escape(row['mode'])} & {row['adherence_numerator']}/{row['adherence_denominator']} & {row['conditional_exposure_numerator']}/{row['conditional_exposure_denominator']} ({exposure}) " + r"\\")
    return "\n".join(rows)


def _tex(metadata: dict[str, Any], projection: dict[str, Any]) -> str:
    return "\n".join((
        r"\documentclass[10pt]{article}", r"\usepackage[margin=0.85in]{geometry}", r"\usepackage[T1]{fontenc}", r"\usepackage{booktabs}", r"\title{" + _escape(metadata["title"]) + r"}", r"\author{" + _escape(metadata["author"]) + r"}", r"\date{}", r"\begin{document}", r"\maketitle",
        r"\begin{abstract}", _escape(metadata["abstract"]), r"\end{abstract}",
        r"\section{Question and scope}",
        "This paper asks a narrow question: when an agent makes a first tool call for a dated synthetic card, does it follow the tool schema, and conditional on adherence, does the harness-returned record fall after the card cutoff? The result is a controlled finite-panel trace measurement, not an assessment of answer quality, live-web behavior, source authenticity, population prevalence, or model ranking.",
        r"\section{Protocol}",
        "The protocol schedules 64 primary traces: eight carried-forward synthetic cards, two modes, two models, and two fixed repetitions. The exact accepted v4 card bytes are reused with fresh precommitted seeds. The two compatibility traces are excluded from all metrics. Repetitions are traces, not independent cases. A model-supplied date never controls retrieval: a mismatch is a non-adherence outcome and gets no retrieval. Conditional exposure is undefined, rather than zero, for non-adherent first calls.",
        r"\section{Replay-verified results}",
        r"\begin{center}\begin{tabular}{llll}\toprule Model & Mode & First adherence & Conditional exposure \\ \midrule", _table(projection), r"\bottomrule\end{tabular}\end{center}",
        f"The replay-verified primary schedule contains {projection['scheduled']} traces and {len(projection['paired_contrasts'])} eligible adherence-complete paired contrasts. All first and final non-adherence categories are retained in the machine-readable projection; the table reports their denominators rather than imputing exposure for any non-adherent call.",
        r"\section{Limitations and custody}",
        "The prior v4 pilot failed operationally and contributes zero empirical rows, rates, raw outputs, or derived statistics here. V5 is a prospectively frozen successor, not an outcome-unseen study. Self-custody supports internal consistency and byte replay, but cannot independently prove raw-response provenance or detect a coherent rewrite of every locally held artifact. No significance, general-population, answer-quality, live-web, authenticity, or model-ranking claim is made.",
        r"\section{Authorship and AI assistance}", _escape(metadata["ai_assistance_disclosure"]), "Lester Leong must review and approve the exact manuscript, metadata, citations, and this disclosure before local release.",
        r"\bibliographystyle{plain}", r"\bibliography{references}", r"\end{document}", "",
    ))


def _tectonic_environment() -> dict[str, str]:
    cache = os.environ.get("TECTONIC_CACHE_DIR")
    if cache is None or not Path(cache).is_absolute() or not Path(cache).is_dir():
        raise CandidatePaperError("dedicated Tectonic cache is unavailable")
    cache_root = Path(cache)
    isolated_home = cache_root.parent
    environment = {
        "HOME": str(isolated_home),
        "SOURCE_DATE_EPOCH": "0",
        "TECTONIC_CACHE_DIR": str(cache_root),
        "XDG_CACHE_HOME": str(isolated_home),
    }
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot")
        if type(system_root) is not str or not system_root:
            raise CandidatePaperError("Windows system root is unavailable")
        program_data = os.environ.get("ProgramData")
        if type(program_data) is not str or not program_data:
            raise CandidatePaperError("Windows program data is unavailable")
        user_profile = os.environ.get("USERPROFILE")
        if type(user_profile) is not str or not user_profile:
            raise CandidatePaperError("Windows user profile is unavailable")
        environment.update(
            {
                "ComSpec": os.environ.get("ComSpec", str(Path(system_root) / "System32" / "cmd.exe")),
                "APPDATA": str(isolated_home),
                "HOMEDRIVE": isolated_home.drive,
                "HOMEPATH": isolated_home.root,
                "LOCALAPPDATA": str(isolated_home),
                "PATH": str(Path(system_root) / "System32"),
                "PATHEXT": os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD"),
                "ProgramData": program_data,
                "SystemRoot": system_root,
                "SystemDrive": Path(system_root).drive,
                "TEMP": str(isolated_home),
                "TMP": str(isolated_home),
                "USERPROFILE": user_profile,
                "WINDIR": os.environ.get("WINDIR", system_root),
            }
        )
    else:
        environment["PATH"] = "/usr/bin:/bin"
    return environment


def _bounded_run(command: list[str], source: Path, label: str, environment: dict[str, str]) -> str:
    try:
        result = subprocess.run(command, cwd=source, env=environment, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise CandidatePaperError(f"{label} failed") from error
    if result.returncode or len(result.stdout) + len(result.stderr) > 1_048_576:
        raise CandidatePaperError(f"{label} failed")
    return result.stdout.decode("utf-8", "replace")


def _verify_tectonic(tectonic: Path, contract: dict[str, Any], environment: dict[str, str]) -> None:
    policy, identity = contract["resource_policy"], contract["tectonic"]
    expected = identity["windows_executable_sha256"] if os.name == "nt" else identity["linux_executable_sha256"]
    try:
        executable = capture_regular(tectonic, "pinned Tectonic executable", policy["tectonic_executable_max_bytes"])
    except V5CustodyError as error:
        raise CandidatePaperError("pinned Tectonic executable differs") from error
    if executable.sha256 != expected:
        raise CandidatePaperError("pinned Tectonic executable differs")
    if f"Tectonic {identity['version']}" not in _bounded_run([str(tectonic), "--version"], tectonic.parent, "Tectonic version check", environment):
        raise CandidatePaperError("pinned Tectonic version differs")


def _run_tectonic(tectonic: Path, source: Path, output: Path, environment: dict[str, str]) -> Path:
    _bounded_run([str(tectonic), "--only-cached", "--keep-logs", "--outdir", str(output), "main.tex"], source, "Tectonic compilation", environment)
    pdf = output / "main.pdf"
    if not pdf.is_file():
        raise CandidatePaperError("Tectonic did not produce a PDF")
    return pdf


def _zip(source: Path, output: Path, allowlist: list[str], policy: dict[str, int], budget: ByteBudget) -> str:
    members = {
        name: capture_regular(source / name, "candidate source member", policy["source_file_max_bytes"])
        for name in allowlist
    }
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "x", compression=zipfile.ZIP_DEFLATED, strict_timestamps=True) as archive:
        for name in allowlist:
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type, info.create_system, info.external_attr = zipfile.ZIP_DEFLATED, 3, 0o100644 << 16
            archive.writestr(info, members[name].raw)
    raw = payload.getvalue()
    if len(raw) > policy["source_archive_max_bytes"]:
        raise CandidatePaperError("candidate source archive exceeds byte cap")
    write_create_only(output, raw, "candidate source archive", budget)
    return hashlib.sha256(raw).hexdigest()


def _archive_manifest(source: Path, allowlist: list[str], policy: dict[str, int]) -> dict[str, Any]:
    return {"files": [{"path": name, "sha256": sha256(source / name, policy["source_file_max_bytes"])} for name in allowlist], "schema_version": "anachron-v5-paper-source-manifest-v1", "v4_included_count": 0}


def _extract_and_compare(archive: Path, source: Path, extracted: Path, allowlist: list[str], policy: dict[str, int]) -> None:
    try:
        captured_archive = capture_regular(archive, "candidate source archive", policy["source_archive_max_bytes"])
        extract_budget = ByteBudget(policy["source_file_max_bytes"] * len(allowlist), len(allowlist))
        with zipfile.ZipFile(io.BytesIO(captured_archive.raw)) as bundle:
            infos = bundle.infolist()
            if [item.filename for item in infos] != allowlist:
                raise CandidatePaperError("source archive allowlist differs")
            for item in infos:
                if item.is_dir() or item.file_size > policy["source_file_max_bytes"] or item.compress_size > policy["source_file_max_bytes"] or item.date_time != (1980, 1, 1, 0, 0, 0) or item.external_attr != 0o100644 << 16:
                    raise CandidatePaperError("source archive metadata differs")
                target = extracted / item.filename
                target.parent.mkdir(parents=True, exist_ok=True)
                raw = bytearray()
                with bundle.open(item) as reader:
                    while chunk := reader.read(65536):
                        if len(raw) + len(chunk) > policy["source_file_max_bytes"]:
                            raise CandidatePaperError("source archive member exceeds byte cap")
                        raw.extend(chunk)
                extracted_digest = _write_staged(target, bytes(raw), "extracted source member", policy["source_file_max_bytes"], extract_budget)
                if extracted_digest != sha256(source / item.filename, policy["source_file_max_bytes"]):
                    raise CandidatePaperError("source archive content differs")
    except (OSError, zipfile.BadZipFile) as error:
        raise CandidatePaperError("source archive cannot be verified") from error


def _renders(pdf: Path, output: Path, temporary: Path, policy: dict[str, int]) -> dict[str, Any]:
    try:
        import fitz
        from PIL import Image
    except ImportError as error:
        raise CandidatePaperError("PDF QA dependencies are unavailable") from error
    document = fitz.open(pdf)
    if not 1 <= len(document) <= policy["pdf_max_pages"]:
        raise CandidatePaperError("candidate PDF page count differs")
    rows, aggregate = [], 0
    budget = ByteBudget(policy["render_aggregate_max_bytes"], policy["pdf_max_pages"])
    for index, page in enumerate(document, 1):
        target = output / f"page-{index}.png"
        transient = temporary / f"qa-page-{index}.png"
        page.get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False).save(transient)
        with Image.open(transient) as image:
            pixels = image.width * image.height
            if image.width < 400 or image.height < 400 or pixels > policy["render_max_pixels"]:
                raise CandidatePaperError("candidate PDF render dimensions differ")
            width, height = image.width, image.height
        try:
            rendered = capture_regular(transient, "candidate PDF render", policy["render_max_bytes"])
        except V5CustodyError as error:
            raise CandidatePaperError("candidate PDF render exceeds byte cap") from error
        size = rendered.size_bytes
        aggregate += size
        if size > policy["render_max_bytes"] or aggregate > policy["render_aggregate_max_bytes"]:
            raise CandidatePaperError("candidate PDF render exceeds byte cap")
        _write_staged(target, rendered.raw, "candidate PDF render", policy["render_max_bytes"], budget)
        rows.append({"height": height, "path": target.name, "sha256": rendered.sha256, "size_bytes": size, "width": width})
    return {"page_count": len(rows), "renders": rows, "schema_version": "anachron-v5-pdf-render-manifest-v1"}


def _candidate_completion(directory: Path) -> None:
    try:
        names = scandir_exact(
            directory,
            _CANDIDATE_FILES,
            len(_CANDIDATE_FILES),
            "candidate staging",
        )
    except V5CustodyError as error:
        raise CandidatePaperError("candidate staging topology differs") from error
    if tuple(sorted(names)) != _CANDIDATE_FILES:
        raise CandidatePaperError("candidate staging topology differs")


def _write_staged(path: Path, raw: bytes, label: str, maximum: int, budget: ByteBudget) -> str:
    if len(raw) > maximum:
        raise CandidatePaperError(f"{label} exceeds byte cap")
    try:
        return write_create_only(path, raw, label, budget)
    except V5CustodyError as error:
        raise CandidatePaperError(f"{label} cannot be written") from error


def _discard(root: Path, label: str) -> None:
    if not root.exists():
        return
    try:
        discard_staging_root(root, label, maximum_entries=64, maximum_depth=2)
    except V5CustodyError as error:
        raise CandidatePaperError(f"{label} cannot be removed") from error


def build_candidate(repository_root: Path, projection: Path, output: Path, tectonic: Path) -> dict[str, Any]:
    root = admit_repository_root(repository_root)
    try:
        projection = admit_external_regular_input(projection, root, "candidate projection")
        destination = admit_create_only_external_output(output, root, "candidate output")
    except V5PathError as error:
        raise CandidatePaperError(str(error)) from error
    candidate, candidate_raw = _json(projection, "candidate projection")
    try:
        validate_candidate_projection(candidate)
    except CandidateProjectionError as error:
        raise CandidatePaperError("candidate projection differs") from error
    template, _ = _json(root / "paper/v5_measurement/candidate_manuscript_template.json", "manuscript template")
    contract, contract_raw = _json(root / "paper/v5_measurement/candidate_contract.json", "candidate contract")
    policy, allowlist = contract.get("resource_policy"), contract.get("source_archive_allowlist")
    if type(policy) is not dict or type(allowlist) is not list or allowlist != ["README.md", "figures/primary_adherence.tex", "main.tex", "references.bib"]:
        raise CandidatePaperError("candidate contract differs")
    environment = _tectonic_environment()
    _verify_tectonic(tectonic, contract, environment)
    metadata = generated_arxiv_metadata(template, candidate)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent))
    try:
        profile = candidate_root_profile(policy)
        candidate_budget = profile.budget()
        source = staging / "source"
        (source / "figures").mkdir(parents=True)
        source_budget = ByteBudget(policy["source_file_max_bytes"] * 4, 4)
        _write_staged(source / "README.md", b"Offline v5 finite-panel candidate source. Raw traces are deliberately excluded.\n", "candidate source README", policy["source_file_max_bytes"], source_budget)
        _write_staged(source / "main.tex", _tex(metadata, candidate["projection"]).encode("utf-8"), "candidate source TeX", policy["source_file_max_bytes"], source_budget)
        references = capture_regular(root / "paper/v5_measurement/candidate_references.bib", "candidate references", policy["source_file_max_bytes"])
        _write_staged(source / "references.bib", references.raw, "candidate source references", policy["source_file_max_bytes"], source_budget)
        _write_staged(source / "figures" / "primary_adherence.tex", b"% Values are generated in main.tex from the answer-free projection.\n", "candidate source figure", policy["source_file_max_bytes"], source_budget)
        rendered, extracted, extracted_rendered = staging / "rendered", staging / "extracted", staging / "extracted-rendered"
        rendered.mkdir()
        pdf = _run_tectonic(tectonic, source, rendered, environment)
        try:
            capture_regular(pdf, "candidate PDF", policy["pdf_max_bytes"])
        except V5CustodyError as error:
            raise CandidatePaperError("candidate PDF exceeds byte cap") from error
        archive_digest = _zip(source, staging / "source.zip", allowlist, policy, candidate_budget)
        _extract_and_compare(staging / "source.zip", source, extracted, allowlist, policy)
        extracted_rendered.mkdir()
        extracted_pdf = _run_tectonic(tectonic, extracted, extracted_rendered, environment)
        if sha256(pdf, policy["pdf_max_bytes"]) != sha256(extracted_pdf, policy["pdf_max_bytes"]):
            raise CandidatePaperError("source archive recompilation PDF differs")
        _write_staged(staging / "projection.json", candidate_raw, "candidate projection", profile.member_cap("projection.json"), candidate_budget)
        _write_staged(staging / "arxiv_metadata.json", canonical_json_bytes(metadata), "candidate metadata", profile.member_cap("arxiv_metadata.json"), candidate_budget)
        _write_staged(staging / "paper_source_manifest.json", canonical_json_bytes(_archive_manifest(source, allowlist, policy)), "candidate source manifest", profile.member_cap("paper_source_manifest.json"), candidate_budget)
        renders = staging / "qa_renders"
        renders.mkdir()
        qa = _renders(pdf, renders, rendered, policy)
        _write_staged(staging / "qa_render_manifest.json", canonical_json_bytes(qa), "candidate render manifest", profile.member_cap("qa_render_manifest.json"), candidate_budget)
        candidate_pdf = capture_regular(pdf, "candidate PDF", profile.member_cap("candidate.pdf"))
        _write_staged(staging / "candidate.pdf", candidate_pdf.raw, "candidate PDF", profile.member_cap("candidate.pdf"), candidate_budget)
        receipt = {
            "actual_go_sha256": candidate["authority"]["conditional_go_sha256"], "archive_sha256": archive_digest,
            "arxiv_metadata_sha256": sha256(staging / "arxiv_metadata.json", policy["candidate_projection_max_bytes"]),
            "authority_contract_sha256": candidate["authority"]["authority_contract_sha256"], "candidate_contract_sha256": sha256_bytes(contract_raw),
            "carry_forward_sha256": candidate["authority"]["carry_forward_sha256"], "compatibility_plan_sha256": candidate["authority"]["compatibility_plan_sha256"],
            "evidence_manifest_sha256": candidate["evidence_manifest_sha256"], "full_plan_sha256": candidate["authority"]["full_plan_sha256"],
            "materialization_receipt_sha256": candidate["authority"]["materialization_receipt_sha256"], "paper_pdf_sha256": sha256(staging / "candidate.pdf", policy["pdf_max_bytes"]),
            "paper_source_manifest_sha256": sha256(staging / "paper_source_manifest.json", policy["source_manifest_max_bytes"]), "presentation_source_closure_sha256": presentation_source_closure(root), "projection_sha256": sha256_bytes(candidate_raw),
            "qa_render_manifest_sha256": sha256(staging / "qa_render_manifest.json", policy["candidate_projection_max_bytes"]), "runtime_identity_sha256": candidate["authority"]["runtime_identity_sha256"],
            "schema_version": "anachron-v5-candidate-receipt-v2", "source_manifest_sha256": candidate["authority"]["source_manifest_sha256"], "v4_included_count": 0,
        }
        _write_staged(staging / "candidate_receipt.json", canonical_json_bytes(receipt), "candidate receipt", profile.member_cap("candidate_receipt.json"), candidate_budget)
        for transient in (source, rendered, extracted, extracted_rendered):
            _discard(transient, "candidate transient")
        _candidate_completion(staging)
        publish_staging_root(staging, destination, "candidate output")
        fsync_directory(destination.parent, "candidate output parent")
        return receipt
    except Exception:
        if staging.exists():
            _discard(staging, "candidate staging")
        raise


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--projection", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--tectonic", required=True, type=Path)
    values = parser.parse_args(arguments)
    build_candidate(**vars(values))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
