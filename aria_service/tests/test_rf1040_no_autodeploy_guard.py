"""R-F1040 — anti-self-harm guard: ARIA is free to code with no restrictions, but
the files where an unreviewed auto-deploy could brick her (boot, constitution,
verification gate, prompts, safety guards, the deploy config itself) must NOT
auto-deploy — a human deploys them. R-F996 had emptied this set ("fully trusted"),
which let those files auto-deploy unreviewed; this restores the guard WITHOUT
limiting what she can edit/stage.
"""
from __future__ import annotations

from aria_service.intel import self_improve as si


def test_no_autodeploy_set_is_populated():
    assert si.NO_AUTODEPLOY_FILES, "NO_AUTODEPLOY_FILES must not be empty"
    # the self-harm-critical files must be present
    for f in (
        "aria_service/main.py",
        "aria_service/aria_engine.py",
        "aria_service/routes/aria.py",
        "aria_service/autonomous/safety.py",
        "aria_service/autonomous/constitutional_validator.py",
        "aria_service/intel/self_improve.py",
    ):
        assert f in si.NO_AUTODEPLOY_FILES, f"{f} must be auto-deploy-protected"


def test_critical_files_never_auto_deploy():
    for f in si.NO_AUTODEPLOY_FILES:
        assert si._auto_deploy_allowed(f, "bug_fix") is False, f
        assert si._auto_deploy_allowed(f, "optimisation") is False, f


def test_ordinary_files_are_free_to_auto_deploy():
    # free to code: non-critical files still auto-deploy (no restriction)
    assert si._auto_deploy_allowed("aria_service/intel/researcher.py", "bug_fix") is True
    assert si._auto_deploy_allowed("lib/self/learning_store.mjs", "optimisation") is True
