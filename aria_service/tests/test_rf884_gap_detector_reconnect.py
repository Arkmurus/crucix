"""R-F884 — gap_detector reconnected to the REAL producer stores.

Pre-R-F884 (Ecosystem-360, 2026-05-25) the coder's gap_detector read keys no
producer writes (`error_ledger`≠`error_log`, dead `health:perf`/`sweep:last_result`,
chat_audit `ts`≠`timestamp`) and ignored the two richest stores
(`capability_gaps`, `mistake_ledger:log`) → "0 raw signals" was literal, the
self-improve loop ran empty. This test seeds one recent entry into each REAL key
and asserts gap_detector.scan() now surfaces them.

Safety: reconnect is paired with ARIA_SELF_IMPROVE_AUTO_DEPLOY=0 (set live) so
the coder STAGES fixes for operator review, never auto-ships.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from aria_service.autonomous import gap_detector as gd


class _MockRedis:
    """Async stand-in: .get returns a string blob, .lrange returns a list of
    JSON strings — matching what set_json / lpush write."""
    def __init__(self, blobs: dict, lists: dict):
        self._blobs = blobs
        self._lists = lists
    async def get(self, key):
        return self._blobs.get(key)
    async def lrange(self, key, start, stop):
        return self._lists.get(key, [])


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_scan_surfaces_gaps_from_every_real_store():
    now_epoch = datetime.now(timezone.utc).timestamp()
    blobs = {
        # error_log — set_json blob (list), epoch timestamp, schema {type,message,file,...}
        "crucix:aria:error_log": json.dumps([
            {"type": "ValueError", "message": "pdf parse blew up in reader",
             "file": "document_reader.py", "function": "read", "traceback": "...",
             "timestamp": now_epoch},
        ]),
    }
    lists = {
        "crucix:chat_audit:log": [
            json.dumps({"timestamp": _now_iso(), "query": "how long do you remember?",
                        "response": "My memory has an 18-month TTL on facts."}),
        ],
        "crucix:aria:capability_gaps": [
            json.dumps({"id": "g1", "type": "file_parse", "detail": "could not parse XYZ",
                        "source": "routes.aria", "message_context": "", "timestamp": _now_iso(),
                        "resolved": False}),
        ],
        "crucix:mistake_ledger:log": [
            json.dumps({"mistake_id": "m1", "ts": _now_iso(), "category": "fabrication",
                        "what": "claimed a clause was absent from a truncated doc",
                        "why": "no partial-extraction guard", "fix": "add banner",
                        "severity": "HIGH", "task_type": "contract_review", "domain": "legal"}),
        ],
    }
    det = gd.GapDetector(redis_client=_MockRedis(blobs, lists))
    gaps = asyncio.run(det.scan())

    assert len(gaps) >= 4, [g.title for g in gaps]
    types = {g.gap_type for g in gaps}
    # error_log → DOCUMENT_PARSE (message mentions pdf/parse)
    assert gd.GapType.DOCUMENT_PARSE in types
    # chat_audit hallucination pattern → HALLUCINATION
    assert gd.GapType.HALLUCINATION in types
    # capability_gap file_parse → DOCUMENT_PARSE; mistake → MISSING_CAPABILITY
    assert gd.GapType.MISSING_CAPABILITY in types


def test_old_entries_outside_lookback_are_skipped():
    old_epoch = datetime.now(timezone.utc).timestamp() - 999999  # ~11.5 days ago
    blobs = {"crucix:aria:error_log": json.dumps([
        {"type": "X", "message": "old error", "file": "f.py", "timestamp": old_epoch},
    ])}
    det = gd.GapDetector(redis_client=_MockRedis(blobs, {}))
    assert asyncio.run(det.scan()) == []


def test_dead_extractors_dropped_real_added():
    names = [e.__class__.__name__ for e in gd.GapDetector(None).extractors]
    assert "HealthPerfExtractor" not in names and "SourceHealthExtractor" not in names
    assert "CapabilityGapExtractor" in names and "MistakeLedgerExtractor" in names
    assert gd.ErrorLedgerExtractor(None).KEY == "crucix:aria:error_log"


def test_rf889_operational_shed_is_skipped_not_a_gap():
    """R-F889 — designed wedge shed (neural timeout / concurrency cap) logged by
    brain_hook at WARNING flows to error_log, but it's NOT a code bug; the
    ErrorLedgerExtractor must skip it so the reconnected coder doesn't churn LLM
    budget 'fixing' intentional shed."""
    ex = gd.ErrorLedgerExtractor(None)
    assert ex._entry_to_gap({"type": "WARNING", "file": "brain_hook.py",
                             "message": "brain_hook(signal_generator): 1 errors — neural: timeout (>2.0s)"}) is None
    assert ex._entry_to_gap({"type": "WARNING", "file": "brain_hook.py",
                             "message": "absorb: concurrency cap (>0.5s wait)"}) is None
    # a real exception still becomes a gap
    real = ex._entry_to_gap({"type": "ValueError", "file": "document_reader.py",
                             "message": "list index out of range parsing pdf"})
    assert real is not None and real.gap_type == gd.GapType.DOCUMENT_PARSE


def test_rf908_more_operational_events_skipped():
    """R-F908 — circuit-breaker trips, mastery clamps, the R-F248 boot-state
    dump, by-design provider/security/infinite-memory events, and the coder's
    OWN 'not in whitelist' rejection are operational/by-design, not code bugs.
    Confirmed live 2026-05-26 (/api/aria/coder/gaps had 49 gaps, mostly these);
    they churned the coder's 6/hr budget. Real bugs must still pass through."""
    ex = gd.ErrorLedgerExtractor(None)
    skipped = [
        "[circuit_breaker] search:duckduckgo: CLOSED -> OPEN (5 consecutive failures)",
        "[circuit_breaker] CIRCUIT TRIPPED — p95=5764ms reason=absorb(knowledge_balkans)",
        "MASTERY HARD FLOOR BREACH: sanctions (50% < 50%) — remediation flagged",
        "[calibration] OVERCONFIDENT by 42.8pp — lowered mastery on 11 topics",
        "[R-F248] ARIA STATE AT BOOT — knowledge_facts=65540 ledger_signals=48062",
        "LLM fallback 'openai' skipped — missing API key. Set its env var.",
        "[neural] neuron 474120fa has 9488 edges > warn threshold 2000. NOT pruning",
        "Content threats detected from http://www.defensa.com/brasil/x: 1 threats",
        "Blocked URL: https://uk.linkedin.com/comm/in/x — Auth-required URL",
        "[aria_coder] stage_improvement failed for aria_service/intel/circuit_breaker.py: "
        "ARIA cannot modify aria_service/intel/circuit_breaker.py — not in whitelist",
        "[autonomous safety] rate limit hit: bucket already at cap 6 this hour. Skipped.",
    ]
    for msg in skipped:
        assert ex._entry_to_gap({"type": "WARNING", "file": "x.py", "message": msg}) is None, msg
    # Real bugs are deliberately NOT filtered.
    stall = ex._entry_to_gap({"type": "WARNING", "file": "main.py",
        "message": "[R-F703] event loop stalled for 52.92s (threshold=5.0s)"})
    assert stall is not None, "event-loop stall is a real perf bug — must stay actionable"
    codegen = ex._entry_to_gap({"type": "ERROR", "file": "self_improvement_codegen.py",
        "message": "Codegen JSON parse failed, storing as raw code"})
    assert codegen is not None, "codegen parse failure is a real bug — must stay"
    exc = ex._entry_to_gap({"type": "TypeError", "file": "researcher.py",
        "message": "'NoneType' object is not subscriptable"})
    assert exc is not None, "a genuine exception must still become a gap"


def test_mistake_ledger_is_observe_only_not_auto_fix():
    """Safety: mistake-ledger gaps map to MISSING_CAPABILITY (auto_fixable=False)
    so a recorded past mistake is surfaced for review, never auto-fixed."""
    g = gd.Gap(gap_id="x", gap_type=gd.GapType.MISSING_CAPABILITY, severity=gd.GapSeverity.HIGH,
               title="t", description="d", module="mistake_ledger")
    assert g.auto_fixable is False
