"""R-F3398 — the tooling must find the project's credentials, and say so when it can't.

TWO FAILURES, ONE ROOT. During the R-F3396 capture, Companies House was
configured on this machine and the capture still could not reach it: nothing in
the training tooling loads the project `.env`, so the key was simply absent from
the shell. The workaround was to source it by hand on every invocation — a
band-aid CLAUDE.md §1 forbids, and one that leaks the value into shell history.

The second failure is worse than the first. With no key, `search_companies`
returned nothing and the capture printed `SKIP <subject>: no registry match` —
a MISSING CREDENTIAL reported as a DATA FINDING. "We are not configured to look"
and "we looked and found nothing" are opposite claims, and the pipeline stated
the second while the first was true. That is the absence-is-not-evidence class
this repo keeps re-learning, here pointed at the corpus that trains the model.

`load_project_env` fixes the first; `require_env` and the credential
precondition in the captures fix the second by refusing to start.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from aria_service import env_bootstrap as E


# --------------------------------------------------------------------------
# finding the repo
# --------------------------------------------------------------------------

def test_find_repo_root_locates_the_crucix_checkout():
    root = E.find_repo_root(Path(__file__).resolve())
    assert root is not None
    assert (root / "aria_service").is_dir()
    assert (root / "CLAUDE.md").is_file()


def test_find_repo_root_returns_none_outside_a_checkout(tmp_path):
    assert E.find_repo_root(tmp_path) is None


# --------------------------------------------------------------------------
# loading .env
# --------------------------------------------------------------------------

def _write_env(d: Path, body: str) -> Path:
    (d / "aria_service").mkdir(exist_ok=True)
    (d / "CLAUDE.md").write_text("x", encoding="utf-8")
    p = d / ".env"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_project_env_sets_absent_keys(tmp_path, monkeypatch):
    _write_env(tmp_path, "RF3398_ALPHA=hello\nRF3398_BETA=world\n")
    monkeypatch.delenv("RF3398_ALPHA", raising=False)
    monkeypatch.delenv("RF3398_BETA", raising=False)
    assert E.load_project_env(tmp_path) == 2
    assert os.environ["RF3398_ALPHA"] == "hello"


def test_existing_environment_always_wins(tmp_path, monkeypatch):
    """Fly secrets must beat a stale local .env, never the other way round."""
    _write_env(tmp_path, "RF3398_GAMMA=from_file\n")
    monkeypatch.setenv("RF3398_GAMMA", "from_environment")
    E.load_project_env(tmp_path)
    assert os.environ["RF3398_GAMMA"] == "from_environment"


def test_comments_blanks_quotes_and_export_prefix(tmp_path, monkeypatch):
    _write_env(tmp_path, "\n# a comment\nexport RF3398_DELTA=\"quoted value\"\n\nRF3398_EPS='single'\n")
    for k in ("RF3398_DELTA", "RF3398_EPS"):
        monkeypatch.delenv(k, raising=False)
    E.load_project_env(tmp_path)
    assert os.environ["RF3398_DELTA"] == "quoted value"
    assert os.environ["RF3398_EPS"] == "single"


def test_values_containing_equals_survive(tmp_path, monkeypatch):
    """Tokens and base64 secrets routinely contain '='."""
    _write_env(tmp_path, "RF3398_TOKEN=abc=def==\n")
    monkeypatch.delenv("RF3398_TOKEN", raising=False)
    E.load_project_env(tmp_path)
    assert os.environ["RF3398_TOKEN"] == "abc=def=="


def test_missing_env_file_is_not_an_error(tmp_path):
    (tmp_path / "aria_service").mkdir()
    (tmp_path / "CLAUDE.md").write_text("x", encoding="utf-8")
    assert E.load_project_env(tmp_path) == 0


def test_loading_never_raises_on_a_malformed_file(tmp_path, monkeypatch):
    """A broken .env must not take down a capture or a boot path."""
    _write_env(tmp_path, "not a kv line\n\x00\xff binary junk\nRF3398_OK=fine\n")
    monkeypatch.delenv("RF3398_OK", raising=False)
    assert E.load_project_env(tmp_path) >= 0


def test_load_does_not_echo_secret_values(tmp_path, monkeypatch, capsys):
    """Loading must never print what it loaded."""
    _write_env(tmp_path, "RF3398_SECRET=super-secret-value\n")
    monkeypatch.delenv("RF3398_SECRET", raising=False)
    E.load_project_env(tmp_path)
    out = capsys.readouterr()
    assert "super-secret-value" not in (out.out + out.err)


# --------------------------------------------------------------------------
# refusing to run credential-less
# --------------------------------------------------------------------------

def test_require_env_passes_when_set(monkeypatch):
    monkeypatch.setenv("RF3398_NEEDED", "x")
    E.require_env(["RF3398_NEEDED"], purpose="the test")


def test_require_env_raises_and_names_what_is_missing(monkeypatch):
    monkeypatch.delenv("RF3398_NEEDED", raising=False)
    with pytest.raises(E.MissingCredentials) as exc:
        E.require_env(["RF3398_NEEDED"], purpose="capturing registry chains")
    msg = str(exc.value)
    assert "RF3398_NEEDED" in msg
    assert "capturing registry chains" in msg


def test_require_env_never_reveals_a_value(monkeypatch):
    monkeypatch.setenv("RF3398_PRESENT", "s3cr3t")
    monkeypatch.delenv("RF3398_ABSENT", raising=False)
    with pytest.raises(E.MissingCredentials) as exc:
        E.require_env(["RF3398_PRESENT", "RF3398_ABSENT"], purpose="p")
    assert "s3cr3t" not in str(exc.value)


def test_blank_and_whitespace_count_as_missing(monkeypatch):
    monkeypatch.setenv("RF3398_BLANK", "   ")
    with pytest.raises(E.MissingCredentials):
        E.require_env(["RF3398_BLANK"], purpose="p")


# --------------------------------------------------------------------------
# the capability: a capture must not call an absent key "no registry match"
# --------------------------------------------------------------------------

def test_registry_capture_refuses_without_a_key_instead_of_reporting_no_match(monkeypatch):
    """The exact R-F3396 symptom, asserted at the capture's precondition.

    Without a key the capture printed `SKIP <subject>: no registry match` for
    every subject and wrote an empty corpus. "Not configured to look" must never
    render as "looked and found nothing".
    """
    from scripts.train import capture_multihop as C

    monkeypatch.setattr(C, "load_project_env", lambda *a, **k: 0)
    monkeypatch.delenv("COMPANIES_HOUSE_API_KEY", raising=False)
    with pytest.raises(E.MissingCredentials) as exc:
        C.check_preconditions()
    assert "COMPANIES_HOUSE_API_KEY" in str(exc.value)


def test_registry_capture_precondition_passes_with_a_key(monkeypatch):
    from scripts.train import capture_multihop as C

    monkeypatch.setattr(C, "load_project_env", lambda *a, **k: 0)
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "present")
    monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "present")
    C.check_preconditions()


# --------------------------------------------------------------------------
# linked worktrees — .env is gitignored and lives only in the primary checkout
# --------------------------------------------------------------------------

def test_worktree_falls_back_to_the_primary_checkouts_env(tmp_path, monkeypatch):
    """Without this every agent in a worktree is pushed back to sourcing by hand."""
    main = tmp_path / "main"
    (main / "aria_service").mkdir(parents=True)
    (main / "CLAUDE.md").write_text("x", encoding="utf-8")
    (main / ".git").mkdir()
    (main / ".env").write_text("RF3398_WT=from_primary\n", encoding="utf-8")

    wt = tmp_path / "wt"
    (wt / "aria_service").mkdir(parents=True)
    (wt / "CLAUDE.md").write_text("x", encoding="utf-8")
    (wt / ".git").write_text(
        f"gitdir: {(main / '.git' / 'worktrees' / 'wt').as_posix()}\n", encoding="utf-8")

    monkeypatch.delenv("RF3398_WT", raising=False)
    assert E.load_project_env(wt) == 1
    assert os.environ["RF3398_WT"] == "from_primary"


def test_a_worktrees_own_env_wins_over_the_primary(tmp_path, monkeypatch):
    """A local override must not be silently replaced by the primary's copy."""
    main = tmp_path / "main"
    (main / "aria_service").mkdir(parents=True)
    (main / "CLAUDE.md").write_text("x", encoding="utf-8")
    (main / ".git").mkdir()
    (main / ".env").write_text("RF3398_OWN=from_primary\n", encoding="utf-8")

    wt = tmp_path / "wt"
    (wt / "aria_service").mkdir(parents=True)
    (wt / "CLAUDE.md").write_text("x", encoding="utf-8")
    (wt / ".git").write_text(
        f"gitdir: {(main / '.git' / 'worktrees' / 'wt').as_posix()}\n", encoding="utf-8")
    (wt / ".env").write_text("RF3398_OWN=from_worktree\n", encoding="utf-8")

    monkeypatch.delenv("RF3398_OWN", raising=False)
    E.load_project_env(wt)
    assert os.environ["RF3398_OWN"] == "from_worktree"


def test_primary_checkout_is_none_for_an_ordinary_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    assert E._primary_checkout(tmp_path) is None


def test_malformed_git_pointer_is_survivable(tmp_path):
    (tmp_path / ".git").write_text("not a gitdir line", encoding="utf-8")
    assert E._primary_checkout(tmp_path) is None
