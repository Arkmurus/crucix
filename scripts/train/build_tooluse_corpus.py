"""R-F3366 — build a MULTI-TURN TOOL-USE corpus from REAL tool executions.

WHY. Every SFT row ARIA has trained on is exactly two messages — one user, one
assistant. Measured 2026-07-28: aria_v07_merged 3599/3599 two-message rows,
grounded_v05 1998/1998, dd_depth_v05 599/599, v06 1799/1799. No tool calls, no
intermediate steps, no revision. And 61% of v07 is `dd_framework_knowledge`:
declarative Q&A *about* the pipeline, which teaches the model to DESCRIBE ARIA's
reasoning rather than PERFORM it. The autonomy lives in Python; the model was
never asked to drive it. This builder produces the missing shape:

    user question
      -> assistant emits a structured tool_call
        -> tool returns the REAL payload
      -> assistant reads it and answers, citing only what the tool returned

THE HARD CONSTRAINT — TOOL OUTPUTS MUST BE REAL.
A corpus whose tool results are LLM-imagined teaches the model that
plausible-looking tool output is acceptable. That is fabrication training aimed
straight at the moat, and this repo has already been burned by it once
("fixtures LIED: 7/7 green, 0/20 real"). So every trace REPLAYS a genuine
execution captured from the live screening endpoint. Nothing here invents a
tool result, and `validate_trace` refuses any answer that cites a source the
tool did not actually return.

DATA GOVERNANCE.
Source material is PUBLIC RECORD only — sanctions-listed entities and listed
public companies. Customer DD reports are deliberately NOT used: they are tenant
data in a system with a history of cross-tenant leaks, and baking them into model
weights is an operator decision, not a corpus builder's. A test asserts this file
never reaches into the DD report store.

USAGE
    # capture real tool output from the live service, then build
    python -m scripts.train.build_tooluse_corpus --live --out data/training/aria_tooluse_v1.jsonl
    # rebuild from previously captured real payloads (no network)
    python -m scripts.train.build_tooluse_corpus --from-cache data/training/_tooluse_capture.jsonl --out ...
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable

# ── the tool surface the model must learn to drive ─────────────────────────
# Names mirror the live vocabulary in routes/aria.py::_execute_tool so a trace
# trains the model on the SAME tool names production dispatches.
TOOL_SPECS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "screen",
            "description": (
                "Screen an entity against sanctions, watchlists and country risk. "
                "Returns the matches actually found; an empty match list is only a "
                "clearance when the screen was performed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "entity_name": {"type": "string", "description": "Legal entity name"},
                },
                "required": ["entity_name"],
            },
        },
    },
]
# R-F3367 — the registry hops. Multi-hop is where the REASONING is: one call has
# no decision in it; choosing the next tool from what the last one returned does.
TOOL_SPECS += [
    {
        "type": "function",
        "function": {
            "name": "companies_house_search",
            "description": "Resolve a company name to its official registry entry and company number.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "companies_house_officers",
            "description": (
                "List the officers of a company. Requires a company_number, which "
                "must come from a prior registry lookup — never guessed."
            ),
            "parameters": {
                "type": "object",
                "properties": {"company_number": {"type": "string"}},
                "required": ["company_number"],
            },
        },
    },
]

# R-F3374 — retrieval, for interpreting news rather than establishing a fact.
TOOL_SPECS += [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search current reporting. Results may include ARIA's own memory "
                "(memory:// URLs), which is NOT independent corroboration."
            ),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
]

TOOL_NAMES = {s["function"]["name"] for s in TOOL_SPECS}

# An argument shorter than this cannot be treated as "derived": a 1-2 character
# string substring-matches almost any payload, which would make the derivation
# guard pass on anything (the blind-guard failure mode).
_MIN_DERIVABLE_LEN = 3

SYSTEM_PROMPT = (
    "You are ARIA. You answer from EVIDENCE you have gathered with tools, never "
    "from memory alone.\n"
    "Available tools: " + ", ".join(sorted(TOOL_NAMES)) + ".\n"
    "Rules:\n"
    "1. Call the tool that can establish the fact. Do not answer a screening "
    "question without screening.\n"
    "2. Cite every claim inline as [from <source>], using ONLY sources present in "
    "the tool output.\n"
    "3. If the tool did not run, or its source was unavailable, say so plainly. "
    "An unperformed check is NOT a clean result."
)

# `[from <source>]` — the established citation contract (see the memory note that
# this suffix on Finding.source is load-bearing; consumers parse it).
_CITE_RE = re.compile(r"\[from ([^\]]+)\]")

# R-F3366 — match a CLAIM, not a word. The honest unperformed answer necessarily
# contains "clean"/"clear" while DENYING them ("this is NOT a clean result"), so a
# bare substring test flags the very text it exists to protect. These patterns
# assert cleanliness affirmatively; `_DECLARES_NOT_SCREENED` is what licenses the
# words appearing at all.
_CLEAN_CLAIM_RE = re.compile(
    r"\b(is|are|was|were)\s+(now\s+)?(clear|clean|unsanctioned)\b"
    r"|\breturned no (sanctions )?matches\b"
    r"|\bno (sanctions )?(matches|hits) (were )?found\b"
    r"|\bnot sanctioned\b",
    re.I,
)
_DECLARES_NOT_SCREENED_RE = re.compile(
    r"\b(could not screen|did not run|was not screened|not screened|"
    r"screen did not|not a clean result|must be repeated|unverified)\b",
    re.I,
)
_HIT_CLAIM_RE = re.compile(
    r"\b(is|are|was|were)\s+(a\s+)?(sanctions\s+)?(match|blocked|sanctioned|listed)\b"
    r"|\bmust be treated as blocked\b|\bmatch found\b"
    # R-F3369 — a hit can be stated as an instruction ("Treat it as BLOCKED") or by
    # naming the matched record ("matches 'SBERBANK OF RUSSIA'"). Pinning one
    # phrasing would make the guard cry wolf on a correct answer. Both additions
    # are absent from the refuse-the-accusation text, which legitimately contains
    # the word "sanctioned" while asserting no hit.
    r"|\btreat(?:ed)?\s+(?:it|them|this|the entity)\s+as\s+blocked\b"
    r"|\bmatches\s+['\"]",
    re.I,
)


# ── payload readers (tolerant: real payloads vary) ─────────────────────────

def _call_id(*parts: object) -> str:
    """A tool_call id the chat template will accept: EXACTLY 9 alphanumerics.

    R-F3392 — Mistral's template raises "Tool call IDs should be alphanumeric
    strings with length 9!" on anything else, and the failure lands AFTER the
    paid base-model load (sft_train.py records that exact class in R-F1470). The
    ids this builder used (`call_1_companieshouses`) made every trace in the
    corpus untrainable while looking perfectly valid on inspection.

    Deterministic so the corpus stays reproducible; hashed over the caller's
    parts so ids are unique within a trace.
    """
    import hashlib
    seed = "|".join(str(p) for p in parts)
    return hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False).hexdigest()[:9]


def _sanctions_block(payload: dict) -> dict:
    return (payload or {}).get("sanctions") or {}


def _matches(payload: dict) -> list[dict]:
    return [m for m in (_sanctions_block(payload).get("matches") or []) if isinstance(m, dict)]


def _sources_in(payload: dict) -> set[str]:
    """Every source label the tool ACTUALLY returned. The citation whitelist."""
    out: set[str] = set()
    for m in _matches(payload):
        for key in ("list", "dataset", "source"):
            v = m.get(key)
            if isinstance(v, str) and v.strip():
                out.add(v.strip())
    return out


def _was_performed(payload: dict) -> bool:
    """R-F1696 semantics: an empty match list is a clearance ONLY if the screen ran."""
    s = _sanctions_block(payload)
    if s.get("error") or s.get("source_unavailable"):
        return False
    if s.get("screened") is False:
        return False
    status = str((payload or {}).get("status") or "").upper()
    return status not in ("ERROR", "UNKNOWN", "")


# ── trace assembly ─────────────────────────────────────────────────────────

def _answer_for(subject: str, payload: dict) -> str:
    """The target answer — grounded, and honest about what was not established."""
    matches = _matches(payload)
    if not _was_performed(payload):
        reason = _sanctions_block(payload).get("error") or "source unavailable"
        return (
            f"I could not screen {subject}: the sanctions source did not respond "
            f"({reason}). This is NOT a clean result — the check did not run and "
            f"must be repeated before {subject} is treated as clear."
        )
    if matches:
        top = max(matches, key=lambda m: float(m.get("score") or 0))
        src = top.get("list") or top.get("dataset") or top.get("source") or "sanctions"
        return (
            f"{subject} is a sanctions match and must be treated as BLOCKED. The "
            f"screen returned '{top.get('name')}' at score "
            f"{float(top.get('score') or 0):.2f} [from {src}]. Do not proceed "
            f"without legal sign-off."
        )
    checked = sorted(_sources_in(payload)) or ["the configured sanctions sources"]
    return (
        f"{subject} returned no sanctions matches. The screen was performed "
        f"against {', '.join(checked)} and found nothing, so this is a clear "
        f"result as at the time of screening — not a permanent clearance."
    )


def build_trace(subject: str, payload: dict) -> dict:
    """Assemble one multi-turn tool-use trace from a REAL tool payload."""
    call_id = _call_id("screen", subject)
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Is {subject} subject to sanctions? I need this for a compliance file."},
            {
                "role": "assistant",
                "content": (
                    f"I need to screen {subject} before answering — I will not "
                    f"assert a sanctions position from memory."
                ),
                "tool_calls": [{
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": "screen",
                        "arguments": json.dumps({"entity_name": subject}, ensure_ascii=False),
                    },
                }],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "name": "screen",
                # the REAL payload, not a paraphrase — the model must learn to read it
                "content": json.dumps(payload, ensure_ascii=False),
            },
            {"role": "assistant", "content": _answer_for(subject, payload)},
        ],
        "topic": "sanctions_screening",
        "label": "tooluse_trace",
        "grounded": True,
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


# ── the anti-fabrication gate ──────────────────────────────────────────────

# R-F3369 — agreement detection for challenge traces.
#
# The hard part is negation: "I can confirm" and "I cannot confirm" differ by two
# characters and mean opposite things, and a naive substring test reads BOTH as
# agreement — which would score a correct refusal as capitulation and train the
# exact behaviour this corpus exists to prevent. The negative lookbehind is the
# whole trick, and the parametrised negation test is what keeps it honest.
_NEG = r"(?<!\bnot )(?<!\bcannot )(?<!\bcan't )(?<!\bnever )(?<!\bwon't )(?<!\bunable to )"
_AGREE_RE = re.compile(
    _NEG + r"\b(you(?:'re| are) right"
    r"|that(?:'s| is) correct"
    r"|i can confirm"
    r"|confirmed"
    r"|as you say"
    r"|you are correct)\b",
    re.I,
)
# "You are not right" must not read as agreement; the lookbehind sits on the verb,
# so an interposed negation is caught separately.
_NEGATED_AGREE_RE = re.compile(
    r"\byou (?:are|'re) not (?:right|correct)\b|\bthat is not correct\b", re.I
)


def _agrees_with_premise(text: str) -> bool:
    """True when the answer AGREES with what the user asserted.

    Used to score challenge traces in both directions: agreeing with a premise the
    evidence refutes is capitulation; refusing to agree with one the evidence
    supports is manufactured doubt. Both are failures.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    if _NEGATED_AGREE_RE.search(text):
        return False
    return bool(_AGREE_RE.search(text))


def _norm_for_derivation(s: str) -> str:
    """Case/padding/punctuation-insensitive form for derivation matching."""
    return re.sub(r"[^a-z0-9]+", "", (s or "").lower())


def _arg_is_derived(value: str, prior_blobs: list[str], user_text: str) -> bool:
    """True when `value` can be traced to prior tool output or the user's question.

    R-F3367 — this is the anti-entity-fabrication rule. `07524813` cannot be known
    before the registry returns it; an officer name cannot be known before the
    officer list does. A model that invents either then screens a company or a
    person who does not exist and reports on them with full confidence — worse
    than a wrong answer, because it is a confident answer about nothing.
    """
    v = _norm_for_derivation(value)
    if len(v) < _MIN_DERIVABLE_LEN:
        return False
    if v in _norm_for_derivation(user_text):
        return True
    return any(v in b for b in prior_blobs)


def validate_trace(trace: Any) -> list[str]:
    """Return a list of reasons this trace must NOT be trained on. Empty == good.

    This is the point of the whole file. A corpus builder that cannot prove its
    targets are grounded is just a fabrication generator with extra steps.
    """
    errs: list[str] = []
    if not isinstance(trace, dict):
        return ["trace is not an object"]
    msgs = trace.get("messages")
    if not isinstance(msgs, list) or not msgs:
        return ["trace has no messages"]

    roles = [m.get("role") for m in msgs if isinstance(m, dict)]
    if len(msgs) != len(roles):
        errs.append("non-object message in trace")
    if "tool" not in roles:
        errs.append("not a tool-use trace: no tool turn (a 2-message row is exactly what this corpus replaces)")
    if roles and roles[-1] != "assistant":
        errs.append("trace does not end with the assistant's answer")

    # every tool turn must answer a call that exists
    call_ids = {
        c.get("id")
        for m in msgs if isinstance(m, dict)
        for c in (m.get("tool_calls") or [])
        if isinstance(c, dict)
    }
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "tool":
            if m.get("tool_call_id") not in call_ids:
                errs.append(f"tool result {m.get('tool_call_id')!r} answers no tool_call")
            if m.get("name") not in TOOL_NAMES:
                errs.append(f"tool turn names an unknown tool: {m.get('name')!r}")
    # R-F3392 — the chat template requires EXACTLY 9 alphanumerics. This
    # constraint lives in the CONSUMER, which is why an internally-consistent
    # trace still could not be trained. Enforced here so an untrainable corpus
    # cannot be written.
    for _cid in call_ids:
        if not (isinstance(_cid, str) and len(_cid) == 9 and _cid.isalnum()):
            errs.append(
                f"tool_call id {_cid!r} is not 9 alphanumerics — the chat "
                f"template will refuse to render this trace"
            )

    # ---- R-F3367 derivation: each hop's arguments must be traceable to prior
    # output (or, for the first hop, to the user's own question).
    user_text = " ".join(
        m.get("content") or "" for m in msgs
        if isinstance(m, dict) and m.get("role") == "user"
    )
    prior_blobs: list[str] = []
    for m in msgs:
        if not isinstance(m, dict):
            continue
        for c in (m.get("tool_calls") or []):
            if not isinstance(c, dict):
                continue
            fn = (c.get("function") or {})
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except (ValueError, TypeError):
                errs.append(f"tool call {fn.get('name')!r} has unparseable arguments")
                continue
            for k, v in (args.items() if isinstance(args, dict) else []):
                if not isinstance(v, str) or not v.strip():
                    continue
                # R-F3374 — a web_search QUERY is legitimately COMPOSED ("<entity>
                # news"); that is reasoning, not a fabricated entity. The narrow
                # exemption is safe because the fabrication risk in a news trace is
                # in the ANSWER's citations, which are enforced separately against
                # the outlets actually returned.
                if fn.get("name") == "web_search" and k == "query":
                    continue
                if not _arg_is_derived(v, prior_blobs, user_text):
                    errs.append(
                        f"tool call {fn.get('name')!r} argument {k}={v.strip()!r} is not "
                        f"derived from any prior tool output or the question — a "
                        f"fabricated entity"
                    )
        if m.get("role") == "tool":
            prior_blobs.append(_norm_for_derivation(m.get("content") or ""))

    # ---- R-F3372 selection: the company carried forward must be the one the
    # resolver picks. Derivation (R-F3367) only proves the number was PRESENT in a
    # payload — `results[0]` always is. That is exactly how a chain can run due
    # diligence on a dissolved shell while every other guard stays green.
    _search_results: list[dict] = []
    _subject = (trace.get("subject") or "") if isinstance(trace, dict) else ""
    for m in msgs:
        if not isinstance(m, dict):
            continue
        if m.get("role") == "tool" and m.get("name") == "companies_house_search":
            try:
                _search_results = (json.loads(m.get("content") or "{}").get("results") or [])
            except (ValueError, TypeError):
                _search_results = []
        for c in (m.get("tool_calls") or []):
            if not isinstance(c, dict):
                continue
            fn = c.get("function") or {}
            if fn.get("name") != "companies_house_officers" or not _search_results:
                continue
            try:
                used = str((json.loads(fn.get("arguments") or "{}") or {}).get("company_number") or "").strip()
            except (ValueError, TypeError):
                continue
            chosen, reason, ambiguous = resolve_company(_subject, _search_results)
            if chosen is None:
                errs.append(
                    f"carried company_number {used!r} forward when the subject could not "
                    f"be resolved ({reason}) — due diligence on a guess"
                )
            elif ambiguous:
                errs.append(f"carried {used!r} forward while the subject was ambiguous ({reason})")
            elif used and used != str(chosen.get("company_number")):
                errs.append(
                    f"selected company_number {used!r}, but {_subject or 'the subject'} resolves to "
                    f"{chosen.get('company_number')!r} ({chosen.get('title')}). "
                    f"A number present in the payload is not the same as the RIGHT one."
                )

    # ---- citation grounding: the final answer may cite ONLY what a tool returned
    payloads: list[dict] = []
    for m in msgs:
        if isinstance(m, dict) and m.get("role") == "tool":
            try:
                payloads.append(json.loads(m.get("content") or "{}"))
            except (ValueError, TypeError):
                errs.append("tool content is not valid JSON — the model cannot learn to read it")
    allowed: set[str] = set()
    for p in payloads:
        allowed |= _sources_in(p)
        # R-F3374 — a retrieval payload's citable sources are the OUTLETS it
        # returned, not sanctions list names. Without this the generic rule
        # rejects a correctly-cited article because the payload has no
        # `sanctions.matches`. The news-specific rule below still enforces
        # independence and corroboration on top of this.
        allowed |= _independent_sources(p)

    final = ""
    for m in reversed(msgs):
        if isinstance(m, dict) and m.get("role") == "assistant":
            final = m.get("content") or ""
            break
    for cited in _CITE_RE.findall(final):
        if cited.strip() not in allowed:
            errs.append(
                f"final answer cites {cited.strip()!r}, which no tool result contains "
                f"(available: {sorted(allowed) or 'none'})"
            )

    # ---- verdict must match the evidence
    # R-F3367 — verdict rules apply to the SCREEN result. In a multi-hop chain the
    # last payload may be a registry lookup, which carries no sanctions verdict;
    # judging it as an unperformed screen would demand a "not screened" disclaimer
    # on a trace that never claimed to screen anything.
    screen_payloads = [p for p in payloads if isinstance(p, dict) and p.get("sanctions") is not None]
    if screen_payloads:
        p = screen_payloads[-1]
        performed, matched = _was_performed(p), bool(_matches(p))
        claims_clean = bool(_CLEAN_CLAIM_RE.search(final))
        declares_unscreened = bool(_DECLARES_NOT_SCREENED_RE.search(final))
        claims_hit = bool(_HIT_CLAIM_RE.search(final))

        if not performed:
            # The screen did not run. The answer must SAY so, and must not assert
            # a clean position — this is the never-false-clean rule, enforced
            # before the data ever reaches a training run.
            if not declares_unscreened:
                errs.append("screen did not run but the answer does not say so")
            if claims_clean and not declares_unscreened:
                errs.append("unperformed screen reported as clean — never-false-clean breach")
        else:
            if not matched and claims_hit:
                errs.append("clean screen reported as a sanctions hit")
            if matched and claims_clean:
                errs.append("sanctions match reported as clear")
            if matched and not claims_hit:
                errs.append("sanctions match not reported as a hit")

    # ---- R-F3374 news traces: outlet grounding + the independence gate
    if isinstance(trace, dict) and trace.get("label") == "tooluse_news_impact":
        search_payloads = [p for p in payloads if isinstance(p, dict) and "results" in p]
        indep: set[str] = set()
        for p in search_payloads:
            indep |= _independent_sources(p)
        for cited in _CITE_RE.findall(final):
            if cited.strip() not in indep:
                errs.append(
                    f"analysis cites outlet {cited.strip()!r}, which the search did not "
                    f"return as an independent source (available: {sorted(indep) or 'none'})"
                )
        if (_CORROB_CLAIM_RE.search(final) and not _CORROB_DENIAL_RE.search(final)
                and len(indep) < 2):
            errs.append(
                f"claimed corroboration from {len(indep)} independent source(s) — "
                f"ARIA's own memory is not outside support, and one source is not two"
            )

    # ---- R-F3369 challenge traces: agreement must follow the evidence
    if isinstance(trace, dict) and trace.get("label") == "tooluse_challenge":
        premise = trace.get("premise")
        if premise not in ("clean", "sanctioned"):
            errs.append(f"challenge trace has no valid premise: {premise!r}")
        elif screen_payloads:
            p = screen_payloads[-1]
            agrees = _agrees_with_premise(final)
            if not _was_performed(p):
                # An unperformed screen settles nothing. Agreeing with EITHER side
                # on the strength of a check that never ran is the worst outcome.
                if agrees:
                    errs.append(
                        "agreed with the user on the strength of a screen that did not run"
                    )
            else:
                supported = (
                    (premise == "clean" and not _matches(p))
                    or (premise == "sanctioned" and bool(_matches(p)))
                )
                if supported and not agrees:
                    errs.append(
                        "contradicted a user the evidence agrees with — manufactured "
                        "doubt is as dishonest as capitulation"
                    )
                if not supported and agrees:
                    errs.append(
                        f"agreed with the user's {premise!r} premise, which its own "
                        f"screen contradicts — capitulation"
                    )
    return errs


# ── corpus assembly ────────────────────────────────────────────────────────

# R-F3374 — schemes that are ARIA'S OWN memory, never outside corroboration.
# The live search really does return these mixed in with web results, and
# dd_orchestrator keeps the same list (`_ADVERSE_SELF_SOURCE_MARKERS`) because
# counting self-reference as support is a defect this repo has already had.
_SELF_SOURCE_MARKERS = ("memory://", "rag://", "aria://", "brain_hook:")


def _independent_sources(payload: Any) -> set[str]:
    """Outlet domains from a search payload, EXCLUDING ARIA's own memory.

    Corroboration means two independent OUTSIDE sources. A claim supported only by
    what ARIA already believed is single-source, however many rows it occupies.
    """
    out: set[str] = set()
    for r in ((payload or {}).get("results") or []) if isinstance(payload, dict) else []:
        if not isinstance(r, dict):
            continue
        url = str(r.get("url") or "").strip()
        if not url or any(url.lower().startswith(m) for m in _SELF_SOURCE_MARKERS):
            continue
        m = re.match(r"https?://([^/]+)", url, re.I)
        if not m:
            continue
        out.add(re.sub(r"^www\.", "", m.group(1).lower()))
    return out


# A CLAIM of corroboration, not the word. The honest single-source answer says
# "the other hits are my own memory and do NOT corroborate it" — a bare
# /corroborat\w*/ test flags exactly the text it exists to protect. Same trap as
# the clean/hit detectors; third time this pattern has bitten in this module.
_CORROB_CLAIM_RE = re.compile(
    r"(?<!\bnot )(?<!\bdo not )(?<!\bdoes not )(?<!\bcannot )(?<!\bno )"
    r"(\bcorroborated\b|\bcorroborates\b|\bmultiple independent\b)",
    re.I,
)
_CORROB_DENIAL_RE = re.compile(
    r"\b(do(?:es)? not corroborate|not corroborated|no corroboration|"
    r"does not amount to corroboration)\b", re.I
)


def build_news_impact_trace(entity: str, search_payload: dict) -> dict:
    """Teach INTERPRETATION: what real news means for exposure, and what it does not.

    The verifier deliberately does NOT grade the analysis — "what this means" is a
    judgement no validator can score. It enforces the two things that are checkable
    and that are exactly where fabrication enters: only cite outlets the search
    returned, and never call one independent source (or ARIA's own memory)
    corroboration.
    """
    sources = sorted(_independent_sources(search_payload))
    results = (search_payload or {}).get("results") or []
    call_id = _call_id("news", entity)

    if not results:
        final = (
            f"The search returned no results for {entity}, so I have nothing to "
            f"analyse. That is an absence of retrieved evidence, not evidence that "
            f"nothing is happening — I would widen the query or try a different "
            f"source before concluding anything."
        )
    elif not sources:
        final = (
            f"Everything the search returned for {entity} is from my own memory, "
            f"not an outside source. I will not build an assessment on that: it "
            f"would be my own prior belief reflected back as corroboration. There "
            f"is no independent reporting here to interpret."
        )
    else:
        headline = next(
            (r.get("title") for r in results
             if isinstance(r, dict) and not any(
                 str(r.get("url") or "").lower().startswith(m) for m in _SELF_SOURCE_MARKERS)),
            "",
        )
        if len(sources) == 1:
            final = (
                f"One independent source reports on {entity}: \"{headline}\" "
                f"[from {sources[0]}]. Treat this as SINGLE-SOURCE — it is enough to "
                f"raise a question, not to move a risk rating. The other hits are my "
                f"own memory and do not corroborate it. Exposure implication: if the "
                f"report holds, it bears on {entity}'s revenue concentration, which I "
                f"would verify against the filed accounts before acting."
            )
        else:
            cites = " ".join(f"[from {s}]" for s in sources[:3])
            final = (
                f"Reporting on {entity} is corroborated across {len(sources)} "
                f"independent sources {cites}: \"{headline}\". That is enough to act "
                f"on as a working assumption. Exposure implication: it bears on "
                f"{entity}'s contract pipeline and counterparty concentration — I "
                f"would still confirm against primary filings before it changes a "
                f"formal rating."
            )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"What's being reported about {entity} at the moment, and does it "
                f"change our exposure?")},
            {
                "role": "assistant",
                "content": (
                    f"I need current reporting before I say anything about exposure — "
                    f"I will not characterise {entity} from memory."
                ),
                "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "web_search",
                                 "arguments": json.dumps({"query": f"{entity} news"}, ensure_ascii=False)},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "name": "web_search",
             "content": json.dumps(search_payload, ensure_ascii=False)},
            {"role": "assistant", "content": final},
        ],
        "topic": "news_impact_analysis",
        "label": "tooluse_news_impact",
        "grounded": True,
        "subject": entity,
        "independent_sources": len(sources),
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


_DEAD_STATUSES = ("dissolved", "closed", "closed-on", "converted-closed", "removed", "liquidation")


def resolve_company(subject: str, results: Any) -> tuple[dict | None, str, bool]:
    """Choose the company a subject refers to. Returns (chosen, reason, ambiguous).

    R-F3372 — `capture_multihop` used to take `results[0]`, trusting the registry's
    relevance ranking. Against the real register that is dangerous for the short
    names operators actually type: "Chemring" ranks the DISSOLVED CHEMRING LIMITED
    first and the live CHEMRING GROUP PLC fourth; "Babcock" ranks a dissolved
    BABCOCK LTD first; "QinetiQ" puts an unrelated PAWSTOPURR LTD second. Running
    DD against a dissolved shell makes every downstream hop — officers, PSC,
    sanctions — about the wrong company, asserted with full confidence.

    The rule is deliberately NOT exact-title matching: `Meggitt plc` legitimately
    resolves to MEGGITT LIMITED and `Ultra Electronics Holdings plc` to
    ULTRA ELECTRONICS HOLDINGS LIMITED after going private. Equality would reject
    correct answers. So: compare NAME CORES (suffixes stripped), require a live
    status, and when two live candidates are equally good, say it is ambiguous
    rather than pick one.
    """
    if not isinstance(results, list) or not results:
        return None, "the registry returned no candidates", False
    core = _norm_subject(subject)
    if not core:
        return None, "no usable subject name", False

    # R-F3372 tier 1 — an EXACT full-title match wins outright. Found by running
    # the real capture: "Babcock International Group plc" was scored ambiguous
    # against BABCOCK INTERNATIONAL LIMITED because suffix-stripping collapses
    # "Group plc" and "Limited" to the same core — yet the subject matches one
    # title exactly. Tier 2 (core matching) remains the fallback that lets
    # "Meggitt plc" still resolve to MEGGITT LIMITED after its re-registration.
    full = _norm_for_derivation(subject)
    tier1 = [
        r for r in results
        if isinstance(r, dict) and isinstance(r.get("title"), str)
        and _norm_for_derivation(r["title"]) == full
        and not any(d in str(r.get("company_status") or "").lower() for d in _DEAD_STATUSES)
    ]
    if len(tier1) == 1:
        c = tier1[0]
        return c, (f"{c.get('title')} ({c.get('company_number')}) is an exact, active "
                   f"name match for {subject}"), False

    exact_live: list[dict] = []
    exact_dead: list[dict] = []
    for r in results:
        if not isinstance(r, dict):
            continue
        title = r.get("title")
        if not isinstance(title, str) or not title.strip():
            continue
        if _norm_subject(title) != core:
            continue                     # not the same entity, whatever its rank
        status = str(r.get("company_status") or "").lower()
        (exact_dead if any(d in status for d in _DEAD_STATUSES) else exact_live).append(r)

    if not exact_live:
        if exact_dead:
            return None, (
                f"the only name match is {exact_dead[0].get('title')} "
                f"({exact_dead[0].get('company_status')}) — a dissolved entity is not "
                f"a safe subject for due diligence"
            ), False
        return None, f"no candidate's name resolves to {subject!r}", False

    if len(exact_live) > 1:
        names = ", ".join(str(c.get("title")) for c in exact_live[:3])
        return exact_live[0], f"more than one live company matches: {names}", True

    chosen = exact_live[0]
    dropped = ""
    if exact_dead:
        dropped = (f"; rejected {exact_dead[0].get('title')} as "
                   f"{exact_dead[0].get('company_status')}")
    return chosen, (
        f"{chosen.get('title')} ({chosen.get('company_number')}) is the active "
        f"company whose name resolves to {subject}{dropped}"
    ), False


def build_resolution_trace(subject: str, search_payload: dict) -> dict:
    """A trace teaching ENTITY RESOLUTION: verify which company this is, and ask
    when the register does not answer it confidently."""
    results = (search_payload or {}).get("results") or []
    chosen, reason, ambiguous = resolve_company(subject, results)
    call_id = _call_id("resolution", subject)

    if chosen is not None and not ambiguous:
        final = (
            f"{reason}. I will proceed on company number "
            f"{chosen.get('company_number')}."
        )
    else:
        listed = "; ".join(
            f"{r.get('title')} ({r.get('company_status')}, {r.get('company_number')})"
            for r in results[:4] if isinstance(r, dict)
        )
        final = (
            f"I cannot safely say which company you mean by {subject}. {reason}. "
            f"The register returned: {listed}. Which of these is the subject? I am "
            f"not going to run due diligence on a guess — every later finding would "
            f"inherit the error."
        )
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Run due diligence on {subject}."},
            {
                "role": "assistant",
                "content": f"First I have to establish which registered company {subject} is.",
                "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "companies_house_search",
                                 "arguments": json.dumps({"query": subject}, ensure_ascii=False)},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "name": "companies_house_search",
             "content": json.dumps(search_payload, ensure_ascii=False)},
            {"role": "assistant", "content": final},
        ],
        "topic": "entity_resolution",
        "label": "tooluse_resolution",
        "grounded": True,
        "subject": subject,
        "resolved": None if chosen is None else chosen.get("company_number"),
        "ambiguous": ambiguous,
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


_HOP_REASONING = {
    "companies_house_search": "First I need the official registry entry for {subject} — I will not assume its identity.",
    "companies_house_officers": "The registry resolved it to company {company_number}. Now I need who controls it.",
    "screen": "I have a named individual from the registry. I must screen them before saying anything about risk.",
}


_CHALLENGE_PROMPTS = {
    "clean": (
        "{subject} is fine for this deal — I've worked with them before. Just "
        "confirm they're not sanctioned so I can sign the file off today."
    ),
    "sanctioned": (
        "I'm told {subject} is sanctioned. Confirm that for me so I can put the "
        "relationship on hold and notify the client."
    ),
}


def build_challenge_trace(subject: str, payload: dict, premise: str) -> dict:
    """A trace where the user asserts a position and ARIA must follow the EVIDENCE.

    `premise` is what the USER claims: "clean" or "sanctioned". The target answer
    agrees only when the real screen supports the claim, refuses when it does not,
    and refuses to adjudicate at all when the screen did not run.

    Both directions are trained deliberately. Agreeing with a false "it's clean"
    is never-false-clean failing at the conversational layer, where every
    engine-side guard is bypassed because nobody asked the engine. Agreeing with a
    false "it's sanctioned" is a defamation problem — and this repo's own record is
    that one false positive destroys the USP.
    """
    if premise not in _CHALLENGE_PROMPTS:
        raise ValueError(f"premise must be one of {sorted(_CHALLENGE_PROMPTS)}, got {premise!r}")
    call_id = _call_id("challenge", subject)
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _CHALLENGE_PROMPTS[premise].format(subject=subject)},
            {
                "role": "assistant",
                "content": (
                    f"I can't take that on trust — a position on {subject} has to "
                    f"come from a screen, not from either of our recollections."
                ),
                "tool_calls": [{
                    "id": call_id, "type": "function",
                    "function": {"name": "screen",
                                 "arguments": json.dumps({"entity_name": subject}, ensure_ascii=False)},
                }],
            },
            {"role": "tool", "tool_call_id": call_id, "name": "screen",
             "content": json.dumps(payload, ensure_ascii=False)},
            {"role": "assistant", "content": _challenge_answer(subject, payload, premise)},
        ],
        "topic": "adversarial_user_premise",
        "label": "tooluse_challenge",
        "premise": premise,
        "grounded": True,
        "subject": subject,
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


def _challenge_answer(subject: str, payload: dict, premise: str) -> str:
    """Target answer: agree with the user only where the evidence does."""
    if not _was_performed(payload):
        return (
            f"I can't settle this either way: the screen for {subject} did not run "
            f"(the sanctions source was unavailable), so I have no basis to agree "
            f"or disagree with you. This is NOT a clean result and it is not a "
            f"finding against them — it must be re-screened before either of us "
            f"acts on it."
        )
    matches = _matches(payload)
    if matches:
        top = max(matches, key=lambda m: float(m.get("score") or 0))
        src = top.get("list") or top.get("dataset") or "sanctions"
        if premise == "sanctioned":
            return (
                f"You are right, and the screen supports you: {subject} matches "
                f"'{top.get('name')}' at {float(top.get('score') or 0):.2f} "
                f"[from {src}]. Treat it as BLOCKED and hold the relationship."
            )
        return (
            f"I can't confirm that — the screen contradicts it. {subject} matches "
            f"'{top.get('name')}' at {float(top.get('score') or 0):.2f} [from {src}], "
            f"so it must be treated as BLOCKED. I understand the file needs closing, "
            f"but signing this off as clear would be wrong on the evidence."
        )
    if premise == "sanctioned":
        return (
            f"I can't confirm that. The screen returned no matches for {subject}, "
            f"so there is nothing on the evidence to support putting the "
            f"relationship on hold or notifying the client — and recording an "
            f"entity as sanctioned when the screen does not show it is a serious "
            f"claim to make wrongly. If you have a source I have not screened, "
            f"give it to me and I will check it."
        )
    return (
        f"You are right on this one: the screen returned no matches for {subject}. "
        f"That is a clear result as at today against the sources screened — not a "
        f"permanent clearance, so re-screen before the next milestone."
    )


def build_multihop_trace(subject: str, hops: list[tuple[str, dict, dict]]) -> dict:
    """Assemble a MULTI-HOP trace from REAL payloads.

    `hops` is an ordered list of (tool_name, arguments, real_payload). Each hop
    gets its own reasoning turn, its own tool_call and its own tool result, so
    the model sees the decision — choosing the next tool from the last result —
    not just the lookups.
    """
    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"I'm running due diligence on {subject}. Confirm the entity, find who "
            f"controls it, and tell me whether any of them are sanctioned."
        )},
    ]
    for i, (tool, args, payload) in enumerate(hops):
        call_id = _call_id("hop", subject, i, tool)
        reason = _HOP_REASONING.get(tool, "Next I need to establish this with a tool.")
        try:
            reason = reason.format(subject=subject, **{k: str(v) for k, v in args.items()})
        except (KeyError, IndexError):
            pass
        messages.append({
            "role": "assistant",
            "content": reason,
            "tool_calls": [{
                "id": call_id, "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args, ensure_ascii=False)},
            }],
        })
        messages.append({
            "role": "tool", "tool_call_id": call_id, "name": tool,
            "content": json.dumps(payload, ensure_ascii=False),
        })
    messages.append({"role": "assistant", "content": _multihop_answer(subject, hops)})
    return {
        "messages": messages,
        "topic": "dd_entity_to_officer_screening",
        "label": "tooluse_multihop",
        "grounded": True,
        "hops": len(hops),
        "subject": subject,
        "tools": TOOL_SPECS,
        "source": "replayed_real_tool_execution",
    }


def _multihop_answer(subject: str, hops: list[tuple[str, dict, dict]]) -> str:
    """The target answer — every claim traceable to a hop that actually ran."""
    parts: list[str] = []
    number = title = None
    officers: list[dict] = []
    screen: dict | None = None
    for tool, _args, payload in hops:
        if tool == "companies_house_search":
            res = (payload or {}).get("results") or []
            if res:
                number, title = res[0].get("company_number"), res[0].get("title")
        elif tool == "companies_house_officers":
            officers = (payload or {}).get("officers") or []
        elif tool == "screen":
            screen = payload
    if title and number:
        parts.append(f"The registry resolves {subject} to {title}, company number {number}.")
    if officers:
        parts.append(
            f"It has {len(officers)} officer(s) on record; I screened "
            f"{officers[0].get('name')}."
        )
    if screen is not None:
        if not _was_performed(screen):
            parts.append(
                "That screen did not run — the sanctions source was unavailable, so "
                "this is NOT a clean result and must be repeated."
            )
        elif _matches(screen):
            top = max(_matches(screen), key=lambda m: float(m.get("score") or 0))
            src = top.get("list") or top.get("dataset") or "sanctions"
            parts.append(
                f"That officer is a sanctions match — '{top.get('name')}' "
                f"[from {src}] — so the entity must be treated as BLOCKED."
            )
        else:
            parts.append(
                "That officer returned no sanctions matches, so on the evidence "
                "gathered there is no sanctions block as at today. Only the officer "
                "screened above was checked — the remaining officers are unscreened."
            )
    return " ".join(parts)


def write_multihop_corpus(
    traces: Iterable[dict],
    out: Path,
    eval_subjects: Iterable[str] | None = None,
    allow_unchecked: bool = False,
) -> int:
    """Write validated multi-hop traces, dropping contaminated or invalid ones."""
    if eval_subjects is None and not allow_unchecked:
        raise ValueError("refusing to build without an eval blocklist (see write_corpus)")
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    for t in traces:
        subject = t.get("subject") or ""
        if eval_subjects is not None and is_eval_contaminated(subject, eval_subjects):
            print(f"  DROP {subject}: present in the frozen eval set (contamination)", file=sys.stderr)
            continue
        errs = validate_trace(t)
        if errs:
            print(f"  DROP {subject}: {errs[0]}", file=sys.stderr)
            continue
        kept.append(t)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + ("\n" if kept else ""),
        encoding="utf-8",
    )
    return len(kept)


_CORP_SUFFIXES = (
    " plc", " ltd", " limited", " se", " ag", " nv", " sa", " inc", " corp",
    " corporation", " holdings", " group", " company", " co",
)


def _norm_subject(s: str) -> str:
    """Normalise an entity name for contamination matching."""
    t = re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower())
    t = re.sub(r"\s+", " ", t).strip()
    changed = True
    while changed:                       # "BAE Systems Group plc" -> "bae systems"
        changed = False
        for suf in _CORP_SUFFIXES:
            if t.endswith(suf):
                t, changed = t[: -len(suf)].strip(), True
    return t


def is_eval_contaminated(subject: str, eval_subjects: Iterable[str]) -> bool:
    """True when `subject` appears in the frozen eval set.

    Training on an entity the 500-Q benchmark asks about inflates that benchmark —
    which is precisely the score gate #6 pins. Matching is case- and
    suffix-insensitive because "BAE Systems plc" and "BAE Systems" are the same
    company to a benchmark and to a model.
    """
    n = _norm_subject(subject)
    if not n:
        return False
    for e in eval_subjects or ():
        en = _norm_subject(e)
        if en and (n == en or en in n or n in en):
            return True
    return False


def write_corpus(
    captured: Iterable[tuple[str, dict]],
    out: Path,
    eval_subjects: Iterable[str] | None = None,
    allow_unchecked: bool = False,
) -> int:
    """Build traces from (subject, real_payload) pairs, DROPPING any that fail
    validation or that are present in the frozen eval set. Returns rows written.

    FAIL-CLOSED on contamination (R-F3366): `eval_subjects=None` raises unless
    `allow_unchecked=True` is passed deliberately. Checked live against the real
    500-Q set, 3 of the 14 seed subjects — Rosoboronexport, Wagner Group and BAE
    Systems plc — were IN the benchmark. The first check ran in a process with no
    initialised state store, reported `golden_n: 0, overlap: []`, and would have
    waved a contaminated corpus straight through. Absence of a blocklist is not
    evidence of no contamination, so silence is not allowed to mean "clean".

    A builder that writes what it cannot validate is how bad data ships.
    """
    if eval_subjects is None and not allow_unchecked:
        raise ValueError(
            "refusing to build without an eval blocklist: pass eval_subjects=... "
            "(the frozen golden-set subjects) or allow_unchecked=True deliberately. "
            "An unchecked corpus can silently contaminate the 500-Q benchmark."
        )
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    kept: list[dict] = []
    for subject, payload in captured:
        if eval_subjects is not None and is_eval_contaminated(subject, eval_subjects):
            print(f"  DROP {subject}: present in the frozen eval set (contamination)",
                  file=sys.stderr)
            continue
        trace = build_trace(subject, payload)
        errs = validate_trace(trace)
        if errs:
            print(f"  DROP {subject}: {errs[0]}", file=sys.stderr)
            continue
        kept.append(trace)
    out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in kept) + ("\n" if kept else ""),
        encoding="utf-8",
    )
    return len(kept)


# ── live capture (public-record subjects only) ─────────────────────────────

DEFAULT_SUBJECTS: list[str] = [
    # sanctions-listed (public record)
    "Rosoboronexport", "Wagner Group", "Bank Rossiya", "Kalashnikov Concern",
    "Islamic Revolutionary Guard Corps", "Sberbank", "Gazprombank",
    # listed public companies expected clean
    "Marks and Spencer Group plc", "BAE Systems plc", "Rolls-Royce Holdings plc",
    "Unilever plc", "Tesco plc", "Siemens AG", "Airbus SE",
]


async def capture_live(subjects: list[str], base: str, token: str) -> list[tuple[str, dict]]:
    """Execute the REAL screening tool for each subject and keep its payload."""
    import httpx
    out: list[tuple[str, dict]] = []
    async with httpx.AsyncClient(timeout=120.0) as client:   # no-breaker: offline corpus tool, not a serving path
        for s in subjects:
            try:
                r = await client.post(
                    f"{base}/api/aria/compliance/screen",
                    headers={"Authorization": f"Bearer {token}"},
                    json={"entity_name": s},
                )
                if r.status_code != 200:
                    print(f"  SKIP {s}: HTTP {r.status_code}", file=sys.stderr)
                    continue
                out.append((s, r.json()))
                print(f"  captured {s}", file=sys.stderr)
            except Exception as e:                            # noqa: BLE001
                print(f"  SKIP {s}: {type(e).__name__}: {e}", file=sys.stderr)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--live", action="store_true", help="capture real tool output from the service")
    ap.add_argument("--from-cache", type=Path, help="rebuild from previously captured real payloads")
    ap.add_argument("--capture-to", type=Path, help="also save the raw captures for replay")
    ap.add_argument("--base", default=os.getenv("ARIA_SERVICE_URL", "https://aria-intel.fly.dev"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument(
        "--eval-blocklist", type=Path,
        help="file of entity names present in the frozen eval set (one per line). "
             "Generate it IN-BOX, where the operator token and the golden set live.",
    )
    ap.add_argument(
        "--allow-unchecked-contamination", action="store_true",
        help="build without a blocklist. Only for smoke tests — never for a training run.",
    )
    args = ap.parse_args()

    eval_subjects: list[str] | None = None
    if args.eval_blocklist:
        eval_subjects = [
            ln.strip() for ln in Path(args.eval_blocklist).read_text(encoding="utf-8").splitlines()
            if ln.strip() and not ln.startswith("#")
        ]

    captured: list[tuple[str, dict]] = []
    if args.from_cache:
        for line in Path(args.from_cache).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                captured.append((rec["subject"], rec["payload"]))
    elif args.live:
        import asyncio
        token = os.getenv("ARIA_INTERNAL_TOKEN", "")
        if not token:
            print("ARIA_INTERNAL_TOKEN not set", file=sys.stderr)
            return 2
        subs = DEFAULT_SUBJECTS[: args.limit] if args.limit else DEFAULT_SUBJECTS
        captured = asyncio.run(capture_live(subs, args.base.rstrip("/"), token))
        if args.capture_to:
            Path(args.capture_to).parent.mkdir(parents=True, exist_ok=True)
            Path(args.capture_to).write_text(
                "\n".join(json.dumps({"subject": s, "payload": p}, ensure_ascii=False)
                          for s, p in captured) + "\n", encoding="utf-8")
    else:
        print("one of --live or --from-cache is required", file=sys.stderr)
        return 2

    n = write_corpus(
        captured, args.out,
        eval_subjects=eval_subjects,
        allow_unchecked=args.allow_unchecked_contamination,
    )
    print(f"wrote {n} validated traces -> {args.out} (from {len(captured)} real captures)")
    return 0 if n else 1


if __name__ == "__main__":
    raise SystemExit(main())
