"""R-F855 — deploy-time constitutional integrity gate (L2 + L4).

The self-improve stage→deploy path had NO content check (R-F851 only gated
AUTO-deploy by file). A staged "failed attack" amendment that faked
verification tags was one POST /api/aria/self/deploy/{id} from going live.

R-F855 wires the existing ConstitutionalValidator into deploy_improvement:
every deploy's content is AST + weakening-pattern + tag-faking +
protected-file checked BEFORE the file is written. On violation the item is
quarantined (DISCARDED_KEY) + a HIGH operator pending_action is raised, and the
file is never touched. FAIL-CLOSED if the validator can't run.

Also pins two validator hardenings:
  - tag-faking patterns (the actual 2026-05-24 incident)
  - non-Python files no longer FATAL on ast.parse (would have blocked every
    legit .yaml/.mjs deploy)
"""
from __future__ import annotations

import asyncio
import pytest


# ── Validator: tag-faking + non-Python handling ───────────────────────────

def _cv():
    from aria_service.autonomous.constitutional_validator import ConstitutionalValidator
    return ConstitutionalValidator()


def test_blocks_hardcoded_grounded_verdict():
    r = _cv().validate('verdict = "GROUNDED"\n', "aria_service/intel/knowledge.py")
    assert r.passed is False
    assert any("verdict" in v.lower() or "grounded" in v.lower() for v in r.violations)


def test_blocks_confirmed_tag_literal():
    r = _cv().validate('def f():\n    return "[CONFIRMED]"\n', "aria_service/intel/knowledge.py")
    assert r.passed is False
    assert any("CONFIRMED" in v for v in r.violations)


def test_blocks_is_grounded_true():
    r = _cv().validate("is_grounded = True\n", "aria_service/intel/knowledge.py")
    assert r.passed is False


def test_clean_python_passes():
    r = _cv().validate("def add(a, b):\n    return a + b\n", "aria_service/intel/knowledge.py")
    assert r.passed is True
    assert r.violations == []


def test_non_python_not_fatal_on_syntax():
    """A clean YAML patch must NOT be blocked just because it isn't Python.
    Pre-R-F855 ast.parse(yaml) raised SyntaxError → wrongful block."""
    r = _cv().validate("sources:\n  - name: gleif\n    url: https://x\n",
                       "aria_service/intel/corpus_registry.yaml")
    assert r.passed is True


def test_non_python_still_regex_checked():
    """Weakening patterns still apply to non-Python content."""
    r = _cv().validate("note: bypass_guards = True everywhere\n",
                       "aria_service/intel/corpus_registry.yaml")
    assert r.passed is False


def test_protected_file_is_fatal():
    r = _cv().validate("x = 1\n", "aria_service/aria_engine.py")
    assert r.passed is False
    assert any("PROTECTED" in v or "protected" in v.lower() for v in r.violations)


# ── deploy_improvement gate (block path — never writes a file) ─────────────

class _FakeRS:
    def __init__(self):
        self.store = {}

    async def get_json(self, key, *a, **kw):
        return self.store.get(key)

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value
        return True


def _patch_si(monkeypatch):
    from aria_service.intel import self_improve as si
    import aria_service.intel.pending_actions as pa
    fake = _FakeRS()
    monkeypatch.setattr(si, "rs", fake)

    async def _noop_log(*a, **kw):
        return None
    monkeypatch.setattr(si, "_log_improvement", _noop_log)

    recorded = {}
    async def _rec(*a, **kw):
        recorded["severity"] = kw.get("severity")
        recorded["called"] = True
        return {}
    monkeypatch.setattr(pa, "record", _rec)
    return si, fake, recorded


def test_deploy_blocks_tag_faking_to_ordinary_file(monkeypatch):
    """A tag-faking diff to an ORDINARY (non-protected) modifiable file is
    blocked by the pattern check, quarantined, and never written."""
    si, fake, recorded = _patch_si(monkeypatch)
    poisoned = {
        "id": "p1", "file": "aria_service/intel/knowledge.py", "status": "staged",
        "new_content": 'verdict = "GROUNDED"  # always pass verification\n',
        "change_type": "bug_fix", "description": "tune verification",
    }
    fake.store[si.STAGED_KEY] = [poisoned]

    res = asyncio.run(si.deploy_improvement("p1"))

    assert res.get("blocked") is True
    assert res.get("violations")
    # moved out of staged → into discarded (evidence)
    assert fake.store[si.STAGED_KEY] == []
    assert len(fake.store.get(si.DISCARDED_KEY, [])) == 1
    assert fake.store[si.DISCARDED_KEY][0]["status"] == "blocked_constitutional"
    # HIGH operator action raised
    assert recorded.get("called") is True
    assert recorded.get("severity") == "HIGH"


def test_deploy_blocks_protected_constitution_file(monkeypatch):
    """Even a syntactically-clean change to a PROTECTED file (the constitution)
    is FATAL-blocked at deploy — constitution changes must go through git, not
    the self-improve pipeline."""
    si, fake, recorded = _patch_si(monkeypatch)
    fake.store[si.STAGED_KEY] = [{
        "id": "c1", "file": "aria_service/aria_engine.py", "status": "staged",
        "new_content": "SAFE = 1\n", "change_type": "bug_fix", "description": "x",
    }]

    res = asyncio.run(si.deploy_improvement("c1"))

    assert res.get("blocked") is True
    assert any("PROTECTED" in v or "protected" in v.lower() for v in res["violations"])
    assert fake.store[si.STAGED_KEY] == []


def test_deploy_missing_item_unaffected(monkeypatch):
    """Sanity: the gate doesn't change the not-found behaviour."""
    si, fake, recorded = _patch_si(monkeypatch)
    fake.store[si.STAGED_KEY] = []
    res = asyncio.run(si.deploy_improvement("nope"))
    assert "error" in res and not res.get("blocked")


# ── Source guard ───────────────────────────────────────────────────────────

def test_gate_wired_into_deploy_source():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "self_improve.py").read_text(encoding="utf-8")
    assert "ConstitutionalValidator().validate(" in src, (
        "R-F855 regression: deploy_improvement no longer runs the "
        "ConstitutionalValidator before writing."
    )
    assert "FAILING CLOSED" in src, "R-F855: the validator gate must fail closed."
