# DD report — Rheinmetall AG — deepseek (deepseek-chat)

> Produced by Claude Code A/B harness, 2026-07-18. Isolated local run, deep mode. deepseek was the LLM behind this DD run.

- **Risk verdict:** GREEN
- **Bottom line:** 🟢 GREEN — Rheinmetall AG passes baseline due diligence. Standard contracting path available.
- **Recommendation:** Proceed with standard DD. No blocking concerns identified in the universal layer.
- **Confidence:** ASSESSED
- **Findings:** 6 · **Sources:** 42 · **Data gaps:** 7
- **LLM:** 1 calls · 132/115 tok · $0.0002 · 143.6s

## Key findings

- **[identity]** Rheinmetall AG — no matches across OFAC SDN, UK OFSI, EU Consolidated, UN 1267, or OpenSanctions datasets. Fuzzy variants / aliases screened. This is a POSITIVE CLEAN result — treat as clearance under standard commercial DD.
- **[identity]** Source: https://search.gleif.org/#/record/529900131QNVTEL4QS96
- **[compliance]** Top awarding agencies: Department of Defense. Source: USASpending.gov (federal award records).
- **[compliance]** Rheinmetall AG is not found in SEC EDGAR (not US-listed). Public financial statements are NOT available from this source — financial health is UNKNOWN, not a clean bill. For UK entities, Companies House accounts apply (Phase 2).
- **[compliance]** Certificate Transparency log analysis for 'Rheinmetall AG': 50 certificates across 5 apex domains, issued by 1 distinct CA(s). Shell-broker fingerprint score: 35/100. High scores indicate domain-spinning patterns common in shell-broker / typo-squat networks. VERIFY by inspecting the actual cert SAN 
- **[compliance]** Signal flagged by cert_transparency.detect_shell_pattern for query 'Rheinmetall AG'. Treat as indicator, not verdict — operator must corroborate with other DD evidence.