"""R-F903 + R-F904 — stage_improvement de-dup + truncation guard.

Live 2026-05-26: /api/aria/self/staged held 50 entries that were only 4 UNIQUE
fixes (churned 20/17/9/4×). The coder re-stages the same fix every cycle because
the underlying gap recurs and nothing collapses identical proposals → R-F903
de-dup (return the existing entry instead of appending a duplicate).

Worse: all 4 unique "fixes" were CATASTROPHIC truncated full-file replacements.
The fixer LLM can't emit a 4087-line file in its output budget, so it returned a
syntactically-valid 164-line STUB that would delete 96% of researcher.py (also
routes/aria.py 19443→208, neural_memory.py 1447→3). `_validate_by_path` only
checks syntax, not preservation, so they passed review-as-valid and would have
destroyed the modules if auto-deployed. R-F904 rejects any full-file replacement
that shrinks a substantial file below half its current line count.
"""
from __future__ import annotations

import asyncio
import importlib
import os

# A real modifiable, non-constitution file (auto-deploy logic stays normal).
MOD_FILE = "aria_service/intel/knowledge.py"


class _FakeRS:
    def __init__(self):
        self.store = {}

    async def get_json(self, key, *a, **kw):
        return self.store.get(key)

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value
        return True


def _fresh_si():
    os.environ.pop("ARIA_SELF_IMPROVE_AUTO_DEPLOY", None)
    import aria_service.intel.self_improve as _si
    importlib.reload(_si)
    return _si


def _wire(monkeypatch, si, root):
    fake = _FakeRS()
    monkeypatch.setattr(si, "rs", fake)
    monkeypatch.setattr(si, "_root", root)

    async def _noop_log(*a, **kw):
        return None
    monkeypatch.setattr(si, "_log_improvement", _noop_log)
    return fake


def _write_target(tmp_path, n_lines):
    target = tmp_path / MOD_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(f"x{i} = {i}" for i in range(n_lines)) + "\n", encoding="utf-8")
    return target


# ── R-F904 truncation guard ───────────────────────────────────────────────

def test_truncation_stub_rejected(tmp_path, monkeypatch):
    """The live failure: a 3-line stub replacing a 100-line file is rejected."""
    si = _fresh_si()
    fake = _wire(monkeypatch, si, tmp_path)
    _write_target(tmp_path, 100)
    res = asyncio.run(si.stage_improvement(
        MOD_FILE, "x = 1\ny = 2\nz = 3\n", "bug_fix", "stub", "r"))
    assert res.get("staged") is False, res
    assert res.get("truncation_guard") is True
    # nothing entered the queue
    assert not fake.store.get(si.STAGED_KEY)


def test_legit_full_replacement_accepted(tmp_path, monkeypatch):
    """A normal edit (99 of 100 lines, >50%) still stages."""
    si = _fresh_si()
    _wire(monkeypatch, si, tmp_path)
    _write_target(tmp_path, 100)
    body = "\n".join(f"x{i} = {i}" for i in range(99)) + "\n"
    res = asyncio.run(si.stage_improvement(MOD_FILE, body, "bug_fix", "edit", "r"))
    assert res.get("staged") is True, res


def test_small_file_not_size_guarded(tmp_path, monkeypatch):
    """Files under the 40-line floor aren't size-guarded (legit tiny modules)."""
    si = _fresh_si()
    _wire(monkeypatch, si, tmp_path)
    _write_target(tmp_path, 20)
    res = asyncio.run(si.stage_improvement(MOD_FILE, "x = 1\n", "bug_fix", "tiny", "r"))
    assert res.get("staged") is True, res


# ── R-F903 de-dup ─────────────────────────────────────────────────────────

def test_identical_proposal_deduped(tmp_path, monkeypatch):
    """Re-staging identical (file, content) returns the existing entry, no dup."""
    si = _fresh_si()
    fake = _wire(monkeypatch, si, tmp_path)  # no file on disk → size guard inert
    body = "def f():\n    return 1\n"
    r1 = asyncio.run(si.stage_improvement(MOD_FILE, body, "bug_fix", "first", "r"))
    r2 = asyncio.run(si.stage_improvement(MOD_FILE, body, "bug_fix", "second", "r"))
    assert r1.get("staged") is True
    assert r2.get("staged") is True
    assert r2.get("duplicate") is True
    assert r2.get("id") == r1.get("id")            # returns the SAME entry
    assert len(fake.store[si.STAGED_KEY]) == 1, "de-dup failed: duplicate appended"


def test_distinct_content_not_deduped(tmp_path, monkeypatch):
    """Genuinely different content still stages as a separate entry."""
    si = _fresh_si()
    fake = _wire(monkeypatch, si, tmp_path)
    r1 = asyncio.run(si.stage_improvement(MOD_FILE, "def f():\n    return 1\n", "bug_fix", "a", "r"))
    r2 = asyncio.run(si.stage_improvement(MOD_FILE, "def f():\n    return 2\n", "bug_fix", "b", "r"))
    assert r1.get("duplicate") is not True
    assert r2.get("duplicate") is not True
    assert len(fake.store[si.STAGED_KEY]) == 2


# ── R-F904 deploy-side guard (defense-in-depth) ───────────────────────────

def test_deploy_side_truncation_guard(tmp_path, monkeypatch):
    """An item staged BEFORE the stage guard existed still can't be DEPLOYED
    if it would shrink the file below half — closes the manual-deploy footgun
    for the 50 destructive entries already in the live store."""
    si = _fresh_si()
    fake = _wire(monkeypatch, si, tmp_path)
    target = _write_target(tmp_path, 100)
    # hand-insert a destructive stub directly (bypassing the stage guard, as the
    # pre-guard live entries did).
    fake.store[si.STAGED_KEY] = [{
        "id": "junk1", "file": MOD_FILE, "change_type": "bug_fix",
        "description": "stub", "reasoning": "", "new_content": "x = 1\n",
        "staged_at": 0, "auto_deployable": False, "status": "staged",
    }]
    res = asyncio.run(si.deploy_improvement("junk1"))
    assert res.get("blocked") is True, res
    assert res.get("truncation_guard") is True, res
    # the real file is untouched
    assert target.read_text(encoding="utf-8").count("\n") + 1 == 101
