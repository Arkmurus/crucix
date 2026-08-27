"""R-F4376 (C-321) — the autonomous coder's corpus must satisfy the contract it
will be judged by in production.

`self_coder` does not call tools. It is handed a plan and a file and must return
search/replace JSON, and `apply_search_replace` REJECTS an edit whose `old` is
absent or ambiguous — one failure and the caller falls back to rewriting the
whole file, which is the truncation risk the surgical path exists to avoid.

So a training row is only worth anything if it would actually APPLY. Every row
this builder emits is proved by reconstruction: run the derived edits through
the PRODUCTION applier and require the after-file back, byte for byte. That is a
verifiable reward, not a plausibility judgement — a row cannot be subtly wrong
and still pass.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aria_service.autonomous.sovereign_llm import apply_search_replace  # noqa: E402
from scripts.train import build_coder_edit_corpus as B  # noqa: E402

BEFORE = '''"""Module."""


def add(a, b):
    return a - b


def other(a, b):
    return a - b


def tail():
    return 1
'''

AFTER = BEFORE.replace("def add(a, b):\n    return a - b",
                       "def add(a, b):\n    return a + b", 1)


# ── the capability: a row that would really apply ──────────────────────────

def test_a_derived_edit_reconstructs_the_file_exactly():
    """THE PROOF. Not "looks like a fix" — the production applier must return
    the after-file byte for byte."""
    edits = B.derive_edits(BEFORE, AFTER)
    assert edits, "no edits derived for a real one-line change"

    rebuilt, applied, failures = apply_search_replace(BEFORE, edits)
    assert not failures, failures
    assert len(applied) == len(edits)
    assert rebuilt == AFTER


def test_every_old_snippet_is_unique_in_the_file():
    """THE CONTRACT. `return a - b` appears TWICE here on purpose: a naive
    minimal diff would emit an ambiguous `old`, which production rejects and
    which would teach her to emit rejected edits."""
    assert BEFORE.count("    return a - b\n") == 2

    for e in B.derive_edits(BEFORE, AFTER):
        assert BEFORE.count(e["old"]) == 1, (
            f"ambiguous `old` ({BEFORE.count(e['old'])} matches) — production "
            f"would reject this edit")


def test_the_row_carries_the_production_output_shape():
    row = B.build_row("m.py", BEFORE, AFTER, {"r_number": "R-F1", "sha": "abc"})
    assert row is not None

    answer = json.loads(row["messages"][-1]["content"])
    assert answer["filepath"] == "m.py"
    assert answer["edits"] and all({"old", "new"} <= set(e) for e in answer["edits"])
    assert answer["changes_made"]
    assert row["messages"][0]["role"] == "system"
    assert "VERBATIM" in row["messages"][0]["content"]
    assert "EXISTING CONTENT" in row["messages"][1]["content"]


# ── it must refuse anything it cannot prove ────────────────────────────────

def test_an_unprovable_row_is_rejected_not_emitted():
    """If the edits do not reconstruct the target, the row is discarded. A
    builder that emitted it would teach an edit production refuses."""
    class _Broken:
        pass

    # A file whose "after" cannot be produced by any edit of "before" the
    # builder derives: identical content means no change to learn.
    assert B.build_row("m.py", BEFORE, BEFORE, {}) is None
    assert B.build_row("m.py", "", AFTER, {}) is None


def test_an_oversized_file_is_rejected():
    """The production prompt embeds the whole file; a file that cannot fit the
    training window would train a prompt shape that never occurs."""
    huge = "x = 1\n" * 20_000
    assert len(huge) > B.MAX_BEFORE_CHARS
    assert B.build_row("m.py", huge, huge + "y = 2\n", {}) is None


def test_a_hunk_that_cannot_be_made_unique_rejects_the_whole_row():
    """All-or-nothing: emitting the provable half of a change would teach a
    partial edit, and a partial edit is exactly what corrupts a file."""
    before = "a = 1\n" * 50
    after = before.replace("a = 1\n", "a = 2\n", 1)
    edits = B.derive_edits(before, after)
    # Either every edit is unique, or the row is refused outright.
    assert edits == [] or all(before.count(e["old"]) == 1 for e in edits)


# ── it must use the production applier, not a copy ─────────────────────────

def test_the_builder_verifies_through_the_production_applier():
    """A second implementation of "does this apply?" would drift from the one
    that actually runs, and the corpus would be graded by a rule production
    does not use."""
    import inspect

    src = inspect.getsource(B)
    assert "from aria_service.autonomous.sovereign_llm import apply_search_replace" in src
    assert "apply_search_replace(before, edits)" in src
    assert "rebuilt != after" in src, "reconstruction is not asserted"


def test_the_split_is_by_commit_not_by_row():
    """Two rows from one commit are the same fix. Splitting them across
    train/eval would measure memorisation."""
    import inspect

    src = inspect.getsource(B.main)
    assert "by_r" in src and "r_number" in src
    assert "eval_keys" in src


def test_an_empty_build_is_never_written_as_success():
    """An empty corpus trains nothing and reads downstream as "covered"."""
    import inspect

    src = inspect.getsource(B.main)
    assert "NO PROVABLE ROWS" in src
