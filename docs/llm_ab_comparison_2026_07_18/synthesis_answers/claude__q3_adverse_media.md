# ARIA grounded synthesis — claude (claude-sonnet-5)

> Produced by Claude Code A/B harness, 2026-07-18 (isolated local run; identical evidence fed to both providers; claude authored this answer).

**Question:** Summarise the main adverse-media, litigation, and governance risks associated with Leonardo S.p.A. over recent years, and how material each is to a supply-chain due-diligence decision.

**Tokens:** 5356 in / 7711 out · **Cost:** $0.1317 · **Latency:** 83.8s · **Evidence:** 8 sources / 1 extracts

---

*🟡 BOTTOM LINE — The OSINT evidence gathered is functionally empty; I cannot confirm or deny material adverse-media, litigation, or governance risk on Leonardo S.p.A. from this data, and the DD tool itself says so explicitly.*

━━━━━━━━━━━━━━━━━━━━

*📋 EVIDENCE QUALITY* [CONFIRMED — tool self-reported failure]

The digital layer explicitly failed: "All 23 sources retrieved... were irrelevant to Leonardo S.p.A., consisting entirely of redirect pages, cookie consent walls, session verification screens... zero intelligence value for defence procurement analysis" [EXTRACT 1 — digital]. Zero named individuals, zero contract values, zero dates, zero corporate relationships were extracted. This is not "no adverse media found" — it's "no search was actually performed successfully."

Do not read the absence of hits as a clean bill. Per Clause 24/17, absence-of-finding from a failed search ≠ verified-clean.

━━━━━━━━━━━━━━━━━━━━

*🔍 WHAT THE EVIDENCE ACTUALLY SHOWS*

1. Identity match: Leonardo SpA hit in OpenSanctions dataset `gem_energy_ownership` at score 1.00 — but the tool itself flags this "informational only, not a refusal ground" [EXTRACT 1 — identity]. This is NOT a sanctions or adverse designation — it's an ownership-tracking dataset entry, likely Global Energy Monitor corporate-ownership data. Don't let the 1.00 score mislead — no red flag here.

2. Registry verification incomplete — "missing registration status, directors/officers, incorporation date" [EXTRACT 1 — identity]. For a company this size and public profile (Milan Borsa Italiana: LDO), this gap reflects tool/source-selection failure, not genuine unavailability of public record. `[UNCERTAIN — tool limitation, not entity opacity]`

3. `[CONFLICT]` — one extract line states "Most recent: 8-K filed 2026-07-17," another states "Leonardo S.p.A. is not found in SEC EDGAR (not US-listed)" [EXTRACT 1 — compliance]. These are not reconciled in the evidence. My assessment: `[ASSESSED]` this 8-K likely belongs to Leonardo DRS Inc. (the NASDAQ-listed US subsidiary), not the Italian parent — but this is my inference, not confirmed by the tool, and needs separate verification.

4. USASpending.gov shows top awarding agency = US Department of Defense [EXTRACT 1 — compliance]. This is `[PROBABLE]` attributable to Leonardo DRS rather than the parent directly — flag for clarification.

5. Financial health: "2 public financial reference(s)... figures not yet extracted... UNKNOWN, not a clean bill" [EXTRACT 1 — compliance]. No financial risk assessment is possible from this run.

6. [S3] carries a fragment "Leonardo — CAAT UK" (Campaign Against Arms Trade) but content was never retrieved. `[UNCERTAIN]` — could indicate NGO scrutiny of a Leonardo export deal, could be unrelated. I will not speculate on content I haven't seen (Clause 35).

7. [S1] (SeekingAlpha via Bing, single source): Iveco Group's full Defence Business (IDV and ASTRA brands) was transferred to Leonardo S.p.A. in March 2026. `[PROBABLE — single source]`. This is real M&A activity, not adverse media, but it is DD-relevant: it adds two subsidiaries to Leonardo's ownership chain that have not yet been individually screened.

━━━━━━━━━━━━━━━━━━━━

*🧭 GENERAL DOMAIN KNOWLEDGE — clearly separated, VERIFY BEFORE USE* `[GENERAL KNOWLEDGE — VERIFY]`

None of the following is confirmed by the evidence above — this is my background knowledge of the sector, and it may be stale. Treat as leads for re-investigation, not findings:

- Leonardo S.p.A. (renamed from Finmeccanica in 2016 partly to distance from historical scandals) has the Italian Ministry of Economy and Finance (MEF) as largest shareholder (~30%), creating a standing state-influence/governance consideration for any counterparty relying on commercial independence.
- Historical bribery/corruption cases from the Finmeccanica era (AgustaWestland India VVIP helicopter deal, and separate Algeria/Panama-linked allegations) resulted in convictions and settlements through the 2010s. Current status (appeals, monitorships, compliance conditions) is unknown to me and must be verified fresh — I cannot confirm whether any compliance obligations from these cases are still live.
- Leonardo (via UK operations — Yeovil helicopters, Edinburgh/Luton missile systems) has recurring NGO/press scrutiny (CAAT, Amnesty-adjacent groups) over UK and Italian-origin components in weapons supplied to Saudi-led coalition partners, tied to the Yemen conflict. This may be exactly what the CAAT UK fragment in [S3] refers to — but I have not verified this.
- Leonardo DRS Inc. is the separately SEC-registered, NASDAQ-listed US subsidiary — this plausibly explains the 8-K conflict above.

━━━━━━━━━━━━━━━━━━━━

*💼 MATERIALITY TO A SUPPLY-CHAIN DD DECISION* [ASSESSED]

For a Tier-1 global defence prime this size, baseline entity-risk (shell company, beneficial-ownership opacity, sanctions exposure) is inherently low — that's not where the real risk sits. The risks that matter for a supply-chain decision are:

1. Multi-jurisdiction export licensing complexity (Italy/EU dual-use + UK ECJU + US ITAR via DRS) — genuinely material to any brokered deal.
2. Whether legacy corruption-case compliance monitorships still impose conditions on intermediaries — unverified, needs checking.
3. Reputational association risk if the counterparty relationship touches Saudi/UAE/Yemen-adjacent programmes — NGO-scrutiny-driven, not legal risk, but real for client-facing positioning.
4. Newly absorbed IDV/ASTRA entities [S1] are unscreened — they inherit Leonardo's compliance profile but haven't been individually checked.

None of this is confirmed adverse — it's where I'd point a properly-run search.

━━━━━━━━━━━━━━━━━━━━

*✅ RECOMMENDED ACTION*

1. Re-run digital search with narrow, named queries ("Leonardo Helicopters AgustaWestland corruption status 2026", "Leonardo Yemen arms export CAAT", "Leonardo DRS SEC 8-K July 2026") — the broad "geopolitical strategy" query failed entirely.
2. Pull Leonardo S.p.A. registry data directly from Registro Imprese (Italy) and Borsa Italiana filings — not generic web search.
3. Screen Leonardo DRS Inc. separately via SEC EDGAR to resolve the 8-K conflict.
4. Screen IDV and ASTRA as newly acquired entities following the March 2026 transfer [S1].
5. Retrieve the actual CAAT UK article behind [S3] before drawing any conclusion from it.

*📅 NEXT STEP*

Re-run the OSINT/DD pass with the corrected query set above within 48 hours — the current run is not usable for a DD file and should not be cited to a client or counterparty as-is.
