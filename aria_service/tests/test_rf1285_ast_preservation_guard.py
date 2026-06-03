"""R-F1285 — Capability test: the self-coder's stage guard rejects a truncated
rewrite that drops public symbols, even when it keeps >50% of the file's lines.

The pre-existing R-F904 guard only rejected proposals under HALF the current line
count, so a stub keeping 51-99% of lines but dropping the tail of the module
sailed through (the live staged queue held ~11 such proposals). R-F1285 adds an
AST symbol-preservation check: a SHRINKING Python proposal that deletes a
top-level public function/class present in the current file is rejected.

These tests call the real `stage_improvement` path (the path the autonomous coder
uses) and assert the user-visible outcome: the destructive proposal is NOT staged.
"""
from __future__ import annotations

import asyncio

import aria_service.intel.self_improve as si


_CURRENT = (
    "def alpha():\n    return 1\n\n\n"
    "def beta():\n    return 2\n\n\n"
    "def gamma():\n    return 3\n\n\n"
    "class Helper:\n    def run(self):\n        return 4\n"
)


def test_rf1285_rejects_symbol_dropping_shrinkage(tmp_path, monkeypatch):
    """Truncated rewrite drops gamma + Helper while staying above 50% of lines."""
    monkeypatch.setattr(si, "_root", tmp_path)
    rel = "rf1285_mod.py"
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {rel})
    (tmp_path / rel).write_text(_CURRENT, encoding="utf-8")

    # Keeps alpha + beta, loses gamma + Helper — a truncated tail.
    proposed = "def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n"

    res = asyncio.run(si.stage_improvement(rel, proposed, "bug_fix", "fix alpha"))
    assert res.get("truncation_guard") is True, res
    assert res.get("staged") is False, res
    # the rejection names the dropped symbols
    assert "gamma" in res.get("error", "") or "Helper" in res.get("error", ""), res


def test_rf1285_rejects_dropping_a_class(tmp_path, monkeypatch):
    monkeypatch.setattr(si, "_root", tmp_path)
    rel = "rf1285_mod_cls.py"
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {rel})
    (tmp_path / rel).write_text(_CURRENT, encoding="utf-8")
    # drops only the class Helper, keeps all functions, but shrinks
    proposed = (
        "def alpha():\n    return 1\n\n\n"
        "def beta():\n    return 2\n\n\n"
        "def gamma():\n    return 3\n"
    )
    res = asyncio.run(si.stage_improvement(rel, proposed, "bug_fix", "drop Helper"))
    assert res.get("truncation_guard") is True, res


def test_rf1285_allows_symbol_preserving_shrink(tmp_path, monkeypatch):
    """A genuine shrink that keeps every public symbol (e.g. removes blank lines /
    a private helper) must NOT be blocked by the truncation guard."""
    monkeypatch.setattr(si, "_root", tmp_path)
    rel = "rf1285_mod_ok.py"
    monkeypatch.setattr(si, "MODIFIABLE_FILES", set(si.MODIFIABLE_FILES) | {rel})
    current = (
        "def _helper():\n    return 0\n\n\n"
        "def alpha():\n    return 1\n\n\n"
        "def beta():\n    return _helper() + 2\n"
    )
    (tmp_path / rel).write_text(current, encoding="utf-8")
    # remove the private helper + blank lines; both public symbols (alpha, beta) kept
    proposed = "def alpha():\n    return 1\n\ndef beta():\n    return 2\n"
    res = asyncio.run(si.stage_improvement(rel, proposed, "bug_fix", "inline helper"))
    assert res.get("truncation_guard") is not True, res
