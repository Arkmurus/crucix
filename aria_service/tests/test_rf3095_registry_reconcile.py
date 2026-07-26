"""R-F3095 — the R-number registry drifts because nothing reconciles it.

FOUND (2026-07-26, cross-review of R-F3083). R-F3083's code was committed AND live
while `data/r_number_reservations.json` still said `in_progress, commit_sha: null` —
and R-F3085, which BUILDS ON IT, was correctly ship-marked. The registry claimed a
dependency shipped before its dependency.

R-F3083 was not the bug. A sweep found **372 `in_progress` entries with no SHA**.
CLAUDE.md §2 says "Mark shipped at push" and nothing enforced, checked, or reported
it — ship-marking is a manual step at the end of a long task, i.e. the step that gets
dropped. Fixing R-F3083 by hand fixes one row and leaves the mechanism intact.

THE FALSE POSITIVE THAT SHAPED THE DESIGN. A first cut matched an R-number anywhere
in the commit message and resolved R-F3057 to `68c0bf24`, whose subject is
"R-F3055 + R-F3056 - adverse media on every surface" — it only MENTIONS R-F3057 in
the body. The real commit is `ceced1fc`. So: a SUBJECT match is evidence of
implementation; a BODY match is evidence of a reference. Body-only hits are reported
for a human and NEVER auto-applied — writing a false ship record into the one log
that exists to be trustworthy is worse than leaving it unmarked.
"""
import json
import subprocess

import pytest

from aria_service.intel import r_number_registry as reg


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=str(repo), check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git repo — the reconciler reads real `git log`, so mocking it would
    test the mock, not the parsing this fix exists to get right."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "f.txt").write_text("1")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "fix: R-F1001 — implemented in the subject")
    (r / "f.txt").write_text("2")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m",
         "fix: R-F1002 — another one\n\nThis supersedes R-F1003 and follows R-F1004.")
    return r


@pytest.fixture
def registry(tmp_path):
    p = tmp_path / "reservations.json"
    p.write_text(json.dumps({
        "schema_version": 1, "next_available": 1010,
        "reservations": [
            {"r_number": "R-F1001", "title": "subject match", "status": "in_progress",
             "commit_sha": None},
            {"r_number": "R-F1002", "title": "subject match 2", "status": "in_progress",
             "commit_sha": None},
            {"r_number": "R-F1003", "title": "body mention only", "status": "in_progress",
             "commit_sha": None},
            {"r_number": "R-F1005", "title": "genuinely unshipped", "status": "in_progress",
             "commit_sha": None},
            {"r_number": "R-F1006", "title": "already shipped elsewhere", "status": "shipped",
             "commit_sha": "deadbeef"},
            {"r_number": "R-F1007", "title": "deliberately abandoned", "status": "abandoned",
             "commit_sha": None},
        ],
    }), encoding="utf-8")
    return p


# ── the subject/body distinction ───────────────────────────────────────────
def test_rf3095_subject_match_is_a_ship_record(repo):
    subj, body = reg.scan_shipped_r_numbers(repo_root=repo)
    assert "R-F1001" in subj and "R-F1002" in subj


def test_rf3095_body_mention_is_a_reference_not_a_ship(repo):
    """THE FALSE POSITIVE: 'supersedes R-F1003' must not ship-mark R-F1003."""
    subj, body = reg.scan_shipped_r_numbers(repo_root=repo)
    assert "R-F1003" not in subj, (
        "R-F3095 REGRESSION: a body mention is being treated as an implementation")
    assert "R-F1003" in body and "R-F1004" in body


def test_rf3095_a_subject_match_outranks_a_body_match(repo):
    subj, body = reg.scan_shipped_r_numbers(repo_root=repo)
    assert set(subj) & set(body) == set(), "an R-number must appear in exactly one bucket"


def test_rf3095_missing_git_reports_nothing_rather_than_guessing(tmp_path):
    subj, body = reg.scan_shipped_r_numbers(repo_root=tmp_path / "not-a-repo")
    assert subj == {} and body == {}


# ── reconciliation ─────────────────────────────────────────────────────────
def test_rf3095_dry_run_reports_and_writes_nothing(repo, registry):
    before = registry.read_text(encoding="utf-8")
    res = reg.reconcile_with_git(path=registry, repo_root=repo)
    assert res["drifted"] == 2 and res["applied"] == 0
    assert {e["r_number"] for e in res["entries"]} == {"R-F1001", "R-F1002"}
    assert {e["r_number"] for e in res["review"]} == {"R-F1003"}
    assert registry.read_text(encoding="utf-8") == before, "dry run must not write"


def test_rf3095_apply_ship_marks_only_the_subject_matches(repo, registry):
    res = reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    assert res["applied"] == 2
    by = {r["r_number"]: r
          for r in json.loads(registry.read_text(encoding="utf-8"))["reservations"]}
    assert by["R-F1001"]["status"] == "shipped" and by["R-F1001"]["commit_sha"]
    assert by["R-F1002"]["status"] == "shipped"
    assert by["R-F1003"]["status"] == "in_progress", (
        "R-F3095 REGRESSION: a body-only mention was auto-ship-marked")
    assert by["R-F1005"]["status"] == "in_progress", "no commit — must stay open"


def test_rf3095_never_rewrites_an_existing_ship_sha(repo, registry):
    """The recorded ship SHA is the audit trail; overwriting it destroys the thing
    this log exists to be."""
    reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    by = {r["r_number"]: r
          for r in json.loads(registry.read_text(encoding="utf-8"))["reservations"]}
    assert by["R-F1006"]["commit_sha"] == "deadbeef"


def test_rf3095_abandoned_is_an_operator_decision_and_is_left_alone(repo, registry):
    reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    by = {r["r_number"]: r
          for r in json.loads(registry.read_text(encoding="utf-8"))["reservations"]}
    assert by["R-F1007"]["status"] == "abandoned"


def test_rf3095_is_idempotent(repo, registry):
    first = reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    second = reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    assert first["applied"] == 2 and second["applied"] == 0


def test_rf3095_reconciled_entries_are_labelled_as_such(repo, registry):
    """A row recovered by a script must be distinguishable from one a human marked
    at push time — otherwise the fix launders its own uncertainty."""
    reg.reconcile_with_git(path=registry, repo_root=repo, apply=True)
    by = {r["r_number"]: r
          for r in json.loads(registry.read_text(encoding="utf-8"))["reservations"]}
    assert "R-F3095 reconciled" in (by["R-F1001"].get("notes") or "")


# ── the live registry, which is what triggered this ────────────────────────
def test_rf3095_live_registry_reconciles_without_error():
    """CAPABILITY: run against the REAL registry and the REAL history."""
    res = reg.reconcile_with_git()
    assert res["checked"] > 2000, "the live registry should be fully scanned"
    assert isinstance(res["entries"], list) and isinstance(res["review"], list)
    assert res["applied"] == 0, "the default must never write"
