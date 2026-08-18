"""R-F3552 — the deploy health check cried wolf when a peer's batch shipped past.

THE CHECK is `expected_sha in build_rev`, a string match. On a shared tree a peer's
deploy routinely batches several commits, so the live build_rev is a DESCENDANT of mine
rather than mine — and the substring misses. Live on 2026-07-31:

    FAIL: intel: health check FAILED — response:
          {'build_rev': 'R-F3548+R-F3549+R-F3550+R-F3551 · sha 18fd5eb8'}

while `git merge-base --is-ancestor 464a28a8 18fd5eb8` proved the commit WAS live. The
deploy had succeeded and the code was serving.

R-F1478 already fixed one false-fail here — a concurrent ci_deploy overwriting
`.last_deploy_sha` — and this is the other one. It cost time three separate times in a
single session. The standing rule is that a guard which cries wolf gets switched off, and
a switched-off deploy check costs far more than it ever saves.

FAILS CLOSED, which is the part that matters. Ancestry is accepted only when git PROVES
it. No git, no repo, an unparseable build_rev, an older build, or a non-zero exit all
return False: "cannot prove it is live" must never render as "it is live" — that would
turn a cry-wolf into a false clean, which is strictly worse.
"""
from __future__ import annotations

import importlib.util
import pathlib

import pytest

# R-F3770/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so an edit mid-run silently returns a DIFFERENT function's body.
from ._source_probe import function_source

_SPEC = importlib.util.spec_from_file_location(
    "lhc", pathlib.Path(__file__).resolve().parents[2] / "scripts" / "live_health_check.py")
lhc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lhc)


def test_capability_a_descendant_build_is_accepted():
    """THE LIVE FALSE FAIL, reproduced with the real shas."""
    data = {"status": "alive",
            "build_rev": "R-F3548+R-F3549+R-F3550+R-F3551 · sha 18fd5eb8"}
    assert lhc._live_contains_expected(data, "464a28a8") is True


def test_an_exact_match_is_accepted():
    assert lhc._live_contains_expected(
        {"build_rev": "R-F3549 · sha 464a28a8"}, "464a28a8") is True


def test_an_OLDER_build_is_REJECTED():
    """The direction that must never pass: if the live sha is an ANCESTOR of what we
    shipped, the deploy did NOT land and saying otherwise is a false clean."""
    assert lhc._live_contains_expected(
        {"build_rev": "R-F3000 · sha 091a2057"}, "18fd5eb8") is False


@pytest.mark.parametrize("data,exp,why", [
    ({"build_rev": "no sha here"}, "464a28a8", "unparseable build_rev"),
    ({"build_rev": "R-F1 · sha deadbeef"}, "464a28a8", "unknown sha"),
    ("ok", "464a28a8", "non-dict response"),
    ({"build_rev": "R-F1 · sha 464a28a8"}, "", "no expected sha supplied"),
])
def test_it_fails_CLOSED_when_it_cannot_prove_containment(data, exp, why):
    assert lhc._live_contains_expected(data, exp) is False, why


def test_the_fallback_runs_only_AFTER_the_normal_check():
    """It must widen the pass condition, never replace it — the substring check is the
    cheap path and stays first."""
    import inspect
    src = function_source(lhc, "check_app_health")
    assert src.index('config["health_check"]') < src.index("_live_contains_expected"), (
        "the ancestry fallback runs before the primary check")
    assert "if _live_contains_expected" in src


def test_the_pass_message_says_a_later_batch_shipped_past():
    """A PASS that looks identical to an exact match would hide that someone else's
    commits are also live — which is exactly what an operator needs to know."""
    import inspect
    src = function_source(lhc, "check_app_health")
    assert "CONTAINS" in src and "shipped past this commit" in src
