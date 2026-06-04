"""R-F990 — file-based back-door between Claude Code and ARIA (the aria CLI).

Claude and ARIA run as separate processes in the same repo, never in one loop,
so the channel is an asynchronous shared mailbox (files only, per CLAUDE.md §6):
a single append-only JSONL of messages plus a per-reader "seen" set so each side
consumes a message once.

Flow:
  * ARIA calls the `ask_claude` tool -> a question lands in the mailbox.
  * Claude (active in the folder) runs `python scripts/agent_bridge.py inbox`,
    sees the question, and replies with `... reply <id> "answer"`.
  * ARIA reads the answer via `check_claude` (or `ask_claude(wait_seconds=N)`
    blocks up to N seconds polling for the reply for a near-live exchange).
  * Either side can also `send` an unprompted note.

The mailbox lives in `<repo>/.agent_bridge/` (gitignored — runtime state).
"""
from __future__ import annotations

import json
import os
import random
import time
from pathlib import Path

BRIDGE_DIRNAME = ".agent_bridge"
MESSAGES_FILE = "messages.jsonl"
PARTIES = ("aria", "claude")

# R-F1313 — comms charter (operator-owned, auditable, engineering-scoped).
# This channel is a human-in-the-loop review log, NOT an unsupervised
# agent-to-agent autonomy loop: the operator owns it and every message is a
# plain-text line they can read. Keep traffic to concrete engineering —
# R-numbers, diffs, test results, deploy/build_rev verification, blockers.
# Claude's role on it is reviewer/assessor surfacing findings to the operator.
CHARTER = (
    "operator-owned, auditable, engineering-scoped review log; "
    "human-in-the-loop; no unsupervised autonomy"
)


def _dir(base: Path | str) -> Path:
    d = Path(base) / BRIDGE_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _messages_path(base: Path | str) -> Path:
    return _dir(base) / MESSAGES_FILE


def _seen_path(base: Path | str, reader: str) -> Path:
    return _dir(base) / f"{reader}_seen.json"


def _all(base: Path | str) -> list[dict]:
    mf = _messages_path(base)
    if not mf.exists():
        return []
    out: list[dict] = []
    for line in mf.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:  # noqa: BLE001 — skip a corrupt line, keep the rest
            continue
    return out


def _gen_id(seq: int) -> str:
    """Collision-resistant message id.

    R-F1313: the old form was ``m{ms:x}{seq:03d}`` where ``seq`` came from
    ``len(_all())`` — two processes appending in the same millisecond both read
    the same length, produced the SAME id, and then a single read_new() could
    mark both seen (dropping one message) or skip one entirely. Mix in the pid
    and a random nibble so Claude's and ARIA's concurrent appends never collide,
    even at the same millisecond with the same observed sequence.
    """
    # 32 bits of entropy (pid folded in for cross-process spread) keeps ids
    # effectively unique even under bursty same-millisecond appends from both
    # parties — birthday collisions stay negligible at any realistic volume.
    salt = (random.getrandbits(32) ^ (os.getpid() << 8)) & 0xFFFFFFFF
    return f"m{int(time.time() * 1000):x}{seq:03d}{salt:08x}"


def send(base: Path | str, frm: str, to: str, text: str,
         kind: str = "note", reply_to: str | None = None) -> dict:
    """Append a message. ``kind`` is question | answer | note."""
    if frm not in PARTIES or to not in PARTIES:
        raise ValueError(f"frm/to must be one of {PARTIES}")
    mf = _messages_path(base)
    seq = len(_all(base))
    msg = {
        "id": _gen_id(seq),
        "ts": time.time(),
        "iso": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frm": frm,
        "to": to,
        "kind": kind,
        "text": text,
        "reply_to": reply_to,
    }
    with mf.open("a", encoding="utf-8") as f:
        f.write(json.dumps(msg, ensure_ascii=False) + "\n")
    return msg


def _load_seen(base: Path | str, reader: str) -> set[str]:
    sf = _seen_path(base, reader)
    if sf.exists():
        try:
            return set(json.loads(sf.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            return set()
    return set()


def _save_seen(base: Path | str, reader: str, seen: set[str]) -> None:
    """Persist the seen-set crash-safely (R-F1313).

    The old direct ``write_text`` was not atomic: if the process was killed
    mid-write (Kaspersky terminating the child, an API error aborting the run),
    the seen file was left truncated/invalid. ``_load_seen`` then swallowed the
    JSON error and returned an empty set, so EVERY message was re-read as new on
    the next poll — a message-replay storm. Write to a temp file in the same
    directory and ``os.replace`` it in (atomic on the same filesystem on both
    Windows and POSIX), so a crash leaves either the old file or the new one,
    never a half-written one."""
    sf = _seen_path(base, reader)
    tmp = sf.with_name(f"{sf.name}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    os.replace(tmp, sf)


def _addressed_unseen(base: Path | str, reader: str, seen: set[str]) -> list[dict]:
    """Messages addressed to ``reader`` that carry an id and aren't yet seen.
    R-F1313: REQUIRE a truthy ``id``. Legacy/corrupt lines without an id can't be
    tracked or replied to, and consuming them used to crash read_new with a
    KeyError on ``m['id']`` (the filter used .get but the update used [...]).
    Skip them so one malformed line can't break the whole channel for either
    party."""
    return [m for m in _all(base)
            if m.get("to") == reader and m.get("id") and m.get("id") not in seen]


def peek(base: Path | str, reader: str) -> list[dict]:
    """Messages addressed to ``reader`` not yet consumed — without marking seen."""
    return _addressed_unseen(base, reader, _load_seen(base, reader))


def read_new(base: Path | str, reader: str) -> list[dict]:
    """Like peek, but marks the returned messages consumed for ``reader``."""
    seen = _load_seen(base, reader)
    new = _addressed_unseen(base, reader, seen)
    if new:
        seen.update(m["id"] for m in new)  # safe: _addressed_unseen guarantees id
        _save_seen(base, reader, seen)
    return new


def wait_for_reply(base: Path | str, reader: str, reply_to_id: str,
                   timeout: float = 0.0, interval: float = 2.0) -> dict | None:
    """Poll for a message addressed to ``reader`` whose reply_to == reply_to_id.
    Returns it (and marks it seen) or None on timeout."""
    deadline = time.time() + max(0.0, timeout)
    while True:
        for m in _all(base):
            if m.get("to") == reader and m.get("reply_to") == reply_to_id and m.get("id"):
                seen = _load_seen(base, reader)
                if m["id"] not in seen:
                    seen.add(m["id"])
                    _save_seen(base, reader, seen)
                return m
        remaining = deadline - time.time()
        if remaining <= 0:
            return None
        time.sleep(min(interval, max(0.1, remaining)))
