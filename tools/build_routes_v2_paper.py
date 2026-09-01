"""Build a final Routes v2 archive only from a replay-verified analysis root."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

if __package__ != "tools":
    raise SystemExit("Run this builder as a module: python -m tools.build_routes_v2_paper ...")

from tools.render_routes_results import render_verified


def sha(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def build(*, tectonic: Path, analysis_root: Path, frozen_root: Path, output_dir: Path) -> tuple[Path, Path]:
    """Replay evidence before TeX, then archive only reproducible final-paper inputs."""
    text, receipts = render_verified(analysis_root.resolve(), frozen_root.resolve())
    source_paper = frozen_root.resolve() / "paper" / "routes_v2"
    if not (source_paper / "routes_v2.tex").is_file():
        raise ValueError("frozen root has no Routes v2 paper source")
    output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="routes-v2-paper-") as temporary:
        paper = Path(temporary) / "paper"
        shutil.copytree(source_paper, paper, ignore=shutil.ignore_patterns("build", "dist", "generated"))
        generated = paper / "generated"
        generated.mkdir()
        (generated / "results.tex").write_bytes(text.encode("utf-8"))
        (generated / "analysis_replay_receipt.json").write_text(
            json.dumps(receipts["analysis_replay"], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        (generated / "render_receipt.json").write_text(
            json.dumps(receipts["render"], ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
        shutil.copyfile(frozen_root / "research" / "routes-v2" / "contract.json", paper / "provenance" / "contract.json")
        shutil.copyfile(frozen_root / "research" / "routes-v2" / "PROTOCOL.md", paper / "protocol_snapshot.md")
        build_dir = Path(temporary) / "build"
        subprocess.run(
            [str(tectonic), "--keep-logs", "--keep-intermediates", "--outdir", str(build_dir), str(paper / "routes_v2.tex")],
            cwd=paper,
            check=True,
        )
        members = (
            "routes_v2.tex", "generated/results.tex", "generated/analysis_replay_receipt.json",
            "generated/render_receipt.json", "protocol_snapshot.md", "provenance/contract.json",
        )
        archive = output_dir / "anachron_routes_v2_arxiv_source.tar.gz"
        with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w") as tar:
            for member in members:
                source = paper / member
                info = tar.gettarinfo(str(source), arcname=member)
                info.mtime = info.uid = info.gid = 0
                info.uname = info.gname = ""
                with source.open("rb") as handle:
                    tar.addfile(info, handle)
        with tarfile.open(archive, "r:gz") as tar:
            if tuple(item.name for item in tar.getmembers()) != members:
                raise ValueError("final archive membership drifted")
        pdf = output_dir / "routes_v2.pdf"
        shutil.copyfile(build_dir / "routes_v2.pdf", pdf)
    receipt = output_dir / "build_receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema_version": "routes-v2-paper-build-receipt",
                "analysis_replay_receipt_sha256": receipts["analysis_replay"]["receipt_sha256"],
                "render_receipt_sha256": receipts["render"]["receipt_sha256"],
                "pdf_sha256": sha(pdf),
                "archive_sha256": sha(archive),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )
    return archive, receipt


def main() -> None:
    parser = argparse.ArgumentParser(description="Build only a replay-verified final Routes v2 archive.")
    parser.add_argument("--tectonic", required=True, type=Path)
    parser.add_argument("--analysis-root", required=True, type=Path)
    parser.add_argument("--frozen-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    archive, receipt = build(
        tectonic=args.tectonic,
        analysis_root=args.analysis_root,
        frozen_root=args.frozen_root,
        output_dir=args.output_dir,
    )
    print(f"Archive: {archive}\nReceipt: {receipt}")


if __name__ == "__main__":
    main()
