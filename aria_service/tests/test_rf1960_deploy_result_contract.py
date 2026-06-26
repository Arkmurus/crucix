"""R-F1960 — deploy_improvement explicit result contract (capability test).

ROOT CAUSE this kills: deploy_improvement returned three shapes
({"deployed":True} success, {"error":...}/{...,"blocked":True} failure) and
callers GUESSED the key. self_coder._stage_or_deploy checked `deploy_res.get("ok")`
— a key deploy_improvement NEVER returned — so EVERY successful auto-deploy was
misread as `deploy_failed:...:unknown`: the gap churned every cycle and the
R-F804 post-deploy regression monitor never ran. Fix: one canonical
`deploy_succeeded()` contract helper + an explicit `ok` on every return.
"""
import asyncio

from aria_service.intel import self_improve as si


def test_deploy_succeeded_classifies_every_real_shape():
    # success (new explicit contract)
    assert si.deploy_succeeded({"ok": True, "deployed": True, "id": "x"}) is True
    # legacy success WITHOUT the explicit ok — must still read as success
    assert si.deploy_succeeded({"deployed": True, "id": "x"}) is True
    # the four real failure shapes
    assert si.deploy_succeeded({"ok": False, "error": "not found"}) is False
    assert si.deploy_succeeded({"error": "Deploy failed: boom"}) is False
    assert si.deploy_succeeded({"error": "BLOCKED", "blocked": True,
                                "constitutional_block": True}) is False
    assert si.deploy_succeeded({"blocked": True, "truncation_guard": True}) is False
    # garbage / None never reads as success
    assert si.deploy_succeeded(None) is False
    assert si.deploy_succeeded("nope") is False
    assert si.deploy_succeeded({}) is False


def test_reproduces_the_get_ok_bug_at_the_decision_point():
    """The exact symptom: a real success dict, classified the OLD way vs NEW way."""
    # This is the literal shape deploy_improvement returns on success.
    real_success = {"ok": True, "deployed": True, "id": "imp1", "file": "a.py"}
    # PRE-FIX: the legacy success shape had NO "ok" key, so `not res.get("ok")`
    # was `not None` == True → the coder returned (False, "deploy_failed:...").
    legacy_success = {"deployed": True, "id": "imp1", "file": "a.py"}
    assert (not legacy_success.get("ok")) is True, "demonstrates the old check misfiring"
    # POST-FIX: the canonical helper classifies BOTH shapes as success.
    assert si.deploy_succeeded(real_success) is True
    assert si.deploy_succeeded(legacy_success) is True


def test_real_deploy_improvement_carries_explicit_ok_on_failure_path():
    """Drive the REAL async function (the not-found path needs no staging) and
    assert the contract: an explicit ok=False is present, not a guessable shape."""
    async def _run():
        res = await si.deploy_improvement("rf1960-does-not-exist")
        assert isinstance(res, dict)
        assert res.get("ok") is False, "every deploy_improvement return must carry explicit ok"
        assert si.deploy_succeeded(res) is False
    asyncio.run(_run())


def test_self_coder_uses_the_helper_not_a_guessed_key():
    """Regression guard: the coder's deploy classification must go through the
    canonical helper, never a hard-coded guessed result key."""
    from pathlib import Path
    src = Path(self_coder_path()).read_text(encoding="utf-8")
    assert "_si.deploy_succeeded(deploy_res)" in src, "coder must classify deploys via deploy_succeeded()"
    # the ACTIVE buggy statement (not a comment mention) must be gone
    assert 'if not deploy_res.get("ok")' not in src, "the guessed-key check must be gone"


def self_coder_path():
    from aria_service.autonomous import self_coder
    return self_coder.__file__
