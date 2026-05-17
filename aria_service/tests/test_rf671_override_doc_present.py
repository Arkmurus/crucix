"""R-F671 — Phase A override audit trail is in the repo.

Audit 2026-05-17 closing-session report flagged that R-F661 / R-F662
commit messages said "explicit operator override granted 2026-05-17"
but didn't include the exact CLAUDE.md §1 phrase:

  "I understand Phase A gate #X is open. Override anyway."

R-F671 backfills the audit trail in
docs/phase_a_override_2026_05_17.md. This test pins the doc's
existence and key content so a future cleanup doesn't quietly delete
the audit record.
"""
from __future__ import annotations

from pathlib import Path


_DOC = Path(__file__).resolve().parent.parent.parent / "docs" / "phase_a_override_2026_05_17.md"


def test_rf671_doc_exists():
    assert _DOC.exists(), (
        f"R-F671: {_DOC} missing — Phase A override audit trail must "
        f"remain in the repo per CLAUDE.md §1."
    )


def test_rf671_doc_quotes_override_phrase():
    """The doc must contain the verbatim CLAUDE.md §1 phrase so an
    audit grep finds it without ambiguity."""
    src = _DOC.read_text(encoding="utf-8")
    assert "I understand Phase A gates #2/#5/#6/#7 are open. Override anyway." in src, (
        "R-F671: exact CLAUDE.md §1 override phrase missing from doc"
    )


def test_rf671_doc_lists_r_numbers():
    """The three R-numbers covered by the override must be explicitly
    named in the doc — so a future R-F-grep can map override → commit."""
    src = _DOC.read_text(encoding="utf-8")
    for rn in ("R-F661", "R-F662", "R-F663"):
        assert rn in src, f"R-F671: {rn} missing from override doc"


def test_rf671_doc_names_open_gates_at_time_of_override():
    src = _DOC.read_text(encoding="utf-8")
    # Should reference each of the four gates that were open
    for gate in ("#2", "#5", "#6", "#7"):
        assert gate in src, f"R-F671: open gate {gate} not named in doc"


def test_rf671_doc_notes_scope_discipline():
    """The override is not a blanket Phase B unlock. The doc must say so
    so a future commit can't cite this as carte blanche."""
    src = _DOC.read_text(encoding="utf-8").lower()
    assert "not a blanket" in src or "not.*blanket" in src or "fresh override" in src, (
        "R-F671: doc must explicitly state the override is not a blanket "
        "Phase B unlock — fresh overrides required for future work."
    )
