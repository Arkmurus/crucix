"""R-F2555 — Golden Intel promotion bridge.

Mines STRUCTURED, authoritative findings from ARIA's existing intelligence
sources (DD watchlist risk changes, sanctions designations, procurement/tenders,
DD-report risk triggers, ...) and normalizes them into the SAME signal schema
that ``news_monitor._build_intel_signal`` produces and that the Telegram gate
(``selectTelegramGoldenIntel``) + the dashboard split (``isDistributionReadyGolden``)
consume.

Why this exists: the Golden Intel gate + UI are correct, but the raw feed is
RSS-only, so "Distribution Ready" is near-empty. This bridge raises VOLUME by
mining stronger sources — WITHOUT lowering the gate. A finding only reaches
"Distribution Ready" if it is HONESTLY decision-grade (HIGH priority, tier_1a/1b/2,
confidence, evidence url, why-it-matters + recommended-action) — those are set
authoritatively by the SOURCE ADAPTER and merely packaged here, never fabricated.

Constitutional compliance:
- §21a wired: every promotion pass calls ``wire_success`` on success AND
  ``wire_failure`` on every error branch (imported from ``engine_wiring``).
- §6 files-only: writes only to the native ``redis_store``/state_store — no new
  persistence, no paid backend.
- never-false-clean: an adapter that CANNOT reach its source records a failure gap
  (``wire_failure``); it never returns an empty list that would read as "all clear".
- §22: schema + store contract verified against news_monitor.py:648-773 (2026-07-11).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from . import news_monitor as _nm
from . import redis_store as rs
from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.intel.golden_intel_bridge")

_MODULE = "golden_intel_bridge"

# Dedup ledger: id -> ISO timestamp of last promotion. A finding is promoted at
# most once per cooldown window so a per-poll pass does not flood the signal list
# with the same active designation/tender.
_PROMOTED_KEY = "crucix:golden_intel_bridge:promoted"
_PROMOTE_COOLDOWN_S = 7 * 86_400  # 7 days
_MAX_PROMOTED_KEYS = 4000

# Mirror of the Node gate's _GOLDEN_ALLOWED_TYPES (channelServerHooks.mjs:162) and
# the dashboard GOLDEN_DISTRIBUTION_TYPES. A finding whose signal_type is NOT here
# is still stored (it shows in the Mining Queue) but can never reach Distribution
# Ready — matching the real gate. Keep in sync with the Node set.
_GOLDEN_ALLOWED_TYPES = frozenset({
    "sanctions_change",
    "active_tender",
    "contract_award",
    "budget_movement",
    "programme_signal",
    "competitor_activity",
    "conflict_escalation",
})

# score consistent with an authoritatively-asserted confidence (drives the gate's
# secondary sort; _confidence(score) round-trips to the same bucket).
_CONFIDENCE_SCORE = {"HIGH": 82, "MEDIUM": 60, "LOW": 30}

# ── Adapter registry ─────────────────────────────────────────────────────────
# An adapter is an async callable returning a list of raw "finding" dicts. Each
# finding carries AUTHORITATIVE source metadata (see _normalize_finding_to_signal
# for the accepted keys). Adapters are registered at import time (bottom of file).
FindingAdapter = Callable[[], Awaitable[list[dict]]]
_ADAPTERS: dict[str, FindingAdapter] = {}


def register_adapter(name: str, fn: FindingAdapter) -> None:
    """Register a source adapter. Idempotent — re-registration overwrites."""
    _ADAPTERS[name] = fn


# ── Normalization ────────────────────────────────────────────────────────────
def _clean(v: Any) -> str:
    return str(v or "").strip()


def _safe_int(v: Any, default: int) -> int:
    """Coerce to int, never raise — a malformed ingested numeric must not abort a pass."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _finding_dedup_key(finding: dict) -> str:
    """Stable identity for a finding across passes: source|type|entity|ref.

    ``ref`` (a designation id / tender id / report id / date) makes a genuinely
    NEW event on the same entity a distinct signal, while a re-observed unchanged
    finding collapses to the same key (and is cooldown-suppressed)."""
    return "|".join([
        _clean(finding.get("source_key") or finding.get("source")),
        _clean(finding.get("signal_type")),
        _clean(finding.get("entity") or finding.get("target")).lower(),
        _clean(finding.get("ref") or finding.get("evidence_url") or finding.get("url")),
    ])


def _normalize_finding_to_signal(finding: dict) -> dict | None:
    """Package one authoritative finding into the exact news_monitor signal schema.

    Returns None only if the finding lacks the minimum to be a signal at all
    (no title/summary). Findings that are merely NOT decision-grade are still
    returned — the gate/dashboard place them in the Mining Queue; honesty is
    preserved by NOT inflating priority/tier/confidence here.
    """
    title = _clean(finding.get("decision_summary") or finding.get("title"))
    if not title:
        return None

    signal_type = _clean(finding.get("signal_type")) or "situational_awareness"
    priority = (_clean(finding.get("priority")) or "LOW").upper()
    confidence = (_clean(finding.get("confidence")) or "LOW").upper()
    source = _clean(finding.get("source")) or "ARIA monitored source"
    source_tier = (_clean(finding.get("source_tier")) or "unclassified").lower()
    evidence_count = _safe_int(finding.get("evidence_count"), 1) or 1
    entities = finding.get("entities") if isinstance(finding.get("entities"), dict) else {}
    target = _clean(finding.get("target") or finding.get("entity")) or source
    url = _clean(finding.get("evidence_url") or finding.get("url"))
    score = _safe_int(finding.get("score"), 0) or _CONFIDENCE_SCORE.get(confidence, 30)
    detected_at = _clean(finding.get("detected_at")) or datetime.now(timezone.utc).isoformat()

    quality_label = _nm._quality_label(priority, confidence, evidence_count)
    action_horizon = _nm._action_horizon(signal_type, priority)
    corroboration = "corroborated" if evidence_count >= 2 else "single-source"
    signal_id = _nm._article_hash(_finding_dedup_key(finding))

    return {
        "id": signal_id,
        "signal_type": signal_type,
        "priority": priority,
        "confidence": confidence,
        "score": score,
        "quality_label": quality_label,
        "confidence_rationale": _nm._confidence_rationale(
            source_tier=source_tier,
            signal_type=signal_type,
            entities=entities,
            evidence_count=evidence_count,
        ),
        "evidence_count": evidence_count,
        "corroboration": corroboration,
        "action_horizon": action_horizon,
        "urgency": "immediate" if action_horizon == "0-72h" else "near-term",
        "title": title[:220],
        "decision_summary": title[:220],
        "why_it_matters": _clean(finding.get("why_it_matters")),
        "recommended_action": _clean(finding.get("recommended_action")),
        "target": target,
        "source": source,
        "source_tier": source_tier,
        "category": _clean(finding.get("category")) or "promoted_intel",
        "language": _clean(finding.get("language")) or "en",
        "url": url,
        "published": _clean(finding.get("published")),
        "detected_at": detected_at,
        "promoted_by": _MODULE,
        "promotion_source": _clean(finding.get("source_key") or finding.get("source")),
        "entities": entities,
        "evidence": {
            "source": source,
            "source_tier": source_tier,
            "url": url,
            "count": evidence_count,
            "corroboration": corroboration,
        },
    }


# ── Dedup ledger ─────────────────────────────────────────────────────────────
async def _load_promoted() -> dict[str, str]:
    try:
        data = await rs.get_json(_PROMOTED_KEY)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.debug("[%s] promoted-ledger read failed", _MODULE, exc_info=True)
        return {}


def _recently_promoted(promoted: dict[str, str], signal_id: str, now: datetime) -> bool:
    ts = promoted.get(signal_id)
    if not ts:
        return False
    try:
        last = datetime.fromisoformat(ts)
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
    except Exception:
        return False
    return (now - last).total_seconds() < _PROMOTE_COOLDOWN_S


async def _save_promoted(promoted: dict[str, str]) -> None:
    # cap: keep the most-recent _MAX_PROMOTED_KEYS by timestamp
    if len(promoted) > _MAX_PROMOTED_KEYS:
        newest = sorted(promoted.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_PROMOTED_KEYS]
        promoted = dict(newest)
    try:
        await rs.set_json(_PROMOTED_KEY, promoted)
    except Exception:
        logger.debug("[%s] promoted-ledger write failed", _MODULE, exc_info=True)


# ── Promotion ────────────────────────────────────────────────────────────────
async def promote_findings(findings: list[dict], *, source_name: str) -> dict:
    """Normalize + dedup + persist a batch of findings from one adapter.

    Returns {promoted, skipped, invalid}. §21-wired by the caller
    (run_promotion_pass); this helper is also directly callable/testable.
    """
    now = datetime.now(timezone.utc)
    promoted_ledger = await _load_promoted()
    result = {"promoted": 0, "skipped": 0, "invalid": 0, "distribution_ready": 0}

    for finding in findings or []:
        # Per-finding guard: a single malformed finding (esp. from the external
        # ingest boundary) must never abort the pass or drop other drained items.
        try:
            signal = _normalize_finding_to_signal(finding)
        except Exception as exc:
            result["invalid"] += 1
            logger.warning("[%s] normalize failed (%s): %s", _MODULE, source_name, exc)
            wire_failure(_MODULE, f"normalize failed ({source_name}): {exc}",
                         gap_type="golden_intel_promotion_failure", source=source_name)
            continue
        if signal is None:
            result["invalid"] += 1
            continue
        if _recently_promoted(promoted_ledger, signal["id"], now):
            result["skipped"] += 1
            continue
        try:
            await _nm._store_intel_signal(signal)
        except Exception as exc:  # never-false-clean: a store failure is surfaced, not swallowed
            logger.warning("[%s] store failed for %s: %s", _MODULE, source_name, exc)
            wire_failure(_MODULE, f"store failed ({source_name}): {exc}", gap_type="golden_intel_promotion_failure",
                         source=source_name)
            continue
        promoted_ledger[signal["id"]] = now.isoformat()
        result["promoted"] += 1
        if _is_distribution_ready(signal):
            result["distribution_ready"] += 1

    if result["promoted"]:
        await _save_promoted(promoted_ledger)
    return result


def _is_distribution_ready(s: dict) -> bool:
    """Mirror selectTelegramGoldenIntel's per-signal filter (for metrics/tests)."""
    return (
        str(s.get("quality_label", "")).lower().startswith("decision-grade")
        and str(s.get("priority", "")).upper() == "HIGH"
        and str(s.get("confidence", "")).upper() in {"HIGH", "MEDIUM"}
        and s.get("signal_type") in _GOLDEN_ALLOWED_TYPES
        and str(s.get("source_tier", "")).lower() in {"tier_1a", "tier_1b", "tier_2"}
        and bool(s.get("decision_summary") or s.get("title"))
        and bool(s.get("why_it_matters"))
        and bool(s.get("recommended_action"))
        # match the STRICTEST real gate: the dashboard (signalEvidenceUrl) requires a
        # real http(s) evidence URL, so this metric must too or it overstates the count.
        and str(s.get("url") or (s.get("evidence") or {}).get("url") or "").startswith(("http://", "https://"))
    )


async def run_promotion_pass() -> dict:
    """Run every registered adapter, promote its findings, and report.

    §21a: wire_success on a completed pass, wire_failure per adapter that raises.
    Called from news_monitor.poll_feeds so it runs on the live poll cadence.
    """
    totals = {"promoted": 0, "skipped": 0, "invalid": 0, "distribution_ready": 0,
              "adapters_ok": 0, "adapters_failed": 0}
    for name, adapter in list(_ADAPTERS.items()):
        try:
            findings = await adapter()
        except Exception as exc:
            # never-false-clean: an adapter failure is a GAP, not a silent "all clear"
            logger.warning("[%s] adapter '%s' failed: %s", _MODULE, name, exc)
            wire_failure(_MODULE, f"adapter '{name}' failed: {exc}",
                         gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:{name}")
            totals["adapters_failed"] += 1
            continue
        res = await promote_findings(findings, source_name=name)
        for k in ("promoted", "skipped", "invalid", "distribution_ready"):
            totals[k] += res[k]
        totals["adapters_ok"] += 1

    # §21a honesty: only emit a success "engine ran" signal if at least one adapter
    # actually completed. If every adapter failed, the per-adapter wire_failure calls
    # already recorded the gaps — do not muddy telemetry with a success on top.
    if totals["adapters_ok"] > 0:
        wire_success(
            _MODULE,
            summary=(f"promotion pass: {totals['promoted']} promoted "
                     f"({totals['distribution_ready']} distribution-ready), "
                     f"{totals['skipped']} deduped, {totals['adapters_failed']} adapter failures"),
        )
    logger.info("[%s] pass complete: %s", _MODULE, json.dumps(totals))
    return totals


# ── Source adapters ──────────────────────────────────────────────────────────
# Only PUBLIC-domain, non-tenant-private sources are promoted to the public Golden
# Intel feed. Deliberately NOT included:
#   - DD watchlist alerts (dd_orchestrator.get_watchlist_alerts): user-scoped
#     (user_id / share_to_company) — promoting them to the public feed/channel
#     would be a cross-tenant/GDPR leak (cf. R-F2401/2402/2456). Needs an explicit
#     public/system-watchlist gate before it can be an adapter.
#   - Sanctions "recent designations": no diff/snapshot producer exists in Python
#     (verified 2026-07-11) — the source modules are name-screens only. Building a
#     designation-diff feed is a separate R-number, not fabricated here.

# Official government / multilateral procurement portals → source tier. These are
# public tender notices, safe for public distribution.
_PORTAL_TIER = {
    "TED": "tier_1a",              # EU official (Tenders Electronic Daily)
    "SAM_GOV": "tier_1a",         # US federal
    "CONTRACTS_FINDER": "tier_1a", # UK government
    "UNGM": "tier_1a",            # UN procurement
    "AFDB": "tier_1b",            # African Development Bank
    "SEACE_PERU": "tier_1b",
    "ELICITATIE_RO": "tier_1b",
    "MERX_CANADA": "tier_1b",
}
_TENDER_WINDOW_H = 48
_TENDER_RELEVANCE_FLOOR = 0.35   # below this, a tender is noise — not promoted at all
_MAX_TENDERS_PER_PASS = 25       # bound the shared 500-slot signal list vs a burst


async def _tender_adapter() -> list[dict]:
    """Promote NEW public procurement tenders (tender_monitor) into active_tender
    signals. Priority/confidence are set HONESTLY from the crawler's relevance
    score — only a high-relevance, product-matched, sourced tender from an official
    portal reaches HIGH/decision-grade (i.e. Distribution Ready). Bounded per pass so
    a 48h backlog burst cannot flood/evict the shared signal list."""
    from . import tender_monitor

    tenders = await tender_monitor.get_new_tenders(since_hours=_TENDER_WINDOW_H)
    if not tenders:
        # never-false-clean: get_new_tenders swallows its own read errors and returns
        # [] (tender_monitor.py:1350-1352), so an empty result could be a BROKEN store,
        # not "no new tenders". Re-probe the store via get_stats (which reads the same
        # keys) — if THAT raises, the source is genuinely unreadable → record a gap.
        try:
            await tender_monitor.get_stats()
        except Exception as exc:
            wire_failure(_MODULE, f"tender source unreadable: {exc}",
                         gap_type="golden_intel_promotion_failure",
                         source=f"{_MODULE}:tender_monitor")
        return []

    # Rank by relevance, drop noise below the floor, cap the batch.
    ranked = sorted(
        (t.to_dict() if hasattr(t, "to_dict") else dict(t) for t in tenders),
        key=lambda d: float(d.get("relevance_score") or 0.0),
        reverse=True,
    )
    ranked = [d for d in ranked
              if float(d.get("relevance_score") or 0.0) >= _TENDER_RELEVANCE_FLOOR
              ][:_MAX_TENDERS_PER_PASS]

    findings: list[dict] = []
    for d in ranked:
        rel = float(d.get("relevance_score") or 0.0)
        url = _clean(d.get("url"))
        matched = d.get("matched_products") or []
        portal = _clean(d.get("portal")).upper()
        # unmapped/unknown portal → tier_3 (fails the gate → Mining Queue only). An
        # unrecognised portal must NOT be treated as trusted (tier_2).
        tier = _PORTAL_TIER.get(portal, "tier_3")

        # Honest priority ladder — decision-grade only when genuinely actionable.
        if rel >= 0.60 and url and matched:
            priority = "HIGH"
        elif rel >= 0.45:
            priority = "MEDIUM"
        else:
            priority = "LOW"
        score = max(0, min(100, round(rel * 100)))
        confidence = _nm._confidence(score)  # >=72 HIGH, >=50 MEDIUM, else LOW

        buyer = _clean(d.get("buyer"))
        country = _clean(d.get("country"))
        value = _clean(d.get("value_estimate")) or "undisclosed"
        deadline = _clean(d.get("deadline")) or "unspecified"
        why = (f"{buyer or 'Buyer undisclosed'} ({country or 'n/a'}) — value {value}, "
               f"deadline {deadline}."
               + (f" Matched products: {', '.join(matched[:4])}." if matched else ""))

        findings.append({
            "source_key": "tender_monitor",
            "source": f"Procurement: {portal or 'portal'}",
            "signal_type": "active_tender",
            "priority": priority,
            "confidence": confidence,
            "score": score,
            "source_tier": tier,
            "title": _clean(d.get("title")),
            "why_it_matters": why,
            "recommended_action": "Assess bid/no-bid — review scope, eligibility and deadline.",
            "target": buyer or country or portal,
            "entities": {
                "countries": [country] if country else [],
                "products": list(matched)[:6],
                "oems": [],
            },
            "evidence_url": url,
            "url": url,
            "ref": _clean(d.get("id")),
            "detected_at": _clean(d.get("detected_at")),
            "published": _clean(d.get("publication_date")),
            "evidence_count": 1,
            "category": "procurement",
        })
    return findings


register_adapter("tender_monitor", _tender_adapter)


# ── Node→brain ingest inbox (R-F2557) ────────────────────────────────────────
# aria-web (Node) and aria-intel (Python) do NOT share a store, so Node-only
# sources (BD Intelligence / OpenSanctions) reach the bridge by POSTing structured
# findings to /api/aria/intel/promote/ingest -> push_to_inbox(). The inbox is
# drained each promotion pass by _inbox_adapter().
_INBOX_KEY = "crucix:golden_intel_bridge:inbox"
_MAX_INBOX = 300               # cap the queue (oldest dropped) — bounded growth
_MAX_INGEST_BATCH = 100        # per-POST cap
_INBOX_DRAIN_PER_PASS = 40     # promote at most this many per pass (vs 500-slot flood)

# Per-source honesty policy (SERVER-SIDE): cap what a NAMED source may claim. This
# constrains the DECLARED source (e.g. opensanctions -> Mining Queue). It is NOT a
# general anti-injection guarantee — auth is that (the ingest route is token-gated,
# routes/aria.py:251, fail-closed in prod). A caller is bound by the policy of whatever
# source label it declares; a relabelled finding takes that label's policy (or none).
_PRIORITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_TIER_RANK = {"tier_1a": 4, "tier_1b": 3, "tier_2": 2, "tier_3": 1, "unclassified": 0}
_RANK_PRIORITY = {3: "HIGH", 2: "MEDIUM", 1: "LOW"}
_RANK_TIER = {4: "tier_1a", 3: "tier_1b", 2: "tier_2", 1: "tier_3", 0: "unclassified"}
_SOURCE_POLICY = {
    # OpenSanctions live feed is a heuristic (OPENSANCTIONS_API_KEY unset in prod →
    # Treasury RSS fallback, no confirmed designation dates). Cap to Mining Queue:
    # MEDIUM priority alone keeps quality_label out of "decision-grade"; tier_2 is a
    # belt-and-suspenders ceiling. It can NEVER reach public "Distribution Ready".
    "opensanctions": {"max_priority": "MEDIUM", "max_tier": "tier_2"},
}


def _apply_source_policy(finding: dict) -> dict:
    pol = _SOURCE_POLICY.get(_clean(finding.get("source_key")))
    if not pol:
        return finding
    if "max_priority" in pol:
        cur = _PRIORITY_RANK.get((_clean(finding.get("priority")) or "LOW").upper(), 1)
        cap = _PRIORITY_RANK.get(pol["max_priority"], 2)
        if cur > cap:
            finding["priority"] = _RANK_PRIORITY[cap]
    if "max_tier" in pol:
        cur = _TIER_RANK.get((_clean(finding.get("source_tier")) or "unclassified").lower(), 0)
        cap = _TIER_RANK.get(pol["max_tier"], 2)
        if cur > cap:
            finding["source_tier"] = _RANK_TIER[cap]
    return finding


async def push_to_inbox(source: str, findings: list) -> int:
    """Queue structured findings pushed from the Node tier for the next promotion
    pass. Returns the count accepted. Defensive: a finding needs a title + a
    signal_type; source_key is stamped SERVER-SIDE (client claim ignored)."""
    source = _clean(source) or "unknown"
    accepted = 0
    for f in (findings or [])[:_MAX_INGEST_BATCH]:
        if not isinstance(f, dict):
            continue
        if not _clean(f.get("title") or f.get("decision_summary")):
            continue
        if not _clean(f.get("signal_type")):
            continue
        f["source_key"] = source
        try:
            await rs.lpush(_INBOX_KEY, json.dumps(f, default=str))
        except Exception as exc:
            logger.warning("[%s] inbox push failed for %s: %s", _MODULE, source, exc)
            wire_failure(_MODULE, f"inbox push failed ({source}): {exc}",
                         gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:ingest")
            break
        accepted += 1
    if accepted:
        try:
            await rs.ltrim(_INBOX_KEY, 0, _MAX_INBOX - 1)
        except Exception:
            logger.debug("[%s] inbox trim failed", _MODULE, exc_info=True)
    return accepted


async def _inbox_adapter() -> list[dict]:
    """Drain the Node→brain ingest inbox (atomic lpop_multi = processed once) and
    return the queued findings with the per-source honesty policy applied."""
    try:
        raw = await rs.lpop_multi(_INBOX_KEY, _INBOX_DRAIN_PER_PASS)
    except Exception as exc:
        wire_failure(_MODULE, f"inbox drain failed: {exc}",
                     gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:inbox")
        return []
    findings: list[dict] = []
    for r in raw or []:
        try:
            f = json.loads(r) if isinstance(r, str) else r
            if isinstance(f, dict):
                findings.append(_apply_source_policy(f))
        except Exception:
            continue
    return findings


register_adapter("ingested", _inbox_adapter)


# ── System-public watchlist adapter (R-F2559) ────────────────────────────────
_PUBLIC_WL_WINDOW_H = 168   # promote risk changes seen in the last 7 days

_PUBLIC_WL_TITLE = {
    "new_hit": "{e}: now appears on a sanctions list",
    "new_pep": "{e}: new PEP / adverse-media match",
    "removed": "{e}: removed from sanctions lists",
    "score_change": "{e}: sanctions match score changed",
}


async def _public_watchlist_adapter() -> list[dict]:
    """Promote operator-curated PUBLIC watchlist risk changes (dd_orchestrator
    system-public list) into sanctions_change signals. GDPR FAIL-CLOSED: reads only
    the physically-separate public alerts store, and REFUSES (records a gap for) any
    alert that carries a tenant field — a customer alert can never reach here, but the
    check is defense-in-depth so a future wiring mistake fails closed, not open."""
    from . import dd_orchestrator
    try:
        alerts = await dd_orchestrator.get_public_watchlist_alerts(since_hours=_PUBLIC_WL_WINDOW_H)
    except Exception as exc:
        wire_failure(_MODULE, f"public watchlist read failed: {exc}",
                     gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:public_watchlist")
        return []
    findings: list[dict] = []
    for a in alerts or []:
        if not isinstance(a, dict):
            continue
        # FAIL-CLOSED: never promote an alert that carries ANY tenant identifier.
        if (a.get("user_id") or a.get("user_email_domain")
                or a.get("impacted_deals") or a.get("run_id")):
            wire_failure(_MODULE, f"public watchlist alert carried a tenant field — REFUSED ({a.get('entity')})",
                         gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:public_watchlist")
            continue
        # positive gate: must be explicitly marked public.
        if _clean(a.get("scope")) != "system_public":
            continue
        entity = _clean(a.get("entity"))
        ct = _clean(a.get("change_type"))
        new_status = _clean(a.get("new_status")).upper()
        if not entity or not ct:
            continue
        worsening = ct in ("new_hit", "new_pep")
        # honest tiering: a confirmed NEW sanctions/PEP hit on a curated public entity
        # (screened against official OFAC/UN/FCDO lists) is decision-grade; a score
        # move or a removal is informational (Mining Queue).
        priority = "HIGH" if (worsening and new_status in ("HIT", "PEP")) else "MEDIUM"
        confidence = "HIGH" if worsening else "MEDIUM"
        findings.append({
            "source_key": "public_watchlist",
            "source": "ARIA Public Watchlist (sanctions/PEP re-screen)",
            "signal_type": "sanctions_change",
            "priority": priority,
            "confidence": confidence,
            "source_tier": "tier_1b",   # ARIA screen vs official sanctions/PEP lists
            "title": _PUBLIC_WL_TITLE.get(ct, "{e}: sanctions status change").format(e=entity),
            "why_it_matters": _clean(a.get("detail")) or f"{entity}: {a.get('old_status')} -> {new_status}.",
            "recommended_action": "Screen counterparties; review exposure to this entity.",
            "target": entity,
            "entities": {"countries": [], "products": [], "oems": []},
            "evidence_url": "https://sanctionssearch.ofac.treas.gov/",
            "url": "https://sanctionssearch.ofac.treas.gov/",
            # stable ref per (entity, transition) — dedups within the cooldown window.
            "ref": f"pubwl|{entity.lower()}|{ct}|{new_status}",
            "detected_at": _clean(a.get("timestamp")),
            "evidence_count": 1,
            "category": "sanctions",
        })
    return findings


register_adapter("public_watchlist", _public_watchlist_adapter)


# ── OFAC/UN/FCDO designation-diff adapter (R-F2560) ───────────────────────────
_SANCTIONS_DIFF_WINDOW_H = 168   # promote designations detected in the last 7 days


async def _sanctions_diff_adapter() -> list[dict]:
    """Promote GENUINELY NEW OFAC/UN/FCDO designations (sanctions_designation_diff)
    into decision-grade tier_1a `sanctions_change` signals — real primary-source
    sanctions intel (contrast: the OpenSanctions heuristic is capped to Mining Queue)."""
    from . import sanctions_designation_diff as sdd
    try:
        alerts = await sdd.get_designation_alerts(since_hours=_SANCTIONS_DIFF_WINDOW_H)
    except Exception as exc:
        wire_failure(_MODULE, f"sanctions diff read failed: {exc}",
                     gap_type="golden_intel_promotion_failure", source=f"{_MODULE}:sanctions_diff")
        return []
    findings: list[dict] = []
    for a in alerts or []:
        if not isinstance(a, dict):
            continue
        entity = _clean(a.get("entity"))
        if not entity:
            continue
        src = _clean(a.get("source"))
        list_type = _clean(a.get("list_type")) or src.upper()
        programs = _clean(a.get("programs"))
        url = _clean(a.get("citation_url"))
        if not url.startswith(("http://", "https://")):   # source-aware fallback (review #5)
            url = {
                "un": "https://www.un.org/securitycouncil/sanctions/information",
                "fcdo": "https://www.gov.uk/government/publications/financial-sanctions-consolidated-list-of-targets",
            }.get(src, "https://sanctionssearch.ofac.treas.gov/")
        why = (f"New {list_type} sanctions designation."
               + (f" Programs: {programs}." if programs else "")
               + (f" Listed {a.get('designation_date')}." if _clean(a.get("designation_date")) else ""))
        findings.append({
            "source_key": "sanctions_diff",
            "source": f"{list_type} designation",
            "signal_type": "sanctions_change",
            "priority": "HIGH",
            "confidence": "HIGH",
            "source_tier": "tier_1a",   # primary official designation
            "title": f"{entity}: newly designated on {list_type}",
            "why_it_matters": why,
            "recommended_action": "Screen counterparties; block/freeze per the designation.",
            "target": entity,
            "entities": {"countries": [], "products": [], "oems": []},
            "evidence_url": url,
            "url": url,
            "ref": f"sanctdiff|{_clean(a.get('source'))}|{_clean(a.get('id'))}",
            "detected_at": _clean(a.get("timestamp")),
            "evidence_count": 1,
            "category": "sanctions",
        })
    return findings


register_adapter("sanctions_diff", _sanctions_diff_adapter)
