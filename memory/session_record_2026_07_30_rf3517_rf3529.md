# 2026-07-30 — news UI truth, temporal compounding, OpenSanctions floor

Covers R-F3517 → R-F3529 (mine). A peer Claude agent worked the same tree and
shipped R-F3520, R-F3522, R-F3524, R-F3527, R-F3530.

## What was wrong, and what it cost

- **The news page and its own breakdown queried different populations.** The list
  fetched the newest 100 and filtered them in the browser; "Coverage by Category"
  beside it aggregated all 1,000 server-side. A category whose articles were older
  than the newest 100 rendered *"No articles yet. Click Poll Now"* next to a bar
  saying it had dozens — and the instruction was false too, because polling adds
  NEW items and could never surface the older ones. R-F3517 moved the filter
  server-side over the same population; R-F3518 made the three empty states
  distinct (empty category / server inconsistency / empty corpus).
- **The correlator treated time as `if ts < cutoff: continue`.** A 13-day-old
  signal counted the same as one from this morning and everything older was
  discarded — against a ledger that retains ~100 years by design (72,729 signals
  live). Nothing could compound. R-F3521 added trajectory bands as an
  **annotation applied after every insight and score is final**, so history cannot
  create an insight, suppress one, or move a score.
- **The DD sanctions screen had no floor under a metered aggregator.** With the
  OpenSanctions monthly quota spent, `screen_with_aliases` reported
  `source_unavailable` while 24,953 local OFAC/EU rows sat loaded on the same box
  answering correctly. R-F3529 made local canonical the floor, consulted only when
  OpenSanctions cannot answer.

## The lessons that generalise

- **A verdict firing on ~all subjects is measuring the instrument.** Trajectory
  read ACCELERATING for 53 of 54 countries with 36 tests green. Corpus-wide 4.82x
  vs median country 3.93x — every country was moving with the tide, and the tide
  was ARIA's own ingestion growth after this week's news work. Fixed by measuring
  relative to the corpus (R-F3526): 47 SUSTAINED / 3 ACCELERATING / 4 DECAYING.
  → [[verdict-distribution-reveals-instrument-error]]
- **A fix that does not move the symptom is not the explanation.** R-F3525
  corrected a genuine defect (the two bands identified stories differently) and
  the live distribution came back byte-identical. That non-movement forced the
  real diagnosis. Banking the fix and moving on would have shipped a false metric.
- **Guards are vacuous in two distinct ways, and injection is how you find out.**
  A *differential* guard ("with history vs without") is defeated by a uniform
  change that moves both sides; a fixture that exercises one branch defends only
  that branch. Both were caught by deliberately injecting the defect the guard
  claimed to catch. Assert the property **absolutely** — snapshot before, require
  only named keys were written.
- **Compute without a carrier is not shipped.** R-F3521 emitted trajectory fields
  that every formatter dropped — `_shape()` in the route is an allow-list. Found
  by probing the live endpoint, not by 26 green tests. → R-F3523.
- **A metered dependency may have a free local equivalent already in the tree.**
  → [[metered-dependency-with-a-free-local-equivalent]]

## Corrections I had to make to my own claims

Recorded because each was stated to the operator before being checked:

- "The R-F469 breaker churns at 300s forever" — **false**. R-F1834 already backs
  it off exponentially to a 24h cap. A second cooldown added on that premise was
  removed; only the *name* of the failure was wrong.
- "The 502s are pre-existing endpoint latency" — **not established**. They were
  the peer's SIGSEGV crash loop. On a healthy box those endpoints answer in
  0.2–1.3s.
- A live `CLEAR/PERMITTED` looked like a sanctions false clean until IRGC came
  back BLOCKED — prod has canonical data my dev box does not. It was a genuine
  clean.
- Two `count=None` readings and a "502 at 62s" were my own harness: Git-Bash
  `/tmp` and Windows Python `/tmp` are different directories, so Python parsed a
  stale file. Use the scratchpad path for both ends.

## Operational notes

- **Never-false-clean survived a second source.** Adding one is how a compliance
  tool starts manufacturing cleans; `check_sanctions` is used precisely because it
  refuses (empty store / partial coverage / stale → INSUFFICIENT_DATA, never
  CLEAR). The match `lists` key is load-bearing: get it wrong and a genuine OFAC
  designation is demoted to a related-name observation.
- **Batching deploys matters.** Three deploys inside 17 minutes (mine + the peer's
  two) kept resetting aria-intel's ~10-min warm-up and produced ~25 minutes of
  box-wide 502s. Check `flyctl releases` before deploying on a shared tree.
- **Commit contents, not exit codes.** `git add <paths>` only; peer WIP
  (`coding_rag_indexer.py`) was verified excluded with `git show --name-only`.
- **Backticks in a bash heredoc are shell-interpreted** and silently ate words
  from a commit message. Write the message to a file in the scratchpad and use
  `git commit -F`.

## Verification

Every R-number above was live-verified by ancestry (`git merge-base
--is-ancestor <sha> <live-sha>`), not by the build_rev label. Failure sets were
diffed BY NAME against pre-change runs — never by count — and were identical in
every case. Full-tree compile gate clean before each deploy.
