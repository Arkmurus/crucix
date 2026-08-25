"""R-F4338 / C-283 — the SFT render silently DISCARDED every system turn.

PROVEN against the real tokenizer (mistralai/Mistral-7B-Instruct-v0.3):

    render WITH the system turn    : 746 chars
    render WITHOUT the system turn : 746 chars
    identical                      : True

Mistral v0.3's chat template has no slot for a standalone `system` role, so
`apply_chat_template` drops it. It does not raise and it does not warn — the
rendered training string is byte-identical either way, which is why this
survived 45 corpora.

BLAST RADIUS, measured across data/training/*.jsonl:
    45 corpora carry system turns
    5,324 training rows had their system instruction silently discarded

So every "You are ARIA...", every "never invent an agency", every citation
contract written into a system turn was thrown away at render time and never
learned. The corpora look correct on disk and train on something else — the
same absence-shaped defect CLAUDE.md §1 records for the Phase A gates, except
here it silently removed the instruction half of 5,324 examples.

THE FIX IS TO FOLD, NOT TO DROP. Mistral renders the first USER turn, so the
system content is prepended there — which is exactly how Mistral itself expects
a system instruction to be carried, and how `aria_grounded_v1.jsonl` (664 rows,
already trained successfully) effectively does it by putting the instruction
inline in the user turn.

WHY NOT "just stop using system turns in corpora": 5,324 rows already exist and
a future corpus author will reach for a system turn again — it is the obvious
shape and every other provider honours it. Fixing the RENDER fixes all of them
at once and cannot be forgotten by the next author.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
_TRAIN = ROOT / "scripts" / "train"
if str(_TRAIN) not in sys.path:
    sys.path.insert(0, str(_TRAIN))

import sft_train as S  # noqa: E402

SYS = "You are ARIA. Never invent an agency, a statute or a treaty status."
USER = "En français : quelle autorité contrôle les exportations ?"
ANSWER = "La CIEEMG."

WITH_SYSTEM = {"messages": [
    {"role": "system", "content": SYS},
    {"role": "user", "content": USER},
    {"role": "assistant", "content": ANSWER},
]}
NO_SYSTEM = {"messages": [
    {"role": "user", "content": USER},
    {"role": "assistant", "content": ANSWER},
]}


class _MistralLikeTokenizer:
    """Stands in for Mistral v0.3: renders user/assistant and DROPS system.

    Modelled on the measured behaviour, so this test needs no model download
    and still reproduces the exact defect.
    """

    def apply_chat_template(self, messages, tokenize=False):
        out = "<s>"
        for m in messages:
            if m["role"] == "user":
                out += f"[INST] {m['content']}[/INST]"
            elif m["role"] == "assistant":
                out += f" {m['content']}</s>"
            # system: silently dropped — this is the defect
        return out


# -- THE CAPABILITY TEST ------------------------------------------------

def test_the_system_instruction_survives_the_render():
    """THE DEFECT. 5,324 rows trained without their instruction."""
    tok = _MistralLikeTokenizer()
    text = S._render_text(tok, WITH_SYSTEM)
    assert SYS in text, (
        "the system turn was discarded by the render — the instruction half of "
        "every such training row is thrown away and never learned"
    )


def test_the_render_differs_from_one_with_no_system_turn():
    """The sharpest form of the bug: identical output meant the turn vanished."""
    tok = _MistralLikeTokenizer()
    assert S._render_text(tok, WITH_SYSTEM) != S._render_text(tok, NO_SYSTEM)


def test_the_user_question_is_still_present():
    """Folding must not swallow the question it is prepended to."""
    tok = _MistralLikeTokenizer()
    text = S._render_text(tok, WITH_SYSTEM)
    assert USER in text and ANSWER in text


def test_the_assistant_answer_is_not_polluted():
    """The instruction belongs on the PROMPT side. Folding it into the answer
    would train her to recite her own system prompt — which is exactly the
    'You are you are you are...' failure the CLI already hit."""
    tok = _MistralLikeTokenizer()
    text = S._render_text(tok, WITH_SYSTEM)
    tail = text.split(USER, 1)[1]
    assert SYS not in tail, "system text leaked past the user turn into the answer"


# -- it must not disturb the rows that were already fine ----------------

def test_a_row_with_no_system_turn_is_unchanged():
    """664 grounded rows are (user, assistant) and already train correctly."""
    tok = _MistralLikeTokenizer()
    assert S._render_text(tok, NO_SYSTEM) == tok.apply_chat_template(
        NO_SYSTEM["messages"], tokenize=False)


def test_multiple_system_turns_are_all_carried():
    """Mistral allows one leading system message; a corpus with two must not
    lose the second silently."""
    tok = _MistralLikeTokenizer()
    rec = {"messages": [
        {"role": "system", "content": "FIRST RULE."},
        {"role": "system", "content": "SECOND RULE."},
        {"role": "user", "content": USER},
        {"role": "assistant", "content": ANSWER},
    ]}
    text = S._render_text(tok, rec)
    assert "FIRST RULE." in text and "SECOND RULE." in text


def test_a_system_only_record_does_not_crash():
    """Malformed corpora must not kill a paid pod cycle after the base-model
    load — the exact failure R-F1470 records."""
    tok = _MistralLikeTokenizer()
    S._render_text(tok, {"messages": [{"role": "system", "content": SYS}]})


def test_an_empty_message_list_does_not_crash():
    tok = _MistralLikeTokenizer()
    S._render_text(tok, {"messages": []})


# -- a template that DOES support system must not be double-fed ---------

def test_a_system_aware_template_is_left_alone():
    """If the tokenizer already renders system turns, folding would duplicate
    the instruction. Only fold when the template actually drops it."""

    class _SystemAware:
        def apply_chat_template(self, messages, tokenize=False):
            return "".join(f"<|{m['role']}|>{m['content']}" for m in messages)

    text = S._render_text(_SystemAware(), WITH_SYSTEM)
    assert text.count(SYS) == 1, (
        f"instruction appears {text.count(SYS)} times — duplicated"
    )
    # The ROLE must survive, not just the text. Folding unconditionally moves
    # the instruction into the user turn and destroys the system role even on a
    # template that handles it correctly. Mutation testing caught that the
    # duplication check alone could not see this.
    assert "<|system|>" in text, (
        "a system-aware template had its system turn folded away — fold ONLY "
        "when the template actually drops it"
    )
