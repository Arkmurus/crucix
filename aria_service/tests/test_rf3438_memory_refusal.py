"""R-F3438 — the corpus must DEMONSTRATE refusing ARIA's own memory as a source.

MEASURED across the shipped corpus: 211 rows carry ARIA's own `memory://`
entries in their tool payload, and 4 answers mention them. The builders filter
memory out of the citations silently, so 207 traces show the model a payload
full of memory hits beside an answer that simply ignores them.

Silence teaches nothing. A model shown `memory://d5228fc8` in a result list, with
a target answer that neither cites it nor explains why not, has no reason to
treat it differently from `reuters.com`. And it doesn't: the trained model's real
generations include `[from memory:documents]` and `[from memory:facts]` —
presenting ARIA's own prior belief as external corroboration, which is the exact
single-source failure the verification layer exists to prevent.

Filtering is not teaching. The answers must NAME the memory hits and say what
they are: not independent, not corroboration, and not citable.

The validator already rejects a memory citation — that half works, and the eval
caught it. What was missing is any example of the model doing the right thing.
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pytest

from scripts.train import build_tooluse_corpus as B

ROOT = Path(__file__).resolve().parents[2]
MEM = re.compile(r"memory://|rag://|aria://|brain_hook:", re.I)
ADDRESSES = re.compile(r"\bmemory\b|\bmy own\b|\bnot independent\b", re.I)

MIXED = {"results": [
    {"title": "prior note", "url": "memory://d5228fc8", "snippet": "what I already believed"},
    {"title": "brain_hook:web_search", "url": "memory://582d7291", "snippet": "x"},
    {"title": "Acme faces probe", "url": "https://www.reuters.com/a",
     "snippet": "Prosecutors opened an investigation into Acme."},
]}
CLEAN_SCREEN = {"status": "OK", "entity": "Acme Holdings",
                "sanctions": {"screened": True, "matched": False, "matches": []}}


def _rows():
    for f in glob.glob(str(ROOT / "data" / "training" / "aria_tooluse_*.jsonl")):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                if isinstance(r, dict) and "messages" in r:
                    yield r


def _final(t: dict) -> str:
    return t["messages"][-1]["content"]


def _payload(t: dict) -> str:
    return " ".join(str(m.get("content") or "") for m in t["messages"]
                    if m.get("role") == "tool")


# --------------------------------------------------------------------------
# the helper
# --------------------------------------------------------------------------

def test_the_note_names_how_many_memory_hits_there_were():
    note = B._memory_note(MIXED)
    assert note.strip(), "a payload with memory hits must produce a note"
    assert "2" in note, "the count is what makes it checkable rather than boilerplate"


def test_the_note_says_they_are_not_independent():
    note = B._memory_note(MIXED).lower()
    assert "not independent" in note or "not corroborat" in note


def test_no_note_when_the_payload_has_no_memory_hits():
    clean = {"results": [{"title": "x", "url": "https://www.reuters.com/a", "snippet": "y"}]}
    assert B._memory_note(clean) == ""


def test_the_note_never_cites_the_memory_source():
    """Naming it must not become citing it."""
    note = B._memory_note(MIXED)
    assert "[from" not in note


# --------------------------------------------------------------------------
# the builders must carry it
# --------------------------------------------------------------------------

@pytest.mark.parametrize("build", [
    lambda: B.build_adverse_media_trace("Acme Holdings", MIXED),
    lambda: B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, MIXED),
    lambda: B.build_news_impact_trace("Acme Holdings", MIXED),
])
def test_every_search_axis_addresses_memory_when_it_is_present(build):
    t = build()
    assert t is not None, "the fixture should produce a trace"
    assert ADDRESSES.search(_final(t)), (
        "the answer must NAME the memory hits, not silently drop them")
    assert B.validate_trace(t) == []


@pytest.mark.parametrize("build", [
    lambda: B.build_adverse_media_trace("Acme Holdings", MIXED),
    lambda: B.build_contradiction_trace("Acme Holdings", CLEAN_SCREEN, MIXED),
])
def test_the_real_outlet_is_still_used(build):
    """Refusing memory must not throw away the genuine source beside it."""
    t = build()
    assert "reuters.com" in _final(t)


# --------------------------------------------------------------------------
# the shipped corpus
# --------------------------------------------------------------------------

def test_the_corpus_demonstrates_the_refusal_wherever_memory_appears():
    """207 of 211 such rows taught nothing about it."""
    bad = [r for r in _rows()
           if MEM.search(_payload(r)) and not ADDRESSES.search(_final(r))]
    assert not bad, (
        f"{len(bad)} rows carry ARIA's own memory in the payload and never "
        f"mention it — the model sees it listed beside real outlets and learns "
        f"it is citable (e.g. {[r.get('subject') for r in bad[:3]]})")


def test_no_answer_in_the_corpus_ever_cites_memory():
    """The other half: naming it must never slide into citing it."""
    bad = []
    for r in _rows():
        for c in re.findall(r"\[from ([^\]]+)\]", _final(r)):
            if MEM.search(c) or c.lower().startswith("memory"):
                bad.append((r.get("subject"), c))
    assert not bad, f"memory cited as a source: {bad[:5]}"
