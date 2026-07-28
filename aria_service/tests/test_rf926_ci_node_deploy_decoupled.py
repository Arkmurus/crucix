"""R-F926 — CI must NOT skip the Node-tier deploys when aria-intel's verify flakes.

aria-web + aria-wa are separate Fly apps with separate builds. Pre-R-F926 all
four Node steps in deploy-fly.yml were `if: success()`, so a slow aria-intel
cold-start (the /health verify has a 180s budget; first-boot RAG warm-up can
exceed it) silently SKIPPED the Node deploys — UI/auth/WA stayed on a stale
image (observed repeatedly, incl. R-F916/F925 needing manual aria-wa deploys).

R-F3335 — these tests indexed `steps["Deploy aria-web"]` and had been failing
with KeyError ever since **R-F1157 removed the Node deploy steps from this
workflow entirely** (commit c085c18a, "Remove Node.js deploy steps from Python
CI"). The workflow's own comment gives the reason: the Node Docker build wasted
~5min per Python commit, failed consistently on Depot, and marked the whole
workflow failed, blocking Python deploys. Verified: deploy-fly.yml now deploys
`--app aria-intel` only, and no workflow deploys the Node tiers at all — ci.yml
merely TESTS them. They ship via scripts/deploy.ps1 / deploy.sh.

So the steps are gone by decision, not by accident, and asserting their shape is
asserting a structure that no longer exists.

What is NOT gone is the DEFECT R-F926 caught: a Node deploy gated on cumulative
`success()` gets silently skipped by an unrelated flake. Deleting this file would
throw that away, and re-adding the steps later is exactly when it would be
needed. So the guard is kept and RE-ARMED CONDITIONALLY — dormant while no Node
step exists, live the moment one comes back, and the dormancy is reported as a
SKIP rather than a pass, because a guard that is green because its subject is
absent is indistinguishable from one that is green because it checked.
"""
from __future__ import annotations

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

_WF = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-fly.yml"

_NODE_APPS = ("aria-web", "aria-wa")


def _steps() -> list[dict]:
    d = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    return [s for s in d["jobs"]["deploy"]["steps"] if isinstance(s, dict)]


def _deployed_apps() -> set[str]:
    """Which Fly apps this workflow actually ships, read from what it RUNS."""
    apps = set()
    for s in _steps():
        run = str(s.get("run", ""))
        if "flyctl deploy" not in run:
            continue
        for app in ("aria-intel", *_NODE_APPS):
            if f"--app {app}" in run:
                apps.add(app)
    return apps


def _node_deploy_steps() -> list[dict]:
    """Steps that deploy a Node app, found by what they RUN, not by their name.

    Name-matching is what broke this file: `steps["Deploy aria-web"]` raises
    KeyError the moment a step is renamed or removed, which says nothing about
    whether the deploy is correctly gated. `flyctl deploy --app aria-web` is the
    thing that actually ships the tier, so that is what is matched.
    """
    out = []
    for s in _steps():
        run = str(s.get("run", ""))
        if "flyctl deploy" in run and any(f"--app {a}" in run for a in _NODE_APPS):
            out.append(s)
    return out


def test_rf3335_deploy_workflow_deploys_intel_only():
    """R-F1157's decision, pinned."""
    deployed = _deployed_apps()

    assert "aria-intel" in deployed, (
        "deploy-fly.yml must still deploy aria-intel — if this fails, the workflow "
        "no longer does what its name says"
    )
    assert deployed == {"aria-intel"}, (
        f"R-F1157 removed the Node tiers from this workflow (they wasted ~5min per "
        f"Python commit, failed on Depot, and blocked Python deploys); it now "
        f"deploys {sorted(deployed)}. If that is a deliberate reversal, the R-F926 "
        f"gating tests below re-arm automatically — read them before shipping."
    )


def test_rf926_node_deploys_if_present_are_decoupled():
    """R-F926's property, dormant but re-armed.

    If a Node deploy ever returns to this workflow it must be gated on
    `!cancelled()`, never on cumulative `success()`, or an unrelated aria-intel
    flake silently skips it and the UI/auth/WA tier stays on a stale image.
    """
    node_steps = _node_deploy_steps()
    if not node_steps:
        pytest.skip(
            "dormant: no Node deploy step in deploy-fly.yml (R-F1157 removed them; "
            "aria-web/aria-wa ship via scripts/deploy.ps1). Re-arms automatically "
            "if one is re-added."
        )

    for s in node_steps:
        cond = str(s.get("if", ""))
        name = s.get("name", "(unnamed)")
        assert cond, f"{name!r} has no `if:` — it inherits cumulative success() gating"
        assert "cancelled()" in cond and cond.strip() != "success()", (
            f"{name!r} must be decoupled (!cancelled), not gated on cumulative "
            f"success(): {cond!r}"
        )
        assert s.get("id"), (
            f"{name!r} needs an `id:` so its verify step can gate on ITS outcome "
            f"rather than on the job's"
        )


def test_rf926_node_verifies_if_present_gate_on_their_own_deploy():
    """A verify must key off the deploy step it verifies, not off the job."""
    node_steps = _node_deploy_steps()
    if not node_steps:
        pytest.skip("dormant: no Node deploy step to verify (see R-F1157 / R-F3335)")

    ids = {s.get("id") for s in node_steps if s.get("id")}
    verifies = [s for s in _steps()
                if "verify" in str(s.get("name", "")).lower()
                and any(a in str(s.get("name", "")) for a in _NODE_APPS)]
    assert verifies, "a Node deploy with no verify step ships unchecked"

    for v in verifies:
        cond = str(v.get("if", ""))
        assert any(f"steps.{i}.outcome == 'success'" in cond for i in ids), (
            f"{v.get('name')!r} must gate on its own deploy's outcome "
            f"(one of {sorted(ids)}), got {cond!r}"
        )
