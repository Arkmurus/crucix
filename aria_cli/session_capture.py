"""R-F4308 (C-261) — turn completed coder sessions into training signal.

The coder CLI produced NOTHING for training. `output_harvester` captures chat
outputs and `claude_distill` captures teacher notes, but the surface doing the
most substantive work — reading files, editing them, running tests — evaporated
every turn.

This mirrors output_harvester's proven shape rather than inventing one: score
deterministically with no LLM call, redact BEFORE writing, append one JSONL row
per accepted turn, in the same `{messages:[user,assistant], ...}` shape the
existing corpora use so the training scripts can read it directly.

WHAT DIFFERS IS THE QUALITY SIGNAL, because what makes a good chat answer is not
what makes a good coding turn. Four gates, each present because its absence would
poison the corpus:

  * ABORTED TURNS ARE NEVER CAPTURED — an aborted turn demonstrates failure, and
    training on it teaches the failure.
  * A TURN THAT USED NO TOOLS IS NOT CODING SIGNAL — in a coder CLI a zero-step
    answer is a chat reply. Capturing it trains conversation, not engineering.
  * REFUSALS AND FRAGMENTS ARE DROPPED — C-257 found the claude corpus was 41
    unique texts with a 26-character median; fragments dilute the rows that
    carry anything.
  * REDACTION HAPPENS BEFORE SCORING AND BEFORE WRITING — a coder reads source
    and shell output, so a turn can carry credentials. Redacting after the write
    would mean the secret was already on disk.

OPT-IN. This writes the operator's source and shell output to disk; it must not
begin doing that because a version changed under them. `ARIA_CODER_CAPTURE_ENABLED=1`.

BEST-EFFORT, ALWAYS. Every failure is swallowed and reported, never raised. A
learning sink that can break the tool it observes will be switched off, and then
it captures nothing at all.
"""
from __future__ import annotations

import json
import os
import pathlib
import time

try:
    from .redact import redact_secrets
except Exception:  # pragma: no cover - redaction is not optional; fail closed
    redact_secrets = None  # type: ignore[assignment]

#: Below this a turn is a fragment, not a demonstration. Deliberately generous:
#: a real coding turn that read a file, changed it and reported back is rarely
#: shorter than a couple of hundred characters.
MIN_CHARS = 200

#: Phrases that mark a non-answer. Matching output_harvester's list so the two
#: harvesters agree about what a refusal looks like.
_REFUSALS = ("i can't", "i cannot", "i'm unable", "i am unable", "as an ai",
             "i won't be able", "unable to help")

_SOURCE = "coder_session"


def _enabled() -> bool:
    return (os.getenv("ARIA_CODER_CAPTURE_ENABLED") or "").strip().lower() in (
        "1", "true", "yes", "on")


def _default_dir() -> pathlib.Path:
    env = (os.getenv("ARIA_CODER_CAPTURE_DIR") or "").strip()
    if env:
        return pathlib.Path(env)
    here = pathlib.Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() or (parent / "aria_service").exists():
            return parent / "data" / "coder_sessions"
    return here.parent / "data" / "coder_sessions"


def _clean(text: str) -> str:
    """Redact, then normalise. Redaction FIRST — everything downstream, scoring
    included, must only ever see the redacted form."""
    t = text or ""
    if redact_secrets is not None:
        try:
            t = redact_secrets(t)
        except Exception:
            # A redactor that errored has NOT redacted. Refuse the text rather
            # than write something unexamined.
            return ""
    return " ".join(t.split())


def capture_turn(user_text, result, *, out_dir=None,
                 respect_env: bool = False) -> tuple[bool, str]:
    """Capture one completed coder turn. Returns (captured, reason). Never raises.

    `respect_env` is False by default so tests drive the gates directly; the
    agent passes True so the operator's opt-in governs in real use.
    """
    try:
        if respect_env and not _enabled():
            return False, "disabled (set ARIA_CODER_CAPTURE_ENABLED=1)"

        # --- the turn must have SUCCEEDED and DONE something -----------------
        aborted = getattr(result, "aborted", None)
        final = getattr(result, "final_text", None)
        steps = getattr(result, "steps", None)
        if aborted is None or final is None:
            return False, "malformed turn result"
        if aborted:
            return False, "turn was aborted - a failed turn is not a demonstration"
        try:
            if int(steps or 0) < 1:
                return False, "no tool steps - a zero-step answer is chat, not coding"
        except (TypeError, ValueError):
            return False, "malformed turn result (steps)"

        instruction = _clean(str(user_text or ""))
        response = _clean(str(final or ""))
        if not instruction:
            return False, "no instruction - never invent the missing half"
        if not response:
            return False, "empty response after redaction"
        if len(response) < MIN_CHARS:
            return False, f"too short ({len(response)} < {MIN_CHARS})"
        low = response.lower()
        if any(r in low for r in _REFUSALS):
            return False, "refusal - not a demonstration"

        row = {
            "messages": [
                {"role": "user", "content": instruction},
                {"role": "assistant", "content": response},
            ],
            "topic": "coding",
            "grounded": False,
            "label": _SOURCE,
            "source": _SOURCE,
            "ts": time.time(),
            "steps": int(steps or 0),
        }

        d = pathlib.Path(out_dir) if out_dir is not None else _default_dir()
        d.mkdir(parents=True, exist_ok=True)
        shard = d / (time.strftime("%Y-%m-%d") + ".jsonl")
        # newline="\n" — corpora are hash-pinned downstream and CRLF would make
        # one file two identities across platforms (see the .gitattributes work).
        with shard.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True, str(shard)
    except Exception as e:      # never let capture break the operator's session
        return False, f"{type(e).__name__}: {e}"
