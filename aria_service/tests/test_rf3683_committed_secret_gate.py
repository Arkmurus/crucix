"""R-F3683 — CAPABILITY test: the committed-secret gate refuses a live credential.

THE DEFECT (found 2026-08-04 by a 360 DD sweep)
-----------------------------------------------
``scripts/verify_all.py:5`` and ``scripts/check_mastery.py:5`` each held::

    TOKEN = 'TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk'

and sent it as ``Authorization: Bearer`` to aria-intel.fly.dev. Both files are
TRACKED IN GIT, so the credential is in history and anyone with repo read
access held a key to ~700 brain endpoints. The same value had a third home in
``aria_cli/tests/test_rf2796_terminal_secret_redaction.py`` as a redaction
fixture — a redaction test does not need a real credential.

WHY THE EXISTING GATE DID NOT CATCH IT
--------------------------------------
``check_no_token_default`` (R-F1824) matches a FIXED list of known fallback
strings (``aria-internal``) and skips ``tests/``. It cannot see an arbitrary
high-entropy literal, and it was blind to the fixture copy by construction.

WHAT THIS TEST DRIVES
---------------------
``scripts/pre-commit::check_committed_secrets`` — the real function, on real
files written to tmp_path. §3c: the assertion is the user-visible outcome
(the commit is refused), not a helper's return shape.

Run: python -m pytest aria_service/tests/test_rf3683_committed_secret_gate.py -v
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

# scripts/pre-commit has no .py suffix, so it needs an explicit spec load.
_PRE_COMMIT = Path(__file__).resolve().parents[2] / "scripts" / "pre-commit"


def _load_gate():
    spec = importlib.util.spec_from_loader(
        "aria_pre_commit_rf3683",
        importlib.machinery.SourceFileLoader("aria_pre_commit_rf3683", str(_PRE_COMMIT)),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def gate():
    if not _PRE_COMMIT.exists():
        pytest.skip(f"{_PRE_COMMIT} not found")
    try:
        return _load_gate()
    except Exception as exc:  # pragma: no cover - import-environment dependent
        pytest.skip(f"scripts/pre-commit not importable here: {exc}")


# The exact live token that leaked. Kept here ONLY as the string the gate must
# refuse; it is being rotated. allowlist-secret
_LEAKED = "TPWspa3T5esw2YVh5Y7wemddnSSiLQAxZUz120u5uvk"


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return p


# ── The two shapes the real credential actually appeared in ─────────────────

def test_refuses_python_assignment_shape(gate, tmp_path):
    """`TOKEN = '<43 chars>'` — verify_all.py:5 and check_mastery.py:5."""
    f = _write(tmp_path, "verify_all.py", f"import json\nTOKEN = '{_LEAKED}'\n")
    issues = gate.check_committed_secrets([f])
    assert issues, "the exact shape that leaked must be refused"
    assert "TOKEN" in issues[0]
    # The finding must not reprint the whole credential.
    assert _LEAKED not in issues[0], "the gate must not echo the full secret"


def test_refuses_env_assignment_inside_a_string(gate, tmp_path):
    """`"ARIA_API_TOKEN=<43 chars>"` — the test-fixture copy."""
    f = _write(tmp_path, "fixture.py", f'LINES = [\n    "ARIA_API_TOKEN={_LEAKED}",\n]\n')
    issues = gate.check_committed_secrets([f])
    assert issues, (
        "the env-assignment shape must be refused too — a gate that caught only "
        "the assignment shape would have left the fixture copy in the repo"
    )


def test_refuses_node_const_shape(gate, tmp_path):
    f = _write(tmp_path, "x.mjs", f"const ARIA_API_TOKEN = '{_LEAKED}';\n")
    assert gate.check_committed_secrets([f]), "Node sources are covered too"


def test_refuses_secret_shaped_names_generally(gate, tmp_path):
    for name in ("JWT_SECRET", "STRIPE_SECRET_KEY", "DB_PASSWORD", "BRAVE_API_KEY"):
        f = _write(tmp_path, f"{name.lower()}.py", f"{name} = 'Ab3{'x' * 30}9Zq'\n")
        assert gate.check_committed_secrets([f]), f"{name} must be refused"


# ── It must not be so noisy that people disable it ─────────────────────────

def test_allows_environment_reads(gate, tmp_path):
    f = _write(
        tmp_path,
        "ok.py",
        "import os\n"
        "TOKEN = os.environ['ARIA_API_TOKEN']\n"
        "API_KEY = os.getenv('BRAVE_SEARCH_API_KEY', '')\n",
    )
    assert gate.check_committed_secrets([f]) == [], (
        "reading from the environment is the prescribed fix and must stay clean"
    )


def test_allows_placeholders(gate, tmp_path):
    body = (
        "TOKEN = 'your-token-here-goes-right-here'\n"
        "API_KEY = 'EXAMPLEnotarealtoken0123456789abcdefGHIJKLM'\n"
        "SECRET = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'\n"
        "PASSWORD = 'changeme_changeme_changeme'\n"
    )
    f = _write(tmp_path, "placeholders.py", body)
    assert gate.check_committed_secrets([f]) == [], "placeholders must not trip it"


def test_line_pragma_opts_out(gate, tmp_path):
    f = _write(tmp_path, "p.py", f"TOKEN = '{_LEAKED}'  # allowlist-secret\n")
    assert gate.check_committed_secrets([f]) == []


def test_file_pragma_opts_out(gate, tmp_path):
    f = _write(
        tmp_path,
        "fixtures.py",
        f'"""allowlist-secret-file — redaction fixtures."""\nTOKEN = \'{_LEAKED}\'\n',
    )
    assert gate.check_committed_secrets([f]) == []


def test_ignores_non_source_files(gate, tmp_path):
    f = _write(tmp_path, "notes.md", f"TOKEN = '{_LEAKED}'\n")
    assert gate.check_committed_secrets([f]) == []


# ── Anti-regression: the real repo is clean ────────────────────────────────

def test_the_two_leaking_scripts_are_now_clean(gate):
    root = Path(__file__).resolve().parents[2]
    targets = [root / "scripts" / "verify_all.py", root / "scripts" / "check_mastery.py"]
    present = [p for p in targets if p.exists()]
    assert present, "expected the audited scripts to exist"
    assert gate.check_committed_secrets(present) == [], (
        "scripts/verify_all.py and scripts/check_mastery.py must no longer "
        "carry a hardcoded credential"
    )


def test_scripts_fail_closed_without_the_env_var():
    """An unset token must exit, never fall back to a baked-in default."""
    root = Path(__file__).resolve().parents[2]
    for name in ("verify_all.py", "check_mastery.py"):
        p = root / "scripts" / name
        if not p.exists():
            continue
        src = p.read_text(encoding="utf-8")
        assert "os.environ.get('ARIA_API_TOKEN')" in src, f"{name} must read the env"
        assert "sys.exit(" in src, f"{name} must fail closed when the token is unset"
        assert _LEAKED not in src, f"{name} still contains the leaked credential"


def test_live_token_is_gone_from_all_source(gate):
    """The leaked value must not survive anywhere in tracked source."""
    # R-F4080 (C-129) — enumerate TRACKED source, which is what this test's own
    # docstring promises. `rglob` walked the whole working tree, so it found
    # THIS FILE's copies inside `.claude/worktrees/<peer>/...` — a second agent's
    # git worktrees — and went red on its own fixture. A security gate that
    # fires on its own test file is one that gets ignored (the cry-wolf failure
    # C-96 records), and excluding one more directory name would only defer it
    # to the next copy. `git ls-files` answers the real question.
    import subprocess

    root = Path(__file__).resolve().parents[2]
    try:
        proc = subprocess.run(
            ["git", "ls-files", "-z", "*.py", "*.mjs", "*.js", "*.cjs"],
            cwd=str(root), capture_output=True, timeout=60,
        )
        git_ok = proc.returncode == 0
    except Exception:
        git_ok = False

    # FAIL CLOSED. A credential gate that cannot enumerate its universe must not
    # report "clean" — that is the §1 collapse of "could not measure" into
    # "measured and found nothing", on the one check where it matters most.
    assert git_ok, (
        "could not enumerate tracked files via git — refusing to report this "
        "credential gate as clean on an unknown universe"
    )

    tracked = [
        f for f in proc.stdout.decode("utf-8", errors="replace").split(chr(0)) if f
    ]
    assert len(tracked) > 100, (
        f"only {len(tracked)} tracked source files found — the enumeration is "
        f"wrong, and a gate with an empty universe always certifies (§1)"
    )

    self_rel = str(Path(__file__).resolve().relative_to(root)).replace("\\", "/")
    hits = []
    for rel in tracked:
        if rel.replace("\\", "/") == self_rel:
            continue  # this file names it deliberately, to refuse it
        path = root / rel
        try:
            if _LEAKED in path.read_text(encoding="utf-8", errors="ignore"):
                hits.append(rel)
        except Exception:
            continue
    assert hits == [], f"leaked credential still present in: {hits}"
