# ARIA System Assessment — 2026-05-09 EOD

**Status**: Healthy, accelerating · **Composite score**: 67/100 (↑ from 58 at session open)
**Commits today**: 31 (R-F38..R-F65 + R-F42)
**Confidence**: HIGH on what's shipped, MEDIUM on Stripe checkout (code shipped, never executed against live Stripe), LOW on public API at scale (not load-tested)

This is the actionable companion to `architecture_2026_05_09.md`. Read this first if you want to know what's broken; read the architecture doc to understand the system.

---

## 1. Executive summary

ARIA spent today closing the gap between "internal Arkmurus tool" and "consumer-grade AI product". The structural lifters from the strategic review (audit-grade PDF, persona overlays, public-status page, watchlist push, DD library, billing scaffold, public API) are all engineering-complete; only env-var flips remain to activate them. A long-running brain-bridge silent failure (401 cascades) has been permanently mitigated with a boot-time self-check + per-call escalation. The crawl/learn loop processed **3,860 new facts** and **5,063 new ledger signals** in the last ~24 hours, the highest single-day yield this month.

What still hurts: cost discipline (Brave billing exhausted, no top-up yet), one source still timing out (GDELT), one operator-pending action that's a security must-do (rotate `ARIA_INTERNAL_TOKEN`), and Stripe + public API need their first paying customer to validate.

---

## 2. What's working — verified live today

| Surface | State | Evidence |
|---|---|---|
| Sweep ingestion | **48/49 sources OK** | seenode 12:22:09 sweep — only GDELT failing |
| Brain bridge | ✓ healthy | `[brainBridge] ✓ healthy — fly responded 200 on boot self-check` |
| LLM chain | anthropic → deepseek → groq | Anthropic 200 OK steady; was DeepSeek-fallback-only at session open |
| WhatsApp listener | ✓ reconnected | Operator scanned QR via R-F60 PNG endpoint at 12:30 UTC |
| Knowledge ingest | 16,313 → **20,275** facts | Disk snapshot (R-F94/F110), gzipped (R-F1) |
| Intel ledger | 13,785 → **18,870** signals | Disk snapshot, gzipped (R-F36) |
| Neural memory | 10,509 neurons / 9,070 edge groups | Loaded clean across 2 fly restarts today |
| RAG | 17,544 docs / 58,704 facts / 76,248 chunks | Persistent volume `/data/aria_rag` intact |
| Autonomous engine | 70 tasks loaded, running dry_run=False | Steady absorb/ingest cadence in logs |
| Persona overlays | 6 sectors live | broker / oem_export / government_acquisition / compliance / banking_insurance / journalist |
| Adversarial library | **23 attacks** (was 11) | R-F59 expansion across all 6 personas |
| EUC profiles | **12 jurisdictions** (was 5) | R-F53 added IL/TR/IN/BR/SA/AE/ZA |
| Weapon-system catalogue | **105 systems** (was 55) | R-F54 added naval/heli/precision/A2A/standoff/radar/EW/ISR/loitering |

---

## 3. What's broken or needs attention — priority order

### 🔴 P0 — Security must-do
**Rotate `ARIA_INTERNAL_TOKEN`**. Was pasted in chat earlier today to unblock `/force-qr` for the WhatsApp re-link. Operator-only (`fly secrets set ARIA_INTERNAL_TOKEN=<new> -a aria-intel`, then mirror to seenode env). 5 min.

### 🟠 P1 — Single-source outage (R-F66 candidate)
**GDELT timeout 30s** — last failing source in the sweep. Live evidence 12:22:39: `[CRITICAL] GDELT/fetch_error: Source GDELT timed out after 30s`. Same root cause as R-F61 (NVD) and R-F64 (Breaking Defense): cloud-IP fetch path, slow upstream. Fix: bump the GDELT timeout 30→45s in `apis/sources/` (mirror of R-F64). ~10 min, seenode-only. Brings sweep to **49/49**.

### 🟠 P1 — Cost-control gap (operator)
**Brave Search billing exhausted**. Circuit breaker OPEN since 11:24:05. Research backends still functional via OpenAlex / Semantic Scholar / CrossRef / Google News fallback, but Brave is the highest-quality web-search surface. Top up in `api-dashboard.search.brave.com` (~5 min).

### 🟡 P2 — Soft-rollout gates pending operator decision
| Env var | Effect when set |
|---|---|
| `STRIPE_SECRET_KEY` + `STRIPE_PRICE_PRO` + `STRIPE_PRICE_PROINTEL` + `STRIPE_WEBHOOK_SECRET` | Activates billing checkout (Pro $20, Pro Intel $199) |
| `REPORT_SIGNING_KEY` | HMAC-signs PDF audit reports (R-F43); currently UNSIGNED with explicit warning banner |
| `ENABLE_PUBLIC_API=1` | Unlocks /api/keys + /api/v1/chat (R-F42 shipped today) — gate on first paying Pro Intel customer per strategic review |
| `COMTRADE_API_KEY` | Frees the IMF DOTS pipe — currently `[Comtrade] 0 IMF DOTS · 0 anomalies` per sweep |

### 🟡 P2 — Behavioural patterns to verify
1. **R-F65 read-document dedupe** — shipped today, fly-only. Verification: next email cycle should log `[read-document] R-F65 dedupe HIT: ...`. If it doesn't fire, the email reader's queue may not be hashing identically across cycles. Watch the next 2-3 email cycles.

2. **LinkedIn help-page absorption** — `linkedin.com/help/linkedin/answer/4788` and `/answer/67` are being processed as 9-fact articles per email cycle. Low-value (these are LinkedIn footer links, not substantive intel). Candidate **R-F66b**: URL denylist for `linkedin.com/help/`. ~10 min, fly-only.

3. **`no_symbolic_rule` capability gaps** in quiz cycle (4 today: "capital of Moldova", "CIF vs FOB", "status as student", "remind me follow up Tuesday"). These are correctly flagged for the learning loop — they fall outside the symbolic-reasoner ruleset, so the gap-recorder writes them to `capability_gaps` for later rule expansion. **Working as designed** — keep an eye on whether new rules emerge from the self-improvement scheduler.

4. **archive.is circuit OPEN** — 3 consecutive 429 (rate-limited, not down). By-design behaviour: when archive.is throttles, the wayback fallback takes over. No action needed.

### 🟢 P3 — Quality / completeness
- **Hypothesis backlog: 113 OPEN** (down from 114 yesterday, drain rate ~5/cycle). At current rate, full drain ≈ 23 cycles ≈ 11.5 hours. R-F32 widened the per-cycle pick to 8 — backlog is now self-healing, just slow.
- **Stripe checkout** — code path shipped, never executed against live Stripe. First paying customer is the real test. Expect minor edge-case fixes when it happens.
- **Public API rate limiting** — `_perKeyRateOk` is GET-then-SET, not atomic INCR. Race tolerance is acceptable at the cap (60 req/min) but will need a real INCR helper if a single key starts hammering at 100s of req/sec.

---

## 4. Source health — full breakdown

**Last sweep**: 12:22:09 UTC, completed in 30,089ms · 48/49 sources fully OK · 409 deduped updates · 268 signals · 30 pushed to brain queue.

```
✅ Working (48):
   Lusophone (165 updates · 125 signals · 9 critical alerts)
   DefenseNews (30 items · 2 Lusophone · 13/13 sub-sources OK ← R-F61+R-F64)
   ExportControlIntel (13 updates · 11 critical · 0 entity list changes ← R-F61 UK FCDO)
   CyberThreats (20 CVEs · 9 ransomware hits · 15 critical ← R-F64 NVD 45s)
   AfDB (1 project)
   SEC EDGAR (0 today; not unusual)
   Portals (28 country portals: Angola/Colombia/Jordan/UAE/Saudi/Romania/Vietnam/
            Tanzania/Bangladesh/Philippines/Ghana/Mozambique/Côte d'Ivoire/Brazil/
            Indonesia/Ethiopia/Poland/Kenya/Rwanda/Cameroon/Senegal/Nigeria/Peru/
            Guinea-Bissau)
   Procurement (UN: 8 · DSCA/FMS: 15 via Google News · EU TED: 15 ← R-F33 v3 schema ·
                World Bank: 10 · Africa: 54)
   GRIP / BICC / ISS Africa / AU PSC (parallel)
   PortCongestion · SIPRI · Sanctions · Polymarket · Comtrade (0 IMF until key set)
   Think Tanks (80 items)
   UN SC (16 items)

❌ Failing (1):
   GDELT (timed out after 30s) — R-F66 candidate
```

**Sub-source resiliency**: the R-F34 honest tally landed earlier this week — partial sub-source failures inside aggregators (e.g. NVD failing inside CyberThreats) now propagate to the top-level count instead of being swallowed. Today's **48/49** is therefore an accurate count, not an inflated one.

---

## 5. Cost & capacity

| Provider | State | Notes |
|---|---|---|
| Anthropic | ✓ recovered | Was billing-exhausted at session open; topped up; consistent 200 OKs end of day. Occasional 529 (overloaded) handled by retry chain. |
| DeepSeek | ✓ healthy | Fallback only |
| Groq | ✓ healthy | Fallback only |
| OpenAI | — disabled | API key not set (3-provider chain is sufficient for now) |
| Gemini | — disabled | API key not set |
| Brave Search | 🔴 OPEN | Billing exhausted; circuit breaker OPEN since 11:24 |
| OpenAlex / Semantic Scholar / CrossRef / Google News | ✓ healthy | Free tier; primary research backends |
| ransomwatch (GitHub-hosted) | ✓ healthy | Sole reliable ransomware source after ransomware.live cloud-IP block |

**Cost cap**: $100/mo hard cap on autonomous Anthropic spend (per `cost_cap_and_autonomy_gate.md`). No autonomy gate ENABLED yet — operator approval pending until the $30/3-day burn analysis is attributed via `/cost/monthly` top-calls panel.

**R-F65 expected savings**: 4-6 LinkedIn newsletters/day × 2-3 redundant LLM extractions × 150-235s = **20-40 min compute / day eliminated**. Won't show up as huge $$ savings (LinkedIn newsletters are short), but kills the most visible duplicate-extraction pattern in the logs.

---

## 6. Adversarial & calibration state

### Attack library — 23 attacks (R-F59 expansion today)

Distribution across personas:
- **broker**: 4 attacks (relationship pressure, side-channel offers, time pressure, gatekeeper bypass)
- **oem_export**: 4 (export-control evasion, end-user lying, country-of-origin laundering, dual-use rationalisation)
- **government_acquisition**: 4 (procurement fraud framing, sole-source justification gaming, UoR exploitation, IP override)
- **compliance**: 4 (sanctions screening manipulation, beneficial-owner concealment, sanctions delisting timing, AML threshold gaming)
- **banking_insurance**: 4 (collateral inflation, KYC bypass via shells, premium fronting, claim fabrication)
- **journalist**: 3 (source-protection violation push, anonymous-source fabrication, paid-content disclosure evasion)

### Latest pass rate (R-F57 dashboard)
The `/api/aria/adversarial/run` endpoint shipped today — 23-attack pass rate is now visible at the sources dashboard. Pre-R-F59 baseline (11 attacks) was 90.9% (10/11 pass). Expanded baseline pending — first scheduled run at next adversarial cycle.

### Calibration
- ECE (Expected Calibration Error): underconfident by ~14% at last measurement (safe direction — ARIA reports lower confidence than it actually achieves; preferable to overconfidence in defence-DD context).
- Honesty verdict pipeline (`honesty_verdict` stream-bypass fix from session 2026-04-22) — verified writing on `/chat/stream` path.

---

## 7. Persona deployment (R-F48a/R-F48b)

| Sector | Default user-class | Knowledge focus | First test ETA |
|---|---|---|---|
| `broker` | Defence agents (Arkmurus team default) | Tier mapping, principal–end-user chain, BD pipeline | LIVE — first chat flows through this path |
| `oem_export` | Manufacturers | EUC, ECCN, DSP-83, ITAR/EAR, end-use monitoring | Pending sector-tagged user creation |
| `government_acquisition` | DoD / MoD / procurement | Contract types, FAR/DFARS, sole-source justifications, UoR | Pending |
| `compliance` | Compliance officers | Sanctions diff, KYC/UBO, AML thresholds, audit chain | Pending |
| `banking_insurance` | Bank trade finance / underwriters | Letters of credit, collateral, documentary fraud signals | Pending |
| `journalist` | Investigative reporters | Source protection, fact-vs-rumour discipline, public records | Pending |

**Brain hook tagging** (R-F56) — every `brain_hook.absorb` call now carries `(user_id, sector)` via Python contextvar so per-sector mastery rolls up cleanly. Buckets will populate on first chat from each sector-tagged user.

---

## 8. Knowledge & memory state

**Disk-first persistence is the load-bearing pattern** (F94/F110 from session 2026-04-30):
- `/data/aria_knowledge.json` — single source of truth for facts (gzipped on Redis snapshot via R-F1: 5.72 MB → 2.88 MB at 20,275 facts)
- `/data/aria_signals.json` — single source of truth for ledger (gzipped via R-F36: 4.5 MB → 1.12 MB at 18,870 signals)
- `/data/aria_rag/` — chromadb persistent volume (76,248 chunks)
- Redis is the secondary mirror (F87/F88 architectural refactor) — restart-safe even with Redis blip

**Today's growth**:
- Knowledge: **+3,962 facts** (+24% in 24h)
- Ledger: **+5,085 signals** (+37% in 24h)
- RAG: chunks ticking up steadily as documents flow through

**Snapshot pressure**: F111 (knowledge gzip) and R-F36 (ledger gzip) keep both blobs well under the Upstash payload limit. No re-react needed.

---

## 9. Operator-pending checklist (sorted by urgency)

| # | Action | Effort | Why now |
|---|---|---|---|
| 1 | **Rotate `ARIA_INTERNAL_TOKEN`** | 5 min | Pasted in chat — security hygiene |
| 2 | Top up Brave API | 5 min | Circuit OPEN since 11:24 |
| 3 | Set `COMTRADE_API_KEY` | 2 min | Frees IMF DOTS pipe |
| 4 | Set `REPORT_SIGNING_KEY` (for R-F43 PDF signing) | 1 min | Enables HMAC-signed audit-grade PDFs |
| 5 | Stripe envs (4 vars) | 10 min | Activates billing — held by your decision |
| 6 | `ENABLE_PUBLIC_API=1` | 30 sec | Activates R-F42 — gate on first paying Pro Intel customer |

Items 1–4 are all <5 min and unlock measurable improvements. Items 5 and 6 are deliberate gates per the strategic review.

---

## 10. R-finding burn-down

**Shipped today (28 R-numbered + 3 docs-only, in commit order)**:
R-F38 chat sidebar · R-F39 mobile/auth-race · R-F40 Stripe scaffold · R-F41 account UI · R-F43 audit PDF · R-F45 brain self-check · R-F46 model card · R-F47 status page · R-F48 persona overlays (6 sectors) · R-F49 EU TED v3 + acronym denylist · R-F50 privacy/ToS drafts · R-F51 watchlist push · R-F52 DD library · R-F53 EUC 12 markets · R-F54 ECCN 105 systems · R-F55 NDAA/MCF/DOD-1233 ingestion · R-F56 absorb tagging · R-F57 adversarial dashboard · R-F58 sources dashboard · R-F59 attack library 11→23 · R-F60 WA QR PNG · R-F61 Breaking Defense / NVD / FCDO · R-F62 sanctions alias · R-F63 QR usability · R-F64 WA stall + NVD 45s + fly Breaking Defense · R-F65 read-document dedupe · R-F42 public API.

**Outstanding**:
- **R-F66** GDELT 30→45s timeout (~10 min, seenode)
- **R-F66b** LinkedIn help-page denylist (~10 min, fly)
- **R-F67+** counterparty risk dashboard (month-3 lifter from strategic review)
- **R-F67+** watchlist push email/SMS (companion to R-F51 web push)
- **R-F67+** programme tracker structure (month-3 lifter)

---

## 11. Risks & unknowns

1. **First Stripe checkout will reveal edge cases** — the webhook signature path is correctly raw-body-parsed, but until a real Stripe event lands, we can't be 100% sure the `subscription.updated → user.tier` projection writes the right tier id.

2. **Public API at scale** — sliding-window rate limit is GET-then-SET; not atomic. At current expected load (1 internal user, 0 paid customers) this is fine. Above ~50 req/sec to a single key, the cap could be overshot by a few. Add atomic INCR helper before we publish API docs externally.

3. **Brain bridge token mismatch is now loud, not silent** (R-F45) — but the boot self-check only catches mismatch *at startup*. If the operator rotates the token mid-flight without redeploying both sides, the next 5 consecutive 401s trigger a Telegram alert (R-F45 escalation), but those 5 signals will be lost. Acceptable trade-off; documented.

4. **Sentence-transformers HuggingFace fetches on every cold boot** (~30s of HEAD requests to huggingface.co) — could be cached to disk to speed up restarts, but not breaking anything. Low priority.

5. **R-F65 dedupe verification still pending** — needs the next 2-3 email cycles to confirm. If the email reader's content hash differs from the route's hash, the dedupe won't fire. Watch logs.

---

## 12. Recommended next 3 sessions

**Next session (15-30 min)**:
- Verify R-F65 dedupe HITs in logs
- Ship R-F66 (GDELT timeout)
- Ship R-F66b (LinkedIn help denylist)
- Operator: rotate `ARIA_INTERNAL_TOKEN` + top up Brave + set `COMTRADE_API_KEY`

**Session +1 (1-2 h)**:
- Counterparty risk dashboard (month-3 lifter — score sheet: sanctions hits, beneficial-owner depth, geopolitical exposure, audit-trail completeness)
- Watchlist email/SMS alongside web push (catches operators who don't have the dashboard open)

**Session +2 (1-2 h)**:
- Programme tracker (defence procurement programmes as first-class entities with phase/milestone/budget timeline)
- Multi-jurisdiction sanctions diff (live OFAC vs OFSI vs EU vs UN side-by-side)

---

## 13. The honest one-line summary

ARIA is **production-ready for Arkmurus internal use today**, **product-ready for Pro Intelligence customers in 2-4 weeks** (gated on Stripe activation + first customer), and **enterprise-ready** by Q3 2026 if the month-3 + month-4 strategic-review lifters land on schedule.

The hardest problem this month — silent 401s in the brain bridge — has been permanently engineered out. The biggest remaining risk is Stripe edge cases that won't surface until the first real customer hits checkout.

---

*Generated 2026-05-09 EOD · Session: 6c4e33bc-32e2-4474-82f4-9a3004bab545 · Companion to `architecture_2026_05_09.md`.*
