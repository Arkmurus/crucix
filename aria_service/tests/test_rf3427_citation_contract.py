"""R-F3427 — the corpus must keep the citation promise its own prompt makes.

MEASURED after the first full cycle. The system prompt says "Cite every claim
inline as [from <source>]", and 387 of 656 rows (59%) carry no citation at all:
resolution 63/63, multi-hop 42/42, person 30/30, single-hop 112/155, challenge
70/116. Where rows DO cite, they use two different target kinds — outlet domains
(reuters.com) on the search axes, list identifiers (ofac_sdn) on the sanctions
axes.

So the model was trained on an unconditional instruction against a corpus that
mostly ignores it, with no consistent notion of what a citation names. It
resolved the contradiction by citing the most salient token available: THE TOOL'S
OWN NAME. The trained model's dominant failures were `[from
company_house_officers]` (7) and `[from aria_search]` (5), and news impact —
the axis most dependent on outlet citation — regressed 1.000 -> 0.500.

The fix is to make the corpus keep the promise, not to weaken the promise.
Citation is the USP: an answer whose sources cannot be checked is not
verifiable, and teaching "cite sometimes" teaches "cite whatever looks source-ish".

THREE RULES, all checkable:
  * a claim derived from a tool payload carries a citation
  * the citation names a SOURCE the payload contains — outlet domain, list id,
    or registry:number — never a tool name
  * when nothing was returned there is nothing to cite, and the answer says so
    rather than inventing a citation
"""
from __future__ import annotations

import glob
import json
import re
from pathlib import Path

import pytest

from scripts.train import build_tooluse_corpus as B

CITE = re.compile(r"\[from ([^\]]+)\]")
ROOT = Path(__file__).resolve().parents[2]

# Axes where the tool legitimately returned nothing to cite. These must SAY so.
NO_SOURCE_AXES = {"tooluse_trace_unavailable", "tooluse_challenge_unavailable"}


def _rows():
    """Every TRACE row in the tool-use corpora.

    R-F3650 — selection is by SCHEMA, not by filename. This glob was written when
    `aria_tooluse_*.jsonl` meant "an SFT trace corpus", and it now also matches
    `aria_tooluse_dpo_v1.jsonl`, whose 13 rows are PREFERENCE PAIRS
    (`prompt`/`chosen`/`rejected`) with no `messages` key at all — so these tests
    died with `KeyError: 'messages'` on any machine that had run a DPO build.
    The file is untracked, which is why the failure is invisible in CI and absent
    from the suite baseline: it appears only after a real cycle produces pairs.

    A preference pair is a legitimately different artefact, so it is skipped. A
    row that is NEITHER a trace nor a pair is still yielded, so a genuinely
    malformed corpus row keeps failing loudly instead of being filtered away.
    """
    for f in glob.glob(str(ROOT / "data" / "training" / "aria_tooluse_*.jsonl")):
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict) and "messages" not in row \
                    and "chosen" in row and "rejected" in row:
                continue          # a DPO preference pair, not a trace
            yield row


# --------------------------------------------------------------------------
# the rule that produced the regression
# --------------------------------------------------------------------------

def test_a_tool_name_is_never_a_valid_citation():
    """`[from company_house_officers]` is what the trained model actually emitted."""
    payload = {"status": "OK", "entity": "Acme",
               "sanctions": {"screened": True, "sources": ["ofac_sdn"], "matches": []}}
    t = B.build_trace("Acme", payload)
    for tool in sorted(B.TOOL_NAMES):
        t["messages"][-1]["content"] = (
            f"Acme was screened and no matches were found [from {tool}]."
        )
        errs = B.validate_trace(t)
        assert errs, f"citing the tool name {tool!r} must never validate"


def test_the_rule_holds_on_every_axis_not_just_the_search_ones():
    """The tool-name citation appeared on registry answers, which had no check."""
    for label in ("tooluse_trace", "tooluse_multihop", "tooluse_person",
                  "tooluse_resolution"):
        rows = [r for r in _rows() if r["label"] == label]
        if not rows:
            continue
        t = dict(rows[0])
        t["messages"] = list(t["messages"][:-1]) + [
            {"role": "assistant",
             "content": f"{t.get('subject','X')} result [from companies_house_officers]."}]
        assert B.validate_trace(t), f"{label}: tool-name citation survived validation"


# --------------------------------------------------------------------------
# the corpus must keep its own promise
# --------------------------------------------------------------------------

def test_the_system_prompt_states_what_a_citation_NAMES():
    """"[from <source>]" alone left the target undefined across axes."""
    p = B.SYSTEM_PROMPT.lower()
    assert "never" in p and "tool name" in p, (
        "the prompt must forbid citing a tool name — the model reached for it")
    assert "nothing" in p or "no source" in p, (
        "the prompt must say what to do when there is nothing to cite")


def _citable_sources(row: dict) -> bool:
    """Did the tools actually return something this answer could cite?

    MEASURED, not assumed: the live screen payload's sanctions block carries
    only {matched, matches, risk_level, verdict} — there is NO `sources` field,
    so a CLEAN screen genuinely names no lists. Forcing a citation there would
    fabricate coverage, which is the exact harm this corpus exists to prevent.
    Only rows whose payload contains a real source identifier are required to
    cite one.
    """
    for m in row["messages"]:
        if m.get("role") != "tool":
            continue
        try:
            p = json.loads(m.get("content") or "{}")
        except Exception:
            continue
        if not isinstance(p, dict):
            continue
        if p.get("company_number"):
            return True
        if (p.get("results") or [{}])[0].get("company_number"):
            return True
        for mm in ((p.get("sanctions") or {}).get("matches") or []):
            if mm.get("list"):
                return True
        for r in (p.get("results") or []):
            if isinstance(r, dict) and str(r.get("url") or "").startswith("http"):
                return True
    return False


@pytest.mark.parametrize("label", [
    "tooluse_multihop", "tooluse_resolution", "tooluse_person",
    "tooluse_trace", "tooluse_challenge",
])
def test_every_row_that_CAN_cite_does(label):
    """The real defect: 123 rows had a source in the payload and cited nothing.

    The other 224 uncited rows are correct — a clean screen returns no source
    identifier, and inventing one would be fabrication. The prompt now permits
    that case explicitly instead of promising a citation that cannot exist.
    """
    rows = [r for r in _rows() if r["label"] == label]
    if not rows:
        pytest.skip(f"{label} not present")
    # An answer that REFUSES to resolve ("I cannot safely say which company you
    # mean") names no record, so it has nothing to cite. The payload contains
    # candidates; the answer deliberately picks none of them. Requiring a
    # citation there would force the trace to assert a company it just declined
    # to identify — the ambiguity failure the resolution axis exists to teach.
    bad = [r for r in rows
           if _citable_sources(r)
           and not CITE.findall(r["messages"][-1]["content"])
           and "cannot safely say" not in r["messages"][-1]["content"].lower()]
    assert not bad, (
        f"{label}: {len(bad)}/{len(rows)} rows had a citable source in the payload "
        f"and cited nothing")


@pytest.mark.parametrize("label", sorted(NO_SOURCE_AXES))
def test_axes_with_nothing_to_cite_say_so_instead_of_citing(label):
    """The honest case: the source was unavailable, so there IS no citation."""
    rows = [r for r in _rows() if r["label"] == label]
    if not rows:
        pytest.skip(f"{label} not present")
    for r in rows:
        final = r["messages"][-1]["content"]
        assert not CITE.findall(final), f"{label}: cited a source that never returned"
        assert B._DECLARES_NOT_SCREENED_RE.search(final), (
            f"{label}: must state the source was unavailable")


def test_every_citation_in_the_corpus_names_something_the_payload_contains():
    """The whole point of the contract, asserted across the shipped corpus."""
    bad = []
    for r in _rows():
        payload = " ".join(
            str(m.get("content") or "") for m in r["messages"]
            if m.get("role") == "tool").lower()
        for c in CITE.findall(r["messages"][-1]["content"]):
            token = c.strip().lower()
            if token in {n.lower() for n in B.TOOL_NAMES}:
                bad.append((r["label"], c, "tool name"))
            # For a `register:identifier` citation the IDENTIFIER is what the
            # payload contains and what grounds it — the register's name is a
            # label, not evidence. Checking the label was backwards, and it
            # disagreed with the validator, which had it right.
            elif ((token.partition(":")[2] or token) not in payload
                  and token not in payload):
                bad.append((r["label"], c, "absent from payload"))
    assert not bad, f"{len(bad)} ungrounded citations, e.g. {bad[:5]}"
