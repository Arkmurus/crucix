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
    _CITATION_SOURCES_KEY, _NOT_ESTABLISHED, _asserts, _call_id,
    _payload_with_citation_sources, _registry_cite,
    CHARGES, INSOLVENCY, OWNERSHIP, REGISTRY_AXES, REGISTRY_TOOL_NAMES,
    validate_registry_trace, validate_trace,
)

CAPTURE = ROOT / "data/training/tooluse_capture_360_2026_08_23.jsonl"


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



def _cite(number: object) -> str:
    return _registry_cite(number)


# ── the two-hop shape ──────────────────────────────────────────────────────

def _two_hop(subject: str, number: str, search: list, tool: str, arg_note: str,
             payload: dict, answer: str, label: str, topic: str) -> dict:
    """search the NAME, derive the NUMBER from the result, then read the register.

    R-F4274 — the first version of these axes went straight to the register with
    `company_number="00502851"` in the very first tool call. `validate_trace`
    rejected all 93 rows and was RIGHT: that number appears nowhere in the
    conversation, so the model would have had to invent it or recall it from
    memory, which rule 1 of the system prompt forbids. The anti-fabrication rule
    (`_arg_is_derived`) states the case exactly — "07524813 cannot be known
    before the registry returns it".

    Resolving first is also the honest shape of the task and the reason these
    axes are worth training: the model has to carry an identifier it has just
    learned from one tool into the argument of the next.
    """
    find = _call_id("search", number)
    read = _call_id(tool, number)
    return _finish({
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _QUESTIONS[label].format(subject=subject)},
            {"role": "assistant",
             "content": (f"I need the registry record for {subject} before I can "
                         f"read any register against it."),
             "tool_calls": [{"id": find, "type": "function", "function": {
                 "name": "companies_house_search",
                 "arguments": json.dumps({"query": subject})}}]},
            {"role": "tool", "tool_call_id": find, "name": "companies_house_search",
             "content": json.dumps({"results": search or []}, ensure_ascii=False)},
            {"role": "assistant",
             "content": f"The registry resolves {subject} to company {number}. {arg_note}",
             "tool_calls": [{"id": read, "type": "function", "function": {
                 "name": tool,
                 "arguments": json.dumps({"company_number": number})}}]},
            {"role": "tool", "tool_call_id": read, "name": tool,
             "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant", "content": answer},
        ],
        "topic": topic, "label": label,
        "subject": subject, "company_number": number,
    })


_QUESTIONS = {
    INSOLVENCY: ("Has {subject} ever been subject to insolvency proceedings? "
                 "I need this for a credit decision."),
    CHARGES: ("Is there any security or prior claim over {subject}'s assets? "
              "We are considering taking them as collateral."),
    OWNERSHIP: ("Who ultimately owns or controls {subject}? I need the natural "
                "persons behind it, not the trading name."),
}


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


def build_insolvency_trace(subject: str, number: str, payload: dict,
                           search: list | None = None) -> dict:
    return _two_hop(
        subject, number, search, "companies_house_insolvency",
        "Now I read the insolvency register against that number.",
        payload, _insolvency_answer(subject, number, payload),
        INSOLVENCY, "financial_standing")


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


def build_charges_trace(subject: str, number: str, payload: dict,
                        search: list | None = None) -> dict:
    return _two_hop(
        subject, number, search, "companies_house_charges",
        "Now I read the charges register against that number.",
        payload, _charges_answer(subject, number, payload),
        CHARGES, "financial_standing")


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
                          exemptions: dict | None = None,
                          search: list | None = None) -> dict:
    if pscs is None:
        # The register did not answer. This MUST stay a distinct payload state
        # from "answered, and empty": collapsing the two is the defect this axis
        # exists to train against, and an earlier draft committed it by coercing
        # None to [].
        payload = {"company_number": number, **_unavailable("Beneficial ownership"),
                   "items": None}
    else:
        payload = {"company_number": number, "checked": True, "outcome": "ok",
                   "psc_count": len(pscs), "items": pscs,
                   "exemptions": exemptions or {"checked": False}}
    return _two_hop(
        subject, number, search, "companies_house_psc",
        "Now I read the persons-with-significant-control register against it.",
        payload, _ownership_answer(subject, number, pscs, exemptions or {}),
        OWNERSHIP, "ownership_control")


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


# R-F4274 — which company's register is made to NOT answer, per axis.
#
# Three per axis, not one. The corpus first shipped with a single refusal row per
# axis, and a subject-disjoint train/eval split then put that row on ONE side —
# leaving the other side unable to teach, or unable to measure, the branch these
# axes exist for. A singleton is not coverage.
#
# Each company refuses on exactly ONE axis and keeps its real rows on the other
# two. That is coherent — one register timed out, the others answered — whereas
# a real and a refused row for the SAME register would be two contradictory
# answers to one question.
REFUSALS: dict[str, tuple[str, ...]] = {
    INSOLVENCY: ("01264385", "08804411", "04241161"),   # Maplin, Revolut, Bet365
    CHARGES: ("01107406", "03772814", "00636095"),      # Iceland, Dyson, River Island
    OWNERSHIP: ("09446231", "01721624", "08130873"),    # Monzo, Specsavers, Gymshark
}

_REFUSAL_SUBJECT = {
    INSOLVENCY: "Insolvency", CHARGES: "Charges",
    OWNERSHIP: "Beneficial ownership",
}


def build_corpus(captures: list[dict]) -> list[dict]:
    """One trace per axis per captured company; designated registers refuse.

    A company listed in REFUSALS for an axis emits the unanswered row for that
    axis INSTEAD of its real one — never both, so the corpus never holds two
    contradictory answers about the same register.
    """
    traces: list[dict] = []
    for record in captures:
        number = str(record.get("company_number") or "").strip()
        subject = str(record.get("subject") or "").strip()
        if not number or not subject or record.get("kind") == "disqualified":
            continue
        name = str((record.get("resolved") or {}).get("title") or subject)
        for axis, builder in ((INSOLVENCY, build_insolvency_trace),
                              (CHARGES, build_charges_trace),
                              (OWNERSHIP, build_ownership_trace)):
            if number in REFUSALS.get(axis, ()):
                payload = _unavailable(_REFUSAL_SUBJECT[axis])
                search = record.get("search") or []
                traces.append(
                    builder(name, number, None, None, search) if axis == OWNERSHIP
                    else builder(name, number, payload, search))
    for record in captures:
        number = str(record.get("company_number") or "").strip()
        subject = str(record.get("subject") or "").strip()
        if not number or not subject or record.get("kind") == "disqualified":
            continue
        resolved_name = str((record.get("resolved") or {}).get("title") or subject)

        insolvency = record.get("insolvency")
        if isinstance(insolvency, dict) and "_capture_error" not in insolvency                 and number not in REFUSALS[INSOLVENCY]:
            traces.append(build_insolvency_trace(resolved_name, number, insolvency,
                                                record.get("search")))

        charges = record.get("charges")
        if isinstance(charges, dict) and "_capture_error" not in charges                 and number not in REFUSALS[CHARGES]:
            traces.append(build_charges_trace(resolved_name, number, charges,
                                             record.get("search")))

        psc = record.get("psc")
        if isinstance(psc, list) and number not in REFUSALS[OWNERSHIP]:
            # The exemption register is what separates a LAWFULLY empty PSC
            # register (a listed company disclosing ownership under market rules)
            # from an unexplained one. Without it every empty register collapses
            # to "UNKNOWN", and the branch that matters most in production is
            # never trained.
            traces.append(build_ownership_trace(
                resolved_name, number, psc, record.get("psc_exemptions"),
                record.get("search")))

    return traces


# ── R-F4274 · the subject-disjoint, branch-stratified split ────────────────

def branch_of(trace: dict) -> str:
    """Which decision state a row exercises. The split is stratified on THIS.

    Splitting on the axis alone is not enough. `tooluse_ownership` holds four
    different answers — named, lawfully exempt, unexplained and unreadable — and
    a split that happened to put every exempt row on one side would leave the
    other side unable to teach it or unable to measure it, while the per-axis row
    counts looked perfectly balanced.
    """
    payload = json.loads([m for m in trace["messages"]
                          if m["role"] == "tool"][-1]["content"])
    final = trace["messages"][-1]["content"]
    if payload.get("checked") is False:
        return "unanswered"
    if trace["label"] == INSOLVENCY:
        return "cases" if (payload.get("cases") or []) else "none"
    if trace["label"] == CHARGES:
        if int(payload.get("outstanding_count") or 0):
            return "outstanding"
        return "historic" if int(payload.get("total_count") or 0) else "none"
    if "ACTIVE exemption" in final:
        return "exempt"
    return "named" if payload.get("items") else "unexplained"


def split_by_subject(traces: list[dict]) -> tuple[list[dict], list[dict]]:
    """(train, eval), disjoint by COMPANY, stratified by (axis, branch).

    Companies, not rows: a company on both sides is the leak the held-out set
    exists to prevent, and every row about one company shares its registry
    payloads. Within each (axis, branch) cell the companies are alternated, so a
    cell holding two or more companies is guaranteed to reach both sides. The
    first assignment a company receives wins — later cells cannot move it — which
    is what keeps the split subject-disjoint rather than cell-disjoint.
    """
    cells: dict[tuple[str, str], list[str]] = {}
    for trace in traces:
        cells.setdefault((trace["label"], branch_of(trace)), []).append(
            trace["company_number"])

    side: dict[str, str] = {}
    # rarest cells first: a two-company cell has no slack, a twenty-company one has
    for cell in sorted(cells, key=lambda c: (len(set(cells[c])), c)):
        for index, number in enumerate(sorted(set(cells[cell]))):
            side.setdefault(number, "train" if index % 2 == 0 else "eval")

    train = [t for t in traces if side.get(t["company_number"]) == "train"]
    held = [t for t in traces if side.get(t["company_number"]) == "eval"]
    return train, held


def stratification_gaps(train: list[dict], held: list[dict]) -> list[str]:
    """Cells present overall but missing from a side. Empty is the contract."""
    def cells(rows):
        return {(t["label"], branch_of(t)) for t in rows}
    everywhere, gaps = cells(train) | cells(held), []
    for cell in sorted(everywhere):
        total = sum(1 for t in train + held
                    if (t["label"], branch_of(t)) == cell)
        if total < 2:
            continue  # a genuine singleton cannot be on both sides
        for name, rows in (("train", train), ("eval", held)):
            if cell not in cells(rows):
                gaps.append(f"{cell[0]}/{cell[1]} is absent from {name} "
                            f"({total} rows exist)")
    overlap = ({t["company_number"] for t in train}
               & {t["company_number"] for t in held})
    if overlap:
        gaps.append(f"companies on BOTH sides: {sorted(overlap)}")
    return gaps


def _rel(path: pathlib.Path) -> str:
    """Display a path repo-relative when it is, verbatim when it is not.

    `relative_to` RAISES on a path that is merely relative (`data/...`), which is
    exactly how the argument arrives from the command line.
    """
    try:
        absolute = path if path.is_absolute() else (pathlib.Path.cwd() / path)
        return absolute.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--capture", type=pathlib.Path, default=CAPTURE)
    parser.add_argument("--out", type=pathlib.Path,
                        default=ROOT / "data/training/aria_tooluse_registry_depth_v1.jsonl")
    parser.add_argument("--strict", action="store_true",
                        help="refuse to write if any trace fails validation")
    parser.add_argument("--split-dir", type=pathlib.Path, default=None,
                        help="also write a subject-disjoint train/eval split here")
    parser.add_argument("--merge-eval", type=pathlib.Path, default=None,
                        help="base eval to concatenate the held-out rows onto")
    args = parser.parse_args(argv)

    captures = [json.loads(line) for line in
                args.capture.read_text(encoding="utf-8").splitlines() if line.strip()]
    traces = build_corpus(captures)

    kept, dropped = [], []
    for trace in traces:
        # validate_trace, not validate_registry_trace: the eval grades through the
        # generic gate too, and it is the generic half that caught these rows
        # handing a register a company number the conversation never derived.
        errors = validate_trace(trace)
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
    print(f"\nwrote {len(kept)} traces to {_rel(args.out)}")
    for label, count in sorted(counts.items()):
        print(f"  {label:<24}{count:>4}")

    if args.split_dir is None:
        return 0

    train, held = split_by_subject([t for t, _ in kept])
    gaps = stratification_gaps(train, held)
    if gaps:
        # A split that strands a decision state is worse than no split: it reads
        # as coverage and measures nothing. Refuse rather than warn.
        print("\nREFUSING to write the split — stratification is not sound:")
        for gap in gaps:
            print(f"  - {gap}")
        return 3

    args.split_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in (("train", train), ("eval", held)):
        path = args.split_dir / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for trace in rows:
                handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
    print(f"\nsplit (subject-disjoint, branch-stratified) -> "
          f"{_rel(args.split_dir)}")
    print(f"  train {len(train):>3} rows / "
          f"{len({t['company_number'] for t in train})} companies")
    print(f"  eval  {len(held):>3} rows / "
          f"{len({t['company_number'] for t in held})} companies")

    if args.merge_eval is not None:
        base = [json.loads(line) for line in
                args.merge_eval.read_text(encoding="utf-8").splitlines() if line.strip()]
        merged = args.split_dir / "eval_360.jsonl"
        with merged.open("w", encoding="utf-8", newline="\n") as handle:
            for trace in base + held:
                handle.write(json.dumps(trace, ensure_ascii=False) + "\n")
        print(f"  360 eval -> {_rel(merged)}: "
              f"{len(base)} + {len(held)} = {len(base) + len(held)} rows, "
              f"{len({t.get('label') for t in base + held})} axes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
