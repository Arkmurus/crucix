"""R-F3299 - deploy.ps1 reported [FAIL] on deploys that actually succeeded.

The verification loop required the live build_rev to BE our commit:

    if ($liveSha -eq $GIT_SHORT) { ... }                    # aria-intel
    if ($liveRev.StartsWith($GIT_SHORT) -and $code -eq 200) # aria-web / aria-wa

When a peer agent ships a commit that CONTAINS ours (routine here, and the whole
premise of the two-agents-one-tree hazard), our code is serving and the sha on
the wire is theirs. The loop then polled its full 5 minutes and printed:

    [FAIL] <app> NOT VERIFIED LIVE - the server did NOT advance to your commit.

on a deploy that had in fact shipped. This is the SHARED deploy path, so it
cry-wolfs for every agent and every operator, and a red deploy result people have
learned to discount is more dangerous than no check: the next genuine failure
reads the same.

Ancestry is the honest test. Our commit is live iff it is an ancestor of what is
serving. The property that must never be weakened is that an UNRELATED or OLDER
sha still fails, and it does, because ancestry is real containment.

Behaviourally proven at authoring time against the functions extracted from the
shipped file (6/6): live==ours True, live OLDER False, unknown sha False, empty
False, peer-shipped-descendant True, 12-char node prefix True. This test is the
CI-safe guard on the properties that made those pass, since pwsh is not
guaranteed in every runner.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_DEPLOY = Path(__file__).resolve().parents[2] / "scripts" / "deploy.ps1"


@pytest.fixture(scope="module")
def src() -> str:
    assert _DEPLOY.exists(), f"missing {_DEPLOY}"
    return _DEPLOY.read_text(encoding="utf-8", errors="replace")


def _fn_body(src: str, name: str) -> str:
    """Return the brace-balanced body of a PowerShell function."""
    i = src.index(f"function {name} {{")
    depth, started = 0, False
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
            started = True
        elif src[j] == "}":
            depth -= 1
            if started and depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"unbalanced braces in {name}")


def test_the_ancestry_helper_exists(src: str) -> None:
    assert "function Test-LiveShaContainsHead {" in src


def test_it_uses_real_containment_not_a_looser_string_match(src: str) -> None:
    """merge-base --is-ancestor, not a prefix or substring compare.

    A looser string test would also silence the false FAIL, and would silently
    pass an unrelated commit. That trade is not available here.
    """
    body = _fn_body(src, "Test-LiveShaContainsHead")
    assert "merge-base --is-ancestor" in body, (
        "containment must be decided by git, not by string shape"
    )
    assert "$GIT_SHA" in body, "ancestry is judged against OUR commit"


def test_an_unverifiable_sha_is_not_a_pass(src: str) -> None:
    """"cannot verify" and "verified" must stay different answers.

    If the live sha is an object we do not hold, ancestry is unknowable. Treating
    unknown as success is precisely how a failed deploy gets ship-marked, which
    is the R-F3122 false ship-mark this script already carries scars from.
    """
    body = _fn_body(src, "Test-LiveShaContainsHead")

    # THE LOAD-BEARING LINE. A peer's commit is frequently not fetched yet, and
    # that is precisely the case this helper exists to resolve. Without the fetch
    # git answers "not an ancestor" for the very scenario being fixed and the
    # false FAIL survives. (An earlier draft asserted on the cat-file probe
    # instead; a mutation run showed that probe is defence-in-depth, since an
    # unknown sha already fails at merge-base. Assert what carries the weight.)
    assert "git fetch origin" in body, (
        "must fetch before concluding a live sha is not ours"
    )

    tail = body[body.index("git fetch origin"):]
    assert "return $false" in tail, (
        "still unknown after the fetch means unknown, and unknown is not success"
    )
    assert body.count("return $false") >= 2, (
        "no-sha and unknown-sha must both return false"
    )


def test_the_helper_cannot_return_a_stray_truthy_value(src: str) -> None:
    """R-F1369's trap, which this function walks straight into without Out-Null.

    Invoke-Native emits to the OUTPUT stream. Any output left there becomes part
    of this function's return value, making it truthy no matter what git decided,
    so the check would pass unconditionally. That is the same class of silent
    no-op as R-F3296, where a diagnostic swallowed its own NameError and looked
    like it worked. Verify the instrument.
    """
    body = _fn_body(src, "Test-LiveShaContainsHead")
    invokes = body.count("Invoke-Native {")
    piped = body.count("| Out-Null")
    assert invokes >= 3, f"expected the git probes, found {invokes}"
    assert piped >= invokes, (
        f"{invokes} Invoke-Native calls but only {piped} piped to Out-Null; "
        "an unpiped one makes this function return truthy unconditionally"
    )


def test_both_verification_branches_consult_it(src: str) -> None:
    """aria-intel and the node apps both suffered the false FAIL."""
    assert src.count("Test-LiveShaContainsHead $liveSha") >= 1, "aria-intel branch"
    assert src.count("Test-LiveShaContainsHead $liveRev") >= 1, "aria-web/aria-wa branch"


def test_the_node_branch_still_requires_a_healthy_server(src: str) -> None:
    """Containment alone must not pass a node app that is not serving traffic."""
    i = src.index("Test-LiveShaContainsHead $liveRev")
    line = src[src.rindex("\n", 0, i) + 1:src.index("\n", i)]
    assert "$code -eq 200" in line, (
        "node containment must be ANDed with HTTP 200, or a healthy-but-down "
        f"server passes: {line.strip()}"
    )


def test_the_pass_is_reported_distinctly(src: str) -> None:
    """The operator must be able to tell the two passes apart.

    "your exact sha is live" and "something containing your code is live" are
    different facts, and flattening them into one green line would hide that a
    peer deployed past you.
    """
    assert "[PASS-ANCESTOR]" in src
    assert "CONTAINS your commit" in src


def test_the_bash_mirror_carries_the_same_fix() -> None:
    """deploy.sh had the identical defect at its own exact-sha compare.

    deploy.ps1's header states it "mirrors scripts/deploy.sh exactly", so a fix
    landing in one and not the other silently makes that claim false, and whoever
    deploys from Linux keeps the false FAIL.

    Behaviourally proven at authoring time on the same six cases as the
    PowerShell helper, all six matching.
    """
    sh = _DEPLOY.parent / "deploy.sh"
    if not sh.exists():
        pytest.skip("deploy.sh absent")
    body = sh.read_text(encoding="utf-8", errors="replace")

    assert "live_sha_contains_head() {" in body, "bash mirror lacks the helper"
    assert "merge-base --is-ancestor" in body, "must be real containment"
    assert "git fetch origin" in body, "must fetch before concluding 'not ours'"
    assert 'live_sha_contains_head "$live_sha"' in body, (
        "the helper exists but the verification loop never calls it"
    )


def test_the_exact_match_path_is_unchanged(src: str) -> None:
    """The fast, strongest check stays first and untouched."""
    assert 'if ($liveSha -eq $GIT_SHORT) {' in src
    assert '$liveRev.StartsWith($GIT_SHORT) -and $code -eq 200' in src
