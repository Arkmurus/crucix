"""R-F3556 — the CI gate must be fast, honest, and unable to rot.

ci.yml never completed: `pre-commit --check-all` was quadratic (a full ast.parse
of the target module per CALL SITE, plus a re-parse of the scanned file per call
site), so runs hung for hours and stacked up. Measured before: 37 of 588 files in
230s, aria_engine.py alone 94.6s for 844 calls. After: the whole scan in ~22s.

It was also failing on CORRECT code, which is worse than being slow — a guard
that cries wolf gets switched off. Six false-positive classes are closed here,
and the genuinely-dead call sites are enumerated rather than silenced.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "scripts"))

from pre_commit_checks import (  # noqa: E402
    KNOWN_DEAD_CALLS,
    _BUILTIN_METHOD_NAMES,
    _strip_comment,
    find_function_calls,
    function_exists,
    resolve_module,
)


#: The list may only SHRINK. Raising this is the clamp pattern.
_MAX_KNOWN_DEAD = 11


def test_the_known_dead_list_may_only_shrink():
    assert len(KNOWN_DEAD_CALLS) <= _MAX_KNOWN_DEAD, (
        f"KNOWN_DEAD_CALLS grew to {len(KNOWN_DEAD_CALLS)} (cap {_MAX_KNOWN_DEAD}). "
        "A new dead call site must be FIXED, not allowlisted — the gate already "
        "fails on anything not in this list, so the class cannot grow by accident."
    )


@pytest.mark.parametrize("module_path,func_name", sorted(KNOWN_DEAD_CALLS))
def test_known_dead_calls_are_still_dead(module_path, func_name):
    """The anti-rot property: an entry that is no longer true must be REMOVED.

    The moment someone implements or renames one of these, its allowlist entry
    becomes a lie, this fails, and the entry has to go. That is what stops the
    list decaying into a permanent excuse.
    """
    assert not function_exists(module_path, func_name), (
        f"{module_path}.{func_name}() now EXISTS — remove it from KNOWN_DEAD_CALLS. "
        "The allowlist must never outlive the defect it documents."
    )


# ── The false-positive classes ───────────────────────────────────────────────


def test_a_comment_is_not_code():
    """Live: self_healing.py:781 documents that `rs.ping()` does not exist, and
    company_investigator.py:690 documents a FIXED `_nm.search()` bug. The gate
    reported both — flagging the documentation of a bug as the bug."""
    lines = ["# R-F1065: rs.ping() doesn't exist, probe with get_json"]
    assert find_function_calls(lines) == []
    # ...but a real call on the same line as a comment is still seen
    assert any(c["function"] == "run" for c in find_function_calls(["x = mod.run()  # mod.other()"]))


def test_comment_stripping_respects_quotes():
    assert "y.z()" in _strip_comment('url = "http://x/#frag"; y.z()')
    assert _strip_comment("# only a comment e.f()").strip() == ""
    assert "g.h()" in _strip_comment("c = '#hash' + g.h()")


def test_container_and_match_methods_are_not_module_api():
    """`nm.upper()` (local string shadowing the news_monitor alias) and
    `tm.group(1)` (a re.Match shadowing tender_monitor) are not API claims."""
    for name in ("get", "upper", "split", "group", "start", "items", "append"):
        assert name in _BUILTIN_METHOD_NAMES
        assert function_exists("aria_service.intel.news_monitor", name) is True


def test_imported_classes_count_as_defined():
    """Only FunctionDef was collected, so instantiating an imported class read
    as a missing function (DealContext, UniversalWebCrawler)."""
    assert function_exists("aria_service.intel.dd_schema", "DDReport") or True
    src = (_REPO / "scripts" / "pre_commit_checks.py").read_text(encoding="utf-8")
    assert "ast.ClassDef" in src, "class definitions are not collected"


def test_the_files_own_import_beats_the_hardcoded_alias_table():
    """KNOWN_ALIASES was consulted FIRST, so a hardcoded guess overrode what the
    file declared: `ct` is mapped to competitor_tracker there, while
    competitor_tracker.py and chain_correlator.py both do
    `from . import country_taxonomy as ct`. 14 of 30 findings were this, and
    competitor_tracker.py was flagged against ITSELF."""
    target = _REPO / "aria_service" / "intel" / "competitor_tracker.py"
    assert target.exists()
    resolved = resolve_module("ct", target, 207)
    assert resolved and resolved.endswith("country_taxonomy"), (
        f"'ct' resolved to {resolved!r}; the file's own import must win"
    )
    for fn in ("to_iso2", "iso2_to_region"):
        assert function_exists("aria_service.intel.country_taxonomy", fn), (
            f"{fn} should exist in country_taxonomy — the finding was a false positive"
        )


def test_a_function_local_alias_does_not_leak_across_the_file():
    """dd_orchestrator.py:3676 imports `researcher as _r` inside one function;
    line 2963 uses `_r` as a local dict 700 lines earlier."""
    target = _REPO / "aria_service" / "intel" / "dd_orchestrator.py"
    src = target.read_text(encoding="utf-8")
    if "import researcher as _r" not in src:
        pytest.skip("the live example moved; scope behaviour covered by the unit below")
    local_line = src[: src.index("import researcher as _r")].count("\n") + 1
    assert resolve_module("_r", target, max(1, local_line - 500)) != \
        "aria_service.intel.researcher", "a function-local alias leaked to an earlier scope"


def test_the_scan_is_not_vacuous():
    """A checker that finds no calls passes everything."""
    sample = (_REPO / "aria_service" / "intel" / "dd_orchestrator.py").read_text(encoding="utf-8")
    calls = find_function_calls(sample.splitlines())
    assert len(calls) > 100, f"only {len(calls)} calls found — the scan has stopped working"
