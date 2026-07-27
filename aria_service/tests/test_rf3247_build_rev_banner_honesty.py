"""R-F3247 — the build_rev banner is a claim about what is IN the build.

Two defects, both observed on the live service today:

  OVER-CLAIM   `build_rev: "...+R-F3229 · sha 4598730c"` on a build that did not
               contain R-F3229. The scan matched any `R-F<n>` in a commit
               SUBJECT, so `chore: reserve R-F3226..R-F3229` contributed two
               numbers whose only committed artefact was a RESERVATION — and
               missed 3227/3228, which the range merely implies.

  UNDER-CLAIM  `build_rev: "no-r-tag · sha 30cd35ca"` on a build containing
               everything. When the newest `deploy-*` tag is at or ahead of
               HEAD — a peer deploys, or two deploys share a commit — the range
               is empty, and "no-r-tag" reads as "this build ships nothing".

Both writers must carry the fix: `deploy.ps1` (the Windows route actually used)
and `deploy.sh`. This repo has shipped the same defect twice by fixing one of
two writers (R-F3019→R-F3031→R-F3038, R-F3039→R-F3050), so the guard checks
both.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
PS1 = ROOT / "scripts" / "deploy.ps1"
SH = ROOT / "scripts" / "deploy.sh"

# The subject prefixes that are registry BOOKKEEPING, not shipped code.
BOOKKEEPING = re.compile(r"^chore:\s*(reserve|mark|ship)")
R_NUMBER = re.compile(r"R-F[0-9]+")

SUBJECTS = [
    "chore: reserve R-F3226..R-F3229 (sanctions honesty follow-ups)",
    "chore: mark R-F3225 shipped",
    "fix: R-F3230 — a real code change",
    "test: R-F3236 — fix blocking-dialog string false positive",
]


def _banner(subjects):
    kept = [s for s in subjects if not BOOKKEEPING.match(s)]
    return sorted({m for s in kept for m in R_NUMBER.findall(s)})


def test_rf3247_a_reservation_commit_never_reaches_the_banner():
    """THE OVER-CLAIM, reproduced: R-F3226/R-F3229 were only ever reserved."""
    assert _banner(SUBJECTS) == ["R-F3230", "R-F3236"]


def test_rf3247_real_work_still_counts_including_test_and_chore_prefixes():
    """The filter must be narrow. A `test:` R-number is a shipped change, and a
    `chore:` that does real work is not bookkeeping — only reserve/mark/ship are."""
    assert "R-F3236" in _banner(SUBJECTS), "a test: R-number is real work"
    assert _banner(["chore: drop the dead import R-F9001"]) == ["R-F9001"]
    assert _banner(["chore: reserve R-F9002"]) == []


def test_rf3247_both_deploy_writers_exclude_bookkeeping():
    """One fix reaching one of two writers is how this repo has shipped the same
    defect twice before."""
    ps1 = PS1.read_text(encoding="utf-8")
    sh = SH.read_text(encoding="utf-8")
    assert "R-F3247" in ps1, "deploy.ps1 lost the fix"
    assert "R-F3247" in sh, "deploy.sh lost the fix"
    # PowerShell uses \s, bash uses [[:space:]] — assert each carries its own.
    assert re.search(r"chore:\\s\*\(reserve\|mark\|ship\)", ps1), ps1[:0] or "ps1 filter missing"
    assert "chore:[[:space:]]*(reserve|mark|ship)" in sh, "sh filter missing"


def test_rf3247_empty_range_does_not_read_as_shipping_nothing():
    """"no-r-tag" on a build that contains everything is the under-claim."""
    ps1 = PS1.read_text(encoding="utf-8")
    sh = SH.read_text(encoding="utf-8")
    assert "no-new-r-numbers" in ps1, "deploy.ps1 still renders the misleading fallback"
    assert "no-new-r-numbers" in sh, "deploy.sh still renders the misleading fallback"
