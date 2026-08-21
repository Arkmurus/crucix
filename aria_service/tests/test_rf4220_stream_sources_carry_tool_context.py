"""R-F4220 / C-200: the streaming chat fork dropped the tool context.

CLAUDE.md §13 (stream-bypass rule): `aria_chat_stream` is a subset-fork of
`aria_chat`, and every post-response hook must be mirrored into BOTH paths.

`_aria_chat_stream_impl` called:

    _sources = _cs.extract(response_text, tool_context="")     # <- empty!

directly under a comment reading *"mirror of the non-stream sources[] field per
CLAUDE.md §13"*. It cited the rule it was breaking. The non-stream path passes
`tool_context=context or ""`, and so does the stream's own grounding check 400
lines earlier in the SAME function — so `context` was in scope the whole time.

CONSEQUENCE. `chat_sources.extract` finds citations in `response_text + tool_context`.
With the tool context blanked, the ONLY citations the streaming path could ever
emit were URLs the model happened to type into its own prose. Every tool-derived
source was invisible: web_explorer's result URLs, sanctions live-check blocks,
and — after R-F4219 — ARIA's own `memory://` provenance. The web UI streams, so
this is the path most users see.

This is the same shape as C-199 one layer up: the evidence existed and the
surface that publishes it could not see it.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import aria_service.aria_engine as engine
from aria_service.intel import chat_sources as cs


def _extract_calls() -> list[tuple[int, ast.Call]]:
    """Every chat_sources.extract(...) call in aria_engine, resolved from source."""
    path = inspect.getsourcefile(engine)
    tree = ast.parse(pathlib.Path(path).read_text(encoding="utf-8", errors="replace"))
    out = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "extract"
                and "_cs" in ast.unparse(node.func.value)):
            out.append((node.lineno, node))
    return out


def test_both_chat_paths_call_the_extractor():
    """§13: the hook must exist on BOTH forks. A guard over one path is no guard."""
    calls = _extract_calls()
    assert len(calls) >= 2, (
        f"expected the extractor on both the stream and non-stream chat paths, "
        f"found {len(calls)}")


def test_no_chat_path_blanks_the_tool_context():
    """The defect, stated as a rule that closes the class.

    A literal "" here silently removes every tool-derived citation from that
    path. It is not a stylistic choice — it is the difference between an answer
    that shows its evidence and one that looks unsourced.
    """
    offenders = []
    for lineno, call in _extract_calls():
        for kw in call.keywords:
            if kw.arg == "tool_context":
                rendered = ast.unparse(kw.value)
                if rendered in ('""', "''"):
                    offenders.append((lineno, rendered))
    assert not offenders, (
        "these chat_sources.extract() calls pass an EMPTY tool_context, so no "
        "tool-derived source (web_explorer URLs, sanctions live-check, ARIA's "
        "own memory:// provenance) can ever be cited on that path:\n"
        + "\n".join(f"  aria_engine.py:{ln}  tool_context={r}" for ln, r in offenders)
        + "\nPass the real context (CLAUDE.md §13 — mirror hooks into BOTH forks)."
    )


def test_every_extract_call_passes_a_tool_context_at_all():
    """Omitting the kwarg defaults it to "" — the same defect, spelled differently."""
    missing = [
        lineno for lineno, call in _extract_calls()
        if not any(kw.arg == "tool_context" for kw in call.keywords)
    ]
    assert not missing, (
        f"aria_engine.py lines {missing} call extract() without tool_context; the "
        f"default is \"\", which silently drops every tool-derived citation")


# ── the behaviour the rule protects, driven directly ─────────────────────────

TOOL_CONTEXT = """
FACTS RETRIEVED (verbatim, with provenance):
  [1] [UNVERIFIED searxng:bing] Angola force modernisation
      URL: https://www.defenceweb.co.za/land/angola/
  [2] [UNVERIFIED memory] prior recall
      URL: memory://2f6008008499
"""


def test_blanking_the_context_provably_loses_every_tool_source():
    """Why the rule above matters, measured rather than asserted."""
    answer = "Angola has pursued a modest programme of force modernisation."
    with_ctx = cs.extract(answer, tool_context=TOOL_CONTEXT)
    without = cs.extract(answer, tool_context="")
    assert len(with_ctx) >= 2, with_ctx
    assert without == [], (
        "if this ever returns sources, the test below no longer demonstrates the "
        "loss and must be re-grounded")
    types = {s.get("type") for s in with_ctx}
    assert "url" in types and "memory" in types, types
