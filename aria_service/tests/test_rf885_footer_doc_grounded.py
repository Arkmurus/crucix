"""R-F885 — footer labels document-grounded answers as such, not "from memory".

Live 2026-05-25: a full document-grounded contract review (quoting the attached
SPA by article/page) carried a footer reading "Tools: (none — from memory /
training)". That mislabels a document-grounded answer as ungrounded recall — an
honesty defect on a Phase-A system. build_footer now takes document_grounded;
when set and no tool fired, the source line reads "attached document (grounded)".
"""
from __future__ import annotations

import os

os.environ.setdefault("ARIA_CONFIDENCE_FOOTER", "1")
from aria_service.intel import confidence_footer as cf

# R-F3786/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


def test_doc_grounded_no_tool_shows_attached_document():
    f = cf.build_footer(
        "Payment terms do not secure either party. [from ATTACHED DOCUMENT: SPA] "
        "Export License No is blank (TBA).",
        verification=None, tools_used=None, build_rev="R-F885",
        document_grounded=True,
    )
    assert "attached document (grounded)" in f
    assert "from memory / training" not in f


def test_non_doc_no_tool_still_says_from_memory():
    f = cf.build_footer(
        "NATO Category A goods need an SITCL licence.",
        verification=None, tools_used=None, build_rev="R-F885",
        document_grounded=False,
    )
    assert "from memory / training" in f


def test_real_tool_still_wins_over_doc_flag():
    # if a tool actually fired, show the tool — doc flag doesn't override it
    f = cf.build_footer(
        "Screened: no hits. [from sanctions_screen]",
        verification=None, tools_used=["sanctions_screen"], build_rev="R-F885",
        document_grounded=True,
    )
    # R-F1170 — human-readable labels
    assert "sanctions screening" in f or "sanctions_screen" in f
    assert "from memory / training" not in f


def test_callers_pass_document_grounded():
    import inspect
    from aria_service.routes import aria as a
    src = module_source(a)
    assert src.count('document_grounded=("[ATTACHED DOCUMENT" in (req.message or "")') >= 2
