"""R-F3520 — R-F3506 fixed ONE of the two watchlists. The public one kept the defect.

R-F3506 closed the non-strict-read clobber on `crucix:dd:watchlist`: every mutation was a
read-modify-write built on ``rs.get_json(KEY) or []``, and `get_json` SWALLOWS a
StoreReadError and returns None — so a mutation landing inside a store reconnect read an
EMPTY list, derived an empty list, and persisted it, destroying every tenant's entries with
no error and no signal. Its docstring records a strict read against production on
2026-07-30 returning "state_store: no connection (reconnect in progress)" while the
non-strict read of the same key returned []. The R-F2277 watchdog reconnects after 45s of
unhealth, so the window recurs.

**`crucix:dd:watchlist:public` was left on the exact same pattern.** Found while triaging
four unrelated red tests — the partial-fix shape: one path converted, its sibling not.

    add_public_watchlist_entity     get_json(...) or []  ->  insert  ->  set_json
    remove_public_watchlist_entity  get_json(...) or []  ->  filter  ->  set_json
    rescreen_public_watchlist       get_json(...) or []  ->  early-return "clean"

REMOVE IS THE WORST. On a swallowed StoreReadError it read [], wrote [] — WIPING the entire
public watchlist — and returned ``{"ok": True, "removed": 0, "count": 0}``. A success
receipt for a total deletion, indistinguishable from "that name was not on the list".

ADD had a second harm beyond the clobber: reading [] also defeats its own dedup check, so
every name looks new.

RE-SCREEN is not a clobber — it writes nothing — but it is the same false clean: it
returned "0 entities screened, no changes detected, no errors", a completed clean public
monitoring cycle that never ran.

ONE IMPLEMENTATION, NOT TWO. `_read_watchlist_or_skip` takes a key rather than being
copied, because a copy is how the two watchlists drifted apart in the first place.
"""
from __future__ import annotations

import asyncio

import pytest

from aria_service.intel import dd_orchestrator as o
import aria_service.intel.redis_store as rs


class _Store:
    """Fake store whose STRICT reader can be made to fail, which is the only way to
    exercise the branch that matters."""

    def __init__(self, data=None):
        self.d = dict(data or {})
        self.unreadable = False
        self.writes: list[tuple[str, object]] = []

    async def get_json(self, k):
        return self.d.get(k)

    async def get_json_strict(self, k):
        if self.unreadable:
            raise rs.StoreReadError(f"state_store: no connection (reconnect in progress) reading {k}")
        return self.d.get(k)

    async def set_json(self, k, v, ex=None, keepttl=False):
        self.writes.append((k, v))
        self.d[k] = v

    def watchlist_writes(self):
        """Writes to the watchlist keys only.

        The first cut asserted `writes == []` and failed against CORRECT code: the
        R-F3506 skip path emits a §21a brain-wiring signal, which is itself a
        `set_json` to `crucix:aria:brain_hook:stats`. Asserting "nothing was written
        anywhere" makes the honest failure-wiring look like the clobber.
        """
        return [(k, v) for k, v in self.writes
                if k in (o.PUBLIC_WATCHLIST_KEY, o.WATCHLIST_KEY)]


@pytest.fixture
def store(monkeypatch):
    s = _Store({o.PUBLIC_WATCHLIST_KEY: [
        {"name": "Alpha Corp", "scope": "system_public"},
        {"name": "Beta Ltd", "scope": "system_public"},
        {"name": "Gamma SA", "scope": "system_public"},
    ]})
    for fn in ("get_json", "get_json_strict", "set_json"):
        monkeypatch.setattr(rs, fn, getattr(s, fn))
    return s


# ── REMOVE: the total wipe reported as success ──────────────────────────────

def test_capability_remove_does_not_wipe_the_list_when_the_store_is_unreadable(store):
    store.unreadable = True
    res = asyncio.run(o.remove_public_watchlist_entity("Beta Ltd"))

    assert store.watchlist_writes() == [], (
        "the public watchlist was REWRITTEN from an unreadable read — every entry "
        f"destroyed: {store.watchlist_writes()}")
    assert res.get("ok") is False, (
        "reporting ok=True after failing to read is the success-receipt-for-a-deletion "
        f"defect: {res}")
    assert res.get("error") == "store_unreadable"
    assert res.get("removed") == 0


def test_capability_a_normal_removal_still_works(store):
    res = asyncio.run(o.remove_public_watchlist_entity("Beta Ltd"))
    assert res["ok"] is True and res["removed"] == 1 and res["count"] == 2
    names = [w["name"] for w in store.d[o.PUBLIC_WATCHLIST_KEY]]
    assert names == ["Alpha Corp", "Gamma SA"]


def test_removing_an_absent_name_is_distinguishable_from_a_failed_read(store):
    """Both used to return removed=0. One means "not on the list", the other means
    "the list was just deleted" — they must never look the same."""
    ok = asyncio.run(o.remove_public_watchlist_entity("Not On The List Ltd"))
    store.unreadable = True
    failed = asyncio.run(o.remove_public_watchlist_entity("Not On The List Ltd"))
    assert ok["ok"] is True and ok["removed"] == 0
    assert failed["ok"] is False and failed["removed"] == 0
    assert ok.get("error") is None and failed.get("error") == "store_unreadable"


# ── ADD: the clobber, and the dedup it silently defeats ─────────────────────

def test_capability_add_does_not_replace_the_list_with_a_single_entry(store):
    store.unreadable = True
    res = asyncio.run(o.add_public_watchlist_entity("Delta Holdings plc"))

    assert store.watchlist_writes() == [], (
        "a one-entry list was persisted over the whole public watchlist: "
        f"{store.watchlist_writes()}")
    assert res.get("ok") is False and res.get("error") == "store_unreadable"


def test_capability_a_normal_add_still_works(store):
    res = asyncio.run(o.add_public_watchlist_entity("Delta Holdings plc"))
    assert res["ok"] is True and res["count"] == 4
    assert store.d[o.PUBLIC_WATCHLIST_KEY][0]["name"] == "Delta Holdings plc"


def test_an_unreadable_store_must_not_make_an_existing_name_look_new(store):
    """The second harm in add: reading [] defeats its own dedup loop, so a duplicate
    entry would be created for a name already curated."""
    store.unreadable = True
    res = asyncio.run(o.add_public_watchlist_entity("Alpha Corp"))
    assert res.get("ok") is False, (
        "an already-public name was accepted as new because the read returned empty")
    assert res.get("note") != "already public"


# ── RE-SCREEN: a clean cycle that never ran ─────────────────────────────────

def test_capability_an_unreadable_public_watchlist_is_not_a_clean_screen(store):
    store.unreadable = True
    res = asyncio.run(o.rescreen_public_watchlist())

    assert res["entities_screened"] == 0
    assert res["changes_detected"] == []
    # ...and, unlike before, it SAYS it did not run.
    assert res.get("screened") is False, (
        "an unreadable store still reports a completed clean monitoring cycle")
    assert res["errors"], "a screening that could not run must report an error"
    assert "unreadable" in str(res["errors"]).lower()
    assert "NOT a clean cycle" in res.get("note", "")


def test_a_genuinely_empty_public_watchlist_is_not_an_error(store):
    """Direction check: empty and unreadable are different states, and an empty list is
    a legitimate clean answer — over-warning is how a real warning gets ignored."""
    store.d[o.PUBLIC_WATCHLIST_KEY] = []
    res = asyncio.run(o.rescreen_public_watchlist())
    assert res["entities_screened"] == 0
    assert res["errors"] == []
    assert res.get("screened") is not False


# ── the structural property ─────────────────────────────────────────────────

def test_there_is_one_strict_reader_not_a_copy():
    """A second copy of the strict-read-or-skip logic is how the private and public
    watchlists drifted apart. The key is a parameter."""
    import inspect
    src = inspect.getsource(o._read_watchlist_or_skip)
    assert "key: str = WATCHLIST_KEY" in src, "the reader is not key-parameterised"
    assert "get_json_strict(key)" in src, (
        "the reader still hardcodes a key — the public path is reading the private one")


def test_no_read_modify_write_on_either_watchlist_key_uses_the_swallowing_reader():
    """THE CLASS GUARD. Any `get_json(<watchlist key>)` whose function also writes that
    key is the defect this closes. `get_public_watchlist` is a PURE read (no write) and
    is deliberately exempt — it cannot clobber and asserts nothing about screening."""
    import pathlib
    import re
    src = (pathlib.Path(o.__file__)).read_text(encoding="utf-8", errors="replace")

    offenders = []
    for m in re.finditer(r"async def (\w+)\(", src):
        start = m.end()
        nxt = src.find("\nasync def ", start)
        body = src[start: nxt if nxt != -1 else len(src)]
        for key in ("PUBLIC_WATCHLIST_KEY", "WATCHLIST_KEY"):
            reads_loose = re.search(rf"rs\.get_json\(\s*{key}\s*\)", body)
            writes = re.search(rf"rs\.set_json\(\s*{key}\s*,", body)
            if reads_loose and writes:
                offenders.append(f"{m.group(1)} read-modify-writes {key} non-strictly")
    assert not offenders, (
        "a swallowing read feeds a write — the R-F3506/R-F3520 clobber is back: "
        + "; ".join(offenders))


# ── R-F3520: the STALE-STUB class, which is how this was found ──────────────

def test_no_store_fake_stubs_get_json_without_the_strict_reader():
    """THE CLASS GUARD, and the reason this R-number exists at all.

    R-F3506 moved every watchlist read-modify-write from `get_json` to
    `get_json_strict` and did not update the store fakes in the suite. A fake that
    patches only `get_json` is BYPASSED by the strict path, which then reads the real
    (empty) store — so `rescreen_watchlist` returns early and the test sees
    ``{"entities_screened": 0, "changes_detected": [], "errors": []}``.

    That is the worst possible failure shape: a PLAUSIBLE result, not an error. Nine
    tests across four files went red reading as "watchlist change detection is broken"
    when the engine was fine, and they sat in the baseline as unexplained failures:

        test_rf2748_alert_classification        (4)
        test_rf2559_public_watchlist            (3, surfaced by R-F3520's own change)
        test_rf798_rescreen_per_entity_timeout  (4)
        test_rf2746_rescreen_lock               (1)

    A module that patches `redis_store.get_json` is claiming to fake the store's read
    surface. If it does not also fake the strict reader, it fakes half of it.
    """
    import pathlib
    import re

    tests_dir = pathlib.Path(__file__).resolve().parent
    offenders = []
    for path in sorted(tests_dir.glob("test_*.py")):
        text = path.read_text(encoding="utf-8", errors="replace")
        # Only files that actually REPLACE the module attribute — a mere mention of the
        # name is not a stub, and counting those is how a scanner reaches hundreds of
        # meaningless hits.
        patches_loose = re.search(
            r"""(setattr\(\s*\w+\s*,\s*["']get_json["']|["']get_json["']\s*,\s*\w+\s*\))""",
            text)
        if not patches_loose:
            continue
        if "get_json_strict" in text:
            continue
        # ...and only when the file drives something that actually reads strictly.
        if not re.search(r"rescreen_watchlist|_read_watchlist_or_skip|add_to_watchlist"
                         r"|remove_from_watchlist|get_watchlist|public_watchlist", text):
            continue
        offenders.append(path.name)

    assert not offenders, (
        "these fakes stub `get_json` but not `get_json_strict`, while driving a path "
        "that reads strictly — they will fail with a plausible empty result instead of "
        "an error: " + ", ".join(offenders))
