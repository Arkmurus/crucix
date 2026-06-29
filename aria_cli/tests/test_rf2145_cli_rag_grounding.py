"""R-F2145 — CLI coder queries live coding RAG at prompt-build time.

The CLI coder's system prompt now includes semantically retrieved constitutional
rules, codebase structure, and past fixes from the coding RAG — same as the
autonomous coder. Tests verify the _query_coding_rag function returns expected
content when the RAG is available, and gracefully degrades when it's not.
"""
from __future__ import annotations


def test_rf2145_query_coding_rag_returns_string():
    """_query_coding_rag returns a string (possibly empty if RAG unavailable)."""
    from aria_cli.prompt import _query_coding_rag
    result = _query_coding_rag("coding conventions")
    assert isinstance(result, str), f"Expected str, got {type(result)}"


def test_rf2145_query_coding_rag_graceful_degradation():
    """_query_coding_rag gracefully returns empty string when RAG unavailable."""
    from aria_cli.prompt import _query_coding_rag
    result = _query_coding_rag("function naming conventions and coding standards")
    assert isinstance(result, str), f"Expected str, got {type(result)}"
    # Graceful degradation: if RAG is unavailable, returns empty string
    # If RAG IS available, it should contain the knowledge header
    if result:
        assert "ARIA code-RAG knowledge" in result, (
            "RAG result should contain the knowledge header when available"
        )


def test_rf2145_build_system_prompt_self_mode_has_contract():
    """build_system_prompt in self-mode includes the operating contract."""
    from pathlib import Path
    from aria_cli.prompt import build_system_prompt
    prompt = build_system_prompt(root=Path.cwd(), self_mode=True)
    assert "OPERATING CONTRACT" in prompt, (
        "Self-mode prompt should include the operating contract"
    )
    assert "THIS IS ARIA'S OWN ECOSYSTEM" in prompt, (
        "Self-mode prompt should include the self-mode section"
    )


def test_rf2145_build_system_prompt_self_mode_with_repo_root():
    """build_system_prompt with repo_root includes binding repo rules."""
    from pathlib import Path
    from aria_cli.prompt import build_system_prompt
    prompt = build_system_prompt(
        root=Path.cwd(), self_mode=True, repo_root=Path.cwd()
    )
    assert "BINDING REPO RULES" in prompt, (
        "Self-mode prompt with repo_root should include binding repo rules"
    )
    assert "CLAUDE.md" in prompt, (
        "Self-mode prompt with repo_root should include CLAUDE.md content"
    )


def test_rf2145_build_system_prompt_general_mode_no_rag():
    """build_system_prompt in general mode does NOT include RAG (only self-mode)."""
    from pathlib import Path
    from aria_cli.prompt import build_system_prompt
    prompt = build_system_prompt(root=Path.cwd(), self_mode=False)
    assert "ARIA code-RAG knowledge" not in prompt, (
        "General mode should not include RAG knowledge"
    )
    assert "BINDING REPO RULES" not in prompt, (
        "General mode should not include binding repo rules"
    )
    assert "OPERATING CONTRACT" in prompt, (
        "General mode should include the operating contract"
    )
