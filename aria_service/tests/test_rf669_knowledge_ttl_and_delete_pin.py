"""R-F669 — defensive guards for ARIA's infinite-memory invariant.

CLAUDE.md §7: "ARIA has infinite memory. No TTL on knowledge."

Audit 2026-05-17 closing-session report flagged two latent regression
vectors that pass clean code review today but would silently delete
knowledge if a future commit slipped:

  1. state_store.sweep_expired() deletes entries with expires_at <= now.
     Nothing structurally prevents a future caller from passing ex= on
     a knowledge key. R-F669 adds a hard raise.

  2. search_index.db.delete_document() is defined but has zero active
     callers. If a future commit invokes it on knowledge-related data,
     ARIA silently forgets. R-F669 adds a pin test forbidding any
     non-test caller from using it.

Both guards live at the code level, not in runtime checks, so they fire
at PR-review time (when the assertion is cheap to fix), not at incident
time (when data is already gone).
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from aria_service.intel import state_store


# ── Guard 1: state_store.set / set_json refuse TTL on knowledge keys ──────

def test_rf669_infinite_key_prefixes_present():
    """The canonical knowledge prefixes must be in the guard list. If
    a future change drops one, this test fails."""
    expected = {
        "crucix:aria:knowledge",
        "crucix:aria:verified_intel",
        "crucix:aria:intel_ledger",
        "crucix:aria:neural",
    }
    actual = set(state_store._INFINITE_KEY_PREFIXES)
    missing = expected - actual
    assert not missing, (
        f"R-F669: knowledge prefix(es) {missing} missing from "
        f"_INFINITE_KEY_PREFIXES — TTL guard would not fire on them."
    )


@pytest.mark.parametrize("key", [
    "crucix:aria:knowledge",
    "crucix:aria:knowledge:shard:0",
    "crucix:aria:knowledge:meta",
    "crucix:aria:verified_intel",
    "crucix:aria:verified_intel:facts",
    "crucix:aria:intel_ledger",
    "crucix:aria:intel_ledger:signals",
    "crucix:aria:neural",
    "crucix:aria:neural_edges",
    "crucix:aria:neural_conflicts",
    "crucix:aria:neural:meta",
])
def test_rf669_is_infinite_key_matches_knowledge(key):
    assert state_store._is_infinite_key(key) is True, (
        f"R-F669: {key!r} should be classified as infinite (no-TTL)"
    )


@pytest.mark.parametrize("key", [
    "",
    "crucix:aria:student:mastery",        # mastery legitimately rotates (180d)
    "crucix:aria:code_knowledge",         # self-improve metadata, not knowledge proper
    "crucix:aria:cost:monthly:2026-05",   # cost stats (rolls monthly)
    "crucix:session:abc123",              # session blob (TTL fine)
    "robots:cache:example.com",           # robots.txt cache (TTL fine)
])
def test_rf669_is_infinite_key_excludes_non_knowledge(key):
    assert state_store._is_infinite_key(key) is False, (
        f"R-F669: {key!r} is NOT a knowledge key — guard would fire incorrectly"
    )


def test_rf669_set_raises_on_ttl_for_knowledge_key():
    """state_store.set('crucix:aria:knowledge:...', ..., ex=N) must raise."""
    async def runner():
        await state_store.set("crucix:aria:knowledge:fact_42", "x", ex=86400)
    with pytest.raises(ValueError, match=r"R-F669.*TTL.*knowledge"):
        asyncio.run(runner())


def test_rf669_set_raises_on_ttl_for_verified_intel():
    async def runner():
        await state_store.set("crucix:aria:verified_intel:facts", "x", ex=3600)
    with pytest.raises(ValueError, match=r"R-F669"):
        asyncio.run(runner())


def test_rf669_set_json_raises_on_ttl_for_knowledge_key():
    async def runner():
        await state_store.set_json(
            "crucix:aria:knowledge:something",
            {"fact": "x"},
            ex=86400,
        )
    with pytest.raises(ValueError, match=r"R-F669"):
        asyncio.run(runner())


def test_rf669_set_no_ttl_on_knowledge_key_is_allowed(monkeypatch, tmp_path):
    """Writing to a knowledge key WITHOUT a TTL must succeed — the guard
    only fires when ex= is non-None."""
    # Point state_store at a fresh DB for this test
    monkeypatch.setenv("ARIA_STATE_BACKEND", "sqlite")
    # The state_store module initialises lazily; the simplest isolation
    # is to monkeypatch the _conn None check via _ensure_open — but for
    # this guard test we only need to verify the no-raise path. Mock
    # _upsert to a no-op so we don't need a live DB.
    captured: dict = {}

    async def _fake_upsert(key, value, kind, expires_at, keepttl=False):
        captured["key"] = key
        captured["expires_at"] = expires_at

    monkeypatch.setattr(state_store, "_upsert", _fake_upsert)

    async def runner():
        # No raise expected — ex=None is the legitimate path
        await state_store.set("crucix:aria:knowledge:abc", "x")

    asyncio.run(runner())
    assert captured["key"] == "crucix:aria:knowledge:abc"
    assert captured["expires_at"] is None


def test_rf669_set_ttl_on_non_knowledge_key_is_allowed(monkeypatch):
    """A TTL on a non-knowledge key must NOT raise — only knowledge
    namespaces are guarded."""
    captured: dict = {}

    async def _fake_upsert(key, value, kind, expires_at, keepttl=False):
        captured["key"] = key
        captured["expires_at"] = expires_at

    monkeypatch.setattr(state_store, "_upsert", _fake_upsert)

    async def runner():
        await state_store.set("crucix:session:abc", "x", ex=3600)

    asyncio.run(runner())
    assert captured["key"] == "crucix:session:abc"
    assert captured["expires_at"] is not None  # TTL applied


# ── Guard 2: pin test — no non-test caller of delete_document ─────────────

def test_rf669_delete_document_has_no_production_callers():
    """search_index.db.delete_document is defined but has zero callers.
    R-F669 pins this so any future commit that invokes it from
    aria_service/ (outside tests/) trips the pin and reviewer is forced
    to justify the call.

    If a legitimate caller is later required, update this test's
    `_ALLOWED_CALLER_FILES` allowlist with explicit justification."""
    repo_root = Path(__file__).resolve().parent.parent  # aria_service/
    pat = re.compile(r"\b(?:db\.)?delete_document\(")

    offenders: list[tuple[str, int, str]] = []
    for py in repo_root.rglob("*.py"):
        # Skip test files — they need to exercise the function
        if "tests" in py.parts or py.name.startswith("test_"):
            continue
        # The definition itself in search_index/db.py is fine — it's not
        # a CALLER.
        if py.name == "db.py" and "search_index" in py.parts:
            continue
        try:
            text = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if pat.search(line):
                offenders.append((str(py.relative_to(repo_root)), lineno, line.strip()))

    assert not offenders, (
        "R-F669 pin: search_index.db.delete_document() has new callers — "
        "review whether they touch knowledge data. If safe, add the file "
        "to this test's allowlist with a comment justifying why deletion "
        "is legitimate.\n  Offenders:\n  "
        + "\n  ".join(f"{f}:{ln} → {src}" for f, ln, src in offenders)
    )


# ── Static guard: marker comment present ──────────────────────────────────

def test_rf669_marker_comment_in_state_store():
    src = (
        Path(__file__).resolve().parent.parent / "intel" / "state_store.py"
    ).read_text(encoding="utf-8")
    assert "R-F669" in src
    assert "CLAUDE.md §7" in src
