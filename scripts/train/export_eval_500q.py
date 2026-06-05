"""export_eval_500q — convert the frozen 500-Q golden set (Phase A gate #6)
into the JSONL format eval_aria_llm.py consumes (R-F1335).

The activation runbook (docs/aria_llm_v01_activation.md) previously said
`python -m aria_service.intel.eval_runner --export ...` — that CLI never
existed (eval_runner.py has no __main__). This script is the real path.

Source:  aria_service.intel.eval_golden_seed.SEED_ENTRIES (in-code, no
         server needed) — entries are {seed_id, category, question,
         expected_answer}.
Target:  JSONL lines {"question", "expected_keywords", "topic", "seed_id"}
         — eval_aria_llm.py passes a question when the response contains
         >=60% of expected_keywords (case-insensitive containment).

Keyword derivation (deterministic, no LLM):
  1. acronyms / all-caps tokens (OFAC, ITAR, SIPRI, NATO...)
  2. capitalised multi-word entities (proper nouns)
  3. numbers with units (ranges, calibres, percentages)
  4. fallback: longest distinctive lowercase terms
  capped at 8 per question; confidence tags ([CONFIRMED] etc.) excluded.

Usage:
  python scripts/train/export_eval_500q.py --out datasets/aria_eval_500q.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Confidence tags + generic words that prove nothing about correctness.
_TAG_RE = re.compile(r"\[(?:CONFIRMED|ASSESSED|UNVERIFIED|SPECULATIVE)[^\]]*\]")
_STOP = {
    "the", "and", "for", "with", "that", "this", "from", "have", "has",
    "are", "is", "was", "were", "not", "can", "cannot", "will", "would",
    "should", "could", "their", "there", "which", "when", "what", "who",
    "any", "all", "but", "you", "your", "its", "his", "her", "they",
    "them", "than", "then", "into", "onto", "over", "under", "about",
    "based", "without", "within", "also", "only", "more", "most", "such",
    "per", "via", "must", "may", "might", "been", "being", "because",
    "these", "those", "where", "while", "before", "after", "between",
    "i", "we", "no", "yes", "do", "does", "did", "of", "in", "on", "to",
    "a", "an", "it", "as", "at", "by", "or", "if", "be", "so", "my",
    "answer", "question", "give", "hold", "last", "prior", "recent",
    "range", "figure", "estimate", "data", "result", "tool", "hit",
}
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z0-9-]{1,9}\b")
_PROPER_RE = re.compile(r"\b(?:[A-Z][a-z][\w-]+(?:\s+[A-Z][a-z][\w-]+){0,3})\b")
_NUMUNIT_RE = re.compile(
    r"\b\d[\d,.]*\s?(?:km|m|mm|kg|t|%|nm|GHz|MHz|USD|EUR|GBP|units?|years?|days?)\b"
)


def derive_keywords(expected_answer: str, *, cap: int = 8) -> list[str]:
    text = _TAG_RE.sub(" ", expected_answer or "")
    out: list[str] = []
    seen: set[str] = set()

    def _add(kw: str) -> None:
        kw = kw.strip()
        k = kw.lower()
        if len(kw) < 3 or k in _STOP or k in seen:
            return
        seen.add(k)
        out.append(kw)

    for m in _ACRONYM_RE.findall(text):
        _add(m)
    for m in _NUMUNIT_RE.findall(text):
        _add(m)
    # Proper-noun phrases — skip sentence-initial false positives by
    # requiring the phrase to also appear mid-sentence OR be >1 word.
    for m in _PROPER_RE.findall(text):
        if " " in m or not text.lstrip().startswith(m):
            _add(m)
    if len(out) < 4:  # fallback: longest distinctive words
        words = sorted(
            {w for w in re.findall(r"[a-z]{6,}", text.lower()) if w not in _STOP},
            key=len, reverse=True,
        )
        for w in words[: cap - len(out)]:
            _add(w)
    return out[:cap]


def export(out_path: Path) -> dict:
    from aria_service.intel.eval_golden_seed import SEED_ENTRIES

    out_path.parent.mkdir(parents=True, exist_ok=True)
    n_written = 0
    n_thin = 0  # entries with <3 keywords — flagged, not silently dropped
    with out_path.open("w", encoding="utf-8") as fh:
        for e in SEED_ENTRIES:
            kws = derive_keywords(e.get("expected_answer", ""))
            if len(kws) < 3:
                n_thin += 1
            fh.write(json.dumps({
                "question": e["question"],
                "expected_keywords": kws,
                "topic": e.get("category", "general"),
                "seed_id": e.get("seed_id", ""),
            }, ensure_ascii=False) + "\n")
            n_written += 1
    return {"written": n_written, "thin_keyword_entries": n_thin, "path": str(out_path)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="output JSONL path")
    args = ap.parse_args()
    stats = export(Path(args.out))
    print(json.dumps(stats, indent=2))
    # No silent caps (§21): thin entries are reported so the operator
    # knows which fraction of the eval has weak keyword coverage.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
