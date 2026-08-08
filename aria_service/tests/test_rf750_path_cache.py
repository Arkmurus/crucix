"""R-F750 (2026-05-20) — cache _project_relative_path to drop pathlib.realpath syscall per emit.

Wedge round 10 (wedge_675_1779301744.log) captured a 6.96s main-loop
stall with the main thread parked at:
  pathlib.realpath
  → error_log_handler._project_relative_path (line 59)
  → ErrorLedgerHandler.emit
  → logger.warning
  → autonomy_surface._safe (line 575)

Root cause: every WARNING+ aria.* log emit called
`Path(pathname).resolve()`. `resolve()` invokes os.path.realpath which
does a syscall chain walking the filesystem (lstat per path component,
symlink follow). Under log bursts this dominated emit latency.

R-F750 caches the result keyed on `pathname`. Stable through process
lifetime because module file paths don't change post-import. Bounded
at 2000 entries (clears on overflow rather than LRU — simpler and
realistic max is ~500 distinct emit sites).
"""
from __future__ import annotations

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_cache_hit_returns_same_result():
    """R-F750: same pathname → same cached string."""
    from aria_service.intel import error_log_handler as elh

    # Clear to ensure fresh state
    elh._PATH_CACHE.clear()
    p = "/app/aria_service/intel/autonomy_surface.py"
    a = elh._project_relative_path(p)
    b = elh._project_relative_path(p)
    assert a == b
    # And the cache now has the entry
    assert p in elh._PATH_CACHE


def test_cache_avoids_filesystem_lookup_on_hit(monkeypatch):
    """R-F750: a second call must NOT hit Path.resolve again."""
    from aria_service.intel import error_log_handler as elh
    from pathlib import Path

    elh._PATH_CACHE.clear()
    p = "/some/test/path.py"

    # Prime the cache with a deterministic value (bypass real resolve)
    elh._PATH_CACHE[p] = "test/path.py"

    # Now monkeypatch Path.resolve to raise — if called, the test fails.
    resolve_calls = []
    original_resolve = Path.resolve

    def _no_resolve(self, *args, **kwargs):
        resolve_calls.append(str(self))
        raise RuntimeError("R-F750 regression: resolve called on cache hit")

    monkeypatch.setattr(Path, "resolve", _no_resolve)

    out = elh._project_relative_path(p)
    assert out == "test/path.py"
    assert resolve_calls == [], (
        f"R-F750: cache hit should not call Path.resolve, but it was called "
        f"with {resolve_calls}"
    )


def test_empty_path_returns_empty_without_caching():
    """R-F750: empty pathname returns empty (no cache pollution)."""
    from aria_service.intel import error_log_handler as elh
    elh._PATH_CACHE.clear()
    assert elh._project_relative_path("") == ""
    assert elh._project_relative_path(None) == ""
    assert "" not in elh._PATH_CACHE
    assert None not in elh._PATH_CACHE


def test_cache_bounded_at_max():
    """R-F750: cache size cap prevents unbounded growth from synthetic
    paths (e.g. <string> sources from exec)."""
    from aria_service.intel import error_log_handler as elh

    elh._PATH_CACHE.clear()
    max_size = elh._PATH_CACHE_MAX

    # Fill past the cap
    for i in range(max_size + 50):
        elh._project_relative_path(f"/synthetic/path_{i}.py")

    # Cache must not exceed the cap by more than one fresh batch
    assert len(elh._PATH_CACHE) <= max_size + 1, (
        f"R-F750: cache grew to {len(elh._PATH_CACHE)} entries (cap {max_size})"
    )


def test_non_project_path_falls_back_to_basename():
    """R-F750: third-party / stdlib paths return basename — fallback
    behavior preserved through the cache."""
    from aria_service.intel import error_log_handler as elh
    elh._PATH_CACHE.clear()

    # A path that's NOT under the project root
    out = elh._project_relative_path("/usr/lib/python3/site-packages/foo/bar.py")
    # Should fall back to the basename ("bar.py")
    assert out == "bar.py"


def test_project_path_returns_relative_form():
    """R-F750: a path inside the project returns a relative POSIX form."""
    from aria_service.intel import error_log_handler as elh
    elh._PATH_CACHE.clear()

    # This file itself is in the project — emulate by passing its path.
    # We use __file__ via inspect to get the real path.
    import inspect
    here = inspect.getfile(elh)
    out = elh._project_relative_path(here)
    # Should NOT be empty, and should NOT be an absolute path
    assert out
    assert not out.startswith("/")
    assert "error_log_handler" in out


def test_rf750_marker_present():
    """R-F750: marker comment present for revert detection."""
    from aria_service.intel import error_log_handler as elh
    import inspect
    src = module_source(elh)
    assert "R-F750" in src
    assert "_PATH_CACHE" in src
