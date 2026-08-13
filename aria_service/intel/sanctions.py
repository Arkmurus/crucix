"""ARIA Sanctions Intelligence 

— fuzzy entity screening with OpenSanctions integration.

The previous /api/aria/compliance/sanctions endpoint only checked the local Node
brain's exact-match `entityMatcher` and ARIA's knowledge base. That misses:

  - Transliteration variants (Gazprom / Газпром / Gazprоm with Cyrillic 'о')
  - Common aliases (UAC → United Aircraft Corporation; KAZ → Kazminerals)
  - Ownership-by-proxy ("Bank Rossiya subsidiary" → flag the parent)
  - Phonetic look-alikes ("Bin Salman" / "Ben Salman")
  - Truncated forms ("Wagner" → "PMC Wagner Group")

This module adds:
  1. Levenshtein-distance fuzzy matching (no external library — implemented inline)
  2. Phonetic Metaphone matching for Latin-script names
  3. OpenSanctions API integration (free, no key, https://api.opensanctions.org)
  4. Name variant generation (acronym expansion, common transliteration tables)
  5. Confidence scoring blended from string distance, phonetic, and authority weight

Usage:
    from .sanctions import fuzzy_screen
    result = await fuzzy_screen("Bank Rossiya")
    # → {matches: [...], top_score: 0.95, blocked: True, suggestions: [...]}"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import asyncio
import logging
import os
import re
import time as _time
from datetime import datetime, timezone  # R-F3019 — screened_at stamp
from typing import Any, NamedTuple

import httpx
from .wire import fail_wire  # R-F1789 §21 brain-wiring

logger = logging.getLogger("aria.sanctions")

OPENSANCTIONS_API = "https://api.opensanctions.org/match/default"
OPENSANCTIONS_SEARCH = "https://api.opensanctions.org/search/default"

# OpenSanctions API key — free tier has heavy rate limits (1 req/sec, 1000/month).
# A paid key (from https://www.opensanctions.org/api/) gives 100 req/sec and unlimited
# monthly volume, plus access to the premium PEP and adverse-media datasets.
# R-F1162 — also checks the portal_registry vault for stored credentials.
OPENSANCTIONS_API_KEY = os.getenv("OPENSANCTIONS_API_KEY", "").strip()

# R-F1542: dataset slug → (jurisdiction_code, human_label) mapping.
# Used by _normalise_match() to tag every match with its jurisdiction,
# so consumers (citation_block, DD report, chat) can say "matched on
# US OFAC SDN" vs "matched on UK OFSI" — preventing the Hikvision
# failure where a US OFAC hit was misrepresented as UK-sanctioned.
# Keys are lowercase substrings matched against dataset slugs.
_DATASET_JURISDICTIONS: dict[str, tuple[str, str]] = {
    # US
    "us_ofac":          ("US", "OFAC (US Treasury)"),
    "ofac_sdn":         ("US", "OFAC SDN (US Treasury)"),
    "us_sdn":           ("US", "OFAC SDN (US Treasury)"),
    "ns_cmic":          ("US", "NS-CMIC (US Treasury)"),
    "us_ssi":           ("US", "OFAC SSI (US Treasury)"),
    "us_bis":           ("US", "BIS Entity List (US Commerce)"),
    "us_unverified":    ("US", "BIS Unverified List (US Commerce)"),
    "us_mil_end_user":  ("US", "BIS Military End User (US Commerce)"),
    "us_dod":           ("US", "DoD Restricted List (US)"),
    "1260h":            ("US", "NDAA Sec 1260H (US DoD)"),
    "chinese_military": ("US", "Chinese Military Companies (US DoD)"),
    "1233":             ("US", "Sec 1233 Russian Defence (US DoD)"),
    "cmic":             ("US", "NS-CMIC (US Treasury)"),
    # UK
    "gb_hmt":           ("UK", "OFSI / HMT (UK Treasury)"),
    "gb_fcdo":          ("UK", "FCDO Sanctions (UK Foreign Office)"),
    "ofsi":             ("UK", "OFSI (UK Treasury)"),
    "uk_hmt":           ("UK", "OFSI / HMT (UK Treasury)"),
    # EU
    "eu_fsf":           ("EU", "EU Financial Sanctions File"),
    "eu_council":       ("EU", "EU Council Restrictive Measures"),
    "eu_consolidated":  ("EU", "EU Consolidated Sanctions"),
    # UN
    "un_sc":            ("UN", "UN Security Council Sanctions"),
    "un_consolidated":  ("UN", "UN Consolidated Sanctions"),
    # World Bank
    "world_bank":       ("WB", "World Bank Debarment List"),
    "wb_debar":         ("WB", "World Bank Debarment List"),
}


def _resolve_jurisdictions(datasets: list[str]) -> list[dict]:
    """Map OpenSanctions dataset slugs to jurisdiction labels.
    
    Given a list of dataset slugs like ["us_ofac_sdn", "eu_consolidated"],
    returns a deduplicated list of {"code": "US", "label": "OFAC SDN (US Treasury)"}.
    
    R-F1542: used by _normalise_match() to tag every match with its
    jurisdiction, preventing the Hikvision failure where a US OFAC hit
    was misrepresented as UK-sanctioned.
    """
    seen: set[str] = set()
    result: list[dict] = []
    for ds in datasets:
        dsl = ds.lower()
        for key, (code, label) in _DATASET_JURISDICTIONS.items():
            if key in dsl:
                if code not in seen:
                    seen.add(code)
                    result.append({"code": code, "label": label})
                break
    if not result:
        # Unknown dataset — tag as generic
        result.append({"code": "??", "label": f"Unknown list ({datasets[0] if datasets else '?'})"})
    return result


# ── R-F3500: breaker-open skips are aggregated, and never name the subject ───
#
# Live 2026-07-30 15:08 the operator saw ~30 lines in ONE second, each naming a
# watched entity ("Silverbrook Capital Management", "ROSSI FACILITY SERVICES",
# "Chemring Group PLC", ...) — one per alias, per entity, per 5-minute monitor
# pass, while the breaker was OPEN and no work was being done.
#
# Two problems, and the confidentiality one is the serious half. For a
# due-diligence product the list of entities under investigation IS commercially
# sensitive: it reveals who a client is looking at. log_redaction (R-F2851)
# covers credentials only — query params, bearer tokens, headers — so subject
# names went straight to the log stream.
#
# Suppressing the names must not suppress the SIGNAL, so the fact and the volume
# are still reported, once, with a count. Silence would be its own dishonesty.
_breaker_skips: int = 0
_breaker_skip_last_log: float = 0.0
_BREAKER_SKIP_LOG_INTERVAL_S = float(
    os.getenv("ARIA_SANCTIONS_SKIP_LOG_INTERVAL_S", "300"))


def _reset_breaker_skip_notes() -> None:
    global _breaker_skips, _breaker_skip_last_log
    _breaker_skips = 0
    _breaker_skip_last_log = 0.0


def _note_breaker_skip(_name: str = "") -> None:
    """Count one skipped screening call. The name is accepted and DISCARDED:
    taking it as a parameter keeps the call sites honest about what they hold,
    while guaranteeing it cannot reach a handler."""
    global _breaker_skips, _breaker_skip_last_log
    _breaker_skips += 1
    if _breaker_skip_last_log <= 0.0:
        # Start the window on the FIRST skip rather than flushing it. Otherwise
        # the opening call reports "1 skipped" and the rest of that pass lands in
        # a second line, so neither number describes the actual burst — and the
        # message promises a count "since the last notice".
        _breaker_skip_last_log = _time.monotonic()
        return
    _flush_breaker_skip_notes()


def _flush_breaker_skip_notes(force: bool = False) -> None:
    global _breaker_skips, _breaker_skip_last_log
    if _breaker_skips <= 0:
        return
    now = _time.monotonic()
    if not force and (now - _breaker_skip_last_log) < _BREAKER_SKIP_LOG_INTERVAL_S:
        return
    logger.info(
        "R-F469/R-F3500: OpenSanctions breaker OPEN — %d screening call(s) "
        "skipped since the last notice (subject names omitted deliberately: a "
        "DD subject list is confidential)", _breaker_skips,
    )
    _breaker_skips = 0
    _breaker_skip_last_log = now


async def _resolve_opensanctions_key() -> str:
    """Resolve the OpenSanctions API key from env or portal_registry vault.

    Priority: OPENSANCTIONS_API_KEY env var > portal_registry stored credential.
    """
    if OPENSANCTIONS_API_KEY:
        return OPENSANCTIONS_API_KEY
    try:
        from .portal_registry import get_credential
        cred = await get_credential("opensanctions")
        if cred and cred.get("api_key"):
            return cred["api_key"]
    except Exception:
        pass
    return ""


# ── R-F3476: pace the calls instead of earning 429s ─────────────────────────
#
# Observed live 2026-07-30: repeated `429 -> breaker CLOSED->OPEN (reason=
# rate_limit) -> backoff 300s -> 2400s`. R-F469 added that breaker as the
# response to a 429 storm. It stops the bleeding, but it treats the SYMPTOM:
# ARIA still fires as fast as it likes, earns a 429, and then has sanctions
# screening disabled for 5-40 minutes.
#
# The free-tier limit is documented (1 req/sec) and therefore knowable in
# ADVANCE, so the root fix is not to exceed it. Pacing is preferred over the
# breaker on this path deliberately: a paced request SUCCEEDS, where a
# breaker-skipped one returns no data — and on a sanctions path "slower" beats
# "absent". The breaker stays as the backstop for real upstream failure.
_OS_FREE_INTERVAL_S = float(os.getenv("ARIA_OPENSANCTIONS_FREE_INTERVAL_S", "1.05"))
# A paid key is 100 req/sec, so it needs only token pacing.
_OS_KEYED_INTERVAL_S = float(os.getenv("ARIA_OPENSANCTIONS_KEYED_INTERVAL_S", "0.02"))
_os_pace_lock: asyncio.Lock | None = None
_os_last_call: float = 0.0


def _os_reset_pacing() -> None:
    """Clear pacing state (tests, and operator recovery)."""
    global _os_last_call, _os_pace_lock
    _os_last_call = 0.0
    _os_pace_lock = None


# ── R-F3528: a 429 is TWO different failures wearing one status code ─────────
#
# Verified against the live API 2026-07-30 (one probe, keyed):
#
#   HTTP 429
#   {"detail":"This API key has exceeded its rate limit for the month.
#              Please wait to retry or contact support for a higher limit."}
#
# That is NOT the per-second limit the pacing in R-F3476 exists to avoid. It is
# the PLAN QUOTA, and it stays exhausted until the month rolls or the plan is
# upgraded.
#
# WHAT IS *NOT* WRONG, checked before changing anything: the R-F469 breaker does
# NOT churn at 300s. R-F1834 already gives it exponential backoff — base 300s ×
# 2^(open_cycles-1), capped at 24h — and open_cycles only resets on a success, so
# a month-long outage backs off to the cap on its own. My first draft of this
# comment claimed otherwise and was wrong; no second cooldown is added here,
# because the existing one is correct and a competing timer would be worse.
#
# What IS wrong is the NAME. Both cases reported reason="rate_limit", so the DD
# obstacle line told the reader ARIA was going too fast, when the true state is
# "the plan is spent" and the true action is the operator's: upgrade or wait for
# the reset. No amount of retrying or pacing can clear it, which makes it a §19e
# item rather than a transient. A wrong cause pointed at the wrong fix.
#
# The body text is the only signal: this response carries no Retry-After and no
# RateLimit-* header (checked on the live probe).
_QUOTA_MARKERS = ("for the month", "monthly", "quota")


def _classify_429(body: str) -> str:
    """'quota_exhausted' when the body says the PLAN limit is spent, else 'rate_limit'.

    Defaults to the transient reading. Mislabelling a per-second blip as a
    month-long outage would silently disable screening for weeks, so the
    ambiguous case takes the recoverable interpretation and lets the existing
    breaker handle it.
    """
    text = (body or "").lower()
    return "quota_exhausted" if any(m in text for m in _QUOTA_MARKERS) else "rate_limit"


_QUOTA_STATE_KEY = "crucix:aria:sanctions:opensanctions_quota_exhausted"


def _next_month_start_utc(now: datetime | None = None) -> datetime:
    """00:00 UTC on the first of the following month — when a MONTHLY plan resets.

    The allowance OpenSanctions meters is monthly, so that boundary is the
    earliest moment a spent quota can become unspent. Anything the flag says
    beyond it is a guess about the past, not a report about now.
    """
    n = now or datetime.now(timezone.utc)
    year, month = (n.year + 1, 1) if n.month == 12 else (n.year, n.month + 1)
    return datetime(year, month, 1, tzinfo=timezone.utc)


# ── R-F3947 — recovery latch. ────────────────────────────────────────────────
# True once this process has PROVEN the quota is not spent (a 200 from
# OpenSanctions) and retired the record. Exists so the clear costs ONE store op
# per recovery episode rather than one per screen: a delete on every successful
# call is the read-modify-write-per-call shape that produced the R-F2157 /
# R-F2172 state_store self-DOS, and fixing an observability bug by saturating
# the writer would be a poor trade.
_QUOTA_RECOVERY_NOTED = False
# Exactly one clear in flight at a time, so concurrent screens cannot pile up
# tasks against a contended writer. Strong refs, or the loop may collect a task
# before it runs.
_QUOTA_RECOVERY_INFLIGHT = False
_QUOTA_RECOVERY_TASKS: set = set()


def _reset_quota_recovery_latch() -> None:
    """Test hook — re-arm the latch so recovery can be observed again."""
    global _QUOTA_RECOVERY_NOTED, _QUOTA_RECOVERY_INFLIGHT
    _QUOTA_RECOVERY_NOTED = False
    _QUOTA_RECOVERY_INFLIGHT = False


async def _retire_quota_record() -> None:
    """Do the actual clear. Runs OFF the request path — see _note_... below."""
    global _QUOTA_RECOVERY_NOTED, _QUOTA_RECOVERY_INFLIGHT
    try:
        from . import redis_store as _rs
        await _rs.delete(_QUOTA_STATE_KEY)
    except Exception as exc:
        # Latch stays ARMED so a later success retries. Marking recovery before
        # achieving it would strand the record exactly as before, on one blip.
        logger.debug("R-F3947 could not retire the quota record: %s", exc)
        return
    finally:
        _QUOTA_RECOVERY_INFLIGHT = False
    _QUOTA_RECOVERY_NOTED = True
    logger.info(
        "[R-F3947] OpenSanctions answered — any exhaustion record retired. "
        "Full aggregate coverage is available again."
    )
    # §21a — the RECOVERY is an outcome, and it was the unobservable one: the
    # exhaustion wired a failure, nothing wired the return to health. Once per
    # episode, never per call, for the same reason the C-39 degraded notice is
    # announce-once: a per-screen signal is a ledger-filling flood.
    try:
        wire_success(
            module="sanctions",
            summary=("OpenSanctions quota recovered — the API answered, so the "
                     "exhaustion record was retired and full ~200-list "
                     "aggregate coverage is back."),
        )
    except Exception as exc:
        logger.debug("R-F3947 recovery wire failed: %s", exc)


async def _note_opensanctions_success():
    """A 200 from OpenSanctions is PROOF the plan quota is not spent.

    THE DEFECT THIS CLOSES. The exhaustion record was written on a 429 and could
    be removed only by a human calling `clear_opensanctions_quota_state()`. So
    the state moved in one direction only, and the surface an operator consults
    to answer "is screening degraded?" kept saying yes long after the answer was
    no. It produced a wrong recommendation on 2026-08-13 — "upgrade the
    OpenSanctions plan" — for a plan that was answering normally.

    Setting a flag on evidence (the 429 body) and clearing it only by hand is
    what makes a latch. The symmetric fix is to clear it on the evidence of the
    same kind, which is what this is. It also covers the case the monthly
    boundary cannot: an operator who UPGRADES the plan mid-month gets a correct
    surface immediately, without waiting for a reset that is no longer relevant.

    ⚠️ THIS MUST NEVER BLOCK THE SCREEN, and the first version of it did.
    R-F3947 shipped with `await _rs.delete(...)` inline here, on the success
    branch of every OpenSanctions call. MEASURED LIVE minutes later: the screen
    endpoint went from sub-second to a hard timeout — POST /sanctions/fuzzy
    HTTP=000 at 150s — while the OpenSanctions API itself answered in 0.11s from
    the same machine and a name too short to trigger a lookup returned in 0.16s.
    The sqlite writer is contended (the knowledge flush holds it for a large
    fraction of every minute), and because a FAILED clear deliberately leaves the
    latch armed, every subsequent screen retried the same blocking write.

    So it scheduled bookkeeping on the critical path of the product's most
    important call, and made a screening outage out of a status-reporting fix.
    Retiring a stale flag is not part of producing a screen result and does not
    belong in its latency budget.

    Now it SCHEDULES and returns immediately. Returns the task (or None) purely
    so tests can await the work deterministically — production never does.
    `_QUOTA_RECOVERY_INFLIGHT` keeps exactly one clear in flight, so concurrent
    screens cannot pile up tasks against a slow store.
    """
    global _QUOTA_RECOVERY_INFLIGHT
    if _QUOTA_RECOVERY_NOTED or _QUOTA_RECOVERY_INFLIGHT:
        return None
    _QUOTA_RECOVERY_INFLIGHT = True
    try:
        task = asyncio.get_running_loop().create_task(_retire_quota_record())
    except RuntimeError:            # no running loop (sync caller / teardown)
        _QUOTA_RECOVERY_INFLIGHT = False
        return None
    # Hold a reference so the task is not garbage-collected mid-flight.
    _QUOTA_RECOVERY_TASKS.add(task)
    task.add_done_callback(_QUOTA_RECOVERY_TASKS.discard)
    return task


async def _record_quota_exhausted(detail: str) -> None:
    """Persist + surface, until the monthly boundary that can clear it.

    Durable because the fact outlives the process: a restart must not present a
    spent monthly quota as a fresh start, which is the same mistake R-F3513
    found in the LLM billing cooldown.

    BOUNDED because the opposite mistake is just as wrong, and this had it.
    MEASURED 2026-08-04: the state still read "exhausted, since
    2026-07-31T23:04:22" — four days after a MONTHLY allowance had rolled over
    on 08-01. It was written with no TTL, nothing re-probes it, and the operator
    clear path the old docstring promised ("only the operator can clear this
    one") did not exist anywhere in the codebase. So the flag could only ever
    move in one direction: once set, exhausted forever.

    That is a status surface asserting a fact it stopped being able to know.
    Nothing gated on it — screening always attempted OpenSanctions and fell back
    to the local canonical lists — so the cost was not a blocked screen; it was
    an operator reading "quota exhausted" and believing screening was degraded
    when it was not. This session began with the same class of defect in
    deploy-fly.yml ("the CI FLY_API_TOKEN is stale") and in CLAUDE.md §16.

    The record now carries `expires_at`, and the reader treats an elapsed
    boundary as LAPSED rather than exhausted. A quota still spent after the
    reset simply gets recorded again on the next 429 — self-healing in the safe
    direction, because being wrong here means attempting a screen, not skipping
    one.
    """
    # R-F3947 — re-arm the recovery latch: the quota really is spent again, so a
    # later 200 must be able to retire this new record too.
    global _QUOTA_RECOVERY_NOTED, _QUOTA_RECOVERY_INFLIGHT
    _QUOTA_RECOVERY_NOTED = False
    _QUOTA_RECOVERY_INFLIGHT = False
    try:
        from . import redis_store as _rs
        _now = datetime.now(timezone.utc)
        _expires = _next_month_start_utc(_now)
        await _rs.set_json(_QUOTA_STATE_KEY, {
            "since": _now.isoformat(timespec="seconds"),
            "expires_at": _expires.isoformat(timespec="seconds"),
            "detail": detail[:300],
            "action": ("operator: upgrade the OpenSanctions plan, or wait for the "
                       "monthly reset — retrying cannot clear it before then"),
        }, ex=max(60, int((_expires - _now).total_seconds())))
    except Exception as exc:
        logger.debug("could not persist quota state: %s", exc)
    try:
        wire_failure(
            module="sanctions",
            detail=(
                "BLOCKED: OpenSanctions monthly plan quota EXHAUSTED — screening "
                "via OpenSanctions is unavailable until the plan resets or is "
                "upgraded. This is an operator action; retrying cannot clear it. "
                f"Upstream said: {detail[:160]}"
            ),
            gap_type="sanctions_source_unavailable",
            source="sanctions:_record_quota_exhausted",
        )
    except Exception as exc:
        logger.debug("quota wire_failure failed: %s", exc)


@fail_wire(module="sanctions", gap_type="source_failure")
async def get_opensanctions_quota_state() -> dict:
    """Operator surface: is the plan quota spent, and since when?

    Exists so the answer to "why is sanctions screening degraded" is queryable
    rather than something an operator has to find in a log line.
    """
    try:
        from . import redis_store as _rs
        state = await _rs.get_json(_QUOTA_STATE_KEY)
    except Exception as exc:
        return {"exhausted": None, "reason": f"could not read state: {exc}"}
    if not state:
        return {"exhausted": False}
    # A monthly allowance cannot still be spent past the month boundary that
    # resets it. Backends without key expiry (and records written before
    # expires_at existed) would otherwise report exhausted indefinitely — which
    # is what this surface actually did for four days.
    # R-F3947 — a legacy record (no `expires_at`) is deliberately NOT lapsed by
    # inference here. That was tried and reverted: deriving the boundary from
    # `since` would flip it to "fine" on a reset NOBODY OBSERVED, which
    # test_opensanctions_quota_flag_lapses pins as a decision, with reasons.
    # The right answer is stronger evidence, not a better guess — a 200 from the
    # API retires the record outright (`_note_opensanctions_success`), and that
    # is an observation. It also covers what no boundary can: a plan upgraded
    # mid-month.
    _exp = state.get("expires_at")
    if _exp:
        try:
            if datetime.now(timezone.utc) >= datetime.fromisoformat(_exp):
                return {
                    "exhausted": False,
                    "lapsed_at": _exp,
                    "note": ("a previous exhaustion record lapsed at the monthly "
                             "reset; the next 429 will record a fresh one"),
                }
        except Exception:
            pass       # unparsable expiry: fall through and report as exhausted
    return {"exhausted": True, **state}


# R-F3767 — §21a. Returns a BOOL, so a failure reads as "the quota was not
# cleared" — the same shape as "there was nothing to clear". This is the reset
# for the OpenSanctions quota-exhausted state (§18), so a silent failure leaves
# screening on the local canonical floor while the operator believes the plan
# was restored. gap_type mirrors the module's own convention (5 existing uses).
@fail_wire(module="sanctions", gap_type="source_failure")
async def clear_opensanctions_quota_state() -> bool:
    """Operator lever: forget the exhaustion record now.

    The old docstring said "only the operator can clear this one" while offering
    the operator no way to do it. Either the sentence or the code had to change;
    this is the code changing. Use after a plan upgrade, when waiting for the
    monthly boundary is not the answer.

    Safe by construction: clearing only allows OpenSanctions to be ATTEMPTED
    again. If the quota really is still spent the next call returns 429 and the
    record is rewritten, so a wrong clear costs one request, not a false clean.
    """
    try:
        from . import redis_store as _rs
        await _rs.delete(_QUOTA_STATE_KEY)
        return True
    except Exception as exc:
        logger.warning("could not clear quota state: %s", exc)
        return False


def _rate_limit_message(key_present: bool, interval: float) -> str:
    """R-F3476 — say what is MEASURED, never assert an unverified cause.

    The old text was unconditional: "rate-limited (free tier: 1 req/sec). Set
    OPENSANCTIONS_API_KEY for unlimited access." It printed that even when the
    key WAS set (it is Deployed on aria-intel and IS sent as `Authorization:
    ApiKey ...`), so the log named a cause the evidence contradicted — and a
    reviewer acting on it recorded "a deployed key that buys nothing".
    """
    if key_present:
        return (
            f"OpenSanctions rate-limited (429). api_key_present=True — the "
            f"configured key hit its plan/quota limit, so this is NOT a missing "
            f"credential. Client pacing is {interval:.2f}s/req; if 429s persist "
            f"the plan quota is the constraint."
        )
    return (
        f"OpenSanctions rate-limited (429). api_key_present=False — running "
        f"UNKEYED on the 1 req/sec free tier at {interval:.2f}s/req. Set "
        f"OPENSANCTIONS_API_KEY for higher quota."
    )


async def _opensanctions_pace() -> None:
    """Block until the next call is allowed by the client-side rate limit.

    The lock is held ACROSS the sleep on purpose: that is what serialises
    concurrent callers into a single paced stream. Without it, N coroutines
    would all read the same `_os_last_call` and fire together — which is how
    the 429 storm happened.
    """
    global _os_last_call, _os_pace_lock
    interval = _OS_KEYED_INTERVAL_S if await _resolve_opensanctions_key() else _OS_FREE_INTERVAL_S
    if interval <= 0:
        return
    if _os_pace_lock is None:
        _os_pace_lock = asyncio.Lock()
    async with _os_pace_lock:
        wait = interval - (_time.monotonic() - _os_last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _os_last_call = _time.monotonic()


async def _opensanctions_headers() -> dict:
    """Build headers for OpenSanctions API calls, including auth if available.

    Checks both the env var and the portal_registry vault for credentials.
    """
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "ARIA-Sanctions/1.0 (Arkmurus defence intelligence)",
    }
    key = await _resolve_opensanctions_key()
    if key:
        headers["Authorization"] = f"ApiKey {key}"
    return headers

# ── String distance: Levenshtein (no external dep) ──────────────────────────

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance. O(n*m), pure Python — fine for short names."""
    if a == b: return 0
    if not a: return len(b)
    if not b: return len(a)
    if len(a) > len(b):
        a, b = b, a
    prev = list(range(len(a) + 1))
    for i, cb in enumerate(b, 1):
        curr = [i] + [0] * len(a)
        for j, ca in enumerate(a, 1):
            cost = 0 if ca == cb else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[-1]

def _similarity(a: str, b: str) -> float:
    """0..1 similarity score from Levenshtein. 1.0 = identical."""
    a, b = a.lower().strip(), b.lower().strip()
    if not a or not b: return 0.0
    if a == b: return 1.0
    max_len = max(len(a), len(b))
    return 1.0 - (_levenshtein(a, b) / max_len)


# ── Phonetic: simplified Metaphone ──────────────────────────────────────────
# Strips vowels (except leading) and normalises consonant clusters so that
# "Smith" / "Smyth" / "Schmidt" all collapse to a similar code.

_VOWELS = set("aeiouy")
_METAPHONE_RULES = [
    (r"[^a-z]", ""),
    (r"^kn", "n"), (r"^gn", "n"), (r"^pn", "n"), (r"^wr", "r"),
    (r"^x", "s"),  (r"^wh", "w"),
    (r"ph", "f"),  (r"th", "0"),
    (r"sch", "sk"),(r"sh", "x"), (r"ch", "x"),
    (r"ck", "k"),  (r"cq", "k"), (r"cc", "k"),
    (r"qu", "kw"), (r"q", "k"),
    (r"x", "ks"),
]

def _metaphone(name: str) -> str:
    s = name.lower()
    for pat, repl in _METAPHONE_RULES:
        s = re.sub(pat, repl, s)
    if not s: return ""
    # Keep first letter (vowel or consonant), strip vowels from rest
    return s[0] + "".join(c for c in s[1:] if c not in _VOWELS)


# ── Transliteration / variant generation ────────────────────────────────────

# Common Cyrillic → Latin substitutions used in sanctions evasion
_CYRILLIC_TO_LATIN = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "Е": "E", "О": "O", "Р": "P", "С": "C", "У": "Y", "Х": "X",
})

# Reverse: Latin → Cyrillic look-alike (catches obfuscation attempts)
_LATIN_TO_CYRILLIC = str.maketrans({
    "a": "а", "e": "е", "o": "о", "p": "р", "c": "с", "y": "у", "x": "х",
})

# Defence-industry acronym expansions
_ACRONYMS = {
    "UAC": "United Aircraft Corporation",
    "URSC": "United Shipbuilding Corporation",
    "USC": "United Shipbuilding Corporation",
    "NPK": "Naval Production Combine",
    "RTC": "Russian Technologies Corporation",
    "UEC": "United Engine Corporation",
    "KAMAZ": "KamAZ",
    "PMC": "Private Military Company",
    "VKO": "Aerospace Defence Forces",
    "FSB": "Federal Security Service",
    "GRU": "Main Intelligence Directorate",
}

# R-F1562 (2026-06-14): Arabic-script → Latin transliteration table.
# Sanctions false-negative class: a sanctioned person spelled in Arabic
# script (e.g. "محمد بن سلمان") generated NO Latin variant, so it never
# matched OpenSanctions' Latin-script primary names — a legal-liability
# miss. This is a compact, deterministic map of the common consonants +
# long vowels used on OFAC/UN/EU sanctions entries. It is NOT a full
# Arabic romanisation engine (no dependency pulled in); it is tuned to
# produce a Latin string close enough for the fuzzy/phonetic matcher.
_ARABIC_TO_LATIN = {
    # Hamza / alef family → 'a' (alef variants collapse to a)
    "ء": "", "آ": "a", "أ": "a", "إ": "i", "ا": "a", "ٱ": "a", "ى": "a",
    # Consonants
    "ب": "b", "ت": "t", "ث": "th", "ج": "j", "ح": "h", "خ": "kh",
    "د": "d", "ذ": "dh", "ر": "r", "ز": "z", "س": "s", "ش": "sh",
    "ص": "s", "ض": "d", "ط": "t", "ظ": "z", "ع": "a", "غ": "gh",
    "ف": "f", "ق": "q", "ك": "k", "ل": "l", "م": "m", "ن": "n",
    "ه": "h", "ة": "a", "و": "w", "ي": "y", "ئ": "y", "ؤ": "w",
    # Persian/Urdu letters occasionally on sanctions entries
    "پ": "p", "چ": "ch", "ژ": "zh", "گ": "g", "ک": "k", "ی": "y",
    "ٔ": "",
}
# Arabic diacritics (harakat / tatweel) stripped before transliteration.
_ARABIC_DIACRITICS = "".join([
    "ً", "ٌ", "ٍ", "َ", "ُ", "ِ",
    "ّ", "ْ", "ٓ", "ٔ", "ٕ", "ٰ",
    "ـ",  # tatweel (kashida)
])
# Detects any Arabic-script codepoint (Arabic + Arabic Supplement +
# Arabic Presentation Forms).
_ARABIC_SCRIPT_RE = re.compile(r"[؀-ۿݐ-ݿﭐ-﷿ﹰ-﻿]")

# R-F1562: maximum variants returned, to cap explosion / API hammering.
_MAX_VARIANTS = 24


def _transliterate_arabic(text: str) -> str:
    """R-F1562: lightweight Arabic-script → Latin transliteration.

    Strips diacritics/tatweel, maps each Arabic letter to its common
    Latin form, leaves Latin/space/punctuation untouched, and collapses
    whitespace. Returns "" if nothing transliterable was produced.
    """
    if not text:
        return ""
    # Drop harakat / tatweel
    cleaned = "".join(ch for ch in text if ch not in _ARABIC_DIACRITICS)
    out: list[str] = []
    for ch in cleaned:
        if ch in _ARABIC_TO_LATIN:
            out.append(_ARABIC_TO_LATIN[ch])
        else:
            out.append(ch)
    result = re.sub(r"\s+", " ", "".join(out)).strip()
    return result


def _nasab_variants(name: str) -> list[str]:
    """R-F1562: nasab-/particle-aware reordered name variants.

    Routes the name through person_resolver.parse_components (verified
    sync def at person_resolver.py:100, signature `parse_components(name)
    -> NameComponents`) and produces forms that catch reordered Arabic
    nasab names (given + bin/ibn/bint/abu/al + family). Imported lazily
    to avoid an import cycle (person_resolver does not import sanctions,
    but keep it lazy as a guard). Returns [] on any failure.
    """
    if not name or not name.strip():
        return []
    # Pre-normalise hyphens to spaces so a glued family particle like
    # "Al-Saud" is seen by the component parser as the particle "al" +
    # surname "Saud" (otherwise "al-saud" is one token, never a particle).
    pre = re.sub(r"[-]", " ", name)
    try:
        from . import person_resolver as _pr  # lazy import (avoid cycle)
        comp = _pr.parse_components(pre)
    except Exception:
        return []

    out: list[str] = []
    given = [g for g in (comp.given or []) if g]
    surname = (comp.surname or "").strip()
    particles = [p for p in (comp.particles or []) if p]

    if not given and not surname:
        return []

    norm_particles = [p.lower().strip("-") for p in particles]
    has_al = ("al" in norm_particles) or ("el" in norm_particles)

    # 1. Particle-normalised full form: given + (kept particles) + surname.
    #    "Ahmed bin Mohammed Al-Saud" → "Ahmed bin Mohammed al Saud"
    if surname:
        full_kept = " ".join(given + norm_particles + [surname]).strip()
        if full_kept:
            out.append(full_kept)

    # 2. Particles DROPPED entirely (most sanctions DBs store bare
    #    given+family): "Ahmed bin Mohammed Al-Saud" → "Ahmed Mohammed Saud"
    if surname:
        dropped = " ".join(given + [surname]).strip()
        if dropped:
            out.append(dropped)

    # 3. given[0] + surname short forms (patronymics dropped):
    #    "Ahmed ... Al-Saud" → "Ahmed Saud" / "Ahmed Al Saud" / "Ahmed Al-Saud"
    if given and surname:
        out.append(f"{given[0]} {surname}")
        if has_al:
            out.append(f"{given[0]} Al {surname}")
            out.append(f"{given[0]} Al-{surname}")

    # 4. Full given block + family with the al/el particle kept inline:
    #    "Ahmed Mohammed Al Saud" — the common Latin sanctions spelling.
    if surname and has_al:
        out.append(" ".join(given + ["Al", surname]).strip())

    # 5. Reorder: surname first (some lists store family-name-first):
    #    "Saud Ahmed Mohammed"
    if surname and given:
        out.append(" ".join([surname] + given).strip())

    # Dedupe, drop the exact input echo (already covered by caller).
    seen: set[str] = set()
    deduped: list[str] = []
    for v in out:
        v = re.sub(r"\s+", " ", v).strip()
        key = v.lower()
        if v and key not in seen:
            seen.add(key)
            deduped.append(v)
    return deduped


def _generate_variants(name: str) -> list[str]:
    """Generate plausible name variants for fuzzy matching against sanctions lists."""
    n = name.strip()
    if not n:
        return []

    # R-F1562: ordered list so high-salience variants (nasab reorderings,
    # Arabic→Latin) land in the first slots — fuzzy_screen only queries
    # variants[:6], so a nasab/Arabic miss had no chance of reaching the
    # API. `ordered` preserves priority; `seen` dedupes case-insensitively.
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(v: str) -> None:
        if not v:
            return
        v = v.strip()
        if not v or len(v) < 2:
            return
        key = v.lower()
        if key in seen:
            return
        seen.add(key)
        ordered.append(v)

    _add(n)

    # R-F1562: Arabic-script → Latin (FIRST, so it reaches the API). If the
    # input is in Arabic script it otherwise produces zero Latin matches.
    if _ARABIC_SCRIPT_RE.search(n):
        latin = _transliterate_arabic(n)
        if latin and latin.lower() != n.lower():
            _add(latin)
            # Also feed the Latin form through the nasab reorderer so
            # "محمد بن سلمان" → "muhammad bin salman" → "muhammad salman".
            for nv in _nasab_variants(latin):
                _add(nv)

    # R-F1562: nasab-/particle-aware reorderings on the original (Latin)
    # input — catches "Ahmed bin Mohammed Al-Saud" ⇄ "Ahmed Mohammed Saud".
    for nv in _nasab_variants(n):
        _add(nv)

    # Whole-string transformations
    _add(n.translate(_CYRILLIC_TO_LATIN))   # Cyrillic→Latin
    # R-F62 (2026-05-09): dropped the n.lower() variant. OpenSanctions
    # match is case-insensitive at the API level, so the lowercase form
    # adds zero discriminating value — but for multi-word inputs like
    # "DD on EDGE Group" → "dd on edge group" it RELIABLY trips the
    # entity-name validator (lowercase non-stopwords), which then logs
    # "rejecting non-entity input" every cycle. Live evidence 2026-05-09
    # 11:18:34 (fly logs). Kept .upper() and .title() because some
    # legitimate-looking corporate variants still differ usefully.
    _add(n.upper())
    _add(n.title())

    # Punctuation strip
    _add(re.sub(r"[^\w\s]", "", n))

    # Acronym expansion
    upper = n.upper().strip()
    if upper in _ACRONYMS:
        _add(_ACRONYMS[upper])

    # If full name contains a known acronym as first word, also try expanded
    parts = n.split()
    if parts and parts[0].upper() in _ACRONYMS:
        expanded = _ACRONYMS[parts[0].upper()] + " " + " ".join(parts[1:])
        _add(expanded.strip())

    # Acronym extraction (strip vowels from each word for orgs)
    # R-F2362: a 2-letter initialism ("MG" from "Modirum Gespi") has no
    # discriminating power — it fuzzy-matches unrelated sanctions names
    # (MG Corp / MG Global / MGフロート) and burns free-tier /match quota.
    # Require >=3 chars so acronym variants are meaningful.
    if len(parts) >= 2 and all(p[:1].isupper() for p in parts):
        acro = "".join(p[0] for p in parts if p)
        if 3 <= len(acro) <= 6:
            _add(acro)

    # Drop common corporate suffixes
    cleaned = re.sub(
        r"\b(ltd|limited|inc|incorporated|llc|gmbh|sa|sarl|plc|pte|"
        r"corporation|corp|company|co|holdings|group|jsc|ojsc|pjsc|pjc)\b\.?",
        "", n, flags=re.IGNORECASE,
    ).strip()
    if cleaned and cleaned != n:
        _add(cleaned)

    # R-F1562: cap variant explosion (already deduped by _add). Priority
    # order is preserved — input, Arabic→Latin, nasab reorderings first.
    return ordered[:_MAX_VARIANTS]


# ── OpenSanctions API ───────────────────────────────────────────────────────

class _SourceQuery(NamedTuple):
    """R-F1696: result of a single OpenSanctions call that distinguishes
    'queried OK, no hits' from 'source unavailable'. The old helpers returned a
    bare list and collapsed EVERY failure (auth/429/5xx/timeout/breaker-open/
    input-rejected) to `[]` — making 'source down' indistinguishable from
    'clean'. A sanctions screen that could not run must NEVER be reported as a
    clearance, so availability has to flow up to the screen + the DD renderer.

      results: normalised raw hits (possibly empty, even when ok=True)
      ok:      True ONLY if a query actually COMPLETED (HTTP 200)
      reason:  'ok' | 'auth' | 'rate_limit' | 'quota_exhausted' |
               'http_<code>' | 'network' | 'breaker_open' | 'input_rejected'

    R-F3528 — 'rate_limit' and 'quota_exhausted' are BOTH HTTP 429 and must stay
    distinct: the first is transient and self-clears, the second is the monthly
    plan allowance and only an operator can clear it. Collapsing them told the
    reader ARIA was going too fast when the truth was that the plan was spent.
    """
    results: list
    ok: bool
    reason: str


def _local_match_to_aria(lm: dict, queried_name: str) -> dict:
    """R-F3529 — canonical-store hit → ARIA's standard match shape.

    The shape is not cosmetic. `is_corroborated_match` (_sanctions_classify.py:344)
    decides whether a match may BLOCK, and it reads two fields:

      * `lists` — checked against _CANONICAL_SANCTIONS_SOURCES slugs. The
        canonical store's own source ids are exactly those slugs (`ofac_sdn`,
        `eu_consolidated`), so they pass unmodified. Get this key wrong and a
        genuine OFAC designation is silently demoted to a "related name
        observation" — a false clean, which is the worst output this tool has.
      * `string_similarity` — a MEASURED-low value excludes a match.

    `match_score` is carried into `string_similarity` because the canonical
    matcher IS a name matcher, and its hit has additionally passed the R-F518
    entity-overlap gate (name + jurisdiction/address/multi-token), which is a
    STRONGER corroboration than string similarity alone. Nothing is invented: the
    score is the one the store computed, and `match_method` travels with it so a
    reader can see how the hit was made.
    """
    src = str(lm.get("source") or "").strip()
    try:
        score = float(lm.get("match_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return {
        "name": lm.get("formatted_name") or lm.get("name") or "",
        "schema": None,
        "lists": [src] if src else [],
        "list": src or "canonical_local",
        "score": round(score, 3),
        "string_similarity": round(score, 3),
        "phonetic_match": False,
        "topics": ["sanction"],
        "countries": lm.get("countries") or [],
        "aliases": [lm["alias_matched"]] if lm.get("alias_matched") else [],
        "relationships": [],
        "url": None,
        "reason": f"local canonical list: {src or 'unknown'}",
        "matched_via_variant": "local_canonical",
        # R-F3529 — provenance the reader needs to know this did NOT come from
        # OpenSanctions, so an auditor can tell which source carried the finding.
        "source_kind": "local_canonical",
        "match_method": lm.get("match_method"),
        "entity_overlap": lm.get("entity_overlap") or [],
        "source_uid": lm.get("source_uid"),
        "queried_name": queried_name,
    }


async def _opensanctions_match(name: str, entity_type: str = "Thing") -> _SourceQuery:
    """Query the OpenSanctions /match endpoint (free, no key required).

    OpenSanctions consolidates OFAC SDN, EU, UK OFSI, UN, Interpol Red Notices,
    PEP databases, and ~200 other lists into a single normalised dataset.
    """
    # F73 fix extension 2026-05-01: live evidence 07:30:05.969 — a chat
    # leaked a prompt fragment ('HIGH-STAKES and you are NOT 100%
    # confident, state what you would need to confirm') into the entity
    # extractor. /search/default already rejects via _looks_like_entity_name
    # but /match/default did NOT — so the bad input burned a free-tier
    # request (1 req/sec quota) before the /search guard caught the
    # follow-on call. Apply the same guard here so neither endpoint
    # wastes quota on prompt-text leaks.
    # R-F311 (2026-05-11): brandify hostname inputs first.
    if name and _DOMAIN_TOKEN_RE.search(name):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(name)
            if brandified and brandified != name:
                name = brandified
        except Exception:
            pass
    if not _looks_like_entity_name(name):
        logger.info(
            "_opensanctions_match: rejecting non-entity input %r "
            "(looks like a prompt fragment / search query, not a name)",
            name[:80],
        )
        return _SourceQuery([], False, "input_rejected")
    # R-F469 (2026-05-14): OpenSanctions circuit breaker. Free-tier is
    # 1 req/sec; under a 429 storm pre-R-F469 every match() call still
    # hit the upstream → quota drained, latency spiked, every screen
    # fired BUT returned empty results silently. The breaker now opens
    # after 3 consecutive failures and cools for 5 min. When OPEN we
    # short-circuit return [] without an HTTP call — caller already
    # treats empty results as "no match", so behaviour from caller's
    # POV is unchanged except faster + no quota burn.
    from .circuit_breaker import get_breaker as _r469_get_breaker
    _r469_breaker = _r469_get_breaker(
        "opensanctions.org",
        failure_threshold=3,
        cooldown_seconds=300,
    )
    if _r469_breaker.is_open():
        _note_breaker_skip(name)   # R-F3500 — counted, never named
        return _SourceQuery([], False, "breaker_open")
    payload = {
        "queries": {
            "q1": {
                "schema": entity_type,
                "properties": {"name": [name]},
            }
        }
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            await _opensanctions_pace()   # R-F3476 — never earn a 429
            resp = await client.post(OPENSANCTIONS_API, json=payload, headers=await _opensanctions_headers())
            if resp.status_code == 401:
                logger.error("OpenSanctions auth failed — check OPENSANCTIONS_API_KEY env var")
                _r469_breaker.record_failure(reason="auth")
                return _SourceQuery([], False, "auth")
            if resp.status_code == 429:
                # R-F3528 — which 429? The body is the only signal.
                _kind = _classify_429(resp.text)
                if _kind == "quota_exhausted":
                    logger.error(
                        "BLOCKED: OpenSanctions MONTHLY PLAN QUOTA exhausted — "
                        "retrying cannot clear this; operator must upgrade the "
                        "plan or wait for the reset. Upstream: %s",
                        (resp.text or "")[:200])
                    await _record_quota_exhausted(resp.text or "")
                else:
                    logger.warning("%s", _rate_limit_message(
                        bool(await _resolve_opensanctions_key()),
                        _OS_KEYED_INTERVAL_S if await _resolve_opensanctions_key()
                        else _OS_FREE_INTERVAL_S))
                # Same breaker either way: R-F1834's exponential backoff is
                # already the right pacing for both, and reason= is what the gap
                # classifier reads.
                _r469_breaker.record_failure(reason=_kind)
                return _SourceQuery([], False, _kind)
            if resp.status_code != 200:
                logger.debug("OpenSanctions match failed: %s %s", resp.status_code, resp.text[:200])
                _r469_breaker.record_failure(reason="server" if resp.status_code >= 500 else "timeout")
                return _SourceQuery([], False, f"http_{resp.status_code}")
            data = resp.json()
            results = (data.get("responses", {}) or {}).get("q1", {}).get("results", [])
            _r469_breaker.record_success()
            # R-F3947 — a 200 PROVES the plan quota is not spent. Retire any
            # exhaustion record here, where the evidence is, rather than waiting
            # for a human to call the operator lever.
            await _note_opensanctions_success()
            return _SourceQuery(results or [], True, "ok")
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions request error: %s", e)
        _r469_breaker.record_failure(reason="timeout")
        return _SourceQuery([], False, "network")


async def _opensanctions_search(query: str, limit: int = 5) -> _SourceQuery:
    """Free-text search against OpenSanctions when /match returns nothing."""
    # F73 fix 2026-04-28: production trace 10:13:40 hit /search/default
    # with `q=HIGH-STAKES and you are NOT 100% confident, state what you
    # would need to confirm — do NOT fabricate verifiable facts...` —
    # a fragment of ARIA's own system prompt extracted by some upstream
    # entity-extraction regex. OpenSanctions returned 400 (query too
    # long / not name-shaped). Same guard as screen_with_aliases now
    # gates the search endpoint too, so the only way bad input reaches
    # OpenSanctions is via direct internal call sites we control.
    # R-F311 (2026-05-11): brandify hostname inputs first.
    if query and _DOMAIN_TOKEN_RE.search(query):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(query)
            if brandified and brandified != query:
                query = brandified
        except Exception:
            pass
    if not _looks_like_entity_name(query):
        logger.info(
            "_opensanctions_search: rejecting non-entity input %r "
            "(looks like a prompt fragment / search query, not a name)",
            query[:80],
        )
        return _SourceQuery([], False, "input_rejected")
    # R-F469: same breaker as match() — single host, single quota pool.
    from .circuit_breaker import get_breaker as _r469_get_breaker
    _r469_breaker = _r469_get_breaker(
        "opensanctions.org",
        failure_threshold=3,
        cooldown_seconds=300,
    )
    if _r469_breaker.is_open():
        _note_breaker_skip(query)  # R-F3500 — counted, never named
        return _SourceQuery([], False, "breaker_open")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            # R-F3476 — search() shares the same upstream quota as match(), so it
            # must share the pacer. Pacing only one path would still earn 429s.
            await _opensanctions_pace()
            resp = await client.get(
                OPENSANCTIONS_SEARCH,
                params={"q": query, "limit": limit},
                headers=await _opensanctions_headers(),
            )
            if resp.status_code == 401:
                logger.error("OpenSanctions auth failed on search — check OPENSANCTIONS_API_KEY")
                _r469_breaker.record_failure(reason="auth")
                return _SourceQuery([], False, "auth")
            if resp.status_code == 429:
                # R-F3528 — which 429? The body is the only signal.
                _kind = _classify_429(resp.text)
                if _kind == "quota_exhausted":
                    logger.error(
                        "BLOCKED: OpenSanctions MONTHLY PLAN QUOTA exhausted — "
                        "retrying cannot clear this; operator must upgrade the "
                        "plan or wait for the reset. Upstream: %s",
                        (resp.text or "")[:200])
                    await _record_quota_exhausted(resp.text or "")
                else:
                    logger.warning("%s", _rate_limit_message(
                        bool(await _resolve_opensanctions_key()),
                        _OS_KEYED_INTERVAL_S if await _resolve_opensanctions_key()
                        else _OS_FREE_INTERVAL_S))
                # Same breaker either way: R-F1834's exponential backoff is
                # already the right pacing for both, and reason= is what the gap
                # classifier reads.
                _r469_breaker.record_failure(reason=_kind)
                return _SourceQuery([], False, _kind)
            if resp.status_code != 200:
                _r469_breaker.record_failure(reason="server" if resp.status_code >= 500 else "timeout")
                return _SourceQuery([], False, f"http_{resp.status_code}")
            data = resp.json()
            _r469_breaker.record_success()
            await _note_opensanctions_success()      # R-F3947 — see above
            return _SourceQuery(data.get("results", []) or [], True, "ok")
    except httpx.HTTPError as e:
        logger.warning("OpenSanctions search error: %s", e)
        _r469_breaker.record_failure(reason="timeout")
        return _SourceQuery([], False, "network")


# ── Public API ──────────────────────────────────────────────────────────────

def _normalise_match(raw: dict, queried_name: str) -> dict:
    """Convert an OpenSanctions hit into ARIA's standard match shape."""
    props = raw.get("properties") or {}
    candidate_name = (props.get("name") or [raw.get("caption", "")])[0]
    sim = _similarity(queried_name, candidate_name)
    phon_match = _metaphone(queried_name) == _metaphone(candidate_name)
    score = float(raw.get("score", 0)) if raw.get("score") else max(sim, 0.7 if phon_match else 0)

    datasets = raw.get("datasets") or []

    # Extract family/associate relationships from OpenSanctions properties.
    # 2026-04-12: these surface inherited risk — if subject's spouse/sibling
    # is sanctioned, the subject inherits elevated risk.
    relationships = []
    for rel_type in ("familyOf", "associateOf", "relatedTo", "spouseOf",
                     "childOf", "parentOf", "siblingOf"):
        rel_targets = props.get(rel_type) or []
        for target in rel_targets[:5]:
            relationships.append({"kind": rel_type, "target": target})

    # R-F335 (2026-05-11): match-path transparency. Operator on the
    # Swisscraft Aviation DD 22:29 saw a HARD_STOP based on a Michele
    # Zagaria SDN match but had no way to verify HOW the query reached
    # Zagaria. Now we capture:
    #   - sdn_entry_id: raw OpenSanctions ID for direct lookup
    #   - match_field: which field on the SDN entry matched
    #     (primary_name / alias / linked_entity / weak_match)
    #   - matched_token: the actual SDN field value that matched
    #   - all_names: every name/alias on the SDN entry for inspection
    sdn_entry_id = raw.get("id") or ""
    all_aliases = (props.get("alias") or [])
    all_names = list(props.get("name") or []) + all_aliases

    # Determine which field on the SDN record best explains the match.
    # The OpenSanctions /match API doesn't tell us directly — infer
    # from string similarity to each candidate string.
    qlower = (queried_name or "").lower().strip()
    match_field = "weak_match"
    matched_token = candidate_name
    if qlower:
        best_sim = 0.0
        for nm in (props.get("name") or []):
            _s = _similarity(queried_name, nm)
            if _s > best_sim:
                best_sim = _s
                matched_token = nm
                match_field = "primary_name"
        for al in all_aliases:
            _s = _similarity(queried_name, al)
            if _s > best_sim:
                best_sim = _s
                matched_token = al
                match_field = "alias"

    # Human-readable match path for chat / dashboard.
    match_path = (
        f"query='{queried_name}' → score={round(score, 3)} → "
        f"matched_field={match_field}='{matched_token}' → "
        f"sdn_id={sdn_entry_id or 'unknown'} "
        f"({datasets[0] if datasets else 'OpenSanctions'})"
    )

    return {
        "name": candidate_name,
        "schema": raw.get("schema"),
        "lists": datasets,
        "list": datasets[0] if datasets else "OpenSanctions",
        "score": round(score, 3),
        "string_similarity": round(sim, 3),
        "phonetic_match": phon_match,
        "topics": props.get("topics") or [],
        "countries": props.get("country") or [],
        "aliases": (props.get("alias") or [])[:5],
        "relationships": relationships,
        "first_seen": raw.get("first_seen"),
        "last_change": raw.get("last_change"),
        "url": f"https://www.opensanctions.org/entities/{raw.get('id', '')}/" if raw.get("id") else None,
        "reason": "; ".join(datasets[:3]) if datasets else "OpenSanctions match",
        # R-F1542: jurisdiction tags — maps dataset slugs to human-readable
        # jurisdiction labels so consumers can say "matched on US OFAC SDN"
        # vs "matched on UK OFSI". Prevents the Hikvision failure where a
        # US OFAC hit was misrepresented as UK-sanctioned.
        "jurisdictions": _resolve_jurisdictions(datasets),
        # R-F335 match-path transparency fields
        "sdn_entry_id": sdn_entry_id,
        "match_field": match_field,
        "matched_token": matched_token,
        "match_path": match_path,
        "all_names_on_sdn": all_names[:10],
    }


# R-F3945 — once-per-process latch for the coverage-degraded announcement.
# Same shape as fallback._RULE_ONE_BREACH_ANNOUNCED, and for the same reason:
# a standing platform condition must be said ONCE, not once per request.
_COVERAGE_DEGRADED_ANNOUNCED = False


def _canonical_floor_sources() -> list[str]:
    """R-F3945 — the source ids the LOCAL canonical store actually holds.

    Read from `sanctions_canonical.lookup._expected_sources()`, which derives
    the list from the loader modules' own `SOURCE_ID` (R-F2373). Deliberately
    NOT a literal here: a hardcoded copy is the hand-maintained list that rots
    the moment a third loader is added, and it would rot SILENTLY toward the
    unsafe answer (claiming coverage we no longer have).

    Returns [] when the registry cannot be determined — which
    `unavailable_sources_for()` reads as "nothing is known to be covered", i.e.
    it fails CLOSED. An undeterminable registry must never render as full
    coverage.
    """
    try:
        from .sanctions_canonical.lookup import _expected_sources
        return list(_expected_sources() or [])
    except Exception as e:      # pragma: no cover - defensive
        logger.debug("R-F3945 could not read the canonical loader registry: %s", e)
        return []


@fail_wire(module="sanctions", gap_type="source_failure")
async def fuzzy_screen(name: str, *, threshold: float = 0.78) -> dict:
    """Comprehensive fuzzy sanctions screen for a single entity name.

    Steps:
      1. Generate name variants (transliteration, acronym, suffix-strip)
      2. Query OpenSanctions /match for each variant
      3. Fall back to /search free-text if /match yields nothing
      4. Score each candidate by string similarity + phonetic + dataset count
      5. Return matches above threshold + suggestions for manual review

    Args:
        name: Entity name to screen.
        threshold: Minimum confidence (0..1) for "blocking" matches.
    """
    name = (name or "").strip()
    if not name or len(name) < 2:
        return {"name": name, "error": "name too short", "matches": [], "blocked": False}

    variants = _generate_variants(name)
    seen_ids: set[str] = set()
    all_matches: list[dict] = []
    # R-F1696: track whether ANY source query actually completed. If every
    # variant + the free-text fallback failed to reach OpenSanctions, an empty
    # result is NOT "clean" — it's an UNPERFORMED screen.
    source_ok = False
    source_reasons: list[str] = []

    for variant in variants[:6]:  # cap to avoid API hammering
        try:
            _q = await _opensanctions_match(variant)
            raw_results = _q.results
            source_ok = source_ok or _q.ok
            if not _q.ok:
                source_reasons.append(_q.reason)
        except Exception as e:
            logger.warning("OpenSanctions match crashed on '%s': %s", variant, e)
            raw_results = []
            source_reasons.append(f"crash:{type(e).__name__}")
        for r in raw_results:
            rid = r.get("id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            normalised = _normalise_match(r, name)
            normalised["matched_via_variant"] = variant
            all_matches.append(normalised)

    # If no /match hits, try free-text /search as a backup
    if not all_matches:
        try:
            _qs = await _opensanctions_search(name, limit=8)
            search_results = _qs.results
            source_ok = source_ok or _qs.ok
            if not _qs.ok:
                source_reasons.append(_qs.reason)
        except Exception as e:
            logger.warning("OpenSanctions search crashed: %s", e)
            search_results = []
            source_reasons.append(f"crash:{type(e).__name__}")
        for r in search_results:
            rid = r.get("id")
            if rid and rid in seen_ids:
                continue
            if rid:
                seen_ids.add(rid)
            normalised = _normalise_match(r, name)
            normalised["matched_via_variant"] = "free_text_search"
            # Free-text matches are lower-confidence by default
            normalised["score"] = round(normalised["score"] * 0.85, 3)
            all_matches.append(normalised)

    # (converter lives at module level — see _local_match_to_aria)
    # ── R-F3529: fall back to the LOCAL canonical lists ──────────────────────
    # Only when OpenSanctions did not answer. Verified live 2026-07-30: the
    # OpenSanctions monthly plan quota is spent, so this whole path returns
    # source_unavailable — while prod's canonical store answers correctly on the
    # same box (POST /compliance/screen "Islamic Revolutionary Guard Corps" →
    # BLOCKED, matched against ofac_sdn AND eu_consolidated).
    #
    # A metered aggregator taking down due-diligence screening while the FREE,
    # AUTHORITATIVE primary lists sit loaded next to it is backwards: §6 puts the
    # burden of proof on any third-party, and §15 is pay-once-remember-forever.
    # OpenSanctions stays PRIMARY (broader coverage, ~200 lists); this is the
    # floor beneath it, not a replacement.
    #
    # NEVER-FALSE-CLEAN, the property that governs everything here:
    # check_sanctions is used precisely because it REFUSES to invent a clean —
    # an empty store, partial source coverage or stale data all return
    # INSUFFICIENT_DATA + store_unavailable (lookup.py:486-556), never CLEAR. So
    # `screened` is only set when the local store genuinely answered. If both
    # sources are down, source_unavailable stands and the screen stays UNVERIFIED.
    # Turning "unavailable" into "clean" is the one outcome a compliance tool must
    # never produce, and it is what this fallback is written to avoid, not risk.
    # R-F3945 — coverage provenance. Set BEFORE the fallback so the healthy
    # path keeps its historical meaning (a real aggregate screen covers every
    # canonical source) and only the floor narrows it.
    _coverage_mode = "opensanctions_aggregate" if source_ok else "none"
    # R-F3958 (C-48) — a canonical REVIEW / HARD_STOP verdict that produced no
    # ARIA-shaped matches used to vanish here. See the carry block below.
    _review_verdict = ""
    _review_reason = ""
    _near_miss_observations: list[dict] = []

    if not source_ok:
        try:
            from .sanctions_canonical import lookup as _canon
            _local = await asyncio.to_thread(_canon.check_sanctions, name)
            _verdict = str((_local or {}).get("verdict") or "")
            if _verdict and _verdict != "INSUFFICIENT_DATA":
                # The local store ANSWERED. That is a performed screen.
                source_ok = True
                # R-F3945 — ...but a NARROWER one, and that is the whole point.
                # The floor holds the loader registry only (ofac_sdn +
                # eu_consolidated); OpenSanctions aggregates ~200 lists. Record
                # WHICH sources actually answered so `derive_verified_sources`
                # cannot go on stamping the other eight CLEAN.
                _coverage_mode = "local_canonical_floor"
                source_reasons.append("opensanctions_unavailable_local_used")
                for lm in (_local.get("matches") or []):
                    all_matches.append(_local_match_to_aria(lm, name))
                # ── R-F3958 (C-48) — carry the VERDICT, not just the matches ──
                # R-F3691's `gate_blocked_near_miss` is the textbook REVIEW
                # case: a name-overlapping designation that could not be
                # corroborated, so a human decides. Its candidates are in
                # `gate_blocked` and by construction NOT in `matches`, so
                # reading `matches` alone dropped the verdict entirely and the
                # screen rendered as a clean bill. `company_investigator`
                # routes REVIEW to UNVERIFIED correctly; the DD path never saw
                # it.
                if _verdict in ("REVIEW", "HARD_STOP"):
                    _review_verdict = _verdict
                    _review_reason = str((_local or {}).get("reason") or "")
                    _gb = _local.get("gate_blocked")
                    if isinstance(_gb, list):
                        for _cand in _gb[:10]:
                            if isinstance(_cand, dict):
                                _near_miss_observations.append(
                                    _local_match_to_aria(_cand, name))
                logger.info(
                    "R-F3529: OpenSanctions unavailable (%s) — screened '%s' "
                    "against local canonical lists instead: verdict=%s matches=%d",
                    ",".join(sorted(set(source_reasons))[:3]), name[:60],
                    _verdict, len(_local.get("matches") or []))
            else:
                # Local store could not answer either. Record WHY, so the
                # obstacle line can name both failures rather than one.
                source_reasons.append(
                    f"local_{(_local or {}).get('reason') or 'insufficient_data'}")
        except Exception as _le:
            logger.warning("R-F3529 local canonical fallback failed: %s", _le)
            source_reasons.append(f"local_crash:{type(_le).__name__}")

    # Rank
    all_matches.sort(key=lambda m: -m["score"])
    top_score = all_matches[0]["score"] if all_matches else 0.0
    # R-F2840 — a BLOCKING verdict must be CORROBORATED, not merely scored.
    # This read `score >= threshold` alone and never consulted string_similarity,
    # topics or matched_via_variant — all present on the match. Live consequence
    # (dd_06bdbcaaa866, SOCAR Trading SA): `blocked: True` on 8 matches, five of them
    # collisions on the ACRONYM "STS" derived from the query, one at string
    # similarity 0.000 scoring 0.74. Matches both name-similar AND sanctioned: ZERO.
    # A `score` of 1.0 can come from an exact hit on a two-letter ALIAS; similarity is
    # what says the SUBJECT matched. is_corroborated_match() is SHARED with the
    # verified_sources HIT logic so the two verdicts cannot drift apart again.
    #
    # SAFETY: this narrows the BLOCKING set only. Every match stays in `matches`,
    # stays counted in `match_count`, and `screened` / source-availability semantics
    # are untouched. blocked=False is NOT "clean" — it means nothing corroborated a
    # block, which is a different statement and is reported as such.
    from ._sanctions_classify import is_corroborated_match
    blocking_matches = [
        m for m in all_matches
        if m["score"] >= threshold and is_corroborated_match(m)
    ]
    # Keep the discarded observations VISIBLE — an analyst may still want the
    # related-name cluster; it just is not a designation.
    related_name_observations = [
        m for m in all_matches
        if m["score"] >= threshold and not is_corroborated_match(m)
    ]
    # R-F3958 (C-48) — the near-miss belongs in this channel, which R-F2840
    # already documents as "reported, never blocking, never clean". A flag with
    # no evidence behind it cannot be actioned by an analyst, so the blocked
    # candidate travels with the verdict.
    if _near_miss_observations:
        for _obs in _near_miss_observations:
            _obs["near_miss_gate_blocked"] = True
        related_name_observations = related_name_observations + _near_miss_observations

    # R-F1696: a screen is only "performed" if the source actually answered
    # (matches found ⇒ it answered; or at least one call returned HTTP 200).
    # No matches AND no successful call ⇒ source unavailable ⇒ NOT clean.
    screened = bool(all_matches) or source_ok
    source_unavailable = not screened

    result = {
        "name": name,
        "variants_tried": variants[:6],
        "matches": all_matches[:10],
        "blocking_matches": blocking_matches,
        # R-F2840 — scored above threshold but NOT corroborated (acronym/alias
        # collisions and the like). Reported, never blocking, never "clean".
        "related_name_observations": related_name_observations,
        "related_name_count": len(related_name_observations),
        "match_count": len(all_matches),
        "top_score": top_score,
        "blocked": len(blocking_matches) > 0,
        "screened": screened,  # R-F1696
        # ── R-F3945 — WHAT was actually consulted. ───────────────────────────
        # ALWAYS present, including on the healthy path. A provenance block that
        # appeared only when something broke would repeat the `source_reasons`
        # trap this fix exists to close: the dangerous case here is the screen
        # that SUCCEEDED against a narrower source set than the verdict implies,
        # and a key that is absent on success cannot describe it.
        #
        #   opensanctions_aggregate → the ~200-list aggregate answered; every
        #                             canonical source is covered (historical
        #                             meaning, unchanged).
        #   local_canonical_floor   → R-F3529 served; ONLY the loader registry.
        #   none                    → nothing answered; screened is False.
        "coverage": {
            "mode": _coverage_mode,
            "sources_consulted": (
                _canonical_floor_sources()
                if _coverage_mode == "local_canonical_floor" else []
            ),
        },
        # R-F3019 — WHEN the screen ran. A compliance reader cannot rely on
        # "clean" without a date: sanctions lists change daily, so an undated
        # clean is an undatable claim. No field carried this before, so reports
        # named neither the lists nor the moment they were checked.
        "screened_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "threshold": threshold,
        "disclaimer": (
            "Pre-screen only. Blocking matches require manual verification against "
            "primary sanctions sources (OFAC SDN, OFSI, EU Consolidated, UN SC). "
            "OpenSanctions is updated daily but may lag designations by 24-48 hours."
        ),
    }
    # ── R-F3958 (C-48) — REVIEW is a THIRD state and must render as one. ──
    # `blocked` deliberately stays False: a gate-blocked candidate is not a
    # corroborated designation, and promoting it to a block would trade a false
    # clean for a false hit — the exact swap R-F2840 narrowed the blocking set
    # to avoid. So the verdict rides on its own flag, which a caller rendering
    # a clean bill has to step over explicitly.
    if _review_verdict and screened:
        result["requires_human_review"] = True
        result["review_verdict"] = _review_verdict
        result["review_reason"] = _review_reason or "canonical_review_verdict"
        result["review_detail"] = (
            f"The canonical sanctions store returned {_review_verdict} for this "
            f"name ({_review_reason or 'no reason given'}). "
            f"{len(_near_miss_observations)} name-overlapping designation(s) were "
            f"found but could not be corroborated by jurisdiction or address, so "
            f"this screen is NOT a clearance — a human must adjudicate it."
        )

    if source_unavailable:
        # R-F1696: surface loudly so callers/the DD renderer NEVER treat an
        # unscreenable entity as a clearance. `error` makes downstream
        # screen-succeeded checks fail closed.
        result["source_unavailable"] = True
        result["error"] = "sanctions_source_unavailable"
        result["source_reasons"] = sorted(set(source_reasons))[:6]

    # ── R-F3945 §21a — the DEGRADED-BUT-SUCCESSFUL screen is a third outcome.
    # It reaches neither branch below: `source_unavailable` is False (a screen
    # DID run) and the success branch reports it as an ordinary clean. That gap
    # is exactly how eight unsearched lists read CLEAN for 13 days with nothing
    # saying so.
    #
    # ANNOUNCE-ONCE, deliberately. While OpenSanctions' quota is spent EVERY
    # screen is degraded, so a per-screen gap would be a self-sustaining flood
    # that evicts real defects from the 500-slot ledger — the failure this repo
    # is already living with elsewhere. The condition is a standing platform
    # state, not a per-entity event; the per-entity fact is carried in the
    # report's own verified_sources table, where the reader needs it.
    global _COVERAGE_DEGRADED_ANNOUNCED
    if screened and _coverage_mode == "local_canonical_floor" and \
            not _COVERAGE_DEGRADED_ANNOUNCED:
        _COVERAGE_DEGRADED_ANNOUNCED = True
        logger.warning(
            "[R-F3945] sanctions coverage DEGRADED — OpenSanctions is not "
            "answering, so screens run against the local canonical floor (%s) "
            "only. Every other canonical list now reports UNAVAILABLE rather "
            "than CLEAN. This is correct, and it is a COVERAGE LOSS: restore "
            "OpenSanctions to regain the ~200-list breadth.",
            ", ".join(_canonical_floor_sources()) or "none",
        )
        try:
            wire_failure(
                module="sanctions",
                detail=(
                    "Sanctions coverage degraded to the local canonical floor "
                    f"({', '.join(_canonical_floor_sources()) or 'none'}) — "
                    "OpenSanctions unavailable. Screens still run; breadth is "
                    "reduced and unsearched lists now report UNAVAILABLE."
                ),
                gap_type="sanctions_coverage_degraded",
                source="sanctions:fuzzy_screen",
            )
        except Exception as _wc:
            logger.debug("R-F3945 coverage wire failed: %s", _wc)

    # ── Brain hook (§21a): feed BOTH outcomes — never let a failed screen be
    #    indistinguishable from a clean one. ──
    if source_unavailable:
        try:
            wire_failure(
                module="sanctions",
                detail=(
                    f"Sanctions screen '{name}' NOT performed — OpenSanctions "
                    f"unreachable ({', '.join(sorted(set(source_reasons))[:4]) or 'unknown'}). "
                    f"Result is UNVERIFIED, not clear."
                ),
                gap_type="sanctions_source_unavailable",
                source="sanctions:fuzzy_screen",
            )
        except Exception as _wf:
            logger.debug("sanctions wire_failure failed: %s", _wf)
    else:
        try:
            from . import brain_hook
            await brain_hook.absorb(
                module="sanctions",
                summary=f"Sanctions screen '{name}': {len(all_matches)} matches, top_score={top_score:.2f}, blocked={result['blocked']}, screened=True",
                entity_name=name,
                success=True,
                confidence="PROBABLE",
            )
        except Exception as _bh:
            logger.debug("sanctions brain_hook failed: %s", _bh)

    return result


@fail_wire(module="sanctions", gap_type="source_failure")
async def enrich_with_relationships(screen_result: dict, *, max_targets: int = 3,
                                    target_threshold: float = 0.78) -> dict:
    """Extend a fuzzy_screen() result with relationship-risk enrichment.

    For each of the top matches, walks up to `max_targets` family/associate
    relationship targets and checks whether each target is itself on a
    sanctions list. If so, attaches an `inherited_risk` block so ARIA can
    cite it as "subject's spouse/sibling/associate is sanctioned".

    Rate-limit aware: caps at `max_targets` relationships per match so a
    subject with 30 associates doesn't hammer the OpenSanctions free tier
    (1 req/sec). Caller should prefer paying for a key in production.

    Non-destructive: mutates nothing, returns a new dict with
    `relationships_enriched=True` and `inherited_risk_count` summary.
    """
    if not isinstance(screen_result, dict) or not screen_result.get("matches"):
        return screen_result
    enriched_matches: list[dict] = []
    seen_targets: set[str] = set()
    inherited_total = 0
    for m in screen_result.get("matches", []):
        m = dict(m)  # shallow copy
        rels = m.get("relationships") or []
        inherited: list[dict] = []
        for rel in rels[:max_targets]:
            target_name = (rel.get("target") or "").strip()
            if not target_name or len(target_name) < 3:
                continue
            target_key = target_name.lower()
            if target_key in seen_targets:
                continue
            seen_targets.add(target_key)
            try:
                # Free-text search is the cheapest path; /match would need a
                # schema choice we don't have for a raw name.
                hits = (await _opensanctions_search(target_name, limit=2)).results
            except Exception:
                continue
            target_hit = None
            for h in hits or []:
                candidate = (((h.get("properties") or {}).get("name") or [h.get("caption", "")])[0])
                sim = _similarity(target_name, candidate)
                if sim >= target_threshold:
                    target_hit = h
                    target_hit["_sim"] = sim
                    break
            if not target_hit:
                continue
            datasets = target_hit.get("datasets") or []
            props = target_hit.get("properties") or {}
            topics = props.get("topics") or []
            inherited.append({
                "kind": rel.get("kind"),
                "target_name": target_name,
                "target_lists": datasets,
                "target_topics": topics,
                "target_score": round(float(target_hit.get("_sim", 0.0)), 3),
                "target_url": (
                    f"https://www.opensanctions.org/entities/{target_hit.get('id','')}/"
                    if target_hit.get("id") else None
                ),
            })
        if inherited:
            m["inherited_risk"] = inherited
            inherited_total += len(inherited)
        m["relationships_enriched"] = True
        enriched_matches.append(m)
    out = dict(screen_result)
    out["matches"] = enriched_matches
    out["inherited_risk_count"] = inherited_total
    out["relationships_enriched"] = True
    if inherited_total and not out.get("blocked"):
        # Inherited risk never auto-blocks, but it DOES escalate the
        # suggestion tier. The DD layer decides whether to hard-stop.
        out["inherited_risk_note"] = (
            f"{inherited_total} relationship(s) match an OpenSanctions entry — "
            "subject inherits elevated risk. Review relationship targets manually."
        )
    return out


@fail_wire(module="sanctions", gap_type="source_failure")
async def screen_with_relationships(name: str, *, threshold: float = 0.78,
                                    max_rel_targets: int = 3) -> dict:
    """Convenience wrapper: fuzzy_screen() + enrich_with_relationships().

    Use when the caller actively cares about family/associate risk
    inheritance (person DD, beneficial-ownership screens). For plain
    tender-counterparty checks, fuzzy_screen() without enrichment is fine.
    """
    screen = await fuzzy_screen(name, threshold=threshold)
    return await enrich_with_relationships(screen, max_targets=max_rel_targets,
                                           target_threshold=threshold)


# R-F3166 — legal-form suffixes, which are conventionally written LOWERCASE.
#
# THE DEFECT (live, Babcock dd_ea78770813cc): `_looks_like_entity_name` requires every
# non-stopword token to start uppercase (the F39 sentence-fragment guard). "plc" is
# lowercase and is not a stopword, so:
#
#   _looks_like_entity_name("Babcock International Group plc")  -> False
#   _looks_like_entity_name("Babcock International Group PLC")  -> True
#
# `screen_with_aliases` returns `{"error": "not_entity_shaped"}` on that, so THE ENTIRE
# SANCTIONS SCREEN WAS SKIPPED and all ten primary sources recorded
# `status=UNAVAILABLE, via=screen_failed`. The readiness scorecard then held
# "Sanctions and export-control exposure" UNRESOLVED — correctly, but for a reason no
# reader could guess, and on every UK public company written the way it writes itself
# ("Babcock International Group plc" is the title of its own annual report). QinetiQ
# Group plc and Mitie Group plc were rejected identically.
#
# A compliance product silently declining to screen a whole class of counterparties is
# the most serious failure mode available to it. The fragment guard stays — it is
# doing real work — but a legal form is not a lowercase common noun.
#
# Terminal-position only (last two tokens), so the guard keeps its strength mid-string:
# suffixes are terminal by convention ("… Pte Ltd", "… Sdn Bhd", "… GmbH & Co KG"), and
# exempting short word-like forms ("as", "co", "dd") anywhere would let real fragments
# through.
_ENTITY_LEGAL_FORMS = frozenset({
    # UK / IE
    "plc", "ltd", "limited", "llp", "lp", "cic", "ug",
    # US / CA
    "inc", "corp", "corporation", "llc", "co", "lllp", "pc",
    # DE / AT / CH
    "gmbh", "ag", "kg", "ohg", "kgaa", "eg", "se",
    # NL / BE
    "bv", "nv", "cv", "vof", "bvba",
    # FR / ES / IT / PT / LATAM
    "sa", "sas", "sarl", "sca", "srl", "spa", "sl", "slu", "sapi", "lda", "eirl",
    # Nordics
    "oy", "oyj", "ab", "asa", "aps", "hf", "ehf",
    # CEE / CIS
    "zrt", "kft", "doo", "dd", "ad", "ood", "eood", "sro", "sp", "zoo",
    "ooo", "oao", "zao", "pao", "jsc", "ojsc", "cjsc", "pjsc", "tov",
    # Asia / MENA
    "pte", "pty", "sdn", "bhd", "kk", "kabushiki", "fzco", "fze", "llc-fz", "wll", "qsc",
})

_ENTITY_STOPWORDS = frozenset({
    "the", "of", "and", "or", "in", "on", "at", "to", "for", "with",
    "by", "from", "as", "is", "was", "are", "were", "be", "been", "being",
    "before", "after", "during", "since", "until", "while", "any", "all",
    "current", "former", "new", "old", "this", "that", "these", "those",
    "independently", "directly", "globally", "weekly", "daily", "monthly",
    "summary", "update", "review", "analysis", "report", "intelligence",
    "without", "with", "via", "without", "across", "between", "among",
})

# Domain-name-looking tokens (".gov", ".com", "site.gov", etc.) inside an
# input is a strong signal it's a search query / URL fragment, not a name.
_DOMAIN_TOKEN_RE = re.compile(r"\.(?:gov|com|org|net|edu|co|io|ai|mil)\b", re.IGNORECASE)


# R-F49 (2026-05-09): denylist of export-control regimes, compliance
# acronyms, and generic concepts that are NOT entity names but pass the
# all-caps shape heuristic. Past leak (memory session_2026_05_06.md):
# 'ITAR' got pushed to OpenSanctions because its shape is identical to
# 'BAE' — short, all-caps. The defensive guard logged "rejecting" but
# only after the wasted quota call. This denylist short-circuits before
# any other checks, and is the cheapest mitigation for R-F37.
_NON_ENTITY_DENYLIST = frozenset({
    # Export-control regimes
    "itar", "ear", "eccn", "euc", "spire", "sitcl", "ofac", "ofsi",
    "ncnt", "wassenaar", "mtcr", "nsg", "ag", "cwc", "att", "ccl",
    # Sanctions list short-names
    "sdn", "consolidated", "sema", "dfat", "seco", "unsc", "scsanc",
    # Compliance / DD generic concepts
    "kyc", "aml", "ubo", "pep", "edd", "cdd", "cft", "mlro",
    # ARIA-internal acronyms surfaced through prompt fragments
    "dd", "rfq", "rfp", "rfi", "moq", "mou", "lol", "loi", "nda",
    # Geopolitical generic shorthand (not a real entity)
    "nato", "eu", "un", "asean", "ecowas", "sadc", "gcc", "mercosur",
    "cplp", "au", "oas", "osce",
})


def _looks_like_entity_name(s: str) -> bool:
    """Reject inputs that don't look like an entity name before they
    waste OpenSanctions API quota.

    Live failure 2026-04-27 18:24:50: tasks.yaml passes search-query
    strings as `entity:` to deep_research, which forwarded them here.
    Strings like 'sanctions update OFAC SDN EU UN Security Council
    embargo 2026' or 'Arkmurus weekly intelligence summary Angola
    Mozambique...' generated 80+ /match POSTs each, all returning junk.

    R-F49 (2026-05-09): added _NON_ENTITY_DENYLIST short-circuit for
    export-control regime / compliance-concept acronyms (ITAR, OFAC,
    EUC, etc.) that pass the all-caps shape heuristic but are never
    sanctions-screenable entities.

    Heuristics — entity names are typically:
      - NOT in the regime/concept denylist
      - 2-100 chars
      - <= 7 words (covers 'Sheikh Hamad bin Khalifa Al Thani II'
        and 'Krasnoyarsk Aluminum Smelter Open Joint-Stock Company';
        rejects 'GAMI current leadership independently before any
        formal outre')
      - No commas, question marks, exclamations
      - Don't end with a 4-digit year (search query smell)
      - Contain at most 1 common English stop-word (entity names like
        'Bank of America' have one; descriptions have many)
    """
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 2 or len(s) > 100:
        return False
    # R-F49: regime / concept acronym denylist BEFORE other heuristics.
    # Single-token inputs like 'ITAR' or 'OFAC' are checked here; multi-
    # word combinations that contain a denylist token as their entirety
    # ('Cite OFAC' would pass; 'OFAC' alone fails) are rejected.
    if s.lower() in _NON_ENTITY_DENYLIST:
        return False
    if "," in s or "?" in s or "!" in s:
        return False
    words = s.split()
    if len(words) > 7:
        return False
    # Year token anywhere is a search-query smell. Real entity names
    # rarely contain bare 4-digit years (model designators like 'Boeing
    # 747' are 3 digits; 'AK-47' has the year embedded but no space).
    if re.search(r"\b(19|20)\d{2}\b", s):
        return False
    if _DOMAIN_TOKEN_RE.search(s):
        return False
    # F39 fix 2026-04-27: previous threshold (>1 stopword) let through
    # 4-word fragments like "Iran nexus before engagement" and 6-word
    # ones like "Iran is openly signalling it will". Word count alone
    # can't distinguish "Bank of America Corp" (valid, 4 words, 1 stop)
    # from "Iran of foreign policy" (fragment, 4 words, 1 stop) — but
    # CASE can: real entity names are proper nouns (uppercase initial),
    # fragments have lowercase common nouns mixed in.
    stopword_count = sum(1 for w in words if w.lower() in _ENTITY_STOPWORDS)
    if stopword_count > 1:
        return False
    # Multi-word inputs: every non-stopword word must start with an
    # uppercase letter or digit ("F-35", "T-90"). One lowercase non-
    # stopword word means it's almost certainly a sentence fragment
    # (Iran NEXUS before ENGAGEMENT — nexus / engagement are lowercase
    # common nouns; Bank OF America CORP — of stopword, others all upper).
    if len(words) > 1:
        for _i, w in enumerate(words):
            if w.lower() in _ENTITY_STOPWORDS:
                continue
            # R-F3166 — a TERMINAL legal-form suffix is not a lowercase common noun.
            # Stripping punctuation catches "S.A.", "Co.", "plc." as written in the
            # wild. Terminal-only (last two tokens) keeps the fragment guard intact
            # mid-string, since suffixes are terminal by convention.
            if (_i >= len(words) - 2
                    and w.lower().strip(".,()") in _ENTITY_LEGAL_FORMS):
                continue
            # R-F3218 — an OPENING BRACKET is punctuation, not a lowercase common
            # noun. "Rossi Security (Rossi Facility Services Ltd)" failed the case
            # test on the token "(Rossi" — `"("[0].isupper()` is False — so the
            # whole screen was declined for a perfectly ordinary way of writing
            # "trading name (registered name)". Strip leading punctuation before
            # the case test; the fragment guard is unchanged for real words.
            _w = w.lstrip("([{\"'“‘")
            if not _w:
                continue          # a bare bracket carries no case signal
            if not _w[0].isupper() and not _w[0].isdigit():
                return False
    return True


# R-F3218 — "Trading Name (Registered Name Ltd)" is how customers actually type a
# subject, and the parenthetical is usually the MORE screenable of the two (it is
# the registered legal name). Screening the composite string as one name matches
# nothing on any list, so split it and screen both.
_BRACKETED_SEGMENT_RE = re.compile(r"[\(\[]([^)\]]{2,80})[\)\]]")


def split_bracketed_name(s: str) -> tuple[str, list[str]]:
    """('Rossi Security (Rossi Facility Services Ltd)')
        -> ('Rossi Security', ['Rossi Facility Services Ltd'])

    Returns (outer, [inner, ...]). Returns (s, []) when there is nothing to split.
    """
    if not s or not isinstance(s, str) or "(" not in s and "[" not in s:
        return (s or "", [])
    inner = [m.strip() for m in _BRACKETED_SEGMENT_RE.findall(s) if m.strip()]
    outer = _BRACKETED_SEGMENT_RE.sub(" ", s)
    outer = re.sub(r"\s+", " ", outer).strip(" -–—,")
    return (outer or s.strip(), inner)


# ── R-F3228 — WHERE THE NAME CAME FROM DECIDES WHETHER TO SECOND-GUESS IT ───
#
# `_looks_like_entity_name` is a SEARCH-QUERY heuristic. It exists (F1/F2,
# 2026-04-27) because tasks.yaml passed strings like "sanctions update OFAC SDN
# EU UN Security Council embargo 2026" as `entity:`, burning 80+ OpenSanctions
# calls per cycle on prose. That is a real problem and the guard solves it.
#
# It was never designed to arbitrate whether a REGISTERED COMPANY NAME is real,
# and used that way it is wrong often enough to matter. Measured against 32 live
# UK company names it rejected SIX:
#
#   Marks & Spencer Group plc      Smith & Nephew plc      Tate & Lyle PLC
#   Compagnie de Saint-Gobain      A.G. Barr p.l.c.        Rossi Security, Ltd
#
# — the "&" token, lowercase particles (de/van/der), punctuated legal forms, and
# any comma. This is the THIRD class of legitimate name it has silently refused
# to screen (R-F3166 lowercase 'plc', R-F3218 brackets, now these), and each fix
# so far taught it one more character. A fourth patch would be the same mistake:
# the guard is not too narrow, it is being asked the wrong question.
#
# A DD subject name arrives from the operator or from Companies House. It is not
# a search query and nothing is saved by doubting it — the quota argument does
# not apply to one screen of one named counterparty. So the CALLER declares the
# provenance, and only untrusted provenance gets the heuristic.
#
# Default is `free_text`: every existing caller keeps today's behaviour, and a
# path must opt IN to being trusted. Trusted still enforces the cheap sanity
# rules that protect the quota (length, and the R-F49 denylist that stops "ITAR"
# or "OFAC" being screened as if they were entities).
_TRUSTED_NAME_SOURCES = frozenset({"dd_subject", "registry", "operator"})


def _screenable(s: str, *, trusted: bool) -> bool:
    """May `s` be sent to the sanctions source?"""
    if not s or not isinstance(s, str):
        return False
    s = s.strip()
    if len(s) < 2 or len(s) > 100:
        return False
    if s.lower() in _NON_ENTITY_DENYLIST:
        return False
    if not trusted:
        return _looks_like_entity_name(s)
    # A trusted name still must not be a sentence — a caller that hands us prose
    # under a trusted label is a bug, and 12 words is prose by any reading.
    return len(s.split()) <= 12


@fail_wire(module="sanctions", gap_type="source_failure")
async def screen_with_aliases(name: str, known_aliases: list[str] | None = None,
                              *, source: str = "free_text") -> dict:
    """Screen a primary name plus user-provided aliases. Combines results.

    Use this when you already know an entity has aliases (e.g. ship name + IMO,
    company name + former name) — passes each through the full fuzzy pipeline
    and returns the worst-case (highest-scoring) hit.

    `source` declares where the name came from (R-F3228). Anything in
    `_TRUSTED_NAME_SOURCES` — an operator-supplied or registry-resolved subject —
    skips the search-query shape heuristic, which rejects ordinary company names
    like "Marks & Spencer Group plc". Leave it at the default for free text.
    """
    _trusted = source in _TRUSTED_NAME_SOURCES
    # R-F311 (2026-05-11): when the operator-supplied name is a hostname
    # ("modirumgespi.com"), brandify it BEFORE the entity-shape gate.
    # The 21:11 live DD on modirumgespi.com had the screen rejected
    # twice because `.com` triggered _DOMAIN_TOKEN_RE — the "CLEAN ✅"
    # verdict in chat output was hollow. Now we brandify the hostname
    # and proceed; the original hostname is added as an alias so the
    # screen also probes the literal string.
    original_name = name
    # R-F434 (2026-05-13): track whether the input was a hostname and whether
    # any legal-name corroboration was supplied via known_aliases. Brandified
    # hostnames produce false-positive HARD STOPs when the stripped stem
    # collides by string similarity with an unrelated SDN entry (live
    # examples this conversation: ngast.com → "Oscar Noe MEDINA GONZALEZ",
    # armesavn.com → "SHAZAND PETROCHEMICAL"). Until the orchestrator
    # re-screens with a verified legal name from the crawl/registry, any
    # match derived from the brandified stem alone must be capped at
    # AMBER by the classifier — operator preserves the signal without
    # the false HARD STOP. The original hostname is NOT corroboration
    # because we appended it ourselves below.
    _from_brandified_hostname = False
    _brandified_stem: str = ""
    # known_aliases AT ENTRY = caller-supplied legal-name corroboration.
    # Captured before we mutate the list with the original hostname.
    _has_caller_supplied_aliases = bool(known_aliases)
    if name and _DOMAIN_TOKEN_RE.search(name):
        try:
            from .web_explorer import brandify_query as _brand
            brandified = _brand(name)
            if brandified and brandified != name:
                logger.info(
                    "R-F311: screen_with_aliases brandified hostname %r → %r",
                    name[:60], brandified[:60],
                )
                # Add original hostname as alias so we also probe the
                # literal — sometimes the registrant or operating-name
                # field on a sanctions list is the domain.
                known_aliases = list(known_aliases or [])
                if original_name not in known_aliases:
                    known_aliases.append(original_name)
                name = brandified
                _from_brandified_hostname = True
                _brandified_stem = brandified
        except Exception as _be:
            logger.debug("R-F311 brandify failed for %r: %s", name[:60], _be)

    # R-F3218 — split "Trading Name (Registered Name Ltd)" so BOTH halves are
    # screened. The composite matches no list entry, and before this the whole
    # screen was declined outright. The outer text stays primary (callers key off
    # `result["name"]`); the registered name rides as an alias, which is exactly
    # what the alias machinery below is for.
    _outer, _inner = split_bracketed_name(name)
    if _inner and _outer and _outer != name:
        known_aliases = list(known_aliases or [])
        for _seg in _inner:
            if _seg not in known_aliases and _screenable(_seg, trusted=_trusted):
                known_aliases.append(_seg)
        if _screenable(_outer, trusted=_trusted):
            logger.info(
                "R-F3218: screen_with_aliases split %r → primary %r + alias(es) %s",
                name[:70], _outer[:50], [s[:40] for s in _inner][:3],
            )
            name = _outer

    # Reject inputs that aren't entity names — caller passed a search
    # query / description by mistake. Returning early avoids hitting
    # OpenSanctions for 80+ wasted calls per cycle (F1+F2 fix 2026-04-27).
    if not _screenable(name, trusted=_trusted):
        logger.info(
            "screen_with_aliases: rejecting non-entity input %r (source=%s; looks "
            "like a search query, not a name)",
            name[:80], source,
        )
        return {
            "name": name,
            "error": "not_entity_shaped",
            "matches": [],
            "blocked": False,
            "top_score": 0,
            "original_name": original_name,
        }

    targets = [name] + (known_aliases or [])
    targets = [t for t in targets if _screenable(t, trusted=_trusted)]
    if not targets:
        return {"error": "no valid names to screen"}

    # R-F434 (2026-05-13): the set of targets that originate from the
    # brandified hostname (brandified stem + original hostname). Matches
    # surfaced via these targets ARE NOT corroborated by a verified legal
    # name, so the classifier must cap them at AMBER. Caller-supplied
    # known_aliases (legal-name corroboration) are NOT in this set.
    _hostname_origin_targets: set[str] = set()
    if _from_brandified_hostname:
        _hostname_origin_targets.add(name)  # brandified stem (already mutated)
        _hostname_origin_targets.add(original_name)  # raw hostname

    all_results = []
    for target in targets[:5]:
        try:
            r = await fuzzy_screen(target)
            r["alias_screened"] = target
            all_results.append(r)
        except Exception as e:
            logger.warning("fuzzy_screen failed for alias '%s': %s", target, e)

    if not all_results:
        return {
            "name": name, "error": "sanctions_source_unavailable",
            "matches": [], "blocked": False,
            "screened": False, "source_unavailable": True,
        }

    # R-F1696: the aggregate is only "performed" if at least one underlying
    # fuzzy_screen actually reached the source. Otherwise every empty result is
    # an UNPERFORMED screen, not a clearance.
    any_screened = any(r.get("screened") or r.get("matches") for r in all_results)

    # Aggregate
    worst = max(all_results, key=lambda r: r.get("top_score", 0))
    all_matches = []
    for r in all_results:
        _alias = r.get("alias_screened")
        _is_hostname_origin = _alias in _hostname_origin_targets
        for m in r.get("matches", []):
            # R-F434: tag origin so the classifier can cap severity
            # at AMBER for brandified-hostname-derived false positives.
            if _is_hostname_origin:
                m["_from_brandified_hostname"] = True
                m["_brandified_stem"] = _brandified_stem
            # R-F449 (2026-05-13) — tag corroboration on EVERY surfaced
            # match, not only hostname-origin ones. Pre-R-F449 R-F436
            # and R-F437 comments claimed "passing the entity name as
            # known_aliases sets _has_caller_supplied_aliases=True per
            # match" but in fact the flag was ONLY written for
            # hostname-origin matches because the assignment sat inside
            # the `if _is_hostname_origin:` guard. Behaviourally moot
            # today (the R-F434 cap only fires when the brandified-
            # hostname tag is also set) but a future non-hostname cap
            # reading the same flag would silently no-op. Move out so
            # the tag reflects reality.
            m["_has_caller_supplied_aliases"] = _has_caller_supplied_aliases
            # R-F444 — deprecated alias retained for one release for
            # any consumer still reading the old key.
            m["_has_legal_name_corroboration"] = _has_caller_supplied_aliases
            all_matches.append(m)
    # Dedup by candidate name + list
    seen = set()
    deduped = []
    for m in sorted(all_matches, key=lambda x: -x["score"]):
        key = (m.get("name", "").lower(), m.get("list", ""))
        if key in seen: continue
        seen.add(key)
        deduped.append(m)


    # R-F996/R-F1696 — wire to brain HONESTLY: success only when the screen
    # actually ran; failure (so the coder/operator can see it) when the source
    # was unreachable for every alias.
    _source_unavailable = not any_screened
    if _source_unavailable:
        wire_failure(
            module="sanctions",
            detail=(
                f"Sanctions screen '{name}' (+{len(targets)-1} aliases) NOT "
                f"performed — source unreachable for all targets. UNVERIFIED, "
                f"not clear."
            ),
            gap_type="sanctions_source_unavailable",
            source="sanctions:screen_with_aliases",
        )
    else:
        wire_success(
            module="sanctions",
            summary="Sanctions screening",
            source_id="sanctions:R-F996",
        )
    return {
        "name": name,
        "aliases_checked": targets,
        "matches": deduped[:15],
        "top_score": worst.get("top_score", 0),
        "blocked": worst.get("blocked", False),
        "screened": any_screened,  # R-F1696
        **({"source_unavailable": True, "error": "sanctions_source_unavailable"}
           if _source_unavailable else {}),
        "match_count": len(deduped),
        "per_alias_results": all_results,
        "disclaimer": worst.get("disclaimer"),
        # R-F434: visibility hooks for renderers and chat output.
        "from_brandified_hostname": _from_brandified_hostname,
        "brandified_stem": _brandified_stem,
        # R-F444 (2026-05-13) — flag renamed to honest name. The old
        # `has_legal_name_corroboration` overstated the guarantee:
        # the flag was True whenever ANY known_aliases were passed,
        # including the R-F312 brandified-re-fire path which passes
        # the raw hostname as an alias (not a verified legal name).
        # New canonical key: `has_caller_supplied_aliases`. Old key
        # preserved as a deprecated alias for one release.
        "has_caller_supplied_aliases": _has_caller_supplied_aliases,
        "has_legal_name_corroboration": _has_caller_supplied_aliases,  # deprecated
    }
