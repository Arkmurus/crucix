"""R-F1295 — Capability test: diff-based (search/replace) editing lets the coder
fix a LARGE file without ever emitting (and truncating) the whole file.

The fixer used to ask the LLM for "the complete updated file content"; past ~650
lines that truncates into a valid-syntax stub. R-F1295 switches large files to
surgical search/replace edits applied to the existing content — file size becomes
irrelevant and truncation is structurally impossible (untouched regions are kept
verbatim). A wrong/ambiguous `old` is REJECTED, never applied, so an edit can never
corrupt the file (the caller falls back to whole-file).
"""
from __future__ import annotations

from aria_service.autonomous.sovereign_llm import apply_search_replace, LARGE_FILE_LINES


def test_single_unique_edit_applies():
    content = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    new, applied, failures = apply_search_replace(
        content, [{"old": "    return 1\n", "new": "    return 99\n"}])
    assert not failures, failures
    assert len(applied) == 1
    assert "return 99" in new and "return 2" in new  # other code preserved


def test_not_found_is_rejected_not_applied():
    content = "x = 1\n"
    new, applied, failures = apply_search_replace(
        content, [{"old": "y = 2\n", "new": "y = 3\n"}])
    assert failures and not applied
    assert new == content, "content must be untouched when an edit can't apply"


def test_ambiguous_old_is_rejected():
    content = "v = 0\nv = 0\n"  # 'v = 0\n' appears twice
    new, applied, failures = apply_search_replace(
        content, [{"old": "v = 0\n", "new": "v = 1\n"}])
    assert failures and not applied
    assert new == content, "ambiguous edit must NOT be applied (no corruption)"


def test_empty_old_rejected():
    new, applied, failures = apply_search_replace("a\n", [{"old": "", "new": "b"}])
    assert failures and not applied


def test_does_not_mutate_input_list_or_string():
    content = "p = 1\n"
    edits = [{"old": "p = 1\n", "new": "p = 2\n"}]
    apply_search_replace(content, edits)
    assert content == "p = 1\n"
    assert edits == [{"old": "p = 1\n", "new": "p = 2\n"}]


def test_large_file_edit_preserves_everything_no_truncation():
    """THE improvement: a 1200-line file gets one surgical edit and stays 1200
    lines — the whole-file rewrite that would truncate is avoided entirely."""
    # build a large, unambiguous file
    body = "".join(f"def fn_{i}():\n    return {i}\n\n\n" for i in range(400))  # ~1600 lines
    marker = "def fn_200():\n    return 200\n"
    assert body.count(marker) == 1
    line_count = body.count("\n") + 1
    assert line_count > LARGE_FILE_LINES  # this is a "large file"

    new, applied, failures = apply_search_replace(
        body, [{"old": marker, "new": "def fn_200():\n    return 'patched'\n"}])

    assert not failures and len(applied) == 1
    # every other function survived — NOT truncated
    assert new.count("def fn_") == 400
    assert "return 'patched'" in new
    assert "def fn_399():" in new  # the tail is intact (the truncation symptom would drop it)
    # only the one function changed
    assert new.count("return 200\n") == 0


def test_multiple_edits_all_apply():
    content = "a = 1\nb = 2\nc = 3\n"
    new, applied, failures = apply_search_replace(content, [
        {"old": "a = 1\n", "new": "a = 10\n"},
        {"old": "c = 3\n", "new": "c = 30\n"},
    ])
    assert not failures and len(applied) == 2
    assert new == "a = 10\nb = 2\nc = 30\n"


# ── coder integration: _generate_target_code chooses edit vs whole-file ───────

import asyncio
from unittest.mock import AsyncMock

from aria_service.autonomous import self_coder as sc


def _coder(write_edit_ret=None, write_code_ret=None):
    coder = sc.ARIACoder.__new__(sc.ARIACoder)
    coder.llm = type("L", (), {})()
    coder.llm.write_edit = AsyncMock(return_value=write_edit_ret or {})
    coder.llm.write_code = AsyncMock(return_value=write_code_ret or {})
    return coder


_BIG = "".join(f"def fn_{i}():\n    return {i}\n\n\n" for i in range(400))  # large


def test_large_file_uses_edit_mode_not_wholefile():
    marker = "def fn_10():\n    return 10\n"
    coder = _coder(
        write_edit_ret={"edits": [{"old": marker, "new": "def fn_10():\n    return 'x'\n"}]},
        write_code_ret={"code": "TRUNCATED STUB"},
    )
    out = asyncio.run(coder._generate_target_code({}, _BIG, "big.py"))
    assert out != "TRUNCATED STUB", "large file must use edit-mode, not whole-file"
    assert out.count("def fn_") == 400, "all functions preserved"
    assert "return 'x'" in out
    coder.llm.write_edit.assert_awaited_once()
    coder.llm.write_code.assert_not_called()


def test_large_file_falls_back_when_edit_does_not_apply():
    coder = _coder(
        write_edit_ret={"edits": [{"old": "NOT IN THE FILE\n", "new": "y\n"}]},
        write_code_ret={"code": "WHOLE FILE FALLBACK"},
    )
    out = asyncio.run(coder._generate_target_code({}, _BIG, "big.py"))
    assert out == "WHOLE FILE FALLBACK", "must fall back to whole-file when edit can't apply"
    coder.llm.write_code.assert_awaited_once()


def test_small_file_uses_wholefile():
    coder = _coder(write_code_ret={"code": "def a():\n    return 1\n"})
    out = asyncio.run(coder._generate_target_code({}, "x = 1\n", "small.py"))
    assert out == "def a():\n    return 1\n"
    coder.llm.write_edit.assert_not_called()
    coder.llm.write_code.assert_awaited_once()
