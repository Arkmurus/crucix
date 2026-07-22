"""R-F2852 — a state-store timeout must never wipe durable topic mastery.

Capability test (CLAUDE.md §3c): the broken path is the *boot sequence*, not a
helper. On a slow boot the mastery read times out, and the boot-time seeder then
overwrites the durable key with a synthetic baseline. These tests drive
``_load_mastery`` -> ``seed_baseline_mastery`` (the real ``main.py:1401`` call)
against a store stub that raises ``StoreReadError``, and assert on what the
store actually received.

Live evidence for the trigger (2026-07-22, aria-intel):
    state_store.get(crucix:aria:student:mastery) timed out after 5s

This mirrors R-F2664, which fixed the identical defect on the regional-mastery
twin. The negative control (``test_nonstrict_read_would_have_clobbered``) pins
the pre-fix behaviour so the guard cannot be removed silently.
"""

from __future__ import annotations

import pytest

from aria_service.intel import student
from aria_service.intel.redis_store import StoreReadError


REAL_MASTERY = {
    "osint": {"score": 0.86, "samples": 240, "correct": 210, "wrong": 30,
              "last_practiced": 1_700_000_000},
    "sanctions": {"score": 0.78, "samples": 180, "correct": 150, "wrong": 30,
                  "last_practiced": 1_700_000_000},
}


@pytest.fixture(autouse=True)
def _reset_student_globals():
    """Mastery caches are module globals; isolate every test."""
    student._mastery_cache = None
    student._mastery_dirty = False
    yield
    student._mastery_cache = None
    student._mastery_dirty = False


class _Store:
    """Records writes; can be told to fail reads the way a wedged store does."""

    def __init__(self, *, read_raises: bool, data=None):
        self.read_raises = read_raises
        self.data = data
        self.writes: list[tuple[str, dict]] = []

    async def get_json_strict(self, key):
        if self.read_raises:
            raise StoreReadError(f"state_store.get({key}) timed out after 5s")
        return self.data

    async def get_json(self, key):
        # Non-strict contract: swallows store failure to None (redis_store:299-303)
        if self.read_raises:
            return None
        return self.data

    async def set_json(self, key, obj, ex=None, keepttl=False):
        self.writes.append((key, obj))


# ── The capability test: the boot sequence must not wipe mastery ────────────

@pytest.mark.asyncio
async def test_boot_seeder_cannot_clobber_mastery_when_store_read_times_out(monkeypatch):
    """The operator-visible symptom: real mastery replaced by a synthetic baseline."""
    store = _Store(read_raises=True)
    monkeypatch.setattr(student, "rs", store)

    # This is exactly what main.py:1401 does at every boot.
    seeded = await student.seed_baseline_mastery()

    assert store.writes == [], (
        f"DURABLE MASTERY CLOBBERED: seeder wrote {store.writes!r} after a "
        f"failed read (seeded={seeded})"
    )
    # The cache must be left uninitialised so the next call retries the read.
    assert student._mastery_cache is None, (
        "poisoned scaffold was cached for the process lifetime"
    )


@pytest.mark.asyncio
async def test_nonstrict_read_would_have_clobbered(monkeypatch):
    """Negative control — proves the guard is what prevents the wipe.

    Reproduces the pre-R-F2852 loader inline. If this stops clobbering, the
    failure mode changed and the positive test above may pass vacuously.
    """
    store = _Store(read_raises=True)
    monkeypatch.setattr(student, "rs", store)

    async def _legacy_load_mastery():
        # Pre-fix body: non-strict read, poisons the cache on failure.
        raw = await store.get_json(student.MASTERY_KEY)
        student._mastery_cache = raw if isinstance(raw, dict) else {
            t: {"score": student.INITIAL_MASTERY, "samples": 0, "correct": 0,
                "wrong": 0, "last_practiced": 0}
            for t in student.TOPICS
        }
        return student._mastery_cache

    monkeypatch.setattr(student, "_load_mastery", _legacy_load_mastery)
    await student.seed_baseline_mastery()

    assert store.writes, "pre-fix clobber no longer reproduces — re-check the chain"
    written_key, written = store.writes[-1]
    assert written_key == student.MASTERY_KEY
    assert all(m["samples"] > 0 for m in written.values()), (
        "expected the synthetic seeded baseline that overwrote real data"
    )


# ── Healthy-store behaviour must be unchanged ──────────────────────────────

@pytest.mark.asyncio
async def test_real_mastery_is_loaded_and_not_reseeded(monkeypatch):
    """A healthy read must return real data, and the seeder must skip it."""
    store = _Store(read_raises=False, data=dict(REAL_MASTERY))
    monkeypatch.setattr(student, "rs", store)

    mastery = await student._load_mastery()
    assert mastery["osint"]["score"] == 0.86
    assert mastery["osint"]["samples"] == 240

    await student.seed_baseline_mastery()
    for key, obj in store.writes:
        assert obj["osint"]["samples"] >= 240, "real samples were reset"
        assert obj["osint"]["score"] == pytest.approx(0.86), "real score was overwritten"


@pytest.mark.asyncio
async def test_genuinely_absent_key_still_scaffolds_and_caches(monkeypatch):
    """Absent (None) is different from failed — it may cache a scaffold."""
    store = _Store(read_raises=False, data=None)
    monkeypatch.setattr(student, "rs", store)

    mastery = await student._load_mastery()
    assert student._mastery_cache is not None, "absent key should cache a scaffold"
    assert all(m["samples"] == 0 for m in mastery.values())


@pytest.mark.asyncio
async def test_save_is_a_noop_while_cache_uninitialised(monkeypatch):
    """The mechanism the guard relies on: _save_mastery early-returns on None."""
    store = _Store(read_raises=True)
    monkeypatch.setattr(student, "rs", store)

    await student._load_mastery()          # deferred; leaves cache None
    student._mastery_dirty = True          # even if something marks it dirty
    await student._save_mastery()

    assert store.writes == [], "save wrote despite an uninitialised cache"


@pytest.mark.asyncio
async def test_deferred_load_returns_usable_scaffold(monkeypatch):
    """Callers index the result, so a deferred load must not return None/{}."""
    store = _Store(read_raises=True)
    monkeypatch.setattr(student, "rs", store)

    mastery = await student._load_mastery()
    assert isinstance(mastery, dict) and mastery, "deferred load returned unusable value"
    for topic in student.TOPICS:
        assert topic in mastery, f"caller would KeyError on {topic}"


@pytest.mark.asyncio
async def test_retry_after_transient_failure_gets_real_data(monkeypatch):
    """Because nothing was cached, the next read must pick up real mastery."""
    store = _Store(read_raises=True)
    monkeypatch.setattr(student, "rs", store)

    await student._load_mastery()
    store.read_raises = False
    store.data = dict(REAL_MASTERY)

    mastery = await student._load_mastery()
    assert mastery["osint"]["samples"] == 240, "retry did not recover real mastery"
