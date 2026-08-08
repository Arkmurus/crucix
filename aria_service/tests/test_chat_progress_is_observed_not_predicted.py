"""The brain must publish what it IS doing, so a channel need not guess.

R-F3664 deleted the WhatsApp interim messages that said "Running the numbers —
checking multiple sources" on a pure 7s timer, because the listener had no idea
what the brain was doing and asserted it anyway. It closed with the condition for
ever saying anything specific again:

    "A research-flavoured interim can only return here if a job-kind flag is
     plumbed through, and then it must be gated on it."

This is that flag. ChatRequest.progress_job_id is stamped by the async wrapper
onto its own synchronous re-entry, and chat_ep writes {stage:'tool', tool:<name>}
into the job record AFTER the tool is chosen and immediately BEFORE it runs. The
poll endpoint returns `{job_id, **job}`, so the field reaches the caller with no
new endpoint.

WHY IT IS WRITTEN THERE AND NOT EARLIER. Detecting intent up front and announcing
it would rebuild the same lie one layer up: the ML detector can still decline, and
a prediction that does not happen is precisely the fabrication R-F3664 removed.
Observed-after-start is the only position where the claim is guaranteed true.

THE OWNER RE-STAMP IS LOAD-BEARING. _chat_job_set REPLACES the record. A progress
write that omitted session_id/user_id would erase the owner, and R-F1852's
ownership guard on /chat/result would then answer not_found to the very user who
asked — turning a cosmetic improvement into a silent delivery failure.

NOTE: no R-number — data/r_number_reservations.json is the peer agent's ledger.
"""
from __future__ import annotations

import inspect
import re

from aria_service.routes import aria as R

# R-F3781/§16 — NOT inspect.getsource: it slices at line numbers captured
# AT IMPORT, so a mid-run edit silently returns a DIFFERENT function's body.
from ._source_probe import module_source


SRC = module_source(R)


def test_chat_request_carries_the_progress_job_id():
    assert "progress_job_id" in R.ChatRequest.model_fields, (
        "the async wrapper has nowhere to hand the job id to the path that "
        "actually runs the tool"
    )
    assert R.ChatRequest(message="hello").progress_job_id == "", (
        "must default empty so the sync path and external callers are unaffected"
    )


def test_the_async_wrapper_stamps_its_own_job_id():
    assert re.search(
        r"model_copy\(update=\{[^}]*progress_job_id[^}]*\}", SRC, re.S
    ), (
        "the async wrapper must pass its job id into the synchronous re-entry, "
        "or the chat path cannot report progress on it"
    )


def _progress_block() -> str:
    i = SRC.find("if getattr(req, \"progress_job_id\", \"\"):")
    assert i != -1, "the progress write is gone — the interim loses its only fact"
    return SRC[i:i + 900]


def test_progress_is_written_only_when_a_tool_was_chosen():
    """It must sit inside the tool branch, after the tool is known."""
    tool_at = SRC.find("tool_used = intent.get(\"tool\")")
    prog_at = SRC.find("if getattr(req, \"progress_job_id\", \"\"):")
    assert tool_at != -1 and prog_at > tool_at, (
        "progress is written before the tool is chosen — that is a prediction, "
        "which is the failure R-F3664 removed"
    )


def test_the_progress_write_declares_the_stage_and_the_tool():
    blk = _progress_block()
    assert '"stage": "tool"' in blk and '"tool": tool_used' in blk, (
        "the consumer gates on stage=='tool'; without both fields it cannot "
        "distinguish observed work from an empty record"
    )


def test_the_progress_write_restamps_the_owner():
    """_chat_job_set REPLACES; dropping the owner breaks R-F1852 ownership."""
    blk = _progress_block()
    assert '"session_id": session_id' in blk, "session_id must be re-stamped"
    assert '"user_id"' in blk, (
        "user_id must be re-stamped — otherwise /chat/result answers not_found "
        "to the job's own owner and the answer is never delivered"
    )


def test_the_progress_write_cannot_cost_the_caller_their_answer():
    blk = _progress_block()
    assert "try:" in blk and "except Exception" in blk, (
        "a best-effort progress update must never propagate into the chat path"
    )
