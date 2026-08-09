"""R-F3530 — a second chromadb client on the SAME PATH kept the SIGSEGV alive.

R-F3527 serialised construction inside `rag_store._get_client` and the crash-loop
CONTINUED. The post-fix faulthandler dump named a different site entirely:

    coding_rag_indexer.py:215 in _ensure
      chromadb/api/client.py:361 in get_or_create_collection
        chromadb/api/rust.py:313 -> 244 in create_collection      <- Rust core

`coding_rag_indexer._get_chromadb_client` documented the correct invariant —
"Reuses rag_store's PersistentClient so there's exactly one chromadb instance in the
process" — and then VIOLATED IT on the next line, falling back to
`chromadb.PersistentClient(path=RAG_PATH)`: a second client on the same path.

The two modules hold DIFFERENT locks (`rag_store._client_build_lock` vs this module's
`_init_lock`), so R-F3527's serialisation could not reach across. chromadb keys systems
by path in `SharedSystemClient._identifier_to_system`; two constructions for one path
tear down / re-enter a system the other is inside, and the Rust core dereferences freed
state.

THE LESSON THIS FILE ENCODES, beyond the one bug: **a lock only protects the call sites
that take it.** Fixing the site the first dump pointed at was necessary and not
sufficient — the class is "how many places construct a chromadb client", and the answer
has to be one. Hence the tree-wide guard below rather than another local fix.

WHY DEGRADING IS CORRECT. Every reason `rag_store._get_client()` returns None — chromadb
absent, the R-F2855 corrupt-store breaker tripped, the R-F2151 cooldown armed — is a
reason NOT to build a rival client. The old fallback turned "RAG is deliberately
disabled" into "build another one anyway", which is the worst possible response to a
tripped breaker.
"""
from __future__ import annotations

import ast
import pathlib

import pytest

from aria_service.intel import coding_rag_indexer as cri

# R-F3795 — these two monkeypatch chromadb.PersistentClient to prove no SECOND
# client is built, so they need chromadb importable. Absent here (no win-arm64
# wheel, §16); present in the Linux image. ENVIRONMENT, not a code defect.
from ._env_probe import requires_module


def test_capability_it_returns_the_shared_client(monkeypatch):
    sentinel = object()
    monkeypatch.setattr("aria_service.intel.rag_store._get_client",
                        lambda: sentinel)
    assert cri._get_chromadb_client() is sentinel


@requires_module("chromadb")
def test_capability_no_client_means_DEGRADED_not_a_second_client(monkeypatch):
    """THE DEFECT. rag_store declining to give a client is a REASON not to build one."""
    monkeypatch.setattr("aria_service.intel.rag_store._get_client", lambda: None)

    built = []

    def _boom(*a, **kw):
        built.append(kw.get("path") or (a[0] if a else "?"))
        return object()

    import chromadb
    monkeypatch.setattr(chromadb, "PersistentClient", _boom)

    assert cri._get_chromadb_client() is None
    assert built == [], (
        f"a SECOND chromadb client was constructed at {built} — this is the "
        "same-path use-after-free that crash-looped production")


@requires_module("chromadb")
def test_capability_an_exception_from_rag_store_also_degrades(monkeypatch):
    def _raise():
        raise RuntimeError("store unavailable")

    monkeypatch.setattr("aria_service.intel.rag_store._get_client", _raise)

    built = []
    import chromadb
    monkeypatch.setattr(chromadb, "PersistentClient",
                        lambda *a, **kw: built.append(1) or object())

    assert cri._get_chromadb_client() is None
    assert built == [], "an error path must not construct a rival client either"


def _persistent_client_calls(path: pathlib.Path) -> list[str]:
    """Real `chromadb.PersistentClient(...)` CALL sites, via AST.

    Text matching was the first cut and it flagged this module's own DOCSTRING — the
    prose that explains the bug necessarily contains the call. A scanner that cannot
    tell code from prose about code is the R-F3449 lesson repeating: verify the
    instrument. AST sees Call nodes only, so comments, docstrings and the
    subprocess-probe template string are all invisible to it.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr == "PersistentClient":
            out.append(f"{path.name}:{node.lineno}")
        elif isinstance(fn, ast.Name) and fn.id == "PersistentClient":
            out.append(f"{path.name}:{node.lineno}")
    return out


def test_the_scanner_can_tell_code_from_prose_about_code():
    """Guard the guard. This module's docstring quotes the very call it forbids; if the
    scanner flagged that, it would be un-narrowable and get deleted."""
    assert _persistent_client_calls(pathlib.Path(cri.__file__)) == []

    probe = pathlib.Path(__file__).parent / "_rf3530_probe.py.txt"
    probe.write_text(
        '"""A docstring mentioning chromadb.PersistentClient(path=x)."""\n'
        "# and a comment: chromadb.PersistentClient(path=x)\n"
        "TEMPLATE = 'chromadb.PersistentClient(path=inner)'\n"
        "import chromadb\n"
        "c = chromadb.PersistentClient(path='real')\n",
        encoding="utf-8")
    try:
        found = _persistent_client_calls(probe)
        assert len(found) == 1, (
            "the scanner must find the ONE real call and ignore the docstring, the "
            f"comment and the string template — got {found}")
        assert found[0].endswith(":5"), f"wrong line matched: {found}"
    finally:
        probe.unlink(missing_ok=True)


def test_this_module_never_constructs_a_client():
    """The point is that no code PATH can reach a construction — a behaviour test only
    covers the paths it happens to drive."""
    hits = _persistent_client_calls(pathlib.Path(cri.__file__))
    assert not hits, f"coding_rag_indexer constructs its own client again: {hits}"


# ── the CLASS guard: one owner, tree-wide ───────────────────────────────────

def test_only_rag_store_may_construct_a_chromadb_client():
    """A lock only protects the call sites that take it.

    R-F3527 locked `rag_store._get_client` and the box kept crashing, because a second
    module built its own client under a different lock. The invariant is not "the build
    is locked" but "there is ONE builder". This fails the moment a third appears.
    """
    intel = pathlib.Path(cri.__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in sorted(intel.rglob("*.py")):
        if "tests" in path.parts:
            continue
        # rag_store is the single legitimate owner. (The PersistentClient inside its
        # R-F2856 probe TEMPLATE runs in a SUBPROCESS — a separate process, so it
        # cannot race this one's client.)
        if path.name == "rag_store.py":
            continue
        try:
            offenders.extend(_persistent_client_calls(path))
        except SyntaxError:
            continue
    assert not offenders, (
        "chromadb clients are constructed outside rag_store — two clients on one path "
        "segfault the Rust core: " + "; ".join(offenders))


def test_rag_store_remains_the_owner_and_is_still_serialised():
    """The other half: if rag_store ever loses its lock, this whole design is void."""
    from aria_service.intel import rag_store as rs
    import threading
    assert isinstance(rs._client_build_lock, type(threading.Lock()))
    src = pathlib.Path(rs.__file__).read_text(encoding="utf-8", errors="replace")
    assert "with _client_build_lock:" in src, "rag_store no longer serialises the build"
