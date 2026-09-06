"""Build a disposable v5 candidate-source fixture for presentation CI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from anachron.v5_measurement import run_measurement
from anachron.v5_registry import canonical_json_bytes, load_v5_registry
from tests.test_v5_measurement import measurement_plan_and_go
from tools import build_v5_measurement_candidate_paper as builder
from tools import project_v5_measurement_candidate as projector


def injected_transport(
    _endpoint: str, path: str, payload: bytes | None, _timeout: int
) -> bytes:
    """Return the fixed synthetic measurement responses used by the E2E lifecycle."""

    models = [
        {
            "digest": "845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e",
            "name": "qwen2.5:7b",
        },
        {
            "digest": "bdbd181c33f2ed1b31c972991882db3cf4d192569092138a7d29e973cd9debe8",
            "name": "qwen3:14b-q4_K_M",
        },
    ]
    if path == "/api/version":
        return b'{"version":"0.33.2"}\n'
    if path == "/api/tags":
        return canonical_json_bytes({"models": models})
    request = json.loads(payload.decode("utf-8"))
    if "tools" in request:
        return canonical_json_bytes(
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "anachron_search",
                                "arguments": {"query": "synthetic bulletin"},
                            }
                        }
                    ],
                }
            }
        )
    return canonical_json_bytes(
        {"message": {"role": "assistant", "content": "excluded from paper content"}}
    )


def materialize_fixture(repository_root: Path, output: Path) -> Path:
    """Create the E2E synthetic plan, evidence, projection, and four source files."""

    if output.exists():
        raise ValueError("presentation fixture output must be absent")
    output.mkdir(parents=True)
    _, cards = load_v5_registry(repository_root)
    plan, go = measurement_plan_and_go(repository_root, cards, output)
    evidence = output / "evidence"
    run_measurement(plan, go, evidence, repository_root=repository_root, transport=injected_transport)
    plans = plan.parent
    projection = output / "projection.json"
    candidate = projector.project_and_write_candidate(
        repository_root,
        source_manifest=plans / "source_manifest.json",
        carry_forward=plans / "carry_forward.json",
        runtime_identity=plans / "runtime_identity.json",
        conditional_go=go,
        materialization_receipt=plans / "materialization_receipt.json",
        evidence=evidence,
        output=projection,
    )
    source = output / "source"
    builder.materialize_candidate_source(repository_root, candidate, source)
    expected = {
        "README.md",
        "figures/primary_adherence.tex",
        "main.tex",
        "references.bib",
    }
    actual = {path.relative_to(source).as_posix() for path in source.rglob("*") if path.is_file()}
    if actual != expected:
        raise ValueError("presentation fixture source tree differs")
    return source


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    values = parser.parse_args(arguments)
    materialize_fixture(values.repository_root, values.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
