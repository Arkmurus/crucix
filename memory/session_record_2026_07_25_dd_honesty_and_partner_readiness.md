# Session record — 2026-07-25 — DD report honesty + design-partner readiness

**34 R-numbers shipped and live-verified** (R-F3017..R-F3068, excluding peer-owned
numbers). Live at close: aria-intel `d4cb28a0`, aria-web v370. All ship-marked.

## What the session was
Started as "finish the three carried-over items, then run a Deep DD to measure
quality". The DDs kept finding defects, so it became a defect-hunt driven by real
runs rather than code reading — which is the pattern worth repeating.

## Shipped, grouped

**Carried-over items**
- R-F3017 GB financial capacity: an UNKNOWN with a NAMED obstacle (CH holds large-PLC
  accounts as a scanned TIFF — proven live).
- R-F3018 deep research: `wait_for` CANCELS, so 40s of research was DISCARDED and
  "partial" meant ZERO. Cooperative deadline returns what it gathered.
- R-F3019/3020/3021 sanctions lists + `screened_at`, OK→COMPLETED, LEI-LAPSED.

**From a practitioner review of `dd_16db41eb5fa8`**
- R-F3022/3023 the wrong AMBER headline: "37 credible adverse items" = 10 URLs, none
  adverse. Reproduced 37→0.
- R-F3024 `previous_company_names` never fetched (a name SWAP with a co-located company).
- R-F3025 FCA attributed another firm's status (name match computed, never read).
- R-F3026 directors/PSCs collected then dropped by ALL THREE renderers.
- R-F3027 a 75-100% controller silently dropped (CH gives no registration number).
- R-F3028/3029 financial narrative contradictions; layer errors absent from data gaps.
- R-F3030/3031 a fabricated "family cluster" from a mis-parsed surname; the DD builds
  its own screen blob so an earlier stamp never reached it.

**Design-partner readiness**
- R-F3051 the PAID primary search contributed NOTHING (Brave drops the quoted phrase
  when an OR-block is present).
- R-F3053 a DD on the CEO of BAE Systems returned HARD STOP — mandatory refusal.
- R-F3054 "registry status 'active' is not a recognised live status".
- R-F3049/3050/3055/3056 PDF↔online parity, adverse media on every surface, clickable
  source links.
- R-F3059/3066 the digital layer could not fit its own ops (three passes).
- R-F3063 **P0** — company-register enrichment ran for a PERSON.
- R-F3067/3068 adverse-media terminal status; a person is not "unscreened".

## Method notes that earned their keep
- **Run the product to find the bugs.** Every serious defect this session came from a
  real DD, not from reading code. Three entity types (person, GB company, non-GB) each
  exposed a different class.
- **Capture a regression baseline BEFORE editing**, then `comm -13`. Result: 13→8
  failures, ZERO new, across ~1260 tests.
- **A timed-out suite is not a pass.** One full run hung and produced an empty FAILED
  list; it was re-run scoped rather than counted.
- **Read the stored blob's KEYS, not one field.** `keys=[]` vs `keys=[ok, findings…]`
  diagnosed the adverse-media status in a single probe.
- **Never `git stash` a file a peer may be writing** — it destroyed an in-flight fix.
- **The registry is the authority for R-numbers (§2)**, not a number in a comment.

## Open at close (handed over, not hidden)
- `test_rf740`, `test_rf526` ×2, `test_bucket_b`, `test_rf1656`, `test_rf1660`,
  `test_rf2286`, `test_rf2942/3` — pre-existing reds, confirmed by stash-diff.
  `test_rf1656`/`test_rf1660` assert a "Brave is a permanent stub" policy that R-F2318
  superseded — they contradict current reality and should be retired deliberately.
- Codex P1 items untouched: per-request search diagnostics (a global last-search object
  races across parallel DDs) and provider execution ledgers.
- **aria-intel live (`d4cb28a0`) is BEHIND origin** — the peer's R-F3069/R-F3075 are
  pushed but not deployed. Theirs to ship.
