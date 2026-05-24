"""R-F851 — honesty-critical files never auto-deploy via self-improve.

Threat (surfaced 2026-05-24): the self-improve stage→deploy path has NO
content guard. R-F462 made bug_fix/optimisation human-gated by DEFAULT, but
§17 set ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 live, re-opening auto-deploy for
EVERY modifiable file — including the constitution itself (aria_engine.py
holds ARIA_SYSTEM_PROMPT + clauses; v3_prompts.py; routes/aria.py holds the
verification gate, prompt-injection detection and footer/confidence tags;
tasks.yaml schedules the safety/adversarial jobs). So the autonomous loop
could auto-deploy a self-generated "bug_fix" that silently rewrote a
verification clause — the constitution-poisoning vector the autonomy gate
exists to stop.

R-F851 re-closes auto-deploy for that critical set ONLY — they can still be
STAGED for a human to review+deploy, but never auto-deploy — while leaving
autonomous self-improvement intact for ordinary files.

This pins:
  1. _auto_deploy_allowed returns False for every NO_AUTODEPLOY_FILE even
     when the env flag is ON and the change_type is auto-eligible.
  2. Ordinary modifiable files still auto-deploy under the flag.
  3. The critical files remain in MODIFIABLE_FILES (human can still deploy).
  4. stage_improvement stamps auto_deployable=False for a critical file even
     under ARIA_SELF_IMPROVE_AUTO_DEPLOY=1 (the user-visible contract).
  5. The autonomous loop carries the defense-in-depth NO_AUTODEPLOY_FILES check.
"""
from __future__ import annotations

import asyncio
import importlib
import os

import pytest


def _reload_self_improve(env: dict):
    """Reload self_improve under specific env so the import-time auto-deploy
    flag reflects the scenario (same pattern as test_rf462)."""
    os.environ.pop("ARIA_SELF_IMPROVE_AUTO_DEPLOY", None)
    for k, v in env.items():
        os.environ[k] = v
    import aria_service.intel.self_improve as _si
    importlib.reload(_si)
    return _si


class _FakeRS:
    """In-memory redis_store stub so stage_improvement is hermetic."""
    def __init__(self):
        self.store = {}

    async def get_json(self, key, *a, **kw):
        return self.store.get(key)

    async def set_json(self, key, value, *a, **kw):
        self.store[key] = value
        return True


# ── _auto_deploy_allowed: the decision the autonomous loop relies on ──────

def test_critical_files_never_autodeploy_even_with_flag_on():
    """Flag ON + auto-eligible change_type → critical files STILL blocked."""
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    # Precondition: the flag really is on for ordinary files.
    assert _si.CHANGE_TYPES["bug_fix"]["auto_deploy"] is True
    for f in _si.NO_AUTODEPLOY_FILES:
        for ct in ("bug_fix", "optimisation"):
            assert _si._auto_deploy_allowed(f, ct) is False, (
                f"R-F851: {f} must never auto-deploy (change_type={ct}) "
                f"even with ARIA_SELF_IMPROVE_AUTO_DEPLOY=1"
            )


def test_ordinary_files_still_autodeploy_under_flag():
    """The gate is surgical — ordinary modifiable files keep auto-deploy."""
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    ordinary = "aria_service/intel/knowledge.py"
    assert ordinary not in _si.NO_AUTODEPLOY_FILES
    assert _si._auto_deploy_allowed(ordinary, "bug_fix") is True
    assert _si._auto_deploy_allowed(ordinary, "optimisation") is True


def test_constitution_files_are_the_critical_set():
    """Anchor the exact set so a refactor can't silently shrink it."""
    _si = _reload_self_improve({})
    assert _si.NO_AUTODEPLOY_FILES == {
        "aria_service/aria_engine.py",
        "aria_service/intel/v3_prompts.py",
        "aria_service/routes/aria.py",
        "aria_service/autonomous/tasks.yaml",
    }


def test_human_only_change_types_still_blocked_on_ordinary_files():
    """prompt_evolution etc. stay human-gated everywhere (R-F462 carried)."""
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    for ct in ("prompt_evolution", "new_intel_layer", "enhancement"):
        assert _si._auto_deploy_allowed("aria_service/intel/knowledge.py", ct) is False


# ── Critical files must remain stageable for HUMAN deploy ─────────────────

def test_critical_files_still_modifiable():
    """R-F851 must not make the constitution un-stageable — a human must
    still be able to stage + review + deploy a legitimate edit."""
    _si = _reload_self_improve({})
    for f in _si.NO_AUTODEPLOY_FILES:
        assert f in _si.MODIFIABLE_FILES, (
            f"{f} dropped from MODIFIABLE_FILES — humans can no longer "
            f"stage a reviewed edit. R-F851 only blocks AUTO-deploy."
        )


# ── Capability: stage_improvement stamps the flag correctly ───────────────

def test_stage_improvement_marks_constitution_not_autodeployable(monkeypatch):
    """End-to-end: staging a bug_fix to aria_engine.py under the live flag
    yields auto_deployable=False; staging the same to an ordinary file
    yields True. This is the contract the autonomous loop reads."""
    _si = _reload_self_improve({"ARIA_SELF_IMPROVE_AUTO_DEPLOY": "1"})
    fake = _FakeRS()
    monkeypatch.setattr(_si, "rs", fake)

    async def _noop_log(*a, **kw):
        return None
    monkeypatch.setattr(_si, "_log_improvement", _noop_log)

    valid_py = "x = 1\n"

    constitution = asyncio.run(_si.stage_improvement(
        "aria_service/aria_engine.py", valid_py, "bug_fix", "t", "t",
    ))
    assert constitution.get("staged") is True
    assert constitution.get("auto_deployable") is False, (
        "R-F851: a staged constitution bug_fix must NOT be auto_deployable"
    )

    ordinary = asyncio.run(_si.stage_improvement(
        "aria_service/intel/knowledge.py", valid_py, "bug_fix", "t", "t",
    ))
    assert ordinary.get("staged") is True
    assert ordinary.get("auto_deployable") is True, (
        "R-F851 must stay surgical: ordinary files keep auto-deploy under the flag"
    )


# ── Source guard: the autonomous loop keeps the defense-in-depth check ────

def test_autonomous_loop_has_defense_in_depth():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "intel" / "self_improve.py").read_text(encoding="utf-8")
    assert 'file_path not in NO_AUTODEPLOY_FILES' in src, (
        "R-F851 regression: the autonomous auto-deploy branch lost its "
        "NO_AUTODEPLOY_FILES guard — a future flag-setting path could "
        "auto-deploy the constitution again."
    )


# ── aria_coder path (self_coder._stage_or_deploy) — the bypass Pass 2 found ──

def test_coder_path_never_autodeploys_constitution_even_with_force_deploy(monkeypatch):
    """The aria_coder pipeline calls deploy_improvement DIRECTLY, reading
    CHANGE_TYPES per change_type and honouring force_deploy (R-F821 ticket
    mode) — bypassing the self_improve gate. R-F851 adds a per-FILE guard
    there too: even with force_deploy=True, a code_change to a constitution
    file stays staged while ordinary files deploy."""
    from types import SimpleNamespace
    from aria_service.autonomous import self_coder as sc
    from aria_service.intel import self_improve as si

    plan = sc.FixPlan(
        fix_id="fix1", gap_id="g1", gap_type="bug", r_number=0,
        title="t", description="d", target_files=[],
        code_changes={
            "aria_service/aria_engine.py": "x = 1\n",      # constitution
            "aria_service/intel/knowledge.py": "y = 2\n",  # ordinary
        },
    )

    ids = iter(["const_id", "ord_id"])

    async def fake_stage(file_path, new_content, change_type, description, reasoning):
        return {"staged": True, "id": next(ids)}

    deployed: list[str] = []

    async def fake_deploy(sid):
        deployed.append(sid)
        return {"ok": True, "deployed": True}

    monkeypatch.setattr(si, "stage_improvement", fake_stage)
    monkeypatch.setattr(si, "deploy_improvement", fake_deploy)

    dummy = SimpleNamespace()  # _stage_or_deploy doesn't touch self on this path
    ok, status, staged_ids = asyncio.run(
        sc.ARIACoder._stage_or_deploy(
            dummy, plan, "bug_fix", force_stage=False, force_deploy=True,
        )
    )
    assert ok is True
    assert "const_id" not in deployed, (
        "R-F851 BYPASS: aria_coder auto-deployed a constitution file via force_deploy"
    )
    assert "ord_id" in deployed, "ordinary file should still auto-deploy"
    assert set(staged_ids) == {"const_id", "ord_id"}, (
        "both items must remain staged (the constitution one for human review)"
    )


def test_coder_source_has_per_file_guard():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "autonomous" / "self_coder.py").read_text(encoding="utf-8")
    assert "NO_AUTODEPLOY_FILES" in src, (
        "R-F851 regression: self_coder.py lost the per-file constitution guard — "
        "the aria_coder path can auto-deploy the constitution again."
    )
