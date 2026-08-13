"""R-F3958 / C-48 — a near-miss flagged for HUMAN REVIEW was discarded and the
screen reported clean.

R-F3691 introduced `gate_blocked_near_miss` for the textbook REVIEW case: "we
found a name-overlapping designation but could not corroborate it, so a human
decides." The canonical lookup returns it correctly. One layer up, the R-F3529
local-canonical fallback in `fuzzy_screen` reads only `_local["matches"]` — and
a gate-blocked candidate is by construction NOT in that list, it is in
`gate_blocked`. So the verdict was dropped on the floor:

    'Rosoboronexport' -> canonical verdict=REVIEW  gate_blocked=1
                      -> fuzzy_screen screened=True blocked=False matches=0

Two consumers of the same canonical verdict disagreed: `company_investigator`
routes REVIEW to UNVERIFIED correctly; the DD path did not see it at all.

The fix routes the near-miss into `related_name_observations` — the channel
R-F2840 already documents as "reported, never blocking, never clean" — and
raises `requires_human_review`, so a caller that renders a clean bill has to
step over an explicit flag to do it.

`blocked` deliberately stays False. A gate-blocked candidate is NOT a
corroborated designation, and promoting it to a block would trade a false clean
for a false hit; R-F2840 narrowed the blocking set for exactly that reason.
REVIEW is a third state and must render as one.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import sanctions as S


class _Down:
    """OpenSanctions unavailable — the precondition for the local fallback."""
    results: list = []
    ok = False
    reason = "quota_exhausted"


@pytest.fixture()
def opensanctions_down(monkeypatch):
    async def _match(name, entity_type="Thing"):
        return _Down()

    async def _search(name, limit=8):
        return _Down()

    monkeypatch.setattr(S, "_opensanctions_match", _match)
    monkeypatch.setattr(S, "_opensanctions_search", _search)


def _canon_returning(payload):
    from aria_service.intel.sanctions_canonical import lookup as _canon

    def _check(name, **kw):
        return payload
    return _canon, _check


_NEAR_MISS = {
    "queried_name": "Rosoboronexport",
    "verdict": "REVIEW",
    "reason": "gate_blocked_near_miss",
    "matches": [],
    "gate_blocked": [{
        "source": "ofac_sdn",
        "formatted_name": "JSC ROSOBORONEXPORT MOSCOW REPRESENTATIVE OFFICE",
        "match_score": 0.81,
        "match_method": "blocked_entity_gate",
        "entity_type": "Entity",
    }],
    "source_unavailable": False,
}

_CLEAN = {
    "queried_name": "Ordinary Trading Ltd",
    "verdict": "CLEAR",
    "matches": [],
    "gate_blocked": [],
    "source_unavailable": False,
}


# ── the defect ───────────────────────────────────────────────────────────────

def test_near_miss_is_not_reported_as_a_completed_clean_screen(
        opensanctions_down, monkeypatch):
    _canon, _check = _canon_returning(_NEAR_MISS)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))

    assert res["screened"] is True, "the local store answered, so a screen ran"
    assert res.get("requires_human_review") is True, (
        "a gate_blocked_near_miss REVIEW verdict was dropped — the screen "
        "renders as a clean bill"
    )
    assert res.get("review_reason") == "gate_blocked_near_miss"


def test_the_near_miss_candidate_is_visible_to_the_reader(
        opensanctions_down, monkeypatch):
    """A flag with no evidence behind it cannot be actioned by an analyst."""
    _canon, _check = _canon_returning(_NEAR_MISS)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))
    obs = res.get("related_name_observations") or []
    assert obs, "the gate-blocked candidate reached no reader-facing field"
    assert any("ROSOBORONEXPORT" in (o.get("name") or "").upper() for o in obs)
    assert res.get("related_name_count") == len(obs)
    assert any("ofac_sdn" in (o.get("lists") or []) for o in obs)


def test_blocked_stays_false_a_near_miss_is_not_a_designation(
        opensanctions_down, monkeypatch):
    """R-F2840 narrowed the blocking set on purpose; do not undo it here."""
    _canon, _check = _canon_returning(_NEAR_MISS)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))
    assert res["blocked"] is False
    assert res["blocking_matches"] == []


def test_hard_stop_verdict_also_carries(opensanctions_down, monkeypatch):
    payload = dict(_NEAR_MISS, verdict="HARD_STOP", reason="")
    _canon, _check = _canon_returning(payload)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))
    assert res.get("requires_human_review") is True


# ── it must still be able to stay quiet ──────────────────────────────────────

def test_a_genuine_clear_is_not_flagged(opensanctions_down, monkeypatch):
    _canon, _check = _canon_returning(_CLEAN)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Ordinary Trading Ltd"))
    assert res["screened"] is True
    assert not res.get("requires_human_review")
    assert res.get("related_name_observations") == []


def test_insufficient_data_still_reports_source_unavailable(
        opensanctions_down, monkeypatch):
    """The R-F3529 contract survives: local cannot answer -> not screened."""
    payload = {"verdict": "INSUFFICIENT_DATA", "reason": "sanctions_store_empty",
               "matches": [], "gate_blocked": []}
    _canon, _check = _canon_returning(payload)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Nobody Ltd"))
    assert res["screened"] is False
    assert res.get("source_unavailable") is True
    assert not res.get("requires_human_review"), (
        "an unperformed screen must not be dressed up as a review finding"
    )


def test_a_real_local_hit_still_blocks(opensanctions_down, monkeypatch):
    """The healthy floor path must be untouched."""
    payload = {
        "verdict": "HARD_STOP",
        "matches": [{
            "source": "ofac_sdn",
            "formatted_name": "JSC ROSOBORONEXPORT",
            "match_score": 0.97,
            "entity_type": "Entity",
        }],
        "gate_blocked": [],
        "source_unavailable": False,
    }
    _canon, _check = _canon_returning(payload)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))
    assert res["screened"] is True
    assert res["blocked"] is True, "a corroborated canonical hit must still block"


def test_malformed_gate_blocked_payload_does_not_crash_the_screen(
        opensanctions_down, monkeypatch):
    payload = dict(_NEAR_MISS, gate_blocked="not-a-list")
    _canon, _check = _canon_returning(payload)
    monkeypatch.setattr(_canon, "check_sanctions", _check)

    res = asyncio.run(S.fuzzy_screen("Rosoboronexport"))
    # The verdict still carries even if the evidence list is unusable.
    assert res.get("requires_human_review") is True
