"""R-F2232 — the aria CLI terminal injects the specialist-persona catalog into
its system prompt (self_mode), so it carries the SAME domain discipline as the
brain's autonomous coder (R-F2231).
"""
from __future__ import annotations

from pathlib import Path

import aria_cli.prompt as prompt
from aria_cli.prompt import build_system_prompt
from aria_service.autonomous.coder_personas import persona_catalog

_ALL = ("sanctions-auditor", "dd-reviewer", "wa-debugger", "autonomy-engineer",
        "ai-engineer", "search-specialist", "python-pro")


def test_catalog_lists_all_personas():
    cat = persona_catalog()
    for name in _ALL:
        assert name in cat, f"{name} missing from catalog"
    assert "INSUFFICIENT_DATA" in cat  # a real sanctions rule carried through


def test_self_mode_prompt_includes_persona_catalog(monkeypatch):
    # isolate from network/file I/O — only the persona injection under test
    monkeypatch.setattr(prompt, "_query_coding_rag", lambda *a, **k: "")
    p = build_system_prompt(root=Path("."), self_mode=True, repo_root=None, task_hint="fix sanctions")
    assert "SPECIALIST PERSONAS" in p
    assert "sanctions-auditor" in p and "INSUFFICIENT_DATA" in p


def test_general_mode_omits_personas():
    p = build_system_prompt(root=Path("."), self_mode=False)
    assert "SPECIALIST PERSONAS" not in p, "ARIA-domain personas must not load in general-project mode"
