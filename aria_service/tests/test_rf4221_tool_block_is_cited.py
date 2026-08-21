"""R-F4221 / C-201: the citation extractor never saw the tool block, on EITHER path.

C-199 taught the extractor to read `memory://` provenance. C-200 stopped the
stream fork blanking its context. Both were real, and neither delivered a
citation, because of a third thing underneath them:

**tool output does not reach the engine as a context argument — it is folded into
the MESSAGE.** `routes/aria.py` builds

    message_for_llm = f"{message_for_llm}\n\n{_wrap_tool_block(tool_context)}"

and then calls `aria_chat(message_for_llm, ...)`. Inside the engine, `context` is
the 7-layer KNOWLEDGE context from `_build_7_layer_context` — a different thing
entirely. So `chat_sources.extract(response_text, tool_context=context)` was
reading a context that never contained the tool's URLs, and the block that did
contain them sat unread in `message`.

MEASURED LIVE (2026-08-21, aria-intel `69ec8684`): a `/api/aria/chat/stream` turn
emitted `tool_running` x6, `tool_done`, and a footer reading "Tools: deep web
search" — a tool demonstrably ran — and produced **no `sources` event at all**.

`_wrap_tool_block`'s own docstring says it is "the ONLY place tool output should
enter message_for_llm", so there is exactly one marker to key on. That marker now
lives in ONE module and both sides import it, rather than the engine matching a
copied literal that could drift (the forked-measure shape R-F2639 forbids).
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest

import aria_service.aria_engine as engine
from aria_service.intel import chat_sources as cs


TOOL_BLOCK = (
    cs.TOOL_BLOCK_PREAMBLE
    + "\n[TOOL: brave_answer → web_explorer]\n"
      "FACTS RETRIEVED (verbatim, with provenance):\n"
      "  [1] Angola force modernisation\n"
      "      URL: https://www.defenceweb.co.za/land/angola/\n"
      "  [2] prior recall\n"
      "      URL: memory://2f6008008499\n"
)


# ── the marker has ONE definition ────────────────────────────────────────────

def test_routes_and_engine_share_one_tool_block_marker():
    from aria_service.routes import aria as routes
    assert routes._TOOL_BLOCK_PREAMBLE == cs.TOOL_BLOCK_PREAMBLE, (
        "two copies of the tool-block marker will drift, and the day they do the "
        "engine silently stops finding the block (R-F2639: one measure)")


# ── the helper ───────────────────────────────────────────────────────────────

def test_tool_block_from_returns_the_block():
    msg = "What is the Angola outlook?\n\n" + TOOL_BLOCK
    got = cs.tool_block_from(msg)
    assert "defenceweb.co.za" in got
    assert "memory://2f6008008499" in got


def test_tool_block_from_returns_empty_when_no_tool_ran():
    """Absence must read as absence — never invent a block that was not there."""
    assert cs.tool_block_from("Just a plain question with no tool output") == ""
    assert cs.tool_block_from("") == ""
    assert cs.tool_block_from(None) == ""


def test_tool_block_from_excludes_the_users_own_text():
    """Only the trusted block — a URL the USER pasted is not ARIA's evidence."""
    msg = "See https://user-pasted.example/thing please\n\n" + TOOL_BLOCK
    got = cs.tool_block_from(msg)
    assert "user-pasted.example" not in got, got
    assert "defenceweb.co.za" in got


def test_the_block_actually_yields_citations():
    """End to end through the real extractor."""
    srcs = cs.extract("Angola has modernised.", tool_context=cs.tool_block_from(
        "q\n\n" + TOOL_BLOCK))
    types = {s.get("type") for s in srcs}
    assert "url" in types and "memory" in types, srcs


# ── both chat paths must feed it in (§13) ────────────────────────────────────

def _extract_calls():
    path = inspect.getsourcefile(engine)
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    return [
        (n.lineno, n) for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "extract" and "_cs" in ast.unparse(n.func.value)
    ]


def test_every_chat_path_feeds_the_tool_block_to_the_extractor():
    """The defect, as a rule that closes the class rather than two edits."""
    missing = []
    for lineno, call in _extract_calls():
        rendered = " ".join(ast.unparse(kw.value) for kw in call.keywords
                            if kw.arg == "tool_context")
        if "tool_block_from" not in rendered:
            missing.append((lineno, rendered or "<no tool_context>"))
    assert not missing, (
        "these chat paths never show the extractor the tool block, so no tool "
        "URL and no memory:// ref can ever be cited from them:\n"
        + "\n".join(f"  aria_engine.py:{ln}  tool_context={r}" for ln, r in missing)
        + "\nTool output arrives inside the MESSAGE (routes wraps it with "
          "_wrap_tool_block); the 7-layer `context` does not contain it."
    )


def test_the_knowledge_context_is_still_included():
    """R-F4220's fix must survive: both sources of evidence, not one swapped for the other."""
    for lineno, call in _extract_calls():
        rendered = " ".join(ast.unparse(kw.value) for kw in call.keywords
                            if kw.arg == "tool_context")
        assert "context" in rendered, (lineno, rendered)
