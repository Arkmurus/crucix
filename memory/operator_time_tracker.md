# Operator time tracker

**CLAUDE.md §20 close ritual: update this file with session hours + R-numbers
shipped + cumulative pace_ratio.**

## Why this file starts on 2026-07-30

It did not exist before today. `git log --all -- memory/operator_time_tracker.md`
returns nothing and `git rev-list --all --objects` finds zero matching objects —
the path has never been tracked. §20 has named it as a binding session-close step
for months and the step silently never ran, the same failure shape as R-F2623
(the §20 coding-RAG priming snippet raised `TypeError` on every invocation, so
that binding step never ran either). A rule that points at a file nobody created
is not a rule that is being followed.

**Prior sessions are NOT reconstructable and are deliberately not invented here.**
Commit timestamps would give a span, but a span is not operator hours, and
back-filling a plausible history is exactly the fabrication this project exists to
prevent. The series starts empty and accrues honestly from here.

## pace_ratio — DEFINED 2026-07-30 by the operator

```
pace_ratio = R-numbers shipped / operator hours
```

**"Shipped" means ship-marked AND verified live** (§11: a deploy is not done until
proven live by ancestry, not by the build_rev label). A reserved-but-unshipped
R-number never counts.

**"Operator hours" means hours the OPERATOR was engaged — not agent wall-clock and
not repo clock.** These are not interchangeable and the difference is large: on
2026-07-30 the repo clock spans 23h40m across two agents and includes unattended
Depot builds and ~10-minute aria-intel cold boots per deploy. Substituting repo
clock would inflate the denominator with time nobody spent, producing a number
that looks measured and is not.

**Operator hours are the one input an agent cannot observe.** They have to be
supplied. Where they are missing, the row records the numerator and leaves the
ratio blank rather than guessing — the same discipline as `pass=None` on the Phase
A gates: could-not-measure is never measured-and-found.

## Attribution caveat (2026-07-30)

Two Claude Opus 5 agents worked the same tree today, so the
`Co-Authored-By: Claude Opus 5` trailer does **not** distinguish authors — 58 of
today's 114 commits carry it, including several that were demonstrably the peer's
(R-F3527, R-F3530). R-numbers below are attributed from the session transcript,
not from the trailer. See [[two-agents-one-tree-hazard]] and
[[shared_tree_corruption_two_agents_2026_07_26]].

---

## Sessions

| Date | Shipped (mine) | Operator hours | pace_ratio | Cumulative |
|---|---|---|---|---|
| 2026-07-30 | **33** | **12.0** (operator-supplied) | **2.75** | **2.75** (33 / 12.0) |
| 2026-07-31 -> 08-01 | **36** | _operator-pending_ | _pending_ | _pending_ |

Cumulative equals the row because this is the first recorded session — the file
did not exist before today, and earlier sessions were deliberately not back-filled
(see above). Cumulative is `sum(shipped) / sum(hours)` across rows, **not** a mean
of the per-row ratios; averaging ratios would silently weight a 2-hour day equal
to a 12-hour one.

R-numbers for 2026-07-30: R-F3457, R-F3464, R-F3469, R-F3475, R-F3476, R-F3477,
R-F3479, R-F3483, R-F3485, R-F3486, R-F3487, R-F3491, R-F3494, R-F3495, R-F3497,
R-F3499, R-F3500, R-F3503, R-F3505, R-F3506, R-F3509, R-F3511, R-F3513, R-F3515,
R-F3517, R-F3518, R-F3519, R-F3521, R-F3523, R-F3525, R-F3526, R-F3528, R-F3529.
All ship-marked and verified live. The peer agent shipped R-F3520, R-F3522,
R-F3524, R-F3527, R-F3530 in the same tree and they are NOT counted here.

**What 2.75 does and does not establish.** It is one data point with no prior
series, so it is a BASELINE, not a benchmark — there is nothing yet to say whether
it is fast, slow, or typical. It becomes informative only against comparable later
days. Do not treat it as a floor to beat.

Note also what the 12 hours contained: ~25 minutes of box-wide 502s caused by
three deploys landing inside 17 minutes (mine plus the peer's two), each resetting
aria-intel's ~10-minute warm-up. That is denominator spent on contention, not on
work, and it is the concrete argument for batching deploys on a shared tree.

**Reading the ratio honestly.** R-numbers are not equal units of work: R-F3491
(one stale test fixture) and R-F3506 (a store reconnect that could wipe every
tenant's watchlist) both count as 1. A rising pace_ratio is therefore ambiguous
between "faster" and "smaller units", and it can be gamed trivially by splitting
work across more R-numbers. Track it as a trend on comparable days, never as a
target — cf. [[autonomous_coder_verdict_2026_07_23]], where the coder's raw
attempt count looked productive and the honest read was 1 gold fix in 52 attempts.

### 2026-07-30 — what the day actually produced

Grouped by workstream, with the outcome rather than the diff:

- **Live-log DD (15 cycles)** → `docs/LIVE_LOG_DD_2026_07_30_15_CYCLES.md`, 13
  findings. Three of my own findings were WRONG and were corrected in the doc:
  #8 (73% aiosqlite was a census of parked threads, not I/O saturation), #3 (the
  fallback cache was fine; every `complete()` had raised), #9 (the OpenSanctions
  key IS sent — the 429 is a monthly quota).
- **News pipeline** — silent permanent article loss closed on two ingest paths
  (R-F3486); MARKET_HEATING stopped counting syndicated copies as corroboration
  (R-F3487); archive-wide resumable classifier replay (R-F3494); three dark
  failure branches wired (R-F3495); selective enrichment with a confidence cap so
  a headline cannot pass as a read article (R-F3499/R-F3509); claim-level
  absorption that must be verbatim-quotable or refused (R-F3511).
- **Watchlist** — a store reconnect could wipe **every tenant's** watchlist
  (R-F3506); the UI reported success when nothing was removed (R-F3503); deleted
  DDs kept being monitored and logged by name (R-F3500); orphan reconcile, dry-run
  by default (R-F3505 — my first matcher read fields the index does not carry and
  would have deleted all 8 entries).
- **Truthful news UI** — the list and the category breakdown queried different
  populations, so a category read "No articles" beside a bar saying it had dozens
  (R-F3517 backend, R-F3518 web, R-F3519 dash-guard repair).
- **Temporal compounding** — the correlator treated time as a binary filter
  (R-F3521), the trajectory reached no surface (R-F3523), the two bands used
  different story identities (R-F3525), and ACCELERATING turned out to be
  measuring ARIA's own ingestion growth — 53 of 54 countries — until measured
  relative to the corpus (R-F3526). Live: 47 SUSTAINED / 3 ACCELERATING / 4
  DECAYING.
- **OpenSanctions** — the monthly plan quota is spent; a 429 now separates
  quota-exhausted (operator-only) from a per-second limit (R-F3528), and the DD
  screen gained a floor: local canonical OFAC/EU lists (24,953 rows live) when the
  paid aggregator cannot answer (R-F3529). Proven live on the real DD path with
  the quota still spent: `Rosoboronexport → screened=True, blocked=True,
  kind=local_canonical`.
- **Operator levers** — LLM cooldown clear after a billing top-up (R-F3513); the
  §17 doc claim that a restart clears a billing cooldown was false and is
  corrected.

### Standing operator items from this session

- **OpenSanctions plan quota is spent.** Screening degrades to local OFAC/EU
  instead of going dark, but the ~200-list breadth is unavailable until the plan
  is upgraded or the month rolls. Only the operator can clear it.
  Status: `GET /api/aria/sanctions/source/status`.
- **`pace_ratio` definition** — see above.
- **DD screen endpoints** (`/explore-deep`, `/sanctions/rca`,
  `/sanctions/divergence`) were 502ing; that was the peer's SIGSEGV crash loop,
  not endpoint latency as I first claimed. Healthy after their R-F3530: 0.2–1.3s.


### 2026-07-31 -> 08-01 - what the session actually produced

**36 R-numbers shipped, all live-verified.** Hours are NOT recorded: the operator
supplies them, and inventing a number here would corrupt the one metric this file
exists to hold. pace_ratio stays pending until they do.

**R-F3627 (added later on 08-01, after the operator reported chat still down).**
The WhatsApp page "BLOCKED: LLM chain - every provider failed" was recurring ~15
min apart - which is the ALERT SUPPRESSION WINDOW, not the failure rate; chat was
failing on every hard question. Root cause: on deepseek-v4-* `max_tokens` is a
COMBINED reasoning+content budget, so R-F3606's 800 -> 4000 raise and R-F3607's
2048 floor both moved the cliff instead of removing it, and the failure returned
~6h later at the new number (13,527 chars of reasoning against a 4,000-token cap).
The caller's budget is now RESERVED for the answer with reasoning headroom added
on top, plus ONE bounded same-provider escalation - failing over could never work,
because fallback.py hands the backup the identical budget.
**Live-smoked on the operator's own question** (Bulgaria travel risk): a real
answer, `degraded=None`.

**R-F3629 (live @ 5d8e4fb1).** Closed the two items R-F3627 left open. The
deepseek-v4 context window was understated 16x — MEASURED from the API's own 400
body (flash 1,048,576; pro 1,048,565), not read off a doc, and the first probe
was discarded as inconclusive (64,094 tokens, under the recorded limit). The
catch worth remembering: 65,536 was ALSO, unstated, the thing bounding spend, so
correcting it would have 16x'd worst-case prompt cost as a side effect —
capability (`_CONTEXT_WINDOWS`) and permission (`ARIA_MAX_PROMPT_TOKENS`) are now
separate. Also: R-F3627's retry gave each attempt the full `timeout`, doubling
the caller's clock; both attempts now share one deadline.

**R-F3630 — WRITTEN, VERIFIED ON TARGET, NOT SHIPPED. Handed off 2026-08-01.**
Root cause PROVEN by arithmetic: R-F3588 deliberately appends `_ambient_now_block`
AFTER the doc-mode length cap so a tail-trim cannot delete the clock (correct, and
~250 chars at the time). R-F3590 grew that block to 2,283 chars, so
20,000 + 61 + 2,283 = 22,344 — exactly what the R-F947/R-F2188/R-F2196 guards have
been reporting, red and unread. The cap stopped bounding the prompt; that cap
exists because a bloated prompt once truncated a customer's contract mid-clause
(Korvera UTS, 2026-05-27). Fix RESERVES the appendix instead of exempting it, so
both invariants hold at once, with an ERROR if it ever grows enough to starve the
constitution. 24/24 on the affected guards, compile gate 0 broken.
**Why it is not shipped:** the wide sweep surfaced 8 test names never run in
earlier selections, and the clean-worktree run that would attribute them was still
in flight at session close. Shipping before that diff would be an unverified
claim. Work preserved as a patch in the session scratchpad AND left uncommitted in
`aria_service/aria_engine.py` + `tests/test_rf3588_aria_has_a_clock.py` — a peer
agent is active in this tree, so do NOT `git add -A` over it.

Ran alongside two peers (a second Claude and Codex) in one tree, so every commit
was staged file-by-file rather than with `git add -A`.

**The four that mattered most, each found by running the thing rather than reading it:**

- **CI went green for the first time in two months.** The blocking wiring audit
  was demanding work already done (R-F3565: aliased imports and `@fail_wire`
  invisible to a literal scan), then the real backlog was closed 17 -> 0
  (R-F3567), surfacing four silent-failure paths on the way.
- **DMs to ARIA were silently dropped for weeks.** Baileys 7 addresses users by
  LID (`<id>@lid`) and the DM predicate tested only `@s.whatsapp.net`, so every
  direct message matched neither branch and vanished with no log (R-F3582).
  Groups worked, which is why it read as "DMs are broken" rather than "an
  addressing scheme is unhandled".
- **aria-wa's volume was 100% INODES with 645MB of bytes free** (R-F3596).
  47,619 Baileys `lid-mapping` cache files across ten orphaned accounts. Every
  write was failing ENOSPC - bindings, auth updates, account metadata - and any
  byte-based disk check called the volume nearly empty. The boot fsck line said
  `64512/64512 files` in every deploy log all evening.
- **ARIA engaged anyone who knew her number**, including `/teach` and `/correct`
  which write to a memory that never evicts (R-F3586). Now gated on one
  authorisation point, with phone-to-account binding behind it (R-F3587/3593).

**Two of my own fixes were wrong and were corrected the same session:**

- R-F3577 wired a consumer to a **retired transport** - nothing writes that Redis
  key. I verified the producer FUNCTION was called and never that it still writes
  the KEY, and my test asserted the key appears in a file where it survives only
  in a stale comment. Reverted by R-F3580.
- R-F3588 gave ARIA a clock and created a contradiction with the tool ANSWER
  SCOPE; she deadlocked and leaked her chain of thought to the operator.
  Corrected by R-F3591/3592.

**The dominant defect class, named:** NINE tests this session asserted WORDING
rather than the property - exact argument lists, one-liners, enumerated element
ids, substring URL matches. Each was green while the thing it guarded was broken
or had legitimately changed. Two were mine, one was Codex's, the rest predated
the session. Worth a deliberate pass of its own; it is not a series of slips.

**Standing operator items opened here:**

- `WA_REQUIRE_VERIFIED_SENDER=1` and `WA_ALLOWED_SENDERS=447786866459` are LIVE.
  The operator's own handset binding was **revoked** at 22:57 (testing the unlink
  dialog), so that admin number is currently the only thing reaching ARIA.
  Re-binding via `/wa-connections.html` would cover it by both routes.
- ~12,000 inodes of session material remain under ten orphaned `wa-accounts`
  dirs. Reclaimable, but deleting real WhatsApp credentials is an operator call.
- Gate A / Meta Cloud API is NOT signed off - the official gateway does not exist
  yet and `/api/whatsapp` is a deprecated Twilio route. Twilio is not needed;
  the next build is the direct Meta webhook around ARIA's real number.
