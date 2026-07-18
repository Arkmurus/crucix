# ARIA grounded synthesis — claude (claude-sonnet-5)

> Produced by Claude Code A/B harness, 2026-07-18 (isolated local run; identical evidence fed to both providers; claude authored this answer).

**Question:** For a defence supply-chain due diligence, compare BAE Systems plc and Leonardo S.p.A. on beneficial-ownership transparency and state influence. Which presents the cleaner ownership picture and why?

**Tokens:** 9463 in / 8000 out · **Cost:** $0.1484 · **Latency:** 83.6s · **Evidence:** 16 sources / 1 extracts

---

*🟡 BOTTOM LINE — On tonight's evidence, BAE Systems returns a more verifiable ownership/compliance picture; Leonardo S.p.A. is structurally the more state-influenced entity, but this DD run failed to retrieve Leonardo's registry data, so that's a research gap — not proof Leonardo is opaque.*

━━━━━━━━━━━━━━━━━━━━

*📋 CLASSIFICATION* [CONFIRMED]
BAE Systems plc: GB-incorporated, LEI-registered, company number 01470151, status ACTIVE [from GLEIF record, EXTRACT 1].

Leonardo S.p.A.: Italian OEM with confirmed US DoD contract exposure via USASpending.gov [EXTRACT 1]; Italian core registry data (registration status, directors, incorporation date) NOT retrieved this run.

━━━━━━━━━━━━━━━━━━━━

*⚠️ COMPLIANCE / SANCTIONS SCREEN*
BAE Systems — clean across OFAC SDN, UK OFSI, EU Consolidated, UN 1267, and OpenSanctions, including fuzzy alias checks [CONFIRMED, EXTRACT 1]. Treat as clearance under standard commercial DD.

Leonardo — one OpenSanctions hit, score 1.00, dataset "gem_energy_ownership" [EXTRACT 1]. This is an energy-ownership graph dataset, NOT a sanctions list — informational only, not a hit. [ASSESSED — single source]

CONFLICT: the Leonardo extract states both "not found in SEC EDGAR (not US-listed)" and "most recent 8-K filed 2026-07-17" [EXTRACT 1]. [CONTRADICTED — sources disagree, human review required]. Likely cause: conflation with Leonardo DRS, the Nasdaq-listed US subsidiary, which IS SEC-registered — needs separation before either fact is cited.

━━━━━━━━━━━━━━━━━━━━

*🔍 OWNERSHIP TRANSPARENCY — BAE SYSTEMS* [PROBABLE, ~70%]
Active, registered, LEI-confirmed [EXTRACT 1]. Two named officers (Griffiths, Kirk) flagged as "possible nominee director pattern," but that identical line repeats five times in the extract — I read this as a tool duplication artefact, not five independent findings. [UNCERTAIN]

A FTSE-listed plc returning only two officers is itself a sign of a partial data pull, not board concealment.

GENERAL KNOWLEDGE — VERIFY: as an LSE main-market plc, BAE is exempt from the UK PSC register; real transparency runs through FCA substantial-shareholder (TR-1) notifications and the annual report. Companies House accounts weren't pulled this run (Phase 2 pending).

GENERAL KNOWLEDGE — VERIFY: BAE carries a legacy UK Government "Special Share" from 1980s privatisation capping foreign voting-share ownership — a control safeguard, not a government equity stake.

*🔍 OWNERSHIP TRANSPARENCY — LEONARDO S.P.A.* [UNCERTAIN, ~35%]
Registry verification explicitly incomplete — no status, no directors, no incorporation date [EXTRACT 1]. ARIA's own DD orchestrator withheld GREEN clearance for exactly this reason.

Digital sweep failed entirely: all 23 sources were redirects/cookie walls/consent screens, zero substantive content on ownership or leadership [EXTRACT 1].

GENERAL KNOWLEDGE — VERIFY (not confirmed by tonight's evidence): Italy's Ministry of Economy and Finance holds a substantial minority stake in Leonardo (commonly cited near 30%) plus "golden power" rights over strategic decisions. If accurate, this makes Leonardo the more state-influenced of the two by ownership structure — but do not quote a figure to a client until pulled live from CONSOB/Leonardo IR.

USASpending.gov confirms DoD as a top awarding agency [EXTRACT 1] — contract-flow evidence, not ownership evidence.

━━━━━━━━━━━━━━━━━━━━

*💼 POSITIONING* [ASSESSED]
Two separate questions here:

1. Data completeness ton
