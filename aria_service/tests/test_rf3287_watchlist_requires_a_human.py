"""R-F3287 — nothing reaches the watchlist unless a person put it there.

Operator directive: "ensure for the future no entities are placed on watchlist
without the user doing it manually as these would have costs for the user."

The cost is real and recurring. A watchlist entry is not a row in a table; it
is a standing instruction to re-screen that entity on every review cycle, for
as long as it sits there. R-F878 enrolled EVERY completed DD automatically, so
a user who ran three DDs to look something up acquired three permanent,
billable monitoring subscriptions without ever asking for one, and only found
out by looking at the watchlist page. Two paths did this:

    dd_orchestrator.py  — every completed DD, source "dd_auto_enroll"
    autonomous/tasks.py — any entity a task's risk keywords matched

Both funnel through `add_to_watchlist`, which is the only writer of
WATCHLIST_KEY anywhere in the tree, so that function is the whole gate.

THE LINE THIS DRAWS, and it is not "block the automatic callers":

  * CREATING an entry is what costs money, so it requires an explicit
    `requested_by_user=True`. The default is False, so a caller that does not
    say a human asked cannot create one — including a future caller nobody has
    written yet. Fail-closed, because the failure mode is silent spend.

  * ENRICHING an entry that already exists stays open to everyone. The entity
    is already enrolled and already being re-screened; back-filling its
    canonical id or its latest risk costs nothing extra and makes the
    monitoring the user DID ask for more accurate. Refusing it would degrade
    the user's own entries to make a point.

What this knowingly gives up: R-F878's coherence property, that a manually
DD'd entity is automatically monitored. That was a real gap when it was
written. The operator has weighed it against unrequested spend and chosen
spend control, so the DD now says plainly that the entity is not being
monitored instead of quietly enrolling it.
"""

from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as dd

# R-F3784/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import function_source, module_source


@pytest.fixture()
def store(monkeypatch):
    """In-memory stand-in for redis_store, scoped to WATCHLIST_KEY."""
    from aria_service.intel import redis_store as rs

    data: dict = {}

    async def _get_json(key, *a, **k):
        return data.get(key)

    async def _set_json(key, value, *a, **k):
        data[key] = value
        return True

    monkeypatch.setattr(rs, "get_json", _get_json)
    # R-F3506 — the watchlist read-modify-write now reads STRICTLY so a store
    # reconnect cannot be mistaken for an empty list. Patch both, or the real
    # store answers and the fixture silently stops controlling the test.
    monkeypatch.setattr(rs, "get_json_strict", _get_json)
    monkeypatch.setattr(rs, "set_json", _set_json)
    return data


def _add(**kw):
    target = {"name": kw.pop("name", "Acme Ltd")}
    requested = kw.pop("requested_by_user", None)
    target.update(kw)
    if requested is None:
        return asyncio.run(dd.add_to_watchlist(target))
    return asyncio.run(dd.add_to_watchlist(target, requested_by_user=requested))


# ── the gate ─────────────────────────────────────────────────────────────

def test_an_unrequested_enrolment_does_not_create_an_entry(store):
    """THE regression. This is the call R-F878 makes on every completed DD."""
    result = _add(name="VIGILO SOLUTIONS LIMITED", source="dd_auto_enroll")

    assert store.get(dd.WATCHLIST_KEY) in (None, []), (
        "an entity was enrolled without anyone asking, which starts a "
        "recurring re-screen charge the user never agreed to")
    assert result["ok"] is False
    assert result.get("enrolled") is False


def test_the_refusal_says_why_rather_than_failing_silently(store):
    """A silent no-op here is indistinguishable from success, and the caller
    would go on believing the entity is monitored."""
    result = _add(name="RCP Parking Limited", source="dd_auto_enroll")
    assert result.get("note"), "the refusal carries no reason"
    assert "manual" in result["note"].lower() or "user" in result["note"].lower()


def test_a_user_requested_add_still_works(store):
    """The gate must not break the thing it is protecting."""
    result = _add(name="Acme Ltd", user_id="alice", requested_by_user=True)
    assert result["ok"] is True
    entries = store[dd.WATCHLIST_KEY]
    assert len(entries) == 1
    assert entries[0]["name"] == "Acme Ltd"


def test_the_default_is_refusal_so_a_future_caller_cannot_enrol_by_omission(store):
    """Fail-closed. A new call site that simply does not think about this gets
    the safe outcome, not the expensive one."""
    import inspect

    sig = inspect.signature(dd.add_to_watchlist)
    param = sig.parameters.get("requested_by_user")
    assert param is not None, "there is no gate parameter at all"
    assert param.default is False, "the gate defaults to permitting enrolment"
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        "the flag must be keyword-only so it cannot be set by a stray "
        "positional argument")


# ── enrichment stays open, because it is free ────────────────────────────

def test_an_automatic_caller_may_still_enrich_an_entry_the_user_added(store):
    """The entity is already enrolled and already being re-screened. Refusing
    to back-fill its canonical id would degrade the user's own entry."""
    _add(name="Acme Ltd", user_id="alice", requested_by_user=True)

    result = _add(name="Acme Ltd", user_id="alice", source="dd_auto_enroll",
                  canonical_entity_id="GB-12345678", last_risk="AMBER-LIGHT")

    assert result["ok"] is True
    assert result["note"] == "already on watchlist"
    entry = store[dd.WATCHLIST_KEY][0]
    assert entry["canonical_entity_id"] == "GB-12345678"
    assert entry["last_risk"] == "AMBER-LIGHT"


def test_enrichment_never_creates_a_second_entry(store):
    _add(name="Acme Ltd", user_id="alice", requested_by_user=True)
    _add(name="Acme Ltd", user_id="alice", source="dd_auto_enroll",
         canonical_entity_id="GB-1")
    assert len(store[dd.WATCHLIST_KEY]) == 1


def test_an_automatic_call_for_a_DIFFERENT_owner_does_not_create(store):
    """R-F2401 makes dedup per-owner, so an automatic call naming a new owner
    falls THROUGH the dedup branch to the insert. That path has to be gated
    too, or the fix has a hole exactly the width of a second tenant."""
    _add(name="Acme Ltd", user_id="alice", requested_by_user=True)

    result = _add(name="Acme Ltd", user_id="bob", source="dd_auto_enroll")

    assert result["ok"] is False
    names = [(e["name"], e.get("user_id")) for e in store[dd.WATCHLIST_KEY]]
    assert names == [("Acme Ltd", "alice")], (
        f"an entry was created for a second owner automatically: {names}")


def test_per_owner_dedup_still_works_for_two_real_users(store):
    """R-F2401's property must survive: two users who each ASK get their own
    entry, and neither is denied because the other got there first."""
    _add(name="Acme Ltd", user_id="alice", requested_by_user=True)
    _add(name="Acme Ltd", user_id="bob", requested_by_user=True)
    owners = sorted(e.get("user_id") for e in store[dd.WATCHLIST_KEY])
    assert owners == ["alice", "bob"]


# ── the callers ──────────────────────────────────────────────────────────

def test_the_user_facing_route_asks_for_enrolment(store):
    """POST /dd/watchlist IS the user doing it manually, and is the one caller
    that must pass the flag. If it does not, the gate blocks the only path the
    user has."""
    import inspect

    from aria_service.routes import aria as routes_aria

    src = function_source(routes_aria, "dd_watchlist_add_ep")
    assert "requested_by_user=True" in src, (
        "the manual add route does not request enrolment, so a user clicking "
        "'Add Entity' is refused")


def test_no_automatic_caller_passes_the_flag():
    """The two automatic paths must not acquire it by copy-paste."""
    import inspect

    from aria_service.autonomous import tasks as auto_tasks

    dd_src = module_source(dd)
    auto_src = module_source(auto_tasks)

    # R-F878's enrol block in dd_orchestrator.
    enrol = dd_src[dd_src.index("R-F878 (2026-05-25)"):]
    enrol = enrol[:enrol.index("async def") if "async def" in enrol else 4000]
    assert "requested_by_user=True" not in enrol, (
        "the DD auto-enrol path is asking for enrolment on the user's behalf")
    assert "requested_by_user=True" not in auto_src, (
        "the autonomous escalation path is asking for enrolment on the "
        "user's behalf")


def test_add_to_watchlist_is_the_only_function_that_can_create_an_entry():
    """The gate is only a gate while every CREATE goes through it.

    Asserted by naming the functions rather than counting the writes. A count
    tells you something changed; it does not tell you whether the new writer
    can enrol an entity, which is the only question that matters. Each function
    below was read and confirmed to mutate or remove entries that already
    exist:

        update_watchlist_schedule  rewrites review_interval_hours in place
        remove_from_watchlist      persists `kept`, a filtered subset
        get_watchlist              R-F2401 owner back-fill, mutates w[...]
        delete_report              persists `_kept`, a filtered subset
        rescreen_watchlist         R-F2613 purge, persists a filtered list

    None of them appends. If a sixth name appears here, it has to be read and
    proven unable to insert before it is added to this list.
    """
    import inspect
    import re

    src = module_source(dd).split("\n")
    allowed = {
        "add_to_watchlist", "update_watchlist_schedule", "remove_from_watchlist",
        "get_watchlist", "delete_report", "rescreen_watchlist",
    }
    found = set()
    for i, line in enumerate(src):
        if not re.search(r"set_json\(\s*WATCHLIST_KEY", line):
            continue
        for j in range(i, -1, -1):          # walk back to the enclosing def
            stripped = src[j].lstrip()
            if (stripped.startswith(("def ", "async def "))
                    and len(src[j]) - len(stripped) == 0):
                found.add(stripped.split("(")[0].replace("async def ", "")
                          .replace("def ", "").strip())
                break

    unexpected = found - allowed
    assert not unexpected, (
        f"a new writer of WATCHLIST_KEY appeared: {sorted(unexpected)}. Read it "
        f"and prove it cannot CREATE an entry, or route it through the gate.")

    # And the gate itself must still hold both of its writes: the enrichment
    # path and the create path.
    fn_src = function_source(dd, "add_to_watchlist")
    assert len(re.findall(r"set_json\(\s*WATCHLIST_KEY", fn_src)) == 2
    # The create write must sit AFTER the refusal, or the gate is unreachable.
    assert fn_src.index("if not requested_by_user") < fn_src.rindex(
        "set_json(WATCHLIST_KEY"), (
        "the create write happens before the gate is checked")


def test_a_target_without_a_name_is_still_rejected(store):
    """The pre-existing contract does not change."""
    with pytest.raises(ValueError):
        asyncio.run(dd.add_to_watchlist({"name": "  "}, requested_by_user=True))
