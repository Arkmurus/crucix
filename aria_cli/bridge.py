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
import pathlib
import secrets
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
    salt = (secrets.randbits(32) ^ (os.getpid() << 8)) & 0xFFFFFFFF
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
    # R-F2400 — forward Claude's teaching to the SERVER brain. The local file
    # mailbox is consumed only by the local aria CLI (ephemeral, no learning
    # sink) and is NEVER shipped to aria-intel, so without this Claude's
    # engineering/reasoning guidance never reaches ARIA's brain (R-F2399 audit:
    # the server Redis collab log had zero writers). Best-effort + env-gated:
    # a no-op when ARIA_SERVICE_URL/ARIA_BRAIN_URL + a token aren't set, and a
    # network failure never breaks the local mailbox write above.
    # R-F4307 (C-260) — forward ONCE and carry the result back so the caller can
    # REPORT it without forwarding again. `_forward` is attached AFTER the file
    # write above, so it never lands in the mailbox: it is in-memory only.
    _ok, _reason = forward_to_server(msg)
    msg["_forward"] = {"ok": _ok, "reason": _reason}
    return msg


def _env_file_values() -> dict:
    """Values from the repo `.env`, if present. Never raises.

    R-F4307 (C-260). The forward was a no-op not because it was missing but
    because it read ONLY `os.getenv`, and the operator keeps credentials in
    `.env` (gitignored). That single omission is why Claude's teaching never
    reached the server: the teacher corpus held 24 substantial notes across its
    whole lifetime.
    """
    out: dict = {}
    try:
        here = pathlib.Path(__file__).resolve()
        for parent in (here.parent, *here.parents):
            f = parent / ".env"
            if f.exists():
                for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    out.setdefault(k.strip(), v.strip().strip('"').strip("'"))
                break
    except Exception:
        return {}
    return out


def _cfg(name: str) -> str:
    """os.environ wins over `.env` — an explicit export overrides a stale file."""
    v = (os.getenv(name) or "").strip()
    if v:
        return v
    return (_env_file_values().get(name) or "").strip()


def _post_ingest(url: str, payload: dict, headers: dict):
    """One POST. Split out so tests drive it without patching httpx globally."""
    import httpx
    r = httpx.post(url, json=payload, headers=headers, timeout=6.0)
    # getattr: R-F2399 patches httpx.post with a fake that has only
    # status_code. A reader that insists on .text would break a pinned contract
    # for no benefit - the body is used only to surface the server-assigned id.
    return r.status_code, (getattr(r, "text", "") or "")[:400]


def forward_to_server(msg: dict) -> tuple[bool, str]:
    """THE ONE forwarder. Best-effort POST of a Claude->ARIA note to the server.

    Returns (ok, reason) and NEVER raises. The local mailbox write is the source
    of truth and happens before this; a failure here can never lose a message.

    R-F4307 (C-260) consolidated two implementations into this one. R-F4302 added
    a second forwarder in scripts/agent_bridge.py after grepping only THAT file
    and concluding no forwarder existed. Both would have fired the moment
    ARIA_SERVICE_URL was exported rather than kept in `.env`, POSTing every note
    twice and doubling the corpus — C-254's amplification in miniature,
    reintroduced by the fix for C-255. One measure, not two (§1, R-F2639).

    The REASON is the useful half of R-F4302: a silent best-effort forward is
    exactly how a corpus reaches 24 notes with nobody noticing, so every
    non-success path says why.

    TEACHER SIGNAL ONLY. ARIA's own questions stay local; forwarding them would
    feed her output back as her own teacher.
    """
    try:
        if (msg or {}).get("frm") != "claude":
            return False, "not teacher signal (only claude->aria is forwarded)"
        url = _cfg("ARIA_SERVICE_URL") or _cfg("ARIA_BRAIN_URL")
        if not url:
            return False, "ARIA_SERVICE_URL not set - note is LOCAL ONLY"
        token = _cfg("ARIA_INTERNAL_TOKEN") or _cfg("ARIA_API_TOKEN")
        if not token:
            return False, "ARIA_INTERNAL_TOKEN not set - refusing to POST unauthenticated"
        status, body = _post_ingest(
            f"{url.rstrip('/')}/api/aria/collab/ingest",
            {
                "text": msg.get("text", ""),
                "kind": msg.get("kind", "note"),
                "reply_to": msg.get("reply_to") or "",
                "frm": "claude",
                "to": "aria",
            },
            {"Authorization": f"Bearer {token}"},
        )
        if 200 <= int(status) < 300:
            sid = ""
            try:
                sid = (json.loads(body) or {}).get("id") or ""
            except Exception:
                sid = ""
            return True, (f"server id {sid}" if sid else "ok")
        return False, f"HTTP {status}: {str(body)[:120]}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def _forward_to_server(msg: dict) -> bool:
    """Legacy bool contract (R-F2399 pins `is False` in three assertions).

    A thin delegation, not a second implementation — breaking a passing test to
    suit a refactor is not a fix.
    """
    return forward_to_server(msg)[0]

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
