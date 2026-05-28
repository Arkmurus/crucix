"""
ARIA Trace Stream — joinable spine for everything else.

Why this exists
═══════════════
You now have three independent signals on every chat request:
  - feedback   (👍/👎 from the user)
  - verification (cited URLs vs fetched URLs)
  - cost       (tokens + USD per call, attributed by feature)

But there's no way to look at ONE bad answer and see all three. If a
team member says "ARIA gave a wrong answer at 3pm", you currently have
to grep three separate Redis indices and manually correlate timestamps.
This module fixes that: one trace record per chat request, linking out
to the feedback / verification / cost records so they can be inspected
together.

The killer use case
═══════════════════
  user reacts 👎 → /feedback fb_xxx → see trace_id → /trace tr_xxx
    → full lifecycle:
       question, response, tool used, every LLM call with cost,
       verification verdict + which URLs were unverified, feedback

That's the answer to "why was this reply bad" in one command.

How it works
════════════
Same pattern as cost_tracker: a contextvar holds the active trace_id.
chat_ep calls start_trace() at the top, which creates a record AND
sets the contextvar. Every cost record subsequently created (via the
metered LLM wrapper) reads the contextvar and self-attaches its
call_id to the trace's llm_calls list. The verification record is
attached explicitly after it's created. Feedback is attached
asynchronously when the reaction lands (the WA listener already
snapshots Q→A by msg_id; we add trace_id to that snapshot).

Storage
═══════
- Per-trace record at crucix:aria:trace:record:{id}
- Lightweight index at crucix:aria:trace:index (last 1000 summaries)
- 30-day TTL on detail, index rotates by cap

Important caveats
═════════════════
- The trace's llm_calls list grows during the request via append. Two
  parallel cost records writing simultaneously could race on the
  Redis JSON read-modify-write. For ARIA's volume (handful of QPS at
  most) this is fine. If we ever scale, switch to a Redis list/stream
  primitive instead of JSON.
- Background loops (research_task, student_quiz, etc) don't currently
  produce traces — they have no parent chat request. Their cost
  records still get attributed via the cost_tracker feature label;
  they just don't live inside a trace. That's intentional — traces
  represent user-facing requests. A future "background trace" type
  could be added if needed.
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from typing import Any

from . import redis_store as rs

logger = logging.getLogger("aria.trace")

TRACE_RECORD_PREFIX = "crucix:aria:trace:record:"
TRACE_INDEX_KEY = "crucix:aria:trace:index"
TRACE_TTL = 30 * 86400
_INDEX_CAP = 1000

# Contextvar for the active trace id. Read by cost_tracker.record_call
# (lazily, to avoid a circular import) so every llm.complete call inside
# a chat_ep request appends itself to the right trace.
_current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "aria_trace_id", default="",
)


def get_current_trace_id() -> str:
    # R-F996 — wire to brain
    from .engine_wiring import wire_success
    wire_success(
        module="trace_stream",
        summary="Get Current Trace Id",
        source_id="trace_stream:R-F996",
    )

    return _current_trace_id.get()


def set_trace_id(trace_id: str) -> contextvars.Token:
    return _current_trace_id.set(trace_id or "")


def reset_trace_id(token: contextvars.Token) -> None:
    try:
        _current_trace_id.reset(token)
    except Exception:
        pass


# ── Trace lifecycle ────────────────────────────────────────────────────────

def _trace_key(trace_id: str) -> str:
    return f"{TRACE_RECORD_PREFIX}{trace_id}"


async def start_trace(
    *,
    question: str,
    session_id: str = "",
    user: str = "",
    source: str = "chat",
    metadata: dict | None = None,
) -> str:
    """Create a new trace record + set the current trace_id contextvar.
    Returns the trace_id so the caller can attach it to the response or
    the WhatsApp snapshot for later feedback joining."""
    trace_id = f"tr_{int(time.time() * 1000)}_{uuid.uuid4().hex[:6]}"
    record = {
        "id": trace_id,
        "ts_start": time.time(),
        "ts_end": None,
        "session_id": session_id or "",
        "user": (user or "")[:120],
        "source": source,  # chat | research_task | eval | other
        "question": (question or "")[:4000],
        "response": "",
        "tool_used": "",
        "tool_context_size": 0,
        "llm_calls": [],          # appended by cost_tracker via attach_call_to_current_trace
        "verification_id": None,
        "feedback_id": None,
        "cost_total_usd": 0.0,
        "tokens_total": 0,
        "latency_ms": 0,
        "status": "in_progress",
        "error": "",
        "metadata": metadata or {},
    }
    try:
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
    except Exception as e:
        logger.warning("start_trace persist failed: %s", e)
    set_trace_id(trace_id)
    return trace_id


async def attach_call_to_current_trace(call_record: dict) -> None:
    """Append a cost-tracker call record to the current trace's llm_calls
    list. Called from cost_tracker.record_call after the call is persisted.

    Read-modify-write on the trace JSON. For ARIA's volume this is fine;
    if we ever need higher throughput, swap to a Redis list under the
    trace key.
    """
    trace_id = get_current_trace_id()
    if not trace_id:
        return
    try:
        record = await rs.get_json(_trace_key(trace_id))
        if not record:
            return
        # Lightweight summary inside the trace — keeps the record bounded
        # even if a single request fires 20 LLM calls
        call_summary = {
            "call_id": call_record.get("id"),
            "model": call_record.get("model"),
            "feature": call_record.get("feature"),
            "input_tokens": call_record.get("input_tokens", 0),
            "output_tokens": call_record.get("output_tokens", 0),
            "total_tokens": call_record.get("total_tokens", 0),
            "cost_usd": call_record.get("cost_usd", 0.0),
            "latency_ms": call_record.get("latency_ms", 0),
            "success": call_record.get("success", True),
        }
        calls = record.get("llm_calls") or []
        calls.append(call_summary)
        record["llm_calls"] = calls[:50]  # cap at 50 calls per trace
        record["cost_total_usd"] = round(
            (record.get("cost_total_usd") or 0.0) + (call_record.get("cost_usd") or 0.0),
            6,
        )
        record["tokens_total"] = (record.get("tokens_total") or 0) + (call_record.get("total_tokens") or 0)
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
    except Exception as e:
        logger.debug("attach_call_to_current_trace failed: %s", e)


async def attach_verification(trace_id: str, verification_summary: dict | None) -> None:
    """Attach a verification record to a trace. Called from chat_ep after
    source_verifier.record_verification returns. Stores the full summary
    inline so /trace shows the verdict without a second lookup."""
    if not trace_id or not verification_summary:
        return
    try:
        record = await rs.get_json(_trace_key(trace_id))
        if not record:
            return
        record["verification_id"] = verification_summary.get("id")
        record["verification"] = {
            "verdict": verification_summary.get("verdict"),
            "grounded_rate": verification_summary.get("grounded_rate"),
            "cited": verification_summary.get("cited", 0),
            "unverified": verification_summary.get("unverified", 0),
        }
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
    except Exception as e:
        logger.debug("attach_verification failed: %s", e)


async def attach_judgment(trace_id: str, judgment_summary: dict | None) -> None:
    """Attach an honesty-judge result to a trace. Called from
    honesty_judge.record_judgment after the judge call completes
    (typically a minute or so after the trace itself finished, since
    the judge runs in a background task)."""
    if not trace_id or not judgment_summary:
        return
    try:
        record = await rs.get_json(_trace_key(trace_id))
        if not record:
            return
        record["judgment_id"] = judgment_summary.get("id")
        record["judgment"] = {
            "status": judgment_summary.get("status"),
            "honesty_score": judgment_summary.get("honesty_score"),
            "claims_total": judgment_summary.get("claims_total", 0),
            "supported_count": judgment_summary.get("supported_count", 0),
        }
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
        # Bubble up to the index so /trace bad picks low-honesty traces
        await _refresh_index_entry(record)
    except Exception as e:
        logger.debug("attach_judgment failed: %s", e)


async def attach_feedback(trace_id: str, feedback_record: dict | None) -> None:
    """Attach a feedback record to a trace. Called from feedback.record_feedback
    after the snapshot lookup yields a trace_id."""
    if not trace_id or not feedback_record:
        return
    try:
        record = await rs.get_json(_trace_key(trace_id))
        if not record:
            return
        record["feedback_id"] = feedback_record.get("id")
        record["feedback"] = {
            "sentiment": feedback_record.get("sentiment"),
            "emoji": feedback_record.get("emoji"),
            "reactor": feedback_record.get("reactor"),
        }
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
        # Bubble up to the index so /trace bad picks it up
        await _refresh_index_entry(record)
    except Exception as e:
        logger.debug("attach_feedback failed: %s", e)


async def finish_trace(
    trace_id: str,
    *,
    response: str = "",
    tool_used: str = "",
    tool_context_size: int = 0,
    status: str = "ok",
    error: str = "",
) -> dict | None:
    """Mark a trace complete: set ts_end, latency, response preview, status."""
    if not trace_id:
        return None
    try:
        record = await rs.get_json(_trace_key(trace_id))
        if not record:
            return None
        ts_end = time.time()
        record["ts_end"] = ts_end
        record["latency_ms"] = int((ts_end - record.get("ts_start", ts_end)) * 1000)
        record["response"] = (response or "")[:6000]
        record["tool_used"] = tool_used or record.get("tool_used", "")
        record["tool_context_size"] = int(tool_context_size or 0)
        record["status"] = status or "ok"
        if error:
            record["error"] = error[:500]
        await rs.set_json(_trace_key(trace_id), record, ex=TRACE_TTL)
        # Index entry for /trace recent
        await _refresh_index_entry(record)
        logger.info(
            "Trace %s done: status=%s latency=%dms calls=%d cost=$%s",
            trace_id, record["status"], record["latency_ms"],
            len(record.get("llm_calls", [])), record.get("cost_total_usd"),
        )
        return record
    except Exception as e:
        logger.warning("finish_trace failed: %s", e)
        return None


async def _refresh_index_entry(record: dict) -> None:
    """Insert/update the trace's summary in the lightweight index. Called
    from finish_trace and attach_feedback so the index reflects the latest
    state of recently-completed traces."""
    try:
        index = await rs.get_json(TRACE_INDEX_KEY) or []
        tid = record.get("id")
        index = [e for e in index if e.get("id") != tid]
        verification = record.get("verification") or {}
        feedback = record.get("feedback") or {}
        judgment = record.get("judgment") or {}
        index.insert(0, {
            "id": tid,
            "ts_start": record.get("ts_start"),
            "session_id": record.get("session_id", ""),
            "user": record.get("user", ""),
            "source": record.get("source", "chat"),
            "question_preview": (record.get("question") or "")[:140],
            "tool_used": record.get("tool_used", ""),
            "calls": len(record.get("llm_calls") or []),
            "tokens_total": record.get("tokens_total", 0),
            "cost_total_usd": record.get("cost_total_usd", 0.0),
            "latency_ms": record.get("latency_ms", 0),
            "status": record.get("status", "ok"),
            "verdict": verification.get("verdict"),
            "grounded_rate": verification.get("grounded_rate"),
            "feedback_sentiment": feedback.get("sentiment"),
            "honesty_score": judgment.get("honesty_score"),
        })
        index = index[:_INDEX_CAP]
        await rs.set_json(TRACE_INDEX_KEY, index, ex=TRACE_TTL)
    except Exception as e:
        logger.debug("_refresh_index_entry failed: %s", e)


# ── Read API ───────────────────────────────────────────────────────────────

async def get_trace(trace_id: str) -> dict | None:
    try:
        return await rs.get_json(_trace_key(trace_id))
    except Exception:
        return None


async def list_traces(
    limit: int = 30,
    *,
    status_filter: str | None = None,
    source_filter: str | None = None,
    bad_only: bool = False,
) -> list[dict]:
    """List recent traces from the index. bad_only=True returns traces
    that either failed, scored ungrounded, or got 👎 feedback — i.e. the
    set the team should investigate first."""
    try:
        index = await rs.get_json(TRACE_INDEX_KEY) or []
        if status_filter:
            index = [e for e in index if e.get("status") == status_filter]
        if source_filter:
            index = [e for e in index if e.get("source") == source_filter]
        if bad_only:
            def _is_bad(e):
                if e.get("status") and e["status"] != "ok":
                    return True
                if e.get("verdict") == "ungrounded":
                    return True
                if e.get("feedback_sentiment") == "negative":
                    return True
                # Honesty score below 0.7 = dishonest [CONFIRMED] tags
                hs = e.get("honesty_score")
                if hs is not None and hs < 0.7:
                    return True
                return False
            index = [e for e in index if _is_bad(e)]
        return index[: max(1, min(limit, _INDEX_CAP))]
    except Exception:
        return []


async def get_trace_stats() -> dict:
    """Aggregate trace counts + averages over the index."""
    try:
        index = await rs.get_json(TRACE_INDEX_KEY) or []
        n = len(index)
        if n == 0:
            return {"total": 0}
        total_cost = sum(e.get("cost_total_usd", 0) or 0 for e in index)
        total_tokens = sum(e.get("tokens_total", 0) or 0 for e in index)
        total_latency = sum(e.get("latency_ms", 0) or 0 for e in index)
        ok_n = sum(1 for e in index if e.get("status") == "ok")
        bad_n = sum(
            1 for e in index
            if e.get("status") != "ok"
            or e.get("verdict") == "ungrounded"
            or e.get("feedback_sentiment") == "negative"
        )
        return {
            "total": n,
            "ok": ok_n,
            "bad": bad_n,
            "mean_cost_usd": round(total_cost / n, 6),
            "mean_tokens": round(total_tokens / n, 1),
            "mean_latency_ms": round(total_latency / n, 1),
            "total_cost_usd": round(total_cost, 6),
        }
    except Exception as e:
        return {"error": str(e)}
