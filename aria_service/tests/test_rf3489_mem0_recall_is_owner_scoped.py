"""R-F3489 — mem0 recall was unscoped: any user could read another user's notebook.

THE LEAK. `mem0.retrieve_for_query(query)` took a query and NOTHING ELSE. It scanned the
SHARED knowledge fact cache, kept every fact tagged `mem0:`, and returned the matches as a
prompt-injection block headed "facts captured from prior conversations". Whose prior
conversations was never asked. One user's chat turn could pull notebook facts summarised
from ANOTHER user's chats directly into the model context — and mem0 summaries are exactly
the sensitive residue of a conversation: names, deal stages, counterparty preferences,
constraints.

This is the cross-tenant class this codebase has already been bitten by five times, on the
one path that feeds the model rather than a report.

THE FIX, both ends, because either alone is useless:
  * WRITE — the owner is recorded on the fact (`...:owner_<key>`), appended so the
    existing provenance parser is untouched, and omitted when unknown so a fact is never
    MIS-attributed.
  * READ — recall is scoped to the asking user, and FAILS CLOSED with no owner. Serving
    someone else's conversation history to whoever asks is the failure being removed, so
    "unknown asker" resolves to "no recall", never to "everything".

LEGACY FACTS carry no owner and are therefore unattributable. They are withheld by
default: unattributable is not the same as mine. A genuinely single-user deployment can
set `ARIA_MEM0_RECALL_UNOWNED=1` — a deliberate, recorded operator choice.

§13 COMPLIANCE: `aria_chat_stream` is a subset-fork of `aria_chat`. Both the recall call
and the store dispatch are threaded in BOTH paths, and the tests below assert that, because
a fix applied to one fork only is how this codebase has leaked before.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from aria_service.intel import mem0

# R-F3597 — resolve source BY NAME through the current AST. `inspect.getsource`
# slices the file at the IMPORTED line numbers, so a concurrent edit during a
# long run returns a DIFFERENT function's body (measured 2026-07-31).
from ._source_probe import function_source

# R-F3605 — `from . import X` resolves from the PARENT PACKAGE ATTRIBUTE,
# not sys.modules. Patching sys.modules alone is a no-op once anything has
# imported the target, which is why this file passed alone and failed in-suite.
from ._module_stub import stub_submodule


@pytest.fixture
def cache(monkeypatch):
    """A shared fact cache holding two users' notebooks plus a legacy fact."""
    facts = [
        {"topic": "alice deal", "content": "Alice's counterparty prefers FOB terms",
         "source": "mem0:session_aaa:2026-07-30T10:00:00Z:owner_user-alice"},
        {"topic": "bob deal", "content": "Bob's counterparty prefers CIF terms",
         "source": "mem0:session_bbb:2026-07-30T11:00:00Z:owner_user-bob"},
        {"topic": "legacy deal", "content": "Legacy counterparty prefers EXW terms",
         "source": "mem0:session_ccc:2026-07-29T09:00:00Z"},
    ]

    class _K:
        _cache = {"facts": facts}

    import sys
    stub_submodule(monkeypatch, "aria_service.intel.knowledge", _K)
    monkeypatch.setenv("ARIA_MEM0_ENABLED", "1")
    monkeypatch.delenv("ARIA_MEM0_RECALL_UNOWNED", raising=False)
    return facts


def test_capability_one_user_cannot_read_anothers_notebook(cache):
    """THE LEAK, driven directly."""
    out = mem0.retrieve_for_query("counterparty prefers terms", owner_key="user-alice")
    assert "FOB" in out, "the owner's own fact was not recalled"
    assert "CIF" not in out, (
        "another user's notebook fact was served into the model context")


def test_the_other_user_sees_only_their_own(cache):
    out = mem0.retrieve_for_query("counterparty prefers terms", owner_key="user-bob")
    assert "CIF" in out and "FOB" not in out


def test_no_owner_means_no_recall_not_everything(cache):
    """FAIL CLOSED. An unidentified asker must not resolve to 'serve all notebooks'."""
    out = mem0.retrieve_for_query("counterparty prefers terms")
    assert out == "", f"unscoped recall returned content: {out[:200]}"
    assert mem0.retrieve_for_query("counterparty prefers terms", owner_key="  ") == ""


def test_legacy_unowned_facts_are_withheld_by_default(cache):
    """Unattributable is not the same as mine."""
    out = mem0.retrieve_for_query("counterparty prefers terms", owner_key="user-alice")
    assert "EXW" not in out, "an unattributable legacy fact was served to a named user"


def test_single_tenant_operators_can_opt_in(cache, monkeypatch):
    """The escape hatch is explicit and recorded, not a silent default."""
    monkeypatch.setenv("ARIA_MEM0_RECALL_UNOWNED", "1")
    out = mem0.retrieve_for_query("counterparty prefers terms", owner_key="user-alice")
    assert "EXW" in out, "the documented single-tenant opt-in does not work"
    assert "CIF" not in out, (
        "the opt-in must only unlock UNOWNED facts, never another user's owned facts")


def test_owner_parsing_handles_both_formats():
    assert mem0._owner_of("mem0:session_x:2026-07-30T10:00:00Z:owner_u1") == "u1"
    assert mem0._owner_of("mem0:session_x:2026-07-30T10:00:00Z") == ""
    assert mem0._owner_of("") == ""


def test_the_owner_suffix_does_not_break_provenance_rendering(cache):
    """The date marker is parsed as split(':', 2)[2][:10]; appending the owner must not
    corrupt it, or every recalled line would show the wrong provenance."""
    out = mem0.retrieve_for_query("counterparty prefers terms", owner_key="user-alice")
    assert "(mem0 2026-07-30)" in out, out


def test_both_chat_paths_are_threaded():
    """§13 — aria_chat_stream is a subset-fork of aria_chat. A scoping fix applied to one
    fork only is how this codebase has leaked before, so assert BOTH."""
    src = (pathlib.Path(__file__).resolve().parents[1] / "aria_engine.py").read_text(
        encoding="utf-8", errors="replace")
    assert src.count("_build_7_layer_context, message, intel_data,") == 2, (
        "the owner is not threaded into BOTH chat paths")
    assert src.count("owner_key=user_id") == 2, (
        "the owner is not recorded at BOTH summarise_and_store dispatch sites")
    assert "_mem0_retrieve(message, owner_key=owner_key)" in src


def test_the_store_path_records_an_owner():
    """Without this the read-side scope would simply withhold everything forever."""
    import inspect
    src = function_source(mem0, "summarise_and_store")
    assert "owner_key" in src
    assert ':owner_{_own}"' in src or ":owner_" in src


def test_system_scope_exists_for_internal_verification(cache):
    """The cited-artifact verifier and the memory diagnostic must still SEE the tier —
    neither injects another user's notebook into anyone's context. Without an explicit
    scope they would have silently returned nothing, which for the diagnostic is a false
    clean (ok=True, empty preview) and for the verifier means citations read as unfound."""
    out = mem0.retrieve_for_query("counterparty prefers terms", system_scope=True)
    assert "FOB" in out and "CIF" in out, "system scope cannot see the tier"


def test_the_chat_engine_never_uses_system_scope():
    """THE GUARD that keeps the exception an exception. system_scope in the chat path
    would restore the leak in one word, and it would look deliberate."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1] / "aria_engine.py").read_text(
        encoding="utf-8", errors="replace")
    assert "system_scope" not in src, (
        "the chat engine uses system_scope — that reinstates cross-user notebook recall")


def test_only_the_two_internal_callers_use_it():
    """A count, so a third caller has to be a deliberate decision rather than a drift."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    users = [
        p.name for p in root.rglob("*.py")
        if "tests" not in p.parts and "system_scope=True" in p.read_text(
            encoding="utf-8", errors="replace")
    ]
    assert sorted(users) == sorted(["memory_diagnostics.py", "mem0_notebook.py"]), (
        f"unexpected system_scope callers: {users}")
