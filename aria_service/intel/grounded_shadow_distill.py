"""R-F2527 — Grounded-synthesis SHADOW distillation corpus (the FLYWHEEL).

On every real grounded turn the model_router SHADOW stage (model_router._log_shadow)
generates DeepSeek AND the sovereign 7B for the SAME retrieved context and scores
BOTH with grounding_reward.score(). Today that comparison is thrown away — kept only
in an in-memory tally (_shadow_stats_acc / _shadow_recent, resets on restart). This
module DURABLY captures the discarded pair so the harvester
(scripts/train/harvest_grounded_flywheel.py) can turn it into contamination-safe DPO
preference pairs: the sovereign learns from the turns where one model was measurably
better-grounded than the other, on REAL production traffic.

This is the reasoning analog of brave_distill.py's teacher/student capture, and it is
built to the SAME shape on purpose (§8 mirror-a-proven-pattern):
  - append-only daily JSONL shards under a volume-backed directory (files only, §6/§7
    — no paid persistence, no eviction, off the state_store single-writer ceiling
    R-F2277/R-F2290 so high-volume shadow captures never add sqlite write load)
  - best-effort: NEVER blocks the turn, NEVER raises (fire-and-forget)
  - §21a-wired: success + failure both reach the brain via engine_wiring
  - FLAG-GATED OFF by default (ARIA_SHADOW_DISTILL_ENABLED) — deploying this changes
    nothing until the operator turns it on.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

logger = logging.getLogger("aria.grounded_shadow_distill")

# Volume-backed on fly (set ARIA_SHADOW_DISTILL_DIR=/data/grounded_shadow_distill);
# repo-relative in local dev. Kept off the state_store (rs) on purpose — shadow runs
# on every grounded turn, and that write volume must not touch the single-writer
# sqlite ceiling (R-F2277/R-F2290). Mirrors brave_distill's _CORPUS_DIR selection.
_CORPUS_DIR = Path(
    os.getenv("ARIA_SHADOW_DISTILL_DIR")
    or os.path.join(os.getenv("ARIA_DATA_DIR", "data"), "grounded_shadow_distill")
)
_MAX_MSG_LEN = 4000        # user message / prompt — bounded so a giant paste can't bloat a shard
_MAX_CONTEXT_LEN = 24000   # retrieved context is the training signal; keep it, but capped
_MAX_ANSWER_LEN = 12000    # per-model synthesis text
# DEFAULT OFF — flipping this on is the flywheel activation step (§6 default-safe rollout).
_ENABLED = (os.getenv("ARIA_SHADOW_DISTILL_ENABLED", "0") or "0").lower() in ("1", "true", "yes")


def _enabled() -> bool:
    """Read the flag live so tests / ops can toggle without re-import."""
    return (os.getenv("ARIA_SHADOW_DISTILL_ENABLED", "0") or "0").lower() in ("1", "true", "yes")


def _bd(breakdown) -> dict:
    """Normalize a grounding_reward.RewardBreakdown (or a plain dict) to a dict.
    Tolerant so capture never depends on the exact object type."""
    if breakdown is None:
        return {}
    as_dict = getattr(breakdown, "as_dict", None)
    if callable(as_dict):
        try:
            return dict(as_dict())
        except Exception:
            pass
    if isinstance(breakdown, dict):
        return dict(breakdown)
    return {}


def _fnum(d: dict, key: str, default: float = 0.0) -> float:
    try:
        v = d.get(key, default)
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def _inum(d: dict, key: str, default: int = 0) -> int:
    try:
        v = d.get(key, default)
        return int(v) if v is not None else default
    except (TypeError, ValueError):
        return default


def record_shadow_pair(
    message: str,
    context: str,
    deepseek_text: str,
    sovereign_text: str | None,
    deepseek_score: float,
    sovereign_score: float | None,
    deepseek_breakdown=None,
    sovereign_breakdown=None,
) -> None:
    """Append one grounded shadow comparison to today's shard. Best-effort;
    swallows every error and never blocks the turn.

    Only writes when BOTH sides actually produced a scored answer — a shadow
    turn where the sovereign errored/timed out (sovereign_text/score None) has no
    preference signal, so there is nothing to distil. Flag-gated OFF by default.
    """
    if not _enabled():
        return
    try:
        # No sovereign side => no comparison => nothing to learn from this turn.
        if sovereign_text is None or sovereign_score is None:
            return
        db = _bd(deepseek_breakdown)
        sb = _bd(sovereign_breakdown)
        margin = float(sovereign_score) - float(deepseek_score)
        rec = {
            "ts": time.time(),
            "message": (message or "")[:_MAX_MSG_LEN],
            "context": (context or "")[:_MAX_CONTEXT_LEN],
            "deepseek_text": (deepseek_text or "")[:_MAX_ANSWER_LEN],
            "sovereign_text": (sovereign_text or "")[:_MAX_ANSWER_LEN],
            "deepseek_score": round(float(deepseek_score), 4),
            "sovereign_score": round(float(sovereign_score), 4),
            "margin": round(margin, 4),  # sovereign - deepseek (winner sign)
            "deepseek_citation_precision": round(_fnum(db, "citation_precision"), 4),
            "sovereign_citation_precision": round(_fnum(sb, "citation_precision"), 4),
            "deepseek_fabricated_citations": _inum(db, "fabricated_citations"),
            "sovereign_fabricated_citations": _inum(sb, "fabricated_citations"),
            "deepseek_keyword_recall": round(_fnum(db, "keyword_recall"), 4),
            "sovereign_keyword_recall": round(_fnum(sb, "keyword_recall"), 4),
        }
        _CORPUS_DIR.mkdir(parents=True, exist_ok=True)
        # Daily shard keeps individual files bounded + easy to iterate for training.
        shard = _CORPUS_DIR / (time.strftime("%Y-%m-%d") + ".jsonl")
        with shard.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        # §21a — wire the successful capture to the brain (lightweight metric path).
        try:
            from .engine_wiring import wire_success
            wire_success(
                module="grounded_shadow_distill",
                summary=(f"captured shadow pair margin={margin:+.3f} "
                         f"(ds={deepseek_score:.3f} sov={sovereign_score:.3f})"),
                source_id="grounded_shadow_distill:record",
            )
        except Exception:
            pass
    except Exception as e:  # never let capture break a turn
        logger.debug("grounded_shadow_distill capture failed (non-fatal): %s", e)
        try:
            from .engine_wiring import wire_failure
            wire_failure(
                module="grounded_shadow_distill",
                detail=f"shadow-pair capture failed: {e}",
                gap_type="engine_failure",
                source="grounded_shadow_distill:record",
            )
        except Exception:
            pass


def stats() -> dict:
    """Lightweight corpus summary for the ops/proprioception surface (§25)."""
    try:
        if not _CORPUS_DIR.exists():
            return {"records": 0, "shards": 0, "dir": str(_CORPUS_DIR),
                    "enabled": _enabled()}
        shards = sorted(_CORPUS_DIR.glob("*.jsonl"))
        total = 0
        for s in shards:
            try:
                with s.open("r", encoding="utf-8") as f:
                    total += sum(1 for _ in f)
            except Exception:
                continue
        return {"records": total, "shards": len(shards), "enabled": _enabled(),
                "dir": str(_CORPUS_DIR), "latest": shards[-1].name if shards else None}
    except Exception as e:
        return {"error": str(e), "dir": str(_CORPUS_DIR)}
