"""R-F4069 (C-123) — the builtin-shadow guard blocked 32 sites, 31 of them
false positives by its own docstring, and CI never ran it.

`check_builtin_shadowing` says it checks "that no **module-level** function
shadows a Python built-in". It walked the whole AST (`ast.walk`), so it also
flagged **methods**. Measured across the tree (excluding .venv/node_modules):

    MODULE-LEVEL shadows: {'set': 1}
        aria_service/intel/redis_store.py:193  async def set(...)
    METHOD-level shadows: 31
        {'set': 21, 'list': 3, 'setattr': 3, 'next': 1, 'format': 1,
         'help': 1, 'exit': 1}

A method named `set` on a class cannot shadow `builtins.set` at module scope —
that is what the docstring already says, and it is why 31 of the 32 hits were
noise. Any commit touching any of those files was blocked.

The remaining one is deliberate: `redis_store` mirrors the Redis command
surface (`set`/`get`/`delete`/`expire`), and the module **already applies the
remedy the checker itself recommends** — it `import builtins` at line 32, the
same convention `state_store.py` uses (`builtins.set()` at the lrem fallback).
With no allowlist, the guard made `redis_store.py` **uncommittable**: R-F4068
needed to add an `hdel` wrapper there and could not.

Two further faults found while fixing it:

* `check_builtin_shadowing` was **defined twice, verbatim**, in
  `pre_commit_checks.py`. The first definition was dead.
* The staged-commit path calls it (`scripts/pre-commit:594`) but
  `check_all_files()` (CI `--check-all`) does not. Live proof from the R-F4068
  session: `pre-commit --check-all` reported "OK — all files checked, no
  issues" on the very tree whose commit the hook then blocked. Same
  two-modes-one-measure fork this file already records at line ~536 for a
  different guard.

A guard must still be able to FAIL, so the allowlist is narrow and keyed on the
exact (file, function) pair, and a test proves a NEW module-level shadow is
still caught.
"""
from __future__ import annotations

import ast
import builtins
import pathlib

import pytest

import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import pre_commit_checks as pcc  # noqa: E402


# ── 1. methods are not module-level functions ──────────────────────────────

def test_a_method_named_set_is_not_flagged(tmp_path):
    f = tmp_path / "fake_store.py"
    f.write_text(
        "class FakeStore:\n"
        "    async def set(self, key, value):\n"
        "        return None\n"
        "    async def list(self):\n"
        "        return []\n",
        encoding="utf-8",
    )
    issues = pcc.check_builtin_shadowing([f])
    assert issues == [], (
        "a method cannot shadow a builtin at module scope; the docstring says "
        f"module-level and 31 sites in the tree were flagged this way: {issues}")


def test_a_new_module_level_shadow_is_still_caught(tmp_path):
    """The guard must be able to fail, or it is not a guard."""
    f = tmp_path / "sloppy.py"
    f.write_text("def set(x):\n    return x\n", encoding="utf-8")
    issues = pcc.check_builtin_shadowing([f])
    assert len(issues) == 1, issues
    assert "shadows builtins.set()" in issues[0]


def test_nested_function_shadow_is_still_caught(tmp_path):
    """A shadow inside a function body DOES rebind the name for the rest of
    that scope, so it stays in scope for the check."""
    f = tmp_path / "nested.py"
    f.write_text(
        "def outer():\n"
        "    def sorted(xs):\n"
        "        return xs\n"
        "    return sorted([3, 1])\n",
        encoding="utf-8",
    )
    issues = pcc.check_builtin_shadowing([f])
    assert len(issues) == 1, issues


# ── 2. the deliberate Redis mirror is allowlisted, and only it ─────────────

def test_redis_store_is_committable():
    repo = pathlib.Path(__file__).resolve().parents[2]
    target = repo / "aria_service" / "intel" / "redis_store.py"
    issues = pcc.check_builtin_shadowing([target])
    assert issues == [], (
        "redis_store mirrors the Redis command surface and already applies the "
        "checker's own remedy (`import builtins`, line 32). With no allowlist "
        f"the guard made the file uncommittable: {issues}")


def test_the_allowlist_is_narrow():
    """Keyed on (file, function). A different builtin in the same file, or the
    same name in a different file, must still be caught."""
    entries = pcc.BUILTIN_SHADOW_ALLOWLIST
    assert entries, "allowlist vanished"
    for (path_suffix, name), reason in entries.items():
        assert path_suffix.endswith(".py"), path_suffix
        assert name in dir(builtins), name
        assert len(reason) > 30, f"{name}: an allowlist entry must state WHY"


def test_allowlist_does_not_cover_a_different_name_in_the_same_file(tmp_path):
    d = tmp_path / "aria_service" / "intel"
    d.mkdir(parents=True)
    f = d / "redis_store.py"
    f.write_text("def list():\n    return []\n", encoding="utf-8")
    issues = pcc.check_builtin_shadowing([f])
    assert len(issues) == 1, (
        f"allowlisting redis_store::set must not exempt every builtin: {issues}")


# ── 3. the two modes must agree ────────────────────────────────────────────

def test_ci_mode_runs_the_same_check():
    """`--check-all` reported clean on the tree the commit hook blocked."""
    src = (_SCRIPTS / "pre-commit").read_text(encoding="utf-8")
    start = src.index("def check_all_files(")
    end = src.index("\ndef ", start + 1)
    body = src[start:end]
    assert "check_builtin_shadowing" in body, (
        "CI mode does not run the builtin-shadow check, so the staged hook and "
        "--check-all disagree about what a violation is")


# ── 4. no duplicate definition ─────────────────────────────────────────────

def test_check_is_defined_once():
    src = (_SCRIPTS / "pre_commit_checks.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    defs = [n for n in tree.body
            if isinstance(n, ast.FunctionDef)
            and n.name == "check_builtin_shadowing"]
    assert len(defs) == 1, (
        f"defined {len(defs)}x — the earlier one is dead code and the two can "
        "drift apart silently")


# ── 5. the whole tree passes, so CI can be turned on safely ───────────────

def test_repo_has_no_unallowlisted_module_level_shadows():
    repo = pathlib.Path(__file__).resolve().parents[2]
    files = [p for p in repo.rglob("*.py")
             if not any(part in {".venv", "node_modules", ".claude", "__pycache__"}
                        for part in p.parts)]
    issues = pcc.check_builtin_shadowing(files)
    assert issues == [], (
        "enabling this check in CI would go red on day one:\n  "
        + "\n  ".join(issues[:10]))
