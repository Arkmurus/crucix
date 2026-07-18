"""R-F2708 — deploy blast-radius brake: a staged full-file snapshot must NOT be
written if the live file drifted since staging (it would silently revert the
intervening change).

The amendment approve→deploy path stages the WHOLE aria_engine.py (base + one
appended clause) and deploys it later via a human-paced POST. If another change
lands on that file in between, writing the stale snapshot reverts it. R-F2708 records
base_sha256 at stage time and refuses the deploy when the live file no longer matches.
"""
from __future__ import annotations

import asyncio
import hashlib

import pytest

import aria_service.intel.self_improve as si
import aria_service.autonomous.constitutional_validator as cv
import aria_service.utils.git_utils as gu


class _Pass:
    passed = True
    violations: list = []
    risk_score = 0.0


class _CVStub:
    def validate(self, *a, **k):
        return _Pass()


class _DVStub:
    def validate_diff(self, *a, **k):
        return _Pass()


def _setup(monkeypatch, tmp_path):
    store: dict = {}

    async def _get(k):
        return store.get(k)

    async def _set(k, v, *a, **kw):
        store[k] = v

    monkeypatch.setattr(si.rs, "get_json", _get)
    monkeypatch.setattr(si.rs, "set_json", _set)
    monkeypatch.setattr(si, "_root", tmp_path)
    monkeypatch.setattr(si, "wire_success", lambda *a, **k: None)
    monkeypatch.setattr(si, "wire_failure", lambda *a, **k: None)

    # Neutralise machinery NOT under test: validators pass, git is a no-op.
    monkeypatch.setattr(cv, "ConstitutionalValidator", _CVStub)
    monkeypatch.setattr(cv, "DiffValidator", _DVStub)
    monkeypatch.setattr(cv, "record_learned_attack", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(gu, "get_current_commit", lambda: ("testsha", ""))

    async def _noop_git(*a, **k):
        return None
    monkeypatch.setattr(si, "_git_commit", _noop_git)

    fp = "aria_service/intel/_rf2708_probe.py"
    (tmp_path / "aria_service" / "intel").mkdir(parents=True, exist_ok=True)
    base = "# base file\nVALUE = 1\n" + "\n".join(f"L{i} = 1" for i in range(60)) + "\n"
    (tmp_path / fp).write_text(base, encoding="utf-8")
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {fp})
    return fp, base, store


def test_rf2708_records_base_and_deploys_when_unchanged(monkeypatch, tmp_path):
    fp, base, store = _setup(monkeypatch, tmp_path)
    new_content = base + "APPENDED = 2  # amendment clause\n"

    async def run():
        staged = await si.stage_improvement(fp, new_content, "prompt_evolution",
                                            "R-F2708 probe: append clause")
        assert staged.get("staged") is True
        # base_sha256 recorded == hash of the current (base) file
        items = store[si.STAGED_KEY]
        item = next(s for s in items if s["id"] == staged["id"])
        assert item["base_sha256"] == hashlib.sha256(base.encode("utf-8")).hexdigest()
        # No drift → deploy applies the snapshot.
        dep = await si.deploy_improvement(staged["id"])
        assert si.deploy_succeeded(dep), dep
        return fp

    asyncio.run(run())
    assert (tmp_path / fp).read_text(encoding="utf-8") == new_content


def test_rf2708_blocks_deploy_when_base_drifted_and_preserves_intervening(monkeypatch, tmp_path):
    fp, base, store = _setup(monkeypatch, tmp_path)
    new_content = base + "APPENDED = 2  # amendment clause\n"
    intervening = base + "HOTFIX = 99  # landed AFTER staging — must NOT be reverted\n"

    async def run():
        staged = await si.stage_improvement(fp, new_content, "prompt_evolution",
                                            "R-F2708 probe: append clause")
        assert staged.get("staged") is True
        # Simulate an intervening change to the live file AFTER staging.
        (tmp_path / fp).write_text(intervening, encoding="utf-8")
        dep = await si.deploy_improvement(staged["id"])
        return dep

    dep = asyncio.run(run())
    assert dep["ok"] is False and dep.get("stale_base") is True, dep
    assert "stale base" in dep["error"].lower()
    # The intervening change is preserved — the stale snapshot did NOT overwrite it.
    live = (tmp_path / fp).read_text(encoding="utf-8")
    assert "HOTFIX = 99" in live
    assert "APPENDED = 2" not in live
