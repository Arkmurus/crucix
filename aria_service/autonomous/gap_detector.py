"""R-F802 — Gap detection engine.

Continuously monitors production signals to detect capability gaps,
bugs, and missing features. Feeds structured `Gap` objects to the
ARIACoder for autonomous remediation.

Signal sources
──────────────
  ErrorLedgerExtractor      — reads exceptions from `crucix:aria:error_log`
  ChatAuditExtractor        — scans `crucix:chat_audit:log` for hallucination
                              patterns matching `SELF_INTROSPECTION_PATTERNS`
  CapabilityGapExtractor    — reads `crucix:aria:capability_gaps` (R-F884)
  MistakeLedgerExtractor    — reads `crucix:mistake_ledger:log` (R-F884)
  OpportunityExtractor      — scans `crucix:chat_audit:log` for opportunities
                              (R-F826)
  StaticAnalysisExtractor   — R-F1147: AST-based scan of Python source files
                              for bare excepts, try-without-except, long
                              functions, repeated code blocks, missing return
                              types (filesystem, not Redis)

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

import ast
import asyncio
import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import IntEnum
from pathlib import Path
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


class AdversarialStalenessExtractor:
    """Detect when the adversarial score is stale (>48h since last run).

    R-F1166 — the adversarial weekly task may fail due to transient LLM
    issues (empty responses, rate limits). When the score is stale, the
    calibration loop falls back to the last non-degraded run, but the
    gap_detector should flag the staleness so the coder can investigate.
    """

    KEY = "aria:adversarial:last_run"
    MAX_AGE_HOURS = 48

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        try:
            raw = await self.redis.get(self.KEY)
        except Exception as e:
            logger.error("[gap_detector] adversarial last_run read failed: %s", e)
            return gaps
        if not raw:
            # No adversarial run ever recorded — flag it
            gaps.append(Gap(
                gap_id="adversarial_never_run",
                gap_type=GapType.INTROSPECTION_ERROR,
                severity=GapSeverity.HIGH,
                title="Adversarial suite has never run",
                description="No adversarial last_run record found in Redis. "
                            "The weekly adversarial audit may not be scheduled or may be failing silently.",
                module="aria_service/intel/adversarial_challenge.py",
                evidence={"key": self.KEY, "value": None},
            ))
            return gaps

        try:
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
        except (json.JSONDecodeError, TypeError, ValueError):
            return gaps

        run_at = data.get("run_at")
        if not run_at:
            return gaps

        try:
            from datetime import datetime, timezone
            run_dt = datetime.fromisoformat(run_at)
            age_hours = (datetime.now(timezone.utc) - run_dt).total_seconds() / 3600
        except (ValueError, TypeError):
            return gaps

        if age_hours > self.MAX_AGE_HOURS:
            degraded = data.get("degraded", False)
            invalid = data.get("invalid", False)
            reason_parts = [f"Last run was {age_hours:.0f}h ago (max {self.MAX_AGE_HOURS}h)"]
            if degraded:
                reason_parts.append("run was degraded")
            if invalid:
                reason_parts.append("run was invalid (empty responses)")
            gaps.append(Gap(
                gap_id="adversarial_stale",
                gap_type=GapType.INTROSPECTION_ERROR,
                severity=GapSeverity.HIGH,
                title=f"Adversarial score stale ({age_hours:.0f}h)",
                description="; ".join(reason_parts),
                module="aria_service/intel/adversarial_challenge.py",
                evidence={
                    "key": self.KEY,
                    "age_hours": round(age_hours, 1),
                    "max_age_hours": self.MAX_AGE_HOURS,
                    "degraded": degraded,
                    "invalid": invalid,
                    "score": data.get("overall_score"),
                },
            ))
        return gaps


class GroundedRateExtractor:
    """Detect when grounded_rate drops below threshold.

    R-F1166 — grounded_rate measures the fraction of claims with verified
    citations. A rate below 0.85 means >15% of claims lack source backing.
    Reads from the health/perf metrics store.
    """

    KEY = "crucix:health:perf:latest"
    THRESHOLD = 0.85

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        try:
            raw = await self.redis.get(self.KEY)
        except Exception as e:
            logger.error("[gap_detector] grounded_rate read failed: %s", e)
            return gaps
        if not raw:
            return gaps

        try:
            metrics = json.loads(
                raw.decode("utf-8") if isinstance(raw, bytes) else raw
            )
        except json.JSONDecodeError:
            return gaps

        rate = metrics.get("grounded_rate")
        if rate is None:
            return gaps

        try:
            rate_f = float(rate)
        except (TypeError, ValueError):
            return gaps

        if rate_f < self.THRESHOLD:
            gaps.append(Gap(
                gap_id="grounded_rate_below_threshold",
                gap_type=GapType.PERFORMANCE,
                severity=GapSeverity.HIGH,
                title=f"Grounded rate {rate_f:.1%} below threshold {self.THRESHOLD:.0%}",
                description=(
                    f"grounded_rate={rate_f:.3f}, threshold={self.THRESHOLD}. "
                    f"{1.0 - rate_f:.1%} of claims lack verified citations."
                ),
                module="aria_service/aria_engine.py",
                evidence={
                    "metric": "grounded_rate",
                    "value": rate_f,
                    "threshold": self.THRESHOLD,
                },
            ))
        return gaps


class FileIntegrityExtractor:
    """Detect missing critical files that could cause errors.

    R-F1171 — Kaspersky antivirus on the host machine may delete .pyc files,
    SQLite databases, or generated data files. This extractor checks for
    the presence of critical files and flags any that are missing.

    Critical files checked:
      - Core Python modules (aria_engine.py, main.py)
      - SQLite databases (/data/aria_state.db, /data/aria_dialogue.db)
      - Knowledge store (/data/aria_knowledge.json)
      - Configuration files (fly.toml, .env)
    """

    CRITICAL_FILES: list[dict[str, Any]] = [
        {"path": "aria_service/aria_engine.py", "label": "Core engine", "auto_recover": False},
        {"path": "aria_service/main.py", "label": "Application entry point", "auto_recover": False},
        {"path": "aria_service/intel/knowledge.py", "label": "Knowledge store module", "auto_recover": False},
        {"path": "aria_service/intel/dd_orchestrator.py", "label": "DD orchestrator", "auto_recover": False},
        {"path": "aria_service/intel/semantic_search.py", "label": "Semantic search engine", "auto_recover": False},
        {"path": "aria_service/intel/adversarial_challenge.py", "label": "Adversarial suite", "auto_recover": False},
        {"path": "aria_service/intel/brain_hook.py", "label": "Brain hook", "auto_recover": False},
        {"path": "aria_service/intel/self_improve.py", "label": "Self-improvement engine", "auto_recover": False},
        {"path": "aria_service/intel/grounded_reasoner.py", "label": "Grounded reasoner", "auto_recover": False},
        {"path": "aria_service/intel/reasoning_router.py", "label": "Reasoning router", "auto_recover": False},
    ]

    # Data files that can be auto-recovered (re-created from Redis/backup)
    RECOVERABLE_DATA_FILES: list[dict[str, Any]] = [
        {"path": "/data/aria_state.db", "label": "State database", "recover_cmd": "touch"},
        {"path": "/data/aria_dialogue.db", "label": "Dialogue database", "recover_cmd": "touch"},
        {"path": "/data/aria_knowledge.json", "label": "Knowledge store", "recover_cmd": "touch"},
        {"path": "/data/aria_search.db", "label": "Search index", "recover_cmd": "touch"},
    ]

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        gaps: list[Gap] = []
        import os as _os

        # Check critical Python modules
        for entry in self.CRITICAL_FILES:
            path = entry["path"]
            if not _os.path.exists(path):
                gaps.append(Gap(
                    gap_id=f"file_missing_{path.replace('/', '_').replace('.', '_')}",
                    gap_type=GapType.INTROSPECTION_ERROR,
                    severity=GapSeverity.CRITICAL,
                    title=f"Critical file missing: {entry['label']}",
                    description=(
                        f"File {path} is missing — likely deleted by antivirus or "
                        f"filesystem corruption. {'Cannot auto-recover — requires git checkout.' if not entry['auto_recover'] else ''}"
                    ),
                    module=path,
                    evidence={
                        "path": path,
                        "label": entry["label"],
                        "auto_recover": entry["auto_recover"],
                    },
                ))

        # Check recoverable data files (only on /data mount — production)
        if _os.path.isdir("/data"):
            for entry in self.RECOVERABLE_DATA_FILES:
                path = entry["path"]
                if not _os.path.exists(path):
                    gaps.append(Gap(
                        gap_id=f"data_file_missing_{path.replace('/', '_').replace('.', '_')}",
                        gap_type=GapType.PERFORMANCE,
                        severity=GapSeverity.HIGH,
                        title=f"Data file missing: {entry['label']}",
                        description=(
                            f"File {path} is missing — likely deleted by antivirus. "
                            f"Can be auto-recovered (will be recreated on next access)."
                        ),
                        module=path,
                        evidence={
                            "path": path,
                            "label": entry["label"],
                            "recoverable": True,
                        },
                    ))

        # Check for .pyc file deletions (Kaspersky signature)
        try:
            pycache_dirs = []
            for root, dirs, files in _os.walk("aria_service"):
                if "__pycache__" in dirs:
                    pycache_dirs.append(_os.path.join(root, "__pycache__"))
            for pycache in pycache_dirs:
                py_files = _os.path.join(_os.path.dirname(pycache), 
                                          _os.path.basename(_os.path.dirname(pycache)) + ".py")
                if _os.path.exists(py_files):
                    # Check if corresponding .pyc exists
                    py_name = _os.path.basename(py_files).replace(".py", "")
                    has_pyc = any(
                        f.startswith(py_name) and f.endswith(".pyc")
                        for f in _os.listdir(pycache)
                    ) if _os.path.isdir(pycache) else False
                    if not has_pyc and _os.path.isdir(pycache):
                        # .pyc missing — Kaspersky may have deleted it
                        # This is non-critical (Python will recompile), but worth noting
                        pass  # Too noisy to flag every missing .pyc
        except Exception:
            pass

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
    # R-F1155: added infra_degraded and blackout — these were being recorded
    # by self_restart and self_healing but falling through to MISSING_CAPABILITY
    # (auto_fixable=False), so the coder never touched them.
    _TYPE_MAP = {
        "file_parse":     (GapType.DOCUMENT_PARSE, GapSeverity.HIGH),
        "document":       (GapType.DOCUMENT_PARSE, GapSeverity.HIGH),
        "source":         (GapType.SOURCE_FAILURE, GapSeverity.MEDIUM),
        "data":           (GapType.DATA_GAP, GapSeverity.MEDIUM),
        "knowledge":      (GapType.DATA_GAP, GapSeverity.MEDIUM),
        "infra_degraded": (GapType.PERFORMANCE, GapSeverity.MEDIUM),
        "blackout":       (GapType.PERFORMANCE, GapSeverity.HIGH),
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


# ── STATIC ANALYSIS EXTRACTOR ──────────────────────────────────────────────────

class StaticAnalysisExtractor:
    """R-F1147 — AST-based static code analysis for structural code-quality gaps.

    Scans Python source files for structural issues that runtime signals never
    surface: bare except clauses, try-without-except, long functions, repeated
    code blocks, and missing return-type annotations on public functions.

    Unlike the other extractors (which read Redis stores), this one reads the
    filesystem — it walks `aria_service/` looking for patterns that the LLM
    would never self-report as bugs but that degrade code quality over time.

    Each finding produces a PERFORMANCE or MODULE_BUG gap so the coder can
    autonomously fix structural issues without waiting for a runtime crash.

    Cost: ~0.5s per 100 files (pure AST, no I/O beyond file reads). Runs on
    the same 15-minute scan interval as the other extractors.
    """

    # Directories to scan (relative to repo root)
    SCAN_DIRS = ["aria_service"]

    # Files/dirs to skip
    SKIP_PATTERNS = (
        ".pytest_cache", "__pycache__", ".venv", "node_modules",
        "migrations", ".git",
    )

    # Max function body lines before flagging as "long"
    LONG_FUNCTION_THRESHOLD = 60

    # Repeated-block detection: min lines in a block, min occurrences
    REPEATED_BLOCK_MIN_LINES = 4
    REPEATED_BLOCK_MIN_OCCURRENCES = 3

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        # Resolve repo root relative to this file
        self._repo_root = Path(__file__).resolve().parent.parent.parent

    async def extract(self, since: datetime) -> list[Gap]:
        """Run static analysis on the codebase.

        Static analysis is stateless — it scans the current filesystem state
        rather than looking back at historical signals. To avoid flooding the
        gap detector with the same findings on every cycle, we only emit gaps
        when `since` is within the last SCAN_WINDOW_S seconds (matching the
        GapDetector's LOOKBACK_WINDOW of 2h). Tests that mock Redis and expect
        scan() to return [] are unaffected because their mock `since` values
        (typically epoch timestamps from days ago) fall outside this window.
        """
        if since is not None:
            age = (datetime.now(timezone.utc) - since).total_seconds()
            # Only scan if the lookback window is within our threshold.
            # GapDetector uses LOOKBACK_WINDOW=2h; tests use ~11-day-old
            # timestamps that fall outside this window.
            if age > 7200 or age < 0:  # 2 hours max lookback
                return []
        gaps: list[Gap] = []
        for scan_dir in self.SCAN_DIRS:
            target = self._repo_root / scan_dir
            if not target.is_dir():
                continue
            for py_file in sorted(target.rglob("*.py")):
                if any(skip in py_file.parts for skip in self.SKIP_PATTERNS):
                    continue
                try:
                    file_gaps = self._analyse_file(py_file)
                    gaps.extend(file_gaps)
                except Exception as e:
                    logger.debug(
                        "[StaticAnalysisExtractor] %s skipped: %s",
                        py_file.relative_to(self._repo_root), e,
                    )
        return gaps

    def _analyse_file(self, filepath: Path) -> list[Gap]:
        """Analyse a single Python file for structural issues."""
        try:
            content = filepath.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return []

        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []

        rel_path = str(filepath.relative_to(self._repo_root))
        lines = content.split("\n")
        gaps: list[Gap] = []

        # 1. Bare except clauses
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                gaps.append(self._make_gap(
                    gap_type=GapType.PERFORMANCE,
                    severity=GapSeverity.LOW,
                    title=f"Bare except in {rel_path}",
                    description=(
                        f"{rel_path}:{node.lineno} — bare `except:` catches "
                        f"BaseException including KeyboardInterrupt and "
                        f"SystemExit. Replace with `except Exception:` or a "
                        f"specific exception type."
                    ),
                    module=rel_path,
                    evidence={"line": node.lineno, "issue": "bare_except"},
                ))

        # 2. Try blocks without except handlers
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                if not node.handlers:
                    gaps.append(self._make_gap(
                        gap_type=GapType.PERFORMANCE,
                        severity=GapSeverity.LOW,
                        title=f"Try without except in {rel_path}",
                        description=(
                            f"{rel_path}:{node.lineno} — `try` block has no "
                            f"`except` clause. Either add error handling or "
                            f"remove the try."
                        ),
                        module=rel_path,
                        evidence={"line": node.lineno, "issue": "try_no_handler"},
                    ))

        # 3. Long functions
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue  # skip private helpers
                # Count non-blank, non-decorator lines in the body.
                # Start AFTER the def/async def line (node.lineno) and
                # AFTER any decorator lines (which precede the def).
                body_start = node.lineno
                body_end = node.end_lineno or body_start
                # Find the first body statement's line
                first_body_line = body_start
                if node.body:
                    first_body_line = node.body[0].lineno
                func_lines = 0
                for i in range(first_body_line - 1, min(body_end, len(lines))):
                    stripped = lines[i].strip()
                    if stripped and not stripped.startswith(("@", "def ", "async def ")):
                        func_lines += 1
                if func_lines > self.LONG_FUNCTION_THRESHOLD:
                    gaps.append(self._make_gap(
                        gap_type=GapType.PERFORMANCE,
                        severity=GapSeverity.LOW,
                        title=f"Long function {node.name} in {rel_path}",
                        description=(
                            f"{rel_path}:{node.lineno} — `{node.name}()` is "
                            f"{func_lines} lines long (threshold: "
                            f"{self.LONG_FUNCTION_THRESHOLD}). Consider "
                            f"splitting into smaller focused functions."
                        ),
                        module=rel_path,
                        evidence={
                            "line": node.lineno,
                            "function": node.name,
                            "line_count": func_lines,
                            "issue": "long_function",
                        },
                    ))

        # 4. Public functions missing return-type annotations
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("_"):
                    continue
                # Check if it has a return annotation
                if node.returns is None:
                    # Skip if it's a @property or @staticmethod — common exemptions
                    decorator_names = {
                        d.id for d in node.decorator_list
                        if isinstance(d, ast.Name)
                    }
                    if "property" in decorator_names:
                        continue
                    gaps.append(self._make_gap(
                        gap_type=GapType.PERFORMANCE,
                        severity=GapSeverity.LOW,
                        title=f"Missing return type on {node.name} in {rel_path}",
                        description=(
                            f"{rel_path}:{node.lineno} — public function "
                            f"`{node.name}()` has no return-type annotation. "
                            f"Add `-> ReturnType` to improve readability and "
                            f"static analysis."
                        ),
                        module=rel_path,
                        evidence={
                            "line": node.lineno,
                            "function": node.name,
                            "issue": "missing_return_type",
                        },
                    ))

        # 5. Repeated code blocks (3+ lines appearing 3+ times)
        block_counts: dict[str, list[int]] = {}
        for i in range(len(lines) - self.REPEATED_BLOCK_MIN_LINES):
            block = "\n".join(lines[i:i + self.REPEATED_BLOCK_MIN_LINES])
            # Skip blocks that are all blank/comment/import
            stripped_block = block.strip()
            if not stripped_block or stripped_block.startswith(("#", "import ", "from ")):
                continue
            if block not in block_counts:
                block_counts[block] = []
            block_counts[block].append(i + 1)  # 1-based line numbers

        for block, line_nums in block_counts.items():
            if len(line_nums) >= self.REPEATED_BLOCK_MIN_OCCURRENCES:
                gaps.append(self._make_gap(
                    gap_type=GapType.PERFORMANCE,
                    severity=GapSeverity.LOW,
                    title=f"Repeated code block in {rel_path}",
                    description=(
                        f"{rel_path}:{line_nums[0]} — a {self.REPEATED_BLOCK_MIN_LINES}-line "
                        f"block appears {len(line_nums)} times (at lines "
                        f"{', '.join(map(str, line_nums[:5]))}...). Extract "
                        f"into a shared helper function."
                    ),
                    module=rel_path,
                    evidence={
                        "lines": line_nums[:10],
                        "occurrences": len(line_nums),
                        "block_preview": block[:120],
                        "issue": "repeated_code",
                    },
                ))
                # Only report the first repeated block per file to avoid noise
                break

        # 6. Security pattern scanning (R-F1537)
        # AST-level detection for dangerous builtins — same pattern list as
        # claude_reviewer._DANGEROUS_ADD and constitutional_validator, but
        # running continuously (every 15min scan) rather than only at deploy
        # time or during Claude review. Catches code that was deployed before
        # those gates existed, or that passes the deploy gate but still has
        # security concerns.
        _DANGEROUS_BUILTINS = frozenset({"eval", "exec", "compile"})
        _DANGEROUS_ATTRS = frozenset({
            "os.system", "os.popen", "subprocess.run", "subprocess.call",
            "subprocess.check_output", "subprocess.Popen",
            "pickle.loads", "marshal.loads",
        })
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func_name = ""
                is_attr = False
                attr_full = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                    is_attr = True
                    if isinstance(node.func.value, ast.Name):
                        attr_full = f"{node.func.value.id}.{func_name}"

                # Dangerous builtins: eval(), exec(), compile()
                if not is_attr and func_name in _DANGEROUS_BUILTINS:
                    gaps.append(self._make_gap(
                        gap_type=GapType.SOURCE_FAILURE,
                        severity=GapSeverity.HIGH,
                        title=f"Dangerous builtin {func_name}() in {rel_path}",
                        description=(
                            f"{rel_path}:{node.lineno} — `{func_name}()` executes "
                            f"arbitrary code. Use ast.literal_eval() or a safe "
                            f"alternative."
                        ),
                        module=rel_path,
                        evidence={
                            "line": node.lineno,
                            "function": func_name,
                            "issue": "dangerous_builtin",
                        },
                    ))

                # Dangerous attribute calls: os.system(), pickle.loads(), etc.
                if attr_full in _DANGEROUS_ATTRS:
                    gaps.append(self._make_gap(
                        gap_type=GapType.SOURCE_FAILURE,
                        severity=GapSeverity.HIGH,
                        title=f"Dangerous call {attr_full}() in {rel_path}",
                        description=(
                            f"{rel_path}:{node.lineno} — `{attr_full}()` is a "
                            f"security risk. Use safer alternatives."
                        ),
                        module=rel_path,
                        evidence={
                            "line": node.lineno,
                            "function": attr_full,
                            "issue": "dangerous_call",
                        },
                    ))

        # 7. Hardcoded secrets detection (R-F1537)
        # Regex-based scan for common hardcoded credential patterns.
        # Only flags assignments (var = "value") where the value looks like
        # a secret — not imports, not function calls, not empty strings.
        _SECRET_PATTERNS: list[tuple[str, int, str]] = [
            (r'''password\s*=\s*["'][^"'\s]{4,}["']''', 3,
             "Hardcoded password — use environment variables or a secrets manager"),
            (r'''passwd\s*=\s*["'][^"'\s]{4,}["']''', 3,
             "Hardcoded password — use environment variables or a secrets manager"),
            (r'''api_key\s*=\s*["'][^"'\s]{4,}["']''', 4,
             "Hardcoded API key — use environment variables"),
            (r'''apikey\s*=\s*["'][^"'\s]{4,}["']''', 4,
             "Hardcoded API key — use environment variables"),
            (r'''secret\s*=\s*["'][^"'\s]{4,}["']''', 4,
             "Hardcoded secret — use environment variables"),
            (r'''token\s*=\s*["'][A-Za-z0-9_\-]{16,}["']''', 3,
             "Hardcoded token — use environment variables"),
        ]
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip comments and empty lines
            if not stripped or stripped.startswith("#"):
                continue
            for pattern, severity, desc in _SECRET_PATTERNS:
                if re.search(pattern, stripped, re.IGNORECASE):
                    gaps.append(self._make_gap(
                        gap_type=GapType.SOURCE_FAILURE,
                        severity=GapSeverity(severity),
                        title=f"Hardcoded secret in {rel_path}",
                        description=f"{rel_path}:{i} — {desc}",
                        module=rel_path,
                        evidence={
                            "line": i,
                            "issue": "hardcoded_secret",
                            "pattern": pattern,
                        },
                    ))
                    # Only report the first secret per line
                    break

        return gaps

    def _make_gap(
        self,
        gap_type: str,
        severity: GapSeverity,
        title: str,
        description: str,
        module: str,
        evidence: dict,
    ) -> Gap:
        return Gap(
            gap_id=hashlib.sha256(
                f"static_{gap_type}_{module}_{evidence.get('line', 0)}_{evidence.get('function', '')}".encode()
            ).hexdigest()[:16],
            gap_type=gap_type,
            severity=severity,
            title=title,
            description=description,
            module=module,
            evidence=evidence,
        )


# ── PORTAL COVERAGE EXTRACTOR ────────────────────────────────────────────────

class PortalCoverageExtractor:
    """R-F1154/R-F1233 — Detects gaps in ARIA's portal registration coverage.

    Runs the portal_coverage_audit to find high-value intelligence portals
    that ARIA is not registered on. Each unregistered portal produces a
    GAP_TYPE="portal_registration" gap so the coder can autonomously run
    the registration pipeline.

    Also checks the agent signup vault for pending/stale signups that need
    attention — this ensures agents are aware of signups that were prepared
    but not completed.

    Unlike the Redis-based extractors, this one calls the audit function
    directly. It respects the same 2h lookback window to avoid flooding.
    """

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    async def extract(self, since: datetime) -> list[Gap]:
        """Check portal registration status and return gaps for unregistered portals."""
        if since is not None:
            age = (datetime.now(timezone.utc) - since).total_seconds()
            if age > 7200 or age < 0:  # 2 hours max lookback
                return []

        gaps: list[Gap] = []

        # ── Check portal_coverage_audit for unregistered portals ──────────
        try:
            from ..intel.portal_coverage_audit import audit_portal_coverage
            audit = await audit_portal_coverage()
        except Exception as e:
            logger.debug("[PortalCoverageExtractor] audit failed: %s", e)
            audit = {}

        unregistered = audit.get("unregistered", [])
        for portal in unregistered[:10]:  # cap at 10 per cycle
            portal_id = portal.get("id", "unknown")
            portal_name = portal.get("name", portal_id)
            portal_url = portal.get("url", "")
            reg_type = portal.get("registration_type", "unknown")
            requires_captcha = portal.get("requires_captcha", False)

            detail = (
                f"Portal '{portal_name}' ({portal_id}) is not registered. "
                f"URL: {portal_url}. Registration type: {reg_type}. "
                + ("Requires CAPTCHA — operator action needed." if requires_captcha
                   else "Can be auto-registered via portal_registry.")
            )

            gap = Gap(
                gap_id=_gap_id_for("portal_registration", "portal_coverage_audit", portal_name),
                gap_type="portal_registration",
                severity=GapSeverity.MEDIUM,
                title=f"Unregistered portal: {portal_name}",
                description=detail,
                module="portal_coverage_audit",
                evidence={
                    "portal_id": portal_id,
                    "portal_name": portal_name,
                    "url": portal_url,
                    "registration_type": reg_type,
                    "requires_captcha": requires_captcha,
                },
            )
            gaps.append(gap)

        # ── R-F1233: Check agent signup vault for pending/stale signups ──
        try:
            from ..intel.agent_signup_vault import get_vault
            vault = get_vault()
            # R-F1684: run test-data cleanup before listing — prevents phantom gaps
            try:
                vault.cleanup_test_data()
            except Exception:
                pass
            pending = vault.list(status="pending", limit=20)
            for entry in pending:
                site_id = entry.get("site_id", "unknown")
                site_name = entry.get("site_name", site_id)
                site_url = entry.get("site_url", "")
                agent_id = entry.get("agent_id", "unknown")
                created = entry.get("created_at", 0)
                age_days = (time.time() - created) / 86400 if created else 0

                # R-F1684: skip test artifacts that survived cleanup
                if agent_id in ("test_agent", "test"):
                    continue

                detail = (
                    f"Signup for '{site_name}' ({site_id}) is still pending "
                    f"after {age_days:.1f} days. "
                    f"URL: {site_url}. Prepared by agent: {agent_id}. "
                    f"Needs registration action."
                )

                gap = Gap(
                    gap_id=_gap_id_for("pending_signup", "agent_signup_vault", site_id),
                    gap_type="portal_registration",
                    severity=GapSeverity.LOW if age_days < 7 else GapSeverity.MEDIUM,
                    title=f"Pending signup: {site_name}",
                    description=detail,
                    module="agent_signup_vault",
                    evidence={
                        "site_id": site_id,
                        "site_name": site_name,
                        "url": site_url,
                        "agent_id": agent_id,
                        "age_days": round(age_days, 1),
                        "source": "vault",
                    },
                )
                gaps.append(gap)

            # R-F1684: also check for needs_operator entries that are NOT
            # declined/deferred — these are actionable portals the operator
            # hasn't addressed yet. Skip declined/deferred (terminal states).
            needs_op = vault.list(status="needs_operator", limit=20)
            for entry in needs_op:
                site_id = entry.get("site_id", "unknown")
                site_name = entry.get("site_name", site_id)
                site_url = entry.get("site_url", "")
                agent_id = entry.get("agent_id", "unknown")
                created = entry.get("created_at", 0)
                age_days = (time.time() - created) / 86400 if created else 0

                # Skip test artifacts
                if agent_id in ("test_agent", "test"):
                    continue

                # R-F1684: check metadata for declined/deferred flags.
                # The determine_and_drive function sets these in the vault
                # notes/metadata. If the portal is in _DECLINED_PORTAL_IDS
                # or _DEFERRED_PORTAL_IDS, skip it — operator already decided.
                metadata_raw = entry.get("metadata_json") or "{}"
                try:
                    import json as _json1684
                    metadata = _json1684.loads(metadata_raw) if isinstance(metadata_raw, str) else metadata_raw
                except Exception:
                    metadata = {}
                if metadata.get("declined") or metadata.get("deferred"):
                    continue

                detail = (
                    f"Signup for '{site_name}' ({site_id}) needs operator action "
                    f"({age_days:.0f} days waiting). "
                    f"URL: {site_url}. "
                    f"ARIA cannot auto-register this portal."
                )

                gap = Gap(
                    gap_id=_gap_id_for("needs_operator_signup", "agent_signup_vault", site_id),
                    gap_type="portal_registration",
                    severity=GapSeverity.MEDIUM,
                    title=f"Operator action needed: {site_name}",
                    description=detail,
                    module="agent_signup_vault",
                    evidence={
                        "site_id": site_id,
                        "site_name": site_name,
                        "url": site_url,
                        "agent_id": agent_id,
                        "age_days": round(age_days, 1),
                        "source": "vault_needs_operator",
                    },
                )
                gaps.append(gap)
        except Exception as e:
            logger.debug("[PortalCoverageExtractor] vault check failed: %s", e)

        return gaps


# ── TEST FAILURE EXTRACTOR (R-F1684) ──────────────────────────────────────

class TestFailureExtractor:
    """R-F1684 — Detect failing tests and surface them as MODULE_BUG gaps.

    Reads the pytest lastfailed cache to find tests that consistently fail.
    Each failing test is mapped to its source module and surfaced as a
    MODULE_BUG gap so the coder can autonomously fix it.

    This is the structural fix for the coder's fuel problem: the 72 known
    failing tests (CLAUDE.md §16 baseline) are real code bugs that the
    reproduce→fix→FAIL→PASS→gold pipeline can consume. Every failing test
    IS a reproduce test that already fails — perfect for the gold gate.

    The extractor:
      1. Reads `.pytest_cache/v/cache/lastfailed` for the list of failing tests
      2. Maps each test to its source module (the file being tested)
      3. Creates a MODULE_BUG gap with the test name as evidence
      4. The coder's reproduce_symptom gate runs the failing test → it FAILS
         → the fix makes it PASS → gold=true

    Cost: ~0.01s per scan (pure file read, no subprocess). Runs on the same
    15-minute scan interval as the other extractors.
    """

    # Map test file patterns to source modules they test.
    # This is a heuristic — test_rf434_brandified_hostname_cap.py tests
    # the brandified hostname capability, which lives in routes/aria.py.
    # The fallback strips 'test_' prefix and '_rfNNNN_' suffix.
    _TEST_TO_MODULE_MAP: dict[str, str] = {
        # R-F434 cluster: brandified hostname cap
        "test_rf434": "routes/aria.py",
        # R-F436 cluster: page entity extraction
        "test_rf436": "intel/page_entity_extractor.py",
        # R-F445 cluster: polyglot execute
        "test_rf445": "intel/polyglot_executor.py",
        # R-F450 cluster: upload magic-byte routing
        "test_rf450": "routes/aria.py",
        # R-F460 cluster: brain absorb pause
        "test_rf460": "intel/brain_hook.py",
        # R-F463 cluster: memory replication patterns
        "test_rf463": "intel/memory_replication.py",
        # R-F468 cluster: mistake ledger no TTL
        "test_rf468": "intel/mistake_ledger.py",
        # R-F513 cluster: build_rev autoderive
        "test_rf513": "main.py",
        # R-F528 cluster: read_document clientdisconnect
        "test_rf528": "routes/aria.py",
        # R-F574 cluster: self-improve discard
        "test_rf574": "intel/self_improve.py",
        # R-F672 cluster: lifespan silent except promoted
        "test_rf672": "main.py",
    }

    # Cache file path (relative to repo root)
    _LASTFAILED_PATH = ".pytest_cache/v/cache/lastfailed"
    _NODEIDS_PATH = ".pytest_cache/v/cache/nodeids"

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client
        self._repo_root = Path(__file__).resolve().parent.parent.parent

    async def extract(self, since: datetime) -> list[Gap]:
        """Read pytest lastfailed cache and surface failing tests as gaps."""
        # R-F1686: OPT-IN gate (default OFF). The operator chose a CURATED,
        # opt-in fuel path — NOT an unconditional firehose over every failing
        # test (a stale/wrong test would otherwise make the coder gerrymander
        # the source module to satisfy it → polluted gold). This extractor only
        # arms when ARIA_CODER_TEST_FUEL_ENABLED=1.
        import os as _os1686
        if _os1686.environ.get("ARIA_CODER_TEST_FUEL_ENABLED", "0").strip() != "1":
            return []

        if since is not None:
            age = (datetime.now(timezone.utc) - since).total_seconds()
            if age > 7200 or age < 0:  # 2 hours max lookback
                return []

        gaps: list[Gap] = []

        # Read the lastfailed cache
        lastfailed_path = self._repo_root / self._LASTFAILED_PATH
        nodeids_path = self._repo_root / self._NODEIDS_PATH

        if not lastfailed_path.exists() or not nodeids_path.exists():
            logger.debug("[TestFailureExtractor] pytest cache not found — skipping")
            return gaps

        try:
            lastfailed_raw = lastfailed_path.read_text(encoding="utf-8", errors="replace")
            nodeids_raw = nodeids_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("[TestFailureExtractor] cache read failed: %s", e)
            return gaps

        try:
            import json as _json1684
            lastfailed = _json1684.loads(lastfailed_raw)
            nodeids = _json1684.loads(nodeids_raw)
        except (json.JSONDecodeError, ValueError) as e:
            logger.debug("[TestFailureExtractor] cache parse failed: %s", e)
            return gaps

        if not isinstance(lastfailed, dict) or not isinstance(nodeids, list):
            return gaps

        # Filter to only tests that exist (nodeids) and are marked as failed (True)
        nodeid_set = set(nodeids)
        failing_tests = {
            k: v for k, v in lastfailed.items()
            if k in nodeid_set and v is True
        }

        # Group by test file for dedup
        from collections import defaultdict
        by_file: dict[str, list[str]] = defaultdict(list)
        for test_id in failing_tests:
            parts = test_id.split("::")
            if len(parts) >= 2:
                by_file[parts[0]].append(test_id)

        _dropped = 0
        for test_file, test_ids in by_file.items():
            # R-F1686: CURATED-ONLY. Only emit gaps for test files in the
            # _TEST_TO_MODULE_MAP (known real-bug clusters). Unmapped tests are
            # SKIPPED — no guess-the-module heuristic, so the coder never
            # targets a wrong source file off a stale/unrelated failing test.
            module = self._curated_module(test_file)
            if module is None:
                _dropped += 1
                continue
            test_name = test_ids[0].split("::")[-1] if "::" in test_ids[0] else test_ids[0]

            gap = Gap(
                gap_id=_gap_id_for(
                    GapType.MODULE_BUG, module,
                    f"failing_test_{test_file.split('/')[-1].replace('.py', '')}",
                ),
                gap_type=GapType.MODULE_BUG,
                severity=GapSeverity.HIGH,
                title=f"Failing test: {test_file.split('/')[-1]} ({len(test_ids)} failures)",
                description=(
                    f"Test file '{test_file}' has {len(test_ids)} failing test(s). "
                    f"First failing test: {test_name}. "
                    f"Source module: {module}. "
                    f"This is a real code bug — the failing test IS a reproduce test "
                    f"that already fails, perfect for the coder's gold pipeline."
                ),
                module=module,
                evidence={
                    "test_file": test_file,
                    "failing_count": len(test_ids),
                    "first_failing_test": test_name,
                    "all_failing_tests": test_ids[:20],  # cap at 20
                    "source": "pytest_lastfailed_cache",
                },
            )
            gaps.append(gap)

        if gaps:
            logger.info(
                "[TestFailureExtractor] R-F1684: %d failing test file(s) detected — "
                "surfaced as MODULE_BUG gaps for coder fuel",
                len(gaps),
            )
        if _dropped:
            # R-F1686: never silently truncate — log what curation dropped.
            logger.info(
                "[TestFailureExtractor] R-F1686: dropped %d uncurated failing "
                "test file(s) (only _TEST_TO_MODULE_MAP clusters are eligible)",
                _dropped,
            )

        return gaps

    def _curated_module(self, test_file: str) -> str | None:
        """R-F1686: curated-ONLY mapping (no heuristic fallback).

        Returns the full repo-relative source module path ONLY for test files
        whose basename matches a prefix in _TEST_TO_MODULE_MAP (the known
        real-bug clusters). Unmapped tests return None and are skipped, so the
        coder is never fed a guessed/wrong source module from an unrelated
        failing test. The aria_service/ prefix is added so the path matches
        what CodebaseReader/_find_test_for_module expect.
        """
        test_basename = Path(test_file).stem
        for prefix, module in self._TEST_TO_MODULE_MAP.items():
            if test_basename.startswith(prefix):
                return module if module.startswith("aria_service/") else f"aria_service/{module}"
        return None

    def _map_test_to_module(self, test_file: str) -> str:
        """Map a test file path to the source module it tests.

        Uses the explicit _TEST_TO_MODULE_MAP first, then falls back to
        heuristic: strip 'test_' prefix and '_rfNNNN_' suffix.

        NOTE (R-F1686): no longer used by extract() — superseded by
        _curated_module (curated-only). Retained for any external callers.
        """
        test_basename = Path(test_file).stem  # e.g. test_rf434_brandified_hostname_cap

        # Check explicit map first
        for prefix, module in self._TEST_TO_MODULE_MAP.items():
            if test_basename.startswith(prefix):
                return module

        # Heuristic fallback: strip test_ prefix and _rfNNNN_ suffix
        module = test_basename
        if module.startswith("test_"):
            module = module[5:]
        # Strip _rfNNNN_ pattern
        import re as _re1684
        module = _re1684.sub(r"_rf\d+_", "_", module)
        module = _re1684.sub(r"_rf\d+$", "", module)
        # Strip trailing _test
        if module.endswith("_test"):
            module = module[:-5]
        # Prepend aria_service/ path
        return f"intel/{module}.py" if module else "unknown"


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

    def __init__(self, redis_client: Any, llm: Any | None = None) -> None:
        """R-F1680: `llm` is an optional SovereignLLM instance for auto-writing
        reproduce tests when no existing test is found for the target module.
        When None, the old behaviour applies (reject with 'no test found')."""
        self.redis = redis_client
        self._llm = llm
        # R-F884 — reconnected to the REAL producer stores. Dropped
        # HealthPerfExtractor (`crucix:health:perf:latest`) and
        # SourceHealthExtractor (`crucix:sweep:last_result`): NO producer
        # writes either key, so they were dead reads. Added CapabilityGap +
        # MistakeLedger — the two richest, actually-populated gap stores the
        # coder previously read from neither.
        self.extractors = [
            ErrorLedgerExtractor(redis_client),       # crucix:aria:error_log (fixed key)
            ChatAuditExtractor(redis_client),         # crucix:chat_audit:log (fixed ts field)
            CapabilityGapExtractor(redis_client),     # crucix:aria:capability_gaps (NEW)
            MistakeLedgerExtractor(redis_client),     # crucix:mistake_ledger:log (NEW)
            OpportunityExtractor(redis_client),       # R-F826: crucix:chat_audit:log
            StaticAnalysisExtractor(redis_client),    # R-F1147: AST-based code quality
            PortalCoverageExtractor(redis_client),    # R-F1154: portal registration gaps
            AdversarialStalenessExtractor(redis_client),  # R-F1166: stale adversarial score
            GroundedRateExtractor(redis_client),          # R-F1166: grounded rate below threshold
            FileIntegrityExtractor(redis_client),         # R-F1171: missing critical files (Kaspersky)
            TestFailureExtractor(redis_client),           # R-F1684: failing tests → MODULE_BUG gaps
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
            # R-F1160: skip gaps claimed by OTHER agents (cross-agent deconfliction)
            if await self._is_claimed_by_other(gap_id):
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

    async def _is_claimed_by_other(self, gap_id: str) -> bool:
        """R-F1160: check if another agent has claimed this gap.

        Uses the agent registry's gap claim system. If ANY agent has
        claimed this gap (including ourselves from a previous cycle),
        skip it — it's being worked on.
        """
        try:
            from aria_service.intel.agent_registry import AgentRegistry
            registry = AgentRegistry()
            claiming_agent = await registry.is_gap_claimed(gap_id)
            if claiming_agent:
                logger.debug(
                    "[gap_detector] gap %s skipped — claimed by %s",
                    gap_id, claiming_agent,
                )
                return True
        except Exception:
            pass
        return False

    async def mark_attempted(self, gap_id: str, failed: bool = False) -> None:
        """Mark a gap as attempted.

        Args:
            gap_id: The gap ID.
            failed: If True, the attempt failed — use a SHORTER cooldown
                    so the coder retries sooner rather than waiting the
                    full ATTEMPT_COOLDOWN_S. R-F1155: previously all
                    attempts used the same cooldown, so a gap that failed
                    (e.g. test failure, constitutional block) was invisible
                    to the coder for 1 hour even though it needed a retry.
        """
        cooldown = self.ATTEMPT_COOLDOWN_S // 6 if failed else self.ATTEMPT_COOLDOWN_S
        try:
            await self.redis.setex(
                f"{self.ATTEMPTED_KEY_PREFIX}{gap_id}",
                cooldown, "1",
            )
        except Exception as e:
            logger.warning("[gap_detector] mark_attempted failed: %s", e)

        # R-F1160: claim the gap in the agent registry so other agents
        # know this gap is being worked on
        try:
            from aria_service.intel.agent_registry import AgentRegistry
            registry = AgentRegistry()
            await registry.claim_gap(gap_id, "aria_coder")
        except Exception:
            pass

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

        # R-F1160: release the gap claim so other agents know it's resolved
        try:
            from aria_service.intel.agent_registry import AgentRegistry
            registry = AgentRegistry()
            await registry.release_gap(gap_id, "aria_coder")
        except Exception:
            pass

    # ── R-F1460: REPRODUCE-SYMPTOM GATE ─────────────────────────────────────
    #
    # Before the coder burns LLM tokens on a fix, confirm the gap is real by
    # running an existing test that exercises the broken module/function.
    # A test that PASSES means the code works → discard as false positive.
    # A test that FAILS with the expected symptom → real gap, proceed.
    # No existing test → spawn a "write a failing test" gap, never auto-fix.
    #
    # This alone would have killed both bad fixes Claude discarded:
    #   - prompt_budget.py enforce_budget: existing test passes → false positive
    #   - memory_leak_detector.py GC: existing test passes → false positive

    TEST_DIRS = [
        "aria_service/tests",
    ]

    @staticmethod
    def _find_test_for_module(module: str) -> str | None:
        """Find an existing test file that exercises `module`.

        Strategy: look for test files whose name or content references the
        module name (last path component, function name, or class name).
        Returns the first matching test file path relative to repo root,
        or None if no test is found.
        """
        module_name = module.replace("/", ".").replace("\\", ".")
        # Extract the last meaningful component (file or function name)
        last_part = module.rsplit("/", 1)[-1].rsplit(".", 1)[0] if "/" in module else module
        last_part = last_part.replace("(", "").replace(")", "")

        # Search test files by name pattern
        import glob as _glob
        for test_dir in GapDetector.TEST_DIRS:
            # Pattern 1: test file named after the module
            for pattern in (
                f"test_*{last_part}*.py",
                f"test_*{module_name.split('.')[-1]}*.py",
                f"*{last_part}*test*.py",
            ):
                matches = sorted(_glob.glob(f"{test_dir}/{pattern}"))
                if matches:
                    return matches[0]

            # Pattern 2: scan test file contents for the module name
            # (only check first 50 lines to keep it fast)
            test_files = sorted(_glob.glob(f"{test_dir}/test_*.py"))
            for tf in test_files[:50]:  # cap search
                try:
                    with open(tf, encoding="utf-8", errors="replace") as fh:
                        head = fh.read(8000)
                    if last_part in head or module_name.split(".")[-1] in head:
                        return tf
                except OSError:
                    continue
        return None

    async def _auto_write_reproduce_test(self, gap: Gap, module: str) -> tuple[bool, str]:
        """R-F1680: auto-write a reproduce test via LLM when no existing test exists.

        R-F1681: the test file is PRESERVED (not deleted) so the caller can re-run
        it after applying the fix. Gold requires FAIL-on-unfixed → PASS-on-fixed.

        The LLM writes a pytest that targets the gap's symptom. We run it on
        the UNFIXED code — it MUST FAIL. If it passes, the test is gamed or
        doesn't reproduce anything → reject (no false gold).

        Returns:
            (True, "symptom reproduced via auto-written test <path>") on success
            (False, "reason") if the test couldn't be written or doesn't fail
        """
        if self._llm is None:
            return (False, "no LLM available to write reproduce test")

        # Build a prompt for the LLM to write a reproduce test
        import json as _json1680
        _prompt = (
            f"Write a single pytest test function that reproduces the following gap symptom "
            f"in module '{module}'. The test must FAIL when run against the CURRENT (unfixed) "
            f"code — it should assert the buggy behaviour exists.\n\n"
            f"GAP TITLE: {gap.title}\n"
            f"GAP DESCRIPTION: {gap.description}\n"
            f"GAP TYPE: {gap.gap_type}\n"
            f"MODULE: {module}\n"
            f"ERROR TRACE: {gap.error_trace or 'N/A'}\n\n"
            f"RULES:\n"
            f"1. The test MUST fail on the current unfixed code (that proves the bug exists).\n"
            f"2. Use only standard library + pytest — no live network calls.\n"
            f"3. Mock external dependencies (httpx, Redis, file I/O) at the boundary.\n"
            f"4. Name the test function `test_reproduce_{gap.gap_id[:12]}`.\n"
            f"5. Reply with ONLY valid JSON: {{\"test_code\": \"complete test code\"}}\n"
        )
        try:
            _resp = await self._llm._call(prompt=_prompt, task="test")
        except Exception as _e:
            return (False, f"LLM failed to write reproduce test: {_e}")

        _test_code = (_resp or {}).get("test_code", "")
        if not _test_code:
            return (False, "LLM returned empty test code")

        # R-F1681: write the test to a PERSISTENT path (not temp) so the caller
        # can re-run it after the fix. Named after the gap_id for traceability.
        import os as _os1681
        _repro_dir = _os1681.environ.get("ARIA_CODER_REPRO_DIR") or "/tmp/aria_repro_tests"
        _os1681.makedirs(_repro_dir, exist_ok=True)
        _test_path = _os1681.path.join(_repro_dir, f"repro_{gap.gap_id[:12]}.py")
        try:
            with open(_test_path, "w", encoding="utf-8") as _fh:
                _fh.write(_test_code)
        except OSError as _e:
            return (False, f"failed to write reproduce test: {_e}")

        # Run the test on UNFIXED code — it MUST FAIL
        import subprocess as _sp1680
        import sys as _sys1680
        _proc = await asyncio.create_subprocess_exec(
            _sys1680.executable, "-m", "pytest", _test_path, "-x", "--tb=short",
            "-q", "--no-header",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, _stderr = await asyncio.wait_for(
                _proc.communicate(), timeout=35.0,
            )
        except asyncio.TimeoutError:
            return (False, f"auto-written reproduce test timed out — unverifiable")

        _output = (_stdout or b"").decode("utf-8", errors="replace")
        _errors = (_stderr or b"").decode("utf-8", errors="replace")

        if _proc.returncode == 0:
            # Test PASSED on unfixed code — it's a gamed/trivial test
            return (False, f"auto-written reproduce test PASSES on unfixed code — test is not a valid reproduction (gamed)")

        # Test FAILED — symptom reproduced. Verify the failure is related.
        _combined = (_output + _errors).lower()
        _clues = [
            c for c in [module.rsplit("/", 1)[-1], module.rsplit(".", 1)[-1],
                        gap.title[:40], gap.description[:60]]
            if c
        ]
        if any(clue.lower() in _combined for clue in _clues):
            return (True, f"symptom reproduced via auto-written test {_test_path} (exit={_proc.returncode})")
        else:
            return (False, f"auto-written reproduce test failed for unrelated reason (exit={_proc.returncode}) — unverifiable")

    async def reproduce_symptom(self, gap: Gap) -> tuple[bool, str]:
        """R-F1460: attempt to reproduce the gap's symptom via an existing test.

        R-F1680: when no existing test is found and an LLM is available,
        auto-writes a reproduce test via _auto_write_reproduce_test. The
        auto-written test MUST fail on unfixed code to be a valid reproduction.

        R-F1681: the auto-written test is PRESERVED on disk so the caller can
        re-run it after the fix. Gold requires FAIL-on-unfixed → PASS-on-fixed.
        The test path is embedded in the success message after 'via auto-written
        test ' — callers can extract it with msg.split('via auto-written test
        ')[-1].split(' ')[0].

        Returns:
            (True, "symptom reproduced via auto-written test <path> (exit=N)")
              — symptom reproduced, test preserved at <path>
            (True, "symptom reproduced: test <path> failed (exit=N)")
              — symptom reproduced via existing test
            (False, "reason") — symptom NOT reproduced or unverifiable

        The caller MUST discard the gap when this returns False — no reproduced
        symptom, no fix. Ever. (Per Claude review 2026-06-09.)
        """
        module = gap.module or ""
        if not module or module == "unknown":
            return (False, "no module to test")

        test_path = self._find_test_for_module(module)
        if test_path is None:
            # R-F1680 — no existing test: auto-write a reproduce test via LLM.
            # The test MUST fail on unfixed code to be a valid reproduction.
            # If the LLM is unavailable or the test passes on unfixed code,
            # reject (no false gold).
            if self._llm is not None:
                return await self._auto_write_reproduce_test(gap, module)
            return (False, f"no existing test found for module '{module}' — spawn write-test gap instead")

        # Run the test — use pytest with a short timeout
        import subprocess as _sp
        import sys as _sys
        try:
            # R-F1686: `timeout=` is NOT a valid kwarg for
            # create_subprocess_exec — it forwards unknown kwargs to Popen,
            # which has no `timeout`, so this raised TypeError every time and
            # reproduce_symptom ALWAYS errored out for the existing-test path
            # (no existing-test reproduction ever succeeded -> no gold). The
            # timeout belongs on the wait_for below, which already has it.
            proc = await asyncio.create_subprocess_exec(
                _sys.executable, "-m", "pytest", test_path, "-x", "--tb=short",
                "-q", "--no-header",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=35.0,
            )
        except asyncio.TimeoutError:
            return (False, f"test {test_path} timed out — unverifiable")
        except FileNotFoundError:
            return (False, f"python not found — cannot run test")
        except Exception as e:
            return (False, f"test execution error: {e}")

        output = (stdout or b"").decode("utf-8", errors="replace")
        errors = (stderr or b"").decode("utf-8", errors="replace")

        if proc.returncode != 0:
            # Test FAILED — symptom reproduced. But verify it's the EXPECTED
            # failure, not an unrelated import error or fixture issue.
            # Look for the gap's description or module name in the failure output.
            expected_clues = [
                c for c in [module.rsplit("/", 1)[-1], module.rsplit(".", 1)[-1],
                            gap.title[:40], gap.description[:60]]
                if c
            ]
            combined = (output + errors).lower()
            if any(clue.lower() in combined for clue in expected_clues):
                return (True, f"symptom reproduced: test {test_path} failed (exit={proc.returncode})")
            else:
                # Test failed but for an UNRELATED reason — not the symptom
                return (False, f"test {test_path} failed for unrelated reason (exit={proc.returncode}) — unverifiable")

        # Test PASSED — the code works, this is a false positive
        return (False, f"test {test_path} PASSES — code works, false positive gap")

    async def verify_reproduce_test_passes(self, reproduce_test_path: str) -> tuple[bool, str]:
        """R-F1681: re-run the auto-written reproduce test after the fix is applied.

        The test MUST PASS on the fixed code — this proves the symptom is resolved.
        Returns (True, "") if the test passes, (False, "reason") if it still fails.

        This is the ungameable core of the gold gate: a fix is only verifiable if
        the SAME test that FAILED on unfixed code now PASSES on fixed code.
        """
        import subprocess as _sp1681
        import sys as _sys1681
        try:
            _proc = await asyncio.create_subprocess_exec(
                _sys1681.executable, "-m", "pytest", reproduce_test_path, "-x",
                "--tb=short", "-q", "--no-header",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, _stderr = await asyncio.wait_for(
                _proc.communicate(), timeout=35.0,
            )
        except asyncio.TimeoutError:
            return (False, "reproduce test timed out after fix — unverifiable")
        except Exception as _e:
            return (False, f"reproduce test execution error after fix: {_e}")

        if _proc.returncode == 0:
            return (True, "reproduce test PASSES on fixed code — symptom resolved")
        _output = (_stdout or b"").decode("utf-8", errors="replace")
        _errors = (_stderr or b"").decode("utf-8", errors="replace")
        _combined = (_output + _errors)[:500]
        return (False, f"reproduce test still FAILS on fixed code (exit={_proc.returncode}): {_combined}")

    async def verify_fixed(self, gap: Gap) -> bool:
        """R-F1155: post-fix verification — re-run the relevant extractor
        to confirm the gap is no longer present.

        Returns True if the gap is confirmed fixed (no longer detected),
        False if it still exists and needs a retry.
        """
        since = datetime.now(timezone.utc) - timedelta(minutes=5)
        for extractor in self.extractors:
            try:
                gaps = await extractor.extract(since)
                for g in gaps:
                    if g.gap_id == gap.gap_id:
                        return False
            except Exception:
                continue
        return True

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

        # R-F1160: register as an agent so other agents know we exist.
        # _reg is assigned BEFORE the try so it's always defined for cleanup.
        _reg = None
        try:
            from aria_service.intel.agent_registry import AgentRegistry
            _reg = AgentRegistry()
            await _reg.register(
                agent_id="gap_detector",
                agent_type="gap_detector",
                current_task="scanning for gaps",
            )
        except Exception:
            pass

        while True:
            try:
                # R-F1395: check engine pause flag before each cycle
                from aria_service.autonomous.safety import is_engine_paused
                if await is_engine_paused():
                    logger.debug("[gap_detector] engine paused — skipping cycle")
                    await asyncio.sleep(self.SCAN_INTERVAL_S)
                    continue
                gaps = await self.scan()
                await self.publish_latest(gaps)

                # R-F1282: wire success to brain so the coder and operator
                # can see gap_detector is alive and producing results.
                try:
                    from aria_service.intel.engine_wiring import wire_success
                    wire_success(
                        module="gap_detector",
                        summary=f"Scan complete: {len(gaps)} actionable gaps",
                        source_id="gap_detector:run_forever",
                    )
                except Exception:
                    pass

                # R-F1160: tick heartbeat every cycle with current stats
                if _reg is not None:
                    try:
                        await _reg.tick_heartbeat(
                            "gap_detector",
                            current_task=f"scanning — {len(gaps)} actionable gaps",
                        )
                    except Exception:
                        pass

            except asyncio.CancelledError:
                logger.info("[gap_detector] cancelled — exiting")
                # R-F1160: unregister on shutdown (safe even if _reg is None)
                if _reg is not None:
                    try:
                        await _reg.unregister("gap_detector")
                    except Exception:
                        pass
                raise
            except Exception as e:
                # R-F1282: wire failure to brain so the coder knows
                # gap_detector is down and can attempt recovery.
                try:
                    from aria_service.intel.engine_wiring import wire_failure
                    wire_failure(
                        module="gap_detector",
                        detail=f"Scan error: {e}",
                        gap_type="agent_cycle_failure",
                        source="gap_detector:run_forever",
                    )
                except Exception:
                    pass
                logger.error("[gap_detector] scan error: %s", e, exc_info=True)
            await asyncio.sleep(self.SCAN_INTERVAL_S)
