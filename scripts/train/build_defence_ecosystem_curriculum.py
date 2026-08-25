"""build_defence_ecosystem_curriculum — Claude-authored mastery curriculum
across ARIA's five customer jobs (R-F4339 / C-284).

OPERATOR, 2026-08-26: "she needs to be a mastery in security and defence
ecosystem therefore lets go deep and lets have 360 approach, lets make her the
best on her field."

THE 360 IS TAKEN FROM THE NORTH STAR, NOT INVENTED.
docs/golden_intel_north_star_2026_07_14.md defines five concrete customer jobs,
each with the action it must produce:

  1. Export and sanctions protection  -> screen, block, escalate, freeze,
                                         obtain licence, avoid
  2. Procurement opportunity discovery-> qualify bid/no-bid, identify partner,
                                         prepare eligibility, monitor deadline
  3. Counterparty and network risk    -> run DD, update rating, request
                                         documents, stop engagement
  4. Market timing and positioning    -> engage, hold, monitor, re-price
  5. Source-health intelligence       -> wait, seek corroboration, treat as
                                         directional only

Job 1 is covered by build_export_control_curriculum.py (21 rows, 11 languages).
THIS FILE COVERS JOBS 2-5, which had no curriculum at all.

WHY EVERY ANSWER ENDS IN AN ACTION. The north star names the gap precisely:

    "The gap is not the guard. The gap is value density. The live feed can
     still produce generic source-derived items with templated impact such as
     'Assess country risk.' That is not enough to be ARIA's USP."

and the success test:

    "I know what changed, why it matters to my decision, what to do next, and
     why ARIA found more value than the raw source headline."

So each row teaches SPECIFICS + DECISION. An answer that names the mechanism
but stops short of what to do is exactly the encyclopedia entry the north star
rejects — and it is what a generic model already produces, so training it adds
nothing.

MASTERY MEANS KNOWING THE TRAPS, not reciting definitions. Each row is written
around the thing a practitioner gets wrong: the de-minimis rule that does not
apply to ITAR, the FMS case that is government-to-government so the "customer"
is not the buyer, the UBO threshold that hides control below 25%, the tender
amendment that resets the clock. That is what separates a specialist from a
summariser.

CONTAMINATION. Authored from general domain knowledge, not from the eval's
expected_answer fields; no eval question is reproduced. The pre-flight checks
PROMPT overlap only, so answer-level discipline is human.

CONFIDENCE. Only `high` rows ship. A wrong threshold or a wrong regulation
trained in is a falsehood ARIA repeats with confidence in a due-diligence
product. Section 24 requires a dataset-quality review before any paid cycle.

Usage:
  python scripts/train/build_defence_ecosystem_curriculum.py \
      --out data/training/aria_defence_ecosystem_v1.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM = (
    "You are ARIA, a defence and security due-diligence intelligence agent. "
    "Give the specific mechanism — named authority, instrument, threshold or "
    "programme — and then state what the operator should DO next. Never stop at "
    "a generic recommendation such as 'assess country risk'. If you do not "
    "know, say so; never invent an agency, a statute, a threshold or a figure."
)

PAIRS: list[dict] = [
    # ============ JOB 2 — PROCUREMENT OPPORTUNITY DISCOVERY ============
    {
        "id": "fms_basics", "job": "procurement", "confidence": "high",
        "q": "What is Foreign Military Sales (FMS) and how does it differ from Direct Commercial Sales for a supplier?",
        "a": (
            "FMS (Foreign Military Sales) is a government-to-government channel administered by "
            "DSCA (Defense Security Cooperation Agency) in the US Department of Defense. The "
            "foreign government contracts with the US Government, and the US Government contracts "
            "the vendor. Major cases require Congressional notification, which DSCA publishes — "
            "those notices are public and are the earliest reliable signal of a pending sale.\n\n"
            "DCS (Direct Commercial Sales) is the alternative: the vendor contracts the foreign "
            "customer directly, under an ITAR licence issued by DDTC.\n\n"
            "What this means for a supplier: under FMS your customer is the US Government, not the "
            "end country — pricing, terms and contracting flow through DoD, and you cannot "
            "negotiate directly with the foreign buyer on the case. The practical action is to "
            "watch DSCA Congressional notifications for your platform and country, then position "
            "with the US programme office rather than the foreign ministry, because by the time "
            "the case is notified the requirement is already defined."
        ),
    },
    {
        "id": "tender_amendment", "job": "procurement", "confidence": "high",
        "q": "A defence tender you are tracking issues an amendment two weeks before the deadline. What should you actually do?",
        "a": (
            "An amendment is a substantive event, not an administrative one. In the EU it is published as a corrigendum on TED (Tenders Electronic Daily) against the original contract notice, under Directive 2009/81/EC for defence and security procurement. It commonly does "
            "three things: changes technical requirements, changes eligibility or certification "
            "demands, and resets or extends the submission deadline.\n\n"
            "What to do, in order: re-read the eligibility clauses first, because an amendment is "
            "the most common point at which a national-content, security-clearance or "
            "prior-experience requirement is introduced that silently disqualifies a bidder who "
            "was compliant under the original notice. Then re-check the deadline — an extension "
            "may also reopen the clarification window, which is your only lawful route to "
            "challenge a restrictive specification. Then re-run the bid/no-bid decision rather "
            "than assuming the earlier one holds.\n\n"
            "The failure mode to avoid is treating an amendment as a date change and continuing a "
            "bid you have just become ineligible for."
        ),
    },
    {
        "id": "eu_procurement_bodies", "job": "procurement", "confidence": "high",
        "q": "Which multinational bodies actually place defence contracts in Europe, and why does it matter which one is running a programme?",
        "a": (
            "NSPA (NATO Support and Procurement Agency) procures sustainment, logistics and "
            "common-funded items for NATO nations. OCCAR (Organisation for Joint Armament "
            "Co-operation) manages collaborative armament programmes for its member states. The "
            "European Defence Fund (EDF) co-finances collaborative R&D through the European "
            "Commission. National ministries run everything else.\n\n"
            "Why the distinction matters: eligibility rules differ per body, and they are the "
            "gating factor rather than technical merit. EDF funding carries EU-ownership and "
            "control conditions on participants; OCCAR programmes are shaped by the participating "
            "states' workshare arrangements; NSPA runs framework contracts where being on the "
            "framework beforehand is what determines whether you can bid at all.\n\n"
            "The action is to identify the contracting body before qualifying the opportunity, "
            "then check the specific eligibility condition that body applies — because a bid that "
            "is technically perfect and structurally ineligible costs the same to prepare and "
            "wins nothing."
        ),
    },
    {
        "id": "offsets", "job": "procurement", "confidence": "high",
        "q": "What are industrial offsets in defence procurement and what do they oblige a winning supplier to do?",
        "a": (
            "An offset is a compensation obligation imposed by the buying country as a condition "
            "of a defence contract — typically local production, technology transfer, local "
            "sourcing, training or investment, valued as a percentage of the contract and "
            "sometimes exceeding 100%.\n\n"
            "What it obliges: the obligation is contractual and usually carries liquidated damages "
            "and a bank guarantee. It runs for years after delivery, is tracked by an offset "
            "authority in the buying ministry, and credit is granted only for activity that "
            "authority accepts — not for whatever the supplier happens to spend locally.\n\n"
            "The action at bid stage is to price the offset as a cost line with its own risk "
            "reserve, confirm which activities earn credit and at what multiplier BEFORE "
            "committing, and check whether the technology transfer implied is itself export-"
            "licensable. A supplier who wins the contract and then discovers the transfer needs a "
            "licence the home state will refuse has bought a penalty, not a sale."
        ),
    },
    # ============ JOB 3 — COUNTERPARTY AND NETWORK RISK ============
    {
        "id": "ubo_threshold", "job": "counterparty", "confidence": "high",
        "q": "What is a UBO and why is the 25% threshold a trap in defence due diligence?",
        "a": (
            "A UBO (ultimate beneficial owner) is the natural person who ultimately owns or "
            "controls an entity. Most registers and AML frameworks use an indicative threshold "
            "around 25% of shares or voting rights.\n\n"
            "Why the threshold is a trap: 25% is a reporting trigger, not a definition of control. "
            "Control can sit below it through shareholder agreements, golden shares, board "
            "appointment rights, veto rights, nominee arrangements, or a chain of entities each "
            "holding under the threshold. A sanctioned person holding 24% in three layers appears "
            "nowhere and controls the company.\n\n"
            "What to do: treat a clean 25% screen as the START of the enquiry. Trace the ownership "
            "chain to natural persons rather than stopping at the first corporate parent, ask for "
            "the shareholder agreement rather than only the register extract, and screen "
            "identified persons and their known associates. Where the chain cannot be resolved, "
            "record it as an unresolved control question — not as a clean result. An unverifiable "
            "owner is a finding."
        ),
    },
    {
        "id": "ofac_50pct", "job": "counterparty", "confidence": "high",
        "q": "An entity is not on the OFAC SDN list but is 60% owned by someone who is. Is it blocked?",
        "a": (
            "Yes. Under OFAC's 50 Percent Rule, an entity owned 50% or more, directly or "
            "indirectly, by one or more blocked persons is itself blocked — even though it is not "
            "named on the SDN list. Ownership aggregates across multiple blocked persons.\n\n"
            "Two consequences practitioners miss. First, the list is not the population: screening "
            "a name against the SDN list and getting no hit does not establish that the "
            "counterparty is permissible. Second, OFAC treats 50% as a bright line for blocking, "
            "but it also cautions about entities controlled by blocked persons below that level — "
            "those are not automatically blocked yet carry real risk.\n\n"
            "What to do: resolve ownership percentages, aggregate blocked-person holdings, and "
            "block at 50% or more. Below 50% with evident control, escalate rather than clear, and "
            "record the reasoning. The EU and UK operate their own ownership-and-control tests "
            "that are similar in intent but not identical in wording, so a multi-jurisdiction "
            "transaction must be tested against each regime, not just the US one."
        ),
    },
    {
        "id": "diversion_typology", "job": "counterparty", "confidence": "high",
        "q": "What are the practical red flags that a defence or dual-use shipment is being diverted?",
        "a": (
            "The recognised indicators are behavioural and commercial rather than technical: a "
            "customer whose business has no plausible use for the item; a freight-forwarder or "
            "trading intermediary as the named end user; a shipping route or transhipment point "
            "inconsistent with the stated destination; willingness to pay a premium with unusual "
            "payment terms; refusal of installation, training or after-sales support that the "
            "product normally requires; a newly incorporated counterparty with no trading history; "
            "and requests to under-declare value or mis-describe the goods.\n\n"
            "What to do when one appears: do not treat it as a documentation gap to be papered "
            "over with a signed end-user certificate — a certificate is a representation, not "
            "verification. Escalate to the compliance function, seek independent confirmation of "
            "the end use, and consider a post-shipment verification condition. Where the flag "
            "cannot be resolved, decline: the exporter carries the licence obligation regardless "
            "of what the customer asserted, and 'we were told it was civil' is not a defence."
        ),
    },
    {
        "id": "pep_adverse_media", "job": "counterparty", "confidence": "high",
        "q": "A counterparty's director is a PEP and there is adverse media about an unrelated company. How should that be handled?",
        "a": (
            "PEP status is a risk indicator, not a prohibition. It requires enhanced due diligence "
            "— establishing source of wealth and source of funds, senior sign-off, and ongoing "
            "monitoring — not automatic refusal. Treating PEP status as disqualifying is itself a "
            "compliance failure, because it substitutes a label for an assessment.\n\n"
            "Adverse media about a DIFFERENT company requires the same discipline in reverse: "
            "establish whether the individual is the same person, what role they held, at what "
            "date, and whether the allegation was tested. Name coincidence is common, and "
            "unverified media is not a finding.\n\n"
            "What to do: record both as open questions with the specific evidence needed to close "
            "them — an identity match on date of birth or registered address, and the status of "
            "the allegation. Then decide on the resolved facts. Report what is verified, what is "
            "unresolved, and the effect on the risk rating separately: a report that merges "
            "confirmed and unconfirmed material into a single conclusion cannot be relied on."
        ),
    },
    {
        "id": "shell_indicators", "job": "counterparty", "confidence": "high",
        "q": "What distinguishes a shell company from a legitimate holding company in a defence supply chain?",
        "a": (
            "A holding company with no operations is normal and lawful. The concern is the "
            "combination of features, not any single one: registration at a mass-registration "
            "address; directors serving on many unrelated entities; incorporation shortly before "
            "the transaction; no employees, premises or filed accounts; a name resembling an "
            "established manufacturer; and a jurisdiction that does not require ownership "
            "disclosure.\n\n"
            "What to do: test function rather than form. Ask what the entity contributes to the "
            "transaction — if it takes title but adds no manufacturing, logistics, financing or "
            "service, ask why it exists in the chain at all. A layer that adds no function is "
            "usually there to add distance, and distance from the end user is the diversion risk.\n\n"
            "Record the specific indicators observed and the unanswered question, then request "
            "documents that would resolve it — filed accounts, a lease, a payroll, or the "
            "shareholder agreement. Absence of a reply is itself informative and should be "
            "reported as such rather than left as a silent gap."
        ),
    },
    # ============ JOB 4 — MARKET TIMING AND POSITIONING ============
    {
        "id": "budget_signal", "job": "market", "confidence": "high",
        "q": "A country announces a large increase in its defence budget. What does that actually tell a supplier, and what should they do?",
        "a": (
            "Less than it appears. An announced headline figure is a political commitment, not "
            "contractible demand. What converts it into opportunity is the appropriation actually "
            "passing, the allocation reaching a specific programme line, and the procurement "
            "authority issuing a requirement. Personnel costs and pensions absorb a large share of "
            "many defence budgets, so headline growth need not increase equipment spend at all.\n\n"
            "What to do: trace the announcement to the programme line rather than reacting to the "
            "total. Check whether the increase is new money or reprofiled from later years, "
            "whether it is capital or operating, and whether the country's absorption capacity — "
            "its procurement staffing and past execution rate — can actually spend it. A budget "
            "that cannot be executed produces announcements, not contracts.\n\n"
            "The correct posture on the announcement alone is monitor, not engage; engage when the "
            "requirement or pre-RFP consultation appears."
        ),
    },
    {
        "id": "conflict_window", "job": "market", "confidence": "high",
        "q": "How should an active conflict change a defence supplier's assessment of a market?",
        "a": (
            "In two directions at once, and conflating them is the common error. Demand for "
            "consumables, munitions, sustainment and ISR typically rises quickly, while major new "
            "platform programmes often slow as funds are redirected to immediate needs.\n\n"
            "Against that, risk rises sharply: export licences to a party to a conflict become "
            "harder or impossible; end-use assurance weakens because materiel moves; diversion and "
            "re-transfer risk increases; and reputational and legal exposure under the ATT risk "
            "criteria and international humanitarian law becomes live rather than theoretical.\n\n"
            "What to do: separate the demand signal from the licensability question and answer the "
            "licensing one first, because it is binary. Check whether the destination is subject to "
            "an embargo or a policy of denial, whether neighbouring-state routes create a diversion "
            "path, and whether existing licences remain valid — states frequently suspend extant "
            "licences on conflict onset. A pipeline that cannot be licensed is not a pipeline."
        ),
    },
    {
        "id": "programme_slip", "job": "market", "confidence": "high",
        "q": "A major platform programme slips by two years. What is the second-order effect a supplier should act on?",
        "a": (
            "The first-order effect is obvious: delayed revenue on that programme. The "
            "second-order effects are where the decision actually is.\n\n"
            "A slip usually extends the life of the legacy fleet being replaced, which raises "
            "demand for sustainment, spares and obsolescence management on the OLD platform — "
            "often a better near-term opportunity than the delayed one. It also creates a "
            "capability gap the customer may fill with an interim buy or a lease. And it changes "
            "the competitive field, because a delay gives a competitor time to qualify.\n\n"
            "What to do: reposition toward the legacy sustainment line and the interim requirement "
            "rather than simply re-phasing the forecast. Confirm whether the slip is funding-driven "
            "or technical, because a technical slip can extend further while a funding slip can "
            "reverse. Where the supplier is already on the legacy platform, this is an engage "
            "signal, not a hold."
        ),
    },
    # ============ JOB 5 — SOURCE-HEALTH INTELLIGENCE ============
    {
        "id": "source_degraded", "job": "source_health", "confidence": "high",
        "q": "A sanctions screening returns no hits, but one of the underlying source lists could not be reached. What should the report say?",
        "a": (
            "It must say that the result is incomplete, and it must say which source was "
            "unavailable. A no-hit produced from a reduced source set is not a clean screen, and "
            "reporting it as one converts an unknown into a false assurance — the most dangerous "
            "single error in screening, because the reader cannot see the gap.\n\n"
            "The correct output is a tri-state, not a binary: screened and clear; screened and "
            "matched; or could not screen against the full source set. The third state carries the "
            "named missing source and the time of the attempt.\n\n"
            "What the operator should do: treat the result as directional only, seek corroboration "
            "against the missing list before relying on it, and re-run when the source recovers. "
            "Where the decision cannot wait, escalate with the gap stated explicitly so the risk "
            "is accepted knowingly rather than by omission."
        ),
    },
    {
        "id": "stale_data", "job": "source_health", "confidence": "high",
        "q": "How should the age of intelligence change how it is used in a due-diligence report?",
        "a": (
            "Age changes what a fact can support, not merely how confident it is. Ownership, "
            "directorships and sanctions status change without notice, so a six-month-old "
            "corporate extract can support a statement about what was recorded at that date but "
            "cannot support a present-tense claim about who controls the entity now.\n\n"
            "The discipline is to date every material fact and to write it in the tense the "
            "evidence supports. 'As of the filing dated 12 March, the register showed...' is "
            "defensible; 'the company is owned by...' on the same evidence is not.\n\n"
            "What the operator should do: refresh the time-sensitive checks — sanctions, "
            "ownership, litigation — immediately before a decision rather than relying on the file, "
            "and state the as-of date beside each. Where a refresh is not possible, mark the item "
            "as directional and say so in the conclusion rather than only in a footnote, because a "
            "caveat the decision-maker does not read has not been given."
        ),
    },
    {
        "id": "single_source", "job": "source_health", "confidence": "high",
        "q": "Every result about a company traces back to one original source. How should that be handled?",
        "a": (
            "As a single point of failure, and it must be stated. Wide repetition across many "
            "outlets is not corroboration when all of them derive from the same original — this is "
            "the most common way an unverified claim acquires the appearance of consensus. Press "
            "aggregation, database records that ingest the same filing, and machine-translated "
            "reprints all inflate apparent coverage without adding evidence.\n\n"
            "What to do: identify the original source and assess it on its own merits — who "
            "published it, on what evidence, with what interest. Then attempt genuinely "
            "independent corroboration: a primary filing, a regulator, a court record, or a party "
            "with no relationship to the original.\n\n"
            "If none exists, report the finding as single-sourced and name the source. The "
            "operator's action is to treat it as directional and weight it accordingly — not to "
            "discard it, and not to rely on it as established fact."
        ),
    },
]


def _rows(include_medium: bool) -> list[dict]:
    out: list[dict] = []
    for p in PAIRS:
        if p["confidence"] != "high" and not include_medium:
            continue
        out.append({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": p["q"]},
                {"role": "assistant", "content": p["a"]},
            ],
            "topic": f"defence_ecosystem_{p['job']}_{p['id']}",
            "job": p["job"],
            "confidence": p["confidence"],
            "source": "claude_authored:R-F4339",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--include-medium", action="store_true")
    args = ap.parse_args()

    rows = _rows(args.include_medium)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n")
    import collections
    jobs = collections.Counter(r["job"] for r in rows)
    print(f"wrote {len(rows)} rows -> {args.out}")
    for j, n in sorted(jobs.items()):
        print(f"   {j:14s} {n}")
    print(f"  all high-confidence: {all(r['confidence'] == 'high' for r in rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
