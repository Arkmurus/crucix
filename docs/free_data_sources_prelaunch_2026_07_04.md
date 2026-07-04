# Free real data sources — pre-launch inventory (no paid membership)

**Prepared 2026-07-04.** Operator directive: *"we don't have the finance yet to have any paid membership until we launch — prepare all the relevant data points."* Binding constraint (CLAUDE.md §6 ARIA-mirrors-Claude / §17 cost discipline): **zero paid data memberships until launch.** Every source below is either **no-auth** or a **free API key** (free registration, $0). Paid providers are listed separately as the post-launch upgrade path.

Every endpoint marked ✅ was **live-verified on 2026-07-04** (live HTTP probe / module run), not cited from memory. Codebase status is grounded in the actual tree.

---

## 1. Free-key registrations to do (operator action — all $0, no card)

These unlock the highest-value aggregated feeds. Registration only; no payment.

| Provider | What it unlocks | Where | Auth |
|---|---|---|---|
| **trade.gov Consolidated Screening List (CSL)** | OFAC + BIS + State — **11 US export-screening lists in one feed**, daily 05:00 EST | developer.trade.gov | free API key |
| **SAM.gov** | US federal opportunities + entity registrations (defence procurement) | sam.gov → api.sam.gov | free API key |
| **Companies House (UK)** | UK company registry / officers / UBO | developer.company-information.service.gov.uk | free API key *(already held — wired)* |

---

## 2. Verified free sources by intelligence product

Legend — **Auth**: none / free-key / **PAID**(excluded). **Wired**: real=live fetch in code · stub=fabricated · brain=Python ingest · none=to-wire.

### Sanctions screening
| Source | Endpoint | Auth | Format | Verified | Wired |
|---|---|---|---|---|---|
| OFAC SDN | `treasury.gov/ofac/downloads/sdn.csv` | none | CSV | ✅ | **brain** `ofac_sdn_ingest.py` (real) |
| OFAC recent actions | Federal Register API (`foreign-assets-control-office`) | none | JSON | ✅ | **real** `ofac.mjs` (R-F2416) |
| UN consolidated | `scsanctions.un.org/resources/xml/en/consolidated.xml` (302→signed blob, follow redirects) | none | XML 2.1MB | ✅ | **real** `un_sc_sanctions.mjs` + brain `un_sanctions_ingest.py` |
| UK OFSI ConList | `ofsistorage.blob.core.windows.net/publishlive/2022format/ConList.csv` (+`.xml`) | none | CSV/XML 16.6MB | ✅ | **brain** `uk_ofsi_ingest.py` (real) |
| EU financial sanctions (FSF) | `webgate.ec.europa.eu/fsd/fsf/public/files/...?token=dG9rZW4tMjAxNw` (public token) | none | XML | ✅ | **brain** `eu_sanctions_ingest.py` (real, via sanctionsmap.eu) |
| **CSL (OFAC+BIS+State, 11 lists)** | `api.trade.gov/consolidated_screening_list/search?api_key=…&q=…` | free-key | JSON | ✅ endpoint | **none — to wire** |
| OpenSanctions **bulk** (2.13M entities, CC-BY) | `data.opensanctions.org/datasets/latest/default/…` (`targets.simple.csv`, `names.txt`, `entities.ftm.json`) | none | CSV/JSON | ✅ | brain uses OpenSanctions *API* (`opensanctions.mjs`) — **bulk file is the free no-key alternative** |
| World Bank debarred | `worldbank.org` debarred-firms | none | HTML/CSV | — | brain `sources/` |

> **Bottom line:** OFAC/EU/UK/UN sanctions are **already ingested for real** (Python brain + `un_sc_sanctions.mjs`). The single fabricated leftover is the Node sweep's **`sanctions.mjs`** (§5 below). The CSL free key would give one clean aggregated feed to replace it.

### Export control
| Source | Endpoint | Auth | Verified | Wired |
|---|---|---|---|---|
| BIS export-control **rules** | Federal Register API (`industry-and-security-bureau`, type=RULE) | none | ✅ | **real** `export_controls.mjs` (R-F2416) |
| BIS Entity List / DPL / UVL | via **CSL** (above) | free-key | ✅ | to wire |
| EU dual-use list | brain `dual_use_classifier.py` / `global_export_control.py` + `eu_dual_use.mjs` | none | — | **real** |

### Defence procurement
| Source | Endpoint | Auth | Verified | Wired |
|---|---|---|---|---|
| US federal awards | USAspending API | none | — | **real** `usaspending.mjs` |
| US opportunities | `api.sam.gov/opportunities/v2/…` (path needs confirm — 404'd on guess) | free-key | ⚠️ key+path | to wire |
| UK Find a Tender | `find-tender.service.gov.uk/api/1.0/ocdsReleasePackages` | none | ✅ 200 | check vs `procurement_tenders.mjs` |
| UK Contracts Finder | `contractsfinder.service.gov.uk/…` (OCDS) | none | — | to confirm |
| EU TED | `ted.europa.eu` API | none/free-key | — | via `procurement_portals.mjs` |

### Conflict / OSINT
| Source | Auth | Verified | Wired |
|---|---|---|---|
| ACLED | free-key (creds set) | — | **real** `acled.mjs` |
| GDELT DOC API | none | ✅ (429 rate-limit today) | **real** `gdelt.mjs` |
| ReliefWeb | none | — | **real** `reliefweb.mjs` |
| Telegram OSINT channels | none | — | **real** (sweep `tg`) |

### Corporate network / UBO / financial
| Source | Auth | Verified | Wired |
|---|---|---|---|
| SEC EDGAR (financials, XBRL) | none | — | **real** `sec_edgar.mjs` + `financial_health.py` |
| GLEIF (LEI) | none | — | **real** brain `gleif.py` |
| Companies House (UK) | free-key | — | **real** brain `companies_house.py` |
| UN Comtrade (trade flows) | none/free-key | — | **real** `comtrade.mjs` |

### Crypto sanctions
| Source | Auth | Wired |
|---|---|---|
| OFAC crypto addresses (in SDN) | none | **real** brain `crypto_sanctions.py` |

---

## 3. PAID — deferred to post-launch (the upgrade path, NOT now)

Do **not** wire any of these pre-launch. Listed so the launch-budget conversation is ready.

| Provider | Adds over free | Rough cost |
|---|---|---|
| **OpenSanctions API** (matching/entity-resolution) | fuzzy match + resolution as a service (vs self-hosting the free bulk) | subscription |
| **World-Check / Dow Jones** | analyst-verified PEP + adverse media depth | enterprise, "expensive" |
| **OpenCorporates API** | global corporate graph at scale | subscription *(declined 2026-05-12)* |
| **Sayari / Kharon / Exiger** | resolved ownership networks, analyst-verified | enterprise |
| **GovWin (Deltek)** | pre-RFP procurement intelligence | $12k–119k/yr |
| **Dataminr** | real-time alerting | $20k–100k+/yr |
| **ACLED** (higher tier) | commercial-use license / higher limits | subscription |

*(Cross-ref the competitive analysis in the OSINT-sweep business review, bridge reply 2026-07-04.)*

---

## 4. What "we already have" (so we don't re-buy or re-build)

- **Sanctions (OFAC/EU/UK/UN):** already ingested real by the Python brain + `un_sc_sanctions.mjs`. The free data points are **in place** for on-demand screening (`sanctions.py`, 1246 lines).
- **Export control:** real via Federal Register (R-F2416) + `dual_use_classifier.py`.
- **Procurement / conflict / corporate / financial / crypto:** real, free, wired (table above).
- **The only fabricated leftover in the live sweep is `sanctions.mjs`** (§5).

---

## 5. Immediate application — `apis/sources/sanctions.mjs`

Confirmed a **pure stub** (0 fetches, fabricated "12,000+ / Russia / Iran" literals) still wired into the sweep as `runSource('Sanctions', …)`. Two free options, no paid membership:

- **Option A (recommended) — retire it.** It duplicates `un_sc_sanctions.mjs` (real UN) and the Python brain's OFAC/EU/UK/UN ingests. Removing the stub eliminates the fabrication with the least surface. Downstream reader `source_registry_bootstrap.mjs:50` (`data.sanctions.updates`) must be repointed or dropped.
- **Option B — make it real via the free CSL feed** (`api.trade.gov`, free key) — one aggregated OFAC+BIS+State feed, same shape as the R-F2416 pattern. Needs the free trade.gov key (§1).

Either is a small, testable change (same discipline as R-F2416: capability test + honest-empty + live smoke).

---

## 6. Verification log (2026-07-04, live HTTP probe unless noted)

- UN consolidated XML → 200, 2.17 MB (302→Azure signed blob; must follow redirects).
- OpenSanctions bulk default → 2,132,251 entities; resources incl. `targets.simple.csv`, `names.txt`.
- UK OFSI ConList.csv → 200, 16.6 MB.
- EU FSF (public token) → 200.
- trade.gov CSL search (no key) → empty (key required) — endpoint live.
- UK Find a Tender ocdsReleasePackages → 200.
- GDELT DOC API → 429 (live, rate-limited).
- SAM.gov `/opportunities/v2/search` → 404 (needs correct path + free key — confirm at registration).
- `ofac.mjs` / `export_controls.mjs` module runs → 15 / 12 real dated Federal Register actions (R-F2416, live on aria-web `7cb7e0fb`).
- Codebase: `un_sc_sanctions.mjs` real (UN XML); brain ingests `ofac_sdn_ingest.py`→treasury.gov, `eu_sanctions_ingest.py`→sanctionsmap.eu, `uk_ofsi_ingest.py`→OFSI blob, `un_sanctions_ingest.py`.
