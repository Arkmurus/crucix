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
        "id": "l1_apply", "layer": "1", "mode": "applied", "external": True,
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
        "id": "evidence_status", "layer": "all", "mode": "contract",
        "q": "What does the evidence status at the bottom of a DD report mean?",
        "a": (
            "It is an EVIDENCE vocabulary, not a probability. The tag takes one of CONFIRMED, "
            "ASSESSED, PROBABLE, UNCERTAIN or UNVERIFIED, and every one of those describes HOW a "
            "claim was established — corroborated by a primary source, inferred, or not checked — "
            "rather than how likely it is to be true.\n\n"
            "The distinction is load-bearing because the two readings diverge most where it "
            "matters. UNVERIFIED does not mean 'probably fine'. It means NOBODY LOOKED. A reader "
            "who takes it as a low-risk probability has inverted the finding.\n\n"
            "The report's tag is the WEAKEST tag across all sections, so the headline can never "
            "oversell what the weakest section actually established.\n\n"
            "Operator action: read the tag as 'how do we know this', then decide whether that "
            "standard of knowing is good enough for the decision in front of you. An UNVERIFIED "
            "line on a question that drives the decision is a task, not a reassurance."
        ),
    },
    {
        "id": "data_gaps", "layer": "all", "mode": "contract",
        "q": "The report lists a data gap instead of a finding. Is that a failure of the run?",
        "a": (
            "No — it is the run doing its job. Every section carries findings AND data_gaps as "
            "first-class fields precisely so that a missing data point can never disappear "
            "silently.\n\n"
            "The alternative is far worse: a report that omits what it could not resolve reads as "
            "a report that resolved everything, and the reader has no way to see the difference. "
            "An absent line and a clean line look identical once the gap is dropped.\n\n"
            "Section status makes the same distinction structurally — OK, PARTIAL, SKIPPED or "
            "ERROR — and SKIPPED specifically means the orchestrator short-circuited before that "
            "layer, usually because Layer 1 hard-stopped.\n\n"
            "Operator action: read data_gaps before findings. They tell you which parts of the "
            "picture are actually established and which questions are still open, and an open "
            "question on the decisive point outranks any number of resolved ones elsewhere."
        ),
    },
    {
        "id": "source_tier", "layer": "all", "mode": "contract",
        "q": "What are the source tiers on a DD finding, and why do they matter?",
        "a": (
            "Findings carry a source tier: OFFICIAL, INDUSTRY, QUALITY_PRESS, or UNVERIFIED.\n\n"
            "The tier is about WHO said it, which is a different axis from how many said it. Three "
            "quality-press reports repeating one another are not equivalent to one official "
            "registry entry, even though a naive corroboration count would score them higher.\n\n"
            "Operator action: for anything that has to survive a regulator or an auditor, the "
            "question is whether an OFFICIAL tier source backs it. Press-tier corroboration is "
            "useful for direction and for adverse-media signal, but it is not the evidence you "
            "produce when asked to justify a block or a clearance."
        ),
    },
    {
        "id": "modes", "layer": "all", "mode": "contract",
        "q": "What is the difference between quick, standard and deep DD?",
        "a": (
            "They are the orchestrator's three modes, and the main thing that changes is how hard "
            "Layer 5 (Digital) looks.\n\n"
            "In deep mode the deep_research pass runs thorough — substantially more search angles "
            "and articles, at correspondingly more time and cost. Quick and standard use the "
            "shallow pass, which keeps a routine run inside the per-run cost cap.\n\n"
            "Cost is tracked PER LAYER, so the cap can halt a run that is burning its whole budget "
            "inside one layer rather than silently spending it.\n\n"
            "Operator action: match the mode to the decision. A counterparty you are about to "
            "contract with justifies deep; a first-pass screen of a long list does not, and running "
            "deep on all of them spends the budget where it changes nothing. If a standard run "
            "returns an ambiguous verdict on a decisive counterparty, that is the signal to "
            "re-run deep rather than to accept the ambiguity."
        ),
    },
    {
        "id": "l2_apply", "layer": "2", "mode": "applied", "external": True,
        "q": "Run the network layer on a company whose sole director also sits on five other firms.",
        "a": (
            "That pattern is exactly what Layer 2 exists to surface, so the walk is the answer "
            "rather than a preliminary to it.\n\n"
            "It pulls the directors and beneficial owners from the registries, screens each of "
            "those persons against PEP and sanctions lists — the entity can be clean while a "
            "controlling person is not — and then looks for structure: are the six companies at "
            "one address, do they share other officers, do they cluster by incorporation date.\n\n"
            "A single director across six firms is not adverse on its own; nominee and "
            "company-formation arrangements are lawful and common. What makes it a finding is the "
            "combination — shared accommodation address, near-simultaneous incorporation, no "
            "operating footprint.\n\n"
            "Operator action: run DD on the connected entities rather than noting the link, and "
            "ask who directs the director. I will not characterise the specific companies or "
            "persons unless the registry evidence is in front of me."
        ),
    },
    {
        "id": "l6_apply", "layer": "6", "mode": "applied", "external": False,
        "q": "Layer 6 returned RED but Layer 1 was clean. How should that be read?",
        "a": (
            "As entirely coherent, and worth reading carefully rather than as a contradiction.\n\n"
            "Layer 1 clean means the entity itself is not designated. Layer 6 RED is the synthesis "
            "across everything — network exposure, compliance classification, digital and "
            "coherence signals — resolved through the competing-hypotheses matrix. A counterparty "
            "can be entirely undesignated and still be the wrong counterparty.\n\n"
            "The ACH step is what makes that verdict worth acting on: the innocent explanation was "
            "scored against the same evidence as the adverse one, so RED means the adverse reading "
            "survived the comparison, not that concerning facts were collected.\n\n"
            "Operator action: go to the sections that carried the weight rather than re-running "
            "Layer 1. And note the inverse case is the dangerous one — a GREEN synthesis sitting "
            "beside a conflict flagged lower down should be read as the conflict first, because "
            "Layer 3 surfaces exactly that shape as [CONTRADICTED] and it requires human review."
        ),
    },
    {
        "id": "route_shell", "layer": "2", "mode": "routing",
        "q": "Which layer would catch a shell company?",
        "a": (
            "No single layer does; a shell is a composite finding, and expecting one layer to "
            "return it is how the pattern gets missed.\n\n"
            "Layer 1 contributes the ghost-score signals — founding date and registered-address "
            "pattern. Layer 2 contributes the network shape: one director across many entities, "
            "shared accommodation addresses, clusters that incorporate together. Layer 5c asks "
            "whether the commercial story holds up, which is where an entity with no operating "
            "footprint but a large stated contract fails. Layer 6 is where those combine into a "
            "verdict through the competing-hypotheses matrix.\n\n"
            "Operator action: if you want the shell question answered, read those sections "
            "together rather than looking for a 'shell: yes' field. Each signal alone has an "
            "innocent explanation — new companies are new, formation agents are lawful — and it is "
            "the conjunction that carries the weight."
        ),
    },
    {
        "id": "route_diversion", "layer": "5", "mode": "routing",
        "q": "Which layer catches a diversion risk?",
        "a": (
            "Mostly Layer 5c (commercial coherence), with Layer 4 setting the frame and Layer 9 "
            "deciding whether it is your problem.\n\n"
            "Layer 4 classifies the item and the destination — what is controlled, which bloc's "
            "rules reach this route. Layer 5c asks whether the deal makes sense on its own terms: "
            "does the stated end use fit the volume, does the customer fit the product, does the "
            "route fit the geography. Layer 9 then tells you whether the authorities that reach "
            "your nexus actually restrict it.\n\n"
            "Diversion is the classic case where every list check passes and the deal still does "
            "not add up, which is precisely the gap 5c exists to fill.\n\n"
            "Operator action: treat a coherence flag as a question to put to the counterparty in "
            "writing. An end-use statement that cannot be given in writing is itself the finding."
        ),
    },
    {
        "id": "route_pep", "layer": "1", "mode": "routing",
        "q": "Where does PEP exposure show up?",
        "a": (
            "In two places, and conflating them loses the more interesting one.\n\n"
            "Layer 1 classifies PEP exposure for the TARGET itself — the entity or person being "
            "screened. Layer 2 screens the directors and beneficial owners one hop out, which is "
            "where PEP exposure usually actually sits: the company is unremarkable and a "
            "controlling person is not.\n\n"
            "Operator action: a PEP finding is not a block. It sets the standard of diligence and "
            "the approval level required, and it is the reason the source of funds question stops "
            "being routine. Reporting it as adverse is as wrong as omitting it."
        ),
    },
    {
        "id": "fail_no_registry", "layer": "1", "mode": "failure",
        "q": "The registry lookup failed. What should the report say?",
        "a": (
            "It should say the registry could not be reached, in the data_gaps for that section, "
            "and the section status should reflect PARTIAL rather than OK.\n\n"
            "What it must NOT do is report the absence of an adverse registry finding as a clean "
            "registry check. Those are different claims and only one of them was established. A "
            "no-hit produced from a source that never answered is not a no-hit.\n\n"
            "Operator action: treat the identity question as open. If the decision turns on "
            "corporate existence or standing, that has to be resolved another way before the "
            "decision, not inferred from the silence."
        ),
    },
    {
        "id": "fail_partial_screen", "layer": "1", "mode": "failure",
        "q": "One sanctions source was unreachable during the screen. Is the result still a clean screen?",
        "a": (
            "No, and this is the most dangerous place to round up.\n\n"
            "A no-hit produced from a reduced source set is not a clean screen. It is a screen "
            "against the sources that answered, and the honest output is a third state: could not "
            "screen against the full set, naming which source was missing and when it was tried.\n\n"
            "Reporting it as clean converts an unknown into a false assurance, and the reader "
            "cannot see the difference — which is what makes it worse than a red finding.\n\n"
            "Operator action: re-run when the source recovers. If the decision cannot wait, "
            "escalate with the gap stated so the risk is accepted knowingly rather than by "
            "omission."
        ),
    },
    {
        "id": "fail_conflict", "layer": "3", "mode": "failure",
        "q": "Two sources disagree about the registered address. What happens?",
        "a": (
            "Layer 3 surfaces it as a contradiction rather than picking a winner. The marker is "
            "explicit — flagged as sources disagreeing, requiring human review — and it "
            "deliberately does not resolve silently to whichever source is more recent or more "
            "convenient.\n\n"
            "Silent resolution is the failure mode worth avoiding: it produces a confident single "
            "answer whose disagreement the reader never sees, and address conflicts are exactly "
            "where a real finding hides.\n\n"
            "Operator action: read a contradiction as an open question, not as noise. Two "
            "registered addresses can mean a stale filing, or a redomiciliation, or two different "
            "entities being conflated under one name — and the third possibility changes the whole "
            "report."
        ),
    },
    {
        "id": "fail_cost_cap", "layer": "all", "mode": "failure",
        "q": "A run stopped early on the cost cap. How should that be read?",
        "a": (
            "As an incomplete run that says so, which is the design. Cost is tracked per layer "
            "precisely so a cap can halt a run that is burning its budget inside one layer instead "
            "of spending it silently.\n\n"
            "The sections that did not run carry SKIPPED rather than a clean status, so the report "
            "distinguishes 'nothing adverse found' from 'never looked'.\n\n"
            "Operator action: check WHICH layers were skipped against what the decision needs. If "
            "the skipped layer is the one that would have answered your question, the run has not "
            "answered it, and re-running scoped to that layer is cheaper than re-running "
            "everything."
        ),
    },
    {
        "id": "contrast_5b_8", "layer": "8", "mode": "contrast",
        "q": "What is the difference between Layer 5b and Layer 8?",
        "a": (
            "They both look for a constructed picture, but at different objects.\n\n"
            "Layer 5b scores how the counterparty COMMUNICATES — linguistic distancing, "
            "unverifiable credentials, urgency pressure. It reads the messages.\n\n"
            "Layer 8 examines the structural picture the counterparty PRESENTS — procurement "
            "fronting, diversion routing, arrangements that exist to be looked at favourably. It "
            "reads the setup.\n\n"
            "Operator action: a 5b flag is usually a reason to slow the exchange down and get "
            "things in writing. A Layer 8 flag is usually an escalation, because asking a "
            "constructed picture for more documents produces a better-constructed picture."
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


#: R-F4360 — QUESTION-FORM AUGMENTATION, and the reasoning matters because
#: padding a corpus is usually the wrong instinct.
#:
#: The measured failure is not that she gives a poor answer about Layer 2 — it
#: is that she does not RECOGNISE the question as being about her own stack, and
#: answers the OSI model instead. That is an invariance failure: one fact, many
#: askings, and she only maps some of them. So the augmentation varies the
#: QUESTION and holds the answer fixed, which is exactly the axis that is broken.
#:
#: Sizing is deliberate, not arbitrary. The corpus is ~24.3k rows; the last
#: attempt put ~64 rows against it (0.26%) and moved the eval by net -3,
#: p>0.05. These templates take the hand-authored facts to ~1.5% of the corpus,
#: which is the weight at which a factual axis has a chance of registering.
#:
#: THE HONEST COST: variants of one fact share an assistant turn verbatim, so
#: the model can memorise a string rather than the knowledge. That is an
#: accepted trade for factual recall — it is how the fact is meant to be
#: recalled — but it is why the underlying FACTS are hand-authored from source
#: and only the questions are generated. Generating answers too would multiply
#: whatever is wrong in one of them by twelve.
#:
#: Phrasings are deliberately operator-voice rather than the textbook
#: "What does Layer N (Name) do?" form. That form belongs to the eval, and the
#: contamination pre-flight already caught three of them in the first cut.
_VARIANTS: dict[str, list[str]] = {
    # EVERY TEMPLATE MUST NAME ITS SUBJECT. A first cut included context-free
    # follow-ups ("How should I read that?", "Talk me through your steps") and
    # they COLLIDED ACROSS PAIRS: 23 identical question strings mapped to 23
    # different answers, which teaches contradiction rather than knowledge.
    # A duplicate-question test now pins this.
    "describe": [
        "Talk me through {ref}.",
        "What actually happens in {ref}?",
        "I'm new to ARK-DD — explain {ref}.",
        "What is {ref} for?",
        "Give me {ref} in plain terms.",
        "What does {ref} contribute to the verdict?",
        "When does {ref} matter to a decision?",
        "I'm reading a DD report — what should the {short} section tell me?",
        "What would I lose if {ref} were skipped?",
        "Summarise {ref} for someone about to act on the report.",
        "What inputs does {ref} use, and what does it produce?",
    ],
    "routing": [
        "{q}",
        "{q} Which part of the DD stack handles it?",
        "{q} Where in ARK-DD does it surface?",
        "{q} If I could only read one section, which?",
        "{q} Which layers do I need to read together?",
    ],
    "failure": [
        "{q}",
        "{q} How should the report handle it?",
        "{q} What is the honest output there?",
        "{q} What should I NOT conclude from it?",
        "{q} Does it invalidate the run?",
    ],
    "contract": [
        "{q}",
        "{q} Why does the report bother with it?",
        "{q} What would go wrong if it were dropped?",
        "{q} How should an operator read it?",
    ],
    "boundary": [
        "{q}",
        "{q} Is that a problem with the run?",
        "{q} What is the correct interpretation?",
        "{q} Does it mean something went wrong?",
    ],
    "contrast": [
        "{q}",
        "{q} Give me the practical difference.",
        "{q} When would I care about one rather than the other?",
    ],
    "orientation": [
        "{q}",
        "Give me the ARK-DD stack end to end.",
        "How is the DD stack structured?",
        "Walk me through the ARK-DD layers.",
        "What are the numbered ARK-DD layers and why does the order matter?",
    ],
    "disambiguation": [
        "{q}",
        "Someone told me ARK-DD Layer 2 is about network packets. Right?",
        "Is ARK-DD's Layer 2 the same as the OSI one?",
        "Clear up what Layer 2 means in ARK-DD.",
    ],
    "applied": [
        "{q}",
        "{q} Walk me through how you would approach it.",
        "{q} What would that look like in practice?",
    ],
}

#: Human-readable references so a generated question reads naturally.
_REF = {
    "1": ("Layer 1, the identity layer", "identity"),
    "2": ("Layer 2, the network layer", "network"),
    "3": ("Layer 3, the triangulation layer", "verification"),
    "4": ("Layer 4, the compliance layer", "compliance"),
    "5": ("Layer 5, the digital layer", "digital"),
    "6": ("Layer 6, the synthesis layer", "synthesis"),
    "7": ("Layer 7, report assembly", "report"),
    "8": ("Layer 8, counter-intelligence", "counter-intelligence"),
    "9": ("Layer 9, sanctions divergence", "sanctions divergence"),
    "10": ("Layer 10, the forensic layer", "forensic"),
    "all": ("the ARK-DD stack", "report"),
}


def _questions_for(p: dict) -> list[str]:
    """Every asking of one fact. Deduped, original first."""
    ref, short = _REF.get(p["layer"], ("the ARK-DD stack", "report"))
    out, seen = [], set()
    for tpl in [ "{q}" ] + _VARIANTS.get(p["mode"], []):
        q = tpl.format(q=p["q"], ref=ref, short=short)
        if q not in seen:
            seen.add(q)
            out.append(q)
    return out


def _rows() -> list[dict]:
    rows = []
    for p in PAIRS:
        for i, q in enumerate(_questions_for(p)):
            rows.append({
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": q},
            {"role": "assistant", "content": p["a"]},
        ],
        "topic": f"ark_dd_layer_{p['layer']}_{p['id']}",
        "variant": i,
        "layer": p["layer"],
        "mode": p["mode"],
        "external": bool(p.get("external", False)),
        "confidence": "high",
        "source": "claude_authored:R-F4360",
            })
    return rows


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
