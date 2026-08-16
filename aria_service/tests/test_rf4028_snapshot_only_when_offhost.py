"""R-F4028 (C-98) — the "off-host backup" must actually be off-host.

R-F334 (2026-05-11) added a sharded snapshot as the "Redis off-host backup
tier", and it genuinely was one. Then R-F745 (2026-05-20) flipped the default
state backend to sqlite and Upstash was cancelled (§6/§18), and nothing
revisited this. Measured live 2026-08-14:

    ARIA_STATE_BACKEND=sqlite
    REDIS_URL          (unset)

    SELECT COUNT(*), SUM(LENGTH(value)) FROM state WHERE key LIKE '%knowledge:shard%'
      -> 225 keys, 83,660,672 bytes          (31% of the entire state store)

So every 600 s `_flush_loop` gzipped the whole 533k-fact graph and wrote 83.7 MB
into `/data/aria_state.db` — **the same volume as the `/data/aria_knowledge.json`
it exists to back up**, roughly 500 MB/hour, plus a whole-graph gzip whose own
docstring records it producing 19-25 s wedges. A backup that shares a failure
domain with its original is not a backup; this was pure cost.

THE CONTRACT.
  - A REMOTE backend is off-host -> snapshot RUNS. This must keep working, or
    the fix quietly deletes the backup for everyone who still has one.
  - sqlite on the SAME volume as the canonical file -> SKIPPED. It protects
    nothing that volume loss would not already take.
  - sqlite on a DIFFERENT volume -> RUNS. Still a real second failure domain.
  - UNDETERMINABLE -> RUNS. This is the safety default and it is the opposite of
    C-95's: "I could not measure" must never silently stop backing data up. An
    unknown is a reason to keep the copy, not to drop it.
"""
import pytest

from aria_service.intel import knowledge


@pytest.fixture(autouse=True)
def _restore():
    saved_disk = knowledge._DISK_PATH
    saved_dev = knowledge._device_of
    yield
    knowledge._DISK_PATH = saved_disk
    knowledge._device_of = saved_dev


def _set_backend(monkeypatch, name):
    from aria_service.intel import redis_store
    monkeypatch.setattr(redis_store, "_BACKEND", name, raising=False)


def _set_devices(monkeypatch, knowledge_dev, state_dev):
    """Patch device identity so the test is deterministic cross-platform."""
    def _dev(path):
        p = str(path)
        return state_dev if "state" in p else knowledge_dev
    monkeypatch.setattr(knowledge, "_device_of", _dev)


# ── a remote backend is genuinely off-host ─────────────────────────────────

@pytest.mark.parametrize("backend", ["upstash", "redis"])
def test_remote_backend_is_offhost(monkeypatch, backend):
    _set_backend(monkeypatch, backend)
    assert knowledge._snapshot_target_is_offhost() is True, (
        "R-F4028 must NOT disable the snapshot for a real remote backend — "
        "that would delete a working backup."
    )


# ── the measured production case ───────────────────────────────────────────

def test_sqlite_on_the_same_volume_is_not_offhost(monkeypatch, tmp_path):
    _set_backend(monkeypatch, "sqlite")
    knowledge._DISK_PATH = str(tmp_path / "aria_knowledge.json")
    monkeypatch.setattr("aria_service.intel.state_store._DB_PATH",
                        tmp_path / "aria_state.db", raising=False)
    _set_devices(monkeypatch, knowledge_dev=42, state_dev=42)

    assert knowledge._snapshot_target_is_offhost() is False, (
        "83.7 MB every 600 s onto the volume it backs up is not a backup"
    )


def test_sqlite_on_a_different_volume_is_offhost(monkeypatch, tmp_path):
    _set_backend(monkeypatch, "sqlite")
    knowledge._DISK_PATH = str(tmp_path / "aria_knowledge.json")
    monkeypatch.setattr("aria_service.intel.state_store._DB_PATH",
                        tmp_path / "aria_state.db", raising=False)
    _set_devices(monkeypatch, knowledge_dev=42, state_dev=99)

    assert knowledge._snapshot_target_is_offhost() is True, (
        "a different volume is a real second failure domain — keep the copy"
    )


# ── the safety default, and it is the OPPOSITE of C-95's ───────────────────

def test_undeterminable_is_unknown_not_false(monkeypatch, tmp_path):
    _set_backend(monkeypatch, "sqlite")
    knowledge._DISK_PATH = str(tmp_path / "aria_knowledge.json")
    monkeypatch.setattr("aria_service.intel.state_store._DB_PATH",
                        tmp_path / "aria_state.db", raising=False)
    monkeypatch.setattr(knowledge, "_device_of", lambda p: None)

    assert knowledge._snapshot_target_is_offhost() is None, (
        "could-not-measure must be None, never False — False stops a backup"
    )


def test_never_raises(monkeypatch):
    def _explode(*a, **kw):
        raise RuntimeError("boom")
    monkeypatch.setattr(knowledge, "_device_of", _explode)
    assert knowledge._snapshot_target_is_offhost() in (True, False, None)


# ── the decision the flush loop actually makes ─────────────────────────────

@pytest.mark.parametrize("offhost,should_run", [
    (True, True),
    (None, True),      # unknown -> keep backing up
    (False, False),
])
def test_flush_loop_runs_the_snapshot_only_when_it_is_a_backup(offhost, should_run):
    assert knowledge._should_snapshot(offhost) is should_run, (
        f"offhost={offhost} must {'run' if should_run else 'skip'} the snapshot"
    )


# ── the skip is a STEADY STATE, so it announces once ───────────────────────

def test_same_volume_skip_announces_once_per_process(monkeypatch):
    """A 600 s condition defeats a 300 s cooldown entirely.

    Without `once`, the skip would emit forever (~144 signals/day) — the
    ledger-filling flood CLAUDE.md records for sanctions_coverage_degraded.
    """
    seen = []
    monkeypatch.setattr(knowledge, "wire_success", lambda **kw: seen.append(kw))
    knowledge._reset_persistence_wire_state()
    try:
        for _ in range(20):
            knowledge._wire_persistence(
                source="knowledge:snapshot_skipped_same_volume",
                ok=True, once=True, summary="skipped",
            )
        assert len(seen) == 1, f"expected 1 signal, got {len(seen)}"
    finally:
        knowledge._reset_persistence_wire_state()
