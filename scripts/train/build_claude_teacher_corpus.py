#!/usr/bin/env python3
"""R-F4304 (C-257) — turn captured Claude teacher signal into SFT rows.

`brave_distill` has had a consumer since R-F2339: `brave_student`, a 6h trainer
loop plus an on-demand train endpoint. `claude_distill` had only a stats
endpoint — 30 MB captured, nothing learned. Its own route docstring says the
corpus exists "for distillation"; the distiller was never written.

WHY THIS IS A BUILDER AND NOT A "claude_student". `brave_student` learns
domain-preference weights into a `model.json` and reranks search results — a
statistical reranker. This corpus is reasoning TEXT, and distilling text means
SFT/DPO rows feeding the pipeline that already exists in `scripts/train/`.
Mirroring the pattern would have produced a plausible-looking module that cannot
distil anything.

THREE RULES, each pinned by a test:

  * NEVER FABRICATE THE MISSING HALF. An SFT row is (instruction, response). A
    note whose prompt cannot be recovered is DROPPED and counted, never given a
    synthesised instruction — inventing the question teaches ARIA to answer
    prompts nobody wrote, which is the training-data equivalent of a clean line
    over a check that never ran.
  * DEDUPLICATE. Before C-254 the drain re-ingested the whole corpus on any
    unreadable cursor: 56,529 records for 41 unique texts, one note captured
    1,250 times. Capture is fixed, the history is not.
  * A SILENT SHRINK IS A LIE. Every drop is counted by reason and the ledger must
    balance (seen == kept + dropped). A builder that quietly discards most of its
    input while reporting a corpus is the "certified over a smaller world" shape.

Usage:
    python scripts/train/build_claude_teacher_corpus.py                # report only
    python scripts/train/build_claude_teacher_corpus.py --write        # emit the corpus
    python scripts/train/build_claude_teacher_corpus.py --corpus-dir /data/claude_distill
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]

#: Below this a record is bridge-probe exhaust, not teacher signal. The live
#: corpus holds 'A', 'like this' and 'ship Phase A'; the median unique text is 26
#: characters. Training on those dilutes the 24 notes that are real.
MIN_CHARS = 400

_OUT_DEFAULT = REPO / "data" / "training" / "aria_claude_teacher_v1.jsonl"
_SOURCE = "claude_teacher"


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def build(records: list) -> tuple[list[dict], dict]:
    """Return (rows, report). Pure — no IO, so the rules are testable directly."""
    rows: list[dict] = []
    dropped: dict[str, int] = {}
    seen_keys: set[str] = set()
    seen = 0

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for rec in records or []:
        seen += 1
        if not isinstance(rec, dict):
            drop("malformed")
            continue
        text = _norm(rec.get("text") or "")
        prompt = _norm(rec.get("prompt") or "")
        if not text:
            drop("malformed")
            continue

        # Duplicate check FIRST, keyed on (msg_id, content). Positional or
        # timestamp keys would miss it: the same note arrives with a fresh ts on
        # every re-drain.
        key = hashlib.sha256(
            f"{rec.get('msg_id', '')}\x00{text}".encode("utf-8", "replace")
        ).hexdigest()
        if key in seen_keys:
            drop("duplicate")
            continue
        seen_keys.add(key)

        if len(text) < MIN_CHARS:
            drop("too_short")
            continue
        if not prompt:
            # The half that was never captured. Reported, never invented.
            drop("no_prompt")
            continue

        rows.append({
            "messages": [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": text},
            ],
            "topic": str(rec.get("kind") or "note"),
            "grounded": False,
            "label": _SOURCE,
            "source": _SOURCE,
        })

    report = {"seen": seen, "kept": len(rows), "dropped": dropped}
    return rows, report


def _iter_corpus(corpus_dir: pathlib.Path):
    for shard in sorted(corpus_dir.glob("*.jsonl")):
        try:
            for line in shard.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except Exception:
                    yield {"__malformed__": True}
        except OSError:
            continue


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus-dir", default=os.getenv("ARIA_CLAUDE_DISTILL_DIR")
                    or str(REPO / "data" / "claude_distill"))
    ap.add_argument("--out", default=str(_OUT_DEFAULT))
    ap.add_argument("--write", action="store_true",
                    help="emit the corpus (default is report-only)")
    args = ap.parse_args(argv)

    cdir = pathlib.Path(args.corpus_dir)
    if not cdir.exists():
        print(f"[teacher-corpus] corpus dir not found: {cdir}", file=sys.stderr)
        return 2

    rows, report = build(list(_iter_corpus(cdir)))

    print(f"[teacher-corpus] {cdir}")
    print(f"  seen    {report['seen']}")
    print(f"  kept    {report['kept']}")
    for reason, n in sorted(report["dropped"].items()):
        print(f"  dropped {n:>7}  {reason}")
    balance = report["kept"] + sum(report["dropped"].values())
    if balance != report["seen"]:
        print(f"  LEDGER DOES NOT BALANCE: {balance} != {report['seen']}",
              file=sys.stderr)
        return 2

    if not args.write:
        print("  (report only — pass --write to emit)")
        return 0

    out = pathlib.Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    # newline="\n" — a corpus is hash-pinned downstream and CRLF would make one
    # file two identities across platforms.
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"  wrote   {len(rows)} row(s) -> {out}")
    if not rows:
        print("  NOTE: zero rows. Until the bridge has carried paired exchanges "
              "this is EXPECTED, not a failure — historical records predate the "
              "prompt field and cannot be paired without inventing the question.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
