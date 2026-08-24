"""R-F1409 — server-mediated Claude<->ARIA collaboration bridge.

ONE ARIA, many I/O surfaces (Coder CLI / intel / web / wa). The collaboration
mailbox lives HERE, on the server (aria-intel), so:
  - the always-on ARIA brain that backs intel/web/wa,
  - the ARIA Coder CLI, and
  - Claude (the senior reviewer)
all read and write the SAME channel. This replaces the old local-file mailbox
(`.agent_bridge/`) that ONLY the local CLI could see — which is what created the
"two ARIAs / notes never arrive" split-brain the operator hit.

Design notes
------------
- CURSOR-BASED, not consume-on-read. Every reader tracks its own last-seen seq,
  so a message is NEVER destroyed by being read. Claude and ARIA poll
  independently; nothing is lost and nothing is double-consumed by whoever
  reads first (the failure mode of the local `_seen` file approach).
- Monotonic integer `seq` from an atomic redis INCR guarantees a total order
  and stable cursors across processes.
- Bounded: the log is trimmed to the last MAX_LOG entries on write (collab
  traffic is low-volume; this is a chat channel, not telemetry).
- §21a wired: send() records a metric signal; the autonomous drain
  (drain_for_aria) absorbs inbound notes to the brain AND records a gap for
  actionable items so the coder loop picks them up — success and failure both
  reach a sink.
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import json
import logging
import os
import time
from typing import Any, Optional

from . import redis_store as rs

logger = logging.getLogger("aria.collab_bridge")

_LOG_KEY = "crucix:aria:collab:log"
_SEQ_KEY = "crucix:aria:collab:seq"
_CURSOR_KEY = "crucix:aria:collab:cursor:{reader}"
MAX_LOG = 500  # keep the last N collaboration messages

VALID_PARTIES = ("claude", "aria")


async def send(
    frm: str,
    to: str,
    text: str,
    *,
    kind: str = "note",
    reply_to: str = "",
) -> dict:
    """Append one message to the shared collaboration log. Returns the stored
    message dict (incl. its monotonic `seq` and `id`)."""
    frm = (frm or "").strip().lower()
    to = (to or "").strip().lower()
    if frm not in VALID_PARTIES or to not in VALID_PARTIES:
        raise ValueError(f"frm/to must be one of {VALID_PARTIES}; got frm={frm!r} to={to!r}")
    if not (text or "").strip():
        raise ValueError("text is required")

    # Atomic monotonic seq → total order + stable cursors across processes.
    seq = await rs.incr(_SEQ_KEY)
    msg = {
        "seq": int(seq),
        "id": f"cb_{int(seq)}",
        "ts": time.time(),
        "frm": frm,
        "to": to,
        "text": text[:20000],
        "kind": (kind or "note")[:32],
        "reply_to": reply_to or "",
    }
    try:
        await rs.lpush(_LOG_KEY, json.dumps(msg))
        # Trim to the most-recent MAX_LOG (lpush prepends → keep head 0..MAX_LOG-1)
        try:
            await rs.ltrim(_LOG_KEY, 0, MAX_LOG - 1)
        except Exception:
            pass
    except Exception as e:
        logger.warning("[R-F1409] collab send persist failed: %s", e)
        raise

    # §21a — low-frequency metric signal (not brain_hook.absorb: that has
    # mastery/neural side-effects unsuited to a transport channel).
    # _record_signal(module, success: bool) is async (brain_hook.py:1328).
    try:
        from . import brain_hook
        await brain_hook._record_signal("collab_bridge", True)
    except Exception:
        pass
    logger.info("[R-F1409] collab %s->%s %s (seq=%d)", frm, to, msg["kind"], seq)
    return msg


async def _read_log() -> list[dict]:
    """Return the full log oldest-first."""
    try:
        raw = await rs.lrange(_LOG_KEY, 0, MAX_LOG - 1)
    except Exception as e:
        logger.warning("[R-F1409] collab read failed: %s", e)
        return []
    out: list[dict] = []
    for item in raw or []:
        try:
            out.append(json.loads(item))
        except Exception:
            continue
    # lpush prepends newest → reverse to oldest-first for cursor semantics
    out.sort(key=lambda m: m.get("seq", 0))
    return out


async def poll(reader: str, after_seq: int = 0) -> list[dict]:
    """Return messages addressed TO `reader` with seq > after_seq, oldest-first.
    Cursor-based: the caller passes the highest seq it has already processed.
    Does NOT mutate any cursor — pure read (use get_cursor/set_cursor to persist
    a server-side reader position, e.g. the autonomous loop)."""
    reader = (reader or "").strip().lower()
    msgs = await _read_log()
    return [m for m in msgs if m.get("to") == reader and int(m.get("seq", 0)) > int(after_seq)]


async def get_cursor(reader: str) -> int | None:
    """How far `reader` has drained. TRI-STATE — None means UNREADABLE.

    R-F4301 (C-254). This returned `int` and collapsed two very different facts
    into `0`:

        v = await rs.get(_CURSOR_KEY.format(reader=reader))
        return int(v) if v else 0          # a FAILED read lands here too

    `rs.get` carries the R-F1 None-on-error contract — None both when the key is
    genuinely absent AND when the store could not be read (dead connection,
    reconnect window). Both became `0`, which rewinds the drain to the beginning
    and re-ingests every message ever sent.

    MEASURED on the live volume 2026-08-24: /data/claude_distill held 56,529
    records representing 41 UNIQUE TEXTS — 48 KB of content amplified ~450x to
    30 MB, one note captured 1,250 times. That corpus exists to be distilled into
    ARIA-LLM, so this is not a wrong verdict a human reads and discounts; it is a
    corrupted training input a fine-tune reads and believes.

    Same absence-reads-as-a-value class as CLAUDE.md §1's three Phase A gates,
    §17's cost meter, C-39 and C-41. `get_strict` exists precisely to separate
    the two and its docstring says so: "None means the key is genuinely absent."

    A CORRUPT value is UNREADABLE, not fresh — garbage in the key is not evidence
    the reader has never run.
    """
    try:
        v = await rs.get_strict(_CURSOR_KEY.format(reader=reader))
    except Exception as e:      # StoreReadError, transport, anything
        logger.warning("[R-F4301] cursor for %s UNREADABLE (%s) — refusing to "
                       "assume 0; this cycle will be skipped", reader, e)
        return None
    if v is None:
        return 0                # genuinely absent -> a fresh reader starts at 0
    try:
        return int(v)
    except (TypeError, ValueError):
        logger.warning("[R-F4301] cursor for %s is CORRUPT (%r) — treated as "
                       "unreadable, never as fresh", reader, v)
        return None


async def set_cursor(reader: str, seq: int) -> bool:
    """Persist the drain cursor. Returns False if it did NOT stick.

    R-F4301 — this swallowed every failure into `logger.debug` and returned None,
    so a failed write was invisible. It is not a cosmetic loss: the cursor not
    advancing GUARANTEES the next cycle re-ingests everything just drained, which
    is the other half of the amplification above. The caller wires the failure.
    """
    try:
        await rs.set(_CURSOR_KEY.format(reader=reader), str(int(seq)))
        return True
    except Exception as e:
        logger.warning("[R-F4301] set_cursor(%s, %d) FAILED (%s) — the next drain "
                       "will re-ingest what this one just did", reader, seq, e)
        return False


async def drain_for_aria() -> dict:
    """Server-side consumer: pull new Claude→ARIA notes (since ARIA's cursor),
    make the ONE ARIA aware of each (brain absorb) and record an actionable gap
    so the coder loop picks up tasks, then advance the cursor.

    R-F1575: also drains the file-based .agent_bridge/ mailbox so messages
    Claude sends via scripts/agent_bridge.py (the CLI tool) are not lost.
    The file bridge is the legacy channel; the Redis bridge is canonical.
    This dual-drain ensures no message is missed regardless of which bridge
    Claude used.

    Returns a summary {drained, last_seq}. Safe to call every loop cycle;
    cursor-guarded so each note is acted on once.
    """
    cursor = await get_cursor("aria")
    if cursor is None:
        # R-F4301 (C-254) — UNREADABLE, not zero. Treating it as 0 asks poll()
        # for everything after seq 0 and re-ingests the whole corpus. Skipping
        # costs one 2-minute interval; rewinding costs the training corpus.
        # "I could not read how far I got" must never mean "I have not started".
        try:
            wire_failure(
                module="collab_bridge",
                detail=("drain SKIPPED — the ARIA cursor was unreadable; refusing "
                        "to rewind to 0 and re-ingest the teacher corpus"),
                gap_type="engine_failure",
                source="collab_bridge:R-F4301",
            )
        except Exception:
            pass
        return {"drained": 0, "last_seq": None, "skipped": "cursor_unreadable"}
    new = await poll("aria", after_seq=cursor)
    if not new:
        new = []

    # R-F1575: also drain the file-based .agent_bridge/ mailbox.
    # Only runs in production (FLY_APP_NAME == "aria-intel") to avoid
    # picking up stale test artifacts when FLY_APP_NAME is set locally
    # for testing. Uses a separate cursor key so old file-bridge messages
    # are not re-drained every cycle.
    if os.environ.get("FLY_APP_NAME") == "aria-intel":
        try:
            from pathlib import Path
            _fb_cursor_key = _CURSOR_KEY.format(reader="aria_file_bridge")
            _fb_cursor = int(await rs.get(_fb_cursor_key) or "0")
            _bridge_dir = Path(__file__).resolve().parent.parent.parent / ".agent_bridge"
            _msgs_file = _bridge_dir / "messages.jsonl"
            if _msgs_file.exists():
                _raw = _msgs_file.read_text(encoding="utf-8", errors="replace")
                _lines = _raw.splitlines()
                _fb_last = _fb_cursor
                for _i, _line in enumerate(_lines):
                    if _i < _fb_cursor:
                        continue
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _fm = json.loads(_line)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if _fm.get("to") == "aria" and _fm.get("frm") == "claude":
                        _seq = cursor + _i + 1  # synthetic seq after Redis messages
                        new.append({
                            "seq": _seq,
                            "id": _fm.get("id", f"fb_{_i}"),
                            "text": _fm.get("text", ""),
                            "frm": "claude",
                            "to": "aria",
                        })
                    _fb_last = _i + 1
                if _fb_last > _fb_cursor:
                    await rs.set(_fb_cursor_key, str(_fb_last))
        except Exception as _fb_e:
            logger.debug("[R-F1575] file-bridge drain failed: %s", _fb_e)

    if not new:
        return {"drained": 0, "last_seq": cursor}

    drained = 0
    # R-F4304 — index the WHOLE log by id so a reply can be paired with the
    # message it answers. _read_log() already returns both directions; this is
    # the lookup that was missing, not new IO.
    _by_id: dict = {}
    try:
        for _m in await _read_log():
            _mid = str(_m.get("id") or "")
            if _mid:
                _by_id[_mid] = _m
    except Exception:
        _by_id = {}

    last_seq = cursor
    for m in new:
        text = (m.get("text") or "").strip()
        seq = int(m.get("seq", 0))
        if text:
            # Make the one ARIA aware (brain absorb). We do NOT blanket-record
            # a capability_gap here: a collaboration note is not a defect, and
            # forcing it through record_gap (no fitting gap_type) would pollute
            # the gap store + mislead the coder. A dedicated collab-log→coder
            # extractor (separate R-number) can selectively promote genuinely
            # actionable notes later.
            try:
                from . import brain_hook
                await brain_hook.absorb(
                    module="collab_bridge",
                    summary=f"Claude→ARIA: {text[:160]}",
                    detail=text[:2000],
                    success=True,
                    confidence="CONFIRMED",
                    source_id=f"collab:{m.get('id','')}",
                )
            except Exception as e:
                logger.warning("[R-F1409] absorb of Claude note failed: %s", e)

            # R-F2399 — distil from the stronger agent. Every Claude→ARIA note is
            # teacher signal: capture it into the claude_teacher corpus so a future
            # SFT/DPO cycle (§24) can distil Claude's engineering/reasoning into
            # ARIA-LLM. brain_hook.absorb above wires the RAG/mastery/neural sinks;
            # this wires the DISTILL sink. Best-effort — never blocks the drain,
            # and a failure is §21a-wired inside capture().
            try:
                from . import claude_distill
                # R-F4304 (C-257) — resolve the PARENT's text, not just its id.
                # An SFT row needs the instruction as well as the response, and
                # the whole log (both directions) is already in hand here, so the
                # pair costs one dict lookup. Best-effort: an unresolvable parent
                # leaves prompt empty and the builder drops the record rather
                # than fabricating a question.
                _parent = ""
                try:
                    _rt = str(m.get("reply_to") or "")
                    if _rt:
                        _parent = str(_by_id.get(_rt, {}).get("text") or "")
                except Exception:
                    _parent = ""
                claude_distill.capture(
                    text,
                    direction=f"{m.get('frm','claude')}->{m.get('to','aria')}",
                    kind=str(m.get("kind", "note")),
                    msg_id=str(m.get("id", "")),
                    reply_to=str(m.get("reply_to", "")),
                    prompt=_parent,
                )
            except Exception as e:
                logger.debug("[R-F2399] claude_distill capture failed (non-fatal): %s", e)
        last_seq = max(last_seq, seq)
        drained += 1

    if not await set_cursor("aria", last_seq):
        # R-F4301 — the cursor did not stick, so the next cycle WILL re-ingest
        # these same notes. Say so; a silent debug line is how 450x accrued.
        try:
            wire_failure(
                module="collab_bridge",
                detail=(f"cursor write FAILED at seq {last_seq} — the next drain "
                        f"will re-ingest {drained} note(s)"),
                gap_type="engine_failure",
                source="collab_bridge:R-F4301",
            )
        except Exception:
            pass
    logger.info("[R-F1409] drained %d Claude→ARIA note(s), cursor→%d", drained, last_seq)
    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="collab_bridge",
                     summary="collab_bridge module active",
                     source_id="collab_bridge:init")
    except Exception:
        try:
            wire_failure(module="collab_bridge", detail="module init failed",
                        gap_type="engine_failure", source="collab_bridge:init")
        except Exception:
            pass

    return {"drained": drained, "last_seq": last_seq}
