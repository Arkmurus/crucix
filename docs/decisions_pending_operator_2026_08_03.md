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

**UPDATE (same evening): a second agent has taken this on** — its task list is
"await measurement verdict and record VALID=YES/NO … refresh
docs/suite_baseline.json only if VALID=YES". Good: that is the correct
procedure, and I have not touched `suite_baseline.json` or `CLAUDE.md`.

⚠️ **But that measurement is very likely `VALID=NO`, and it is my fault.** I
committed to `aria_service/**/*.py` three times while it was in flight
(`d05ed69a`, `6f0d4dbb`, `fdbd6e61`), including **three new test files**
(`test_rf3657_call_arity_gate.py`, `test_rf3663_dan_false_positive.py`,
`test_rf3665_memory_self_claim.py`) which change the suite's own composition,
plus edits to `aria_engine.py`, `security_protocol.py`, `dd_layer_extensions.py`,
`bd_strategy.py` and `defence_source_seed.py`. That is exactly the R-F3597
corruption condition §16 warns about: `inspect.getsource` slices at line numbers
captured AT IMPORT, so a peer commit landing mid-run silently returns a different
function's body.

**The run should be discarded and re-taken on a tree I am no longer writing to.**
I have stopped committing to `aria_service/` as of `fdbd6e61` (deployed and
verified live). The three new test files are additive and green
(25 python tests), so the re-measured baseline should be *higher* by that count,
not worse.

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

## DR-E. `ARIA_LLM_URL` is forcing the small-model prompt onto every chat

**Evidence (live).** `_compact_prompt_active()` (`aria_engine.py:713`) returns
True **whenever `ARIA_LLM_URL` is set** — and it IS set on aria-intel (secret
digest `0a2a5f12fa6ad3af`, Deployed). Its own docstring accepts the consequence:
*"when her provider cools down mid-window, the fallback (DeepSeek) also gets the
compact prompt for that request — acceptable, documented."*

But ARIA-LLM is **not actually serving**: `/health` reports
`chain_order: ["deepseek","deepseek_backup"]`, `serving_provider: "deepseek"`,
and no ARIA-LLM entry anywhere in the chain.

So production is running the **~2K-char 8-rule compact prompt** — written for a
7B model that "latches onto whatever scaffold is loudest" — against a
frontier-class model, for **all** chat traffic, permanently. The full
`ARIA_SYSTEM_PROMPT` (~100K chars, 25+ constitutional clauses) is not being
used on that path.

**This is not theoretical — it caused a live incident.** On 2026-08-03 ARIA told
the operator *"I don't carry memory across chats. Each conversation starts fresh
for me."* That is false (§7: infinite memory, no TTL, no eviction; mem0 is a
first-class recall layer). It happened because the compact prompt said nothing
about memory and omitted the full prompt's clause 25 (no architectural
self-claims). **R-F3665 patched the compact prompt** with both invariants, so the
symptom is fixed — but the underlying exposure remains: every other clause the
full constitution carries is still absent from live chat.

**Options**
1. `ARIA_LLM_COMPACT_PROMPT=0` — explicit override; keeps `ARIA_LLM_URL` set for
   when ARIA-LLM is actually wired in. Smallest, most reversible change.
2. Unset `ARIA_LLM_URL` until ARIA-LLM is genuinely in the chain.
3. Change the activation rule so it keys off the **serving provider** rather than
   the presence of a URL — the honest signal.

**Recommendation: option 1 now, option 3 as the real fix.** A URL being *set* is
not evidence that a model is *serving*; the gate should read the live chain. Note
§16 records ARIA-LLM v0.1 as "NOT wired into live chain" — so the URL has been
set ahead of activation, and that alone silently downgraded the prompt.

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
