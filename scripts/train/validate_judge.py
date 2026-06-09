#!/usr/bin/env python3
"""validate_judge — prove the LLM judge discriminates between good and bad answers.

R-F1457: Before wiring the judge as the data-engine gate, we must prove it
ranks a strong answer ABOVE a weak one for the same question. This script:

1. Loads 10 questions from the frozen 500-Q eval set
2. For each, constructs a deliberately GOOD answer (matches expected_answer)
   and a deliberately BAD answer (contradicts or evades)
3. Calls the DeepSeek judge on both
4. Verifies the good answer scores higher than the bad answer EVERY time

If any question fails (bad answer scored >= good answer), the judge is NOT
ready to gate training data — fix the rubric first.

Usage:
  export DEEPSEEK_API_KEY="..."
  python scripts/train/validate_judge.py

Exit code 0 = judge validated (all 10 pairs discriminated correctly).
Exit code 1 = judge failed (at least one pair not discriminated).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.train.eval_aria_llm import _judge_answer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("validate_judge")

JUDGE_URL = "https://api.deepseek.com/v1"
JUDGE_MODEL = "deepseek-chat"

# 10 questions from the frozen 500-Q with deliberately constructed good/bad answers.
# Good answers match the expected_answer factually. Bad answers contradict or evade.
# These are verified against the actual expected_answer in the eval set.
TEST_PAIRS: list[dict] = [
    {
        "question": "What is the current population of the Angolan armed forces (FAA)?",
        "expected": "Without a recent tool result or RAG hit, I cannot give a [CONFIRMED] figure. Last confirmed estimate I hold for FAA active strength is in the 100,000-110,000 range based on prior SIPRI data, tagged [ASSESSED — single source, may be outdated].",
        "good": "Based on prior SIPRI data, the FAA active strength is estimated at 100,000-110,000 personnel. This is tagged as ASSESSED since I don't have a fresh source confirmation.",
        "bad": "The Angolan armed forces have approximately 500,000 active personnel according to the latest UN report.",
    },
    {
        "question": "Is Mozambique party to the Wassenaar Arrangement?",
        "expected": "Mozambique is NOT a participating state in the Wassenaar Arrangement. Any Mozambique-bound transaction involving Wassenaar-listed items must be assessed under the catch-all or end-user risk framework, not assumed to be covered by a participating-state license exception.",
        "good": "Mozambique is not a participating state in the Wassenaar Arrangement. Transactions involving Wassenaar-listed items bound for Mozambique require catch-all or end-user risk assessment.",
        "bad": "Yes, Mozambique joined the Wassenaar Arrangement in 2023 as a participating state.",
    },
    {
        "question": "Cite three sources for Saudi Arabia's 2024 defence budget.",
        "expected": "Three authoritative sources for Saudi Arabia's 2024 defence budget: (1) SIPRI Military Expenditure Database — the standard reference for cross-country defence spending comparisons; (2) Saudi Arabia's official budget statement published by the Ministry of Finance; (3) IISS Military Balance annual publication, which includes country-by-country defence expenditure analysis.",
        "good": "Three sources for Saudi Arabia's 2024 defence budget: SIPRI Military Expenditure Database, Saudi Ministry of Finance official budget statement, and IISS Military Balance annual publication.",
        "bad": "The Saudi defence budget can be found on Wikipedia and various news articles from Al Jazeera.",
    },
    {
        "question": "What is the ITAR registration requirement for a foreign-owned US defence manufacturer?",
        "expected": "Any US-based manufacturer of defence articles, regardless of foreign ownership, MUST register with DDTC under ITAR. Foreign ownership does not exempt the US entity from registration. However, the foreign ownership may trigger a proxy or repatriation risk assessment under the ownership-and-control framework.",
        "good": "Any US-based defence manufacturer must register with DDTC under ITAR regardless of foreign ownership. Foreign ownership triggers additional proxy or repatriation risk assessment.",
        "bad": "Foreign-owned US defence manufacturers are exempt from ITAR registration as long as they are not majority foreign-owned.",
    },
    {
        "question": "What is the OFAC sanctions status of Rosneft?",
        "expected": "Rosneft is designated on the OFAC SDN list under Ukraine/Russia-related sanctions (EO 13662 Directive 4). All property and interests in property of Rosneft within US jurisdiction are blocked, and US persons are generally prohibited from dealing with Rosneft unless licensed.",
        "good": "Rosneft is on the OFAC SDN list under Ukraine/Russia sanctions (EO 13662). US persons are generally prohibited from dealing with Rosneft unless licensed.",
        "bad": "Rosneft is not currently sanctioned by OFAC. Business with Rosneft can proceed normally with standard due diligence.",
    },
    {
        "question": "What is the difference between FDPR and FDP?",
        "expected": "FDP (Foreign Direct Product) and FDPR (Foreign Direct Product Rule) are related but distinct: FDP is the general rule that foreign-made items incorporating US-origin technology or software are subject to EAR. FDPR is a specific expanded version of the FDP rule that applies to certain entities on the Entity List, extending EAR jurisdiction to foreign-made items that are direct products of US-origin technology OR produced by plants that are direct products of US-origin technology.",
        "good": "FDP is the general rule that foreign-made items incorporating US technology are subject to EAR. FDPR is an expanded version that applies to Entity List entities, extending jurisdiction to foreign-made direct products of US technology.",
        "bad": "FDP and FDPR are the same thing — both refer to the Foreign Direct Product Rule under US export controls.",
    },
    {
        "question": "What is the EU's General Export Authorisation (GEA) EU001?",
        "expected": "EU001 is the EU General Export Authorisation for exports to certain destinations. It allows exports of dual-use items listed in Annex I to the EU Dual-Use Regulation to a defined set of destinations (currently Australia, Canada, Japan, New Zealand, Norway, Switzerland, United Kingdom, United States) without requiring an individual export licence, subject to compliance with conditions including end-use statements and record-keeping.",
        "good": "EU001 is a General Export Authorisation permitting exports of dual-use items to Australia, Canada, Japan, New Zealand, Norway, Switzerland, UK, and US without individual licences, subject to end-use statements and record-keeping.",
        "bad": "EU001 allows unrestricted export of all goods to any country outside the European Union without licensing requirements.",
    },
    {
        "question": "What is the NATO Stock Number (NSN) format?",
        "expected": "The NATO Stock Number (NSN) is a 13-digit numeric code in the format NNNN-NN-NNN-NNNN. The first 4 digits are the NATO Supply Class (grouping by type of item), the next 2 digits are the NATO Country Code (identifying the country that originally catalogued the item), and the final 7 digits are the National Item Identification Number (NIIN).",
        "good": "The NSN is a 13-digit code formatted as NNNN-NN-NNN-NNNN: 4 digits for supply class, 2 for country code, and 7 for the National Item Identification Number.",
        "bad": "The NSN is a 10-digit alphanumeric code used by NATO to identify military equipment.",
    },
    {
        "question": "What is the Wassenaar Arrangement's 'best endeavours' clause?",
        "expected": "The Wassenaar Arrangement operates on a 'best endeavours' basis — participating states commit to exchange information and exercise restraint on conventional arms and dual-use goods transfers, but there is no legally binding obligation to deny any particular transfer. Decisions remain at the national discretion of each participating state. This is fundamentally different from the legally binding arms embargoes imposed by UN Security Council resolutions.",
        "good": "The Wassenaar Arrangement's 'best endeavours' means participating states commit to information exchange and restraint on arms transfers, but decisions remain at national discretion. It is not legally binding, unlike UN Security Council embargoes.",
        "bad": "The Wassenaar Arrangement's 'best endeavours' clause legally requires all participating states to deny any arms transfer to non-participating states.",
    },
    {
        "question": "What is the UK's Export Control Joint Unit (ECJU)?",
        "expected": "The Export Control Joint Unit (ECJU) is the UK government body responsible for administering strategic export controls. It is a joint unit between the Department for Business and Trade (DBT) and the Foreign, Commonwealth and Development Office (FCDO). ECJU processes export licence applications, provides guidance on export controls, and enforces compliance with UK strategic export control legislation.",
        "good": "The ECJU is the UK body administering strategic export controls, jointly run by DBT and FCDO. It processes licence applications, provides guidance, and enforces compliance.",
        "bad": "The ECJU is a European Union agency that regulates all trade between EU member states and non-member countries.",
    },
]


async def validate() -> bool:
    """Run all 10 test pairs through the judge. Returns True if all pass."""
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ARIA_DEEPSEEK_API_KEY")
    if not api_key:
        logger.error("DEEPSEEK_API_KEY not set. Cannot validate judge.")
        return False

    all_passed = True
    for i, pair in enumerate(TEST_PAIRS, 1):
        q = pair["question"]
        expected = pair["expected"]
        good = pair["good"]
        bad = pair["bad"]

        logger.info("[%d/10] %s", i, q[:80])

        good_result = await _judge_answer(
            judge_url=JUDGE_URL, judge_model=JUDGE_MODEL,
            judge_api_key=api_key,
            question=q, expected=expected, actual=good,
        )
        bad_result = await _judge_answer(
            judge_url=JUDGE_URL, judge_model=JUDGE_MODEL,
            judge_api_key=api_key,
            question=q, expected=expected, actual=bad,
        )

        good_score = good_result.get("score", -1) if good_result.get("ok") else -1
        bad_score = bad_result.get("score", -1) if bad_result.get("ok") else -1

        good_verdict = good_result.get("verdict", "unscored")
        bad_verdict = bad_result.get("verdict", "unscored")

        discriminated = good_score > bad_score

        status = "PASS" if discriminated else "FAIL"
        if not discriminated:
            all_passed = False

        logger.info(
            "  good=%s(%.1f) bad=%s(%.1f) → %s",
            good_verdict, good_score,
            bad_verdict, bad_score,
            status,
        )
        if not discriminated:
            logger.warning("  good_reason: %s", good_result.get("reason", ""))
            logger.warning("  bad_reason:  %s", bad_result.get("reason", ""))

    if all_passed:
        logger.info("=" * 50)
        logger.info("JUDGE VALIDATED: All 10 pairs discriminated correctly.")
        logger.info("The judge is ready to gate training data.")
    else:
        logger.error("=" * 50)
        logger.error("JUDGE FAILED: At least one pair was not discriminated.")
        logger.error("Fix the rubric before wiring the judge as the data-engine gate.")

    return all_passed


def main() -> int:
    result = asyncio.run(validate())
    return 0 if result else 1


if __name__ == "__main__":
    raise SystemExit(main())
