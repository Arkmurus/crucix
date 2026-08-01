"""R-F3637 — pin the training corpus, and make §24's pre-flight mechanical.

WHY THIS EXISTS
---------------
§24 attaches a condition to the operator's standing spend approval for the weekly
train/eval cycle:

    "training must be REAL — pre-flight review of the training pipeline + dataset
     quality before any paid cycle; a cycle that would train on unreviewed/contaminated
     data is cancelled, not run."

That condition had no machine. Worse, it had nothing to review AGAINST: every file in
`data/training/*.jsonl` is UNTRACKED. Nothing pins them, so:

  * a run cannot be attributed to a corpus — there is no sha to record beside a
    checkpoint, and no diff between v06 and v07;
  * an eval movement cannot be explained — you cannot tell a real gain from a
    relocated artefact (`a-fix-that-relocates-a-failure-is-not-a-fix`);
  * CONTAMINATION cannot be disproved. Phase A gate #6 pins the 500-Q golden set at
    hash a07b6af760ad7f44. If a training row is also an eval row, the score is
    measuring memorisation and gate #6 stops meaning anything — the exact false clean
    this repo exists to prevent, in the one place it would be most expensive.

The corpus is NOT committed here: it is large, and a hash manifest gives attribution
without turning the repo into a data store. What must be reproducible is the ANSWER to
"which rows produced this checkpoint", and a manifest answers it.

USAGE
-----
    python scripts/admin/training_corpus_manifest.py            # report + contamination check
    python scripts/admin/training_corpus_manifest.py --record   # write the manifest

Exit codes:  0 clean · 1 CONTAMINATION FOUND · 2 refused to record

Contamination is checked against the LIVE golden set when reachable. When it is not,
this reports UNKNOWN and refuses to record a clean bill — absent is not false
(`declared-capability-flag-drifts`). A manifest claiming "no contamination" because it
could not look is worse than no manifest.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
CORPUS_DIR = ROOT / "data" / "training"
MANIFEST = ROOT / "docs" / "training_corpus_manifest.json"


def _norm(text: str) -> str:
    """Normalise a prompt for overlap comparison.

    Deliberately aggressive: contamination that survives whitespace or case changes is
    still contamination. A stricter (exact-match) comparison would under-report, and
    under-reporting is the failure mode that matters here.
    """
    return " ".join((text or "").split()).strip().lower()


def _row_prompt(row: dict) -> str:
    """Pull the question/prompt out of a corpus row, whatever the schema calls it."""
    for key in ("question", "prompt", "instruction", "input", "query"):
        v = row.get(key)
        if isinstance(v, str) and v.strip():
            return v
    # chat-style: first user turn
    msgs = row.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    return c
    return ""


def scan_corpus() -> list[dict]:
    """SHA-256, row count and byte size for every corpus file — the pin."""
    out: list[dict] = []
    for path in sorted(CORPUS_DIR.glob("*.jsonl")):
        digest = hashlib.sha256()
        rows = 0
        bad = 0
        prompts: set[str] = set()
        with path.open("rb") as fh:
            for raw in fh:
                digest.update(raw)
                if not raw.strip():
                    continue
                rows += 1
                try:
                    p = _row_prompt(json.loads(raw))
                except Exception:
                    bad += 1          # counted, never silently dropped
                    continue
                if p:
                    prompts.add(_norm(p))
        out.append({
            "file": path.name,
            "sha256": digest.hexdigest()[:32],
            "rows": rows,
            "unparseable_rows": bad,
            "bytes": path.stat().st_size,
            "_prompts": prompts,          # stripped before writing
        })
    return out


async def _golden_prompts() -> set[str] | None:
    """The frozen 500-Q set, or None when it cannot be read.

    None is NOT an empty set. Returning empty on failure would report "no overlap"
    for a check that never ran.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from aria_service.intel import eval_runner
        golden = await eval_runner.get_golden_set()
    except Exception as exc:                      # noqa: BLE001 — reported, not swallowed
        print(f"  golden set UNREACHABLE ({type(exc).__name__}: {exc})")
        return None
    if not golden:
        print("  golden set returned EMPTY — treating as unreachable, not as 'no overlap'")
        return None
    return {_norm(_row_prompt(g)) for g in golden if _row_prompt(g)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--record", action="store_true", help="write docs/training_corpus_manifest.json")
    args = ap.parse_args()

    if not CORPUS_DIR.is_dir():
        print(f"no corpus at {CORPUS_DIR}")
        return 2

    files = scan_corpus()
    total_rows = sum(f["rows"] for f in files)
    total_bad = sum(f["unparseable_rows"] for f in files)
    print(f"{len(files)} corpus files · {total_rows:,} rows · "
          f"{sum(f['bytes'] for f in files) / 1e6:.1f} MB"
          + (f" · {total_bad} UNPARSEABLE rows" if total_bad else ""))

    import asyncio
    golden = asyncio.run(_golden_prompts())

    contaminated: list[dict] = []
    if golden is None:
        print("CONTAMINATION=UNKNOWN — the golden set could not be read.")
    else:
        print(f"  golden set: {len(golden)} distinct prompts")
        for f in files:
            hits = f["_prompts"] & golden
            if hits:
                contaminated.append({"file": f["file"], "overlapping_rows": len(hits),
                                     "examples": sorted(hits)[:3]})
        if contaminated:
            print(f"\nCONTAMINATION=YES — {len(contaminated)} file(s) share prompts with the "
                  f"frozen 500-Q eval. Training on the eval measures memorisation and "
                  f"makes Phase A gate #6 meaningless:")
            for c in contaminated:
                print(f"  ! {c['file']}: {c['overlapping_rows']} overlapping row(s)")
        else:
            print("CONTAMINATION=NO — no corpus prompt appears in the frozen eval set.")

    for f in files:
        f.pop("_prompts", None)

    if args.record:
        if golden is None:
            print("\nrefusing --record: contamination is UNKNOWN. A manifest that omits the "
                  "check reads as a clean bill of health for a check that never ran.")
            return 2
        if contaminated:
            print("\nrefusing --record: pinning a contaminated corpus would make the "
                  "contamination look reviewed and accepted.")
            return 2
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                                cwd=ROOT, capture_output=True, text=True).stdout.strip()
        MANIFEST.write_text(json.dumps({
            "recorded_at_commit": commit,
            "note": ("The corpus itself is untracked by design (size). This manifest is the "
                     "PIN: a training run records these sha256s so a checkpoint can be "
                     "attributed to exact inputs."),
            "golden_set_prompts": len(golden),
            "contamination": "none",
            "totals": {"files": len(files), "rows": total_rows,
                       "unparseable_rows": total_bad},
            "files": files,
        }, indent=1) + "\n", encoding="utf-8", newline="\n")
        print(f"recorded -> {MANIFEST.relative_to(ROOT)}")
        return 0

    return 1 if contaminated else 0


if __name__ == "__main__":
    raise SystemExit(main())
