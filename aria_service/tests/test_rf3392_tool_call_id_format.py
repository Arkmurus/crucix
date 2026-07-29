"""R-F3392 — tool_call ids broke the chat template, so the corpus could not be trained at all.

FOUND BY THE §24 PRE-FLIGHT, BEFORE SPENDING GPU HOURS. Rendering a real trace
through the base model's tokenizer:

    tok.apply_chat_template(row["messages"], tokenize=False)
    -> TemplateError: Tool call IDs should be alphanumeric strings with length 9!

Mistral's chat template requires tool_call ids to be EXACTLY 9 alphanumeric
characters. Every id this builder emitted looked like `call_1_companieshouses` —
underscores, wrong length. So every trace carrying a tool call (all 222 of them:
single-hop, multi-hop, challenge, resolution, news, unavailable) would raise the
moment `sft_train.py:94` rendered it.

WHY THAT MATTERED SO MUCH. The failure happens AFTER the base model is loaded —
sft_train.py's own comments record precisely this class (R-F1470: a format
mismatch that only surfaced "AFTER the paid base-model load, wasting the" cycle).
A training run would have consumed A100 hours and then died on the first row.

The corpus was validated, real, and completely untrainable. Nothing in
`validate_trace` covered it, because the trace was internally consistent — the
constraint lives in the CONSUMER, and nobody had asked the consumer yet.

THE FIX. Ids are now derived deterministically as a 9-character alphanumeric
hash of (trace subject, hop index, tool name). Deterministic keeps the corpus
reproducible; the hash keeps ids unique within a trace; 9 alphanumerics satisfy
the template. And `validate_trace` now enforces the format, so a corpus that
cannot be trained can no longer be written in the first place.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scripts.train import build_tooluse_corpus as B

_ID_RE = re.compile(r"^[a-zA-Z0-9]{9}$")

CLEAN = {"result": "CLEAR", "status": "CLEAR",
         "sanctions": {"matched": False, "matches": [], "verdict": "CLEAR"}}
SEARCH = {"results": [{"title": "TESCO PLC", "company_status": "active",
                       "company_number": "00445790"}]}


def _ids(trace) -> list[str]:
    return [c["id"] for m in trace["messages"] for c in (m.get("tool_calls") or [])]


# ── every builder emits conforming ids ────────────────────────────────────

def test_single_hop_id_is_nine_alphanumerics():
    for i in _ids(B.build_trace("Tesco plc", CLEAN)):
        assert _ID_RE.match(i), i


def test_challenge_id_is_conforming():
    for i in _ids(B.build_challenge_trace("Tesco plc", CLEAN, premise="clean")):
        assert _ID_RE.match(i), i


def test_resolution_id_is_conforming():
    for i in _ids(B.build_resolution_trace("Tesco", SEARCH)):
        assert _ID_RE.match(i), i


def test_news_id_is_conforming():
    payload = {"results": [{"title": "T", "source": "aria_search",
                            "url": "https://reuters.com/x", "snippet": "s"}]}
    for i in _ids(B.build_news_impact_trace("Tesco plc", payload)):
        assert _ID_RE.match(i), i


def test_multihop_ids_are_all_conforming_and_unique():
    t = B.build_multihop_trace("Tesco plc", [
        ("companies_house_search", {"query": "Tesco plc"}, SEARCH),
        ("companies_house_officers", {"company_number": "00445790"},
         {"company_number": "00445790", "officers": [{"name": "DOE, J", "resigned_on": None}]}),
        ("screen", {"entity_name": "DOE, J"}, CLEAN),
    ])
    ids = _ids(t)
    assert len(ids) == 3
    for i in ids:
        assert _ID_RE.match(i), i
    assert len(set(ids)) == 3, f"ids collide within one trace: {ids}"


def test_ids_are_deterministic():
    """A corpus that changes ids on every rebuild is not reproducible."""
    a = _ids(B.build_multihop_trace("Tesco plc", [
        ("companies_house_search", {"query": "Tesco plc"}, SEARCH)]))
    b = _ids(B.build_multihop_trace("Tesco plc", [
        ("companies_house_search", {"query": "Tesco plc"}, SEARCH)]))
    assert a == b


def test_different_subjects_get_different_ids():
    a = _ids(B.build_trace("Tesco plc", CLEAN))
    b = _ids(B.build_trace("Unilever plc", CLEAN))
    assert a != b


# ── linkage still holds ───────────────────────────────────────────────────

def test_tool_turn_still_references_its_call():
    t = B.build_multihop_trace("Tesco plc", [
        ("companies_house_search", {"query": "Tesco plc"}, SEARCH)])
    call = next(m for m in t["messages"] if m.get("tool_calls"))
    tool = next(m for m in t["messages"] if m["role"] == "tool")
    assert tool["tool_call_id"] == call["tool_calls"][0]["id"]
    assert B.validate_trace(t) == []


# ── the validator refuses an untrainable trace ────────────────────────────

def test_validator_rejects_a_non_conforming_id():
    """The constraint lives in the CONSUMER (the chat template), which is why
    validation missed it. It is enforced here now, so an untrainable corpus
    cannot be written again."""
    t = B.build_trace("Tesco plc", CLEAN)
    bad = "call_not_nine"
    t["messages"][2]["tool_calls"][0]["id"] = bad
    t["messages"][3]["tool_call_id"] = bad
    errs = B.validate_trace(t)
    assert errs, "a trace that cannot be rendered by the chat template was accepted"
    assert any("9" in e or "alphanumeric" in e.lower() for e in errs), errs


# ── the shipped corpora are trainable ────────────────────────────────────

@pytest.mark.parametrize("name", [
    "aria_tooluse_v1.jsonl", "aria_tooluse_multihop_v1.jsonl",
    "aria_tooluse_challenge_v1.jsonl", "aria_tooluse_resolution_v1.jsonl",
    "aria_tooluse_news_v1.jsonl", "aria_tooluse_unavailable_v1.jsonl",
])
def test_shipped_corpus_ids_conform(name):
    p = Path(B.__file__).resolve().parents[2] / "data" / "training" / name
    if not p.exists():
        pytest.skip(f"{name} not present")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert rows
    for r in rows:
        for i in _ids(r):
            assert _ID_RE.match(i), f"{name}: {i}"
