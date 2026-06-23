"""ARIA Compliance Watch — evidentiary capture store (R-F933, slice 1).

Slice 1 of the Compliance Watch pipeline (CAPTURE → analyse → feedback → private
delivery). This is the CAPTURE layer: every WhatsApp group message the listener
forwards (brain/signal, signal_type=whatsapp_group_message) is persisted here
append-only, fully attributed (group, sender, message-time, verbatim text), and
HASH-CHAINED so the record is tamper-evident — an analyst (or, ultimately, a
regulator/court) can verify that no entry was altered or removed after capture.
Nothing is ever deleted (CLAUDE.md §7 — infinite memory, no eviction).

Design (compliance-grade, no room for mistakes):
  - append-only Redis/file list (newest at index 0 via lpush),
  - monotonic `seq` via an atomic counter (ordering survives a hash fork),
  - each record carries prev_hash + hash = sha256(prev_hash + canonical(record)),
    so verify_chain() can prove integrity end-to-end,
  - verbatim text is stored (capped) so every later finding can cite real
    evidence — the analysis/digest slices NEVER accuse without a quote.

The analysis + private-digest layers (later slices) READ from here via
get_captured(); they never re-fetch from the listener, so this store is the
single evidentiary source of truth. Best-effort + non-fatal: capture must never
break the live brain/signal path.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from typing import Any, Optional

logger = logging.getLogger("aria.intel.compliance_watch")

# R-F1165 — wire to brain on capture success/failure so ARIA learns
# from compliance evidentiary capture health.
from .engine_wiring import wire_success, wire_failure
from .wire import fail_wire  # R-F1789 §21 brain-wiring

_LOG_KEY = "crucix:aria:compliance_watch:log"      # lpush → newest at index 0
_SEQ_KEY = "crucix:aria:compliance_watch:seq"
_GENESIS = "0" * 64
_MAX_SCAN = 5000          # hard cap on records a single query/verify walks
_TEXT_CAP = 8000          # verbatim text cap per record


def _canonical(rec: dict) -> str:
    """Deterministic serialisation of the evidentiary fields (excludes the
    hash fields themselves) so the hash is reproducible for verification."""
    core = {k: rec.get(k) for k in
            ("seq", "group", "sender", "timestamp", "captured_at", "text", "channel")}
    return json.dumps(core, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hash(prev_hash: str, rec: dict) -> str:
    return hashlib.sha256((prev_hash + _canonical(rec)).encode("utf-8")).hexdigest()


async def _head_hash() -> str:
    """Hash of the most-recent record, or genesis when the log is empty."""
    try:
        from . import redis_store as rs
        head = await rs.lrange(_LOG_KEY, 0, 0)
        if head:
            return (json.loads(head[0]) or {}).get("hash") or _GENESIS
    except Exception:
        pass
        pass
    return _GENESIS


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def capture_message(*, group: str, sender: str, text: str,
                          timestamp: str = "", channel: str = "whatsapp") -> dict:
    """Append one attributed, hash-chained message to the evidentiary store.

    Best-effort: never raises into the caller (the brain/signal path). Returns
    {captured, seq, hash} on success or {captured: False, error} otherwise."""
    try:
        from . import redis_store as rs
        prev = await _head_hash()
        seq = await rs.incr(_SEQ_KEY)
        rec: dict[str, Any] = {
            "seq": int(seq),
            "group": str(group or "")[:200],
            "sender": str(sender or "")[:200],
            "timestamp": str(timestamp or "")[:40],      # message time (from WA)
            "captured_at": round(time.time(), 3),         # server receipt time
            "text": str(text or "")[:_TEXT_CAP],
            "channel": str(channel or "whatsapp")[:40],
            "prev_hash": prev,
        }
        rec["hash"] = _hash(prev, rec)
        await rs.lpush(_LOG_KEY, json.dumps(rec, ensure_ascii=False))
        # R-F1165 — wire success to brain
        wire_success(
            module="compliance_watch",
            summary=f"Captured compliance message seq={rec['seq']} from {rec['group']}",
            entity_name=rec["group"],
            source_id="compliance_watch:capture_message",
        )
        return {"captured": True, "seq": rec["seq"], "hash": rec["hash"]}
    except Exception as e:
        logger.warning("compliance_watch.capture_message failed (non-fatal): %s", e)
        # R-F1165 — wire failure to brain
        wire_failure(
            module="compliance_watch",
            detail=f"capture_message failed: {e}",
            gap_type="compliance_capture_failure",
            source="compliance_watch:capture_message",
        )
        return {"captured": False, "error": str(e)[:200]}


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def get_captured(*, since_epoch: Optional[float] = None,
                       group: Optional[str] = None, limit: int = 500) -> list[dict]:
    """Return captured messages newest-first, optionally filtered by group and/or
    server-receipt time (`captured_at` >= since_epoch). For the analysis layer."""
    out: list[dict] = []
    try:
        from . import redis_store as rs
        scan = min(_MAX_SCAN, max(1, limit * 4))
        rows = await rs.lrange(_LOG_KEY, 0, scan - 1)
        for raw in rows:
            try:
                rec = json.loads(raw)
            except Exception:
                pass
                continue
            if since_epoch is not None and float(rec.get("captured_at") or 0) < since_epoch:
                continue
            if group and rec.get("group") != group:
                continue
            out.append(rec)
            if len(out) >= limit:
                break
    except Exception as e:
        logger.warning("compliance_watch.get_captured failed: %s", e)
    return out


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def verify_chain(limit: int = 1000) -> dict:
    """Walk the recent chain newest→oldest, confirming each record's own hash
    AND its linkage to the next-older record. Returns
    {ok, checked, broken_at}. This is the tamper-evidence proof."""
    try:
        from . import redis_store as rs
        rows = await rs.lrange(_LOG_KEY, 0, max(1, min(_MAX_SCAN, limit)) - 1)
        checked = 0
        for i, raw in enumerate(rows):
            try:
                rec = json.loads(raw)
            except Exception:
                pass
                return {"ok": False, "checked": checked, "broken_at": "unparseable_record"}
            if _hash(rec.get("prev_hash", ""), rec) != rec.get("hash"):
                return {"ok": False, "checked": checked, "broken_at": rec.get("seq")}
            if i + 1 < len(rows):
                try:
                    older = json.loads(rows[i + 1])
                    if rec.get("prev_hash") != older.get("hash"):
                        return {"ok": False, "checked": checked, "broken_at": rec.get("seq")}
                except Exception:
                    pass
                    return {"ok": False, "checked": checked, "broken_at": "unparseable_link"}
            checked += 1
        return {"ok": True, "checked": checked, "broken_at": None}
    except Exception as e:
        # R-F1165 — wire chain verification failure to brain
        wire_failure(
            module="compliance_watch",
            detail=f"verify_chain failed: {e}",
            gap_type="compliance_chain_verification_failure",
            source="compliance_watch:verify_chain",
        )
        return {"ok": False, "checked": 0, "error": str(e)[:200]}


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def stats() -> dict:
    """Coverage stats for the capture store."""
    try:
        from . import redis_store as rs
        total = await rs.llen(_LOG_KEY)
        # R-F1165 — wire success to brain
        wire_success(
            module="compliance_watch",
            summary=f"Compliance watch stats: {total} captured",
            source_id="compliance_watch:stats",
        )
        return {"total_captured": total}
    except Exception as e:
        logger.warning("compliance_watch.stats failed: %s", e)
        # R-F1165 — wire failure to brain
        wire_failure(
            module="compliance_watch",
            detail=f"stats failed: {e}",
            gap_type="compliance_stats_failure",
            source="compliance_watch:stats",
        )
        return {"total_captured": 0}


# ═════════════════════════════════════════════════════════════════════════════
# SLICE 2 — ANALYSE: grounded findings engine (R-F934)
# Reads the capture store and produces findings, each carrying its exact
# evidence (verbatim quote + attribution). HARD RULE: no finding without a
# quote — Compliance Watch never accuses on an inference alone.
# ═════════════════════════════════════════════════════════════════════════════

# Deterministic risk lexicon (no LLM, no cost). Tuned for defence-broking
# compliance: sanctions, export-control, diversion, and bribery/ABC.
_RISK_PATTERNS: list[tuple[str, str, "re.Pattern[str]"]] = [
    ("sanctions", "HIGH", re.compile(
        r"\b(sanction(ed|s)?|embargo(ed)?|ofac|ofsi|sdn list|consolidated list|"
        r"designated (entity|person)|asset freeze|blocked person)\b", re.I)),
    ("diversion", "HIGH", re.compile(
        r"\b(divert(ed|ing)?|re-?export|tran(s-?)?ship(ment)?|front compan(y|ies)|"
        r"false (end-?user|euc)|grey market|circumvent(ing)? (the )?(sanction|control)|"
        r"change (the )?(final )?destination)\b", re.I)),
    ("export_control", "MEDIUM", re.compile(
        r"\b(export licen[cs]e|\bitar\b|dual-?use|\bml\s?\d|controlled goods|"
        r"end-?user (cert|certificate|undertaking)|\beuc\b|wassenaar|brokering licen[cs]e)\b", re.I)),
    ("bribery", "HIGH", re.compile(
        r"\b(bribe|kickback|facilitation payment|under the table|off the books|"
        r"grease (the )?palm|secret commission|slush fund|envelope for|"
        r"commission to (the )?(official|minister|general|colonel))\b", re.I)),
]

# Cues that a message is an ASK aimed at the team (for the blind-spot lane).
_ASK_CUE = re.compile(
    r"(\?\s*$)|\b(can you|could you|please (confirm|send|advise|approve|review)|"
    r"need(ed)? (it |this )?(by|before)|deadline|by (tomorrow|monday|tuesday|wednesday|"
    r"thursday|friday|end of (day|week)|cob|eod)|asap|waiting (on|for) (you|your)|"
    r"awaiting your|any update|chase|follow(-| )?up)\b", re.I)

# Conservative contradiction cue: an explicit reversal by the same person.
_REVERSAL_CUE = re.compile(
    r"\b(actually|in fact|to be clear|correction|i misspoke|that('?s| is) (not|wrong)|"
    r"no longer|we don'?t|we do not|never (said|agreed)|changed? my mind|"
    r"scratch that|disregard|the opposite)\b", re.I)

_SEV_RANK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}
_URGENT_SEVERITIES = {"HIGH", "CRITICAL"}

_LAST_ANALYSED_KEY = "crucix:aria:compliance_watch:last_analysed_seq"


def _operator_email() -> str:
    for k in ("ARIA_COMPLIANCE_DIGEST_TO", "ARIA_OPERATOR_EMAIL", "ARIA_EMAIL_USER"):
        v = (os.getenv(k) or "").strip()
        if v:
            return v
    return "acorrea@arkmurus.com"


def _principal_names() -> set[str]:
    raw = os.getenv("ARIA_COMPLIANCE_PRINCIPAL_NAMES") or "Antonio,Arkmurus"
    return {n.strip().lower() for n in raw.split(",") if n.strip()}


_deception_analyser = None


def _get_deception_analyser():
    global _deception_analyser
    if _deception_analyser is None:
        from .deception_detection import ARIADeceptionAnalyser
        _deception_analyser = ARIADeceptionAnalyser()
    return _deception_analyser


def _finding(*, category: str, severity: str, rec: dict, read: str, action: str,
             confidence: float, extra: Optional[dict] = None) -> dict:
    """Build one grounded finding. `rec` supplies the verbatim evidence +
    attribution — a finding can ONLY exist with a backing record."""
    f = {
        "category": category,
        "severity": severity,
        "group": rec.get("group", ""),
        "sender": rec.get("sender", ""),
        "timestamp": rec.get("timestamp", ""),
        "seq": rec.get("seq"),
        "quote": (rec.get("text") or "")[:600],     # the evidence — verbatim
        "read": read,
        "action": action,
        "confidence": round(float(confidence), 2),
    }
    if extra:
        f.update(extra)
    return f


@fail_wire(module="compliance_watch", gap_type="engine_failure")
def analyse_message(rec: dict) -> list[dict]:
    """Per-message deterministic analysis: risk lexicon + linguistic deception.
    Cheap (no LLM). Returns zero or more grounded findings."""
    findings: list[dict] = []
    text = (rec.get("text") or "").strip()
    if not text:
        return findings

    # ── Risk lexicon ──
    for category, severity, pat in _RISK_PATTERNS:
        m = pat.search(text)
        if m:
            findings.append(_finding(
                category=f"risk:{category}", severity=severity, rec=rec,
                read=(f"Message references {category.replace('_',' ')} "
                      f"(matched: \"{m.group(0)}\"). In a defence-broking channel "
                      f"this is a {severity.lower()}-priority compliance signal."),
                action=("Review the full thread; if this concerns a live deal, run "
                        "sanctions/export-control screening on the parties + end-user "
                        "before any commitment."),
                confidence=0.6, extra={"matched_term": m.group(0)},
            ))

    # ── Linguistic deception (heuristic, no LLM) ──
    try:
        score = _get_deception_analyser().analyse(text, context_type="business_communication")
        tier = getattr(getattr(score, "tier", None), "value", "LOW")
        if tier in ("ELEVATED", "HIGH"):
            sigs = ", ".join(s.category for s in (score.signals_detected or [])[:3]) or "linguistic markers"
            conf = round(float(getattr(score, "confidence", 0.5)) * float(getattr(score, "raw_score", 0.5)) + 0.2, 2)
            dscore = round(float(getattr(score, "raw_score", 0.0)), 3)
            is_principal = (rec.get("sender", "") or "").lower() in _principal_names()
            if is_principal:
                # R-F937 — self-feedback. The principal asked to know whether their
                # OWN messages read as misleading. Framed as constructive
                # self-awareness coaching (capped MEDIUM — it is feedback, not a
                # counterparty alert), so the daily digest tells you how you come
                # across. The lane always analysed the principal; this just
                # reframes the output for the person reading their own digest.
                findings.append(_finding(
                    category="self_style", severity="MEDIUM", rec=rec,
                    read=(f"YOUR OWN message reads as potentially misleading/evasive "
                          f"({tier}, {score.percentage}) — signals: {sigs}. "
                          f"Self-awareness feedback on how you come across — not a verdict."),
                    action=("If clarity matters here, restate the point plainly — fewer "
                            "hedges, qualifiers and negations tends to read as more candid."),
                    confidence=conf,
                    extra={"deception_score": dscore, "deception_tier": tier, "self_feedback": True},
                ))
            else:
                findings.append(_finding(
                    category="deception", severity=("HIGH" if tier == "HIGH" else "MEDIUM"), rec=rec,
                    read=(f"Linguistic deception risk {tier} ({score.percentage}) — "
                          f"signals: {sigs}. This is a RISK INDICATOR, not a verdict."),
                    action="Treat statements in this message with extra scrutiny; corroborate independently before relying on them.",
                    confidence=conf,
                    extra={"deception_score": dscore, "deception_tier": tier},
                ))
    except Exception as e:
        logger.debug("deception analyse failed (non-fatal): %s", e)

    return findings


@fail_wire(module="compliance_watch", gap_type="engine_failure")
def detect_blind_spots(records: list[dict]) -> list[dict]:
    """Flag clear asks/deadlines that went UNANSWERED in the window — things
    the principal/team may have missed. Conservative: only fires on an explicit
    ask with no subsequent reply in the same group. Records are newest-first."""
    findings: list[dict] = []
    principals = _principal_names()
    # Index later replies per group (anything said AFTER a given seq in that group).
    by_group: dict[str, list[dict]] = {}
    for r in records:
        by_group.setdefault(r.get("group", ""), []).append(r)
    for grp, recs in by_group.items():
        # recs are newest-first; iterate and check if anyone replied AFTER each ask
        for i, r in enumerate(recs):
            text = (r.get("text") or "")
            if not _ASK_CUE.search(text):
                continue
            sender = (r.get("sender") or "").lower()
            if sender in principals:
                continue  # the principal asking others isn't a blind spot for them
            # any message in this group NEWER than r (index < i, since newest-first)?
            answered = any(
                (recs[j].get("sender") or "").lower() != sender
                for j in range(0, i)
            )
            if not answered:
                findings.append(_finding(
                    category="blind_spot", severity="MEDIUM", rec=r,
                    read=("A direct ask/deadline aimed at the team appears UNANSWERED "
                          "in the captured window — a potential dropped ball."),
                    action="Confirm whether this was handled off-channel; if not, respond or delegate.",
                    confidence=0.5,
                ))
    return findings


@fail_wire(module="compliance_watch", gap_type="engine_failure")
def detect_contradictions(records: list[dict]) -> list[dict]:
    """Conservative same-sender reversal detector (deterministic, no LLM): a
    sender who later explicitly reverses/corrects an earlier statement. Low
    confidence by design — flags for review, never asserts bad faith. The deep
    LLM claim-ledger contradiction pass is available separately (cost-gated)."""
    findings: list[dict] = []
    by_sender: dict[str, list[dict]] = {}
    for r in records:
        key = (r.get("group", ""), (r.get("sender") or "").lower())
        by_sender.setdefault(str(key), []).append(r)
    for _, recs in by_sender.items():
        for r in recs:  # newest-first; a reversal cue references something earlier
            text = (r.get("text") or "")
            if len(recs) >= 2 and _REVERSAL_CUE.search(text):
                findings.append(_finding(
                    category="contradiction", severity="HIGH", rec=r,
                    read=("Same participant appears to REVERSE/correct an earlier "
                          "statement in this group — verify which version is operative; "
                          "shifting positions can signal risk or simply a clarification."),
                    action="Pull this sender's prior messages in the thread and reconcile the two statements before relying on either.",
                    confidence=0.45,
                ))
    return findings


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def analyse_window(*, window_hours: float = 24.0, group: Optional[str] = None,
                         limit: int = 2000) -> dict:
    """Pull the recent capture window and run every analysis lane. Returns
    {findings, analysed, analysed_seq_max, window_hours}. Findings sorted by
    severity then recency."""
    since = time.time() - window_hours * 3600.0
    records = await get_captured(since_epoch=since, group=group, limit=limit)
    findings: list[dict] = []
    seq_max = 0
    for rec in records:
        try:
            seq_max = max(seq_max, int(rec.get("seq") or 0))
        except (TypeError, ValueError):
            pass
        findings.extend(analyse_message(rec))
    findings.extend(detect_blind_spots(records))
    findings.extend(detect_contradictions(records))
    findings.sort(key=lambda f: (_SEV_RANK.get(f.get("severity", "LOW"), 0),
                                 f.get("seq") or 0), reverse=True)
    return {
        "findings": findings,
        "analysed": len(records),
        "analysed_seq_max": seq_max,
        "window_hours": window_hours,
    }


# ═════════════════════════════════════════════════════════════════════════════
# SLICE 4 — FEEDBACK LOOP + coverage ledger (R-F936)
# ═════════════════════════════════════════════════════════════════════════════

@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def record_findings_to_brain(findings: list[dict]) -> None:
    """Feed findings into ARIA's brain so she learns patterns + the operator
    dashboard/coder can see them. Best-effort; never raises."""
    if not findings:
        return
    try:
        from . import brain_hook as _bh
        for f in findings[:50]:
            sev = f.get("severity", "LOW")
            await _bh.observe_self_event(
                f"compliance_finding:{f.get('category','')}",
                {k: f.get(k) for k in ("category", "severity", "group", "sender",
                                       "timestamp", "seq", "confidence")},
                success=(sev not in _URGENT_SEVERITIES),
                gap_type=("compliance_risk" if sev in _URGENT_SEVERITIES else "compliance_observation"),
            )
    except Exception as e:
        logger.debug("record_findings_to_brain failed (non-fatal): %s", e)


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def mark_analysed(seq: int) -> None:
    try:
        from . import redis_store as rs
        if seq and int(seq) > 0:
            await rs.set(_LAST_ANALYSED_KEY, str(int(seq)))
    except Exception:
        pass
        pass


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def coverage_report() -> dict:
    """The 'nothing missed' proof: total captured vs highest seq analysed.
    A positive gap = messages captured but not yet analysed."""
    try:
        from . import redis_store as rs
        total = await rs.llen(_LOG_KEY)
        head = await rs.lrange(_LOG_KEY, 0, 0)
        max_seq = 0
        if head:
            try:
                max_seq = int((json.loads(head[0]) or {}).get("seq") or 0)
            except Exception:
                pass
                max_seq = 0
        last = await rs.get(_LAST_ANALYSED_KEY)
        last_seq = int(last) if (last and str(last).isdigit()) else 0
        return {
            "total_captured": total,
            "max_seq": max_seq,
            "last_analysed_seq": last_seq,
            "unanalysed_gap": max(0, max_seq - last_seq),
            "fully_covered": max_seq <= last_seq,
        }
    except Exception as e:
        return {"error": str(e)[:200]}


# ═════════════════════════════════════════════════════════════════════════════
# SLICE 3 — PRIVATE DELIVERY: structured digest emailed to the principal (R-F935)
# ═════════════════════════════════════════════════════════════════════════════

@fail_wire(module="compliance_watch", gap_type="engine_failure")
def format_digest(findings: list[dict], *, period_label: str, coverage: Optional[dict] = None) -> tuple[str, str]:
    """Render (subject, body) — structured exactly per the spec:
    Subject · Group · Person · Time · verbatim quote · ARIA's read · action · confidence."""
    urgent = [f for f in findings if f.get("severity") in _URGENT_SEVERITIES]
    subject = (f"[ARIA Compliance Watch] {period_label} — "
               f"{len(findings)} finding(s), {len(urgent)} urgent")
    lines = [
        "ARIA COMPLIANCE WATCH — PRIVATE / COMPLIANCE PRINCIPAL ONLY",
        f"Period: {period_label}",
        "",
        ("⚠ This brief is grounded: every finding cites the verbatim message + "
         "attribution. Risk indicators are not verdicts."),
        "",
    ]
    if coverage:
        lines.append(f"Coverage: {coverage.get('total_captured','?')} messages captured · "
                     f"gap {coverage.get('unanalysed_gap','?')} unanalysed")
        lines.append("")
    if not findings:
        lines.append("No findings in this window. (Capture + analysis ran clean.)")
        return subject, "\n".join(lines)

    for f in findings:
        lines += [
            "─" * 60,
            f"[{f.get('severity')} · {f.get('category')}]  "
            f"{f.get('group','')} · {f.get('sender','')} · {f.get('timestamp','')}  (seq {f.get('seq')})",
            f"  Evidence: \"{f.get('quote','')}\"",
            f"  Read:     {f.get('read','')}",
            f"  Action:   {f.get('action','')}",
            f"  Confidence: {f.get('confidence')}",
        ]
    lines.append("─" * 60)
    return subject, "\n".join(lines)


@fail_wire(module="compliance_watch", gap_type="engine_failure")
async def run_compliance_watch(*, window_hours: float = 24.0, urgent_only: bool = False,
                               period_label: str = "") -> dict:
    """End-to-end: analyse the window → (feedback to brain + coverage) → email the
    private digest to the compliance principal. Uses the gated email_outbound
    bridge: it DRAFTS (and logs) until ARIA_EMAIL_OUTBOUND_ENABLED=1 + the
    principal is allow-listed, so this is safe to run before delivery is turned on.
    urgent_only=True suppresses the email unless there is a HIGH/CRITICAL finding."""
    label = period_label or ("urgent scan" if urgent_only else f"last {int(window_hours)}h")
    res = await analyse_window(window_hours=window_hours)
    findings = res["findings"]
    if urgent_only:
        findings = [f for f in findings if f.get("severity") in _URGENT_SEVERITIES]

    # Feedback loop + coverage (always, even if nothing emailed).
    await record_findings_to_brain(findings)
    if res.get("analysed_seq_max"):
        await mark_analysed(res["analysed_seq_max"])
    coverage = await coverage_report()

    if urgent_only and not findings:
        return {"sent": False, "reason": "no_urgent_findings", "analysed": res["analysed"],
                "findings": 0, "coverage": coverage}

    subject, body = format_digest(findings, period_label=label, coverage=coverage)
    delivery = {"sent": False, "draft": True}
    try:
        from ..integrations import email_outbound as _eo
        delivery = _eo.send_email(
            to=_operator_email(), subject=subject, body=body, internal=True,
            sender_note=("ARIA Compliance Watch — PRIVATE to the compliance principal. "
                         "Do not forward. Grounded findings (verbatim evidence cited)."),
        )
    except Exception as e:
        logger.warning("compliance digest send failed (non-fatal): %s", e)
        delivery = {"sent": False, "error": str(e)[:200]}

    return {
        "sent": bool(delivery.get("sent")),
        "drafted": bool(delivery.get("draft")) and not delivery.get("sent"),
        "to": _operator_email(),
        "findings": len(findings),
        "urgent_only": urgent_only,
        "analysed": res["analysed"],
        "coverage": coverage,
        "subject": subject,
    }
