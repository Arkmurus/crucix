"""R-F4376 (C-321) — train the AUTONOMOUS coder in the format it must actually emit.

TWO CODERS, TWO INTERFACES. R-F4371 taught the CLI (`aria_cli`) to call tools —
read_file / edit_file / run. The autonomous coder
(`autonomous/self_coder.fix_gap` via `sovereign_llm._build_edit_prompt`) does
something else entirely: it is handed a plan and a whole file, and must reply
with SURGICAL SEARCH/REPLACE JSON:

    {"filepath": "...",
     "edits": [{"old": "<verbatim, UNIQUE snippet>", "new": "..."}],
     "changes_made": ["..."]}

Training the tool-call corpus and hoping it transfers would repeat the mistake
this session already made once with the DD scorer: measuring, or teaching, a
different capability than the one in use.

THE CONTRACT IS UNFORGIVING, and that is exactly why it is trainable.
`apply_search_replace` rejects an edit whose `old` is absent OR ambiguous — one
failure and the caller falls back to rewriting the whole file, which is the
truncation risk the surgical path exists to avoid. So the skill is precise:
copy a snippet VERBATIM including indentation, and make it UNIQUE.

WHERE THE DATA COMES FROM — REAL FIXES, NOT INVENTED ONES.
`scripts/eval/mine_git_fixes.py` (R-F2434) already mines this repo's own R-F
commits into VERIFIED fail->pass rows: the commit's test is run against the
parent (must FAIL) and against the fix (must PASS), and anything unverifiable is
discarded. Those rows carry whole-file before/after. This builder converts that
evidence into the coder's own output format.

EVERY ROW IS PROVED BY RECONSTRUCTION. An edit set is kept only if:

  * every `old` occurs EXACTLY ONCE in the before-file (the contract), and
  * applying the edits through `apply_search_replace` — the SAME function the
    coder uses in production — reproduces the after-file BYTE FOR BYTE.

That is a verifiable reward, not a plausibility judgement: a row cannot be
subtly wrong and still pass. No teacher model is involved, so there is nothing
to grade and no per-row cost.

USAGE
    python -m scripts.train.build_coder_edit_corpus \
        --mined data/training/mined_code_fixes_verified.jsonl \
        --out data/training/aria_coder_edits_v1.jsonl
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The PRODUCTION applier, deliberately. A second implementation of "does this
# edit apply?" would drift from the one that actually runs, and a corpus graded
# by a divergent rule is a corpus of near-misses.
from aria_service.autonomous.sovereign_llm import apply_search_replace  # noqa: E402

#: Mirrors `_build_edit_prompt`. Training under a DIFFERENT prompt than
#: inference teaches the behaviour in a context she never sees.
SYSTEM = (
    "You are ARIA's autonomous self-coding engine. This file is LARGE — do NOT "
    "rewrite the whole file. Make SURGICAL edits: emit only the exact snippets "
    "that change, as search/replace pairs.\n\n"
    "RULES FOR EDITS (a wrong `old` is rejected, not applied):\n"
    "- Each `old` must be copied VERBATIM from the existing content, including "
    "exact indentation and whitespace.\n"
    "- Each `old` must be UNIQUE in the file — include enough surrounding lines "
    "that it matches exactly ONE place.\n"
    "- `new` is the full replacement for that `old` block.\n"
    "- Keep edits minimal and surgical. Do NOT delete unrelated code."
)

USER = """TARGET FILE: {path}

PLAN
{plan}

EXISTING CONTENT (read-only — find your `old` snippets verbatim in here)
```python
{before}
```

OUTPUT
Reply with ONLY valid JSON:
{{
  "filepath": "{path}",
  "edits": [
    {{"old": "<exact existing snippet, unique>", "new": "<replacement>"}}
  ],
  "changes_made": ["specific changes"]
}}"""

#: A file this size does not fit a training window beside its edits, and the
#: production prompt would not carry it either.
MAX_BEFORE_CHARS = 24_000


def _as_dict(value):
    """Mined rows store dicts as repr strings. Read either shape."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return ast.literal_eval(value)
        except Exception:  # noqa: BLE001
            return {}
    return {}


def derive_edits(before: str, after: str, context: int = 3) -> list[dict]:
    """Turn a before/after pair into search/replace edits that SATISFY the rule.

    Grows the context around each changed hunk until `old` is unique in the
    file. Uniqueness is the contract, and a hunk that cannot be made unique is
    dropped — the caller then rejects the whole row rather than emitting an
    edit production would refuse.
    """
    b_lines = before.splitlines(keepends=True)
    a_lines = after.splitlines(keepends=True)
    sm = difflib.SequenceMatcher(None, b_lines, a_lines, autojunk=False)
    edits: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        pad = context
        while pad <= 60:
            lo, hi = max(0, i1 - pad), min(len(b_lines), i2 + pad)
            old = "".join(b_lines[lo:hi])
            if old and before.count(old) == 1:
                new = ("".join(b_lines[lo:i1]) + "".join(a_lines[j1:j2])
                       + "".join(b_lines[i2:hi]))
                edits.append({"old": old, "new": new})
                break
            pad += 3
        else:
            return []          # this hunk cannot be made unique — reject the row
    return edits


def build_row(path: str, before: str, after: str, plan: dict) -> dict | None:
    """One training row, or None if it cannot be PROVED correct."""
    if not before.strip() or before == after:
        return None
    if len(before) > MAX_BEFORE_CHARS:
        return None
    edits = derive_edits(before, after)
    if not edits:
        return None

    # THE PROOF. Apply through the production applier and require byte equality.
    rebuilt, applied, failures = apply_search_replace(before, edits)
    if failures or len(applied) != len(edits) or rebuilt != after:
        return None

    answer = {
        "filepath": path,
        "edits": edits,
        "changes_made": plan.get("changes_made")
        or [plan.get("summary") or f"apply the {plan.get('r_number', 'fix')}"],
    }
    return {
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": USER.format(
                path=path, plan=json.dumps(plan, indent=2), before=before)},
            {"role": "assistant",
             "content": json.dumps(answer, indent=2, ensure_ascii=False)},
        ],
        "source": "verified_git_fix_search_replace",
        "builder": "R-F4376",
        "sha": plan.get("sha", ""),
        "r_number": plan.get("r_number", ""),
        "n_edits": len(edits),
    }


def build(mined: Path) -> tuple[list[dict], dict]:
    rows: list[dict] = []
    stats = {"mined_rows": 0, "candidates": 0, "kept": 0,
             "rejected_unprovable": 0, "rejected_too_large": 0}
    for line in mined.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        stats["mined_rows"] += 1
        rec = json.loads(line)
        before_files = _as_dict(rec.get("buggy_context"))
        after_files = _as_dict(rec.get("fix"))
        plan = {
            "r_number": rec.get("r_number", ""),
            "sha": rec.get("sha", ""),
            "summary": (rec.get("instruction") or "").strip()[:600],
        }
        for path, after in after_files.items():
            before = before_files.get(path)
            if before is None or not isinstance(after, str):
                continue
            stats["candidates"] += 1
            if len(before) > MAX_BEFORE_CHARS:
                stats["rejected_too_large"] += 1
                continue
            row = build_row(path, before, after, plan)
            if row is None:
                stats["rejected_unprovable"] += 1
                continue
            stats["kept"] += 1
            rows.append(row)
    return rows, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mined", default="data/training/mined_code_fixes_verified.jsonl")
    ap.add_argument("--out", default="data/training/aria_coder_edits_v1.jsonl")
    ap.add_argument("--eval-frac", type=float, default=0.15)
    args = ap.parse_args()

    mined = Path(args.mined)
    if not mined.exists():
        print(f"missing mined corpus: {mined}", file=sys.stderr)
        return 1
    rows, stats = build(mined)
    if not rows:
        # Never write an empty corpus and call it a build: an empty file trains
        # nothing and reads downstream as "this capability is covered".
        print(f"NO PROVABLE ROWS — {stats}", file=sys.stderr)
        return 1

    # Split by R-NUMBER, not at random: two rows from the same commit are the
    # same fix, and splitting them across train/eval measures memorisation.
    by_r: dict[str, list[dict]] = {}
    for r in rows:
        by_r.setdefault(r.get("r_number") or r.get("sha") or "?", []).append(r)
    keys = sorted(by_r)
    n_eval = max(1, int(len(keys) * args.eval_frac))
    eval_keys = set(keys[-n_eval:])
    train = [r for k in keys if k not in eval_keys for r in by_r[k]]
    held = [r for k in sorted(eval_keys) for r in by_r[k]]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="\n") as fh:
        for r in train:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    ev = out.with_suffix(".eval.jsonl")
    with ev.open("w", encoding="utf-8", newline="\n") as fh:
        for r in held:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"kept {stats['kept']} of {stats['candidates']} candidates "
          f"from {stats['mined_rows']} mined rows")
    print(f"  rejected: {stats['rejected_unprovable']} unprovable, "
          f"{stats['rejected_too_large']} too large")
    print(f"  train {len(train)} -> {out}")
    print(f"  eval  {len(held)} -> {ev}   ({len(eval_keys)} held-out commits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
