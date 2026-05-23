"""R-F802 — Gap detection engine.

Continuously monitors production signals to detect capability gaps,
bugs, and missing features. Feeds structured `Gap` objects to the
ARIACoder for autonomous remediation.

Signal sources
──────────────
  ErrorLedgerExtractor    — reads exceptions from `crucix:aria:error_ledger`
  ChatAuditExtractor      — scans `crucix:chat_audit:log` for hallucination
                            patterns matching `SELF_INTROSPECTION_PATTERNS`
  HealthPerfExtractor     — checks `crucix:health:perf:latest` against
                            threshold floors (grounded_rate, p95_latency, etc.)
  SourceHealthExtractor   — checks `crucix:sweep:last_result` for
                            consecutive-failure source modules

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


# ── EXTRACTORS ───────────────────────────────────────────────────────────────

class ErrorLedgerExtractor:
    """Read structured ERROR/CRITICAL entries from `crucix:aria:error_ledger`.

    Format expected (per self_improve.py error reader): list of JSON
    entries with keys: timestamp, level, message, module, traceback.
    """

    KEY = "crucix:aria:error_ledger"

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        since_ts = since.timestamp()
        try:
            raw = await self.redis.lrange(self.KEY, 0, 500)
        except Exception as e:
            logger.error("[gap_detector] error_ledger read failed: %s", e)
            return gaps

        for entry_bytes in raw:
            try:
                entry = json.loads(
                    entry_bytes.decode("utf-8")
                    if isinstance(entry_bytes, bytes) else entry_bytes
                )
                if float(entry.get("timestamp", 0)) < since_ts:
                    continue
                gap = self._entry_to_gap(entry)
                if gap is not None:
                    gaps.append(gap)
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
        return gaps

    def _entry_to_gap(self, entry: dict) -> Optional[Gap]:
        msg = entry.get("message", "")
        level = entry.get("level", "")
        module = entry.get("module", "unknown")
        trace = entry.get("traceback", "")

        if level not in ("ERROR", "CRITICAL"):
            return None

        gap_type = GapType.MODULE_BUG
        severity = GapSeverity.HIGH
        title = f"Error in {module}"

        lowered = msg.lower()
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
                if float(entry.get("ts", 0)) < since_ts:
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
        self.extractors = [
            ErrorLedgerExtractor(redis_client),
            ChatAuditExtractor(redis_client),
            HealthPerfExtractor(redis_client),
            SourceHealthExtractor(redis_client),
            OpportunityExtractor(redis_client),  # R-F826
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
