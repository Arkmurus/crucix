"""build_ark_dd_layer_curriculum — teach ARIA her own ARK-DD layer stack
(R-F4360 / C-306).

THE MEASUREMENT. On the clean v0.8 eval (2026-08-26, 500-Q open-book, the first
run through the repaired harness): **`dd_layer` is 100 rows — 20% of the entire
benchmark — and 93 of them fail.** It is the single largest failure class, and
it is not a retrieval gap. Asked "What does Layer 2 (Network) do?", she answered
with the OSI model:

    "Layer 2 (Data Link Layer) is responsible for providing reliable
     communication between two devices on the same network."

Asked about Layer 4 she could not even identify which stack was meant — "could
refer to either the 7-layer Contact Intelligence stack or the 5-layer Financial
Analysis layer". Asked to run a Layer 1 identity check on Rosoboronexport she
refused for lack of context, when the expected output is a sanctions finding.

WHY THIS IS THE NORTH-STAR TARGET AND NOT INTERNAL TRIVIA.
`docs/golden_intel_north_star_2026_07_14.md` is explicit: "The gap is not the
guard. The gap is value density", and every item must say what the customer
should DO differently. The ARK-DD layers are the mechanism that produces those
decisions. A DD verdict she cannot describe is a verdict she cannot justify to
the operator who has to act on it — so every row here ends in the customer
action the layer enables, not in a definition.

AUTHORED FROM THE CODE, NOT FROM THE EVAL. The operator's directive is that
Claude trains ARIA rather than DeepSeek. Every mechanism below was read out of
`aria_service/intel/dd_orchestrator.py`, `commercial_coherence.py` and
`dd_layer_extensions.py`. No expected_answer from the eval set was consulted
while writing the assistant turns; the pre-flight checks PROMPT overlap only, so
answer-level discipline is human and it was exercised here.

TWO THINGS THE SOURCE SETTLED THAT GUESSWORK WOULD HAVE GOT WRONG:

1. **THE NUMBERING IS NOT `DD_LAYER_NAMES` ORDER.** That tuple lists
   verification 11th, but every numbered label in the code reads
   `Layer 3 (Verification)` and `Layer 4 (Compliance)`. The tuple is an
   iteration order, not a numbering. Teaching the tuple order would have made
   her confidently wrong about her own stack.

2. **LAYER 3 IS NOT VERIFICATION IN THE INDEPENDENT-SOURCE SENSE, AND SAYING SO
   IS THE POINT.** R-F393 records the legacy name as a Phase A honesty bug,
   found when a Lukoil DD returned 0% grounded while the layer self-reported as
   wired. The honest description is triangulation + conflict detection;
   `grounded_rate` is the fraction of claims with >= 2 sources, NOT a
   URL-verification rate; it does NOT re-fetch sources to re-confirm a claim's
   truth. A curriculum that taught "Layer 3 verifies" would re-introduce the
   exact overclaim the repo already fixed once.

A COLLISION FOUND WHILE READING, recorded rather than trained: `5b` is used for
TWO different things. `commercial_coherence.py:5` and `dd_orchestrator.py:16238`
both treat 5b as **deception scoring**, while `dd_orchestrator.py:12492`
docstrings a *sweep intelligence* helper as "Layer 5b". The deception-scoring
meaning is the dominant one and is what the rest of the stack references, so it
is what these rows teach. The sweep-intelligence label is an inconsistency in
the code and should be renamed there, not taught here.

WHAT THIS DELIBERATELY DOES NOT DO. It does not teach her to answer a Layer 1
check without evidence. The applied rows teach the SHAPE of the answer — which
sources the layer consults, what a hard-stop means, what the operator does next
— while keeping the grounded-refusal contract intact for facts she has not been
given. Trading that away would give back the honesty the fine-tune bought.

Usage:
  python scripts/train/build_ark_dd_layer_curriculum.py \
      --out data/training/aria_ark_dd_layers_v1.jsonl
"""
from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

SYSTEM = (
    "You are ARIA, a due-diligence intelligence agent. You run a layered "
    "due-diligence stack called ARK-DD and you know it precisely: what each "
    "layer consults, what it produces, what it does NOT establish, and what "
    "the operator should do with its output. Never confuse these layers with "
    "the OSI networking model. Never claim a layer verifies something it only "
    "triangulates, and never invent a finding you were not given."
)

#: Layer -> (name, mechanism, boundary, operator action). Every field read from
#: source; see the module docstring for the files.
PAIRS: list[dict] = [
    {
        "id": "numbering", "layer": "all", "mode": "orientation",
        "q": "What are the ARK-DD layers, in order?",
        "a": (
            "ARK-DD runs as a numbered stack, and the numbering is its own thing — it is not the "
            "OSI networking model and it is not the alphabetical order the code iterates in.\n\n"
            "Layer 1 Identity · Layer 2 Network · Layer 3 Verification (triangulation) · "
            "Layer 4 Compliance · Layer 5 Digital, with 5b deception scoring and 5c commercial "
            "coherence · Layer 6 Synthesis · Layer 7 Report assembly · Layer 8 "
            "Counter-intelligence · Layer 9 Sanctions divergence · Layer 10 Forensic.\n\n"
            "The order matters operationally: Layer 1 can HARD-STOP the run on a sanctions hit, "
            "which short-circuits everything after it. So a report that ends at Layer 1 is not an "
            "incomplete report — it is a complete answer that the counterparty is blocked, and the "
            "operator should stop the transaction rather than wait for the rest."
        ),
    },
    {
        "id": "not_osi", "layer": "2", "mode": "disambiguation",
        "q": "Is Layer 2 the data link layer?",
        "a": (
            "No. That is the OSI networking model, which is a different thing entirely and has "
            "nothing to do with due diligence.\n\n"
            "In ARK-DD, Layer 2 is NETWORK in the ownership-and-control sense: it walks the "
            "one-hop director and beneficial-owner graph around the target entity.\n\n"
            "If you are asked about ARK-DD and answer about frames, MAC addresses or reliable "
            "delivery between adjacent nodes, you have answered a different question and the "
            "operator gets nothing they can act on."
        ),
    },
    {
        "id": "l1_describe", "layer": "1", "mode": "describe",
        "q": "What does Layer 1 (Identity) do?",
        "a": (
            "Layer 1 establishes WHO the counterparty is and screens that identity. It resolves "
            "name variants, screens each variant against the sanctions lists, classifies PEP "
            "exposure, and for companies adds registry lookups, ghost-score signals such as "
            "founding date and registered-address pattern, and domain-ownership (RDAP) as a "
            "further identity signal.\n\n"
            "It is the only layer that can HARD-STOP the whole run: a direct sanctions hit on a "
            "primary authority in an applicable deal jurisdiction short-circuits the orchestrator, "
            "because nothing found later can make a blocked party unblocked.\n\n"
            "Person and company modes diverge here on purpose — a natural person has no Companies "
            "House record, no ghost score and no address-pattern check, but does need name-variant "
            "resolution, multi-variant screening and PEP classification.\n\n"
            "Operator action: on a hard stop, stop the transaction and escalate. On a clean pass, "
            "treat identity as established for the layers that build on it — everything downstream "
            "is about the entity Layer 1 resolved, so a mis-resolution poisons the whole report."
        ),
    },
    {
        "id": "l1_apply", "layer": "1", "mode": "applied",
        "q": "Run a Layer 1 identity check on a state-owned arms exporter.",
        "a": (
            "Layer 1 on a state-owned arms exporter runs in this order.\n\n"
            "First, name-variant resolution — transliteration variants and legal-form suffixes, "
            "because a designation is recorded against a specific rendering of the name and a "
            "single-string screen misses the others. Then multi-variant sanctions screening across "
            "the primary authorities, and PEP classification of the entity's controlling persons.\n\n"
            "For a state-owned exporter the expected shape is a designation hit rather than a "
            "clean pass, and the relevant question becomes WHICH authority, on what date, and "
            "whether the deal jurisdiction makes it applicable — a hit on an authority that does "
            "not reach this transaction is a finding, not automatically a block.\n\n"
            "If a hard stop fires, the run short-circuits and the operator stops the transaction "
            "and escalates. I will not name specific designations, dates or list entries unless "
            "the screening evidence is in front of me; inventing a citation here would be the one "
            "failure mode that does real damage, because it reads exactly like a sourced finding."
        ),
    },
    {
        "id": "l2_describe", "layer": "2", "mode": "describe",
        "q": "Walk me through the network layer of the DD stack — what does it actually pull?",
        "a": (
            "Layer 2 walks the one-hop ownership and control graph around the entity, via "
            "`network_walker.walk_network`. It pulls directors and beneficial owners from the "
            "registries, screens each of them against PEP and sanctions lists, and surfaces "
            "structural clusters — shared registered addresses, shared directors across otherwise "
            "unconnected entities — that indicate a network rather than a standalone company.\n\n"
            "The point is that risk hides one hop out. A clean entity whose sole director sits on "
            "six companies at one accommodation address is a different proposition from a clean "
            "entity with an unremarkable board, and only the network walk can tell them apart.\n\n"
            "Operator action: treat a director-network hit as a reason to run DD on the connected "
            "party, not merely to note it. Shared-address clustering is where a mass-registration "
            "address stops being administrative trivia and becomes a question about who actually "
            "operates the business."
        ),
    },
    {
        "id": "l3_describe", "layer": "3", "mode": "describe",
        "q": "How does the DD stack decide how well-sourced a claim is, and which layer does that?",
        "a": (
            "Layer 3 does source triangulation and conflict detection — and the honest framing "
            "matters, because the name overpromises.\n\n"
            "It counts how many independent sources back each claim that Layers 1, 2, 4 and 5 "
            "already collected, computes a grounded_rate as the fraction of claims carrying two or "
            "more sources, detects conflicts BETWEEN sections (a green ghost score sitting beside "
            "a hard-stop country rating), and takes the weakest confidence tag across all sections "
            "as the report's own confidence.\n\n"
            "What it does NOT do: it does not re-fetch external sources to re-confirm that each "
            "claim is true. grounded_rate is a corroboration count, NOT a URL-verification rate. "
            "Citation grounding is checked — that URLs the report cites were actually fetched — "
            "but that is a different and narrower claim than 'verified'.\n\n"
            "This distinction was a Phase A honesty bug: the layer reported itself as working "
            "while a real DD returned 0% grounded. Operator action: read grounded_rate as 'how "
            "many sources agree', and treat a [CONTRADICTED] flag as requiring human review rather "
            "than as a resolved fact."
        ),
    },
    {
        "id": "l4_describe", "layer": "4", "mode": "describe",
        "q": "Which layer of the DD stack decides licensable versus prohibited, and on what inputs?",
        "a": (
            "Layer 4 composes the regulatory picture: country risk indices, technology "
            "classification, and international / export-control / regional-bloc rules retrieved "
            "through the RAG store. Court records also feed this layer.\n\n"
            "Concretely it answers three questions the operator has to answer before shipping "
            "anything: how risky is this jurisdiction, what is this item under export control, and "
            "which bloc's rules apply to this route.\n\n"
            "Operator action: this is the layer that decides licensable versus prohibited. A "
            "classification here changes what you file and to whom; a bloc rule can make a "
            "transaction impossible regardless of how clean the counterparty is. Where an embargo "
            "applies, no amount of licensing cures it, and the answer is to stop rather than to "
            "prepare an application."
        ),
    },
    {
        "id": "l5_describe", "layer": "5", "mode": "describe",
        "q": "What does Layer 5 (Digital) do, and what are 5b and 5c?",
        "a": (
            "Layer 5 is the open-source and inference layer: multilingual web search, RAG hits, "
            "neural inference, and — in deep mode — a deep_research pass at greater depth and cost "
            "than the standard run.\n\n"
            "Layer 5b is deception scoring on counterparty communications: linguistic distancing, "
            "unverifiable credentials, urgency pressure, and similar signals, fed partly by "
            "anomalies detected earlier.\n\n"
            "Layer 5c is commercial and legal coherence: does the deal make commercial sense on "
            "its own terms — does the stated volume fit the stated customer, does the margin fit "
            "the route, does the legal structure fit the trade.\n\n"
            "Operator action: 5c is the layer that catches a deal which passes every list check "
            "and still does not add up, which is the shape most diversion cases take. Treat a 5c "
            "flag as a question to put to the counterparty, not as a finding to file silently."
        ),
    },
    {
        "id": "l6_describe", "layer": "6", "mode": "describe",
        "q": "What does Layer 6 (Synthesis) do?",
        "a": (
            "Layer 6 is where the report becomes a decision. It rolls up the authoritative ghost "
            "score, runs an ACH (analysis of competing hypotheses) matrix over the collected "
            "findings, classifies overall risk, and triggers a SAR where the pattern warrants "
            "it.\n\n"
            "ACH matters because it is the discipline that stops a report from being a list of "
            "concerning facts: competing explanations are scored against the same evidence, so the "
            "conclusion has to survive the innocent reading as well as the adverse one.\n\n"
            "Note the ghost score is a company-only signal — it is built from founding date and "
            "registered-address pattern, neither of which a natural person has, so a person DD "
            "reaches its verdict without one.\n\n"
            "Operator action: this is the layer whose output you act on. If Layer 6 says RED, the "
            "supporting layers tell you why; if it says GREEN while a lower layer flagged a "
            "conflict, that conflict is the thing to read first."
        ),
    },
    {
        "id": "l7_describe", "layer": "7", "mode": "describe",
        "q": "What does Layer 7 do?",
        "a": (
            "Layer 7 is report assembly: it takes every prior layer's output and produces the "
            "structured ARK-DD report — the artefact the customer actually receives. It is "
            "persisted under a run id, appended to the intel ledger as a DD-class signal, and "
            "serialised to markdown for chat delivery.\n\n"
            "Two fields on it carry more weight than they look. `layers_run` and `layers_skipped` "
            "record what actually executed, so a reader can tell a clean finding from a layer that "
            "never ran — an absent section is not a clean section.\n\n"
            "And `verdict_logic_version` stamps WHICH DECISION RULES produced the findings. The "
            "schema version pins the report's shape and the generator pins its name; neither says "
            "anything about the logic. Without that stamp, a report re-read months later is "
            "interpreted under whatever rules exist then, so a verdict can appear to change with "
            "no change in the evidence.\n\n"
            "Operator action: when a regulated buyer's auditor asks why a verdict was reached, "
            "these three fields are the answer — what ran, what did not, and under which rules. "
            "Quote them rather than re-deriving the verdict under today's logic, because "
            "re-deriving it is precisely the thing the stamp exists to make unnecessary."
        ),
    },
    {
        "id": "l8_describe", "layer": "8", "mode": "describe",
        "q": "What does Layer 8 (Counter-intelligence) do?",
        "a": (
            "Layer 8 asks whether the counterparty is presenting a constructed picture rather than "
            "a real one — procurement fronts, diversion routing, and the patterns that indicate "
            "someone is being looked at by a third party or is arranging to be looked at "
            "favourably.\n\n"
            "It is distinct from Layer 5b: 5b scores how a counterparty COMMUNICATES, while "
            "Layer 8 is about the structural picture the counterparty presents.\n\n"
            "Operator action: a counter-intelligence flag changes who inside the customer needs to "
            "know. It is generally not a 'request more documents' finding — more documents from a "
            "constructed picture produce a better-constructed picture — it is an escalation."
        ),
    },
    {
        "id": "l9_describe", "layer": "9", "mode": "describe",
        "q": "What does Layer 9 (Sanctions divergence) do?",
        "a": (
            "Layer 9 compares what the different sanctions authorities say about the same entity "
            "and surfaces where they DISAGREE — designated by one bloc and not another, designated "
            "on different dates, or designated under different programmes.\n\n"
            "Divergence is a finding in itself rather than a data-quality problem. It is the "
            "difference between a transaction that is prohibited everywhere and one that is "
            "prohibited on a specific nexus, and it is where a route or a subsidiary choice "
            "changes the answer.\n\n"
            "Operator action: read divergence against YOUR nexus — which authority reaches this "
            "transaction, through which entity, in which currency. A clean result under one "
            "authority is not a clean screen if a different authority reaches your route, and "
            "reporting it as clean would be the dangerous error."
        ),
    },
    {
        "id": "l10_describe", "layer": "10", "mode": "describe",
        "q": "What does Layer 10 (Forensic) do?",
        "a": (
            "Layer 10 is the document-level layer: it examines the artefacts themselves rather "
            "than what registries say about the entity — the filings, the contracts, the "
            "supporting paperwork — for signs of alteration, inconsistency, or construction.\n\n"
            "Operator action: a forensic finding usually changes the standing of every other "
            "finding that depended on the same document. If the paperwork is questionable, facts "
            "sourced from that paperwork inherit the doubt, and the honest report says so rather "
            "than carrying them forward at full confidence."
        ),
    },
    {
        "id": "hard_stop", "layer": "1", "mode": "boundary",
        "q": "A DD report stopped at Layer 1. Is that report incomplete?",
        "a": (
            "No — that is a complete answer, and reporting it as incomplete would understate it.\n\n"
            "Layer 1 short-circuits the orchestrator when it hits a hard stop: a direct sanctions "
            "hit on a primary authority where the deal jurisdiction applies. Everything after Layer "
            "1 exists to characterise a counterparty you can transact with. Once the counterparty "
            "is blocked, more characterisation does not change the decision.\n\n"
            "Operator action: stop the transaction and escalate. Do not commission the remaining "
            "layers to 'complete the file' — the file is complete, and the finding is the "
            "strongest one the stack can produce."
        ),
    },
    {
        "id": "unobservable", "layer": "all", "mode": "boundary",
        "q": "One of the layers reported no output at all. Did it fail?",
        "a": (
            "Not necessarily, and the distinction is deliberate. A layer that ran but stored "
            "nothing inspectable is UNOBSERVABLE — it is neither proof of success nor evidence of "
            "failure, and scoring it as an error would be a fabricated negative.\n\n"
            "Three shapes exist: a section carrying an explicit status, a plain payload with no "
            "status (which counts as ok unless it carries an explicit failure marker), and no "
            "stored attribute at all (unobservable).\n\n"
            "Operator action: treat unobservable as a gap in the INSTRUMENT, not a finding about "
            "the entity. If that layer's question matters to the decision, it has to be answered "
            "another way rather than assumed clean — an absent reading is not a clean reading."
        ),
    },
]


def _rows() -> list[dict]:
    return [{
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": p["q"]},
            {"role": "assistant", "content": p["a"]},
        ],
        "topic": f"ark_dd_layer_{p['layer']}_{p['id']}",
        "layer": p["layer"],
        "mode": p["mode"],
        "confidence": "high",
        "source": "claude_authored:R-F4360",
    } for p in PAIRS]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    rows = _rows()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n")
    print(f"wrote {len(rows)} rows -> {args.out}")
    print("  modes :", dict(collections.Counter(r["mode"] for r in rows)))
    print("  layers:", sorted({r["layer"] for r in rows}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
