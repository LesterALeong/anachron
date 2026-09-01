"""Build the Routes v1 pre-results manuscript and a clean arXiv source archive."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PAPER_DIRECTORY = REPOSITORY_ROOT / "paper" / "routes_v1"
BUILD_DIRECTORY = PAPER_DIRECTORY / "build"
DIST_DIRECTORY = PAPER_DIRECTORY / "dist"
MAIN_TEX = PAPER_DIRECTORY / "routes_v1.tex"
ARCHIVE_MEMBERS = (
    "routes_v1.tex",
    "references.bib",
    "routes_v1.bbl",
    "generated/results_placeholder.tex",
)
SAFE_ARCHIVE_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:/[A-Za-z0-9][A-Za-z0-9_.-]*)*$")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_tectonic(tectonic: Path) -> None:
    if not tectonic.is_file():
        raise FileNotFoundError(f"Tectonic executable not found: {tectonic}")
    if BUILD_DIRECTORY.exists():
        shutil.rmtree(BUILD_DIRECTORY)
    BUILD_DIRECTORY.mkdir(parents=True)
    command = [
        str(tectonic),
        "--keep-logs",
        "--keep-intermediates",
        "--outdir",
        str(BUILD_DIRECTORY),
        str(MAIN_TEX),
    ]
    subprocess.run(command, cwd=PAPER_DIRECTORY, check=True)


def validate_archive_members(members: tuple[str, ...]) -> None:
    for member in members:
        if not SAFE_ARCHIVE_MEMBER.fullmatch(member):
            raise ValueError(f"Unsafe arXiv archive member name: {member}")


def build_archive() -> Path:
    validate_archive_members(ARCHIVE_MEMBERS)
    DIST_DIRECTORY.mkdir(parents=True, exist_ok=True)
    archive_path = DIST_DIRECTORY / "anachron_routes_v1_arxiv_source.tar.gz"
    if archive_path.exists():
        archive_path.unlink()
    source_paths = {
        "routes_v1.tex": PAPER_DIRECTORY / "routes_v1.tex",
        "references.bib": PAPER_DIRECTORY / "references.bib",
        "routes_v1.bbl": BUILD_DIRECTORY / "routes_v1.bbl",
        "generated/results_placeholder.tex": PAPER_DIRECTORY / "generated" / "results_placeholder.tex",
    }
    for member, source in source_paths.items():
        if not source.is_file():
            raise FileNotFoundError(f"Required arXiv source missing for {member}: {source}")
    with (
        archive_path.open("wb") as raw_archive,
        gzip.GzipFile(fileobj=raw_archive, mode="wb", mtime=0) as compressed_archive,
        tarfile.open(fileobj=compressed_archive, mode="w") as archive,
    ):
        for member in ARCHIVE_MEMBERS:
            source = source_paths[member]
            archive_info = archive.gettarinfo(str(source), arcname=member)
            archive_info.mtime = 0
            archive_info.uid = 0
            archive_info.gid = 0
            archive_info.uname = ""
            archive_info.gname = ""
            with source.open("rb") as source_file:
                archive.addfile(archive_info, source_file)
    with tarfile.open(archive_path, "r:gz") as archive:
        archived_names = tuple(member.name for member in archive.getmembers())
    if archived_names != ARCHIVE_MEMBERS:
        raise ValueError(f"Unexpected arXiv archive members: {archived_names}")
    return archive_path


def write_receipt(tectonic: Path, archive_path: Path) -> Path:
    pdf_path = BUILD_DIRECTORY / "routes_v1.pdf"
    log_path = BUILD_DIRECTORY / "routes_v1.log"
    if not pdf_path.is_file() or not log_path.is_file():
        raise FileNotFoundError("Tectonic did not produce the expected PDF and log")
    version = subprocess.run(
        [str(tectonic), "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    distribution_archives = tuple(tectonic.parent.parent.glob("tectonic-*.zip"))
    distribution_archive_hash = (
        sha256(distribution_archives[0]) if len(distribution_archives) == 1 else None
    )
    receipt = {
        "tectonic_path": str(tectonic),
        "tectonic_sha256": sha256(tectonic),
        "tectonic_distribution_zip_sha256": distribution_archive_hash,
        "tectonic_version": version,
        "pdf_sha256": sha256(pdf_path),
        "log_sha256": sha256(log_path),
        "archive_sha256": sha256(archive_path),
        "archive_members": list(ARCHIVE_MEMBERS),
    }
    receipt_path = DIST_DIRECTORY / "build_receipt.json"
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt_path


def main() -> None:
    raise SystemExit("Routes v1 paper build is BLOCKED; use the receipt-bound Routes v2 workflow.")
    parser = argparse.ArgumentParser()
    parser.add_argument("--tectonic", required=True, type=Path)
    arguments = parser.parse_args()
    run_tectonic(arguments.tectonic)
    archive_path = build_archive()
    receipt_path = write_receipt(arguments.tectonic, archive_path)
    print(f"PDF: {BUILD_DIRECTORY / 'routes_v1.pdf'}")
    print(f"Archive: {archive_path}")
    print(f"Receipt: {receipt_path}")


if __name__ == "__main__":
    main()
