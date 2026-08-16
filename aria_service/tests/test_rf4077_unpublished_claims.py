"""R-F4077 (C-127) — a reservation lost to a concurrent merge is invisible until too late.

WHAT HAPPENED, 2026-08-16. Two agents share this tree. I reserved R-F4061/R-F4062
(C-123/C-124) and wrote them into code, tests and the defect register. A peer
committed the SAME numbers concurrently — `fix: R-F4061..R-F4072 (C-109..C-119,
C-122)` — and won the ledger merge. My entries were gone, so my code referenced
numbers whose ledger titles described someone else's work. Recovery meant a
rename pass across five files, which is exactly what §2 created the allocator to
abolish.

WHY THE EXISTING GUARDS DID NOT CATCH IT. They are good and they were not enough:

  * `reserve()` unions the ledger with `r_numbers_known_to_git()` (R-F3248), and
    `expand_r_numbers` correctly expands the peer's `R-F4061..R-F4072` RANGE —
    verified, that scan does know 4062.
  * But git can only know a claim that has been COMMITTED. Both reservations
    lived in working trees at the moment of allocation, so neither allocator
    could see the other. The exposure is the reserve-to-commit WINDOW.

WHY THIS DETECTOR AND NOT THE OBVIOUS ONE. The intuitive check is "does my local
ledger entry still match origin's?" — measured on this repo, **hundreds of
entries** differ in title between local and `origin/main` from ordinary edits and
reconciliation. A guard that fires hundreds of times is one nobody reads (the
same reasoning C-96 used to keep `busy` out of `degraded_reasons`).

The signal that is both precise and quiet is **the unpublished claim**: a
reservation present locally and absent from the published ledger. That set is
normally 0-2 entries, it is exactly the window that loses claims, and it is
actionable — publish the ledger before building on the number.
"""
from __future__ import annotations

import json

from aria_service.intel import r_number_registry as reg


def _ledger(entries):
    return {"next_available": 9999, "reservations": entries}


def _entry(num, title="t", by="claude"):
    return {"r_number": num, "title": title, "claimed_at": "2026-08-16T00:00:00Z",
            "claimed_by": by, "status": "in_progress", "commit_sha": None,
            "notes": None}


def test_unpublished_claim_is_reported(tmp_path, monkeypatch):
    local = _ledger([_entry("R-F1"), _entry("R-F2")])
    published = _ledger([_entry("R-F1")])

    monkeypatch.setattr(reg, "_published_reservations", lambda **kw: (published, True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(local), encoding="utf-8")

    out = reg.unpublished_claims(path=p)
    assert out["readable"] is True
    assert out["unpublished"] == ["R-F2"], (
        f"the claim that exists only locally is the one a merge can lose: {out}"
    )


def test_fully_published_ledger_is_quiet(tmp_path, monkeypatch):
    """A guard that fires constantly is one nobody reads."""
    local = _ledger([_entry("R-F1"), _entry("R-F2")])
    monkeypatch.setattr(reg, "_published_reservations", lambda **kw: (local, True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(local), encoding="utf-8")

    assert reg.unpublished_claims(path=p)["unpublished"] == []


def test_title_edits_do_not_trigger_it(tmp_path, monkeypatch):
    """THE reason this detector exists rather than a title-divergence one.

    Measured on this repo, hundreds of entries differ in title between local and
    origin from ordinary edits. Divergence is noise; absence is signal.
    """
    local = _ledger([_entry("R-F1", title="renamed later")])
    published = _ledger([_entry("R-F1", title="original")])
    monkeypatch.setattr(reg, "_published_reservations", lambda **kw: (published, True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(local), encoding="utf-8")

    assert reg.unpublished_claims(path=p)["unpublished"] == []


def test_unreadable_published_ledger_reports_unknown_not_clean(tmp_path, monkeypatch):
    """`readable: False` must never be rendered as 'nothing unpublished'.

    "Could not measure" is not "measured and found nothing" — the §1 collapse
    this repo has paid for three times.
    """
    local = _ledger([_entry("R-F1")])
    monkeypatch.setattr(reg, "_published_reservations", lambda **kw: (None, False))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(local), encoding="utf-8")

    out = reg.unpublished_claims(path=p)
    assert out["readable"] is False
    assert out["unpublished"] is None, (
        "an unreadable published ledger must yield None (unknown), never [] "
        f"(measured-and-clean): {out}"
    )


# ── displacement: the case absence-detection CANNOT see ────────────────────
#
# Proven live on 2026-08-16, by this very fix: I committed R-F4076 for C-127
# while a peer published R-F4076 for a C-113 follow-up. `unpublished_claims`
# reported "all published" — because the number WAS present upstream, just
# carrying someone else's claim. Absence is the wrong question once the number
# exists; identity is the right one.

def test_displaced_claim_is_reported(tmp_path, monkeypatch):
    """Same number upstream, DIFFERENT claim — absence-detection is blind to it."""
    mine = _entry("R-F9", title="my work", by="claude")
    theirs = dict(mine, title="someone else's work", claimed_at="2026-08-16T23:00:00Z")

    monkeypatch.setattr(reg, "_published_reservations",
                        lambda **kw: (_ledger([theirs]), True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(_ledger([mine])), encoding="utf-8")

    out = reg.unpublished_claims(path=p)
    assert out["unpublished"] == [], "the number IS present upstream"
    assert out["displaced"] == ["R-F9"], (
        "a number whose published claim differs from the local one is a "
        f"COLLISION — the local work references a number it does not own: {out}"
    )


def test_identical_claim_is_not_displaced(tmp_path, monkeypatch):
    """My own published claim must never look displaced."""
    mine = _entry("R-F9", title="my work")
    monkeypatch.setattr(reg, "_published_reservations",
                        lambda **kw: (_ledger([mine]), True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(_ledger([mine])), encoding="utf-8")

    assert reg.unpublished_claims(path=p)["displaced"] == []


def test_a_later_ship_mark_is_not_displacement(tmp_path, monkeypatch):
    """status/commit_sha change on the SAME claim — not a collision."""
    mine = _entry("R-F9", title="my work")
    shipped = dict(mine, status="shipped", commit_sha="abc1234")
    monkeypatch.setattr(reg, "_published_reservations",
                        lambda **kw: (_ledger([shipped]), True))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(_ledger([mine])), encoding="utf-8")

    assert reg.unpublished_claims(path=p)["displaced"] == [], (
        "ship-marking my own claim must not read as someone taking my number"
    )


def test_displaced_is_unknown_when_unreadable(tmp_path, monkeypatch):
    monkeypatch.setattr(reg, "_published_reservations", lambda **kw: (None, False))
    p = tmp_path / "led.json"
    p.write_text(json.dumps(_ledger([_entry("R-F9")])), encoding="utf-8")
    assert reg.unpublished_claims(path=p)["displaced"] is None
