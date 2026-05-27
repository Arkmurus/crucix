"""R-F926 — CI must NOT skip the Node-tier deploys when aria-intel's verify flakes.

aria-web + aria-wa are separate Fly apps with separate builds. Pre-R-F926 all
four Node steps in deploy-fly.yml were `if: success()`, so a slow aria-intel
cold-start (the /health verify has a 180s budget; first-boot RAG warm-up can
exceed it) silently SKIPPED the Node deploys — UI/auth/WA stayed on a stale
image (observed repeatedly, incl. R-F916/F925 needing manual aria-wa deploys).

This test guards the decoupled gates so they can't regress to `if: success()`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-fly.yml"


def _steps():
    d = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    return {s["name"]: s for s in d["jobs"]["deploy"]["steps"] if s.get("name")}


def test_rf926_node_deploys_run_when_not_cancelled():
    steps = _steps()
    for name in ("Deploy aria-web", "Deploy aria-wa"):
        cond = str(steps[name].get("if", ""))
        assert "cancelled()" in cond and "success()" != cond, (
            f"{name!r} must be decoupled (!cancelled), not gated on cumulative success(): {cond!r}"
        )


def test_rf926_node_verifies_gate_on_own_deploy_outcome():
    steps = _steps()
    assert "steps.deploy_web.outcome == 'success'" in str(steps["Verify aria-web is live"]["if"])
    assert "steps.deploy_wa.outcome == 'success'" in str(steps["Verify aria-wa is live"]["if"])


def test_rf926_deploy_steps_have_ids_for_gating():
    steps = _steps()
    assert steps["Deploy aria-web"].get("id") == "deploy_web"
    assert steps["Deploy aria-wa"].get("id") == "deploy_wa"
