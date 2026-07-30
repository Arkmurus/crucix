"""R-F3503 — the watchlist UI reported success when nothing was removed.

public/watchlist.html makes a precise, testable promise when you remove an entry:

    "'X' will no longer be re-screened against sanctions & PEP lists."

and then declares success on the HTTP status alone:

    const r = await authed('/api/aria/dd/watchlist/' + name, {method:'DELETE'});
    if (r.ok) Toast.show('"' + name + '" removed from watchlist', 'success');

``remove_from_watchlist`` returned ``{"ok": True, "removed": 0}`` whenever it
removed NOTHING — which happens on a name that matches no entry, and, more
importantly, whenever owner-scoping legitimately refuses the caller (another
tenant's entry, or an owner-less entry when the caller is not the configured
legacy operator; R-F2401).

In those cases the user was told the entity would no longer be re-screened, and
the autonomous dd_monitor went on screening it every 300s. The UI's claim was
false and nothing anywhere contradicted it.

This is the defect class memory/ui_unverified_claim_defect_class.md records — "a
source assertion proves SHAPE, not BEHAVIOUR" — and the same shape as
memory/approval_without_provisioning: a workflow that ends in a label grants
nothing; you have to check the store that holds the RESULT.

The fix is on both sides, because either alone still lies:
  * the API must not answer ok=True for "I did nothing"
  * the UI must confirm the entity actually left the list before claiming it did
"""
from __future__ import annotations

import pytest

from aria_service.intel import dd_orchestrator


class _FakeStore:
    def __init__(self, entries):
        self.entries = list(entries)

    async def get_json(self, _key):
        return list(self.entries)

    async def set_json(self, _key, value, **_kw):
        self.entries = list(value)
        return True


@pytest.fixture
def _store(monkeypatch):
    def _install(entries):
        fake = _FakeStore(entries)
        import aria_service.intel.redis_store as rs
        monkeypatch.setattr(rs, "get_json", fake.get_json)
        monkeypatch.setattr(rs, "set_json", fake.set_json)
        return fake
    return _install


class TestApiDoesNotClaimSuccessForANoOp:

    @pytest.mark.asyncio
    async def test_removing_a_real_entry_reports_ok(self, _store):
        _store([{"name": "Acme Ltd", "user_id": "u1"}])
        out = await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert out["ok"] is True
        assert out["removed"] == 1

    @pytest.mark.asyncio
    async def test_removing_a_missing_entry_is_NOT_ok(self, _store):
        _store([{"name": "Acme Ltd", "user_id": "u1"}])
        out = await dd_orchestrator.remove_from_watchlist("Nonexistent", user_id="u1")
        assert out["removed"] == 0
        assert out["ok"] is False, (
            "the API said ok for a removal that removed nothing — the UI shows "
            "'will no longer be re-screened' on the strength of this"
        )
        assert out.get("reason"), "a no-op removal must say why"

    @pytest.mark.asyncio
    async def test_a_scope_refusal_is_NOT_reported_as_success(self, _store):
        """The dangerous case: another tenant's entry. Owner-scoping correctly
        refuses (R-F2401), and the user must NOT be told it was removed."""
        _store([{"name": "Acme Ltd", "user_id": "someone_else"}])
        out = await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert out["removed"] == 0
        assert out["ok"] is False, (
            "a permission refusal was reported to the user as a successful removal"
        )

    @pytest.mark.asyncio
    async def test_the_entry_really_survives_a_refused_removal(self, _store):
        """Prove the BEHAVIOUR, not just the response: it is still monitored."""
        fake = _store([{"name": "Acme Ltd", "user_id": "someone_else"}])
        await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="u1")
        assert any(e["name"] == "Acme Ltd" for e in fake.entries), (
            "test premise wrong — the entry was actually removed"
        )

    @pytest.mark.asyncio
    async def test_internal_admin_removal_still_unrestricted(self, _store):
        """user_id='' is internal/admin and must keep working — the cascade in
        R-F3500 and the autonomous self-purge both rely on it."""
        _store([{"name": "Acme Ltd", "user_id": "someone_else"}])
        out = await dd_orchestrator.remove_from_watchlist("Acme Ltd", user_id="")
        assert out["ok"] is True and out["removed"] == 1


class TestTheUiVerifiesBeforeItClaims:

    def test_ui_checks_the_removed_count(self):
        import pathlib
        html = (pathlib.Path(__file__).resolve().parents[2]
                / "public" / "watchlist.html").read_text(encoding="utf-8")
        idx = html.find("async function removeEntry")
        assert idx > 0, "removeEntry not found"
        block = html[idx:idx + 2600]
        # NON-VACUOUS: the word "removed" also appears inside the success toast,
        # so asserting on it alone passes against the defect. Require the code to
        # actually READ the count off the response payload.
        assert ("payload.removed" in block or ".removed)" in block
                or "data.removed" in block), (
            "removeEntry claims success on r.ok alone; a 200 with removed=0 is "
            "reported to the user as 'will no longer be re-screened'"
        )
        assert "r.ok && reallyRemoved" in block or "reallyRemoved" in block, (
            "the success branch is not gated on a verified removal"
        )

    def test_ui_still_promises_what_the_backend_now_guarantees(self):
        """The copy is a behavioural promise; keep it and make it true, rather
        than softening the wording to match a weak backend."""
        import pathlib
        html = (pathlib.Path(__file__).resolve().parents[2]
                / "public" / "watchlist.html").read_text(encoding="utf-8")
        assert "no longer be re-screened" in html, (
            "the removal promise was weakened instead of being made true"
        )
