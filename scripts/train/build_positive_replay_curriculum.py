"""Build a full-replay positive SFT continuation curriculum.

Continuation must rehearse the complete accepted-parent curriculum before adding
new measured-axis examples.  This prevents a small delta from replacing the
output grammar and multi-hop synthesis learned by the parent.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from scripts.train.build_mixed_tooluse_cycle import ALL_AXES
from scripts.train.build_tooluse_corpus import (
    _CLEAN_DENIAL_RE,
    _CLEAN_VERDICT_RE,
    _CITATION_SOURCES_KEY,
    _citation_tokens,
    _norm_subject,
    apply_citation_source_contract,
)

_CITATION = re.compile(r"\[from ([^\]]+)\]")


def load_jsonl(path: Path) -> list[dict]:
    """Read non-empty JSONL rows."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _final_answer(row: dict) -> str:
    assistants = [str(message.get("content") or "") for message in row.get("messages") or []
                  if message.get("role") == "assistant" and message.get("content")]
    if not assistants:
        raise ValueError("row has no final assistant answer")
    return assistants[-1]


def validate_reference_contract(row: dict) -> None:
    """Reject the two output-contract failures observed in the v6 child."""
    answer = _final_answer(row)
    citeable: set[str] = set()
    has_explicit_contract = False
    for message in row.get("messages") or []:
        if message.get("role") != "tool":
            continue
        try:
            payload = json.loads(message.get("content") or "{}")
        except (TypeError, ValueError):
            continue
        explicit = payload.get(_CITATION_SOURCES_KEY)
        if isinstance(explicit, list):
            has_explicit_contract = True
            citeable |= {str(source).strip().casefold() for source in explicit}
    for citation in _CITATION.findall(answer):
        for token in _citation_tokens(citation):
            normal = token.casefold()
            if normal.startswith(("memory:", "aria_search")) or "credibility" in normal:
                raise ValueError(f"invalid citation token: {token}")
            if has_explicit_contract and normal not in citeable:
                raise ValueError(f"citation is absent from citation_sources: {token}")
    label = str(row.get("label") or "")
    subject = str(row.get("subject") or "").strip()
    if label == "tooluse_contradiction" and _CLEAN_VERDICT_RE.search(answer) \
            and not _CLEAN_DENIAL_RE.search(answer):
        raise ValueError("contradiction answer asserts a CLEAN verdict")
    if label == "tooluse_multihop" and subject.casefold() not in answer.casefold():
        raise ValueError(f"multihop answer omits subject: {subject}")


def build_replay_curriculum(parent: list[dict], delta: list[dict],
                            forbidden_entities: set[str]) -> tuple[list[dict], dict]:
    """Return complete parent replay followed by the positive delta."""
    if not parent or not delta:
        raise ValueError("parent and delta curricula must both be non-empty")
    rows = [apply_citation_source_contract(row) for row in (*parent, *delta)]
    contaminated = sorted({str(row.get("subject") or "") for row in rows
                           if _norm_subject(str(row.get("subject") or "")) in forbidden_entities})
    if contaminated:
        raise ValueError(f"curriculum contains held-out or golden entities: {contaminated}")
    for row in rows:
        validate_reference_contract(row)
    parent_axes = Counter(str(row.get("label") or "") for row in parent)
    total_axes = Counter(str(row.get("label") or "") for row in rows)
    if set(parent_axes) != ALL_AXES or set(total_axes) != ALL_AXES:
        raise ValueError("full replay does not cover all ten axes")
    manifest = {
        "complete": True,
        "strategy": "accepted_parent_full_replay_plus_positive_delta",
        "parent_rows": len(parent),
        "delta_rows": len(delta),
        "total_rows": len(rows),
        "parent_axis_counts": dict(sorted(parent_axes.items())),
        "total_axis_counts": dict(sorted(total_axes.items())),
        "all_axes_retained": True,
        "dpo_rows": 0,
        "citation_source_contract": "explicit_allowlist_v1",
        "contradiction_contract": "no_match_is_not_clean_v1",
    }
    return rows, manifest


def main(argv: list[str] | None = None) -> int:
    """Build and persist the replay curriculum and its manifest."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--parent", type=Path, required=True)
    parser.add_argument("--delta", type=Path, required=True)
    parser.add_argument("--eval", type=Path, required=True)
    parser.add_argument("--golden", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    args = parser.parse_args(argv)
    forbidden = {_norm_subject(str(row.get("subject") or ""))
                 for path in (args.eval, args.golden) for row in load_jsonl(path)}
    rows, manifest = build_replay_curriculum(
        load_jsonl(args.parent), load_jsonl(args.delta), forbidden)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                        encoding="utf-8", newline="\n")
    args.manifest_out.parent.mkdir(parents=True, exist_ok=True)
    args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
