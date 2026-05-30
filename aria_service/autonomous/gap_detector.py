"""R-F802 — Gap detection engine.

Continuously monitors production signals to detect capability gaps,
bugs, and missing features. Feeds structured `Gap` objects to the
ARIACoder for autonomous remediation.

Signal sources
──────────────
  ErrorLedgerExtractor    — reads exceptions from `crucix:aria:error_log`
  ChatAuditExtractor      — scans `crucix:chat_audit:log` for hallucination
                            patterns matching `SELF_INTROSPECTION_PATTERNS`
  CapabilityGapExtractor  — reads `crucix:aria:capability_gaps` (R-F884)
  MistakeLedgerExtractor  — reads `crucix:mistake_ledger:log` (R-F884)
  OpportunityExtractor    — scans `crucix:chat_audit:log` for opportunities
                            (R-F826)

NOTE (R-F884): HealthPerfExtractor and SourceHealthExtractor were DROPPED
because no producer writes their keys (`crucix:health:perf:latest` and
`crucix:sweep:last_result`). They remain as class definitions for reference
but are NOT in the active extractor list.

Dedup
─────
  Gap.gap_id is sha256(gap_type + module + msg_prefix), so duplicate
  gaps within 24h collapse to one. A gap marked fixed is suppressed
  for `DEDUP_WINDOW_S` (24h). A gap recently attempted (but not yet
  fixed) is suppressed for 1h to avoid loop storms.

Lineage
───────
Architecture from `aria_autonomy_engine.zip` (operator-shared, 2026-05-22).
Redis keys renamed from `aria:*` to `crucix:*` to match prod conventions
(see `aria_service/intel/chat_audit_log.py:35-38`).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from typing import Any, Optional

logger = logging.getLogger("aria.autonomous.gap_detector")


# ── TAXONOMY ─────────────────────────────────────────────────────────────────

class GapSeverity(IntEnum):
    LOW = 1       # cosmetic, minor performance
    MEDIUM = 2    # functional gap, degraded quality
    HIGH = 3      # feature missing, DD layer failing
    CRITICAL = 4  # hallucination, safety bypass, data loss risk


class GapType(str):
    """String-typed enum so we can use values as Redis keys and dict keys."""
    MODULE_BUG = "module_bug"                  # exception in existing code
    MISSING_CAPABILITY = "missing_capability"  # feature ARIA needs but lacks
    DATA_GAP = "data_gap"                      # knowledge/source coverage gap
    HALLUCINATION = "hallucination"            # guard violation / invented fact
    PERFORMANCE = "performance"                # latency / memory / cost regression
    DOCUMENT_PARSE = "document_parse"          # PDF/DOCX ingestion failure
    SOURCE_FAILURE = "source_failure"          # RSS/API source returning errors
    DD_LAYER_FAILURE = "dd_layer_failure"      # specific DD layer wrong output
    INTROSPECTION_ERROR = "introspection_error"  # ARIA wrong about herself
    OPPORTUNITY = "opportunity"                # R-F826: proactive enhancement —
                                               # ARIA could be better here, not broken


# (gap_type → (auto_fixable, requires_wa_approval, requires_hard_gate))
#
# Deterministic risk routing — NOT LLM-judged. Per CLAUDE.md §3 we cannot
# trust an LLM to self-report whether its own change is risky.
AUTONOMY_LEVEL: dict[str, tuple[bool, bool, bool]] = {
    GapType.MODULE_BUG:          (True,  False, False),
    GapType.PERFORMANCE:         (True,  False, False),
    GapType.SOURCE_FAILURE:      (True,  False, False),
    GapType.DOCUMENT_PARSE:      (True,  False, False),
    GapType.DATA_GAP:            (True,  False, False),
    GapType.HALLUCINATION:       (True,  True,  False),  # always notify
    GapType.DD_LAYER_FAILURE:    (True,  True,  False),
    GapType.MISSING_CAPABILITY:  (False, True,  False),  # operator decides
    GapType.INTROSPECTION_ERROR: (True,  True,  False),
    GapType.OPPORTUNITY:         (False, True,  False),  # operator decides
                                                          # (proactive, never urgent)
}


@dataclass
class Gap:
    gap_id: str
    gap_type: str
    severity: GapSeverity
    title: str
    description: str
    module: str
    related_files: list[str] = field(default_factory=list)
    error_trace: Optional[str] = None
    evidence: dict[str, Any] = field(default_factory=dict)
    detected_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    fix_attempts: int = 0
    last_seen: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def auto_fixable(self) -> bool:
        return AUTONOMY_LEVEL.get(self.gap_type, (False, False, False))[0]

    @property
    def requires_wa_approval(self) -> bool:
        return AUTONOMY_LEVEL.get(self.gap_type, (False, False, False))[1]

    @property
    def requires_hard_gate(self) -> bool:
        return AUTONOMY_LEVEL.get(self.gap_type, (False, False, False))[2]

    def to_dict(self) -> dict:
        return asdict(self)


def _gap_id_for(gap_type: str, module: str, msg_prefix: str) -> str:
    return hashlib.sha256(
        f"{gap_type}{module}{msg_prefix[:100]}".encode("utf-8")
    ).hexdigest()[:16]


def _entry_ts_epoch(ts: Any) -> float:
    """R-F884 — normalise a store entry's timestamp to epoch seconds.
    Producers write either an ISO string (chat_audit, capability_gaps,
    mistake_ledger) or an epoch float (error_log). Returns 0.0 if unparseable
    (→ the entry is treated as old / outside the lookback window)."""
    if ts is None:
        return 0.0
    if isinstance(ts, (int, float)):
        return float(ts)
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
        except ValueError:
            try:
                return float(ts)
            except ValueError:
                return 0.0
    return 0.0


# ── EXTRACTORS ───────────────────────────────────────────────────────────────

class ErrorLedgerExtractor:
    """R-F884 — read errors from `crucix:aria:error_log` (the REAL producer key).

    Pre-R-F884 this read `crucix:aria:error_ledger` via lrange — a key NO
    producer writes, so the coder saw zero errors. self_improve.record_error
    (`self_improve.py:1105`) writes `crucix:aria:error_log` via `set_json` as a
    JSON BLOB (a list), not a Redis list — so we GET + json.loads, not lrange.
    Entry schema: {type, message, file, function, traceback, timestamp(epoch)}.
    There is no severity LEVEL field — every recorded entry IS an error.
    """

    KEY = "crucix:aria:error_log"

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        since_ts = since.timestamp()
        try:
            raw = await self.redis.get(self.KEY)
        except Exception as e:
            logger.error("[gap_detector] error_log read failed: %s", e)
            return gaps
        if not raw:
            return gaps
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            entries = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, ValueError):
            return gaps
        if not isinstance(entries, list):
            return gaps

        for entry in entries:
            try:
                if not isinstance(entry, dict):
                    continue
                if float(entry.get("timestamp", 0) or 0) < since_ts:
                    continue
                gap = self._entry_to_gap(entry)
                if gap is not None:
                    gaps.append(gap)
            except (ValueError, TypeError, AttributeError):
                continue
        return gaps

    # R-F889 — operational fail-fast SHED that is BY DESIGN, not a code bug.
    # brain_hook logs these at WARNING under "aria.brain_hook" so the operator
    # can watch wedge pressure (R-F872 levers: concurrency cap + neural/tier
    # timeouts). error_log_handler mirrors WARNING+ aria.* into error_log, so
    # after the R-F884 reconnect the coder started picking them up as "gaps"
    # and churning LLM budget trying to "fix" intentional shed. Skip them.
    _OPERATIONAL_SHED_MARKERS = (
        "neural: timeout", "concurrency cap", "absorb: concurrency",
        "absorb pause", "tier: timeout", "knowledge: timeout",
        "brain_hook(", "hard cooldown", "fallback chain",
        # R-F908 — more BY-DESIGN / operational / external events that the
        # R-F884 reconnect started surfacing as "Error in X" gaps. Confirmed
        # live 2026-05-26 in /api/aria/coder/gaps (49 gaps, most non-bugs).
        # None of these are code defects the coder can fix; they churned its
        # 6/hr budget + inflated the backlog. NOTE: real bugs are deliberately
        # NOT here — "event loop stalled" (R-F703 perf) and "codegen json parse
        # failed" stay actionable, as do genuine exceptions + source failures.
        "consecutive failures",     # circuit breaker tripping (source down)
        "circuit tripped",          # brain_hook circuit shed
        "mastery hard floor",       # mastery clamp (R-F796, by design)
        "overconfident by",         # calibration signal (not a code bug)
        "state at boot",            # R-F248 ARIA-STATE INFO dump (mis-classed)
        "missing api key",          # provider not configured (by design)
        "not pruning",              # infinite-memory rule (§7, by design)
        "warn threshold",           # neural edge-count warning (by design)
        "content threats detected", # security detection working (by design)
        "blocked url",              # security blocking auth-required URLs
        "not in whitelist",         # the coder's OWN stage rejection (control flow)
        "rate limit hit",           # autonomous rate limiter (by design)
        "already at cap",           # rate bucket at cap (by design)
        # R-F967 (2026-05-28) — operational events that the ErrorLedgerExtractor
        # was mis-classing as auto_fixable gaps, churning the coder's 6/hr
        # budget (live: 16:58Z self_coder picked 3× "Document parse failure in
        # routes/aria.py" — all rate-blocked, nothing staged). These are NOT
        # code defects the coder can fix from a one-line WARNING:
        "extraction failed",        # PDF/DOCX/Excel/PPTX/EML extraction of a
                                    # user upload failed + was handled/fell
                                    # through (routes/aria.py:9187-9442). Bad
                                    # input, not a routes/aria.py bug. A genuine
                                    # extraction code bug still surfaces via the
                                    # document_reader failure path + operator.
        "probe failed",             # circuit_breaker HALF_OPEN recovery probe
                                    # for an external source still down (§14).
        "returned empty",           # external provider (OCR.space etc.) returned
                                    # an empty body — operational, not a bug.
    )

    def _entry_to_gap(self, entry: dict) -> Optional[Gap]:
        msg = entry.get("message", "")
        etype = entry.get("type", "")
        # error_log has no `module`; use file/function as the locus.
        module = entry.get("file") or entry.get("function") or "unknown"
        trace = entry.get("traceback", "")

        lowered = f"{msg} {etype}".lower()
        # R-F889 — drop designed operational shed (not code bugs) so the coder
        # stays focused on real errors instead of churning on wedge warnings.
        if any(m in lowered for m in self._OPERATIONAL_SHED_MARKERS):
            return None

        gap_type = GapType.MODULE_BUG
        severity = GapSeverity.HIGH
        title = f"Error in {module}"

        if "dd_orchestrator" in module or "layer" in lowered:
            gap_type = GapType.DD_LAYER_FAILURE
            severity = GapSeverity.CRITICAL
        elif any(t in lowered for t in ("parse", "pdf", "document")):
            gap_type = GapType.DOCUMENT_PARSE
            title = f"Document parse failure in {module}"
        elif any(t in lowered for t in ("source", "rss", "fetch", "http")):
            gap_type = GapType.SOURCE_FAILURE
            severity = GapSeverity.MEDIUM
            title = f"Source fetch failure: {module}"
        elif "hallucin" in lowered or "guard" in lowered:
            gap_type = GapType.HALLUCINATION
            severity = GapSeverity.CRITICAL
            title = "Hallucination guard triggered"

        return Gap(
            gap_id=_gap_id_for(gap_type, module, msg),
            gap_type=gap_type,
            severity=severity,
            title=title,
            description=msg,
            module=module,
            error_trace=trace,
            evidence=entry,
        )


class ChatAuditExtractor:
    """Detect hallucination patterns in `crucix:chat_audit:log`."""

    KEY = "crucix:chat_audit:log"

    HALLUCINATION_PATTERNS: list[tuple[str, str]] = [
        (r"\bTTL\b.*\bmonth", "Invented TTL claim"),
        (r"my\s+memory\s+has\s+an?\s+\d+.month", "Invented memory retention"),
        (r"\d+[,\d]*\s*fact[s]?\b.*know", "Unverified fact count"),
        (r"\b18.month\b|\b12.month\b.*memor", "Invented memory retention"),
        (r"layer\s+\d+\s+does\s+not\s+exist", "Invented DD layer"),
        (r"i\s+forgot|i\s+can\s+forget|will\s+forget", "False memory claim"),
        (r"my\s+context\s+window\s+is\s+\d+k\s+tokens", "Unverified context claim"),
    ]

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        since_ts = since.timestamp()
        try:
            raw = await self.redis.lrange(self.KEY, 0, 1000)
        except Exception as e:
            logger.error("[gap_detector] chat_audit read failed: %s", e)
            return gaps

        for entry_bytes in raw:
            try:
                entry = json.loads(
                    entry_bytes.decode("utf-8")
                    if isinstance(entry_bytes, bytes) else entry_bytes
                )
                # R-F884 — producer writes "timestamp" (ISO string), not "ts"
                # (chat_audit_log.py:121). Pre-R-F884 every entry was skipped
                # because entry.get("ts") was always 0.
                if _entry_ts_epoch(entry.get("timestamp")) < since_ts:
                    continue
                response = entry.get("response", "")
                gap = self._check_response(response, entry)
                if gap is not None:
                    gaps.append(gap)
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
        return gaps

    def _check_response(self, response: str, entry: dict) -> Optional[Gap]:
        for pattern, label in self.HALLUCINATION_PATTERNS:
            if re.search(pattern, response, re.IGNORECASE):
                return Gap(
                    gap_id=_gap_id_for(
                        GapType.HALLUCINATION, "aria_engine",
                        f"halluc_{label}_{response[:50]}",
                    ),
                    gap_type=GapType.HALLUCINATION,
                    severity=GapSeverity.CRITICAL,
                    title=f"Hallucination detected: {label}",
                    description=(
                        f"Pattern matched: {pattern}. "
                        f"Query: {entry.get('query', '')[:200]}. "
                        f"Response excerpt: {response[:300]}"
                    ),
                    module="aria_service/aria_engine.py",
                    evidence={
                        "pattern": pattern,
                        "label": label,
                        "ts": entry.get("ts"),
                    },
                )
        return None


class HealthPerfExtractor:
    """Detect performance / coverage regressions from `crucix:health:perf:latest`."""

    KEY = "crucix:health:perf:latest"

    # (threshold, direction): direction "below" means VALUE < threshold breaches.
    THRESHOLDS: dict[str, tuple[float, str]] = {
        "grounded_rate":         (0.85, "below"),
        "adversarial_pass_rate": (0.80, "below"),
        "source_ok_ratio":       (0.90, "below"),
        "p95_latency_ms":        (8000.0, "above"),
        "training_pairs_today":  (50.0, "below"),
    }

    HIGH_SEVERITY_METRICS: frozenset[str] = frozenset({
        "adversarial_pass_rate",
        "grounded_rate",
    })

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        try:
            raw = await self.redis.get(self.KEY)
        except Exception as e:
            logger.error("[gap_detector] health/perf read failed: %s", e)
            return gaps
        if not raw:
            return gaps

        try:
            metrics = json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw
            )
        except json.JSONDecodeError:
            return gaps

        for metric_key, (threshold, direction) in self.THRESHOLDS.items():
            value = metrics.get(metric_key)
            if value is None:
                continue
            try:
                value_f = float(value)
            except (TypeError, ValueError):
                continue
            breached = (
                value_f < threshold if direction == "below"
                else value_f > threshold
            )
            if not breached:
                continue
            severity = (
                GapSeverity.HIGH if metric_key in self.HIGH_SEVERITY_METRICS
                else GapSeverity.MEDIUM
            )
            gaps.append(Gap(
                gap_id=f"perf_{metric_key}",
                gap_type=GapType.PERFORMANCE,
                severity=severity,
                title=f"Performance regression: {metric_key}",
                description=(
                    f"{metric_key}={value_f:.3f}, threshold={threshold} "
                    f"({direction})"
                ),
                module="aria_service/aria_engine.py",
                evidence={
                    "metric": metric_key,
                    "value": value_f,
                    "threshold": threshold,
                },
            ))
        return gaps


class SourceHealthExtractor:
    """Detect source modules failing consecutive sweeps."""

    KEY = "crucix:sweep:last_result"
    CONSECUTIVE_FAIL_THRESHOLD = 3

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        try:
            raw = await self.redis.get(self.KEY)
        except Exception as e:
            logger.error("[gap_detector] sweep read failed: %s", e)
            return gaps
        if not raw:
            return gaps

        try:
            result = json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw
            )
        except json.JSONDecodeError:
            return gaps

        for failed in result.get("failed_sources", []):
            name = failed.get("name", "unknown")
            reason = failed.get("reason", "unknown")
            consecutive = int(failed.get("consecutive_failures", 0))
            if consecutive < self.CONSECUTIVE_FAIL_THRESHOLD:
                continue
            gap_id = f"source_{hashlib.sha256(name.encode()).hexdigest()[:8]}"
            gaps.append(Gap(
                gap_id=gap_id,
                gap_type=GapType.SOURCE_FAILURE,
                severity=GapSeverity.MEDIUM,
                title=f"Source consistently failing: {name}",
                description=(
                    f"{name} has failed {consecutive} consecutive sweeps. "
                    f"Reason: {reason}"
                ),
                module="lib/intel/source_registry.mjs",
                evidence={
                    "source": name,
                    "reason": reason,
                    "consecutive": consecutive,
                },
            ))
        return gaps


class OpportunityExtractor:
    """R-F826: Detect proactive improvement opportunities from chat audits.

    Scans `crucix:chat_audit:log` for topics where ARIA was consistently
    weak (low grounded_rate). A topic that appears in
    `MIN_OCCURRENCES` chats within the lookback window with
    `grounded_rate < GROUNDED_THRESHOLD` becomes an OPPORTUNITY gap —
    not a bug, but a place where acquiring/indexing more authoritative
    material would measurably improve ARIA's answers.

    Routes through `enhancement` change-type per `self_coder.py`, so it
    NEVER auto-deploys regardless of `ARIA_SELF_IMPROVE_AUTO_DEPLOY`.
    Always staged at `/api/aria/self/staged` for operator approval.
    """

    KEY = "crucix:chat_audit:log"
    GROUNDED_THRESHOLD = 0.60   # below this counts as "weak"
    MIN_OCCURRENCES = 3         # a topic must recur this often to matter
    READ_BATCH = 2000           # cover ~24h of audit traffic

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        try:
            raw = await self.redis.lrange(self.KEY, 0, self.READ_BATCH)
        except Exception as e:
            logger.error(
                "[gap_detector] chat_audit read failed (opportunity): %s", e,
            )
            return gaps

        topic_counts: dict[str, int] = {}
        topic_samples: dict[str, list[dict]] = {}

        for entry_bytes in raw:
            try:
                entry = json.loads(
                    entry_bytes.decode("utf-8")
                    if isinstance(entry_bytes, bytes) else entry_bytes
                )
            except (json.JSONDecodeError, AttributeError):
                continue
            ts_raw = entry.get("timestamp", "")
            if not isinstance(ts_raw, str):
                continue
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts < since:
                continue
            grounded = entry.get("grounded_rate")
            if grounded is None:
                continue
            try:
                grounded_f = float(grounded)
            except (TypeError, ValueError):
                continue
            if grounded_f >= self.GROUNDED_THRESHOLD:
                continue
            weak_topics = entry.get("mastery_weak_topics") or []
            if not isinstance(weak_topics, list):
                continue
            for topic in weak_topics:
                if not isinstance(topic, str) or not topic.strip():
                    continue
                topic_norm = topic.strip().lower()
                topic_counts[topic_norm] = topic_counts.get(topic_norm, 0) + 1
                if len(topic_samples.setdefault(topic_norm, [])) < 5:
                    topic_samples[topic_norm].append({
                        "ts": ts_raw,
                        "grounded_rate": grounded_f,
                    })

        for topic, count in topic_counts.items():
            if count < self.MIN_OCCURRENCES:
                continue
            gaps.append(Gap(
                gap_id=_gap_id_for(
                    GapType.OPPORTUNITY, "aria_engine",
                    f"low_grounded_topic_{topic}",
                ),
                gap_type=GapType.OPPORTUNITY,
                severity=GapSeverity.MEDIUM,
                title=(
                    f"Capability opportunity: low grounded coverage on '{topic}'"
                ),
                description=(
                    f"Topic '{topic}' appeared in {count} chats over the "
                    f"lookback window with grounded_rate < "
                    f"{self.GROUNDED_THRESHOLD}. Recurring weakness suggests "
                    f"ARIA should acquire or index more authoritative "
                    f"material on this topic."
                ),
                module="aria_service/aria_engine.py",
                evidence={
                    "topic": topic,
                    "occurrences": count,
                    "grounded_threshold": self.GROUNDED_THRESHOLD,
                    "samples": topic_samples.get(topic, []),
                },
            ))
        return gaps


class CapabilityGapExtractor:
    """R-F884 — read recorded capability gaps from `crucix:aria:capability_gaps`.

    This is where brain_hook.absorb(gap_type=…) + capability_gaps.record_gap
    route (lpush list). Pre-R-F884 NO extractor read it — one of the two richest
    gap stores was invisible to the coder. Entry: {id, type, detail, source,
    message_context, timestamp(ISO), resolved, …}.
    """

    KEY = "crucix:aria:capability_gaps"

    # capability_gap "type" → (GapType, GapSeverity). Unknown types map to
    # MISSING_CAPABILITY (auto_fixable=False → operator decides), so a novel
    # gap class never silently drives an auto-fix.
    _TYPE_MAP = {
        "file_parse": (GapType.DOCUMENT_PARSE, GapSeverity.HIGH),
        "document":   (GapType.DOCUMENT_PARSE, GapSeverity.HIGH),
        "source":     (GapType.SOURCE_FAILURE, GapSeverity.MEDIUM),
        "data":       (GapType.DATA_GAP, GapSeverity.MEDIUM),
        "knowledge":  (GapType.DATA_GAP, GapSeverity.MEDIUM),
    }

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        since_ts = since.timestamp()
        try:
            raw = await self.redis.lrange(self.KEY, 0, 500)
        except Exception as e:
            logger.error("[gap_detector] capability_gaps read failed: %s", e)
            return gaps
        for entry_bytes in raw or []:
            try:
                entry = json.loads(
                    entry_bytes.decode("utf-8") if isinstance(entry_bytes, bytes) else entry_bytes
                )
                if not isinstance(entry, dict) or entry.get("resolved"):
                    continue
                if _entry_ts_epoch(entry.get("timestamp")) < since_ts:
                    continue
                ctype = (entry.get("type") or "").lower()
                gap_type, severity = GapType.MISSING_CAPABILITY, GapSeverity.MEDIUM
                for key, (gt, sev) in self._TYPE_MAP.items():
                    if key in ctype:
                        gap_type, severity = gt, sev
                        break
                detail = entry.get("detail") or ctype or "capability gap"
                gaps.append(Gap(
                    gap_id=_gap_id_for(gap_type, entry.get("source", "capability_gaps"), detail),
                    gap_type=gap_type,
                    severity=severity,
                    title=f"Capability gap: {ctype or 'unspecified'}",
                    description=str(detail)[:1000],
                    module=entry.get("source", "capability_gaps"),
                    evidence={"capability_gap_id": entry.get("id"), "type": ctype,
                              "message_context": entry.get("message_context", "")},
                ))
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
        return gaps


class MistakeLedgerExtractor:
    """R-F884 — read recorded mistakes from `crucix:mistake_ledger:log`.

    The mistake ledger (hash-chained, signed) is read by calibration_review +
    memory_replication but NEVER by the coder. Each entry already carries a
    `fix` — a past mistake with a known remedy. We surface them as
    MISSING_CAPABILITY (auto_fixable=False) so they are OBSERVED for operator
    review, not auto-fixed (a recorded mistake is not necessarily a live code
    bug). Entry: {mistake_id, ts(ISO), category, what, why, fix, severity, …}.
    """

    KEY = "crucix:mistake_ledger:log"

    _SEV_MAP = {
        "LOW": GapSeverity.LOW, "MEDIUM": GapSeverity.MEDIUM,
        "HIGH": GapSeverity.HIGH, "CRITICAL": GapSeverity.CRITICAL,
    }

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        since_ts = since.timestamp()
        try:
            raw = await self.redis.lrange(self.KEY, 0, 300)
        except Exception as e:
            logger.error("[gap_detector] mistake_ledger read failed: %s", e)
            return gaps
        for entry_bytes in raw or []:
            try:
                entry = json.loads(
                    entry_bytes.decode("utf-8") if isinstance(entry_bytes, bytes) else entry_bytes
                )
                if not isinstance(entry, dict):
                    continue
                if _entry_ts_epoch(entry.get("ts")) < since_ts:
                    continue
                what = entry.get("what") or entry.get("what_class") or "recorded mistake"
                severity = self._SEV_MAP.get((entry.get("severity") or "").upper(), GapSeverity.MEDIUM)
                gaps.append(Gap(
                    gap_id=_gap_id_for(GapType.MISSING_CAPABILITY, "mistake_ledger",
                                       entry.get("mistake_id") or what),
                    gap_type=GapType.MISSING_CAPABILITY,
                    severity=severity,
                    title=f"Recorded mistake: {entry.get('category', 'general')}",
                    description=(f"{str(what)[:600]} | WHY: {str(entry.get('why', ''))[:300]} "
                                 f"| KNOWN FIX: {str(entry.get('fix', ''))[:300]}"),
                    module=entry.get("task_type") or "mistake_ledger",
                    evidence={"mistake_id": entry.get("mistake_id"),
                              "domain": entry.get("domain"), "category": entry.get("category")},
                ))
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
        return gaps


# ── MAIN DETECTOR ────────────────────────────────────────────────────────────

class GapDetector:
    """Continuous gap detection — runs every `SCAN_INTERVAL_S` seconds.

    Aggregates signals from all `Extractor`s, deduplicates, prioritises by
    severity, and stores the actionable list at `crucix:aria:gaps:latest`
    for the ARIACoder to consume.
    """

    SCAN_INTERVAL_S = 900           # 15 minutes
    DEDUP_WINDOW_S = 86400          # 24 hours
    ATTEMPT_COOLDOWN_S = 3600       # 1 hour
    LOOKBACK_WINDOW = timedelta(hours=2)

    LATEST_KEY = "crucix:aria:gaps:latest"
    FIXED_KEY_PREFIX = "crucix:aria:gap:fixed:"
    ATTEMPTED_KEY_PREFIX = "crucix:aria:gap:attempted:"

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        # R-F884 — reconnected to the REAL producer stores. Dropped
        # HealthPerfExtractor (`crucix:health:perf:latest`) and
        # SourceHealthExtractor (`crucix:sweep:last_result`): NO producer
        # writes either key, so they were dead reads. Added CapabilityGap +
        # MistakeLedger — the two richest, actually-populated gap stores the
        # coder previously read from neither.
        self.extractors = [
            ErrorLedgerExtractor(redis_client),     # crucix:aria:error_log (fixed key)
            ChatAuditExtractor(redis_client),       # crucix:chat_audit:log (fixed ts field)
            CapabilityGapExtractor(redis_client),   # crucix:aria:capability_gaps (NEW)
            MistakeLedgerExtractor(redis_client),   # crucix:mistake_ledger:log (NEW)
            OpportunityExtractor(redis_client),     # R-F826: crucix:chat_audit:log
        ]
        self._active_gaps: dict[str, Gap] = {}

    async def scan(self) -> list[Gap]:
        """Run all extractors, dedupe, return prioritised list."""
        since = datetime.now(timezone.utc) - self.LOOKBACK_WINDOW
        all_gaps: list[Gap] = []

        for extractor in self.extractors:
            try:
                gaps = await extractor.extract(since)
                all_gaps.extend(gaps)
            except Exception as e:
                logger.error(
                    "[gap_detector] %s failed: %s",
                    extractor.__class__.__name__, e,
                )

        # Dedupe — keep highest severity per gap_id
        seen: dict[str, Gap] = {}
        for gap in all_gaps:
            existing = seen.get(gap.gap_id)
            if existing is None or gap.severity > existing.severity:
                seen[gap.gap_id] = gap

        # Filter recently fixed or attempted
        new_gaps: list[Gap] = []
        for gap_id, gap in seen.items():
            if await self._is_recently_fixed(gap_id):
                continue
            if await self._is_recently_attempted(gap_id):
                continue
            new_gaps.append(gap)
            self._active_gaps[gap_id] = gap

        new_gaps.sort(key=lambda g: g.severity, reverse=True)
        logger.info(
            "[gap_detector] scan complete: %d actionable gaps "
            "(from %d raw signals)",
            len(new_gaps), len(all_gaps),
        )
        return new_gaps

    async def _is_recently_fixed(self, gap_id: str) -> bool:
        try:
            return bool(await self.redis.get(f"{self.FIXED_KEY_PREFIX}{gap_id}"))
        except Exception:
            return False

    async def _is_recently_attempted(self, gap_id: str) -> bool:
        try:
            return bool(await self.redis.get(f"{self.ATTEMPTED_KEY_PREFIX}{gap_id}"))
        except Exception:
            return False

    async def mark_attempted(self, gap_id: str) -> None:
        try:
            await self.redis.setex(
                f"{self.ATTEMPTED_KEY_PREFIX}{gap_id}",
                self.ATTEMPT_COOLDOWN_S, "1",
            )
        except Exception as e:
            logger.warning("[gap_detector] mark_attempted failed: %s", e)

    async def mark_fixed(self, gap_id: str, r_number: int) -> None:
        try:
            await self.redis.setex(
                f"{self.FIXED_KEY_PREFIX}{gap_id}",
                self.DEDUP_WINDOW_S, str(r_number),
            )
        except Exception as e:
            logger.warning("[gap_detector] mark_fixed failed: %s", e)
        self._active_gaps.pop(gap_id, None)
        logger.info("[gap_detector] gap %s → R-F%d (fixed)", gap_id, r_number)

    async def publish_latest(self, gaps: list[Gap]) -> None:
        try:
            await self.redis.setex(
                self.LATEST_KEY,
                self.SCAN_INTERVAL_S * 2,
                json.dumps([g.to_dict() for g in gaps]),
            )
        except Exception as e:
            logger.warning("[gap_detector] publish_latest failed: %s", e)

    async def run_forever(self) -> None:
        """Main loop. Cancellable via asyncio.Task.cancel()."""
        logger.info(
            "[gap_detector] starting — scan interval %ds", self.SCAN_INTERVAL_S,
        )
        while True:
            try:
                gaps = await self.scan()
                await self.publish_latest(gaps)
            except asyncio.CancelledError:
                logger.info("[gap_detector] cancelled — exiting")
                raise
            except Exception as e:
                logger.error("[gap_detector] scan error: %s", e, exc_info=True)
            await asyncio.sleep(self.SCAN_INTERVAL_S)
