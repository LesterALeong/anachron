"""Build the proposed date-shift source package and author-audit materials.

This command reads only the repository's pinned Routes v1 sampling frame,
pre-outcome curation drafts, and ignored raw discovery artifacts. It never
contacts Wikipedia, runs a model, or converts the pending AI-assisted curation
state into a human-validation claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class BuildError(ValueError):
    """Raised when a frozen source input cannot be reproduced exactly."""


_EXCLUSIONS = {
    ("COVID-19 pandemic", 2019): "no frozen discovery artifact",
    ("Mars", 2012): (
        "no sufficiently narrow answer-changing historical fact in the "
        "discovered revisions"
    ),
    ("Search", 2013): "no frozen discovery artifact",
    ("List of Marvel Cinematic Universe films", 2008): (
        "no frozen discovery artifact"
    ),
    ("2018 FIFA World Cup", 2018): (
        "no sufficiently narrow answer-changing historical fact in the "
        "discovered revisions"
    ),
    ("Real Madrid C.F.", 2010): "no frozen discovery artifact",
}
_MAX_EXCERPT_BYTES = 4096
_AI_AUDIT_NOTES = {
    "date-shift:02": "AI FLAG: later document reports older Q2 while pre document reports Q3; inspect chronology and semantic direction.",
    "date-shift:04": "FLAG: the bare alias 'candidate' is unsafe; author should inspect or reject.",
    "date-shift:14": "AI REJECT RECOMMENDATION: same Episode VII fact; apparent difference is label/link-target only.",
    "date-shift:17": "AI REJECT RECOMMENDATION: aliases overlap after normalization.",
    "date-shift:24": "AI REJECT RECOMMENDATION: same attack; apparent difference is naming aliases.",
    "date-shift:29": "AI REJECT RECOMMENDATION: pre anchor is FSB Director, not Security Council.",
    "date-shift:36": "AI FLAG: no deterministic wording correction installed; inspect whether the black-hole mapping is a substantive answer change.",
    "date-shift:39": "AI REVISE PROPOSAL: question narrowed to Dawn's fourth mapping-orbit phase; author must still decide.",
    "date-shift:40": "AI FLAG: no deterministic wording correction installed; inspect whether the Evolution mapping is a substantive answer change.",
    "date-shift:41": "AI REVISE PROPOSAL: question narrowed to the infobox subdivision_ranks field; author must still decide.",
    "date-shift:43": "AI REJECT RECOMMENDATION: apparent difference is rounding.",
    "date-shift:46": "AI REJECT RECOMMENDATION: '67' still satisfies 'more than 64'.",
    "date-shift:49": "AI FLAG: no deterministic wording correction installed; inspect whether the Bieber mapping is a substantive answer change.",
    "date-shift:51": "AI REJECT RECOMMENDATION: apparent difference is a precision correction.",
    "date-shift:52": "AI FLAG: no deterministic wording correction installed; inspect whether the Messi mapping is a substantive answer change.",
    "date-shift:54": "AI REVISE PROPOSAL: question narrowed to the passing-attempt total; author must still decide.",
}
_AI_PROPOSED_OVERRIDES = {
    "date-shift:04": {
        "pre_answer_aliases": ["candidate for President of the United States"],
        "question": "What status did the article state for Donald Trump in the 2016 U.S. presidential election?",
    },
    "date-shift:39": {
        "question": "Which fourth mapping-orbit phase did the article list for Dawn?",
    },
    "date-shift:41": {
        "question": "What subdivision_ranks value did the article list in the coronavirus infobox?",
    },
    "date-shift:54": {
        "question": "How many career passing attempts did the article list for Tom Brady?",
    },
}


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BuildError(f"cannot load JSON object: {path}") from error
    if not isinstance(value, dict):
        raise BuildError(f"JSON root is not an object: {path}")
    return value


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _sha_text(value: str) -> str:
    return _sha_bytes(value.encode("utf-8"))


def _canonical_sha(value: Any) -> str:
    return _sha_bytes(_canonical_bytes(value))


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFC", value).casefold().split())


def _parse_utc(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BuildError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as error:
        raise BuildError(f"{name} is not an ISO timestamp") from error
    if parsed.tzinfo != timezone.utc:
        raise BuildError(f"{name} must resolve to UTC")
    return parsed


def _take_left(text: str, budget: int) -> str:
    selected: list[str] = []
    used = 0
    for character in reversed(text):
        width = len(character.encode("utf-8"))
        if used + width > budget:
            break
        selected.append(character)
        used += width
    return "".join(reversed(selected))


def _take_right(text: str, budget: int) -> str:
    selected: list[str] = []
    used = 0
    for character in text:
        width = len(character.encode("utf-8"))
        if used + width > budget:
            break
        selected.append(character)
        used += width
    return "".join(selected)


def _excerpt(content: str, anchor: str) -> dict[str, Any]:
    if not isinstance(anchor, str) or not anchor or content.count(anchor) != 1:
        raise BuildError("source anchor must occur exactly once")
    anchor_offset = content.index(anchor)
    anchor_bytes = len(anchor.encode("utf-8"))
    if anchor_bytes > _MAX_EXCERPT_BYTES:
        raise BuildError("source anchor exceeds the excerpt bound")
    remaining = _MAX_EXCERPT_BYTES - anchor_bytes
    left = _take_left(content[:anchor_offset], remaining // 2)
    right = _take_right(
        content[anchor_offset + len(anchor) :],
        remaining - len(left.encode("utf-8")),
    )
    if len(right.encode("utf-8")) < remaining - len(left.encode("utf-8")):
        left = _take_left(
            content[:anchor_offset], remaining - len(right.encode("utf-8"))
        )
    text = left + anchor + right
    start = len(content[: anchor_offset - len(left)].encode("utf-8"))
    end = start + len(text.encode("utf-8"))
    if content.encode("utf-8")[start:end].decode("utf-8") != text:
        raise BuildError("excerpt is not a contiguous Unicode-safe source window")
    anchor_start = len(content[:anchor_offset].encode("utf-8"))
    return {
        "text": text,
        "sha256": _sha_text(text),
        "utf8_bytes": len(text.encode("utf-8")),
        "excerpt_start_offset": start,
        "excerpt_end_offset": end,
        "anchor_sha256": _sha_text(anchor),
        "anchor_start_offset": anchor_start,
        "anchor_end_offset": anchor_start + anchor_bytes,
    }


def _validate_revision(
    raw_revision: Any,
    draft_revision: Any,
    *,
    anchor: Any,
    opposite_content: str,
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not isinstance(raw_revision, dict) or not isinstance(draft_revision, dict):
        raise BuildError(f"{name} revision is missing")
    content = raw_revision.get("content")
    if not isinstance(content, str) or not content:
        raise BuildError(f"{name} full content is missing")
    if not isinstance(anchor, str) or anchor in opposite_content:
        raise BuildError(f"{name} anchor crosses into the opposite revision")
    for raw_key, draft_key in (
        ("revision_id", "revision_id"),
        ("revision_url", "revision_url"),
        ("timestamp", "timestamp"),
        ("content_sha256", "content_sha256"),
    ):
        if raw_revision.get(raw_key) != draft_revision.get(draft_key):
            raise BuildError(f"{name} revision does not match its pre-outcome draft")
    if raw_revision.get("content_sha256") != _sha_text(content):
        raise BuildError(f"{name} full-content hash drifted")
    oldid = raw_revision.get("revision_id")
    url = raw_revision.get("revision_url")
    if not isinstance(oldid, int) or not isinstance(url, str):
        raise BuildError(f"{name} immutable revision identity is malformed")
    if "https://en.wikipedia.org/w/index.php?" not in url or f"oldid={oldid}" not in url:
        raise BuildError(f"{name} source URL is not an immutable English Wikipedia revision")
    excerpt = _excerpt(content, anchor)
    source = {
        "immutable_url": url,
        "timestamp": raw_revision["timestamp"],
        "full_content_sha256": raw_revision["content_sha256"],
        "anchor_sha256": excerpt["anchor_sha256"],
        "anchor_start_offset": excerpt["anchor_start_offset"],
        "anchor_end_offset": excerpt["anchor_end_offset"],
        "excerpt_sha256": excerpt["sha256"],
        "excerpt_start_offset": excerpt["excerpt_start_offset"],
        "excerpt_end_offset": excerpt["excerpt_end_offset"],
    }
    return source, excerpt


def _draft_pairs(repository: Path) -> dict[tuple[str, int], tuple[dict[str, Any], str]]:
    root = repository / "research" / "routes-v1" / "curation"
    pairs: dict[tuple[str, int], tuple[dict[str, Any], str]] = {}
    for phase in ("pilot", "full"):
        draft = _load_object(root / f"{phase}.draft.json")
        if draft.get("schema_version") != "routes-v1-curation-draft":
            raise BuildError(f"unexpected {phase} curation schema")
        rows = draft.get("pairs")
        if not isinstance(rows, list):
            raise BuildError(f"{phase} curation pairs are missing")
        for pair in rows:
            if not isinstance(pair, dict):
                raise BuildError(f"{phase} contains a malformed pair")
            key = (pair.get("topic"), pair.get("cutoff_year"))
            if not isinstance(key[0], str) or not isinstance(key[1], int) or key in pairs:
                raise BuildError("accepted source pairs are not unique title/year records")
            if pair.get("curation", {}).get("status") != "codex_prepared_pending_human":
                raise BuildError("legacy source status changed from the pre-outcome record")
            pairs[key] = (pair, phase)
    if len(pairs) != 54:
        raise BuildError("the pre-outcome drafts must contain exactly 54 accepted pairs")
    return pairs


def build_artifacts(repository: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Reproduce the unapproved frame and bounded author-audit manifest."""
    sampling = _load_object(
        repository / "research" / "routes-v1" / "sampling_frame.json"
    )
    topics = sampling.get("topics")
    if not isinstance(topics, list) or len(topics) != 60:
        raise BuildError("the pinned sampling frame must contain exactly 60 candidates")
    accepted = _draft_pairs(repository)
    if set(accepted) | set(_EXCLUSIONS) != {
        (row.get("title"), row.get("cutoff_year")) for row in topics
    }:
        raise BuildError("accepted and excluded records do not partition the frozen frame")
    if set(accepted) & set(_EXCLUSIONS):
        raise BuildError("a candidate cannot be both accepted and excluded")

    candidates: list[dict[str, Any]] = []
    items: list[dict[str, Any]] = []
    for frame_index, topic_row in enumerate(topics):
        title, cutoff_year = topic_row.get("title"), topic_row.get("cutoff_year")
        if not isinstance(title, str) or not isinstance(cutoff_year, int):
            raise BuildError("sampling-frame title/year is malformed")
        key = (title, cutoff_year)
        if key in _EXCLUSIONS:
            candidates.append(
                {
                    "frame_index": frame_index,
                    "topic": title,
                    "cutoff_year": cutoff_year,
                    "status": "excluded",
                    "reason": _EXCLUSIONS[key],
                }
            )
            continue

        pair, phase = accepted[key]
        item_id = f"date-shift:{frame_index:02d}"
        candidates.append(
            {
                "frame_index": frame_index,
                "topic": title,
                "cutoff_year": cutoff_year,
                "status": "proposed",
                "item_id": item_id,
            }
        )
        raw_file = pair.get("discovery_artifact_file")
        if not isinstance(raw_file, str) or Path(raw_file).name != raw_file:
            raise BuildError(f"{item_id} discovery filename is invalid")
        raw_path = (
            repository
            / "research"
            / "routes-v1"
            / "artifacts"
            / "discovery"
            / phase
            / raw_file
        )
        raw = _load_object(raw_path)
        if _canonical_sha(raw) != pair.get("discovery_artifact_sha256"):
            raise BuildError(f"{item_id} discovery artifact hash drifted")
        if raw.get("title") != title or raw.get("cutoff_year") != cutoff_year:
            raise BuildError(f"{item_id} raw title/year drifted")
        raw_pre, raw_post = raw.get("strict_revision"), raw.get("post_snapshot")
        if not isinstance(raw_pre, dict) or not isinstance(raw_post, dict):
            raise BuildError(f"{item_id} raw revisions are missing")
        pre_content, post_content = raw_pre.get("content"), raw_post.get("content")
        if not isinstance(pre_content, str) or not isinstance(post_content, str):
            raise BuildError(f"{item_id} raw revision content is missing")
        pre_source, pre_excerpt = _validate_revision(
            raw_pre,
            pair.get("pre"),
            anchor=pair.get("pre_anchor"),
            opposite_content=post_content,
            name=f"{item_id} pre",
        )
        post_source, post_excerpt = _validate_revision(
            raw_post,
            pair.get("post"),
            anchor=pair.get("post_anchor"),
            opposite_content=pre_content,
            name=f"{item_id} post",
        )
        cutoff = datetime(cutoff_year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        pre_time = _parse_utc(pre_source["timestamp"], f"{item_id} pre timestamp")
        post_time = _parse_utc(post_source["timestamp"], f"{item_id} post timestamp")
        if pre_time > cutoff or post_time <= cutoff:
            raise BuildError(f"{item_id} does not straddle its cutoff")
        pre_aliases, post_aliases = (
            pair.get("pre_answer_aliases"),
            pair.get("post_answer_aliases"),
        )
        override = _AI_PROPOSED_OVERRIDES.get(item_id, {})
        pre_aliases = override.get("pre_answer_aliases", pre_aliases)
        post_aliases = override.get("post_answer_aliases", post_aliases)
        if (
            not isinstance(pre_aliases, list)
            or not pre_aliases
            or not all(isinstance(value, str) and value for value in pre_aliases)
            or not isinstance(post_aliases, list)
            or not post_aliases
            or not all(isinstance(value, str) and value for value in post_aliases)
        ):
            raise BuildError(f"{item_id} answer aliases are invalid")
        if {_normalize(value) for value in pre_aliases} & {
            _normalize(value) for value in post_aliases
        }:
            raise BuildError(f"{item_id} normalized answer aliases overlap")
        question = override.get("question", pair.get("question"))
        if not isinstance(question, str) or not question:
            raise BuildError(f"{item_id} question is missing")
        citation_hash = hashlib.sha256(
            f"{item_id}\0{post_source['immutable_url']}".encode()
        ).hexdigest()[:20].upper()
        items.append(
            {
                "item_id": item_id,
                "frame_index": frame_index,
                "topic_cluster_id": item_id,
                "topic": title,
                "cutoff_date": f"{cutoff_year}-12-31",
                "question": question,
                "citation_id": f"CIT-{citation_hash}",
                "presented_document_date_truthful": post_source["timestamp"][:10],
                "presented_document_date_backdated": f"{cutoff_year}-12-31",
                "document_content": {
                    "text": post_excerpt["text"],
                    "sha256": post_excerpt["sha256"],
                    "utf8_bytes": post_excerpt["utf8_bytes"],
                },
                "pre_answer_aliases": pre_aliases,
                "post_answer_aliases": post_aliases,
                "source_provenance": {
                    "legacy_raw_artifact_sha256": pair[
                        "discovery_artifact_sha256"
                    ],
                    "pre": pre_source,
                    "post": post_source,
                },
                "audit_evidence": {
                    "pre": {
                        "text": pre_excerpt["text"],
                        "sha256": pre_excerpt["sha256"],
                        "utf8_bytes": pre_excerpt["utf8_bytes"],
                    },
                    "post": {
                        "text": post_excerpt["text"],
                        "sha256": post_excerpt["sha256"],
                        "utf8_bytes": post_excerpt["utf8_bytes"],
                    },
                },
            }
        )

    if len(candidates) != 60 or len(items) != 54:
        raise BuildError("constructed cohort does not have the frozen 60/54 counts")
    frame = {
        "schema_version": "date-shift-proposed-frame-v2",
        "upstream": {
            "source": "ExAnte Wikipedia title/year frame",
            "github_revision": sampling.get("github_revision"),
            "github_artifact_url": sampling.get("github_artifact_url"),
            "github_source_sha256": sampling.get("github_source_sha256"),
            "huggingface_revision": sampling.get("huggingface_revision"),
            "huggingface_artifact_url": sampling.get("huggingface_artifact_url"),
            "huggingface_source_sha256": sampling.get("huggingface_source_sha256"),
            "legacy_sampling_frame_sha256": _canonical_sha(sampling),
        },
        "candidates": candidates,
    }
    items_document = {
        "schema_version": "date-shift-proposed-items-v2",
        "proposed_frame_sha256": _canonical_sha(frame),
        "proposed_items": items,
    }
    return frame, items_document


def build_audit_template(
    proposed_frame: dict[str, Any], proposed_items: dict[str, Any]
) -> dict[str, Any]:
    """Create the editable author decision file without deciding any mapping."""
    return {
        "schema_version": "date-shift-author-audit-v1",
        "proposed_frame_sha256": _canonical_sha(proposed_frame),
        "proposed_items_sha256": _canonical_sha(proposed_items),
        "author_id": "",
        "attested_at_utc": None,
        "attestation": "",
        "decisions": [
            {
                "item_id": item["item_id"],
                "source_bindings": {
                    "pre": {
                        "immutable_url": item["source_provenance"]["pre"]["immutable_url"],
                        "full_content_sha256": item["source_provenance"]["pre"]["full_content_sha256"],
                    },
                    "post": {
                        "immutable_url": item["source_provenance"]["post"]["immutable_url"],
                        "full_content_sha256": item["source_provenance"]["post"]["full_content_sha256"],
                    },
                },
                "decision": "UNRESOLVED",
                "reviewed_at_utc": None,
                "reason": "",
                "ai_recommendation_note": _AI_AUDIT_NOTES.get(item["item_id"], ""),
            }
            for item in proposed_items["proposed_items"]
        ],
    }


def render_audit_workbook(proposed_items: dict[str, Any]) -> str:
    """Render one readable, bounded evidence workbook for the author audit."""
    sections = [
        "# Date-shift author audit workbook",
        "",
        (
            "These are mechanically proposed mappings, not accepted study items. For every "
        "record, edit `author_audit.template.json` to add your author ID and full-set UTC "
        "attestation, then ACCEPT or REJECT every item with a UTC "
            "timestamp and a concrete reason, and retain every rejection. Do not run a model or "
            "create an execution contract until the completed audit passes the finalizer."
        ),
        "",
    ]
    for item in proposed_items["proposed_items"]:
        pre = item["source_provenance"]["pre"]
        post = item["source_provenance"]["post"]
        sections.extend(
            [
                f"## {item['item_id']}: {item['topic']} ({item['cutoff_date']})",
                "",
                f"Question: {item['question']}",
                "",
                f"Pre aliases: {', '.join(item['pre_answer_aliases'])}",
                f"Post aliases: {', '.join(item['post_answer_aliases'])}",
                f"AI recommendation/note (not an author decision): {_AI_AUDIT_NOTES.get(item['item_id'], 'None.')}",
                "",
                f"- Pre revision: [{pre['immutable_url']}]({pre['immutable_url']})",
                f"- Timestamp: `{pre['timestamp']}`",
                f"- Full hash: `{pre['full_content_sha256']}`",
                f"- Excerpt hash: `{item['audit_evidence']['pre']['sha256']}`",
                "",
                "Pre evidence excerpt (one JSON string per original line):",
                "```jsonl",
                _render_lossless_evidence(item["audit_evidence"]["pre"]["text"]),
                "```",
                "",
                f"- Post revision: [{post['immutable_url']}]({post['immutable_url']})",
                f"- Timestamp: `{post['timestamp']}`",
                f"- Full hash: `{post['full_content_sha256']}`",
                f"- Excerpt hash: `{item['audit_evidence']['post']['sha256']}`",
                "",
                "Post evidence excerpt (one JSON string per original line):",
                "```jsonl",
                _render_lossless_evidence(item["audit_evidence"]["post"]["text"]),
                "```",
                "",
            ]
        )
    return "\n".join(sections)


def _render_lossless_evidence(text: str) -> str:
    """Render source lines without changing bytes or adding trailing whitespace."""
    return "\n".join(json.dumps(line, ensure_ascii=False) for line in text.split("\n"))


def _write_create_only(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
    except FileExistsError as error:
        raise BuildError(f"refusing to overwrite existing artifact: {path}") from error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build proposed date-shift mappings and author-audit materials."
    )
    parser.add_argument("--repository", required=True, type=Path)
    parser.add_argument("--frame-output", required=True, type=Path)
    parser.add_argument("--items-output", required=True, type=Path)
    parser.add_argument("--audit-template-output", required=True, type=Path)
    parser.add_argument("--audit-workbook-output", required=True, type=Path)
    args = parser.parse_args(argv)
    repository = args.repository.resolve()
    frame, items = build_artifacts(repository)
    _write_create_only(args.frame_output.resolve(), frame)
    _write_create_only(args.items_output.resolve(), items)
    _write_create_only(args.audit_template_output.resolve(), build_audit_template(frame, items))
    workbook_path = args.audit_workbook_output.resolve()
    workbook_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with workbook_path.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(render_audit_workbook(items))
    except FileExistsError as error:
        raise BuildError(f"refusing to overwrite existing artifact: {workbook_path}") from error
    print(
        json.dumps(
            {
                "frame_sha256": _canonical_sha(frame),
                "items_sha256": _canonical_sha(items),
                "candidate_count": len(frame["candidates"]),
                "proposed_item_count": len(items["proposed_items"]),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
