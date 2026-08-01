"""R-F3635 — CLAUDE.md §11 must not describe a deploy route the workflow does not have.

WHAT HAPPENED
-------------
§11 said, as its documented fallback:

    1. Add `[deploy]` to the commit message so CI auto-deploys on push
    2. Then push: git push origin main

That is FALSE. `.github/workflows/deploy-fly.yml` has **no `push:` trigger** — only
`workflow_dispatch`. The `if:` guard testing for `[deploy]` in the commit message is
real code and is unreachable by construction.

On 2026-08-01 it cost a live deploy: R-F3634 (chain health reporting the order dispatch
actually walks) was committed, tagged `[deploy]`, and pushed. No workflow ran. The fix
sat un-deployed while the operator was told a deploy was in flight — and the route was
chosen *specifically* because it was the safe one with a peer agent holding uncommitted
files. The safe path was the broken one.

R-F3238 had ALREADY found this and written it into a comment inside deploy-fly.yml.
Nobody corrected CLAUDE.md — the file every session is required to read first. A
finding recorded only where the defect lives, and not where the instruction is read,
does not prevent the next occurrence. This test is the missing half.

WHAT IT GUARDS
--------------
Not the wording — the AGREEMENT between the doc and the workflow. Either:
  * the workflow gains a real `push:` trigger, and §11's original claim becomes true; or
  * it has none, and §11 must carry the warning.
Both are fine. Silently disagreeing is not.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-fly.yml"
CLAUDE_MD = ROOT / "CLAUDE.md"


def _workflow_has_push_trigger() -> bool:
    """True iff deploy-fly.yml is triggered by `push`.

    Parsed structurally, not by substring: the word 'push' appears in comments and in
    `git push` examples throughout the file. Only a top-level `on:` key counts, so this
    cannot be fooled by prose — the way a naive grep for 'push' would be.
    """
    lines = WORKFLOW.read_text(encoding="utf-8", errors="replace").splitlines()
    in_on = False
    for raw in lines:
        line = raw.split("#", 1)[0].rstrip()      # strip comments — prose is not config
        if not line.strip():
            continue
        if re.match(r"^on:\s*$", line):
            in_on = True
            continue
        if in_on:
            if not line.startswith((" ", "\t")):   # dedented to column 0 -> `on:` ended
                break
            if re.match(r"^\s{1,4}push:\s*$", line):
                return True
    return False


def test_the_deploy_workflow_trigger_is_what_the_doc_says_it_is():
    assert WORKFLOW.exists(), "deploy-fly.yml is the deploy route §11 points at"
    doc = CLAUDE_MD.read_text(encoding="utf-8", errors="replace")

    if _workflow_has_push_trigger():
        # The original instruction would be TRUE again. Then the warning must go, or
        # §11 tells the next session a working route is broken — the same failure
        # mirrored, and just as expensive.
        assert "A `[deploy]` COMMIT MESSAGE DOES NOTHING" not in doc, (
            "deploy-fly.yml now has a push: trigger, so §11's warning is stale and "
            "would send someone to dispatch manually when pushing would do"
        )
    else:
        assert "A `[deploy]` COMMIT MESSAGE DOES NOTHING" in doc, (
            "deploy-fly.yml has NO push: trigger, so a [deploy] commit message deploys "
            "nothing. §11 must say so — it is the file every session reads first, and "
            "R-F3238 recording it only in a workflow comment did not stop R-F3634 "
            "being pushed with the tag and never deployed."
        )
        assert "workflow_dispatch" in doc, (
            "§11 must name the route that DOES work, not merely warn about the one "
            "that does not — a warning without a replacement leaves the reader stuck"
        )


def test_the_doc_does_not_still_instruct_the_dead_route():
    """The specific sentence that misled. Guarded verbatim because a warning added
    ABOVE a surviving instruction is worse than either alone: the reader follows the
    numbered step."""
    doc = CLAUDE_MD.read_text(encoding="utf-8", errors="replace")
    dead = "Add `[deploy]` to the commit message so CI auto-deploys on push"
    assert dead not in doc, (
        "§11 still carries the instruction that does nothing. Remove it, do not just "
        "annotate it — a numbered step outranks a paragraph above it."
    )


def test_dispatch_requires_an_auditable_reason():
    """The working route is `gh workflow run ... -f reason=...`. If `reason` ever stops
    being required, §11's documented command breaks and deploys stop being auditable."""
    wf = WORKFLOW.read_text(encoding="utf-8", errors="replace")
    assert "workflow_dispatch" in wf
    assert "reason" in wf and "required: true" in wf, (
        "manual deploys must carry an auditable justification; §11 documents -f reason"
    )
