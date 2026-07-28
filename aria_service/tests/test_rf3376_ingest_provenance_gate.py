"""R-F3376 — corpus ingest had no rights gate: copyrighted or marked material entered RAG unchecked.

THE GAP. `ingest_corpus_document()` validates exactly one thing — that `tier` is
in VALID_TIERS. Its docstring says it stores "full provenance metadata", but
provenance there means WHO published it, never whether ARIA is ALLOWED to hold or
repeat it. There is no rights field, no licence field, and no marking check, and
`POST /api/aria/corpus/ingest` passes a base64 blob straight through.

WHY IT MATTERS FOR THIS PRODUCT SPECIFICALLY. ARIA is a defence/security/DD
system whose retrieved text reaches customer-facing output. Two classes of
document must never be held verbatim:

  1. THIRD-PARTY COPYRIGHT. The vetting module already carries the binding rule
     "clause numbers ONLY — never store the standard's text (BSI copyright)", and
     encodes BS 7858 as a 26-clause register in `standard_map.py` for exactly
     that reason. Nothing stopped the PDF itself being ingested and later quoted
     back to a client.
  2. PROTECTIVE MARKINGS. In this field documents arrive marked OFFICIAL-SENSITIVE,
     CONFIDENTIAL, "not for onward distribution". Putting those into a shared
     retrieval corpus is a disclosure problem, not a licensing one.

FAIL-CLOSED, AND NOT MERELY SELF-DECLARED. `rights` is now REQUIRED — absent is
not permissive. `third_party_copyright` and `restricted` are refused outright;
the vocabulary itself provides the legitimate route (`licensed`) so no override
flag is needed, and an absolute refusal cannot be argued around under deadline.

And because a self-declared label can simply be wrong, the text is ALSO scanned
for markings that contradict the declaration. A document stamped "© BSI" or
"OFFICIAL-SENSITIVE" is refused even when the uploader ticked "owned". The label
is a claim; the tripwire is evidence.

CARRIER, NOT DECORATION. rag_store.search returns chunk metadata, so `rights`
survives to retrieval and `may_quote_verbatim()` lets every consumer honour it.
A marking that does not reach the consumer is the producer→consumer-no-carrier
defect this repo keeps rediscovering.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from aria_service.intel import corpus_ingest as CI


def _run(coro):
    return asyncio.run(coro)


def _ingest(**kw):
    """Drive the REAL ingest with rag_store stubbed at the boundary."""
    defaults = dict(filename="doc.pdf", tier="A", source_class="Test Publisher")
    defaults.update(kw)
    text = defaults.pop("text", "A" * 200)
    with patch("aria_service.intel.rag_store.ingest_document",
               new=AsyncMock(return_value={"chunks": 1})) as m:
        result = _run(CI.ingest_corpus_document(text, **defaults))
    return result, m


# ── rights is REQUIRED, and absent is not permissive ───────────────────────

def test_ingest_without_rights_is_refused():
    with pytest.raises(ValueError) as e:
        _ingest()
    assert "rights" in str(e.value).lower()


def test_unknown_rights_value_is_refused():
    with pytest.raises(ValueError):
        _ingest(rights="probably fine")


@pytest.mark.parametrize("ok", ["owned", "public_domain", "open_licence", "licensed"])
def test_permitted_rights_values_ingest(ok):
    # `licensed` legitimately requires a note naming the licence — supply one.
    extra = {"rights_note": "site licence ref 12345"} if ok == "licensed" else {}
    result, m = _ingest(rights=ok, **extra)
    assert m.called
    assert result["rights"] == ok


# ── the two refused classes ────────────────────────────────────────────────

def test_third_party_copyright_is_refused_outright():
    """The BS 7858 class. No override exists on purpose — `licensed` is the
    legitimate route, so an absolute refusal cannot be argued around."""
    with pytest.raises(ValueError) as e:
        _ingest(rights="third_party_copyright")
    assert "copyright" in str(e.value).lower()


def test_restricted_is_refused_outright():
    with pytest.raises(ValueError):
        _ingest(rights="restricted")


def test_refusal_happens_before_any_write():
    with patch("aria_service.intel.rag_store.ingest_document", new=AsyncMock()) as m:
        with pytest.raises(ValueError):
            _run(CI.ingest_corpus_document("A" * 200, filename="f.pdf", tier="A",
                                           source_class="X", rights="third_party_copyright"))
    assert not m.called, "refused document still reached the RAG store"


# ── the tripwire: a wrong label does not get you through ───────────────────

@pytest.mark.parametrize("marking", [
    "© BSI 2019. All rights reserved.",
    "BRITISH STANDARD BS 7858:2019",
    "OFFICIAL-SENSITIVE",
    "This document is CONFIDENTIAL and not for onward distribution.",
])
def test_declared_owned_but_marked_document_is_refused(marking):
    body = "Screening and vetting procedures. " * 20 + marking
    with pytest.raises(ValueError) as e:
        _ingest(rights="owned", text=body)
    assert "marking" in str(e.value).lower() or "detect" in str(e.value).lower()


def test_detector_names_what_it_found():
    found = CI.detect_restricted_markings("Something OFFICIAL-SENSITIVE here")
    assert found and any("OFFICIAL-SENSITIVE" in f.upper() for f in found)


def test_detector_does_not_fire_on_ordinary_prose():
    assert CI.detect_restricted_markings(
        "The company publishes an annual report and confidential investor "
        "relations are handled by the board."
    ) == []


def test_licensed_document_carrying_a_copyright_notice_is_allowed():
    """A licence is exactly the case where a © notice is expected. Refusing it
    would make the gate unusable for material we have paid for."""
    body = "Standard text. " * 30 + "© BSI 2019"
    result, m = _ingest(rights="licensed", rights_note="site licence ref 12345", text=body)
    assert m.called and result["rights"] == "licensed"


def test_licensed_requires_a_note_saying_under_what():
    with pytest.raises(ValueError) as e:
        _ingest(rights="licensed")
    assert "note" in str(e.value).lower()


# ── the marking must CARRY to retrieval, or it is decoration ──────────────

def test_rights_is_written_into_chunk_metadata():
    _result, m = _ingest(rights="owned")
    meta = m.call_args.kwargs["extra_metadata"]
    assert meta["rights"] == "owned"


def test_rights_is_visible_in_the_source_string():
    """Citations render the source string; the rights marking travels with it."""
    _result, m = _ingest(rights="open_licence")
    assert "open_licence" in m.call_args.kwargs["source"]


@pytest.mark.parametrize("rights,quotable", [
    ("owned", True), ("public_domain", True), ("open_licence", True),
    ("licensed", False),
])
def test_may_quote_verbatim_reflects_the_rights(rights, quotable):
    assert CI.may_quote_verbatim({"rights": rights}) is quotable


def test_may_quote_verbatim_is_fail_closed_on_unknown_metadata():
    for meta in ({}, None, {"rights": ""}, {"rights": "nonsense"}, "x"):
        assert CI.may_quote_verbatim(meta) is False


# ── the HTTP route must not be a way around the gate ──────────────────────

def test_route_requires_rights():
    from pathlib import Path
    src = (Path(CI.__file__).parent.parent / "routes" / "aria.py").read_text(encoding="utf-8")
    i = src.find('_ci.ingest_corpus_document(')
    assert i > 0
    window = src[i - 2500:i + 600]
    assert "rights" in window, "the ingest route never passes a rights value"


# ── derived facts: ARIA's own sentence from someone else's data fields ────

def test_derived_facts_is_permitted_and_quotable():
    result, m = _ingest(rights="derived_facts", rights_note="from CSV fields")
    assert m.called and result["rights"] == "derived_facts"
    assert CI.may_quote_verbatim({"rights": "derived_facts"}) is True


def test_existing_sipri_caller_declares_rights():
    """A required parameter breaks every caller that does not pass it. The one
    in-tree caller must declare, and declare HONESTLY — not as `owned`, which
    would overstate our claim to SIPRI's dataset."""
    from pathlib import Path
    src = (Path(CI.__file__).parent / "sipri_ingest.py").read_text(encoding="utf-8")
    assert "rights=" in src, "sipri_ingest would now raise on every row"
    assert 'rights="derived_facts"' in src
    assert 'rights="owned"' not in src


def test_no_other_caller_is_left_undeclared():
    """Any call site that omits rights is now a runtime break, not a warning."""
    import re
    from pathlib import Path
    root = Path(CI.__file__).parent.parent
    offenders = []
    for py in list(root.rglob("*.py")):
        if "tests" in py.parts or py.name == "corpus_ingest.py":
            continue
        txt = py.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"ingest_corpus_document\s*\(", txt):
            window = txt[m.end():m.end() + 900]
            if "rights" not in window:
                offenders.append(f"{py.name}:{txt[:m.start()].count(chr(10)) + 1}")
    assert not offenders, f"call sites without a rights declaration: {offenders}"
