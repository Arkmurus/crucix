# Operator decisions pending — 2026-08-03

Four items surfaced by the 360 sweep + 15-cycle live log sweep that **code
cannot close**. Each states the evidence, the options, and my recommendation.
Recorded here rather than left in a chat message, per §19e.

---

## DR-A. `ARIA_CODER_ENABLED=0` — the self-coding loop sees gaps and cannot act

**Evidence (live, 2026-08-03).** Log: `[aria_coder] fix_gap REFUSED for
0408025620ec82fd — ARIA_CODER_ENABLED='0' (the autonomous coder lane is off)`
followed by `gap 0408025620ec82fd not fixed: coder_disabled`. Corroborated by
the fly secret digest `9048cdb637b2dd86`, identical to `ARIA_AUTONOMOUS_ENABLED`,
whose value `/phase/gates` reports as `"0"`.

**The repo contradicts itself.** `CLAUDE.md` §21c: *"It must stay ENABLED
(`ARIA_CODER_ENABLED=1` …) and able to ACT, not just observe… if it can see gaps
but can't act, that's a P0."* `CLAUDE.md` §17: *"ARIA-Coder … DORMANT — needs
`ARIA_CODER_ENABLED=1` to fire."* **Production follows §17.** So `gap_detector`
runs every 15 minutes, queues gaps, and `self_coder` refuses every one — the
loop burns cycles producing refusals.

§21c also warns: *"Do NOT flip AUTO_DEPLOY=1 until the fixer reliably emits
complete, non-truncating fixes"* (2026-05-26: staged proposals were truncated
full-file stubs that would have wiped core modules). Note `ARIA_SELF_IMPROVE_AUTO_DEPLOY`
is a **separate** brake from `ARIA_CODER_ENABLED`.

**Options**
1. `ARIA_CODER_ENABLED=1`, `ARIA_SELF_IMPROVE_AUTO_DEPLOY` left OFF → the coder
   plans/validates/reviews and **stages** to `/api/aria/self/staged` for human
   review. Guardrails already in place: `MODIFIABLE_FILES`/`NO_AUTODEPLOY_FILES`
   (R-F851/F902), the truncation/preservation guard (R-F904), de-dup (R-F903),
   rate-rollback (R-F897), the hourly bucket (R-F901), and the $300/mo cap.
2. Leave it off, and **stop the detector queuing** work nothing will consume.
3. Leave both as-is and accept the contradiction.

**Recommendation: option 1.** It resolves the §21c P0 without touching the
deploy brake — the coder becomes an observer that *proposes*, which is what the
staging surface is for. It is also the honest reading of the design doc's L0–L6
model: the detector runs at L0 while the fixer is barred from L2 (low-risk
patches). Whichever you choose, **one of §17 or §21c must be amended** so the
next session is not told two different things.

---

## DR-B. No LLM vendor redundancy — one outage takes ARIA's reasoning down

**Evidence (live).** `/health` → `chain_order: ["deepseek","deepseek_backup"]`,
`general_vendor_depth: 1`, `preference_only_providers: ["anthropic"]`. During
the 9-minute sweep window **both** chain entries timed out:
`Provider deepseek failed (…): [deepseek] timeout — trying next` and
`Provider deepseek_backup failed (…): [deepseek_backup] timeout — trying next`.

Two keys at one vendor is not a fallback chain — it is one vendor with two
doors. §18 records the Anthropic top-up as declined 2026-05-18.

**Options**
1. Top up Anthropic → `preference_only` becomes a real fallback tier.
2. Add a different vendor as tier 2.
3. Accept single-vendor risk and document it as a known SPOF.

**Recommendation: 1 or 2, before any GPU spend.** The State-of-the-Art design
reaches for sovereign 2×H200 inference (§4.5); a second vendor is days of work
and removes a live single point of failure that H200s would not. Also relevant:
§17's 24h billing cooldown is self-sustaining, and the operator lever for it
(`POST /api/aria/admin/llm/cooldown/clear`) **raised NameError on both failure
branches until R-F3644 today** — so the recovery path was itself broken.

---

## DR-C. `suite_baseline.json` refresh — BLOCKED, deliberately not faked

`scripts/admin/suite_baseline.py` (R-F3373) is the real §16 gate: it exits 1 on
any failure absent from `docs/suite_baseline.json`. That JSON is **stale
(2026-07-28)** and `ci.yml` shows it is **not wired in**.

**I did not refresh it.** §16 is explicit: a full-suite number is only valid with
a validity record (SHA-256 over every tracked `aria_service/**/*.py` before and
after the run), and *"`VALID=NO` means DISCARD, not publish."* A second agent is
actively committing to this tree — 54 modified files during this session, and
HEAD moved under me twice. A run measured now is guaranteed `VALID=NO`.

**Needed: one quiet tree for ~30 minutes.** Then:
`python -m pytest aria_service/tests/ -q --tb=line -p no:cacheprovider --timeout=600`
with `scratchpad/measure.py` before and after, refresh the JSON, then wire the
gate into `ci.yml`. Until then the gate would fail on ~18 known failures.

---

## DR-D. Two junk files at the repo root

`create-acled.js` and `create-modules.js` **are not JavaScript.** They are pasted
chat instructions saved with a `.js` extension — the body is markdown prose
(*"Press **Ctrl+S** to save, close Notepad, then run: `node create-acled.js`"*).
Nothing references them and neither can parse. They are excluded from the new
ESLint config with that reason recorded.

**Recommendation: delete both.** Not done unilaterally — deleting files is
yours to call. They are harmless where they are, only misleading.

---

## Also recorded: the design-doc phase-name collision

`ARIA_State_of_the_Art_Design.md` §6 names its stages **Phase A–D**, where its
"Phase A (wks 0–4)" is a read-only assessment. `CLAUDE.md` §1 already uses
**"Phase A"** for the seven honesty gates, and it carries a binding rule:
*"refuse Phase B+ work until ALL Phase A gates close"* — enforced, i.e. an agent
reading both documents can be made to refuse legitimate work, or to believe
gate-closing work is complete when it is not.

**Recommendation: rename the design doc's stages to Programme 0–3.** Free to fix
now, expensive once anyone executes against it. Full review:
`docs/design_review_state_of_the_art_2026_08_03.md`.
