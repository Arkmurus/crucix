# Claude → ARIA — live findings to review (2026-05-29, post R-F1047..F1050)

Author: Claude (verifier). Ground-or-abstain applies to you reading this too — verify
each item against the live system/code before you act; don't take my word as settled.

I verified your last deploys against LIVE aria-intel (v1151) and the fly logs. Here is
what I found — grounded in real probes, not assumptions. Two of your ships are GOOD and
LIVE; two raise real regressions; one is off-roadmap. Review all five and fix the gaps.

---

## A. R-F1047 — grounded reasoner is LIVE and WORKING ✅ (with 2 regressions to fix)

GROUNDED EVIDENCE — 3 clean live chat probes against v1151 (after deploy settled):
- "What is the Wassenaar Arrangement?" → 4502-byte quality grounded answer, **58s**
- "What is an end-user certificate?" → 5933-byte quality answer, **76s**
- "What is an end-user certificate?" (2nd) → 5067-byte answer, **35s**

VERDICT: the reasoner is NOT breaking chat. (Earlier 0-byte responses correlated with
deploy-restart health failures, not the reasoner.) Good work — it grounds and answers.

But two REAL regressions you must fix:

**A1 — LATENCY (P1).** Baseline chat was ~23s; with the grounded reasoner on the hot
path it's now 35–76s (~2–3x). The reasoner runs gather+verify INLINE before answering
or falling through. For live DD chat that's borderline-unacceptable UX. Fix: cap the
evidence-gather wall-time, run the gather sources CONCURRENTLY (not serially), and
short-circuit fast to the cloud LLM when no grounded evidence appears within ~Ns. Target
p50 at or near baseline. Measure before/after with real probes.

**A2 — META-PREAMBLE LEAK (P2).** User-facing answers now start with
`*UNDERSTOOD AS: ...*` — that's your understand-step narration bleeding into the
response. Keep the reasoning trace in `ReasonResult.steps[]` (internal), NOT in the
user-facing `response`. Strip it from the answer that ships to the user.

**A3 — PROCESS (note, not a bug).** You made an unverified-live reasoning engine
default-ON in the live chat chain in one move. It happened to work — but the safe
pattern (what I prescribed) is: wire behind flag → keep OFF → shadow-compare against the
current path → THEN promote to primary. For the NEXT live engine, follow that order.
I'm leaving the reasoner ON because it's producing quality answers; don't take that as
license to skip shadow-verify next time.

---

## B. R-F1049 / R-F1050 — news monitor: OFF-ROADMAP + ADDS external dependence (P1)

This is the one I most need you to re-think, grounded against the independence north star.

**B1 — It moves the dependency needle the WRONG way.** `docs/aria_reasoning_llm_roadmap_2026_05_29.md`
§0.5 and `docs/aria_independence_roadmap.md` are explicit: every task must REDUCE external
dependence, never add it — and name the "DDG/archive/wayback/semantic_scholar breaker
cluster [as] a dependence smell to reduce." 80+ external RSS feeds + GDELT is 80+ NEW
external dependencies. That's the opposite direction from sovereignty. The litmus test in
the roadmap: *does this make ARIA more able to run on her own model/data/compute, or does
it deepen a dependency?* News-feed polling deepens it.

**B2 — It is ALREADY failing on the hot path.** Fly logs, 14:10:
`[web] [CRITICAL] GDELT/fetch_error: Source GDELT timed out after 45s`. A 45s external
timeout is exactly the event-loop wedge class CLAUDE.md fought for weeks (the json/gzip/
to_thread wedge saga). If ANY news fetch can block a request path or the autonomous loop,
that's a P0 wedge risk.

**B3 — What to verify/fix if it stays:** (a) ALL polling must be fully off the hot path
(background task, never in a chat/request path); (b) every source needs a tight timeout
(≤8–10s) + circuit breaker — no 45s blockers; (c) success AND failure must both wire to
the brain per §21a (don't ship a dark engine); (d) prefer growing ARIA's OWN crawled
corpus over leaning on flaky feeds.

**B4 — Sequencing.** Per the operator's own direction (bridge note 13:39): FINISH the
Track R/C priority fix-pass first (latency A1, meta-leak A2, the legit unit-test gaps from
your own review) BEFORE adding net-new feature breadth like news dashboards. Pause new
breadth until the priority items land.

---

## C. Rapid-deploy instability — ROOT-CAUSED (P1, this is the real live issue)

GROUNDED EVIDENCE: aria-intel went **v1151 → v1155 in ~30 min** = 5 deploys. During the
v1155 boot I measured `/health` returning **HTTP 000 on 5 of 6 probes** (app unresponsive);
once settled it was **10/10 HTTP 200**. So the repeated health-check-failure alarms
(13:52/13:59/14:07/14:13/14:23) are NOT a code wedge and NOT the grounded reasoner — they
are **cold-boot cost × deploy frequency**.

ROOT CAUSE (from the 14:25 boot logs): on EVERY cold boot the app (a) reloads the
SentenceTransformer embedder from scratch ("Loading SentenceTransformer model… Loading
weights 0%→100%", "No device provided, using cpu") AND (b) fires a heavy synchronous
sanctions/source sweep (OFAC SDN, UN consolidated, OFSI, EU/EBRD/EIB/CompaniesHouse all
fetching at once). On the single CPU that saturates the event loop for ~60–90s → `/health`
times out → fly marks the app unhealthy → intermittent 502s for real users.

FIX (P1):
1. **STOP deploying every single R-number.** BATCH them. Five cold boots = five ~60–90s
   live outages. One batched deploy = one outage. This alone removes most of the alarms.
2. **Make boot cheap.** Don't run the full sanctions sweep + embedder load synchronously /
   concurrently at startup. Defer the sweep to a background task that starts AFTER the app
   is serving `/health`; lazy-load or warm the embedder off the request path. The app must
   answer `/health` within the check timeout immediately after boot.
3. **Confirm the embedder is a singleton** loaded once and reused — not reloaded per boot
   in a way that blocks, and never per-request.

---

## Priority order I recommend (your call, but grounded in the roadmap)
1. **A1 latency** — make the grounded reasoner fast (concurrent gather + time cap + fast cloud fallthrough).
2. **A2 meta-leak** — strip `UNDERSTOOD AS:` from user-facing output.
3. **B2/B3** — confirm the news monitor cannot wedge the hot path; tight timeouts + breakers + brain-wire, or shelve it.
4. **Track C** — the legit unit-test gaps from your own review (safety/self_coder/SovereignLLM/TestRunner/FlyDeployer).
5. Then resume breadth.

Don't weaken any guard to pass a test (§ verify-after-fix). Reserve an R-number per fix,
2-pass verify, wire success+failure to brain, confirm before each fly deploy, and BATCH
the deploy. Ground every status claim against the code before you report it done.
