"""R-F934/F935/F936 — Compliance Watch analysis + private delivery + feedback.

Proves the findings engine is grounded (every finding cites verbatim evidence),
the risk/blind-spot/contradiction lanes fire correctly, the private digest is
structured per spec, coverage ('nothing missed') is provable, and the
end-to-end run emails the compliance principal via the gated bridge (draft-safe).
"""
from __future__ import annotations

import asyncio
import json

import pytest

from aria_service.intel import compliance_watch as cw
from aria_service.intel import redis_store as rs


@pytest.fixture
def fake_rs(monkeypatch):
    store = {"lists": {}, "ctr": {}, "kv": {}}

    async def lpush(k, v): store["lists"].setdefault(k, []).insert(0, v)
    async def lrange(k, s, e):
        lst = store["lists"].get(k, []); n = len(lst); e2 = (n - 1) if e == -1 else e
        return lst[s:e2 + 1]
    async def llen(k): return len(store["lists"].get(k, []))
    async def incr(k, amount=1):
        store["ctr"][k] = store["ctr"].get(k, 0) + amount; return store["ctr"][k]
    async def _set(k, v, ex=None, **kw): store["kv"][k] = v
    async def _get(k): return store["kv"].get(k)

    monkeypatch.setattr(rs, "lpush", lpush)
    monkeypatch.setattr(rs, "lrange", lrange)
    monkeypatch.setattr(rs, "llen", llen)
    monkeypatch.setattr(rs, "incr", incr)
    monkeypatch.setattr(rs, "set", _set)
    monkeypatch.setattr(rs, "get", _get)
    return store


def _rec(seq, sender, text, group="DEAL ROOM", ts="2026-05-27T10:00:00Z"):
    return {"seq": seq, "group": group, "sender": sender, "timestamp": ts,
            "text": text, "channel": "whatsapp", "captured_at": 1.0 * seq}


# ── Slice 2: analysis lanes ──────────────────────────────────────────────────

@pytest.mark.parametrize("text,expect_cat", [
    ("We can divert the shipment via a front company to the final destination", "risk:diversion"),
    ("Just send a facilitation payment to the general and it's done", "risk:bribery"),
    ("Are they on the OFAC SDN list / consolidated list?", "risk:sanctions"),
    ("Do we have the end-user certificate and the export licence?", "risk:export_control"),
])
def test_rf934_risk_lexicon_flags_with_evidence(text, expect_cat):
    findings = cw.analyse_message(_rec(1, "Counterparty", text))
    cats = [f["category"] for f in findings]
    assert expect_cat in cats, f"expected {expect_cat} in {cats}"
    for f in findings:
        assert f["quote"], "every finding MUST cite verbatim evidence"
        assert f["group"] and f["sender"] and f["timestamp"]  # full attribution


def test_rf934_clean_message_no_findings():
    assert cw.analyse_message(_rec(1, "Bob", "Great, thanks — see you Tuesday.")) == []


def test_rf934_deception_lane_does_not_crash():
    # heuristic-dependent; just assert it runs + any finding is grounded
    for f in cw.analyse_message(_rec(1, "X", "I didn't do it, I swear, you have to believe me, it wasn't me at all")):
        assert f["quote"]


def test_rf935_blind_spot_unanswered_ask_flagged():
    recs = [_rec(1, "Counterparty", "Can you please confirm the EUC by tomorrow?")]
    f = cw.detect_blind_spots(recs)
    assert len(f) == 1 and f[0]["category"] == "blind_spot" and f[0]["quote"]


def test_rf935_blind_spot_answered_ask_not_flagged():
    recs = [_rec(2, "Bob", "Confirmed, EUC attached"),       # newer reply, diff sender
            _rec(1, "Counterparty", "Can you confirm the EUC by tomorrow?")]
    assert cw.detect_blind_spots(recs) == []


def test_rf935_blind_spot_principal_own_ask_ignored():
    recs = [_rec(1, "Antonio", "Can you confirm by tomorrow?")]  # principal asking others
    assert cw.detect_blind_spots(recs) == []


def test_rf935_contradiction_reversal_flagged():
    recs = [_rec(2, "Counterparty", "Actually, we never agreed to that price"),
            _rec(1, "Counterparty", "Yes we agreed to deliver at that price")]
    f = cw.detect_contradictions(recs)
    assert len(f) >= 1 and f[0]["category"] == "contradiction" and f[0]["quote"]


# ── Slice 2 integration + slice 4 coverage (need the capture store) ──────────

def test_rf934_analyse_window_integrates(fake_rs):
    asyncio.run(cw.capture_message(group="DEAL", sender="CP",
                                   text="we can re-export and divert to the final destination"))
    asyncio.run(cw.capture_message(group="DEAL", sender="Bob", text="ok noted"))
    res = asyncio.run(cw.analyse_window(window_hours=99999))
    assert res["analysed"] == 2
    assert any(f["category"].startswith("risk:") for f in res["findings"])
    assert res["analysed_seq_max"] == 2


def test_rf936_coverage_report_gap(fake_rs):
    for i in range(3):
        asyncio.run(cw.capture_message(group="G", sender="S", text=f"m{i}"))
    cov = asyncio.run(cw.coverage_report())
    assert cov["total_captured"] == 3 and cov["max_seq"] == 3
    assert cov["unanalysed_gap"] == 3 and cov["fully_covered"] is False
    asyncio.run(cw.mark_analysed(3))
    cov2 = asyncio.run(cw.coverage_report())
    assert cov2["unanalysed_gap"] == 0 and cov2["fully_covered"] is True


# ── Slice 3: digest format + end-to-end delivery ─────────────────────────────

def test_rf935_format_digest_structure():
    findings = cw.analyse_message(_rec(7, "CP", "send a bribe to the minister", group="G", ts="T"))
    subject, body = cw.format_digest(findings, period_label="last 24h")
    assert "Compliance Watch" in subject and "finding" in subject
    assert "Evidence:" in body and "Read:" in body and "Action:" in body and "Confidence:" in body
    assert "send a bribe to the minister" in body  # verbatim evidence present


def test_rf935_format_digest_empty():
    subject, body = cw.format_digest([], period_label="last 24h")
    assert "No findings" in body


def test_rf935_run_end_to_end_emails_principal(fake_rs, monkeypatch):
    sent = {}
    def _send(**kw):
        sent.update(kw); return {"sent": True, "draft": False}
    async def _obs(*a, **k): return {}
    from aria_service.integrations import email_outbound as eo
    from aria_service.intel import brain_hook as bh
    monkeypatch.setattr(eo, "send_email", _send)
    monkeypatch.setattr(bh, "observe_self_event", _obs)
    monkeypatch.setenv("ARIA_COMPLIANCE_DIGEST_TO", "aria@imaria.io")

    asyncio.run(cw.capture_message(group="DEAL", sender="CP",
                                   text="we can divert via a front company"))
    report = asyncio.run(cw.run_compliance_watch(window_hours=99999))
    assert report["findings"] >= 1
    assert report["to"] == "aria@imaria.io"
    assert sent["to"] == "aria@imaria.io"
    assert sent["internal"] is True            # principal-only internal send
    assert "Compliance Watch" in sent["subject"]
    assert report["sent"] is True
    # coverage advanced (feedback loop ran)
    assert report["coverage"]["fully_covered"] is True


def test_rf937_principal_own_message_is_self_feedback(monkeypatch):
    """R-F937 — the principal's OWN messages get reframed as self-awareness
    coaching (category self_style, MEDIUM); a counterparty saying the same gets
    a deception ALERT. Uses a forced-HIGH score so the branch is deterministic."""
    class _T:
        value = "HIGH"
    class _FakeScore:
        tier = _T(); percentage = "75%"; signals_detected = []; confidence = 0.7; raw_score = 0.75
    class _FakeAnalyser:
        def analyse(self, *a, **k): return _FakeScore()
    monkeypatch.setattr(cw, "_get_deception_analyser", lambda: _FakeAnalyser())
    monkeypatch.setenv("ARIA_COMPLIANCE_PRINCIPAL_NAMES", "Antonio,Arkmurus")

    fp = cw.analyse_message(_rec(1, "Antonio", "well i mean its sort of basically fine more or less"))
    cats = [f["category"] for f in fp]
    assert "self_style" in cats and "deception" not in cats
    sf = next(f for f in fp if f["category"] == "self_style")
    assert sf["severity"] == "MEDIUM" and sf.get("self_feedback") is True and sf["quote"]

    fc = cw.analyse_message(_rec(2, "Counterparty", "well i mean its sort of basically fine more or less"))
    cats2 = [f["category"] for f in fc]
    assert "deception" in cats2 and "self_style" not in cats2


def test_rf935_urgent_only_silent_without_high_finding(fake_rs, monkeypatch):
    calls = {"n": 0}
    def _send(**kw): calls["n"] += 1; return {"sent": True}
    async def _obs(*a, **k): return {}
    from aria_service.integrations import email_outbound as eo
    from aria_service.intel import brain_hook as bh
    monkeypatch.setattr(eo, "send_email", _send)
    monkeypatch.setattr(bh, "observe_self_event", _obs)
    asyncio.run(cw.capture_message(group="G", sender="Bob", text="thanks, see you tuesday"))
    report = asyncio.run(cw.run_compliance_watch(window_hours=99999, urgent_only=True))
    assert report["sent"] is False and report["reason"] == "no_urgent_findings"
    assert calls["n"] == 0   # no email when nothing urgent
