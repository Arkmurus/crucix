#!/usr/bin/env python3
"""R-F2367 — §24 pre-flight: refuse to train on eval-contaminated data.

CLAUDE.md §24 (operator, 2026-06-07): "training must be REAL ... a cycle that
would train on unreviewed/contaminated data is cancelled, not run." This script
makes that structural: every train/eval cycle calls it BEFORE any scp/train, and
it EXITS NON-ZERO (aborting the cycle) if any training file's prompts overlap the
frozen 500-Q eval set beyond a tiny threshold.

Why it exists: the audit on 2026-07-03 found `aria_dpo_v1.jsonl` (396/396 = 100%)
and `aria_dpo_v04.jsonl` (350/356 = 98%) were built by using the eval QUESTIONS as
DPO prompts (root: scripts/train/build_dpo_from_eval.py). Training on those teaches
the model the eval questions → the 500-Q eval (Phase-A gate #6) stops being a
held-out measure and any gate-#2 "lift" is fraudulent. All other training families
(grounded_*, sft_distill_*, dpo_pairs_v1_str, citation_dpo_v2, train_*_questions)
are clean (0 leak) and are the correct inputs.

Usage:
  python scripts/train/preflight_eval_contamination.py \
      --train data/training/aria_dpo_pairs_v1_str.jsonl \
      --train data/training/aria_grounded_v1.jsonl \
      --eval  data/training/aria_eval_500q.jsonl \
      --max-overlap 0.01
Exit 0 = clean (safe to train). Exit 2 = contaminated (cycle MUST abort).
"""
import argparse
import json
import re
import sys


def norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"\s+", " ", s)
    return s.rstrip(" ?.!:")


def extract_q(rec) -> str:
    """Pull the question/prompt text from a record regardless of schema
    (prompt / question / messages[user])."""
    if not isinstance(rec, dict):
        return ""
    for k in ("question", "prompt"):
        v = rec.get(k)
        if isinstance(v, str) and v.strip():
            return v
    msgs = rec.get("messages")
    if isinstance(msgs, list):
        for m in msgs:
            if isinstance(m, dict) and m.get("role") in ("user", "human"):
                c = m.get("content")
                if isinstance(c, str) and c.strip():
                    return c
    return ""


def load_eval_norms(path: str) -> set:
    out = set()
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            q = d.get("question") or extract_q(d)
            if q:
                out.add(norm(q))
    return out


def scan(path: str, eval_norms: set):
    total = hits = 0
    hit_examples = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            total += 1
            nq = norm(extract_q(rec))
            if nq and nq in eval_norms:
                hits += 1
                if len(hit_examples) < 3:
                    hit_examples.append(nq[:80])
    frac = (hits / total) if total else 0.0
    return total, hits, frac, hit_examples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--train", action="append", default=[], required=True,
                    help="training file to check (repeatable)")
    ap.add_argument("--eval", required=True, help="frozen eval question set")
    ap.add_argument("--max-overlap", type=float, default=0.01,
                    help="max allowed leaked-record fraction (default 0.01 = 1%%)")
    args = ap.parse_args()

    eval_norms = load_eval_norms(args.eval)
    if not eval_norms:
        print(f"[preflight] FATAL: eval set {args.eval} yielded 0 questions — "
              f"cannot verify contamination; refusing to proceed.", file=sys.stderr)
        return 2
    print(f"[preflight] eval set: {len(eval_norms)} unique questions "
          f"({args.eval}); max_overlap={args.max_overlap:.3f}")

    contaminated = False
    for path in args.train:
        total, hits, frac, examples = scan(path, eval_norms)
        status = "CLEAN" if frac <= args.max_overlap else "CONTAMINATED"
        print(f"[preflight] {status:>12}  {path}  "
              f"recs={total} leaked={hits} ({frac*100:.1f}%)")
        if frac > args.max_overlap:
            contaminated = True
            for ex in examples:
                print(f"[preflight]              e.g. leaked prompt: {ex!r}")

    if contaminated:
        print("[preflight] ABORT (§24): a training file overlaps the eval set beyond "
              "the threshold. Training on eval questions makes gate #6 fraudulent. "
              "Point the cycle at CLEAN data (grounded_*, dpo_pairs_v1_str, "
              "citation_dpo_v2, sft_distill_*) — NOT dpo_v1/dpo_v04.", file=sys.stderr)
        return 2
    print("[preflight] OK — no eval contamination; safe to train.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
