"""Read-only, fail-closed MediaWiki revision discovery for Routes v1."""

from __future__ import annotations

import argparse
import csv
import difflib
import hashlib
import io
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

from anachron.routes.schema import (
    ContractValidationError,
    load_contract,
    validate_contract_document,
)

_API_ENDPOINT = "https://en.wikipedia.org/w/api.php"
_USER_AGENT = "AnachronRoutes/0.1 (https://github.com/LesterALeong/anachron)"
_MEDIAWIKI_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}\Z")
_Fetch = Callable[[str, float], bytes]


class SourceDiscoveryError(ValueError):
    """Raised when source discovery cannot prove an exact immutable revision."""


class SourceIneligibleError(SourceDiscoveryError):
    """Raised when a declared topic has no admissible revision pair."""


@dataclass(frozen=True)
class UpstreamFetchReceipt:
    """Exact requested bytes and the final URL returned by an upstream service."""

    body: bytes
    resolved_url: str


_ArtifactFetch = Callable[[str, float], UpstreamFetchReceipt]


@dataclass(frozen=True)
class RevisionEvidence:
    """One immutable revision plus hashes required for independent audit."""

    revision_id: int
    timestamp: str
    mediawiki_sha1: str
    revision_url: str
    raw_response_sha256: str
    content_sha256: str
    content: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "revision_id": self.revision_id,
            "timestamp": self.timestamp,
            "mediawiki_sha1": self.mediawiki_sha1,
            "revision_url": self.revision_url,
            "raw_response_sha256": self.raw_response_sha256,
            "content_sha256": self.content_sha256,
            "content": self.content,
        }


@dataclass(frozen=True)
class DiscoveryArtifact:
    """Review input for one declared topic, without an outcome label."""

    title: str
    cutoff_year: int
    boundary_timestamp: str
    strict_revision: RevisionEvidence
    post_snapshot_horizon_days: int
    post_snapshot: RevisionEvidence

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "routes-v1-source-discovery",
            "title": self.title,
            "cutoff_year": self.cutoff_year,
            "boundary_timestamp": self.boundary_timestamp,
            "strict_revision": self.strict_revision.to_dict(),
            "post_snapshot_horizon_days": self.post_snapshot_horizon_days,
            "post_snapshot": self.post_snapshot.to_dict(),
            "snapshot_diff": "".join(
                difflib.unified_diff(
                    self.strict_revision.content.splitlines(keepends=True),
                    self.post_snapshot.content.splitlines(keepends=True),
                    fromfile=f"oldid:{self.strict_revision.revision_id}",
                    tofile=f"oldid:{self.post_snapshot.revision_id}",
                )
            ),
        }


def _canonical_utc(value: str, path: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise SourceDiscoveryError(f"{path} must be a canonical UTC timestamp")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise SourceDiscoveryError(f"{path} must be a canonical UTC timestamp") from error
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise SourceDiscoveryError(f"{path} must be a canonical UTC timestamp")
    return parsed


def _sha256(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _immutable_revision_url(title: str, revision_id: int) -> str:
    return "https://en.wikipedia.org/w/index.php?" + urlencode(
        {"title": title, "oldid": str(revision_id)}
    )


def _default_fetch_bytes(url: str, timeout_seconds: float) -> bytes:
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise SourceDiscoveryError(f"source returned HTTP {response.status}")
            if response.geturl() != url:
                raise SourceDiscoveryError("source HTTP redirect is not admissible")
            body = response.read()
    except (HTTPError, URLError, OSError) as error:
        raise SourceDiscoveryError(f"source request failed: {error}") from error
    if not body:
        raise SourceDiscoveryError("source returned an empty response")
    return body


def _default_fetch_artifact(
    url: str, timeout_seconds: float
) -> UpstreamFetchReceipt:
    request = Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            if response.status != 200:
                raise SourceDiscoveryError(f"source returned HTTP {response.status}")
            body = response.read()
            resolved_url = response.geturl()
    except (HTTPError, URLError, OSError) as error:
        raise SourceDiscoveryError(f"source request failed: {error}") from error
    if not body:
        raise SourceDiscoveryError("source returned an empty response")
    return UpstreamFetchReceipt(body=body, resolved_url=resolved_url)


def _validate_huggingface_cache_redirect(
    resolved_url: str, revision: str
) -> tuple[str, str]:
    parsed = urlparse(resolved_url)
    expected_path = (
        "/api/resolve-cache/datasets/yachuanliu/ExAnte/"
        f"{revision}/exante_wiki.csv"
    )
    if (
        parsed.scheme != "https"
        or parsed.netloc != "huggingface.co"
        or parsed.path != expected_path
        or parsed.fragment
    ):
        raise SourceDiscoveryError("Hugging Face cache redirect is not the exact pinned path")
    query = parse_qs(parsed.query, strict_parsing=True)
    etags = query.get("etag")
    if etags is None or len(etags) != 1 or not etags[0]:
        raise SourceDiscoveryError("Hugging Face cache redirect lacks one non-empty etag")
    return resolved_url, etags[0]


def _validate_upstream_receipt(
    name: str, upstream: dict[str, Any], receipt: UpstreamFetchReceipt
) -> tuple[str, str | None]:
    if not isinstance(receipt, UpstreamFetchReceipt) or not isinstance(receipt.body, bytes):
        raise SourceDiscoveryError("upstream fetcher must return an UpstreamFetchReceipt")
    if not receipt.body:
        raise SourceDiscoveryError("pinned upstream artifact is empty")
    requested_url = upstream["artifact_url"]
    if name == "exante_github":
        if receipt.resolved_url != requested_url:
            raise SourceDiscoveryError("GitHub artifact redirect is not admissible")
        return receipt.resolved_url, None
    if name == "exante_huggingface":
        return _validate_huggingface_cache_redirect(
            receipt.resolved_url, upstream["revision"]
        )
    raise SourceDiscoveryError("unknown upstream artifact")


def _decode_response(
    raw: bytes, expected_title: str
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SourceDiscoveryError("MediaWiki returned invalid UTF-8 JSON") from error
    if not isinstance(payload, dict):
        raise SourceDiscoveryError("MediaWiki response must be an object")
    if "error" in payload or "warnings" in payload:
        raise SourceDiscoveryError("MediaWiki returned an API error or warning")
    query = payload.get("query")
    if not isinstance(query, dict):
        raise SourceDiscoveryError("MediaWiki response lacks query object")
    if "redirects" in query or "normalized" in query:
        raise SourceDiscoveryError("MediaWiki title redirect or normalization is not admissible")
    pages = query.get("pages")
    if not isinstance(pages, list) or len(pages) != 1 or not isinstance(pages[0], dict):
        raise SourceDiscoveryError("MediaWiki response must contain exactly one page")
    page = pages[0]
    if "missing" in page or "redirect" in page or page.get("title") != expected_title:
        raise SourceDiscoveryError("MediaWiki page is missing, redirected, or title-mismatched")
    revisions = page.get("revisions")
    if revisions is None:
        revisions = []
    if not isinstance(revisions, list) or not all(
        isinstance(revision, dict) for revision in revisions
    ):
        raise SourceDiscoveryError("MediaWiki revisions have an invalid schema")
    return revisions, payload


def _revision_evidence(
    revision: dict[str, Any], title: str, raw_response: bytes
) -> RevisionEvidence:
    revision_id = revision.get("revid")
    if (
        isinstance(revision_id, bool)
        or not isinstance(revision_id, int)
        or revision_id <= 0
    ):
        raise SourceDiscoveryError("MediaWiki revision id is invalid")
    timestamp = revision.get("timestamp")
    _canonical_utc(timestamp, "MediaWiki revision timestamp")
    mediawiki_sha1 = revision.get("sha1")
    if (
        not isinstance(mediawiki_sha1, str)
        or _MEDIAWIKI_SHA1.fullmatch(mediawiki_sha1) is None
    ):
        raise SourceDiscoveryError("MediaWiki revision sha1 is invalid")
    slots = revision.get("slots")
    if not isinstance(slots, dict) or not isinstance(slots.get("main"), dict):
        raise SourceDiscoveryError("MediaWiki revision main slot is missing")
    content = slots["main"].get("content")
    if not isinstance(content, str):
        raise SourceDiscoveryError("MediaWiki revision content is missing")
    if hashlib.sha1(content.encode("utf-8")).hexdigest() != mediawiki_sha1:
        raise SourceDiscoveryError("MediaWiki revision sha1 does not match content")
    return RevisionEvidence(
        revision_id=revision_id,
        timestamp=timestamp,
        mediawiki_sha1=mediawiki_sha1,
        revision_url=_immutable_revision_url(title, revision_id),
        raw_response_sha256=_sha256(raw_response),
        content_sha256=_sha256(content.encode("utf-8")),
        content=content,
    )


def _request_revisions(
    title: str,
    *,
    start: datetime,
    direction: str,
    limit: int,
    timeout_seconds: float,
    fetcher: _Fetch,
) -> tuple[list[RevisionEvidence], dict[str, Any]]:
    if direction not in {"older", "newer"}:
        raise SourceDiscoveryError("MediaWiki revision direction is invalid")
    if limit < 1 or limit > 500:
        raise SourceDiscoveryError("MediaWiki revision limit must be in [1, 500]")
    start_text = start.isoformat().replace("+00:00", "Z")
    url = _API_ENDPOINT + "?" + urlencode(
        {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "maxlag": "5",
            "prop": "revisions",
            "titles": title,
            "rvprop": "ids|timestamp|sha1|content",
            "rvslots": "main",
            "rvstart": start_text,
            "rvdir": direction,
            "rvlimit": str(limit),
        }
    )
    raw = fetcher(url, timeout_seconds)
    if not isinstance(raw, bytes):
        raise SourceDiscoveryError("MediaWiki fetcher must return bytes")
    revisions, payload = _decode_response(raw, title)
    evidence = [_revision_evidence(revision, title, raw) for revision in revisions]
    if len({item.revision_id for item in evidence}) != len(evidence):
        raise SourceDiscoveryError("MediaWiki response contains duplicate revisions")
    return evidence, payload


def validate_exante_sampling_frame(
    contract: dict[str, Any], frame: Any
) -> dict[str, Any]:
    """Reject a title-year frame unless it exactly binds the frozen ExAnte pins."""
    if not isinstance(frame, dict):
        raise SourceDiscoveryError("sampling frame must be an object")
    required = {
        "schema_version",
        "github_revision",
        "github_artifact_url",
        "github_source_sha256",
        "huggingface_revision",
        "huggingface_artifact_url",
        "huggingface_resolved_url",
        "huggingface_etag",
        "huggingface_source_sha256",
        "observed_row_count",
        "observed_unique_pair_count",
        "topics",
    }
    if set(frame) != required:
        raise SourceDiscoveryError("sampling frame has missing or extra fields")
    if frame["schema_version"] != "routes-v1-exante-sampling-frame":
        raise SourceDiscoveryError("sampling frame schema_version is invalid")
    upstreams = contract["upstreams"]
    if frame["github_revision"] != upstreams["exante_github"]["revision"]:
        raise SourceDiscoveryError("sampling frame GitHub revision does not match contract")
    if frame["github_artifact_url"] != upstreams["exante_github"]["artifact_url"]:
        raise SourceDiscoveryError("sampling frame GitHub artifact URL does not match contract")
    if frame["huggingface_revision"] != upstreams["exante_huggingface"]["revision"]:
        raise SourceDiscoveryError("sampling frame Hugging Face revision does not match contract")
    if frame["huggingface_artifact_url"] != upstreams["exante_huggingface"]["artifact_url"]:
        raise SourceDiscoveryError(
            "sampling frame Hugging Face artifact URL does not match contract"
        )
    _resolved_url, etag = _validate_huggingface_cache_redirect(
        frame["huggingface_resolved_url"],
        upstreams["exante_huggingface"]["revision"],
    )
    if frame["huggingface_etag"] != etag:
        raise SourceDiscoveryError("sampling frame Hugging Face etag does not match URL")
    for name in ("github_source_sha256", "huggingface_source_sha256"):
        if not isinstance(frame[name], str) or _SHA256.fullmatch(frame[name]) is None:
            raise SourceDiscoveryError(f"sampling frame {name} is invalid")
    topics = frame["topics"]
    if not isinstance(topics, list):
        raise SourceDiscoveryError("sampling frame topics must be a list")
    observed: set[tuple[str, int]] = set()
    for index, topic in enumerate(topics):
        if not isinstance(topic, dict) or set(topic) != {"title", "cutoff_year"}:
            raise SourceDiscoveryError(f"sampling frame topic {index} has an invalid schema")
        title = topic["title"]
        year = topic["cutoff_year"]
        if (
            not isinstance(title, str)
            or not title
            or isinstance(year, bool)
            or not isinstance(year, int)
        ):
            raise SourceDiscoveryError(f"sampling frame topic {index} is invalid")
        observed.add((title, year))
    if len(observed) != len(topics):
        raise SourceDiscoveryError("sampling frame topics contain duplicates")
    expected = {
        (topic["title"], topic["cutoff_year"])
        for group in contract["sampling"]["topics"].values()
        for topic in group
    }
    if observed != expected:
        raise SourceDiscoveryError("sampling frame does not exactly match frozen title-year pairs")
    observed_row_count = frame["observed_row_count"]
    observed_unique_pair_count = frame["observed_unique_pair_count"]
    if (
        isinstance(observed_row_count, bool)
        or not isinstance(observed_row_count, int)
        or observed_row_count < len(topics)
    ):
        raise SourceDiscoveryError("sampling frame observed_row_count is invalid")
    if (
        isinstance(observed_unique_pair_count, bool)
        or not isinstance(observed_unique_pair_count, int)
        or observed_unique_pair_count < len(topics)
    ):
        raise SourceDiscoveryError("sampling frame observed_unique_pair_count is invalid")
    return frame


def _normalize_title(value: str) -> str:
    """Apply the only allowed title normalization: NFC plus outer whitespace trim."""
    return unicodedata.normalize("NFC", value).strip()


def _parse_exante_wiki_csv(raw: bytes) -> tuple[int, set[tuple[str, int]]]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise SourceDiscoveryError("ExAnte wiki CSV is not UTF-8") from error
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != ["Title", "Cutoff_Year"]:
        raise SourceDiscoveryError(
            "ExAnte wiki CSV must contain exact Title,Cutoff_Year columns"
        )
    pairs: set[tuple[str, int]] = set()
    row_count = 0
    for row in reader:
        row_count += 1
        title = row.get("Title")
        year_text = row.get("Cutoff_Year")
        if not isinstance(title, str) or not isinstance(year_text, str):
            raise SourceDiscoveryError("ExAnte wiki CSV contains a malformed row")
        normalized_title = _normalize_title(title)
        normalized_year = year_text.strip()
        if not normalized_title or not re.fullmatch(r"[0-9]{4}", normalized_year):
            raise SourceDiscoveryError("ExAnte wiki CSV contains an invalid title-year pair")
        pairs.add((normalized_title, int(normalized_year)))
    if row_count == 0:
        raise SourceDiscoveryError("ExAnte wiki CSV has no data rows")
    return row_count, pairs


def build_sampling_frame(
    contract: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
    fetcher: _ArtifactFetch | None = None,
) -> dict[str, Any]:
    """Fetch exact upstream bytes and construct, but do not seal, a frame artifact."""
    contract = validate_contract_document(contract)
    if timeout_seconds <= 0:
        raise SourceDiscoveryError("timeout_seconds must be positive")
    active_fetcher = _default_fetch_artifact if fetcher is None else fetcher
    github = contract["upstreams"]["exante_github"]
    huggingface = contract["upstreams"]["exante_huggingface"]
    github_receipt = active_fetcher(github["artifact_url"], timeout_seconds)
    huggingface_receipt = active_fetcher(huggingface["artifact_url"], timeout_seconds)
    github_resolved_url, _github_etag = _validate_upstream_receipt(
        "exante_github", github, github_receipt
    )
    huggingface_resolved_url, huggingface_etag = _validate_upstream_receipt(
        "exante_huggingface", huggingface, huggingface_receipt
    )
    github_raw = github_receipt.body
    huggingface_raw = huggingface_receipt.body
    row_count, pairs = _parse_exante_wiki_csv(huggingface_raw)
    expected_topics = [
        topic
        for group in contract["sampling"]["topics"].values()
        for topic in group
    ]
    expected_pairs = {
        (_normalize_title(topic["title"]), topic["cutoff_year"])
        for topic in expected_topics
    }
    if not expected_pairs.issubset(pairs):
        missing = sorted(expected_pairs - pairs)
        raise SourceDiscoveryError(f"ExAnte wiki CSV is missing frozen title-year pairs: {missing}")
    return {
        "schema_version": "routes-v1-exante-sampling-frame",
        "github_revision": github["revision"],
        "github_artifact_url": github_resolved_url,
        "github_source_sha256": _sha256(github_raw),
        "huggingface_revision": huggingface["revision"],
        "huggingface_artifact_url": huggingface["artifact_url"],
        "huggingface_resolved_url": huggingface_resolved_url,
        "huggingface_etag": huggingface_etag,
        "huggingface_source_sha256": _sha256(huggingface_raw),
        "observed_row_count": row_count,
        "observed_unique_pair_count": len(pairs),
        "topics": expected_topics,
    }


def _declared_topic(contract: dict[str, Any], phase: str, title: str) -> int:
    if phase not in {"pilot", "full"}:
        raise SourceDiscoveryError("phase must be pilot or full")
    groups = ("pilot",) if phase == "pilot" else ("extension",)
    matches = [
        topic["cutoff_year"]
        for group in groups
        for topic in contract["sampling"]["topics"][group]
        if topic["title"] == title
    ]
    if len(matches) != 1:
        raise SourceDiscoveryError("title is not declared for the requested phase")
    return matches[0]


def discover_topic(
    contract: dict[str, Any],
    sampling_frame: dict[str, Any],
    *,
    phase: str,
    title: str,
    timeout_seconds: float = 30.0,
    fetcher: _Fetch | None = None,
) -> DiscoveryArtifact:
    """Fetch one strict and one fixed-horizon revision for a declared topic."""
    contract = validate_contract_document(contract)
    validate_exante_sampling_frame(contract, sampling_frame)
    cutoff_year = _declared_topic(contract, phase, title)
    if timeout_seconds <= 0:
        raise SourceDiscoveryError("timeout_seconds must be positive")
    active_fetcher = _default_fetch_bytes if fetcher is None else fetcher
    boundary = datetime(cutoff_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    strict, _strict_payload = _request_revisions(
        title,
        start=boundary,
        direction="older",
        limit=1,
        timeout_seconds=timeout_seconds,
        fetcher=active_fetcher,
    )
    if len(strict) != 1:
        raise SourceIneligibleError("declared topic has no strict pre-cutoff revision")
    if _canonical_utc(strict[0].timestamp, "strict timestamp") > boundary:
        raise SourceIneligibleError("declared topic has no strict pre-cutoff revision")
    horizon_days = contract["source_selection"]["post_snapshot_horizon_days"]
    snapshot_boundary = boundary + timedelta(days=horizon_days)
    post_snapshot, _post_snapshot_payload = _request_revisions(
        title,
        start=snapshot_boundary,
        direction="older",
        limit=1,
        timeout_seconds=timeout_seconds,
        fetcher=active_fetcher,
    )
    if len(post_snapshot) != 1:
        raise SourceIneligibleError(
            "declared topic has no valid fixed-horizon post-cutoff revision"
        )
    snapshot_time = _canonical_utc(post_snapshot[0].timestamp, "post-snapshot timestamp")
    if snapshot_time <= boundary or snapshot_time > snapshot_boundary:
        raise SourceIneligibleError(
            "declared topic has no valid fixed-horizon post-cutoff revision"
        )
    if post_snapshot[0].revision_id == strict[0].revision_id:
        raise SourceIneligibleError(
            "declared topic has no valid fixed-horizon post-cutoff revision"
        )
    return DiscoveryArtifact(
        title=title,
        cutoff_year=cutoff_year,
        boundary_timestamp=boundary.isoformat().replace("+00:00", "Z"),
        strict_revision=strict[0],
        post_snapshot_horizon_days=horizon_days,
        post_snapshot=post_snapshot[0],
    )


def _write_canonical_json(path: str | Path, value: dict[str, Any]) -> None:
    output = Path(path)
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    output.write_bytes((payload + "\n").encode("utf-8"))


def write_discovery_artifact(path: str | Path, artifact: DiscoveryArtifact) -> None:
    """Write deterministic UTF-8 discovery evidence; labels are never created here."""
    _write_canonical_json(path, artifact.to_dict())


def write_sampling_frame(path: str | Path, frame: dict[str, Any]) -> None:
    """Write a deterministic frame after exact upstream-byte validation."""
    _write_canonical_json(path, frame)


def _load_sampling_frame(path: str | Path) -> dict[str, Any]:
    try:
        content = Path(path).read_text(encoding="utf-8")
        value = json.loads(content)
    except (OSError, json.JSONDecodeError) as error:
        raise SourceDiscoveryError(f"unable to load sampling frame: {error}") from error
    if not isinstance(value, dict):
        raise SourceDiscoveryError("sampling frame must be an object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Discover immutable Routes v1 revision evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    build_frame = commands.add_parser("build-frame")
    build_frame.add_argument("--contract", required=True, type=Path)
    build_frame.add_argument(
        "--output",
        type=Path,
        default=Path("research/routes-v1/sampling_frame.json"),
    )
    build_frame.add_argument("--timeout-seconds", type=float, default=30.0)
    discover = commands.add_parser("discover")
    discover.add_argument("--contract", required=True, type=Path)
    discover.add_argument("--sampling-frame", required=True, type=Path)
    discover.add_argument("--phase", required=True, choices=("pilot", "full"))
    discover.add_argument("--title", required=True)
    discover.add_argument("--output", required=True, type=Path)
    discover.add_argument("--timeout-seconds", type=float, default=30.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run read-only source discovery for one already-declared topic."""
    args = _parser().parse_args(argv)
    try:
        contract = load_contract(args.contract)
        if args.command == "build-frame":
            frame = build_sampling_frame(
                contract, timeout_seconds=args.timeout_seconds
            )
            write_sampling_frame(args.output, frame)
        else:
            sampling_frame = _load_sampling_frame(args.sampling_frame)
            artifact = discover_topic(
                contract,
                sampling_frame,
                phase=args.phase,
                title=args.title,
                timeout_seconds=args.timeout_seconds,
            )
            write_discovery_artifact(args.output, artifact)
    except (ContractValidationError, SourceDiscoveryError) as error:
        raise SystemExit(f"source discovery failed: {error}") from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
