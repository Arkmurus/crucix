"""Build a deterministic, audited DPO curriculum from retention plus delta."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.train.build_positive_curve_assets import deduplicate_preferences
from scripts.train.build_tooluse_corpus import _norm_subject, validate_trace


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()]


def _signature(messages: list[dict]) -> str:
    return json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_curriculum(
    preference_sets: list[list[dict]],
    corpus: list[dict],
    held_out: list[dict],
) -> tuple[list[dict], dict]:
    """Deduplicate preferences and refuse contamination or invalid chosen sides."""
    pairs = deduplicate_preferences([row for rows in preference_sets for row in rows])
    if not pairs:
        raise ValueError("no preference pairs after deduplication")
    held = {_norm_subject(str(row.get("subject") or "")) for row in held_out} - {""}
    by_prompt = {
        _signature(list(trace.get("messages") or [])[:-1]): trace
        for trace in corpus
    }
    axes: dict[str, int] = {}
    for pair in pairs:
        subject = str(pair.get("subject") or "")
        if _norm_subject(subject) in held:
            raise ValueError(f"held-out subject in DPO curriculum: {subject}")
        if str(pair.get("chosen") or "").strip() == str(pair.get("rejected") or "").strip():
            raise ValueError(f"degenerate preference pair: {subject}")
        trace = by_prompt.get(_signature(list(pair.get("prompt") or [])))
        if trace is None:
            raise ValueError(f"no exact canonical trace for preference: {subject}")
        probe = dict(trace)
        probe["messages"] = list(pair["prompt"]) + [
            {"role": "assistant", "content": pair["chosen"]}
        ]
        errors = validate_trace(probe)
        if errors:
            raise ValueError(f"chosen answer invalid for {subject}: {errors}")
        label = str(pair.get("label") or "unlabelled")
        axes[label] = axes.get(label, 0) + 1
    return pairs, {
        "complete": True,
        "source_rows": sum(len(rows) for rows in preference_sets),
        "deduplicated_rows": len(pairs),
        "held_out_overlap": 0,
        "chosen_valid": len(pairs),
        "nondegenerate": len(pairs),
        "axis_counts": dict(sorted(axes.items())),
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--preferences", type=Path, action="append", required=True)
    parser.add_argument("--corpus", type=Path, action="append", required=True)
    parser.add_argument("--held-out", type=Path, action="append", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--manifest-out", type=Path, required=True)
    args = parser.parse_args(argv)
    preference_sets = [_jsonl(path) for path in args.preferences]
    pairs, manifest = build_curriculum(
        preference_sets,
        [row for path in args.corpus for row in _jsonl(path)],
        [row for path in args.held_out for row in _jsonl(path)],
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in pairs),
                        encoding="utf-8", newline="\n")
    manifest["input_sha256"] = {
        "preferences": {str(path): _sha(path) for path in args.preferences},
        "corpus": {str(path): _sha(path) for path in args.corpus},
        "held_out": {str(path): _sha(path) for path in args.held_out},
    }
    manifest["output_sha256"] = _sha(args.out)
    args.manifest_out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(manifest, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
