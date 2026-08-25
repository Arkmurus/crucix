"""R-F4316 (C-264) — a question ARIA asked Claude must not stall silently.

THE DEFECT. Claude's side of this bridge is serviced only when a Claude session
runs the inbox. When none does, ARIA's question just sits there and nothing
anywhere says she is waiting. That is not hypothetical: a reply in this very log
went out FOUR DAYS late, and the apology in it names the cause — "the bridge is
only serviced when a Claude session runs the inbox, and no session did".

So the teaching loop had exactly the failure mode CLAUDE.md §19e exists to
forbid: work blocked on a human, left for that human to discover on their own.
The fix is not a scheduler — there is no durable Claude-side scheduler to build
on — it is to make the stall VISIBLE.

Three properties are load-bearing and each is pinned below:

  * TRI-STATE ON THE STORE. `_read_log` returns `[]` on failure, so "no messages"
    and "could not look" are the same value — the C-254 shape, in the module
    C-254 was found in. Here it would report a healthy teaching loop precisely
    when the store is broken. `pending_questions` reads strictly and reports
    `readable: False` / `count: None`, never a comforting zero.
  * ONCE PER QUESTION, NOT PER CHECK. This rides the existing 2-minute
    `collab_drain`. A gap per check is 720/day — the self-sustaining ledger flood
    recorded for `sanctions_coverage_degraded` and the Brave non-DD refusals.
  * ONLY CLAUDE CAN CLEAR IT. A reply counts only when `frm == "claude"`.
    Otherwise ARIA replying to her own question would mark the loop served.
"""
import asyncio
import time

import pytest

from aria_service.intel import collab_bridge as cb


def _msg(seq, frm, to, kind="note", reply_to="", ts=None, text="hello there"):
    return {"seq": seq, "id": f"cb_{seq}", "ts": ts if ts is not None else time.time(),
            "frm": frm, "to": to, "kind": kind, "reply_to": reply_to, "text": text}


@pytest.fixture
def log(monkeypatch):
    """Control the collab log and the announce dedupe store."""
    state = {"messages": [], "unreadable": False, "marks": {}, "gaps": []}

    async def _lrange(key, start, stop):
        if state["unreadable"]:
            raise RuntimeError("state store down")
        import json
        return [json.dumps(m) for m in reversed(state["messages"])]

    async def _get(key):
        return state["marks"].get(key)

    async def _set(key, val, ex=None):
        state["marks"][key] = val
        return True

    monkeypatch.setattr(cb.rs, "lrange", _lrange)
    monkeypatch.setattr(cb.rs, "get", _get)
    monkeypatch.setattr(cb.rs, "set", _set)
    monkeypatch.setattr(cb, "wire_failure",
                        lambda **kw: state["gaps"].append(kw))
    return state


HOUR = 3600.0


# ── the defect: an old unanswered question is surfaced ───────────────────────

def test_a_question_nobody_answered_is_reported(log):
    """CAPABILITY: the stall becomes visible instead of sitting in silence."""
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 20 * HOUR,
             text="Should I widen the retention gate?"),
    ]
    out = asyncio.run(cb.announce_stalled_questions())

    assert out["announced"] == 1
    assert len(log["gaps"]) == 1
    gap = log["gaps"][0]
    assert gap["gap_type"] == "collab_question_stalled"
    assert "cb_1" in gap["detail"]
    assert "waiting" in gap["detail"].lower()


def test_a_recent_question_is_not_nagged_about(log):
    """Below the threshold is normal working latency, not a stall."""
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 0.5 * HOUR),
    ]
    assert asyncio.run(cb.announce_stalled_questions())["announced"] == 0
    assert log["gaps"] == []


def test_an_answered_question_is_not_reported(log):
    """CAPABILITY: Claude's reply clears it — otherwise the signal is noise."""
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 20 * HOUR),
        _msg(2, "claude", "aria", reply_to="cb_1"),
    ]
    state = asyncio.run(cb.pending_questions())
    assert state["count"] == 0
    assert asyncio.run(cb.announce_stalled_questions())["announced"] == 0


def test_aria_cannot_clear_her_own_question(log):
    """Only CLAUDE answering counts, or the loop could mark itself served."""
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 20 * HOUR),
        _msg(2, "aria", "claude", reply_to="cb_1", text="following up"),
    ]
    assert asyncio.run(cb.pending_questions())["count"] == 1, (
        "ARIA replying to herself must not count as an answer"
    )


def test_only_questions_count_not_every_note(log):
    """An unprompted note is not a question and nobody owes it an answer."""
    log["messages"] = [_msg(1, "aria", "claude", kind="note", ts=time.time() - 40 * HOUR)]
    assert asyncio.run(cb.pending_questions())["count"] == 0


# ── the flood guard ──────────────────────────────────────────────────────────

def test_each_stalled_question_is_announced_once_not_every_drain(log):
    """CAPABILITY: this rides a 2-MINUTE loop.

    720 checks a day emitting a gap each is the self-sustaining flood that has
    already filled the 500-slot capability ledger twice in this codebase.
    """
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 20 * HOUR),
    ]
    first = asyncio.run(cb.announce_stalled_questions())
    later = [asyncio.run(cb.announce_stalled_questions()) for _ in range(5)]

    assert first["announced"] == 1
    assert all(r["announced"] == 0 for r in later), (
        "the same stalled question was announced again — at one drain every two "
        "minutes this fills the ledger within a day"
    )
    assert len(log["gaps"]) == 1


# ── tri-state: an unreadable store is never a comforting zero ────────────────

def test_an_unreadable_log_is_not_reported_as_nothing_waiting(log):
    """The C-254 shape, in the module C-254 was found in.

    `_read_log` swallows a store failure into `[]`. If this surface did the same,
    it would report a perfectly healthy teaching loop at exactly the moment the
    store is broken — an absence rendered as a measurement.
    """
    log["unreadable"] = True
    state = asyncio.run(cb.pending_questions())

    assert state["readable"] is False
    assert state["count"] is None, "an unreadable log must never render as 0 waiting"
    assert state["oldest_age_s"] is None

    out = asyncio.run(cb.announce_stalled_questions())
    assert out["readable"] is False and out["announced"] == 0


def test_the_plain_read_still_collapses_which_is_why_the_strict_one_exists(log):
    """Pins the contrast, so nobody 'simplifies' pending_questions back onto _read_log."""
    log["unreadable"] = True
    assert asyncio.run(cb._read_log()) == [], (
        "_read_log's [] on failure is its documented contract and other callers "
        "rely on it — the point is that pending_questions must NOT use it"
    )
    assert asyncio.run(cb._read_log_strict()) is None


def test_oldest_first_so_the_worst_stall_leads(log):
    log["messages"] = [
        _msg(1, "aria", "claude", kind="question", ts=time.time() - 8 * HOUR),
        _msg(2, "aria", "claude", kind="question", ts=time.time() - 30 * HOUR),
    ]
    state = asyncio.run(cb.pending_questions())
    assert [p["id"] for p in state["pending"]] == ["cb_2", "cb_1"]
    assert state["oldest_age_s"] > 29 * HOUR


# ── it must actually run ─────────────────────────────────────────────────────

def test_the_check_runs_on_the_quiet_drain_path_too():
    """CAPABILITY: hooked where it matters.

    "Nothing inbound to drain" is precisely the state in which ARIA is waiting on
    Claude. A hook only on the drained-something path would be silent exactly
    when the loop is stalled. Read by AST so a reformat cannot fool it.
    """
    import ast
    from . import _source_probe

    tree = ast.parse(_source_probe.module_source(cb))
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == "drain_for_aria")
    calls = [n for n in ast.walk(fn)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "announce_stalled_questions"]
    assert len(calls) == 2, (
        f"expected the stalled-question check on BOTH live return paths of "
        f"drain_for_aria, found {len(calls)} — the quiet path is the one that "
        f"matters and is the easiest to drop"
    )


def test_the_gap_type_is_registered():
    """An unregistered type lands under a name nothing filters on — §21b dark."""
    from aria_service.intel.capability_gaps import VALID_GAP_TYPES
    assert "collab_question_stalled" in VALID_GAP_TYPES


def test_a_reporting_failure_never_breaks_the_drain(log, monkeypatch):
    """Teaching visibility is a report, not a precondition for ARIA receiving notes."""
    async def _boom(**kw):
        raise RuntimeError("reporting exploded")

    monkeypatch.setattr(cb, "announce_stalled_questions", _boom)
    log["messages"] = []

    async def _cursor(reader):
        return 0

    monkeypatch.setattr(cb, "get_cursor", _cursor)
    # drain must still return normally with the reporter blowing up
    out = asyncio.run(cb.drain_for_aria())
    assert isinstance(out, dict) and "drained" in out
