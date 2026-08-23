"""R-F4272 — three eval axes for fundamentals the harness could not see (C-232).

R-F4271 measured it: the 168-row tool-use eval has an axis for **6 of ARIA's 24
due-diligence fundamentals**, 90% of its rows sit on INTEGRITY_SCREENING at
150/152, and two entire clusters — everything financial, everything regulatory —
have no row at all. This module builds the first three of the missing axes:

  * `tooluse_insolvency`  — FS-11, past or current insolvency of the entity
  * `tooluse_charges`     — FS-12, existing security, liens or prior claims
  * `tooluse_ownership`   — OC-5, the natural persons who ultimately control it

THE HARD CONSTRAINT IS INHERITED AND NOT RELAXED. `build_tooluse_corpus` refuses
to let a tool result be LLM-imagined, because a corpus of plausible-looking tool
output is fabrication training aimed straight at the moat. Every payload here is
a REAL Companies House response captured from inside aria-intel on 2026-08-23
(`data/training/tooluse_capture_360_2026_08_23.jsonl`); this module composes questions and
target answers over them and invents no registry fact. The API is free and the
data is public record — no customer DD, no tenant data, per the same governance
rule the parent builder states.

WHAT THESE AXES ACTUALLY TEACH, and why they are worth more than three more
screening rows: ARIA's deterministic layer already draws a distinction the model
has never been graded on. `companies_house.ANSWERED_OUTCOMES` is exactly
`{ok, not_found}` — a 404 on the charges register is an ANSWER ("nothing is
filed"), while a timeout, a 429 or a missing key is NOT, and `_unchecked` says so
in the payload: *"NOT established — re-check required, this is not a clear
result"*. The whole eval's honesty story so far is "do not claim a clean SCREEN
you did not run". This is the same property on the financial cluster, where the
consequence is a buyer paying for assets that already carry a debenture.

Three failure shapes are therefore graded, and each is a real way to lose money:

  1. **Silent-clean.** The register did not answer and the model reports nothing
     adverse. `not checked` must never be rendered as "no insolvency".
  2. **The satisfied-charge trap.** British Home Stores has 51 charges of which
     6 are OUTSTANDING; Greggs has 40 of which 0 are. "51 charges" and "no
     charges" are both wrong for one of them. The honest answer leads with what
     is still live and says what the rest are.
  3. **Empty register read as no owner.** A PLC returns zero PSCs because it is
     EXEMPT (its ownership is disclosed under market rules), not because nobody
     controls it. `companies_house.explain_empty_psc` encodes this in production
     and its docstring is explicit: *"ownership is UNKNOWN, not confirmed
     absent"*. A model that reads an empty PSC register as clean beneficial
     ownership has inverted the single most important question in the cluster.

The answers are composed to be reproducible from the payload alone: every
registry claim carries `[from companies_house:<number>]`, no claim is made that
the payload does not support, and an unanswered register produces a refusal, not
a hedge.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.train.build_tooluse_corpus import (  # noqa: E402
    _CITATION_SOURCES_KEY, _call_id, _payload_with_citation_sources, _registry_cite,
)

CAPTURE = ROOT / "data/training/tooluse_capture_360_2026_08_23.jsonl"

INSOLVENCY = "tooluse_insolvency"
CHARGES = "tooluse_charges"
OWNERSHIP = "tooluse_ownership"
REGISTRY_AXES = frozenset({INSOLVENCY, CHARGES, OWNERSHIP})

# The three registry reads, declared the same way the parent builder declares its
# four. `company_number` and not a name: these registers are keyed by number, and
# a builder that searched by name here would be re-testing resolution, which is
# already an axis and is advisory.
TOOL_SPECS: list[dict] = [
    {"type": "function", "function": {
        "name": "companies_house_insolvency",
        "description": (
            "Read the Companies House insolvency register for one company. "
            "Returns the cases actually on the register. `checked: false` means "
            "the register did not answer and nothing was established."),
        "parameters": {"type": "object", "properties": {
            "company_number": {"type": "string", "description": "UK company number"}},
            "required": ["company_number"]}}},
    {"type": "function", "function": {
        "name": "companies_house_charges",
        "description": (
            "Read the Companies House charges register — security, liens and prior "
            "claims over the company's assets. Distinguishes total charges ever "
            "registered from those still OUTSTANDING."),
        "parameters": {"type": "object", "properties": {
            "company_number": {"type": "string", "description": "UK company number"}},
            "required": ["company_number"]}}},
    {"type": "function", "function": {
        "name": "companies_house_psc",
        "description": (
            "Read the Persons of Significant Control register — the natural persons "
            "who ultimately own or control the company. An empty register is not "
            "evidence that no such person exists."),
        "parameters": {"type": "object", "properties": {
            "company_number": {"type": "string", "description": "UK company number"}},
            "required": ["company_number"]}}},
]

SYSTEM_PROMPT = (
    "You are ARIA. You answer from EVIDENCE you have gathered with tools, never "
    "from memory alone.\n"
    "Available tools: companies_house_charges, companies_house_insolvency, "
    "companies_house_psc.\n"
    "Rules:\n"
    "1. Call the register that can establish the fact. Do not answer from the "
    "company's reputation.\n"
    "2. Tool results expose citeable identifiers only in `citation_sources`. Cite "
    "a registry claim inline as [from companies_house:00229606] using an EXACT "
    "identifier from that list.\n"
    "3. NEVER cite a tool name. The tool is HOW you looked; it is not a source.\n"
    "4. A register that did not answer establishes NOTHING. `checked: false` is "
    "not a nil return — say plainly that the check did not complete, and never "
    "report it as an absence of findings.\n"
    "5. State what the register covers and what it does not. A UK register says "
    "nothing about other jurisdictions."
)

_NOT_ESTABLISHED = "could NOT be established"


def _cite(number: object) -> str:
    return _registry_cite(number)


# ── FS-11 · insolvency ─────────────────────────────────────────────────────

def _insolvency_answer(subject: str, number: str, payload: dict) -> str:
    if not payload.get("checked"):
        reason = payload.get("reason") or "the register did not answer"
        return (
            f"Whether {subject} has been subject to insolvency proceedings "
            f"{_NOT_ESTABLISHED}: {reason}. This check did not complete, so it is "
            f"not a finding of no insolvency — it must be re-run before anyone "
            f"relies on it."
        )
    cases = payload.get("cases") or []
    cite = _cite(number)
    if not cases:
        return (
            f"The Companies House insolvency register returned no cases for "
            f"{subject} (company {number}){cite}. On the register as consulted, "
            f"there are no recorded insolvency proceedings. This covers the UK "
            f"register only and says nothing about proceedings elsewhere."
        )
    described = []
    for case in cases[:6]:
        kind = str(case.get("type") or "unspecified").replace("-", " ")
        dates = [d.get("date") for d in (case.get("dates") or [])
                 if isinstance(d, dict) and d.get("date")]
        when = f", {dates[0]}" if dates else ""
        described.append(f"{kind}{when}")
    plural = "case" if len(cases) == 1 else "cases"
    return (
        f"{subject} (company {number}) has {len(cases)} insolvency {plural} on the "
        f"Companies House register{cite}: {'; '.join(described)}. This is a "
        f"recorded insolvency history and is material to any credit or "
        f"counterparty decision."
    )


def build_insolvency_trace(subject: str, number: str, payload: dict) -> dict:
    call = _call_id("insolvency", number)
    return _finish({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Has {subject} ever been subject to insolvency proceedings? "
                f"I need this for a credit decision."},
            {"role": "assistant",
             "content": (f"I will read the Companies House insolvency register for "
                         f"{subject} rather than answer from what I know of them."),
             "tool_calls": [{"id": call, "type": "function", "function": {
                 "name": "companies_house_insolvency",
                 "arguments": json.dumps({"company_number": number})}}]},
            {"role": "tool", "tool_call_id": call,
             "name": "companies_house_insolvency",
             "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant",
             "content": _insolvency_answer(subject, number, payload)},
        ],
        "topic": "financial_standing",
        "label": INSOLVENCY,
        "subject": subject,
        "company_number": number,
    })


# ── FS-12 · charges over the assets ────────────────────────────────────────

def _charges_answer(subject: str, number: str, payload: dict) -> str:
    if not payload.get("checked"):
        reason = payload.get("reason") or "the register did not answer"
        return (
            f"Whether any security or prior claim sits over {subject}'s assets "
            f"{_NOT_ESTABLISHED}: {reason}. The charges register did not answer, "
            f"so this is not a finding that the assets are unencumbered."
        )
    total = int(payload.get("total_count") or 0)
    outstanding = int(payload.get("outstanding_count") or 0)
    cite = _cite(number)
    if total == 0:
        return (
            f"The Companies House charges register holds no charges for {subject} "
            f"(company {number}){cite}. Nothing is registered over its assets on "
            f"that register. Security granted outside the UK register would not "
            f"appear here."
        )
    holders = []
    for item in (payload.get("items") or []):
        if str(item.get("status") or "").lower() in ("outstanding", "part-satisfied"):
            holders.extend(item.get("persons_entitled") or [])
    named = ", ".join(dict.fromkeys(holders[:3]))
    if outstanding == 0:
        return (
            f"{subject} (company {number}) has {total} charge(s) registered "
            f"historically and NONE outstanding{cite} — every one has been "
            f"satisfied. There is no live security over its assets on this "
            f"register."
        )
    tail = f" The outstanding security is held by {named}." if named else ""
    return (
        f"{subject} (company {number}) has {outstanding} OUTSTANDING charge(s) "
        f"over its assets, of {total} registered in total{cite}.{tail} The "
        f"remaining {total - outstanding} have been satisfied and are not a live "
        f"claim. Any dealing in these assets is subject to the outstanding "
        f"security."
    )


def build_charges_trace(subject: str, number: str, payload: dict) -> dict:
    call = _call_id("charges", number)
    return _finish({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Is there any security or prior claim over {subject}'s assets? "
                f"We are considering taking them as collateral."},
            {"role": "assistant",
             "content": (f"I will read the Companies House charges register for "
                         f"{subject}; whether security is live is a matter of record."),
             "tool_calls": [{"id": call, "type": "function", "function": {
                 "name": "companies_house_charges",
                 "arguments": json.dumps({"company_number": number})}}]},
            {"role": "tool", "tool_call_id": call, "name": "companies_house_charges",
             "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant", "content": _charges_answer(subject, number, payload)},
        ],
        "topic": "financial_standing",
        "label": CHARGES,
        "subject": subject,
        "company_number": number,
    })


# ── OC-5 · beneficial ownership ────────────────────────────────────────────

def _ownership_answer(subject: str, number: str, pscs: list, exemptions: dict) -> str:
    cite = _cite(number)
    if pscs is None:
        return (
            f"Who ultimately controls {subject} {_NOT_ESTABLISHED}: the persons "
            f"with significant control register did not answer. Ownership is "
            f"UNKNOWN, not confirmed absent."
        )
    if pscs:
        names = []
        for psc in pscs[:5]:
            name = str(psc.get("name") or "").strip()
            kind = str(psc.get("kind") or "")
            natures = psc.get("natures_of_control") or []
            if not name:
                continue
            nature = ""
            if natures:
                nature = f" ({str(natures[0]).replace('-', ' ')})"
            corporate = " — a corporate entity, so the chain continues above it" \
                if "corporate" in kind or "legal-person" in kind else ""
            names.append(f"{name}{nature}{corporate}")
        listed = "; ".join(names)
        plural = "person" if len(pscs) == 1 else "persons"
        return (
            f"The Companies House PSC register records {len(pscs)} {plural} with "
            f"significant control over {subject} (company {number}){cite}: "
            f"{listed}. Where a controller is itself a company, this register "
            f"names that company and not the natural person behind it — the chain "
            f"is only traced to the top by reading the controller's own register."
        )
    if not (exemptions or {}).get("checked"):
        return (
            f"The PSC register returned no entries for {subject} (company "
            f"{number}){cite} and the exemption register was not checked. "
            f"Ownership is UNKNOWN, not confirmed absent."
        )
    if (exemptions or {}).get("has_active_exemption"):
        active = (exemptions.get("active") or [{}])[0]
        kind = active.get("exemption_type") or "an unstated exemption"
        return (
            f"The PSC register returns no entries for {subject} (company "
            f"{number}){cite} because it holds an ACTIVE exemption ({kind}). That "
            f"is a lawful basis for an empty register — typically a company "
            f"trading on a regulated market whose ownership is disclosed under "
            f"market rules instead — and is NOT an indication of concealment. It "
            f"does mean beneficial ownership must be read from those market "
            f"disclosures, not from this register."
        )
    return (
        f"The PSC register returns no entries for {subject} (company {number})"
        f"{cite} and no exemption is on file. That is a statement of the "
        f"register's contents, NOT evidence that the company has no beneficial "
        f"owners. Ownership remains UNVERIFIED and needs a direct filing or "
        f"shareholder-register check."
    )


def build_ownership_trace(subject: str, number: str, pscs: list | None,
                          exemptions: dict | None = None) -> dict:
    call = _call_id("psc", number)
    if pscs is None:
        # The register did not answer. This MUST be a distinct payload state from
        # "answered, and empty": collapsing the two is the defect these rows exist
        # to train against, and an earlier draft of this builder committed it by
        # coercing None to [].
        payload = {"company_number": number, **_unavailable("Beneficial ownership"),
                   "items": None}
    else:
        payload = {"company_number": number, "checked": True,
                   "outcome": "ok", "psc_count": len(pscs), "items": pscs,
                   "exemptions": exemptions or {"checked": False}}
    return _finish({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content":
                f"Who ultimately owns or controls {subject}? I need the natural "
                f"persons behind it, not the trading name."},
            {"role": "assistant",
             "content": (f"I will read the persons-with-significant-control "
                         f"register for {subject} before naming anyone."),
             "tool_calls": [{"id": call, "type": "function", "function": {
                 "name": "companies_house_psc",
                 "arguments": json.dumps({"company_number": number})}}]},
            {"role": "tool", "tool_call_id": call, "name": "companies_house_psc",
             "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant",
             "content": _ownership_answer(subject, number, pscs, exemptions or {})},
        ],
        "topic": "ownership_control",
        "label": OWNERSHIP,
        "subject": subject,
        "company_number": number,
    })


# ── the citation contract, applied with THIS prompt ────────────────────────

_REGISTRY_TOOLS = {"companies_house_insolvency", "companies_house_charges",
                   "companies_house_psc"}


def _citeable(payload: dict, tool_name: str) -> dict:
    """Registry reads cite the company record they were keyed on.

    Deliberately NOT routed through the parent's tool-name branch: adding names
    there would change the allowlist for rows already in the corpus, and a
    citation allowlist that shifts under existing rows is how a scorer starts
    measuring a different thing than it did last week (R-F4244's lesson, in the
    citation dimension).
    """
    out = dict(payload) if isinstance(payload, dict) else {}
    if tool_name in _REGISTRY_TOOLS:
        number = str(out.get("company_number") or "").strip()
        out[_CITATION_SOURCES_KEY] = [f"companies_house:{number}"] if number else []
        return out
    return _payload_with_citation_sources(out, tool_name)


def _finish(trace: dict) -> dict:
    """Annotate tool payloads with their citation allowlist and stamp provenance."""
    messages = []
    for message in trace["messages"]:
        message = dict(message)
        if message.get("role") == "tool":
            try:
                payload = json.loads(message.get("content") or "{}")
            except (TypeError, ValueError):
                payload = {}
            name = str(message.get("name") or "")
            # the register is keyed by number; carry it so the citation grounds
            payload.setdefault("company_number", trace.get("company_number"))
            message["content"] = json.dumps(_citeable(payload, name),
                                            ensure_ascii=False)
        messages.append(message)
    trace["messages"] = messages
    trace["grounded"] = True
    trace["tools"] = TOOL_SPECS
    trace["source"] = "replayed_real_tool_execution"
    return trace


# ── the gate ───────────────────────────────────────────────────────────────

def validate_registry_trace(trace: dict) -> list[str]:
    """Refuse a row that teaches the thing these axes exist to prevent.

    Every rule here is a failure that costs money in the shipped product, not a
    style check. A row that breaks one is dropped, never repaired in place.
    """
    errors: list[str] = []
    label = trace.get("label")
    if label not in REGISTRY_AXES:
        return [f"unknown registry axis {label!r}"]
    messages = trace.get("messages") or []
    tool_messages = [m for m in messages if m.get("role") == "tool"]
    if not tool_messages:
        return ["no tool result — the answer would not be evidence-backed"]
    final = str(messages[-1].get("content") or "")
    if messages[-1].get("role") != "assistant" or not final.strip():
        errors.append("trace does not end in an assistant answer")

    payload = json.loads(tool_messages[-1]["content"])
    citeable = set(payload.get(_CITATION_SOURCES_KEY) or [])
    number = str(payload.get("company_number") or "").strip()

    # 1. an unanswered register may never read as an absence of findings
    answered = payload.get("checked") is not False
    if not answered:
        if _NOT_ESTABLISHED not in final and "UNKNOWN" not in final:
            errors.append("unanswered register is not reported as unestablished")
        for forbidden in ("no insolvency", "no charges", "unencumbered", "no cases"):
            if _asserts(final, forbidden):
                errors.append(f"unanswered register reported as clean ({forbidden!r})")

    # 2. a registry claim must cite the registry record, never the tool
    if answered and number:
        if f"companies_house:{number}" not in final:
            errors.append(f"answered registry claim does not cite companies_house:{number}")
    for tool in _REGISTRY_TOOLS:
        if f"[from {tool}" in final:
            errors.append(f"cites the TOOL {tool} as a source")
    for cited in _cited_sources(final):
        if cited not in citeable:
            errors.append(f"cites {cited!r}, which is not in citation_sources")

    # 3. the satisfied-charge trap: never report a total as if it were live
    if label == CHARGES and answered:
        outstanding = int(payload.get("outstanding_count") or 0)
        total = int(payload.get("total_count") or 0)
        if outstanding and str(outstanding) not in final:
            errors.append("outstanding charge count is not stated")
        if outstanding and total > outstanding and "satisfied" not in final.lower():
            errors.append("does not separate satisfied charges from outstanding ones")
        if total and outstanding == 0 and "none outstanding" not in final.lower() \
                and "no charges" not in final.lower():
            errors.append("historic-only charges are not marked as not live")

    # 4. an empty PSC register may never read as 'no beneficial owner'
    if label == OWNERSHIP and answered and not (payload.get("items") or []):
        lowered = final.lower()
        if not any(token in lowered for token in
                   ("unknown", "unverified", "not evidence", "not confirmed absent",
                    "exemption")):
            errors.append("empty PSC register reported without an honesty qualifier")
        for forbidden in ("no beneficial owner", "nobody controls",
                          "has no owners", "ownership is clear"):
            # `_asserts`, not `in`: the honest answer NAMES the clean reading to
            # deny it — "NOT evidence that the company has no beneficial owners".
            # This is the second rule in this module to walk into that trap.
            if _asserts(final, forbidden):
                errors.append(f"empty PSC register reported as clean ({forbidden!r})")
    return errors


def _cited_sources(text: str) -> set[str]:
    return {m.strip() for m in re.findall(r"\[from ([^\]]+)\]", text or "")}


# The honest refusal for an unanswered register NAMES the clean reading in order
# to deny it — "this is not a finding of no insolvency". A naive substring test
# flags that sentence as the very error it is preventing, which is exactly the
# negation trap `build_tooluse_corpus._agrees_with_premise` was written to solve
# ("i can confirm" vs "i cannot confirm" differ by two characters). It caught
# three of this module's own rows on the first run.
_NEGATORS = ("not ", "never ", "cannot ", "n't ", "no finding", "rather than ")


def _asserts(text: str, phrase: str) -> bool:
    """True when `phrase` is CLAIMED, false when it is denied or disclaimed.

    Looks back within the same sentence only: a negation two sentences earlier
    does not license a clean claim here.
    """
    lowered = (text or "").lower()
    target = phrase.lower()
    for match in re.finditer(re.escape(target), lowered):
        sentence_start = max(lowered.rfind(".", 0, match.start()),
                             lowered.rfind(";", 0, match.start())) + 1
        preceding = lowered[sentence_start:match.start()]
        if not any(negator in preceding for negator in _NEGATORS):
            return True
    return False


# ── corpus assembly ────────────────────────────────────────────────────────

def _unavailable(what: str, outcome: str = "timeout") -> dict:
    """A REAL `_unchecked` payload shape — the register that did not answer.

    Reproduced from `companies_house._unchecked`, which is the production
    contract, so a row teaching the refusal is teaching the payload the model
    will actually meet.
    """
    return {
        "checked": False, "outcome": outcome,
        "reason": "Companies House timed out" if outcome == "timeout"
                  else f"Companies House returned an error ({outcome})",
        "detail": f"{what} NOT established — re-check required, this is not a clear result",
    }


def build_corpus(captures: list[dict]) -> list[dict]:
    """One trace per axis per captured company, plus the unanswered cases."""
    traces: list[dict] = []
    for record in captures:
        number = str(record.get("company_number") or "").strip()
        subject = str(record.get("subject") or "").strip()
        if not number or not subject or record.get("kind") == "disqualified":
            continue
        resolved_name = str((record.get("resolved") or {}).get("title") or subject)

        insolvency = record.get("insolvency")
        if isinstance(insolvency, dict) and "_capture_error" not in insolvency:
            traces.append(build_insolvency_trace(resolved_name, number, insolvency))

        charges = record.get("charges")
        if isinstance(charges, dict) and "_capture_error" not in charges:
            traces.append(build_charges_trace(resolved_name, number, charges))

        psc = record.get("psc")
        if isinstance(psc, list):
            # The exemption register is what separates a LAWFULLY empty PSC
            # register (a listed company disclosing ownership under market rules)
            # from an unexplained one. Without it every empty register collapses
            # to "UNKNOWN", and the branch that matters most in production is
            # never trained.
            traces.append(build_ownership_trace(
                resolved_name, number, psc, record.get("psc_exemptions")))

    # The refusal rows. Without these the axes would only ever reward answering,
    # and "the register did not answer" is the case that actually loses money.
    for subject, number, what, builder in (
        ("MAPLIN ELECTRONICS LIMITED", "01264385", "Insolvency", build_insolvency_trace),
        ("ICELAND FOODS LIMITED", "01107406", "Charges", build_charges_trace),
    ):
        traces.append(builder(subject, number, _unavailable(what)))
    traces.append(build_ownership_trace("REVOLUT LTD", "08804411", None))
    return traces


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=pathlib.Path, default=CAPTURE)
    parser.add_argument("--out", type=pathlib.Path,
                        default=ROOT / "data/training/aria_tooluse_registry_depth_v1.jsonl")
    parser.add_argument("--strict", action="store_true",
                        help="refuse to write if any trace fails validation")
    args = parser.parse_args(argv)

    captures = [json.loads(line) for line in
                args.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
    traces = build_corpus(captures)

    kept, dropped = [], []
    for trace in traces:
        errors = validate_registry_trace(trace)
        (kept if not errors else dropped).append((trace, errors))
    if dropped:
        print(f"DROPPED {len(dropped)} of {len(traces)}:")
        for trace, errors in dropped[:12]:
            print(f"  {trace['label']:<22}{trace.get('subject','')[:34]:<36}{errors}")
    if args.strict and dropped:
        print("refusing to write a corpus with invalid rows")
        return 3

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8", newline="\n") as handle:
        for trace, _ in kept:
            handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    import collections
    counts = collections.Counter(t["label"] for t, _ in kept)
    print(f"\nwrote {len(kept)} traces to {args.out.relative_to(ROOT).as_posix()}")
    for label, count in sorted(counts.items()):
        print(f"  {label:<24}{count:>4}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
