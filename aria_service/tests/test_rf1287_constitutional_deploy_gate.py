"""R-F1287 — Capability test: the self-improve DEPLOY gate blocks constitution-
violating content fail-closed (the gate restored after ARIA autonomously deleted
her own constitutional_validator.py in 085d0751 / R-F1191).

These tests invoke the real `deploy_improvement` path: they inject a staged item
and assert that a protected-file target and a weakening-pattern payload are BLOCKED
before any write, and that a clean payload passes the validator. The block path
returns at the gate, before backup/write/git, so the tests are side-effect free.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.self_improve as si
from aria_service.autonomous.constitutional_validator import (
    ConstitutionalValidator, PROTECTED_FILES,
)


def _deploy_staged(tmp_path, monkeypatch, file_path, new_content):
    monkeypatch.setattr(si, "_root", tmp_path)

    async def _run():
        await si.rs.set_json(si.STAGED_KEY, [{
            "id": "t1", "file": file_path, "new_content": new_content,
            "status": "staged", "change_type": "bug_fix",
            "description": "test", "staged_at": 1,
        }])
        return await si.deploy_improvement("t1")

    return asyncio.run(_run())


def test_rf1287_gate_blocks_protected_file(tmp_path, monkeypatch):
    """A staged edit to a PROTECTED_FILE must be blocked at deploy."""
    prot = sorted(PROTECTED_FILES)[0]
    res = _deploy_staged(tmp_path, monkeypatch, prot, "def x():\n    return 1\n")
    assert res.get("blocked") is True, res
    assert res.get("constitutional_block") is True, res


def test_rf1287_gate_blocks_weakening_pattern(tmp_path, monkeypatch):
    """Emptying PROTECTED_FILES is a weakening pattern — blocked even on an
    ordinary, non-protected target."""
    bad = "PROTECTED_FILES = frozenset()\n\n\ndef foo():\n    return 1\n"
    res = _deploy_staged(tmp_path, monkeypatch,
                         "aria_service/intel/rf1287_ordinary.py", bad)
    assert res.get("constitutional_block") is True, res


def test_rf1287_gate_fail_closed_when_validator_errors(tmp_path, monkeypatch):
    """If the validator can't run, the gate must FAIL CLOSED (block), not open."""
    monkeypatch.setattr(si, "_root", tmp_path)

    class _Boom:
        def __init__(self, *a, **k):
            raise RuntimeError("validator import broken")

    import aria_service.autonomous.constitutional_validator as cv
    monkeypatch.setattr(cv, "ConstitutionalValidator", _Boom)

    async def _run():
        await si.rs.set_json(si.STAGED_KEY, [{
            "id": "t1", "file": "aria_service/intel/rf1287_x.py",
            "new_content": "def ok():\n    return 1\n", "status": "staged",
            "change_type": "bug_fix", "description": "test", "staged_at": 1,
        }])
        return await si.deploy_improvement("t1")

    res = asyncio.run(_run())
    assert res.get("blocked") is True, res
    assert res.get("constitutional_block") is True, res


def test_rf1287_validator_allows_clean_change():
    """A clean, benign change passes the validator (gate would let it through)."""
    clean = "def greet(name):\n    return f'hi {name}'\n"
    result = ConstitutionalValidator().validate(clean, "aria_service/intel/rf1287_ok.py")
    assert result.passed is True, (result.violations, result.risk_score)
