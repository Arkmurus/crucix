"""R-F4272 — the three registry-depth axes, and the gate that admits them.

These axes exist because R-F4271 measured the harness seeing 6 of ARIA's 24
fundamentals, with FINANCIAL_STANDING and OWNERSHIP_CONTROL almost entirely
unmeasured. Every payload is a REAL Companies House response; the tests below
care mostly about the ONE thing that makes such a corpus dangerous if it is
wrong — that a register which did not answer is never rendered as good news.
"""
from __future__ import annotations

import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train import build_registry_depth_corpus as brd  # noqa: E402

CORPUS = ROOT / "data/training/aria_tooluse_registry_depth_v1.jsonl"
CAPTURE = ROOT / "data/training/tooluse_capture_360_2026_08_23.jsonl"


@pytest.fixture(scope="module")
def traces() -> list[dict]:
    return [json.loads(line) for line in
            CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(scope="module")
def captures() -> list[dict]:
    return [json.loads(line) for line in
            CAPTURE.read_text(encoding="utf-8").splitlines() if line.strip()]


def _final(trace: dict) -> str:
    return trace["messages"][-1]["content"]


def _payload(trace: dict) -> dict:
    tools = [m for m in trace["messages"] if m["role"] == "tool"]
    return json.loads(tools[-1]["content"])


# -- the corpus as shipped ---------------------------------------------------

def test_every_shipped_trace_passes_its_own_gate(traces: list[dict]) -> None:
    for trace in traces:
        assert brd.validate_registry_trace(trace) == [], trace.get("subject")


def test_all_three_axes_are_populated(traces: list[dict]) -> None:
    import collections
    counts = collections.Counter(t["label"] for t in traces)
    assert set(counts) == brd.REGISTRY_AXES
    for label, count in counts.items():
        assert count >= 12, f"{label} is too thin to be an axis: {count}"


def test_tool_payloads_are_real_captures_never_composed(traces: list[dict],
                                                        captures: list[dict]) -> None:
    """THE INHERITED CONSTRAINT: no registry fact is invented.

    Every answered payload must be byte-identical to something the capture file
    holds for that company. A corpus whose tool results are LLM-imagined teaches
    that plausible-looking tool output is acceptable.
    """
    real = {}
    for record in captures:
        number = str(record.get("company_number") or "")
        if not number:
            continue
        for key in ("insolvency", "charges", "psc"):
            if record.get(key) is not None:
                real[(number, key)] = json.dumps(record[key], sort_keys=True, default=str)

    checked = 0
    for trace in traces:
        payload = _payload(trace)
        if payload.get("checked") is False:
            continue  # the refusal rows are the documented `_unchecked` shape
        number = trace["company_number"]
        if trace["label"] == brd.INSOLVENCY:
            captured = json.loads(real[(number, "insolvency")])
            assert payload.get("cases") == captured.get("cases")
        elif trace["label"] == brd.CHARGES:
            captured = json.loads(real[(number, "charges")])
            assert payload.get("total_count") == captured.get("total_count")
            assert payload.get("outstanding_count") == captured.get("outstanding_count")
        else:
            captured = json.loads(real[(number, "psc")])
            assert payload.get("items") == captured
        checked += 1
    assert checked >= 60, "too few rows were provenance-checked"


def test_subjects_do_not_collide_with_the_existing_split(traces: list[dict]) -> None:
    """A new axis must not leak subjects into or out of the held-out eval."""
    existing = set()
    for name in ("eval", "train"):
        path = ROOT / f"data/training/split_v1/{name}.jsonl"
        existing |= {json.loads(line)["subject"] for line in
                     path.read_text(encoding="utf-8").splitlines() if line.strip()}
    mine = {t["subject"] for t in traces}
    assert not (mine & existing), sorted(mine & existing)


# -- the property that makes these axes worth having -------------------------

def test_an_unanswered_register_is_never_reported_as_clean(traces: list[dict]) -> None:
    unanswered = [t for t in traces if _payload(t).get("checked") is False]
    assert len(unanswered) >= 3, "no refusal rows — the axis only rewards answering"
    for trace in unanswered:
        final = _final(trace)
        assert brd._NOT_ESTABLISHED in final or "UNKNOWN" in final
        for phrase in ("no insolvency", "no charges", "unencumbered", "no cases"):
            assert not brd._asserts(final, phrase), (trace["label"], phrase)


def test_the_unanswered_ownership_payload_is_not_an_empty_register() -> None:
    """Collapsing 'did not answer' into 'answered, empty' is THE defect.

    An earlier draft of this builder coerced `None` to `[]` and the refusal row
    became indistinguishable from a genuinely empty register.
    """
    trace = brd.build_ownership_trace("REVOLUT LTD", "08804411", None)
    payload = _payload(trace)
    assert payload["checked"] is False
    assert payload["items"] is None
    assert payload["items"] != []
    assert brd.validate_registry_trace(trace) == []
    assert "UNKNOWN" in _final(trace)


def test_the_satisfied_charge_trap_is_graded(traces: list[dict]) -> None:
    """51 charges of which 6 are outstanding: both '51' and 'none' mislead."""
    bhs = [t for t in traces if t["company_number"] == "00229606"
           and t["label"] == brd.CHARGES]
    assert bhs, "British Home Stores charges row is missing"
    final = _final(bhs[0])
    payload = _payload(bhs[0])
    assert payload["total_count"] == 51 and payload["outstanding_count"] == 6
    assert "6 OUTSTANDING" in final
    assert "satisfied" in final.lower()

    greggs = [t for t in traces if t["company_number"] == "00502851"
              and t["label"] == brd.CHARGES]
    assert "none outstanding" in _final(greggs[0]).lower()


def test_an_empty_psc_register_never_reads_as_no_owner(traces: list[dict]) -> None:
    empty = [t for t in traces if t["label"] == brd.OWNERSHIP
             and _payload(t).get("checked") is not False
             and not _payload(t).get("items")]
    assert empty, "no empty-PSC rows — the exemption case is untrained"
    for trace in empty:
        lowered = _final(trace).lower()
        assert any(tok in lowered for tok in
                   ("unknown", "unverified", "not evidence", "exemption"))


def test_the_four_ownership_states_are_all_trained(traces: list[dict]) -> None:
    """OC-5's whole difficulty is that an empty register has THREE meanings.

    Lawfully exempt, unexplained, and did-not-answer are different answers with
    different consequences; a corpus holding only one of them would teach the
    model to give that answer to all three. The exemption branch had zero rows
    until the exemption register was captured — an untrained branch in this
    module's own builder.
    """
    owner = [t for t in traces if t["label"] == brd.OWNERSHIP]
    finals = [_final(t) for t in owner]
    named = [f for f in finals if "significant control over" in f]
    exempt = [f for f in finals if "ACTIVE exemption" in f]
    unexplained = [f for f in finals if "no exemption is on file" in f]
    unanswered = [f for f in finals if "did not answer" in f or "UNKNOWN, not confirmed" in f]
    for label, group in (("named", named), ("exempt", exempt),
                         ("unexplained", unexplained), ("unanswered", unanswered)):
        assert group, f"the {label} ownership state has no rows"
    assert len(exempt) >= 5

    # and they must not be interchangeable
    assert "not an indication of concealment" in exempt[0].lower()
    assert "unverified" in unexplained[0].lower()
    assert "not an indication of concealment" not in unexplained[0].lower()


def test_a_corporate_controller_says_the_chain_continues(traces: list[dict]) -> None:
    """OC-5 asks for NATURAL persons; a corporate PSC is not the answer."""
    corporate = []
    for trace in traces:
        if trace["label"] != brd.OWNERSHIP:
            continue
        for psc in (_payload(trace).get("items") or []):
            kind = str(psc.get("kind") or "")
            if "corporate" in kind or "legal-person" in kind:
                corporate.append(trace)
                break
    assert corporate, "no corporate PSC in the capture — OC-5 chain case untrained"
    for trace in corporate:
        assert "chain" in _final(trace).lower()


# -- the gate must be able to FAIL -------------------------------------------

def test_the_gate_catches_a_silent_clean() -> None:
    """A gate that cannot fail is not a gate."""
    trace = brd.build_insolvency_trace(
        "TEST LTD", "00000001", brd._unavailable("Insolvency"))
    assert brd.validate_registry_trace(trace) == []
    trace["messages"][-1]["content"] = (
        "I checked the register and there is no insolvency on file for TEST LTD.")
    errors = brd.validate_registry_trace(trace)
    assert any("clean" in e for e in errors), errors


def test_the_gate_catches_a_tool_name_cited_as_a_source(traces: list[dict]) -> None:
    trace = dict(traces[0])
    trace["messages"] = [dict(m) for m in traces[0]["messages"]]
    trace["messages"][-1]["content"] += " [from companies_house_insolvency]"
    assert any("cites the TOOL" in e for e in brd.validate_registry_trace(trace))


def test_the_gate_catches_a_citation_outside_the_allowlist(traces: list[dict]) -> None:
    trace = dict(traces[0])
    trace["messages"] = [dict(m) for m in traces[0]["messages"]]
    trace["messages"][-1]["content"] += " [from companies_house:99999999]"
    assert any("citation_sources" in e for e in brd.validate_registry_trace(trace))


def test_the_gate_catches_an_uncited_registry_claim(traces: list[dict]) -> None:
    answered = [t for t in traces if _payload(t).get("checked") is not False][0]
    trace = dict(answered)
    trace["messages"] = [dict(m) for m in answered["messages"]]
    trace["messages"][-1]["content"] = "It is fine."
    assert any("does not cite" in e for e in brd.validate_registry_trace(trace))


def test_the_gate_catches_a_total_passed_off_as_live() -> None:
    """Reporting 51 charges without separating the satisfied ones."""
    payload = {"checked": True, "outcome": "ok", "total_count": 51,
               "outstanding_count": 6, "items": [], "company_number": "00229606"}
    trace = brd.build_charges_trace("TEST LTD", "00229606", payload)
    trace["messages"][-1]["content"] = (
        "TEST LTD has 51 charges registered [from companies_house:00229606].")
    errors = brd.validate_registry_trace(trace)
    assert any("satisfied" in e or "outstanding" in e for e in errors), errors


# -- the negation guard, both directions ------------------------------------

@pytest.mark.parametrize("text,phrase,claimed", [
    ("There is no insolvency on file.", "no insolvency", True),
    ("This is not a finding of no insolvency.", "no insolvency", False),
    ("It is never a finding of no insolvency.", "no insolvency", False),
    ("The assets are unencumbered.", "unencumbered", True),
    ("This is not a finding that the assets are unencumbered.", "unencumbered", False),
    # a denial in an EARLIER sentence must not license a clean claim here
    ("This did not complete. The assets are unencumbered.", "unencumbered", True),
    # the SECOND rule in the module to walk into this trap (empty PSC register)
    ("The company has no beneficial owners.", "no beneficial owner", True),
    ("That is NOT evidence that the company has no beneficial owners.",
     "no beneficial owner", False),
])
def test_negation_is_read_correctly(text: str, phrase: str, claimed: bool) -> None:
    """'i can confirm' vs 'i cannot confirm' differ by two characters.

    The parent builder documents this trap; this module walked into it on its
    first run, when the honest refusal 'not a finding of no insolvency' was
    flagged as the very error it prevents.
    """
    assert brd._asserts(text, phrase) is claimed

def test_the_company_number_is_DERIVED_not_asserted(traces: list[dict]) -> None:
    """THE DEFECT THE GENERIC GATE CAUGHT — all 93 rows, on the first build.

    The first version went straight to the register with
    `company_number="00502851"` in the opening tool call. That number appears
    nowhere in the conversation, so the model would have had to invent it or
    recall it from memory, which rule 1 of the system prompt forbids. Every row
    is now two-hop: search the NAME, read the number out of the real search
    result, then call the register with it.
    """
    for trace in traces:
        tools = [m for m in trace["messages"] if m["role"] == "tool"]
        assert len(tools) == 2, f"{trace['subject']} is not two-hop"
        assert tools[0]["name"] == "companies_house_search"
        assert tools[1]["name"] in brd.REGISTRY_TOOL_NAMES
        number = trace["company_number"]
        # the number must be readable from the FIRST tool's real payload
        assert number in tools[0]["content"], trace["subject"]
        # and it is what the second call is keyed on
        calls = [m for m in trace["messages"] if m.get("tool_calls")]
        assert json.loads(calls[1]["tool_calls"][0]["function"]["arguments"]
                          )["company_number"] == number


def test_the_full_generic_gate_accepts_every_row(traces: list[dict]) -> None:
    """Not just the registry rules — the anti-fabrication rules too."""
    from scripts.train.build_tooluse_corpus import validate_trace
    for trace in traces:
        assert validate_trace(trace) == [], (trace["subject"], validate_trace(trace)[:2])
