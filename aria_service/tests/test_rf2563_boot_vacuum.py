"""R-F2563 — boot-time one-shot VACUUM of the hot state_store DB."""
from __future__ import annotations
import asyncio, os, sqlite3
from aria_service.intel import state_store as ss


def _bloat(path):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT)")
    con.executemany("INSERT INTO state VALUES (?,?)", [(f"k{i}", "x" * 5000) for i in range(6000)])
    con.commit()
    con.execute("DELETE FROM state")   # leaves ~30MB of free pages
    con.commit()
    con.close()


def _run(path):
    async def go():
        return await ss.maybe_boot_vacuum(path)
    return asyncio.run(go())


def test_boot_vacuum_reclaims_free_pages(monkeypatch, tmp_path):
    p = str(tmp_path / "bloat.db")
    _bloat(p)
    before = os.path.getsize(p)
    monkeypatch.setattr(ss, "_VACUUM_MIN_FREE_MB", 0.001)   # force it to fire on the small test DB
    monkeypatch.delenv("ARIA_STATE_VACUUM_ON_BOOT", raising=False)
    r = _run(p)
    assert r["vacuumed"] is True
    assert os.path.getsize(p) < before          # DB shrank


def test_boot_vacuum_disabled_by_flag(monkeypatch, tmp_path):
    p = str(tmp_path / "d.db")
    _bloat(p)
    monkeypatch.setenv("ARIA_STATE_VACUUM_ON_BOOT", "0")
    assert _run(p)["reason"] == "disabled"


def test_boot_vacuum_skips_below_threshold(monkeypatch, tmp_path):
    p = str(tmp_path / "small.db")
    con = sqlite3.connect(p); con.execute("CREATE TABLE state (key TEXT, value TEXT)"); con.commit(); con.close()
    monkeypatch.delenv("ARIA_STATE_VACUUM_ON_BOOT", raising=False)   # default 300MB threshold
    assert _run(p)["reason"] == "below_threshold"


def test_boot_vacuum_no_db_file_never_creates_one(monkeypatch, tmp_path):
    """Existence guard (R-F2563 blocker fix): a missing DB must be a no-op AND must not
    create a stray file (would happen when the backend isn't sqlite)."""
    p = str(tmp_path / "does_not_exist.db")
    monkeypatch.delenv("ARIA_STATE_VACUUM_ON_BOOT", raising=False)
    assert _run(p)["reason"] == "no_db_file"
    assert not os.path.exists(p)          # never created a stray DB


def test_boot_vacuum_default_path_from_env(monkeypatch, tmp_path):
    """The public entrypoint resolves the DB path from ARIA_STATE_DB_PATH when called
    with no arg (how the lifespan invokes it)."""
    p = str(tmp_path / "bloat.db")
    _bloat(p)
    monkeypatch.setenv("ARIA_STATE_DB_PATH", p)
    monkeypatch.setattr(ss, "_VACUUM_MIN_FREE_MB", 0.001)
    monkeypatch.delenv("ARIA_STATE_VACUUM_ON_BOOT", raising=False)
    async def go():
        return await ss.maybe_boot_vacuum()   # no arg → resolves from env
    r = asyncio.run(go())
    assert r["vacuumed"] is True


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-v"]))
