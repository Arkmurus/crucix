"""Output harvester — captures high-quality ARIA chat outputs into a
training corpus on /data, for later fine-tuning / DPO / capability
transfer to local models.

Dry-run by default
──────────────────
Ships 2026-04-20 in dry-run mode. `ARIA_OUTPUT_HARVEST_ENABLED=0`
(default) means this module SCORES every response but writes
nothing. We use the first 2 weeks to:

  1. Calibrate the 0.75 quality threshold against a 100-example
     human-graded sample.
  2. Measure how many responses would pass per day.
  3. Validate the PII redactor against real chat data without risk
     of exfiltration (scores are logged; redacted text is never
     written anywhere in dry-run).

When the operator flips `ARIA_OUTPUT_HARVEST_ENABLED=1`, the
harvester starts appending passing examples to
`/data/aria_training/harvest-{YYYY-MM-DD}.jsonl`.

Scoring signals (all deterministic, no LLM call)
────────────────────────────────────────────────
Each response gets a score in [0.0, 1.0] composed from:
  • cited_source         : response references at least one URL  (+0.30)
  • constitutional_tag   : response cites a clause marker (e.g.
                           [CONFIRMED], [ASSESSED], Clause N)      (+0.25)
  • has_structure        : contains headers / bullet lists /
                           BOTTOM LINE framing                     (+0.15)
  • length_reasonable    : 400..6000 chars (too short = trivial,
                           too long = probably rambling)           (+0.15)
  • no_refusal_pattern   : absence of "I can't", "I'm unable",
                           "as an AI"                              (+0.15)

Threshold: `ARIA_OUTPUT_HARVEST_THRESHOLD` (default "0.75").

PII redaction
─────────────
Applied BEFORE scoring, so the score reflects the redacted text that
would actually be written. Redaction targets:
  • Email addresses        -> [EMAIL]
  • Phone-like digits      -> [PHONE]
  • 12-digit+ number runs  -> [ID_NUM]
  • Known personal tokens  -> [NAME] (names on the operator's
                              protected list via env var
                              ARIA_HARVEST_REDACT_NAMES, comma-sep)

NOT a substitute for
────────────────────
  - training_export (captures selected training pairs on demand)
  - mem0 (operator's personal notebook)
  - verified_intel (knowledge facts with provenance)

This is specifically for "ARIA did well here, keep this as a
fine-tune positive example" data collection.

Public API
──────────
  score_response(user_msg, response) -> dict
  harvest(user_msg, response, meta=None) -> dict     # fire-and-forget
  stats() -> dict                                     # for dashboards
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.learning.output_harvester")

# R-F4052 (C-108) — §21a wiring for this module's WORK, not its import.
#
# The old R-F1319 block fired wire_success at IMPORT time under
# "learning.output_harvester" — a name brain_hook._MODULE_TOPICS does not contain, so it
# was invisible to `never_seen` (C-104) and proved only that the file had been
# imported. Outcomes are now reported under the REGISTERED name, on BOTH
# branches. Success is throttled because this module's work runs per item.


def _wire_output_harvester(ok: bool, summary: str) -> None:
    """Report a real outcome to the brain. Never raises."""
    try:
        from aria_service.intel.engine_wiring import (
            wire_failure, wire_success_throttled,
        )
        if ok:
            wire_success_throttled(
                "output_harvester", summary, source_id="output_harvester:work",
            )
        else:
            wire_failure(
                module="output_harvester", detail=summary,
                gap_type="engine_failure", source="output_harvester:work",
            )
    except Exception as _exc:   # pragma: no cover - telemetry never blocks
        # Swallowed deliberately: brain wiring must never break this
        # module's work. Logged rather than `pass`ed so the wiring
        # failure is not itself dark (bandit B110) — wiring the wiring
        # failure would be circular.
        logger.debug("[R-F4052] output_harvester outcome wiring failed: %s", _exc)


_DATA_DIR = Path(os.getenv("ARIA_HARVEST_DIR", "/data/aria_training"))
_MIN_LEN = 400
_MAX_LEN = 6000

# ── Scoring weights (sum to 1.00 at max) ──────────────────────────────────
_W_CITED       = 0.30
_W_CLAUSE      = 0.25
_W_STRUCTURE   = 0.15
_W_LEN_OK      = 0.15
_W_NO_REFUSAL  = 0.15

# Compile once at import
_URL_RE       = re.compile(r"https?://[^\s<>\"')\]}]+", re.IGNORECASE)
_CLAUSE_RE    = re.compile(
    r"\[(?:CONFIRMED|ASSESSED|STALE|UNVERIFIED|CONTRADICTED|"
    r"CITATION-QUARANTINED|MEMORY|WEB|CONFLICT)\]"
    r"|\bClause\s+\d+\b"
    r"|BOTTOM\s+LINE",
    re.IGNORECASE,
)
_STRUCTURE_RE = re.compile(
    r"(?:^|\n)\s*(?:[-*•]\s|\d+\.\s|\*\*[^*]+\*\*|#{1,3}\s)",
    re.MULTILINE,
)
_REFUSAL_RE = re.compile(
    r"\b(?:"
    r"I\s+can'?t\s+(?:help|assist|do|provide)|"
    r"I'?m\s+(?:unable|sorry|not\s+able)|"
    r"as\s+an\s+AI|"
    r"I\s+don'?t\s+have\s+the\s+ability|"
    r"I\s+cannot\s+(?:help|assist|provide)"
    r")\b",
    re.IGNORECASE,
)

# PII patterns
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]?\d{3,4}[\s.-]?\d{3,4}"
)
_LONG_NUM_RE = re.compile(r"\b\d{12,}\b")

# ── R-F4080 (C-128) — the key must name the window it actually keeps ────────
#
# This was `crucix:learning:harvest:stats_24h` while the write below uses
# `ex=14 * 86400`. The 14-day window is DELIBERATE (see `_incr_stats`: an
# earlier version reset the TTL on every event so the counter never rolled at
# all) — the NAME was the defect. Measured live 2026-08-16 against every
# sibling:
#
#     281.5h  crucix:learning:harvest:stats_24h     <- this one
#       0.7h  crucix:learning:spider:stats_24h
#       3.8h  crucix:learning:research:stats_24h
#       7.5h  crucix:learning:memory_backup:stats_24h
#
# Found during the C-109 TTL audit and left standing, which is how a fix for a
# CLASS of defect becomes a fix for one instance. Anyone comparing
# `harvest.total_scored` against a genuinely-24h sibling was comparing a
# fortnight to a day with nothing on the surface to say so.
#
# The duration is now the single source for both the TTL and the key suffix, so
# a future change to one cannot leave the other behind.
_STATS_TTL_S = 14 * 86400
_REDIS_STATS_KEY = f"crucix:learning:harvest:stats_{_STATS_TTL_S // 86400}d"
_LEGACY_STATS_KEY = "crucix:learning:harvest:stats_24h"
# Carry the old counters across once rather than discarding a fortnight of
# scoring to fix a label. Process-local: one extra read per process, not per
# harvest event.
_legacy_stats_migrated = False


# ═══════════════════════════════════════════════════════════════════════
# Configuration helpers
# ═══════════════════════════════════════════════════════════════════════

def is_enabled() -> bool:
    """Is the harvester in live mode? False = dry-run (score only,
    no writes)."""
    return (os.getenv("ARIA_OUTPUT_HARVEST_ENABLED", "0") or "0").strip() == "1"


def _threshold() -> float:
    try:
        return float(os.getenv("ARIA_OUTPUT_HARVEST_THRESHOLD", "0.75"))
    except (TypeError, ValueError):
        return 0.75


def _redact_names() -> list[str]:
    raw = (os.getenv("ARIA_HARVEST_REDACT_NAMES") or "").strip()
    return [n.strip() for n in raw.split(",") if n.strip()]


# ═══════════════════════════════════════════════════════════════════════
# PII redaction — applied BEFORE scoring
# ═══════════════════════════════════════════════════════════════════════

def _redact(text: str) -> str:
    """Conservative PII scrub. Never raises, silently drops any pattern
    that doesn't apply. Applied once — the caller passes redacted text
    to both the scorer and the writer."""
    if not text:
        return ""
    out = _EMAIL_RE.sub("[EMAIL]", text)
    out = _PHONE_RE.sub("[PHONE]", out)
    out = _LONG_NUM_RE.sub("[ID_NUM]", out)
    for name in _redact_names():
        if name:
            # Case-insensitive whole-word replace
            out = re.sub(
                rf"\b{re.escape(name)}\b",
                "[NAME]",
                out,
                flags=re.IGNORECASE,
            )
    return out


# ═══════════════════════════════════════════════════════════════════════
# Scoring — deterministic, LLM-free
# ═══════════════════════════════════════════════════════════════════════

def score_response(user_msg: str, response: str) -> dict[str, Any]:
    """Compute the quality score for a (prompt, response) pair. Returns
    a breakdown dict — makes the threshold calibration process concrete
    (we can see exactly which signals are doing the work).

    Operates on the ALREADY-REDACTED response so signals are measured
    on the text that would actually be written to disk."""
    clean = _redact(response or "")
    signals: dict[str, bool] = {
        "cited_source":        bool(_URL_RE.search(clean)),
        "constitutional_tag": bool(_CLAUSE_RE.search(clean)),
        "has_structure":       bool(_STRUCTURE_RE.search(clean)),
        "length_reasonable":   _MIN_LEN <= len(clean) <= _MAX_LEN,
        "no_refusal_pattern":  not bool(_REFUSAL_RE.search(clean)),
    }
    score = 0.0
    if signals["cited_source"]:       score += _W_CITED
    if signals["constitutional_tag"]: score += _W_CLAUSE
    if signals["has_structure"]:      score += _W_STRUCTURE
    if signals["length_reasonable"]:  score += _W_LEN_OK
    if signals["no_refusal_pattern"]: score += _W_NO_REFUSAL
    return {
        "score": round(score, 3),
        "signals": signals,
        "chars": len(clean),
        "had_pii": clean != (response or ""),
    }


# ═══════════════════════════════════════════════════════════════════════
# Write path — gated behind ARIA_OUTPUT_HARVEST_ENABLED
# ═══════════════════════════════════════════════════════════════════════

def _daily_file() -> Path:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _DATA_DIR / f"harvest-{today}.jsonl"


def _write(record: dict) -> tuple[bool, str]:
    """Append one JSONL record. Returns (wrote, reason).
    Directory is created on first call."""
    try:
        _DATA_DIR.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return (False, f"mkdir_failed:{e}")
    try:
        path = _daily_file()
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, default=str) + "\n")
        return (True, "written")
    except Exception as e:
        return (False, f"write_failed:{e}")


async def _incr_stats(passed: bool, dry_run: bool) -> None:
    """Best-effort Redis counter for the dashboard. Never raises.

    Uses keepttl on subsequent writes so the 14-day rolling window
    actually rolls. The previous version called set_json with ex=14d on
    every event, which reset the TTL each time -- under continuous
    harvest traffic the counter never expired and `counters_rolling`
    on the dashboard became a lifetime tally.
    """
    try:
        from ..intel import redis_store as rs
        existing = await rs.get_json(_REDIS_STATS_KEY)
        # R-F4080 — one-time carry-over from the misnamed key, then retire it.
        # Retiring matters: two live counters for one thing is worse than the
        # wrong name, because the next reader has to guess which is current.
        global _legacy_stats_migrated
        if not isinstance(existing, dict) and not _legacy_stats_migrated:
            _legacy_stats_migrated = True
            try:
                legacy = await rs.get_json(_LEGACY_STATS_KEY)
                if isinstance(legacy, dict):
                    existing = legacy
                    await rs.delete(_LEGACY_STATS_KEY)
            except Exception as _mig_e:
                logger.debug("harvest stats carry-over skipped: %s", _mig_e)
        is_fresh = not isinstance(existing, dict)
        stats = existing if isinstance(existing, dict) else {
            "total_scored": 0,
            "total_passed": 0,
            "total_written": 0,
            "total_dry_skipped": 0,
        }
        stats["total_scored"] = int(stats.get("total_scored", 0)) + 1
        if passed:
            stats["total_passed"] = int(stats.get("total_passed", 0)) + 1
        if dry_run and passed:
            stats["total_dry_skipped"] = int(stats.get("total_dry_skipped", 0)) + 1
        elif passed:
            stats["total_written"] = int(stats.get("total_written", 0)) + 1
        if is_fresh:
            await rs.set_json(_REDIS_STATS_KEY, stats, ex=_STATS_TTL_S)
        else:
            await rs.set_json(_REDIS_STATS_KEY, stats, keepttl=True)
    except Exception as e:
        logger.debug("output_harvester stats incr failed: %s", e)


# ═══════════════════════════════════════════════════════════════════════
# Public entry point
# ═══════════════════════════════════════════════════════════════════════

async def harvest(
    user_msg: str,
    response: str,
    *,
    meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Score + (if enabled) write a chat-turn to the training corpus.

    Fire-and-forget — never raises. Call from the chat pipeline after
    the final response is delivered. Example:

        from aria_service.learning import output_harvester as _oh
        await _oh.harvest(user_msg, final_response, meta={
            "session_id": session_id,
            "model": model,
            "trace_id": trace_id,
        })
    """
    try:
        if not response or not isinstance(response, str):
            return {"ok": False, "reason": "empty_response"}
        clean_response = _redact(response)
        clean_user_msg = _redact(user_msg or "")
        scored = score_response(user_msg, clean_response)
        passed = scored["score"] >= _threshold()
        dry = not is_enabled()

        out: dict[str, Any] = {
            "ok": True,
            "score": scored["score"],
            "passed": passed,
            "dry_run": dry,
            "chars": scored["chars"],
            "signals": scored["signals"],
            "had_pii": scored["had_pii"],
            "written": False,
            "reason": "dry_run" if (dry and passed) else ("below_threshold" if not passed else "pending"),
        }

        if passed and not dry:
            record = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "score": scored["score"],
                "signals": scored["signals"],
                "user_msg": clean_user_msg[:_MAX_LEN],
                "response":  clean_response[:_MAX_LEN],
                "meta": meta or {},
            }
            wrote, reason = _write(record)
            out["written"] = wrote
            out["reason"] = reason

        await _incr_stats(passed=passed, dry_run=dry)
        _wire_output_harvester(
            True,
            f"harvested chat turn (passed={passed}, written={out.get('written')})",
        )
        return out
    except Exception as e:
        logger.debug("harvest exception (non-fatal): %s", e)
        _wire_output_harvester(False, f"harvest failed: {type(e).__name__}: {e}")
        return {"ok": False, "reason": f"exception:{type(e).__name__}"}


# ═══════════════════════════════════════════════════════════════════════
# Stats
# ═══════════════════════════════════════════════════════════════════════

async def stats() -> dict[str, Any]:
    """Return the dashboard-side view. Complements score_response for
    dev debugging."""
    try:
        from ..intel import redis_store as rs
        counters = await rs.get_json(_REDIS_STATS_KEY) or {}
        # R-F4082 — the READ must fall back to the legacy key too. R-F4080's
        # carry-over fires on the next _incr_stats, i.e. the next harvest event,
        # so between deploy and that event the dashboard read `{}` and 21
        # accumulated scores looked like none. Caught on the live box, not in a
        # test: the fixture drove the write path, which is exactly where the
        # migration was.
        #
        # Read-only on purpose. It does NOT delete the legacy key and does NOT
        # write the new one — C-112's whole finding was a GET that mutated
        # state, and repeating that here to save one branch would be perverse.
        # The write path still owns the one-time migration.
        if not counters:
            counters = await rs.get_json(_LEGACY_STATS_KEY) or {}
    except Exception:
        counters = {}
    return {
        "enabled": is_enabled(),
        "threshold": _threshold(),
        "data_dir": str(_DATA_DIR),
        "today_file": str(_daily_file()),
        "today_file_exists": _daily_file().exists(),
        "counters_rolling": counters,
    }


def summary() -> dict[str, Any]:
    """Capability-manifest summary — shape matches knowledge_spider.summary()."""
    return {
        "enabled": is_enabled(),
        "threshold": _threshold(),
        "redacts": ["email", "phone", "id_num", "names_env"],
        "data_dir": str(_DATA_DIR),
    }
