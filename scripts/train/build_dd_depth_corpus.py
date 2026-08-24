"""build_dd_depth_corpus — R-F2498 — v0.5 DD-DEPTH training-corpus builder.

WHY THIS EXISTS (verified context)
══════════════════════════════════
The sovereign reasoning model's one release-blocking gap is DD-finding DEPTH
(eval dd_layer ≈ 0.13, sanctions_divergence ≈ 0.24 — see data/eval_frozen/
aria_eval_500q.jsonl topics). That is a CONTENT gap, not a prompt gap: the clean
grounded corpus (data/training/aria_grounded_v3.jsonl, DeepSeek-distilled) is
dominated by refusal/grounding/abstention rows, NOT multi-hop investigative DD
reasoning. Real DD reports are not a viable source (local dd_vault.db is ~empty;
live reports are user-scoped PII). So v0.5 is a REWARD-VERIFIED DISTILLATION of
NEW multi-hop DD-depth traces over SYNTHETIC entities.

Each training row teaches the SKILL the eval measures: layered investigative DD —
  entity resolution (L1) -> network/UBO (L2) -> verification (L3) ->
  sanctions/PEP/export-control (L4) -> risk synthesis (L6) -> recommended action.
Grounding is REAL: the teacher answers ONLY from a synthetic CASE FILE (labelled
[Source: ...] evidence, same shape as the open-book eval) and cites [from ...],
exactly like build_grounded_corpus.py. Depth is REAL: the case files are authored
so the correct conclusion REQUIRES connecting >=3 hops (e.g. target-co owned by
holdco owned by an SDN person -> OFAC 50%-rule exposure).

REUSED (not reinvented) REAL COMPONENTS
  - teacher path: build_grounded_corpus._deepseek  (the same DeepSeek distiller
    that produced aria_grounded_v*)                 [scripts/train]
  - judge:        aria_service.intel.eval_judge.judge_answer (R-F1396 LLM judge)
  - contamination gate: scripts/train/preflight_eval_contamination.py (--max-overlap 0.01)
  - SFT schema:   identical to data/training/aria_grounded_v3.jsonl

REWARD (a trace is KEPT only if ALL gates pass — components emitted per example)
  1. grounding : answer cites >= MIN_CITES inline [from <label>] AND every cited
                 label exists in the case-file context (NO invented sources).
  2. depth     : >= MIN_LAYERS distinct DD layers present (section detection).
  3. correctness: eval_judge.judge_answer(candidate vs AUTHORED golden) verdict in
                 {correct, partial} (wrong is rejected). Golden is authored from the
                 same facts the context encodes, so the judge grades factual agreement.

CONTAMINATION GATE (fail-loud)
  - internal, per example: exact-normalised + token-Jaccard(>=0.75) vs EVERY eval
    question (frozen + openbook) + eval entity-name blocklist. Reject on any hit.
  - external, on the whole output: runs preflight_eval_contamination.py; any
    overlap > 0.01 raises SystemExit (the run FAILS, no file is trusted).

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
FULL-RUN RECIPE (this run is a SMALL SAMPLE only — no GPU, minimal LLM cost)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Sample (this run):  --count 16   -> ~3 DeepSeek calls/example (teacher + judge +
                      shallow-reject variant) ≈ 48 calls ≈ $0.05-0.15.
  Full corpus:        --count 600  over the 5 scenario families (add param-sets /
                      raise per-family instances). ~1,800 calls ≈ $2-6 on DeepSeek
                      (deepseek-chat) — inside the §24 weekly LLM budget ($8-18/wk),
                      run ONCE as a data-prep step (NOT a GPU/RunPod run).
                      Expected yield after reward gates ≈ 55-75% -> ~350-450 SFT rows.
  Merge:              concatenate the kept SFT rows with aria_grounded_v3.jsonl for the
                      next SFT cycle; ALWAYS re-run preflight over the merged file.
  DPO pairing (stub): every kept example already emits chosen (deep grounded answer)
                      + rejected (shallow/ungrounded variant). --emit-dpo writes those
                      as {prompt, chosen, rejected} rows -> plug into
                      scripts/train/prepare_dpo.py (same shape as aria_dpo_pairs_v1_str).

Usage (this sample run):
  python scripts/train/build_dd_depth_corpus.py \
     --count 16 \
     --out data/training/aria_dd_depth_v05_sample.jsonl \
     --emit-dpo
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

# ── REUSE the real teacher distiller (do NOT reinvent a client) ───────────────
from scripts.train.build_grounded_corpus import _deepseek, DEEPSEEK_URL  # noqa: E402
# ── REUSE the real LLM judge ──────────────────────────────────────────────────
from aria_service.intel import eval_judge  # noqa: E402

EVAL_FROZEN = REPO / "data" / "eval_frozen" / "aria_eval_500q.jsonl"
EVAL_OPENBOOK = REPO / "data" / "eval_reports" / "aria_eval_500q_openbook.jsonl"
PREFLIGHT = REPO / "scripts" / "train" / "preflight_eval_contamination.py"

MIN_CITES = 3          # grounding: >= this many inline [from ...] citations
MIN_LAYERS = 3         # depth: >= this many distinct DD layers present
JACCARD_MAX = 0.75     # contamination: reject prompt/answer with fuzzy overlap >= this
SOURCE_LABEL = "dd_depth_v05"

# DD-layer section markers the teacher is asked to emit; depth = how many appear.
_LAYER_PATTERNS = {
    "identity":        re.compile(r"identity|entity resolution|layer\s*1|who (?:is|are)", re.I),
    "network_ubo":     re.compile(r"network|ubo|ultimate beneficial|ownership|director|shareholder|layer\s*2", re.I),
    "verification":    re.compile(r"verif|cross-?source|corrobor|discrepan|layer\s*3", re.I),
    "sanctions_pep":   re.compile(r"sanction|ofac|ofsi|\beu\b|sdn|pep|embargo|export control|end-?user|layer\s*4", re.I),
    "risk_synthesis":  re.compile(r"risk (?:rating|synthesis|assessment)|overall risk|residual risk|layer\s*6", re.I),
    "recommended_action": re.compile(r"recommend|next step|action|escalat|must (?:not|stop)|do not proceed|halt", re.I),
}

# ══════════════════════════════════════════════════════════════════════════════
# 1) SYNTHETIC ENTITIES — deliberately invented, NON-colliding with the eval sets.
#    (The contamination gate is the backstop; these are the by-construction avoidance.)
# ══════════════════════════════════════════════════════════════════════════════
_COMPANIES = [
    "Valmoreth Trading FZE", "Corvane Holdings Ltd", "Draymoor Logistics SARL",
    "Pellandor Defence Systems", "Quintaria Metals BV", "Serevane Shipping Pte",
    "Ashkellon Group Holdings", "Bracklowe Industrial LLC", "Merovane Procurement DAC",
    "Thalvic Aerospace GmbH", "Oskenar Freight OÜ", "Nembrec Systems Kft",
]
_PEOPLE = [
    "Dariel Voss", "Ingrid Halvorsen-Kray", "Emeka Osunde", "Ruslan Petrovar",
    "Marisol Aguirre-Delgado", "Tomas Brenholt", "Nadia Farouk-Zayn", "Kwame Adjeman",
]
# corridors chosen to AVOID eval corridors (eval uses Bulgaria->Burkina Faso, Saudi,
# Mexico, Indonesia, South Africa, Senegal, Angola, Mozambique, UAE).
_CORRIDORS = [
    ("Serbia", "Republic of Chad"), ("Czechia", "Republic of Guinea"),
    ("Slovakia", "Central African Republic"), ("Kazakhstan", "Republic of Yemen"),
]


@dataclass
class Case:
    """One authored case file: prompt + grounded context + AUTHORED golden."""
    cid: str
    topic: str
    question: str
    context: str          # labelled [Source: ...] evidence
    golden: str           # authored correct multi-hop synthesis (correctness oracle)
    entities: list = field(default_factory=list)


def _src(label: str, text: str) -> str:
    return f"[Source: {label}]\n{text}"


# ── Scenario family 1: shell-layering -> indirect OFAC exposure (50% rule) ─────
def _tpl_shell_ubo(i: int) -> Case:
    co = _COMPANIES[i % len(_COMPANIES)]
    hold = _COMPANIES[(i + 3) % len(_COMPANIES)]
    ubo = _PEOPLE[i % len(_PEOPLE)]
    jur = ["Malta", "Cyprus", "Mauritius", "UAE (RAK)"][i % 4]
    ctx = "\n\n".join([
        _src(f"corporate_registry:{jur}",
             f"{co} is registered in {jur}. Sole shareholder of record: {hold}. "
             f"Incorporated 2022; nominee director listed."),
        _src(f"ubo_filing:{hold}",
             f"{hold}'s ultimate beneficial owner (holding 75%) is {ubo}. "
             f"{hold} shares its registered agent and address with two other holding vehicles."),
        _src("ofac_sdn_extract",
             f"{ubo} is designated on the OFAC SDN list (proliferation programme); "
             f"all property and interests in property blocked."),
        _src("adverse_media:trade-press",
             f"{co} was named in a 2025 trade-press report on suspected transhipment of "
             f"controlled goods through free-zone intermediaries."),
    ])
    golden = (
        f"IDENTITY: {co} ({jur}) resolves to a real registry entry, but with a nominee "
        f"director — a control-opacity flag [from corporate_registry:{jur}].\n"
        f"NETWORK/UBO: {co} is wholly owned by {hold}, whose 75% ultimate beneficial owner "
        f"is {ubo}; {hold} reuses a registered agent/address across multiple vehicles — a "
        f"classic layering signal [from ubo_filing:{hold}].\n"
        f"SANCTIONS: {ubo} is OFAC SDN-designated, so under the OFAC 50% Rule {hold} and in "
        f"turn {co} are themselves blocked property despite not being listed by name "
        f"[from ofac_sdn_extract].\n"
        f"VERIFICATION: the transhipment allegation is single-source adverse media and must "
        f"be corroborated, but it aligns with the ownership risk [from adverse_media:trade-press].\n"
        f"RISK SYNTHESIS: HIGH — indirect but binding sanctions nexus plus layering.\n"
        f"RECOMMENDED ACTION: do NOT onboard/transact; freeze, run enhanced DD on the wider "
        f"{hold} vehicle cluster, and file the OFAC 50%-rule determination."
    )
    return Case(f"shell_ubo_{i}", "dd_layer_shell_ubo",
                f"Assess {co}, a {jur} trading company, for sanctions and ownership risk. "
                f"Walk the network to its ultimate beneficial owner and give a decision.",
                ctx, golden, [co, hold, ubo])


# ── Scenario family 2: sanctions-corridor divergence (OFAC vs EU vs OFSI) ──────
def _tpl_sanctions_divergence(i: int) -> Case:
    ent = _COMPANIES[(i + 1) % len(_COMPANIES)]
    sub_hq = ["Delaware, US", "Frankfurt, Germany", "London, UK"][i % 3]
    ctx = "\n\n".join([
        _src("ofac_sdn_extract", f"{ent} appears on the OFAC SDN list (Russia-related programme)."),
        _src("eu_consolidated_extract", f"{ent} does NOT appear on the EU consolidated sanctions list."),
        _src("ofsi_extract", f"{ent} does NOT appear on the UK OFSI consolidated list."),
        _src("corporate_registry:group",
             f"The prospective counterparty is a {sub_hq}-headquartered subsidiary of a "
             f"multinational group with US-person directors on its board."),
    ])
    golden = (
        f"IDENTITY: counterparty is a {sub_hq} subsidiary with US-person board members "
        f"[from corporate_registry:group].\n"
        f"SANCTIONS DIVERGENCE: {ent} is OFAC SDN-listed but absent from both the EU "
        f"consolidated list and UK OFSI [from ofac_sdn_extract] [from eu_consolidated_extract] "
        f"[from ofsi_extract]. The lists DIVERGE.\n"
        f"COMPLIANCE POSITION: any US person or US-nexus transaction is PROHIBITED regardless of "
        f"EU/UK status; the {sub_hq} entity's US-person directors create direct US jurisdiction, "
        f"and non-US entities still face CAATSA-style secondary-sanctions exposure for material "
        f"dealings.\n"
        f"VERIFICATION: confirm list identity (name/DOB/registration) before acting on the hit.\n"
        f"RISK SYNTHESIS: HIGH for any US-touch; MEDIUM secondary-sanctions risk for EU/UK legs.\n"
        f"RECOMMENDED ACTION: block the US-nexus leg, escalate to compliance, and do not rely on "
        f"EU/UK silence as a clearance."
    )
    return Case(f"sanc_div_{i}", "sanctions_divergence_synth",
                f"{ent} is on the OFAC SDN list but not on the EU or UK OFSI lists. A "
                f"{sub_hq}-headquartered subsidiary wants to trade with it — what is our position?",
                ctx, golden, [ent])


# ── Scenario family 3: arms end-user / diversion risk ─────────────────────────
def _tpl_enduser_diversion(i: int) -> Case:
    origin, dest = _CORRIDORS[i % len(_CORRIDORS)]
    broker = _COMPANIES[(i + 5) % len(_COMPANIES)]
    goods = ["surplus 7.62mm ammunition", "refurbished APC hulls",
             "night-vision modules", "man-portable radios (dual-use)"][i % 4]
    ctx = "\n\n".join([
        _src("export_licence_record",
             f"Proposed export: {goods} from {origin} to a stated end-user in {dest}, "
             f"brokered by {broker}. No end-use certificate attached."),
        _src("shipping_manifest",
             f"Routing shows a transhipment stop and a consignee change mid-voyage to a "
             f"free-zone forwarder not named on the licence application."),
        _src("embargo_reference",
             f"{dest} is subject to an active UN/EU arms-embargo review; several destinations in "
             f"the region carry Category-A diversion risk."),
        _src("adverse_media:ngo-report",
             f"An NGO report links {broker} to a prior re-export that breached end-user undertakings."),
    ])
    golden = (
        f"IDENTITY: broker {broker} is arranging {goods} on the {origin}->{dest} corridor "
        f"[from export_licence_record].\n"
        f"EXPORT-CONTROL/COMPLIANCE: STOP — there is no end-use certificate, {dest} is under an "
        f"active embargo review, and the manifest shows a consignee change to an unlisted free-zone "
        f"forwarder — three diversion red flags [from export_licence_record] [from shipping_manifest] "
        f"[from embargo_reference].\n"
        f"VERIFICATION: {broker} has prior adverse media for breaching end-user undertakings — the "
        f"pattern corroborates the diversion risk and needs independent confirmation "
        f"[from adverse_media:ngo-report].\n"
        f"RISK SYNTHESIS: HIGH diversion / embargo-breach risk.\n"
        f"RECOMMENDED ACTION: halt the shipment; obtain and verify an end-use certificate and the "
        f"true final consignee; screen against the embargo list and apply for the correct licence "
        f"(e.g. SIEL/OIEL) before any movement; escalate the broker's history to compliance."
    )
    return Case(f"enduser_{i}", "dd_layer_enduser_diversion",
                f"We are being offered a deal to move {goods} from {origin} to {dest} via broker "
                f"{broker}. What do we need to check first?",
                ctx, golden, [broker, origin, dest])


# ── Scenario family 4: procurement fraud / bid-rigging (related party) ─────────
def _tpl_procurement_fraud(i: int) -> Case:
    winner = _COMPANIES[(i + 2) % len(_COMPANIES)]
    loser = _COMPANIES[(i + 6) % len(_COMPANIES)]
    director = _PEOPLE[(i + 2) % len(_PEOPLE)]
    ctx = "\n\n".join([
        _src("corporate_registry:tender",
             f"Winning bidder {winner} and losing bidder {loser} share a common director, "
             f"{director}, appointed to both within the same quarter."),
        _src("bid-price-analysis",
             f"Across the tender the losing bids cluster 3-5% above {winner}'s, and first-digit "
             f"frequencies deviate from a Benford expectation — a bid-suppression signature."),
        _src("bank_wire_record",
             f"A payment routed from {winner} to a consultancy beneficially owned by {director} "
             f"two weeks after award; no deliverable on file."),
    ])
    golden = (
        f"IDENTITY/NETWORK: winner {winner} and losing bidder {loser} are related parties — a "
        f"shared director {director} appointed to both — so the tender was not arm's-length "
        f"[from corporate_registry:tender].\n"
        f"VERIFICATION: the bid prices show cover-bid clustering and a Benford deviation consistent "
        f"with bid-rigging [from bid-price-analysis].\n"
        f"COMPLIANCE/RISK: a post-award wire from {winner} to {director}'s consultancy with no "
        f"deliverable indicates a likely kickback [from bank_wire_record]. RISK SYNTHESIS: HIGH — "
        f"collusive tendering plus corruption indicators.\n"
        f"RECOMMENDED ACTION: void/quarantine the award, preserve evidence, make a suspicious-"
        f"activity/ bribery referral, and bar the related bidders pending investigation."
    )
    return Case(f"procfraud_{i}", "dd_layer_procurement_fraud",
                f"Review this tender: {winner} won and {loser} came second. Is there a fraud or "
                f"integrity concern, and what should we do?",
                ctx, golden, [winner, loser, director])


# ── Scenario family 5: PEP network / director-walk to sanctioned entity ───────
def _tpl_pep_network(i: int) -> Case:
    ent = _COMPANIES[(i + 4) % len(_COMPANIES)]
    director = _PEOPLE[(i + 4) % len(_PEOPLE)]
    hop = _COMPANIES[(i + 7) % len(_COMPANIES)]
    ctx = "\n\n".join([
        _src("corporate_registry:directors",
             f"{ent}'s controlling director {director} also sits on the board of {hop}."),
        _src("pep_database",
             f"{director} is a politically exposed person — a sitting deputy minister's "
             f"immediate family member (PEP by association)."),
        _src("ofac_sdn_extract",
             f"{hop} is on the OFAC SDN list (defence-sector programme)."),
    ])
    golden = (
        f"IDENTITY: subject entity {ent}, controlled by director {director} "
        f"[from corporate_registry:directors].\n"
        f"NETWORK/UBO: a one-hop director walk connects {director} to {hop} "
        f"[from corporate_registry:directors].\n"
        f"SANCTIONS/PEP: {director} is a PEP by association [from pep_database] AND the connected "
        f"entity {hop} is OFAC SDN-listed, so {ent} sits one hop from a sanctioned party "
        f"[from ofac_sdn_extract].\n"
        f"RISK SYNTHESIS: HIGH — combined PEP exposure and a 1-hop sanctions nexus.\n"
        f"RECOMMENDED ACTION: apply enhanced due diligence, establish source of wealth/funds, "
        f"screen the full board, and escalate the sanctions proximity to compliance before onboarding."
    )
    return Case(f"pep_net_{i}", "dd_layer_pep_network",
                f"Run a network check on {ent} and its director {director}. Any PEP or sanctions "
                f"exposure, and what do we do?",
                ctx, golden, [ent, director, hop])


_TEMPLATES = [
    _tpl_shell_ubo, _tpl_sanctions_divergence, _tpl_enduser_diversion,
    _tpl_procurement_fraud, _tpl_pep_network,
]


def build_cases(count: int) -> list:
    cases, i = [], 0
    while len(cases) < count:
        tpl = _TEMPLATES[i % len(_TEMPLATES)]
        cases.append(tpl(i // len(_TEMPLATES)))
        i += 1
    return cases[:count]


# ══════════════════════════════════════════════════════════════════════════════
# 2) TEACHER — grounded, multi-hop, sectioned answer over the case file.
#    Reuses build_grounded_corpus._deepseek (same DeepSeek distiller).
# ══════════════════════════════════════════════════════════════════════════════
_TEACHER_SYS = (
    "You are producing a TRAINING TARGET for a grounded, multi-hop due-diligence "
    "analyst. Answer the QUESTION using ONLY the facts in CONTEXT. You MUST reason in "
    "LAYERS and label them, covering at least: IDENTITY, NETWORK/UBO, VERIFICATION, "
    "SANCTIONS/PEP/EXPORT-CONTROL, RISK SYNTHESIS, and RECOMMENDED ACTION. CONNECT the "
    "hops (do not just restate facts): if ownership/control links a subject to a "
    "sanctioned or PEP party, state the consequence (e.g. OFAC 50%-rule exposure, "
    "diversion risk). Cite EVERY material claim inline as [from <label>] using ONLY the "
    "source labels that appear in CONTEXT. Do NOT invent facts, names, numbers, or "
    "sources. Be concise and decision-grade."
)


def _user_block(question: str, context: str) -> str:
    return (
        "[CONTEXT — answer ONLY from this evidence; cite inline as [from <source>]; "
        "reason in labelled DD layers and connect the hops]\n"
        f"{context}\n\n[QUESTION]\n{question}"
    )


async def _teacher(api_key: str, question: str, context: str) -> tuple[bool, str]:
    # Reuse the exact DeepSeek call shape from build_grounded_corpus, but with the
    # multi-hop layered system prompt. _deepseek uses build_grounded_corpus._SYS; we
    # want the layered sys, so call DeepSeek with the same client contract here.
    import httpx
    body = {
        "model": "deepseek-chat",
        "messages": [
            {"role": "system", "content": _TEACHER_SYS},
            {"role": "user", "content": _user_block(question, context)},
        ],
        "max_tokens": 900, "temperature": 0.2,
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


async def _shallow_variant(api_key: str, question: str) -> str:
    """DPO 'rejected': a plausible but SHALLOW, UNGROUNDED answer (no case file,
    no citations, no layered reasoning). This is what the model must learn to beat."""
    ok, ans = await _deepseek(
        api_key,
        "[NO CONTEXT — answer in one short paragraph from general knowledge, no sources.]\n\n"
        f"[QUESTION]\n{question}",
        max_tokens=180,
    )
    return ans.strip() if ok else ""


# ══════════════════════════════════════════════════════════════════════════════
# 3) REWARD VERIFICATION
# ══════════════════════════════════════════════════════════════════════════════
def _context_labels(context: str) -> set:
    return set(re.findall(r"\[Source:\s*([^\]]+)\]", context))


def _parse_citations(answer: str) -> list:
    """Extract cited source labels, tolerant of combined brackets the teacher
    emits, e.g. '[from a; from b]', '[from a, b]', '[from a and b]'."""
    labels = []
    for block in re.findall(r"\[from\s+([^\]]+)\]", answer):
        for part in re.split(r"\s*(?:;|,|\band\b)\s*", block):
            part = re.sub(r"^\s*from\s+", "", part.strip()).strip()
            if part:
                labels.append(part)
    return labels


def reward_grounding(answer: str, context: str) -> dict:
    cited = _parse_citations(answer)
    ctx_labels = _context_labels(context)
    invented = sorted({c.strip() for c in cited} - ctx_labels)
    n = len(cited)
    passed = n >= MIN_CITES and not invented
    return {
        "citations": n, "distinct_sources": len(set(cited)),
        "invented_sources": invented,
        "score": round(min(1.0, n / max(MIN_CITES, len(ctx_labels))), 3),
        "pass": passed,
    }


def reward_depth(answer: str) -> dict:
    present = sorted(k for k, rx in _LAYER_PATTERNS.items() if rx.search(answer))
    return {
        "layers_present": present, "n_layers": len(present),
        "score": round(len(present) / len(_LAYER_PATTERNS), 3),
        "pass": len(present) >= MIN_LAYERS,
    }


class _JudgeLLM:
    """Minimal adapter so eval_judge.judge_answer (which expects an object with
    async .complete(...) -> obj with .text/.model) can drive DeepSeek. Uses the
    SAME endpoint/contract as the rest of the repo — no new provider invented."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = "deepseek-chat"

    async def complete(self, system_prompt, user_message, *, max_tokens=220, timeout=45.0):
        import httpx
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "max_tokens": max_tokens, "temperature": 0.0,
        }
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await asyncio.wait_for(
                client.post(f"{DEEPSEEK_URL}/chat/completions", headers=headers, json=body),
                timeout=timeout + 5)
        resp.raise_for_status()
        data = resp.json()
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
        return eval_judge_result(text, self.model)


class eval_judge_result:  # noqa: N801 - tiny value holder with .text/.model
    def __init__(self, text: str, model: str):
        self.text = text
        self.model = model


async def reward_correctness(judge_llm: _JudgeLLM, question: str, golden: str, answer: str) -> dict:
    """Grade candidate vs the AUTHORED golden using the REAL R-F1396 judge."""
    res = await eval_judge.judge_answer(judge_llm, question, golden, answer)
    verdict = res.get("verdict", "unscored")
    return {
        "ok": bool(res.get("ok")),
        "verdict": verdict,
        "judge_grounded": bool(res.get("grounded", False)),
        "reason": res.get("reason", ""),
        "score": eval_judge.VERDICT_SCORES.get(verdict, 0.0),
        "pass": res.get("ok") and verdict in ("correct", "partial"),
    }


# ══════════════════════════════════════════════════════════════════════════════
# 4) CONTAMINATION GATE (internal, per example) + external preflight on output.
# ══════════════════════════════════════════════════════════════════════════════
_STOP = set("the a an of to in on for and or is are with from that this it as at by we our i "
            "you your what how do does can could should would first need to their its".split())
# common words that appear quoted in eval questions but are NOT entities — excluded
# from the entity blocklist so word-boundary matching does not false-positive.
_COMMON_WORDS = set("author action assess audit append annex allowed anomaly agree argue "
                    "confirmed critical address answer auditor number".split())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower()).rstrip(" ?.!:")


def _tokens(s: str) -> set:
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if len(t) > 2 and t not in _STOP}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def load_eval_guards() -> tuple[set, list, set]:
    """Returns (exact_norms, token_sets, entity_blocklist) from BOTH eval files."""
    exact, toksets, ents = set(), [], set()
    for p in (EVAL_FROZEN, EVAL_OPENBOOK):
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                d = json.loads(ln)
            except Exception:
                continue
            q = d.get("question", "")
            exact.add(_norm(q))
            toksets.append(_tokens(q))
            # entity blocklist: DISTINCTIVE quoted names only — multi-word, or a
            # capitalised proper noun of length >= 5. Single common words (e.g.
            # 'Author', 'Action', 'CONFIRMED') are NOT entities and would cause
            # word-boundary false positives, so they are excluded here.
            for m in re.findall(r"'([^']+)'", q):
                m = m.strip()
                if (" " in m and len(m) > 5) or (len(m) >= 5 and m[:1].isupper()
                                                 and m.lower() not in _COMMON_WORDS):
                    ents.add(m.lower())
    # curated distinctive eval named-entities (belt-and-braces)
    ents |= {e.lower() for e in [
        "Rosoboronexport", "Almaz-Antey", "Hassan Reza Tehrani", "Katebi",
        "Rafael Advanced Defense", "ASELSAN", "Belarusbank", "Bayraktar",
        "Antonov", "Carlos Mendes", "Ahmed Khalid", "Streit", "World-Check",
        "Arkmurus",
    ]}
    return exact, toksets, ents


def contamination_check(question: str, answer: str, entities: list,
                        exact: set, toksets: list, ents: set) -> dict:
    """Reject on: exact-normalised match, token-Jaccard >= JACCARD_MAX vs any eval
    question, or any eval entity name appearing in our prompt/answer/entities."""
    reasons = []
    qn = _norm(question)
    if qn in exact:
        reasons.append("exact_prompt_match")
    qt = _tokens(question)
    max_j = max((_jaccard(qt, t) for t in toksets), default=0.0)
    if max_j >= JACCARD_MAX:
        reasons.append(f"jaccard_{max_j:.2f}")
    hay = f"{question}\n{answer}\n{' '.join(entities)}".lower()
    for e in ents:
        # word-boundary match so short names don't match inside longer words
        if re.search(r"\b" + re.escape(e) + r"\b", hay):
            reasons.append(f"eval_entity:{e}")
    return {"max_jaccard": round(max_j, 3), "clean": not reasons, "reasons": reasons}


def run_preflight(out_path: Path) -> tuple[bool, str]:
    """External gate: the repo's own preflight_eval_contamination.py at --max-overlap 0.01
    against BOTH eval files. Returns (clean, combined_output). FAIL LOUD on any overlap."""
    outputs = []
    ok = True
    for evalp in (EVAL_FROZEN, EVAL_OPENBOOK):
        if not evalp.exists():
            continue
        proc = subprocess.run(
            [sys.executable, str(PREFLIGHT), "--train", str(out_path),
             "--eval", str(evalp), "--max-overlap", "0.01"],
            capture_output=True, text=True,
        )
        outputs.append(f"$ preflight --eval {evalp.name} (rc={proc.returncode})\n"
                       f"{proc.stdout}{proc.stderr}")
        if proc.returncode != 0:
            ok = False
    return ok, "\n".join(outputs)


# ══════════════════════════════════════════════════════════════════════════════
# 5) SFT + DPO writers (SFT schema == aria_grounded_v3.jsonl)
# ══════════════════════════════════════════════════════════════════════════════
def sft_row(question: str, context: str, answer: str, topic: str, reward: dict) -> dict:
    return {
        "messages": [
            {"role": "user", "content": _user_block(question, context)},
            {"role": "assistant", "content": answer.strip()},
        ],
        "topic": topic,
        "grounded": True,
        "label": "dd_depth_grounded",
        "source": SOURCE_LABEL,
        "reward": reward,          # provenance: components that let this row through
    }


def dpo_row(question: str, context: str, chosen: str, rejected: str, topic: str) -> dict:
    """DPO shape aligned with prepare_dpo.py inputs (aria_dpo_pairs_v1_str family)."""
    return {
        "prompt": _user_block(question, context),
        "chosen": chosen.strip(),
        "rejected": (rejected or "").strip(),
        "topic": topic,
        "source": SOURCE_LABEL + "_dpo",
    }


# ══════════════════════════════════════════════════════════════════════════════
async def build(count: int, out: Path, emit_dpo: bool, concurrency: int) -> int:
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

    cases = build_cases(count)
    print(f"[gen] built {len(cases)} synthetic DD-depth case files "
          f"across {len(_TEMPLATES)} scenario families")

    sem = asyncio.Semaphore(max(1, concurrency))
    stats = {"generated": len(cases), "teacher_err": 0,
             "rej_contam": 0, "rej_grounding": 0, "rej_depth": 0, "rej_correct": 0, "kept": 0}
    kept_sft, kept_dpo, examples = [], [], []

    async def one(case: Case):
        async with sem:
            ok, ans = await _teacher(api_key, case.question, case.context)
        if not ok or not ans.strip():
            stats["teacher_err"] += 1
            print(f"  [{case.cid}] TEACHER ERROR: {ans[:80]}")
            return

        # (d) contamination gate FIRST — never spend judge budget on a tainted row
        contam = contamination_check(case.question, ans, case.entities, exact, toksets, ents)
        if not contam["clean"]:
            stats["rej_contam"] += 1
            print(f"  [{case.cid}] REJECT contamination: {contam['reasons']}")
            return

        # (c) reward components
        g = reward_grounding(ans, case.context)
        d = reward_depth(ans)
        async with sem:
            c = await reward_correctness(judge_llm, case.question, case.golden, ans)

        reward = {"grounding": g, "depth": d, "correctness": c, "contamination": contam}

        if not g["pass"]:
            stats["rej_grounding"] += 1
            print(f"  [{case.cid}] REJECT grounding: cites={g['citations']} invented={g['invented_sources']}")
            return
        if not d["pass"]:
            stats["rej_depth"] += 1
            print(f"  [{case.cid}] REJECT depth: layers={d['layers_present']}")
            return
        if not c["pass"]:
            stats["rej_correct"] += 1
            print(f"  [{case.cid}] REJECT correctness: verdict={c['verdict']} ({c['reason'][:60]})")
            return

        stats["kept"] += 1
        kept_sft.append(sft_row(case.question, case.context, ans, case.topic, reward))
        print(f"  [{case.cid}] KEEP  cites={g['citations']} layers={d['n_layers']} "
              f"verdict={c['verdict']}")

        if emit_dpo:
            rej = await _shallow_variant(api_key, case.question)
            kept_dpo.append(dpo_row(case.question, case.context, ans, rej, case.topic))

        examples.append((case, ans, reward))

    await asyncio.gather(*[one(c) for c in cases])

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
        print(f"[write] {len(kept_dpo)} DPO pairs (chosen=deep grounded / rejected=shallow) -> {dpo_path}")

    # (d) EXTERNAL contamination gate on the whole output — FAIL LOUD.
    clean, report = run_preflight(out)
    print("\n" + "═" * 70 + "\n[CONTAMINATION PREFLIGHT]\n" + report + "═" * 70)
    if not clean:
        print("FATAL: preflight_eval_contamination reported overlap > 0.01 — the sample is "
              "CONTAMINATED and MUST NOT be trained on.", file=sys.stderr)
        return 4

    # summary
    print("\n[SUMMARY]")
    for k in ("generated", "teacher_err", "rej_contam", "rej_grounding",
              "rej_depth", "rej_correct", "kept"):
        print(f"  {k:14s} = {stats[k]}")
    yld = stats["kept"] / max(1, stats["generated"])
    print(f"  yield          = {yld*100:.0f}%")

    # one full worked example
    if examples:
        case, ans, reward = examples[0]
        print("\n" + "═" * 70 + "\n[FULL EXAMPLE]\n" + "═" * 70)
        print("PROMPT:\n" + case.question)
        print("\nCONTEXT (evidence given to teacher):\n" + case.context)
        print("\nGRADED ANSWER (SFT target):\n" + ans.strip())
        print("\nREWARD COMPONENTS:\n" + json.dumps(reward, ensure_ascii=False, indent=2))

    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="v0.5 DD-depth reward-verified corpus builder")
    ap.add_argument("--count", type=int, default=16, help="number of synthetic case files (small sample default)")
    ap.add_argument("--out", type=Path, default=REPO / "data" / "training" / "aria_dd_depth_v05_sample.jsonl")
    ap.add_argument("--emit-dpo", action="store_true", help="also emit DPO chosen/rejected pairs")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    return asyncio.run(build(args.count, args.out, args.emit_dpo, args.concurrency))


if __name__ == "__main__":
    raise SystemExit(main())
