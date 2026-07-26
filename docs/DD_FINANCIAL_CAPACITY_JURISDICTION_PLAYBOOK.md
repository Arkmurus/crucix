# DD Financial Capacity — Jurisdiction Playbook

**Status:** binding methodology. **Owner:** DD engine.
**Created:** 2026-07-26 (R-F3124). **Supersedes:** nothing; extends R-F3017.

This document exists because "can this counterparty pay?" is the decision-critical
question ARIA most often leaves UNRESOLVED, and because the work of answering it was
being **re-attempted blind** in successive sessions. Every route below has been
**probed live**. Do not re-attempt a route marked DEAD without new evidence that the
underlying source changed.

---

## 1. The question, and what counts as answering it

`financial_capacity` on the decision scorecard is ANSWERED only when
`financial_health.data_available` is true AND `health_verdict` is not
UNKNOWN/UNAVAILABLE (`dd_schema.py`, `_dd_decision_readiness`).

**A figure that cannot be verified must never answer it.** A fabricated solvency
number is the single worst output this product can emit: it is confidently wrong, it
is the reason a customer would transact, and it is unfalsifiable to a reader who
trusted us. An honest UNKNOWN with a named obstacle is a *better product* than a
plausible guess.

---

## 2. Route ledger — what works, per source class

| # | Route | Status | Evidence |
|---|-------|--------|----------|
| 1 | **SEC EDGAR XBRL** | ✅ **LIVE** | R-F2322. Structured `companyfacts`, free, no key. Gives ratios + Altman Z''. **US-listed only.** |
| 2 | **CH iXBRL (UK)** | ⛔ **DEAD for large groups** | R-F3017 live probe: large groups file no iXBRL at all. Works for smaller UK filers. |
| 3 | **CH accounts PDF (UK)** | ⛔ **DEAD** | Cohort PLC 05684823 FY2025 = **129-page TIFF scan** (`Producer: libtiff/tiff2pdf`), zero text layer, every page an image XObject. pypdf extracts **0 chars**. |
| 4 | **FCA National Storage Mechanism** | ⛔ **DEAD (API defect)** | `POST https://api.data.fca.org.uk/search?index=nsm-search` is open + free, 5.3M docs, right fields — but **ignores the query**: every body shape returns full-index `match_all` with uniform `max_score 1.0`. Only `from`/`size` honoured. `sort` 404s. Cannot target an issuer. |
| 5 | **Subsidiary walk** (officers → other appointments) | ☠️ **FABRICATION TRAP — never enable** | Walking Cohort's 7 directors surfaced 8 active companies which were **THALES** entities (one shared non-exec). A naive "group member" heuristic bills Thales UK's finances as Cohort's. The PSC-proof guard correctly rejected all 8 → 0 proven. Listed-group subsidiaries name intermediate holdcos, not the ultimate PLC. |
| 6 | **Issuer's own annual report** | ✅ **LIVE (R-F3124)** | The only remaining route. Non-deterministic, therefore **gated** — see §3. |

**Rule:** a new jurisdiction is worked in this order — structured registry data first
(cheapest, deterministic), issuer publication last (most capable, most guarded).

---

## 3. The R-F3124 gate chain — how a non-deterministic route is made safe

Route 6 reads a PDF with an LLM. That is only acceptable because **four independent
gates** must all pass, and any failure leaves the existing honest UNKNOWN untouched.

```
Brave finds the report ──► G1 PROVENANCE ──► G2 TEXT LAYER ──► G3 GROUNDING ──► G4 ARITHMETIC ──► ANSWERED
                                │                │                 │                  │
                             not the           scanned          no verbatim      does not
                            issuer's own       (no text)          quote          reconcile
                                └────────────────┴─────────────────┴──────────────────┴──► UNKNOWN + named obstacle
```

| Gate | Rule | Why it exists |
|------|------|---------------|
| **G1 PROVENANCE** | document must be on the **issuer's own domain** | A third party's summary of a company's accounts is not the company's accounts. Matches distinctive name tokens against the flattened HOST — so `mitie.com/...pdf` passes and `uk.investing.com/equities/mitie-group` fails (the name is in the path, not the host). |
| **G2 TEXT LAYER** | ≥2000 chars of real text | Route 3's lesson. We do **not** OCR a balance sheet and call the result a solvency assessment. |
| **G3 GROUNDING** | every figure needs a **verbatim quote** present in the document | An ungrounded number is a model's guess. The quote is checked back against the source text. |
| **G4 ARITHMETIC** | `net_assets == total_assets − total_liabilities` (±2%) | **The anti-fabrication gate**, prescribed by R-F3017. A model inventing plausible figures will not produce a balance sheet that balances; a model reading a real one will. Missing/non-numeric ⇒ REFUSE. |

**Tolerance is 2%** — rounding and presentation differences only, never a
reconciliation "close enough" allowance.

---

## 4. Adding a new jurisdiction — the procedure

1. **Check this ledger first.** If the route is DEAD, do not re-probe without a reason
   to believe the source changed.
2. **Probe live before designing.** Every DEAD verdict above came from an actual HTTP
   call, not from documentation. Record the request shape and the observed response.
3. **Prefer structured data.** A registry that publishes XBRL/JSON is deterministic
   and needs no gate chain. Wire it like SEC EDGAR (route 1).
4. **If only a PDF exists**, reuse `extract_issuer_financials` — do not write a second
   extractor. Pass jurisdiction-specific candidate discovery in, keep the gates.
5. **Never add a heuristic that infers a group relationship** (route 5). Ownership must
   be registry-anchored (PSC/UBO with a registration number) or it does not exist.
6. **Wire both branches** (§21a): success and failure must reach the brain. A source
   that stops answering is a `source_failure` gap, not a silent empty.
7. **Add the route to the table above** with its live-probe evidence, in the same PR.

---

## 5. What this does NOT do, and must not pretend to

- It does **not** produce an Altman Z'' or ratio series from route 6 — only the
  headline balance-sheet position the document states verbatim.
- It does **not** cover private companies with no published report. For those, UNKNOWN
  with a named obstacle remains the correct output.
- It does **not** OCR. A scanned filing stays UNKNOWN.
- It does **not** substitute a parent's or subsidiary's figures for the subject's
  (route 5). Entity scope is disclosed separately (R-F3091/R-F3123).

---

## 6. Cross-references

- `R-F3017` — the four dead routes, and the "UNKNOWN with a named obstacle" contract
- `R-F2322` — SEC EDGAR structured financials (route 1)
- `R-F2782` — GB registry accounts as **evidence**, explicitly not an answer
- `R-F3091` / `R-F3123` — which entity the figures are about; ambiguity disclosure
- `R-F3119` / `R-F3122` — Brave is the DD search tier, ARIA's SearXNG the fallback
- `CLAUDE.md §6` — no paid third party without justification; §21a — wired, not dark
