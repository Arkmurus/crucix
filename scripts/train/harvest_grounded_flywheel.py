#!/usr/bin/env python3
"""R-F2527 — harvest the grounded-synthesis SHADOW corpus into DPO pairs (FLYWHEEL).

grounded_shadow_distill.py durably captures, for every real grounded turn, the
DeepSeek vs sovereign comparison the model_router SHADOW stage computes and throws
away: {message, context, deepseek_text, sovereign_text, deepseek_score,
sovereign_score, margin, citation_precision..., fabricated_citations...}. This
script reads those daily shards and emits a CLEAN, contamination-safe, non-degenerate
DPO preference set the sovereign can train on — the same {prompt, chosen, rejected,
meta} schema as scripts/train/build_dpo_from_report.py.

Selection (all objective, no silent caps — every drop is bucketed + printed):
  - MARGIN: keep only |sovereign_score - deepseek_score| >= --margin (default 0.25)
    so we train on turns where one model was MEASURABLY better grounded.
  - WINNER FULLY GROUNDED: the chosen (winner) answer must have citation_precision
    == 1.0 AND 0 fabricated citations AND score >= --min-win (default 0.6). We never
    teach the model to prefer an ungrounded / fabricating answer.
  - ANTI-DEGENERACY (mode-collapse guard): when the winner is the SOVEREIGN's own
    text, keep it only when the margin is large (>= --degen-margin, default 0.4) and
    DeepSeek is the rejected — otherwise training on the model's own outputs risks
    mode collapse. Provenance recorded in meta.degen_guard.
  - CONTAMINATION: drop any row whose message overlaps the frozen 500-Q eval, by
    EXACT normalized match (preflight_eval_contamination.norm) PLUS a fuzzy token
    Jaccard >= --jaccard (default 0.75) near-dup check. Never poison gate #6.

§24: this only PREPARES data (no GPU, no serving, no training). Run the existing
scripts/train/preflight_eval_contamination.py on the output before any paid cycle.

Usage:
  python scripts/train/harvest_grounded_flywheel.py \
      --corpus-dir data/grounded_shadow_distill \
      --out data/training/aria_flywheel_dpo.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Reuse preflight's EXACT normalization + question extractor so the contamination
# gate here is byte-identical to the one the training cycle enforces (R-F2367).
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
try:
    from preflight_eval_contamination import norm as _norm, extract_q as _extract_q
except Exception:  # pragma: no cover — fallback keeps the harvester runnable standalone
    def _norm(s: str) -> str:
        s = (s or "").strip().lower()
        s = re.sub(r"\s+", " ", s)
        return s.rstrip(" ?.!:")

    def _extract_q(rec) -> str:
        if not isinstance(rec, dict):
            return ""
        for k in ("question", "prompt"):
            v = rec.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(s: str) -> set:
    return set(_TOKEN_RE.findall((s or "").lower()))


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    if not inter:
        return 0.0
    return inter / len(a | b)


def load_eval(paths: list[str]) -> tuple[set, list[set]]:
    """Return (exact-normalized question set, list of token-sets) for the frozen eval.
    Both files are the SAME 500 questions in different schemas — union them."""
    exact: set = set()
    token_sets: list[set] = []
    for path in paths:
        p = Path(path)
        if not p.exists():
            print(f"[flywheel] WARN: eval file not found, skipping: {path}", file=sys.stderr)
            continue
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                q = d.get("question") or _extract_q(d)
                if not q:
                    continue
                nq = _norm(q)
                if nq and nq not in exact:
                    exact.add(nq)
                    token_sets.append(_tokens(nq))
    return exact, token_sets


def _is_contaminated(message: str, exact: set, token_sets: list[set], jac_thr: float) -> bool:
    nmsg = _norm(message)
    if not nmsg:
        return False
    if nmsg in exact:                       # exact normalized match
        return True
    mtok = _tokens(nmsg)
    if not mtok:
        return False
    for ts in token_sets:                   # fuzzy near-dup
        if _jaccard(mtok, ts) >= jac_thr:
            return True
    return False


def iter_captured(corpus_dir: Path):
    """Yield every captured shadow-pair record across all daily shards."""
    if not corpus_dir.exists():
        return
    for shard in sorted(corpus_dir.glob("*.jsonl")):
        try:
            with shard.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except Exception:
                        continue
        except Exception:
            continue


def _f(rec: dict, key: str, default: float = 0.0) -> float:
    try:
        v = rec.get(key, default)
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _i(rec: dict, key: str, default: int = 0) -> int:
    try:
        v = rec.get(key, default)
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def harvest(records, exact: set, token_sets: list[set], *, margin: float,
            min_win: float, degen_margin: float, jaccard: float):
    """Apply the full selection pipeline; return (pairs, drops)."""
    pairs = []
    seen: set = set()
    drops = {
        "no_signal_tie": 0, "margin": 0, "below_min_win": 0, "not_grounded": 0,
        "empty": 0, "identical": 0, "degeneracy": 0, "contamination": 0, "dup": 0,
    }
    total = 0
    for rec in records:
        total += 1
        if not isinstance(rec, dict):
            continue
        message = rec.get("message") or ""
        ds_text = (rec.get("deepseek_text") or "").strip()
        sov_text = (rec.get("sovereign_text") or "").strip()
        ds_score = _f(rec, "deepseek_score")
        sov_score = _f(rec, "sovereign_score")
        margin_abs = abs(sov_score - ds_score)

        if sov_score == ds_score:               # tie — no preference signal
            drops["no_signal_tie"] += 1
            continue
        if margin_abs < margin:
            drops["margin"] += 1
            continue

        sovereign_won = sov_score > ds_score
        if sovereign_won:
            chosen, rejected = sov_text, ds_text
            win_prec = _f(rec, "sovereign_citation_precision")
            win_fab = _i(rec, "sovereign_fabricated_citations")
            win_score = sov_score
            winner = "sovereign"
        else:
            chosen, rejected = ds_text, sov_text
            win_prec = _f(rec, "deepseek_citation_precision")
            win_fab = _i(rec, "deepseek_fabricated_citations")
            win_score = ds_score
            winner = "deepseek"

        if win_score < min_win:
            drops["below_min_win"] += 1
            continue
        if not (win_prec >= 1.0 and win_fab == 0):   # winner must be fully grounded
            drops["not_grounded"] += 1
            continue
        if not chosen or not rejected:
            drops["empty"] += 1
            continue
        if chosen == rejected:
            drops["identical"] += 1
            continue

        # Anti-degeneracy (mode collapse): winner is the sovereign's own text.
        degen_guard = False
        if winner == "sovereign":
            if margin_abs < degen_margin:
                drops["degeneracy"] += 1
                continue
            degen_guard = True  # kept only because margin is large + DeepSeek is rejected

        # Contamination — never let the frozen eval leak into training.
        if _is_contaminated(message, exact, token_sets, jaccard):
            drops["contamination"] += 1
            continue

        nmsg = _norm(message)
        if nmsg in seen:
            drops["dup"] += 1
            continue
        seen.add(nmsg)

        pairs.append({
            "prompt": message,
            "chosen": chosen,
            "rejected": rejected,
            "meta": {
                "source": "flywheel",
                "margin": round(margin_abs, 4),
                "winner": winner,
                "winner_score": round(win_score, 4),
                "winner_citation_precision": round(win_prec, 4),
                "degen_guard": degen_guard,
            },
        })
    return pairs, drops, total


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus-dir", type=Path,
                    default=Path(os.getenv("ARIA_SHADOW_DISTILL_DIR")
                                 or os.path.join(os.getenv("ARIA_DATA_DIR", "data"),
                                                 "grounded_shadow_distill")),
                    help="dir of grounded_shadow_distill daily JSONL shards")
    ap.add_argument("--out", type=Path, default=Path("data/training/aria_flywheel_dpo.jsonl"))
    ap.add_argument("--eval", action="append", default=None,
                    help="frozen eval file(s) to exclude (repeatable); default = the "
                         "500-Q closed-book + openbook sets")
    ap.add_argument("--margin", type=float, default=0.25,
                    help="min |sovereign_score - deepseek_score| to keep a pair")
    ap.add_argument("--min-win", type=float, default=0.6,
                    help="min grounding score of the winning (chosen) answer")
    ap.add_argument("--degen-margin", type=float, default=0.4,
                    help="min margin required when the winner is the sovereign's own text")
    ap.add_argument("--jaccard", type=float, default=0.75,
                    help="token-Jaccard near-dup threshold for contamination")
    args = ap.parse_args()

    eval_paths = args.eval or [
        "data/eval_frozen/aria_eval_500q.jsonl",
        "data/eval_reports/aria_eval_500q_openbook.jsonl",
    ]
    exact, token_sets = load_eval(eval_paths)
    if not exact:
        print("[flywheel] FATAL: eval set yielded 0 questions — cannot verify "
              "contamination; refusing to emit training data.", file=sys.stderr)
        return 2

    pairs, drops, total = harvest(
        iter_captured(args.corpus_dir), exact, token_sets,
        margin=args.margin, min_win=args.min_win,
        degen_margin=args.degen_margin, jaccard=args.jaccard,
    )

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p, ensure_ascii=False) + "\n")

    # REPORT — no silent caps; every drop bucket is named.
    print(f"[flywheel] corpus dir      : {args.corpus_dir}")
    print(f"[flywheel] eval guard      : {len(exact)} unique frozen questions "
          f"(exact + Jaccard>={args.jaccard})")
    print(f"[flywheel] total captured  : {total}")
    print(f"[flywheel] selected pairs  : {len(pairs)} -> {args.out}")
    print(f"[flywheel] dropped-margin       : {drops['margin']}")
    print(f"[flywheel] dropped-below-min-win: {drops['below_min_win']}")
    print(f"[flywheel] dropped-not-grounded : {drops['not_grounded']}")
    print(f"[flywheel] dropped-degeneracy   : {drops['degeneracy']}")
    print(f"[flywheel] dropped-contamination: {drops['contamination']}")
    print(f"[flywheel] dropped-tie          : {drops['no_signal_tie']}")
    print(f"[flywheel] dropped-empty        : {drops['empty']}")
    print(f"[flywheel] dropped-identical    : {drops['identical']}")
    print(f"[flywheel] dropped-dup          : {drops['dup']}")
    winners = {}
    for p in pairs:
        winners[p["meta"]["winner"]] = winners.get(p["meta"]["winner"], 0) + 1
    print(f"[flywheel] winner spread   : {winners}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
