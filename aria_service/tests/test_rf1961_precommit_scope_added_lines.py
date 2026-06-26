"""R-F1961 — pre-commit checks scope to ADDED lines/functions (capability test).

Activating the R-F1958 hook surfaced that several checks were WHOLE-FILE: touching
any legacy module flagged ALL its pre-existing untested functions / patterns, so
incremental commits were false-blocked on debt they didn't introduce (and the
checker self-flagged its own pattern definitions). It also wrongly flagged
`Path(...) / "str"` — the correct pathlib idiom. R-F1961 scopes the checks to what
the diff ADDS and deletes the bogus pattern.
"""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))
import pre_commit_checks as p


def test_correct_pathlib_idiom_no_longer_flagged(tmp_path):
    f = tmp_path / "m.py"
    f.write_text('from pathlib import Path\nx = Path("a") / "aria_training"\n', encoding="utf-8")
    # whole-file mode (CI) must NOT flag the correct `Path / "str"` idiom anymore
    assert p.check_windows_compat([f]) == []


def test_capability_check_scopes_to_added_functions(tmp_path):
    # Build names so NO literal appears in this test's source — otherwise the
    # capability check (substring scan of test files) would find them "tested".
    pfx = "zzqq" + "rf1961"
    fn_legacy = pfx + "legacy"
    fn_added = pfx + "added"
    f = tmp_path / "rf1961mod.py"
    f.write_text(f"def {fn_legacy}():\n    return 1\n\ndef {fn_added}():\n    return 2\n",
                 encoding="utf-8")
    # Scoped mode: legacy function (not in the added set) must NOT block.
    scoped = p.check_capability_tests([f], {"rf1961mod.py": {fn_added}})
    assert not any(fn_legacy in i for i in scoped), \
        "pre-existing untested functions must NOT block an incremental commit"
    # Empty added-set → nothing flagged even though the file has untested funcs.
    assert p.check_capability_tests([f], {"rf1961mod.py": set()}) == []
    # Whole-file mode (changed_funcs=None, CI) still flags pre-existing debt.
    whole = p.check_capability_tests([f])
    assert any(fn_legacy in i for i in whole)


def test_windows_check_scopes_to_added_lines(tmp_path):
    f = tmp_path / "m.py"
    # line 2 has a genuine Windows-incompatible pattern (os.fork)
    f.write_text("import os\nos.fork()\nx = 1\n", encoding="utf-8")
    # If os.fork is PRE-EXISTING (not in the added set), it must not block.
    assert p.check_windows_compat([f], {"m.py": {3}}) == []
    # If os.fork IS on an added line, it is flagged.
    assert p.check_windows_compat([f], {"m.py": {2}})


def test_false_success_scopes_to_added_lines(tmp_path):
    f = tmp_path / "m.py"
    f.write_text('def f():\n    return {"success": True}\n', encoding="utf-8")
    # pre-existing (line 2 not added) → no block
    assert p.check_false_success([f], {"m.py": set()}) == []
    # added → flagged
    assert p.check_false_success([f], {"m.py": {2}})


def test_added_line_numbers_parses_hunks():
    from importlib.machinery import SourceFileLoader
    mod = SourceFileLoader("_pc_driver", str(_REPO / "scripts" / "pre-commit")).load_module()
    diff = (
        "diff --git a/m.py b/m.py\n"
        "--- a/m.py\n+++ b/m.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "+os.fork()\n"
        " x = 1\n"
    )
    assert mod.added_line_numbers(diff) == {2}
    assert mod.added_func_names("@@ +1 @@\n+def newfn():\n") == {"newfn"}
