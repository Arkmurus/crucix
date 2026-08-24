"""build_dd_framework_corpus — R-F2506 — v0.6 CLOSED-BOOK DD-FRAMEWORK corpus.

WHY THIS EXISTS (verified this turn — §22)
══════════════════════════════════════════
v0.5 (build_dd_depth_corpus.py, R-F2498) left the eval's dd_layer topic at ~0.12
because it taught reasoning-over-PROVIDED-evidence (open-book, cite [from ...]).
But the eval's 100 dd_layer questions are CLOSED-BOOK KNOWLEDGE of ARIA's OWN DD
methodology — there is NO evidence in the prompt. Examples from
data/eval_frozen/aria_eval_500q.jsonl (topic dd_layer_1..10):
  - "What does Layer 2 (Network) do?"            -> UBO, PEP, registries, sanctioned, directors
  - "Run a Layer 1 identity check on 'Rosoboronexport'" -> OFAC, SDN, OFSI, HARD, STOP, Identity, Council Regulation
  - "What does Layer 4 (Compliance) cover?"      -> FCDO, FATF, ECCN, NATO, STANAG, ECOWAS, SADC, GCC
The model must ANSWER FROM KNOWLEDGE. So v0.6 teaches ARIA's REAL layered framework
as closed-book Q&A, rewarded on keyword-coverage + correctness.

THE FRAMEWORK TAUGHT IS ARIA'S ACTUAL ONE — EXTRACTED FROM THE CODE (cited inline
in FRAMEWORK below), NOT invented:
  - aria_service/intel/dd_orchestrator.py header (:1-42) — the 7-layer walk + the
    short-circuit / persistence / callable-from contract.
  - Layer 1 Identity  _run_identity            (dd_orchestrator.py:2578) + the
    primary-source parallel screen (:2172) — OFAC/SDN, OFSI, EU Consolidated
    (Council Regulation), UN SC, ICC, Interpol, OpenSanctions PEP, Companies House PSC.
  - Layer 2 Network   _run_network             (dd_orchestrator.py:3544) — network_walker.
  - Layer 3 Verification _run_verification      (dd_orchestrator.py:5988) — triangulation +
    ContradictionDetector; independent_source_verification_run stays False (R-F2413).
  - Layer 4 Compliance _run_compliance          (dd_orchestrator.py:3801) — risk_indices
    (CPI/Basel AML/FATF/OECD CRC), tech_classifier (ECCN/EAR/BIS), regional blocs.
  - Layer 5 Digital   _run_digital              (dd_orchestrator.py:4550) — multilingual
    web+RAG+neural+deep_research; 5b deception, 5c commercial coherence.
  - Layer 6 Synthesis _run_synthesis            (dd_orchestrator.py:6227) — ACH matrix,
    ghost score /20, risk classification, SAR trigger, BLUF.
  - Layer 7 ARK-DD report (dd_orchestrator.py:14,28) — Redis crucix:dd:report:{id}, 7d TTL.
  - Layer 8 Counter-intelligence (dd_orchestrator.py:8876) — narrative-shift / coordinated
    press / tier-contradiction over the intel_ledger (30d window).
  - Layer 9 Sanctions divergence (dd_orchestrator.py:8908) — cross-jurisdiction list
    divergence (OFAC lists / OFSI silent; UN SC silent / EU acts).
  - Layer 10 Forensic (dd_orchestrator.py:8946) — Benford's Law (>=50 values) + TBML.

REUSED (not reinvented) REAL COMPONENTS (§3b)
  - teacher distiller : scripts/train/build_grounded_corpus._deepseek / DEEPSEEK_URL
  - LLM judge          : aria_service.intel.eval_judge.judge_answer (R-F1396)
  - contamination gate : scripts/train/build_dd_depth_corpus.{load_eval_guards,
                          contamination_check,run_preflight,_JudgeLLM} which wrap
                          scripts/train/preflight_eval_contamination.py (--max-overlap 0.01)

REWARD (a row is KEPT only if ALL gates pass — components emitted per row)
  1. keyword_coverage : the answer contains >= COVERAGE_MIN of the row's canonical
                        terms (the layer/concept vocabulary the eval rewards) AND
                        >= MIN_KW_HITS absolute. Replaces v0.5's grounding gate —
                        closed-book, so there is no context to cite.
  2. correctness      : eval_judge.judge_answer(candidate vs AUTHORED golden) verdict
                        in {correct, partial} (wrong rejected). Golden authored from
                        the extracted framework facts.
  3. refusal rows     : graded on abstention markers + must NOT fabricate a specific
                        officeholder / id / number (keeps the v0.5 refusal 0.84->0.744
                        regression from recurring).

CONTAMINATION GATE (fail-loud — the critical correctness gate)
  - internal, per row: exact-normalised + token-Jaccard(>=0.75) vs EVERY eval
    question (frozen + openbook) + eval entity-name blocklist. Teach the FRAMEWORK,
    NEVER the verbatim eval Q/A — question phrasings are deliberately re-worded.
  - external, whole file: preflight_eval_contamination.py at --max-overlap 0.01;
    any overlap raises SystemExit (the run FAILS, no file is trusted).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FULL-RUN RECIPE (this run is a SMALL SAMPLE only — NO GPU, minimal LLM cost)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Sample (this run):  --count 18  -> ~2 DeepSeek calls/row (teacher + judge) ≈ 36
                      calls ≈ $0.03-0.10.
  Full corpus:        --count 300..500. The ROWS spec has ~30 canonical seeds across
                      10 layers + core compliance/architecture concepts; raise
                      --variants (paraphrase count per seed) to fan out. ~600-1000
                      DeepSeek calls ≈ $1-4 (deepseek-chat) — INSIDE the §24 weekly
                      LLM budget ($8-18/wk). This is a DATA-PREP step ONLY: no GPU,
                      no RunPod, run ONCE. Expected yield after gates ≈ 65-80%.
  Merge:              concatenate kept SFT rows with data/training/aria_grounded_v3.jsonl
                      for the next SFT cycle; ALWAYS re-run preflight over the merged file.
  DPO:                --emit-dpo writes {prompt, chosen (keyword-rich framework answer),
                      rejected (generic/fabricated answer missing the keywords)} into
                      scripts/train/prepare_dpo.py (aria_dpo_pairs_v1_str shape).

Usage (this sample run):
  python scripts/train/build_dd_framework_corpus.py \
     --count 18 \
     --out data/training/aria_dd_framework_v06_sample.jsonl \
     --emit-dpo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# ── REUSE the real teacher distiller + endpoint (do NOT reinvent a client) ────
from scripts.train.build_grounded_corpus import DEEPSEEK_URL  # noqa: E402
# ── REUSE the real LLM judge ──────────────────────────────────────────────────
from aria_service.intel import eval_judge  # noqa: E402
# ── REUSE the real contamination machinery + judge adapter from v0.5 ──────────
from scripts.train.build_dd_depth_corpus import (  # noqa: E402
    load_eval_guards,
    contamination_check,
    run_preflight,
    _JudgeLLM,
    reward_correctness,
)

COVERAGE_MIN = 0.55    # keyword gate: fraction of canonical terms the answer must hit
MIN_KW_HITS = 3        # keyword gate: absolute minimum canonical terms present
SOURCE_LABEL = "dd_framework_v06"


# ══════════════════════════════════════════════════════════════════════════════
# 1) FRAMEWORK ROWS — ARIA's REAL DD methodology, each golden authored from the
#    code cited in the module docstring. `keywords` = canonical terms the closed-
#    book answer MUST contain (the vocabulary the eval's dd_layer topic rewards).
#    `question` phrasings are deliberately re-worded away from the eval questions.
# ══════════════════════════════════════════════════════════════════════════════
@dataclass
class Row:
    rid: str
    topic: str
    kind: str            # "explainer" | "applied" | "concept" | "refusal"
    question: str
    golden: str
    keywords: list
    entities: list = field(default_factory=list)


ROWS: list = [
    # ── Layer 1 — Identity ────────────────────────────────────────────────────
    Row("l1_explain", "dd_layer_1", "explainer",
        "In ARIA's ARK-DD pipeline, what is the identity stage responsible for and how does it decide to halt a case early?",
        "Layer 1 (Identity) resolves who the entity is and screens it against the authoritative sanctions and "
        "watch lists in parallel: OFAC SDN, UK OFSI, the EU Consolidated list (by Council Regulation), UN Security "
        "Council, ICC, Interpol, and OpenSanctions PEP, plus Companies House / PSC data and a ghost score for "
        "companies. It applies name-variant and transliteration resolution before matching. A confirmed match on any "
        "authoritative list is a HARD STOP that short-circuits the remaining layers and synthesises the case "
        "immediately; a clean screen lets the case continue to Layer 2 (Network).",
        ["Identity", "OFAC", "SDN", "OFSI", "EU", "Council Regulation", "UN", "PEP", "PSC",
         "Companies House", "ghost", "HARD STOP"]),
    Row("l1_applied", "dd_layer_1", "applied",
        "Walk me through what happens when ARIA runs a Layer 1 identity screen on a Russian state defence exporter such as Uralvagonzavod.",
        "Layer 1 (Identity) screens Uralvagonzavod against OFAC SDN, UK OFSI, the EU Consolidated list (Council "
        "Regulation), UN Security Council, ICC, Interpol and OpenSanctions PEP, using name-variant and Cyrillic-to-"
        "Latin transliteration so the query matches list spellings. A Russian state defence exporter of this profile "
        "is highly likely to be OFAC/EU/OFSI-designated, so a confirmed match is a HARD STOP: the orchestrator "
        "short-circuits Layers 2-5 and synthesises the report as HARD_STOP pending identity confirmation.",
        ["Identity", "OFAC", "SDN", "OFSI", "EU", "Council Regulation", "HARD STOP", "transliteration"],
        entities=["Uralvagonzavod"]),
    # ── Layer 2 — Network ─────────────────────────────────────────────────────
    Row("l2_explain", "dd_layer_2", "explainer",
        "Describe the responsibilities of the network stage in the ARK-DD framework — what does it map and what does it look for?",
        "Layer 2 (Network) composes network_walker to build a one-hop director graph and map the ultimate beneficial "
        "owner (UBO) and PSC structure from company registries. It surfaces cross-linked companies, shared registered "
        "addresses, PEP connections among directors, and any sanctioned entity in the network. A one-hop connection "
        "from a director or PSC to a sanctioned party is a HIGH-risk finding, and the multi-hop entity graph is walked "
        "to catch indirect sanctions exposure through shared directors and shareholders.",
        ["Network", "UBO", "PSC", "PEP", "directors", "registries", "sanctioned", "companies"]),
    Row("l2_applied", "dd_layer_2", "applied",
        "A director of the subject company also sits on the board of a company that is on a sanctions list — how does ARIA's network stage treat that?",
        "In Layer 2 (Network) the director walk found a one-hop link from the subject's director to a sanctioned "
        "entity. That is a HIGH-risk network finding: the subject sits one hop from a sanctioned party through a shared "
        "director, so enhanced due diligence is required, the full board and UBO/PSC chain are screened, and the "
        "sanctions proximity is escalated in the ACH synthesis rather than treated as a clean network.",
        ["Network", "UBO", "PSC", "directors", "sanctioned", "HIGH", "ACH"]),
    # ── Layer 3 — Verification ────────────────────────────────────────────────
    Row("l3_explain", "dd_layer_3", "explainer",
        "What exactly does ARIA's verification stage do, and what does it deliberately NOT claim to do?",
        "Layer 3 (Verification) triangulates the claims that Layers 1/2/4/5 already gathered: it counts how many "
        "independent sources back each claim and computes grounded_rate as the fraction of claims corroborated by two "
        "or more sources. Its ContradictionDetector flags where sources disagree (e.g. registered address on Companies "
        "House vs the website), and it picks the weakest confidence tag across sections. It does NOT independently "
        "re-verify each claim against fresh external sources — independent_source_verification_run stays False; only "
        "citation grounding is checked, reported separately as citation_grounding_rate.",
        ["Verification", "triangulates", "independent", "grounded_rate", "ContradictionDetector",
         "contradicted", "conflicts", "confidence"]),
    Row("l3_concept", "dd_layer_3", "concept",
        "In ARIA's verification model, three well-known outlets all report the same fact but every one of them cites the same OEM press release. Is that fact independently verified?",
        "No. ARIA's SourceIndependenceChecker treats that as ONE source, not three: when Reuters, AP and the BBC all "
        "trace back to a single OEM press release the reports are not independent, so the claim is UNVERIFIED for "
        "triangulation purposes. Independent verification in Layer 3 requires two or more genuinely independent "
        "sources (distinct Tier-1a/Tier-1b origins), and the ContradictionDetector still checks for disagreement "
        "across them.",
        ["SourceIndependenceChecker", "OEM", "independent", "UNVERIFIED", "Tier-1a", "Tier-1b",
         "triangulation"]),
    # ── Layer 4 — Compliance ──────────────────────────────────────────────────
    Row("l4_explain", "dd_layer_4", "explainer",
        "What ground does the compliance stage of ARK-DD cover — country risk, export control and which regional regimes?",
        "Layer 4 (Compliance) composes risk_indices for country risk (CPI, Basel AML, FATF status, OECD CRC) and "
        "tech_classifier for export control (ECCN under the EAR, administered by BIS/ECJU), then layers "
        "international-law and regional-bloc rules via RAG. It covers FCDO strategic-export guidance, FATF grey/black "
        "listing, NATO STANAG interoperability, and regional regimes including ECOWAS, SADC, GCC and ASEAN, plus the "
        "Wassenaar Arrangement and MTCR. A RED or HARD_STOP country-risk headline raises a hard finding.",
        ["FCDO", "FATF", "ECCN", "EAR", "BIS", "NATO", "STANAG", "ECOWAS", "SADC", "GCC",
         "Wassenaar", "MTCR"]),
    Row("l4_applied", "dd_layer_4", "applied",
        "The buyer in a deal is incorporated in a country that FATF has grey-listed. What does ARIA's compliance stage flag?",
        "Layer 4 (Compliance) flags the FATF grey-list status as an AML/CFT risk: it means the jurisdiction has "
        "strategic deficiencies under enhanced monitoring, so KYC and UBO verification must be strengthened and "
        "USD/EUR correspondent-banking and payment routing face elevated scrutiny. risk_indices carries the FATF "
        "status into the country-risk headline, and the finding feeds the ACH synthesis rather than blocking the "
        "case on its own.",
        ["FATF", "AML", "CFT", "KYC", "UBO", "USD"]),
    Row("l4_concept", "dd_layer_4", "concept",
        "What does an ECCN mean in ARIA's export-control checks, and when does it drive escalation?",
        "An ECCN (Export Control Classification Number) is the category ARIA's tech_classifier assigns to a good under "
        "the US EAR, administered by BIS (and mirrored in the UK by ECJU on the Strategic Export Control List). It "
        "escalates when the item is controlled (e.g. Wassenaar or MTCR-listed dual-use), when the classification is "
        "not deterministic from the product description (a CCATS/commodity-classification referral), or when the "
        "destination or end-user is embargoed — driving a licence requirement such as a SIEL or OGEL.",
        ["ECCN", "EAR", "BIS", "ECJU", "Wassenaar", "MTCR", "SIEL"]),
    # ── Layer 5 — Digital ─────────────────────────────────────────────────────
    Row("l5_explain", "dd_layer_5", "explainer",
        "What does the digital stage of ARK-DD do, and how do its 5b and 5c sub-stages differ?",
        "Layer 5 (Digital) runs multilingual web search plus RAG, a neural pass, and — in deep mode — deep_research "
        "(thorough: 8 search angles x 3 articles) to build the open-source picture of the counterparty. Sub-stage 5b "
        "scores deception risk in the counterparty's own materials (pitch deck / claimed credentials), while 5c "
        "assesses commercial coherence — whether the jurisdiction, payment routing and stated business actually fit "
        "(a sector or geography mismatch signals a possible fronting arrangement).",
        ["Digital", "RAG", "deep_research", "multilingual", "deception", "commercial", "coherence",
         "counterparty"]),
    # ── Layer 6 — Synthesis ───────────────────────────────────────────────────
    Row("l6_explain", "dd_layer_6", "explainer",
        "What does the synthesis stage produce at the end of an ARK-DD run?",
        "Layer 6 (Synthesis) builds an ACH (Analysis of Competing Hypotheses) matrix over the layer outputs, rolls up "
        "the ghost score out of 20, and aggregates the worst-case risk classification across LOW/MEDIUM/HIGH (GREEN / "
        "AMBER / RED / HARD STOP). Any hard_stop finding anywhere forces HARD_STOP. It fires the SAR (Suspicious "
        "Activity Report) trigger when warranted and writes the BLUF (Bottom Line Up Front) for the operator.",
        ["Synthesis", "ACH", "Analysis", "Competing Hypotheses", "ghost", "HIGH", "SAR", "BLUF"]),
    Row("l6_concept", "dd_layer_6", "concept",
        "When does ARIA's synthesis fire a SAR trigger?",
        "The Layer 6 (Synthesis) SAR trigger fires when the case reaches a CRITICAL / HIGH risk pattern that meets a "
        "reporting threshold — for example a confirmed sanctions nexus, a PEP with unexplained source of funds, or a "
        "TBML/fraud indicator. It raises a Suspicious Activity Report (SAR) flag in the ACH synthesis and the BLUF so "
        "a compliance officer can file, rather than the system filing on its own.",
        ["SAR", "PEP", "CRITICAL", "HIGH", "Suspicious Activity Report", "BLUF", "ACH"]),
    # ── Layer 7 — ARK-DD report ───────────────────────────────────────────────
    Row("l7_explain", "dd_layer_7", "explainer",
        "How is the assembled ARK-DD report persisted, and what sections make it up?",
        "Layer 7 is the assembled ARK-DD report. The full report is persisted in Redis under "
        "crucix:dd:report:{run_id} with a 7-day TTL, a markdown render is written to the mem0 notebook "
        "(WhatsApp-ready) and the lifecycle is linked via trace_stream. Its sections are the BLUF, then the Identity, "
        "Network, Verification, Compliance, Digital and Synthesis sections plus the ACH matrix, with SKIPPED/ERROR "
        "layers surfaced honestly.",
        ["ARK-DD", "Redis", "TTL", "BLUF", "ACH", "Synthesis", "trace_stream"]),
    # ── Layer 8 — Counter-intelligence ────────────────────────────────────────
    Row("l8_explain", "dd_layer_8", "explainer",
        "What is the counter-intelligence stage of ARK-DD looking for that the earlier layers cannot see?",
        "Layer 8 (Counter-intelligence) sweeps the recent intel_ledger signals about the entity over a 30-day window "
        "for behavioural patterns the earlier layers miss: narrative-shift (positive press timed against a negative "
        "event), coordinated press (three or more Tier-3 sources publishing in the same window), and tier-"
        "contradiction (a Tier-1 source says listed while Tier-3 says clean). A composite score at or above 0.5 logs "
        "a WARNING alert; it is fail-open and never blocks the DD on its own.",
        ["intelligence", "coordinated", "contradiction", "Tier-3", "WARNING", "30 days", "narrative"]),
    Row("l8_concept", "dd_layer_8", "concept",
        "Give a concrete tier-contradiction pattern that ARIA's counter-intelligence stage would flag.",
        "A tier-contradiction is when authoritative and low-tier sources disagree about the same entity — for example "
        "a Tier-1 primary source such as the OFAC SDN list designates a Russian defence exporter while a cluster of "
        "Tier-3 Russian-language outlets describe it as clean and unremarkable. Layer 8 (Counter-intelligence) treats "
        "that Tier-1-listed vs Tier-3-clean split as a coordinated-narrative red flag and logs a WARNING for synthesis.",
        ["OFAC", "SDN", "Tier-3", "contradiction", "Russian", "WARNING", "coordinated"]),
    # ── Layer 9 — Sanctions divergence ────────────────────────────────────────
    Row("l9_explain", "dd_layer_9", "explainer",
        "What does the sanctions-divergence stage add on top of the Layer 1 screen?",
        "Layer 9 (Sanctions divergence) reads the per-list results and tells the operator the MEANING of a "
        "cross-jurisdiction split: an entity listed by US OFAC but not by UK OFSI, or the UN Security Council staying "
        "silent while the EU acts. It reports jurisdictions_listed vs jurisdictions_not_listed. It is informational "
        "(a WARNING) and does not block the DD, but an OFAC-only listing carries a USD-clearing / secondary-sanctions "
        "implication that Layer 1's presence/absence screen does not spell out.",
        ["DIVERGENCE", "OFAC", "OFSI", "UN", "EU", "USD", "WARNING", "jurisdictions"]),
    Row("l9_applied", "dd_layer_9", "applied",
        "OFAC lists an entity on the SDN list but the UK OFSI and EU consolidated lists do not. What does ARIA conclude from that divergence?",
        "Layer 9 (Sanctions divergence) concludes the lists DIVERGE: jurisdictions_listed = OFAC while "
        "jurisdictions_not_listed = OFSI and EU. Any US person or USD-clearing transaction is prohibited by the OFAC "
        "SDN designation regardless of UK/EU silence, and non-US parties still face secondary-sanctions exposure, so "
        "EU/OFSI silence must NOT be read as a clearance. It is logged as a WARNING for the compliance officer, not an "
        "automatic block.",
        ["DIVERGENCE", "OFAC", "SDN", "OFSI", "EU", "USD", "WARNING"],
        entities=[]),
    # ── Layer 10 — Forensic ───────────────────────────────────────────────────
    Row("l10_explain", "dd_layer_10", "explainer",
        "What does the forensic stage of ARK-DD test, and under what conditions does each test run?",
        "Layer 10 (Forensic) runs two conservative gates. The Benford's Law gate tests the first-digit distribution of "
        "the entity's financial figures for statistical anomaly, but only fires when there are at least 50 distinct "
        "values (below that it is statistically meaningless). The TBML transaction classifier screens caller-provided "
        "transaction line items for trade-based money-laundering patterns. When neither gate has enough data the "
        "layer self-skips, and its contribution to the final classification is deliberately conservative.",
        ["Benford", "Law", "TBML", "forensic", "transactions", "conservative", "statistically"]),
    Row("l10_concept", "dd_layer_10", "concept",
        "Why does ARIA gate the Benford's Law test on a minimum number of values, and what does a HIGH Benford tier mean?",
        "Benford's Law only holds over a large span of naturally occurring figures, so ARIA's Layer 10 (Forensic) "
        "requires at least 50 distinct values before it will run the test — below that the chi-square result is not "
        "statistically meaningful and would produce false anomalies. This conservative gating avoids flagging "
        "legitimate financial figures as manipulation. A HIGH Benford tier means the first-digit distribution deviates "
        "significantly from the Benford expectation (low p-value), so ARIA is statistically confident there is "
        "possible figure manipulation — treated as supplementary evidence, not on its own conclusive.",
        ["Benford", "statistically", "conservative", "HIGH", "confident", "financial"]),
    # ── Cross-cutting compliance / architecture concepts ──────────────────────
    Row("c_hardstop", "dd_layer_1", "concept",
        "What is a HARD STOP in ARIA's due-diligence engine and what causes one?",
        "A HARD STOP is ARIA's highest-severity outcome: a confirmed match against an authoritative sanctions list — "
        "OFAC SDN, UK OFSI, the EU Consolidated list (Council Regulation) or a UN Security Council designation — "
        "detected at Layer 1 (Identity). It short-circuits the remaining layers and forces the Layer 6 synthesis to "
        "classify the case as HARD_STOP, meaning do NOT proceed / do not onboard or transact until compliance clears "
        "the identity match.",
        ["HARD STOP", "OFAC", "SDN", "OFSI", "EU", "Council Regulation", "UN", "Identity"]),
    Row("c_ubo", "dd_layer_2", "concept",
        "In ARIA's methodology, what is a UBO and how does it relate to PSC and PEP screening?",
        "A UBO (ultimate beneficial owner) is the natural person who ultimately owns or controls an entity, mapped in "
        "Layer 2 (Network) from company-registry and PSC (People with Significant Control) data. ARIA walks the "
        "ownership chain to the UBO so that indirect sanctions exposure — for example the OFAC 50%-rule where a "
        "blocked person owns a majority — is caught, and it flags when a UBO or director is a PEP (politically exposed "
        "person) requiring enhanced due diligence.",
        ["UBO", "PSC", "PEP", "Network", "OFAC", "directors", "sanctioned"]),
    Row("c_ach", "dd_layer_6", "concept",
        "What is the ACH matrix in ARIA's synthesis and why is it used?",
        "ACH stands for Analysis of Competing Hypotheses, the structured technique ARIA uses in Layer 6 (Synthesis) to "
        "weigh the layer findings against multiple explanations rather than confirming a single one. Each hypothesis "
        "(e.g. legitimate counterparty vs front vs sanctioned nexus) is scored against the evidence from Layers 1-5, "
        "contradictions lower confidence, and the matrix drives the final LOW/MEDIUM/HIGH risk classification and the "
        "BLUF.",
        ["ACH", "Analysis", "Competing Hypotheses", "Synthesis", "HIGH", "MEDIUM", "BLUF"]),
    # ── Refusal / abstention rows (keep the v0.5 refusal regression from recurring) ──
    Row("r_officer", "dd_layer_1", "refusal",
        "Who is the current chief procurement officer of the fictional firm Draymoor Logistics SARL, and what is their national ID number?",
        "I cannot confirm the identity of a specific officeholder or a national ID number for that firm without an "
        "authoritative current source, and I will not fabricate a name or an ID. In ARK-DD terms this is a Layer 1 "
        "(Identity) / Layer 2 (Network) question that must be answered from a company registry (e.g. Companies House / "
        "PSC filings) or a sanctions/PEP screen, not from memory. Provide the registry record and I will screen it.",
        ["cannot confirm", "without", "will not fabricate", "Identity", "registry"],
        entities=["Draymoor Logistics SARL"]),
    Row("r_sanction", "dd_layer_9", "refusal",
        "State the exact date on which the fictional entity Corvane Holdings Ltd was added to the OFAC SDN list.",
        "I cannot confirm a specific SDN listing date for that entity without checking the authoritative source, and I "
        "will not fabricate a date. The correct ARK-DD step is a Layer 1 (Identity) screen against the OFAC SDN list "
        "and the Layer 9 sanctions-divergence check across OFSI/EU/UN; the designation date must come from the OFAC "
        "record itself. Point me at the listing and I will verify it.",
        ["cannot confirm", "without", "will not fabricate", "OFAC", "SDN", "Identity"],
        entities=["Corvane Holdings Ltd"]),
    Row("r_ubo", "dd_layer_2", "refusal",
        "Name the ultimate beneficial owner and their exact shareholding percentage for the fictional company Pellandor Defence Systems.",
        "I cannot confirm the ultimate beneficial owner (UBO) or an exact shareholding percentage for that company "
        "without an authoritative source, and I will not fabricate a name or a figure. In ARK-DD this is a Layer 2 "
        "(Network) determination that must come from the company registry and PSC (People with Significant Control) "
        "filings, not from memory. Give me the registry or PSC record and I will walk the ownership to the UBO.",
        ["cannot confirm", "without", "will not fabricate", "UBO", "PSC", "Network", "registry"],
        entities=["Pellandor Defence Systems"]),
]


# ══════════════════════════════════════════════════════════════════════════════
# 2) TEACHER — closed-book: NO evidence in the training prompt. The teacher is given
#    the extracted framework facts (golden + canonical vocab) to author a natural,
#    varied answer the model will learn to reproduce FROM KNOWLEDGE.
# ══════════════════════════════════════════════════════════════════════════════
_TEACHER_SYS = (
    "You are ARIA, an autonomous due-diligence analyst. You are writing a TRAINING TARGET: a "
    "concise, decision-grade, CLOSED-BOOK answer that teaches your own ARK-DD methodology from "
    "knowledge. The trainee's prompt contains NO evidence — do NOT invent case facts, do NOT add "
    "[from ...] citations. You are given REFERENCE FACTS about the real ARK-DD framework; restate "
    "them accurately in your own words, VARYING the phrasing (do not copy verbatim). You MUST use "
    "the canonical terminology listed. Be specific and correct; keep it to a tight paragraph."
)
_TEACHER_SYS_REFUSAL = (
    "You are ARIA, an autonomous due-diligence analyst answering CLOSED-BOOK with NO evidence. The "
    "user asks for a SPECIFIC fact (an officeholder, an ID, or an exact date) that you have no "
    "authoritative source for. Produce an HONEST ABSTENTION: state you cannot confirm it without an "
    "authoritative current source and that you will NOT fabricate a name/ID/number. Then name the "
    "correct ARK-DD step / source that would settle it. Do NOT guess any specific value. Use the "
    "canonical terminology listed. One tight paragraph."
)


def _teacher_user(row: Row) -> str:
    return (
        f"[REFERENCE FACTS — the real ARK-DD methodology to teach]\n{row.golden}\n\n"
        f"[CANONICAL TERMS you MUST include]\n{', '.join(row.keywords)}\n\n"
        f"[TRAINEE QUESTION — answer closed-book, from knowledge]\n{row.question}"
    )


async def _teacher(api_key: str, row: Row) -> tuple[bool, str]:
    import httpx
    sys_prompt = _TEACHER_SYS_REFUSAL if row.kind == "refusal" else _TEACHER_SYS
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": _teacher_user(row)},
        ],
        "max_tokens": 480, "temperature": 0.4,
    }
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"}
    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await asyncio.wait_for(
                client.post(f"{DEEPSEEK_URL}/chat/completions", headers=headers, json=body),
                timeout=95.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        data = resp.json()
        return True, (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    except Exception as e:
        return False, str(e)[:160]


# ══════════════════════════════════════════════════════════════════════════════
# 3) REWARD — keyword coverage (replaces open-book grounding) + correctness (judge).
# ══════════════════════════════════════════════════════════════════════════════
def _norm_alnum(s: str) -> str:
    """Lowercase + collapse non-alphanumerics to single spaces so 'HARD-STOP',
    'HARD STOP' and 'hard  stop' all compare equal."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def _covers(answer_norm: str, kw: str) -> bool:
    k = _norm_alnum(kw)
    if not k:
        return False
    if " " in k:  # multi-word phrase — substring on normalised text
        return k in answer_norm
    # single token — word-boundary on normalised (space-delimited) text
    return re.search(r"(?:^| )" + re.escape(k) + r"(?:$| )", answer_norm) is not None


def reward_keyword_coverage(answer: str, keywords: list) -> dict:
    an = _norm_alnum(answer)
    hits = sorted({kw for kw in keywords if _covers(an, kw)})
    missing = sorted(set(keywords) - set(hits))
    frac = len(hits) / max(1, len(keywords))
    return {
        "hits": hits, "n_hits": len(hits), "n_total": len(keywords),
        "missing": missing, "score": round(frac, 3),
        "pass": (frac >= COVERAGE_MIN and len(hits) >= min(MIN_KW_HITS, len(keywords))),
    }


# refusal answers must abstain AND must not fabricate specifics.
_ABSTAIN_MARKERS = (
    "cannot confirm", "can't confirm", "cannot provide", "will not fabricate",
    "won't fabricate", "without an authoritative", "without a source", "not fabricate",
    "cannot verify", "unable to verify", "no authoritative source",
)
_FABRICATION_MARKERS = re.compile(
    r"\bthe (chief|current) [a-z ]+ officer is\b|"
    r"\bnational id (?:number )?is\b|"
    r"\bwas added to the ofac sdn list on\b|"
    r"\bon \d{1,2}\s+\w+\s+\d{4}\b",
    re.I,
)


def reward_refusal(answer: str) -> dict:
    a = (answer or "").lower()
    abstained = any(m in a for m in _ABSTAIN_MARKERS)
    fabricated = bool(_FABRICATION_MARKERS.search(answer or ""))
    return {"abstained": abstained, "fabricated": fabricated,
            "score": 1.0 if (abstained and not fabricated) else 0.0,
            "pass": abstained and not fabricated}


# ══════════════════════════════════════════════════════════════════════════════
# 4) SFT + DPO writers.  SFT schema mirrors data/training/aria_grounded_v3.jsonl,
#    grounded=False (closed-book — there is no retrieved context).
# ══════════════════════════════════════════════════════════════════════════════
def sft_row(row: Row, answer: str, reward: dict) -> dict:
    return {
        "messages": [
            {"role": "user", "content": row.question},
            {"role": "assistant", "content": answer.strip()},
        ],
        "topic": row.topic,
        "grounded": False,
        "label": "dd_framework_knowledge",
        "kind": row.kind,
        "source": SOURCE_LABEL,
        "reward": reward,
    }


def _generic_rejected(row: Row) -> str:
    """DPO 'rejected': a plausible-but-WRONG generic answer that omits the canonical
    vocabulary (for refusal rows: a FABRICATED specific, which the model must learn
    to avoid). Deterministic + zero-cost; provably lower keyword coverage."""
    if row.kind == "refusal":
        return ("The chief procurement officer is Jonathan Meyer and the national ID number is "
                "these details are on file; the entity was added to the OFAC SDN list on 14 March "
                "2023.")  # fabricated specifics — the negative behaviour to suppress
    n = re.sub(r"^([a-z0-9_]+)_.*$", r"\1", row.rid)
    return ("This stage runs an automated due-diligence check on the entity, gathers whatever "
            "information is available online, flags anything that looks risky, and the analyst "
            "then reviews the results and decides whether it is safe to proceed with the "
            f"relationship. It is a standard step in the {n} review process.")


def dpo_row(row: Row, chosen: str, rejected: str) -> dict:
    return {
        "prompt": row.question,
        "chosen": chosen.strip(),
        "rejected": (rejected or "").strip(),
        "topic": row.topic,
        "kind": row.kind,
        "source": SOURCE_LABEL + "_dpo",
    }


# ══════════════════════════════════════════════════════════════════════════════
REFUSAL_FRACTION = 0.17   # ~15-20% abstention rows (keeps the v0.5 refusal regression away)


def _select_rows(count: int, variants: int) -> list:
    """Round-robin over the ROWS seeds so a small --count still spans many layers AND
    always carries ~REFUSAL_FRACTION abstention rows (regardless of ROWS order — the
    refusal seeds live at the end of the list). Full runs raise --variants to
    paraphrase-fan-out per seed via teacher temperature."""
    refusals = [r for r in ROWS if r.kind == "refusal"]
    others = [r for r in ROWS if r.kind != "refusal"]

    def _emit(seq, cap, v):
        out = []
        for i in range(cap):
            r = seq[i % len(seq)]
            vv = i // len(seq) if v is None else v
            if vv > 0 or (v is None and i >= len(seq)):
                r = Row(f"{r.rid}_v{vv}", r.topic, r.kind, r.question, r.golden,
                        r.keywords, list(r.entities))
            out.append(r)
        return out

    n_ref = max(1, round(count * REFUSAL_FRACTION)) if refusals else 0
    n_other = count - n_ref
    picked_ref = _emit(refusals, n_ref, None) if n_ref else []
    picked_other = _emit(others, n_other, None) if n_other else []

    # interleave so refusals are spread through the set, not clustered at the end
    out: list = []
    ref_i = other_i = 0
    step = max(1, round(len(picked_other) / max(1, len(picked_ref)))) if picked_ref else 1
    for i in range(count):
        if picked_ref and ref_i < len(picked_ref) and (i % (step + 1) == step or other_i >= len(picked_other)):
            out.append(picked_ref[ref_i]); ref_i += 1
        elif other_i < len(picked_other):
            out.append(picked_other[other_i]); other_i += 1
        elif ref_i < len(picked_ref):
            out.append(picked_ref[ref_i]); ref_i += 1
    return out[:count]


async def build(count: int, out: Path, emit_dpo: bool, concurrency: int, variants: int) -> int:
    api_key = os.environ.get("DEEPSEEK_API_KEY")
    if not api_key and (REPO / ".env").exists():
        for ln in (REPO / ".env").read_text(encoding="utf-8").splitlines():
            if ln.startswith("DEEPSEEK_API_KEY="):
                api_key = ln.split("=", 1)[1].strip().strip('"').strip("'"); break
    if not api_key:
        print("FATAL: DEEPSEEK_API_KEY not set / not in .env", file=sys.stderr)
        return 1

    judge_llm = _JudgeLLM(api_key)
    exact, toksets, ents = load_eval_guards()
    print(f"[gate] eval guards loaded: {len(exact)} exact norms, {len(toksets)} token sets, "
          f"{len(ents)} entity names")

    rows = _select_rows(count, variants)
    print(f"[gen] selected {len(rows)} closed-book framework rows over "
          f"{len({r.topic for r in rows})} dd_layer topics "
          f"(kinds: {sorted({r.kind for r in rows})})")

    sem = asyncio.Semaphore(max(1, concurrency))
    stats = {"generated": len(rows), "teacher_err": 0, "rej_contam": 0,
             "rej_keyword": 0, "rej_refusal": 0, "rej_correct": 0, "kept": 0}
    kept_sft, kept_dpo, examples = [], [], []

    async def one(row: Row):
        async with sem:
            ok, ans = await _teacher(api_key, row)
        if not ok or not ans.strip():
            stats["teacher_err"] += 1
            print(f"  [{row.rid}] TEACHER ERROR: {ans[:80]}")
            return

        # (contamination FIRST — never spend judge budget on a tainted row)
        contam = contamination_check(row.question, ans, row.entities, exact, toksets, ents)
        if not contam["clean"]:
            stats["rej_contam"] += 1
            print(f"  [{row.rid}] REJECT contamination: {contam['reasons']}")
            return

        kw = reward_keyword_coverage(ans, row.keywords)
        reward = {"keyword_coverage": kw, "contamination": contam}

        if row.kind == "refusal":
            ref = reward_refusal(ans)
            reward["refusal"] = ref
            if not ref["pass"]:
                stats["rej_refusal"] += 1
                print(f"  [{row.rid}] REJECT refusal: abstained={ref['abstained']} "
                      f"fabricated={ref['fabricated']}")
                return
            # refusal rows still keyword-check softly (registry/Identity vocab)
            if not kw["pass"]:
                stats["rej_keyword"] += 1
                print(f"  [{row.rid}] REJECT keyword(refusal): hits={kw['n_hits']}/{kw['n_total']} "
                      f"missing={kw['missing']}")
                return
        else:
            if not kw["pass"]:
                stats["rej_keyword"] += 1
                print(f"  [{row.rid}] REJECT keyword: hits={kw['n_hits']}/{kw['n_total']} "
                      f"missing={kw['missing']}")
                return

        async with sem:
            c = await reward_correctness(judge_llm, row.question, row.golden, ans)
        reward["correctness"] = c
        if not c["pass"]:
            stats["rej_correct"] += 1
            print(f"  [{row.rid}] REJECT correctness: verdict={c['verdict']} ({c['reason'][:60]})")
            return

        stats["kept"] += 1
        kept_sft.append(sft_row(row, ans, reward))
        print(f"  [{row.rid}] KEEP  kw={kw['n_hits']}/{kw['n_total']} verdict={c['verdict']} "
              f"kind={row.kind}")

        if emit_dpo:
            rej = _generic_rejected(row)
            rej_kw = reward_keyword_coverage(rej, row.keywords)
            # provenance guarantee: the rejected answer is strictly worse on keywords
            kept_dpo.append({**dpo_row(row, ans, rej),
                             "chosen_kw": kw["n_hits"], "rejected_kw": rej_kw["n_hits"]})

        examples.append((row, ans, reward))

    await asyncio.gather(*[one(r) for r in rows])

    if not kept_sft:
        print("FATAL: 0 rows survived the reward gates — nothing to write.", file=sys.stderr)
        return 3

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in kept_sft) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"\n[write] {len(kept_sft)} SFT rows -> {out}")

    if emit_dpo and kept_dpo:
        dpo_path = Path(str(out).replace(".jsonl", "_dpo.jsonl"))
        dpo_path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in kept_dpo) + "\n",
                            encoding="utf-8", newline="\n")
        print(f"[write] {len(kept_dpo)} DPO pairs (chosen=framework / rejected=generic-or-fabricated) "
              f"-> {dpo_path}")

    # EXTERNAL contamination gate on the whole output — FAIL LOUD.
    clean, report = run_preflight(out)
    print("\n" + "═" * 70 + "\n[CONTAMINATION PREFLIGHT]\n" + report + "═" * 70)
    if not clean:
        print("FATAL: preflight_eval_contamination reported overlap > 0.01 — the sample is "
              "CONTAMINATED and MUST NOT be trained on.", file=sys.stderr)
        return 4

    print("\n[SUMMARY]")
    for k in ("generated", "teacher_err", "rej_contam", "rej_keyword",
              "rej_refusal", "rej_correct", "kept"):
        print(f"  {k:14s} = {stats[k]}")
    print(f"  yield          = {stats['kept'] / max(1, stats['generated']) * 100:.0f}%")

    if examples:
        row, ans, reward = examples[0]
        print("\n" + "═" * 70 + "\n[FULL EXAMPLE]\n" + "═" * 70)
        print("PROMPT (closed-book — NO evidence given to the model):\n" + row.question)
        print("\nANSWER (SFT target):\n" + ans.strip())
        print("\nCANONICAL KEYWORDS COVERED: "
              + ", ".join(reward["keyword_coverage"]["hits"]))
        print("\nREWARD COMPONENTS:\n" + json.dumps(reward, ensure_ascii=False, indent=2))

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="v0.6 closed-book DD-framework corpus builder (R-F2506)")
    ap.add_argument("--count", type=int, default=18, help="number of framework rows (small sample default)")
    ap.add_argument("--out", type=Path,
                    default=REPO / "data" / "training" / "aria_dd_framework_v06_sample.jsonl")
    ap.add_argument("--emit-dpo", action="store_true", help="also emit DPO chosen/rejected pairs")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--variants", type=int, default=1,
                    help="paraphrase passes per seed for full runs (temperature-varied)")
    args = ap.parse_args()
    return asyncio.run(build(args.count, args.out, args.emit_dpo, args.concurrency, args.variants))


if __name__ == "__main__":
    raise SystemExit(main())
