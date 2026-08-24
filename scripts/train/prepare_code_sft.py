"""R-F2440 — code-sovereign SFT corpus builder + pre-flight (§24).

Turns the VERIFIED mined fix corpus (R-F2434) into SFT {input, output} pairs
for a code-native base (Qwen2.5-Coder). Training uses the EXACT same localized-
edit format the eval scores (eval_mined_tier), so the model is trained on the
task it is measured on — train/eval task-parity (no format skew that would waste
a paid cycle).

This is also the §24 PRE-FLIGHT: it dedups by sha, ENFORCES train<->eval
disjointness (drops any eval-tier sha from the SFT set — the #1 contamination
trap), drops degenerate/oversized rows, and prints a readiness report. A paid
SFT cycle is only justified when this reports a real, clean, sufficiently large
corpus.

Output schema (matches scripts/train/prepare_sft.py so sft_train.py ingests it):
  {"input": <localized-edit prompt>, "output": <### FIXED n blocks>,
   "meta": {sha, r_number, multi_file, n_windows, source_files}}

Usage:
  python scripts/train/prepare_code_sft.py \
    --corpus data/training/mined_code_fixes_verified.jsonl \
    --eval-tier data/eval/mined_code_eval_tier.jsonl \
    --out data/training/code_sft_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts" / "eval"))
import eval_mined_tier as E  # reuse aligned_windows -> train/eval task-parity

_MAX_INPUT_CHARS = 24000   # ~6k tokens; skip rows whose windows exceed (keeps seqs trainable)
_MAX_OUTPUT_CHARS = 12000


def _pair(row: dict) -> tuple[str, str, int] | None:
    """Build (input_prompt, target_output, n_windows) for a mined row, using the
    SAME window format as the eval. Returns None if the row has no usable edit
    windows (degenerate)."""
    windows: list[tuple[int, str, str, str]] = []  # idx, path, buggy, gold
    for p in row["source_files"]:
        buggy = row["buggy_context"].get(p)
        gold = row["fix"].get(p)
        if buggy is None or gold is None:
            continue
        for bw, gw in E.aligned_windows(buggy, gold):
            windows.append((len(windows) + 1, p, bw, gw))
    if not windows:
        return None
    win_block = "\n".join(
        f"### WINDOW {i} file: {p}\n```python\n{bw}\n```" for i, p, bw, gw in windows)
    test_block = "\n".join(
        f"### TEST: {p}\n```python\n{c}\n```" for p, c in row["verify_test"].items())
    prompt = (
        "You are ARIA's autonomous coder. Below are the exact code REGION(S) that "
        "must be edited to make the failing test pass, taken verbatim from real "
        "(very large) source files. For EACH numbered window, return the FULL "
        "corrected version of that same region, in order, in this exact format:\n\n"
        "### FIXED <n>\n```python\n<corrected region>\n```\n\n"
        "Preserve everything you are not changing; only fix the bug. Do NOT edit "
        "the test.\n\n"
        f"GAP / TASK:\n{row['instruction']}\n\n"
        f"REGION(S) TO FIX:\n{win_block}\n\n"
        f"REPRODUCE TEST (currently FAILS — do not edit it):\n{test_block}\n"
    )
    target = "\n".join(
        f"### FIXED {i}\n```python\n{gw}\n```" for i, p, bw, gw in windows)
    return prompt, target, len(windows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", default="data/training/mined_code_fixes_verified.jsonl")
    ap.add_argument("--eval-tier", default="data/eval/mined_code_eval_tier.jsonl")
    ap.add_argument("--out", default="data/training/code_sft_v1.jsonl")
    args = ap.parse_args()

    corpus_p = _REPO / args.corpus
    rows = [json.loads(l) for l in corpus_p.read_text(encoding="utf-8").splitlines() if l.strip()]

    eval_shas: set[str] = set()
    eval_p = _REPO / args.eval_tier
    if eval_p.exists():
        eval_shas = {json.loads(l)["sha"] for l in eval_p.read_text(encoding="utf-8").splitlines() if l.strip()}

    drops = {"duplicate_sha": 0, "eval_tier_holdout": 0, "no_windows": 0,
             "oversized": 0}
    seen: set[str] = set()
    pairs: list[dict] = []
    for r in rows:
        sha = r["sha"]
        if sha in seen:
            drops["duplicate_sha"] += 1
            continue
        seen.add(sha)
        if sha in eval_shas:                 # NEVER train on a held-out eval row
            drops["eval_tier_holdout"] += 1
            continue
        built = _pair(r)
        if built is None:
            drops["no_windows"] += 1
            continue
        prompt, target, nwin = built
        if len(prompt) > _MAX_INPUT_CHARS or len(target) > _MAX_OUTPUT_CHARS:
            drops["oversized"] += 1
            continue
        pairs.append({"input": prompt, "output": target,
                      "meta": {"sha": sha, "r_number": r["r_number"],
                               "multi_file": r["multi_file"], "n_windows": nwin,
                               "source_files": r["source_files"]}})

    out_p = _REPO / args.out
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text("".join(json.dumps(p, ensure_ascii=False) + "\n" for p in pairs), encoding="utf-8", newline="\n")

    # ── §24 readiness report ──
    import statistics
    in_lens = [len(p["input"]) for p in pairs]
    # disjointness is a property of the WRITTEN pairs (eval-tier shas were dropped)
    pair_shas = {p["meta"]["sha"] for p in pairs}
    disjoint = not (pair_shas & eval_shas)
    print("=== CODE-SFT PRE-FLIGHT (R-F2440) ===")
    print(f"corpus rows read:         {len(rows)}")
    print(f"unique shas:              {len(seen)}")
    print(f"SFT pairs written:        {len(pairs)}  -> {out_p}")
    print(f"drops:                    {drops}")
    print(f"multi_file pairs:         {sum(1 for p in pairs if p['meta']['multi_file'])}")
    print(f"train<->eval DISJOINT:    {disjoint}  (eval holdout shas: {len(eval_shas)})")
    if in_lens:
        print(f"input chars min/med/max:  {min(in_lens)}/{int(statistics.median(in_lens))}/{max(in_lens)}")
    # honest readiness verdict
    ready = len(pairs) >= 300 and disjoint and drops["eval_tier_holdout"] >= 0
    print(f"SFT-READY (>=300 clean disjoint pairs): {ready}")
    if not ready:
        print("  NOT YET SFT-READY — the mine is still producing rows; re-run at "
              "mine completion. Do NOT spend a paid cycle on a thin corpus (§24).")


if __name__ == "__main__":
    main()
