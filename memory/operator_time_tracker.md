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

---

## Session 2026-08-03 — 360 Prospector sweep, live log sweep, Programme −1 gates

**R-numbers shipped: 27** (R-F3644…R-F3670, less reservations folded into
batches). All ship-marked, pushed, and — where they touch a service — verified
live by `build_rev`.

**What it was.** A full static-analysis pass (the repo's own `.prospector.yml`
had never actually been installed and run: 32,313 messages over 2,250 py files),
an ESLint pass over a Node tier that had no semantic lint gate at all, a 15-cycle
live log sweep across all three apps, and four defects the operator reported from
WhatsApp in real time.

**The single finding that matters.** 7 of the first 9 defects were invisible for
the same reason: a call that could NEVER succeed, sitting inside a swallowing
`except`/`catch`. Prospector counts **1,117 `try/except/pass`** and **4,822
broad-except** in `aria_service/`. The symptom is never an error — it is "no
result", which is indistinguishable from a clean one. That is the
"unknown is never success" doctrine failing at the plumbing layer, and it is why
three mechanical gates (call-arity, auth-on-egress, semantic lint) were the
right response rather than 20 individual fixes.

**Notable individually:**
- `handleAriaMention` threw `ReferenceError` on EVERY WhatsApp @-mention — R-F1770
  had merged a comment into a `const` and deleted R-F1760's whole self-healing
  loop with it. Recovered from git history, not guessed.
- ARIA's entire curiosity-exploration loop had been dead behind a circuit breaker
  that reported the wrong cause (401, not "brain unreachable").
- The `DAN` jailbreak pattern was case-insensitive and hard-blocked the ordinary
  word "dan" in five languages — on a platform whose stated differentiator is
  Lusophone/Hispanic handling.
- The ecosystem rollup consulted only `red`, so 17 degraded organs certified
  themselves HEALTHY.
- A claim could be both `[unverified]` and `CONFIRMED`; worse, `[CONFIRMED]`
  exempted a sentence from grounding entirely.

**Estate:** aria-intel, aria-web and aria-wa all deployed and verified.
aria-web and aria-wa had no CI deploy route at all before this session, and
aria-wa had no `build_rev` — no deploy of the acute §25 output channel had ever
been provable.

**Operator decisions actioned:** coder lane enabled stage-only (rate 2000→6/hr),
full constitution restored to chat (`ARIA_LLM_COMPACT_PROMPT=0`), two corrupt
scripts removed, Fly deploy tokens minted + scoped + superseded ones revoked.

**Left open, honestly:** LLM vendor monoculture (needs funding — the only item
no agent can close); sensor coverage at 25/623 nodes; the training-pipeline
review half of §24; and whether ARIA-Coder's staged fixes are any good, which
cannot be known until she has produced some.

**Runway for the next session:** `docs/training_cycle_runway_2026_08_04.md`.

## Session 2026-08-04 — the LLM chain: a dead backup, a lockout that could not be lifted, and a channel gate that could not pass

**R-numbers shipped: 9** — R-F3680, R-F3681, R-F3685, R-F3686, R-F3687, R-F3688,
R-F3693, R-F3698, R-F3705. All ship-marked, pushed, and verified live by
`build_rev` (aria-intel `d6a276fb`; aria-web `d6a86a34` for R-F3688). Plus one
docs commit correcting a §18 entry that was inverted.

**Operator hours: not supplied → pace_ratio deliberately blank.** Agent
wall-clock spans most of a working day and includes ~10-minute cold boots per
deploy and a peer agent's builds; substituting it would inflate the denominator
with time nobody spent.

**What it was.** Two WhatsApp pages ("no fallback left" / "every provider
failed") that turned out to describe a chain running on ONE provider while two
funded ones sat locked out, and — after the operator said "anthropic has credit"
— a 24-hour lockout that no code path could ever lift.

**The single finding that matters.** Five of the nine defects are the same
shape: **a mechanism that exists to protect or heal silently no-ops, and the
surface reports the opposite.** R-F1758's "she never goes silent" guard compared
time REMAINING, so it shortened a cooldown by 5s instead of capping it at 5s and
had never once delivered its guarantee. A hard cooldown could only be cleared by
`_record_success`, and a cooling provider is never called — so the cooldown was
the sole cause of the silence that sustained it, for the full 24h. The recovery
probe built to fix that then failed on an unrelated HTTP 400 every 15 minutes
forever, recording nothing. Its own wrapper swallowed a crash at `logger.debug`.
The Telegram morning slot reported "no Grade A" for four days with twelve Grade A
signals in the store. In every case the honest signal existed one layer away and
nothing read it.

**Notable individually:**
- Two permanently-dead provider entries DISABLED the guard protecting the one
  provider that worked — configuring a backup made ARIA *less* available than
  she was with one. The readiness doc had predicted exactly this illusion; it had
  simply landed in the dispatch path rather than on a health surface.
- The pre-outage page named the provider that had **just failed** as the one
  "still serving", so the operator got "answers are NOT degraded right now" and
  "every provider failed" one minute apart.
- `ARIA_LLM_SHADOW` meant *hold the sovereign back* in one consumer and *insert
  it into production failover* in the other — the cautious action armed a
  mostly-offline RunPod hop. R-F3636 had fixed half of this and never reached the
  chain builder.
- The Telegram allow-list knew `contract_award` but not `active_tender`, so the
  procurement lane was split across two names and the open half — the lane the
  channel exists for — was dropped silently.
- §18 recorded the two API tokens as identical and warned against separating
  them. They had been separated, and the entry had the consequence backwards:
  while equal, `_auth_is_internal_var` could never be True, which did not "fail
  safe" — it made R-F2778 dead code. The pairing test §18 demanded had never been
  run; it was run this session and passes (external → 0 reports, internal → 27
  across 8 owners).

**Method note.** Three defects were found only by driving the live system by
hand, not by tests: the probe's empty system prompt (green tests, permanently
broken in production), the Telegram gate (verified by running the real selector
against the real feed), and the two dark wrappers (found by a mechanical §21a
audit of my own work rather than trusting the earlier wiring pass). Two of my own
probes were invalid and were retracted rather than reported — a detached SSH
process has no state-store connection, so its "0 gaps" proved nothing.

**Operator decisions actioned:** autonomy set OFF — and the real switch was the
durable override, not the fly secret, which was already `0`; setting it would
have changed nothing while reporting success. Telegram channel scope widened to
`active_tender` + `security_operation` + `political_transition`, `cyber_threat`
declined. Provenance gate investigated rather than relaxed, and left intact.

**Left open, honestly:** `test_rf762_state_backend_health` fails — NOT from this
session's work (proven by re-running against a stashed tree) and in a peer
agent's active files, so it was surfaced rather than fixed. The Telegram 07:00
slot is unproven end-to-end: the selector was verified live but no post had gone
out by session end. Of 39 live Golden Intel signals only 13 carry `source_adapter`
provenance — the gate rejecting the other 26 is correct, but why the supply skews
that way was never investigated. The full §16 suite was NOT measured: a peer agent
committed four times during the session and §16 is explicit that a moving tree
makes the measurement `VALID=NO`.

## Session 2026-08-11 — the search stack: a source that could not lie, and four absences that read as health

**R-numbers shipped: 9** — R-F3853, R-F3857, R-F3858, R-F3859, R-F3863, R-F3864,
R-F3865, R-F3868, R-F3870. All ship-marked and verified live by `build_rev`
(aria-intel `bc7b3a8b` → `aa44f08e` → `9648cfea` → `a59273bd` → `d0efeb34`;
aria-searxng deployed twice). Plus CLAUDE.md §17/§16/§27 corrections and defect
register entries C-22 (+postscript) and C-23.

**Operator hours: not supplied → pace_ratio deliberately blank.** Agent wall-clock
covers a full working day, most of it waiting on ~8–10 minute torch builds and
cold boots; substituting it would inflate the denominator with time nobody spent.

**What it was.** Started as "sort out anthropic billing and chain order". Two of
the three premises turned out to be false, and saying so was most of the value:
Anthropic was never broken (live 200s, real credit, 393 calls/$21.23 MTD, already
hard-pinned for DD and non-degrading), and the chain order was already correct —
making Anthropic global primary would have cost ≈$889/mo against a $600 cap
(measured: $8.01/M vs DeepSeek's $0.195/M, ~41×) and would have 404'd every call
because `LLM_MODEL` is pinned to a DeepSeek id. What WAS broken was SearXNG.

**Root cause, corrected twice by measurement.** Not "response cross-contamination"
(R-F3849's recorded diagnosis, and a peer's). Bing answers popular queries
correctly and serves a rotating soft-404/trending page for queries it has no hits
on, which SearXNG scrapes as ten well-formed results — 9-10/10 related on
Microsoft/BAE/London weather, 0/10 on Rosoboronexport/Modirum Gespi/nonsense. DD
and research queries are ALWAYS the niche case, so it failed hardest exactly where
ARIA depends on it. Then: Wikimedia 403s an unidentified client and 200s a
descriptive one on the SAME IP in the same second, so "blocked from a datacenter"
was the wrong diagnosis for the whole API tier.

**Method note — I shipped a regression and found it by reviewing my own work.**
R-F3853's per-engine filter dropped junk engines unconditionally, including when
EVERY engine was junk, which emptied the set; the R-F3844 gate reads an empty set
as "nothing found", so a detected backend failure became `ok=True, count=0` — a
false clean for an adverse-media sweep. It was firing in production within the
hour and was fixed by R-F3857. Separately, the first draft of `classify_429`
keyed on the phrase "rate limit" and therefore bucketed the REAL OpenSanctions
body ("exceeded its rate limit FOR THE MONTH") as pacing — the §18 defect written
fresh inside the function whose only job is to prevent it, caught solely because a
test asserted the real body text rather than a paraphrase.

**Two of my own probes were invalid and were retracted rather than reported.** A
detached `flyctl ssh` python3 process has no state-store connection, so the R-F1
None-on-error contract rendered cost reads as `spent_usd: 0.0` across 12 days of
absent daily keys. I came within one step of filing a P0 ("the cost meter is
blind, the cap cannot trip"); through the running server the same instant read
**$48.26 of $600 over 19,751 calls**. The same artefact later made the new engine-
health report look empty. Recorded in §17 so the next session does not repeat it.

**Two baseline entries were themselves the defect.** `test_all_search_backends_have_circuit_breakers`
was BLIND — a fixed 80-line window plus literal-name matching reported a backend
with two failure wires as dark; it could never go green, so it could never carry
information either. `test_backend_names_no_brave` asserted a REVERSED policy and
pointed at the defect: the obvious way to green it is to delete `["brave"] if
_brave_on else []`, which would have silently disabled ARIA's paid primary and
taken DD search with it. Local baseline 90 → 88.

**Operator decisions actioned:** Tier 2 settled as SearXNG (not Brave, not a new
vendor) — the instinct "paying = not in control" inverts here, because SearXNG has
no index of its own and borrows from companies that blocked us three times in two
months; it is acceptable only because R-F3844/3853/3857 plus the new health gate
mean it can no longer lie. Brave stays DD-only; the existing bounded Pass-2
student escalation (≤3/session, cost-shed) was left deliberately unwidened.

**Left open, honestly:** `yep` answered 20/20, 403'd within the hour (likely my own
~40-query bake-off), then RECOVERED — left enabled rather than ripped out, and the
engine list is now self-maintaining rather than hand-curated. Every general web
engine remains blocked from the Fly IP, which no engine-list tuning fixes; ARIA
survives on news/academic/memory backends measuring 10/10 related, so it is a
redundancy loss, not a blackout. `plan_limits` will only populate once the live
server itself calls Brave — verified by parsing real headers, not by waiting for
it. The full §16 suite was NOT re-measured: a peer agent and Codex were both
committing, and §16 is explicit that a moving tree makes it `VALID=NO`.

---

## Session 2026-08-11 (continuation) — the two "left open, honestly" items, closed

**R-numbers shipped: 2** — R-F3873 (`b0e089a4`), R-F3874 (`8ca447de`). Both live and
verified: `aria-intel` build_rev advanced `d0efeb34` → `8ca447de` → `729055df` via
workflow dispatch (peer had uncommitted work in `public/`, so `deploy.ps1` was
excluded per §11).

**The finding: both open items were one defect, and neither was what it looked like.**
Each had been framed as *waiting on the world* — an IP block to lift, organic Brave
traffic to arrive. In both cases **the provider was already publishing the answer on
every single response and ARIA was discarding it.**

- **R-F3873.** SearXNG reported `yep: "Suspended: access denied"` while
  `/api/aria/search/health` reported it as the **healthiest engine on the board**
  (`total: 81, ratio: 0.025, quarantined: false`) — measured in the same second.
  `record_observation` is driven by engines appearing in RESULT ROWS, inside
  `if normalised:`, so a blocked engine accrues no observations and its ratio stays
  perfect forever. A source that stopped answering was indistinguishable from one
  never asked. §27d had already made that surface BINDING for future sessions, so the
  instrument everyone was told to trust could not show a dead engine. A repo-wide grep
  for `unresponsive_engines` found no consumer at all.
- **R-F3874.** `_search_brave` passed `headers=` on exactly ONE of five branches: the
  **429**. So the gauge built to warn at 80% consumed could only ever be fed by the
  exhaustion it exists to pre-empt. Sharpest detail: R-F3870's own docstring records
  those headers being measured on an `HTTP 200 with results` — a branch its fix did
  not read. The previous session's "it just needs live traffic" was therefore wrong;
  traffic would never have populated it.
- **Third, and the one that would have bitten hardest:** `usage_report` read through
  non-strict `get`/`get_json`, so a wedged store rendered as `monthly: {}, plan_limits:
  null` — "Brave was never called" — indistinguishable from a healthy quiet key. §17's
  `spent_usd: 0.0` fabricated-P0 shape, reproduced inside the module written to prevent
  that class.

**Live proof, driven through the operator's real path** (`POST /api/aria/search/web`):
`blocked: ['wikidata','yep']` with `wikipedia serving=True`, and **`wikidata` was a
SECOND dark engine** ("Suspended: too many requests") that had been reporting perfect
health. Later, on organic traffic, `plan_limits_state` moved `never_observed` → **`fresh`**
with real windows recorded from a **success** — R-F3874's exact capability, proven by
production rather than asserted.

**Verification caught what reading did not.** Pass 1 found 4 regressions: reading
`resp.headers` put an attribute access inside the try/except that converts any
exception into a failed search, so a metering line could turn a served result set into
a backend failure. Contained by `_safe_headers()` mirroring the existing `_safe_body()`
— same hazard as R-F3857. Pass 2 clean: 1494 passed, the only 2 failures both already
in the recorded §16 baseline.

**Also corrected:** the `searxng/settings.yml` status comment now points at the live
`engine_relevance.blocked` measurement instead of asserting a frozen snapshot — that
file's whole history is such snapshots rotting.

**Flagged, not silently fixed:** `docs/cure/defects.md` collided TWICE in one day
(C-23 and C-24 both duplicated — a peer agent allocated C-24 hours apart from me). I
renumbered my own entry to C-25 and left the rest, because the register has **no
allocator**: R-numbers stopped colliding only when §2 gave them a reservation log after
9 collisions in 50h. C-numbers are still claimed by writing one into a heading. That is
a mechanism gap and the operator's call.

**Not done:** the full §16 suite re-measurement — the peer was committing throughout, and
§16 is explicit that a moving tree reads `VALID=NO`.

---

## Session 2026-08-11 (continuation 2) — the C-number allocator

**R-number shipped: 1** — R-F3878. Built at operator direction after the C-24
collision surfaced in the previous block.

**The problem was 6× worse than the collision that prompted it.** Measured in the
live register: 29 canonical claims under 25 distinct numbers — **C-18, C-19, C-22 and
C-23 are each claimed twice by unrelated work.** My C-24/C-25 was not an anomaly, it
was the norm. And the damage compounds, because the register's own cross-references
are already broken: *"the C-18 XSS residual"* names one of two unrelated C-18s, and
*"Deep review of C-19..C-21"* is a range over ambiguous numbers. A defect register
whose identifiers cannot be cited has lost the property that makes it a register —
and §26 makes it the binding record of what may be worked on at all.

**Cause, exactly as §2 describes it for R-numbers:** a C-number was claimed by
*writing a heading into a markdown file*. R-numbers stopped colliding only when they
got an allocator; C-numbers never did.

**Three design calls worth recording, each evidence-driven:**
- **Git is NOT an allocation source here**, though copying R-F3248 was the obvious
  move. Implemented, it moved the next number from `C-26` to **`C-296`** — `\bC-\d+\b`
  matched the Airbus **C-295** in a commit about defence hardware and an unrelated
  internal "C-3 gate". `R-F####` is a coined token and safe to grep; `C-` is a bigram
  in ordinary English. It also made `peek` contradict `audit`, which is the exact lie
  R-F3248 names. Removed, and pinned by a test so it is not helpfully re-added.
- **The write primitives are IMPORTED from `r_number_registry`, not copied** — they
  carry the R-F1026/R-F3187/R-F3200 fixes (per-PID temp file, Windows retry,
  fail-open lock, read-back verification) and a copy would let the two registries
  drift. Extracting a shared module would be tidier but §26 forbids refactoring
  inside a fix PR.
- **Allocation is monotonic**, never gap-filling: a gap is not evidence a number is
  free, it may still be cited.

**Two defects in my own work, both caught by verification rather than by reading:**
1. The gate used `sys.path` while `pre_commit_checks.py` never imports `sys`, and a
   bare `except Exception: return []` swallowed the `NameError`. **It would have
   certified "no collisions" forever** — the R-F3791 blind-guard shape, inside the
   fix written to stop collisions. Both failure branches now REPORT.
2. **The gate had no trigger that fires.** It was wired into `scripts/pre-commit`,
   which ci.yml runs — but ci.yml's push trigger carries
   `paths-ignore: ['docs/**','data/**','**/*.md']`, and a colliding commit touches
   ONLY `docs/cure/defects.md`, matching two of those. On the push-to-main path this
   repo actually uses, CI would have been skipped for exactly the commits the gate
   exists to catch. Fixed with a separate seconds-long workflow on the register
   itself; **verified firing in CI on push** (run 31509441108, green).

**Enabled, not merely wired:** `reserve_c_number.py` (reserve/peek/close/list/audit/
backfill), CI gate proven to exit 0 clean and 1 on an injected collision, §21a brain
wiring on both branches, CLAUDE.md §26a codifies the rule. The four existing
collisions are baselined **shrink-only** so the gate is on today; a third claim on
C-18 still fails. The 25 pre-existing headings were backfilled once and stamped
`claimed_by: backfill:register`, `claimed_at: null` — imported headings, not
reservations, because nobody reserved them and inventing a timestamp would put
fiction in the log.

**Deliberately NOT done:** renumbering the four existing collisions. Every one is
cited by other entries and by commit history; renumbering would break more than it
fixes, and it is the operator's call.

---

## Session 2026-08-11 (continuation 3) — DD on my own work

Adversarial review of R-F3873 / R-F3874 / R-F3878, verified against production
rather than against my own tests. Three claims held; three new findings came out of
it, one of which is a fix (R-F3883).

**VERIFIED LIVE, with evidence:**
- **Wired (§21a) — proven, not assumed.** `GET /api/aria/coder/gaps` carries four
  gaps from `search_engine_health:record_unresponsive` (yep, google cse, wikipedia,
  wikidata) with the full actionable detail. And only ONE per engine against 85
  unresponsive events, so the transition-dedupe holds — the alert cannot flood.
- **Enabled — proven red AND green on real work.** The C-number gate failed
  automatically on the peer's colliding commit `0f3dfab4` (`C-25 <- NEW`) and passed
  on the resolution `838e879b`. A gate observed only passing has not been tested.
- **Both meters run on production traffic.** Brave: `monthly {total 14, ok 9,
  empty 5}`, `store_readable: true`, `plan_limits_state: fresh`. Engine health:
  bing serving (142/0), yep blocked (82 result sets / 81 failures — it oscillates).

**FINDING 1 — R-F3883, fixed and deployed.** `searxng/settings.yml` disabled
`- name: google`, **which is a no-op: this SearXNG build has no engine by that
name.** The general-web one is `google cse`. Probed the live instance's own
`/config`: five of six disables matched, `google` matched nothing — so Google was
queried on every search and failed every time while the comment claimed otherwise.
Invisible until R-F3873 gave the list an instrument: `serving=False,
unresponsive=85, total=None` — 85 failures and **not one result set, ever**
(categorically unlike `yep`, which oscillates and is deliberately kept). Fixed,
aria-searxng redeployed, verified: enabled engines 82 -> 81, exactly one change,
`google cse` gone from `unresponsive_engines`, search still returns 10 results.
**Generalises §27d: a hand-maintained list does not merely go stale, it can be
silently INEFFECTIVE — a disable matching no engine looks exactly like one that
worked.**

**FINDING 2 — my own parser could be evaded by a stray `#`.** `_CLAIM_RE` was pinned
to `###`; an entry written `## C-30 ·` or `#### C-30 ·` was invisible, so the
allocator would reissue the number AND the gate would pass the collision. Widened to
`#{2,4}` with tests both ways. Found by reviewing my own module, not by a red test.

**FINDING 3 — surfaced, deliberately NOT actioned: `core.hooksPath` is unset.** The
repo ships `scripts/git-hooks/pre-commit` and `test_rf1958_precommit_hook_active`
asserts it exists in "the ACTIVE core.hooksPath dir" — but never asserts the config
is SET, and it is unset both locally and globally. So **no local hook runs at all**,
for any of the ~12 checks, not just mine. Pre-existing and repo-wide. My CI wiring is
the real enforcement and is proven, so nothing of mine depends on it. Not flipped
unilaterally: `.git/config` is shared with the live peer agent and enabling a
pre-commit hook mid-session would start gating their commits. One-liner when wanted:
`git config core.hooksPath scripts/git-hooks`.

**HONEST LIMITATION (not a defect):** R-F3874's pre-exhaustion alert still cannot
fire, because Brave advertises `limit 0` on the 31-day window, i.e. no monthly
ceiling. That is the correct reading (0 means uncapped, never exhausted), and the
report keeps saying `set BRAVE_MONTHLY_QUOTA to enable headroom alerts` — which is
now an honest ask rather than a fabricated one. Until it is set, the only protection
against a spent plan is the 429 classifier, which is live.

---

## Session 2026-08-11 (continuation 4) — "nothing left undone" sweep

**R-numbers shipped: 5** — R-F3884, R-F3885, R-F3886 (`0234e011`), R-F3887, R-F3888
(`4da045d6`). All live and verified.

**Theme: every mechanism found this round EXISTED, looked configured, and did
nothing.** None were missing features; all were dead wiring that read as health.

- **R-F3886 — the pre-commit staged path CRASHED, so ~12 checks failed open.**
  `python scripts/pre-commit` died with `NameError: name 'lines' is not defined` at
  two sites passing `lines` where the scope's variable is `added_lines`. The hook is
  deliberately FAIL-OPEN (blocks only on an explicit `VERIFICATION FAILED`
  sentinel), so a crash printed no sentinel and every check was silently skipped.
  **CI stayed green because `--check-all` runs `check_all_files()`, which never
  reaches those lines** — green in one mode, dead in the other, nothing comparing
  them.
- **R-F3885 — and the hook was never invoked anyway.** `core.hooksPath` was unset
  locally AND globally, while `test_rf1958` asserted the hook file exists in "the
  ACTIVE core.hooksPath dir" and never checked the config was SET. **TWO failures
  had stacked**, which is why "the checks are enforced" survived as a belief:
  activate the hook and it crashes; fix the crash and nothing calls it. Both fixed,
  hook activated, verified blocking a real violation (exit 1) and passing clean work.
  It then **caught a real §21a violation in my own `brave_usage.py`** (wire_failure
  on every error path, wire_success on none) one second after being revived — and
  later blocked a legitimate commit, which found R-F3888.
- **R-F3884 — the Brave cost recorder had ZERO callers.**
  `/api/aria/cost/external` still returned `by_service: {}` — verbatim the symptom
  C-23 cites as proof Brave was unmetered — because `cost_tracker.record_brave_call()`,
  purpose-built with a documented price and a `BRAVE_COST_PER_CALL_USD` override, was
  never called. R-F3868 built a SECOND parallel counter and **verified it against a
  different surface than the one whose emptiness defined the defect.** Now wired at
  `brave_usage.record_call` (the single funnel). Live: `{"brave": {"calls": 2,
  "cost_usd": 0.01}}`. Cap interaction checked FIRST — external spend feeds
  `COST_MONTH_PREFIX`, the same rollup `assert_monthly_cap` reads, so Brave now
  counts against the §17 ceiling (correct; headroom ample at ~$48/$600).
- **R-F3887 — a READ was gated by a WRITE-coalescing interval.**
  `get_external_summary` claimed to "surface pending coalesced records" above a bare
  `_flush_external_pending()`, which is time-gated (~15s). **This nearly made me
  "fix" a working feature**: I read `by_service: {}` twice and twice concluded
  R-F3884 had failed. Probe 1 was inside the window; probe 2 was a store-less local
  process (`state_store: no connection` → clean zero, the §17 trap verbatim). Both
  were INSTRUMENT errors, not defects.
- **R-F3888 — the revived hook blocked me for writing the word "empty".**
  `WINDOWS_INCOMPATIBLE_PATTERNS` carried `pty\.`, matching the substring in
  "empty."; and it scanned COMMENTS AS CODE, though the file's own quote-aware
  `_strip_comment` existed for exactly that. Guard is now `(?<![\w.])` (a bare `\b`
  still matches `self.resource.x`). A false positive in a commit gate is not small:
  the instinct is `--no-verify`, and a guard people bypass protects nothing.

**Not mine, recorded honestly:**
`test_rf2392_brave_region_sourcing_gate2::test_brave_escalation_credits_cell_free_stack_missed`
fails under a broad `-k` selection and passes in isolation. **Verified pre-existing**:
it fails identically with all five of my new test files excluded, and none of them
triggers it pairwise. Not in `docs/suite_baseline.json`. Same order-dependence family
§16 already documents; per §16, reproduce before adding another isolation fixture.

**Deliberate, and the operator should know:** `core.hooksPath` is now SET in this
clone's `.git/config`, which is shared with the peer agent — their commits are now
gated too. That is the repo's intent (the hook and its test both exist) and the hook
is fail-open, but it is reversible with `git config --unset core.hooksPath`.

## Session 2026-08-11 (aria-web security audit → jQuery 3 → 360 review)

**Attribution:** this is the OTHER agent's session, concurrent with continuations
1–4 above (which are the peer's). Two Claude Opus 5 agents shared the tree all day;
per the 2026-07-30 attribution caveat these are taken from the transcript, not from
the commit trailer.

**R-numbers shipped this session: 12** — R-F3845, R-F3852, R-F3855, R-F3860,
R-F3862, R-F3866, R-F3871, R-F3872, R-F3876, R-F3882, R-F3889, R-F3892, R-F3895.
(R-F3879 also mine: the jQuery upgrade ATTEMPTED and correctly REVERTED — shipped
as a reverting commit, superseded by R-F3882.) All ship-marked and verified live.

**Operator hours: NOT SUPPLIED → pace_ratio deliberately blank.** The operator was
engaged intermittently across a long span; agent wall-clock is not operator hours
and substituting it would inflate the denominator with unattended CI builds and
browser waits. Numerator recorded, ratio left for the operator to complete.

**Theme: three defects that every green signal agreed did not exist.**

- **R-F3882 — jQuery 3.7.1, and "breakage 2" was breakage 1 one frame down.**
  R-F3879 fixed the removed `.load(fn)` shorthand, could not explain why the lead
  form still would not bind, and reverted — the right call with the cause unknown;
  the error was stopping there. Waypoints' `refresh()` called `.offset()`
  unconditionally on `window`; jQuery 2 guarded it with a `typeof getClientRects`
  check, jQuery 3 does not, and the throw escaped the enclosing
  `$(document).ready()` at custom.js:107, so the form's binding at :124 never ran.
  **The form was never failing to bind — its binding code never executed.** Every
  ruling-out R-F3879 made was correct and aimed at the wrong frame. Sequential
  markers plus a try/catch separated them: execution reached 106, not 111.
- **C-27 / R-F3889 — an instrument built for observability, with no reader.**
  `brainWireStats()` counts delivered/dropped/throttled and records `lastError`,
  built by R-F2821 because "a signal that silently fails is still dark". All six
  call sites were under `test/`. In production the wire was **exactly as
  unobservable as before the fix**.
- **C-28 / R-F3892 — a verdict rendered without its reason.** `ECOSYSTEM: DEGRADED`
  displayed while the same response carried `degraded_reasons` that nothing
  rendered. It misled this very review: with only the badge visible the obvious
  suspect was the open `search:duckduckgo` breaker — wrong, and already displayed
  two rows up.

**Three of my own instruments were blind, each caught by self-testing it first:**
an iframe error harness that reported `[]` for everything (setting `src` replaces
the window and discards listeners); `read_console_messages`, which does not survive
navigation, so four "clean" page readings meant nothing; and `document.cookie`
reading `loggedIn:false` against an httpOnly session, one probe short of a false P0
about customer DD data leaking. **The earlier "zero page errors" claim for R-F3882
came from the blind harness and was withdrawn** — the conclusion stood on the
functional diff instead.

**Process, recorded because it cost real audit trail:**
- **Two of my commits were absorbed into the peer's** (`cbcab3ac`, `30e39f29`):
  their `git add -A` picks up anything in the index, so an explicit include-list is
  NOT sufficient protection. The code landed under their message, losing the
  R-number, reasoning and `Verified-by:`. Their pre-commit hook then blocked MY
  commit over THEIR shell script. Fixed by staging and committing atomically in one
  step; the operator's standing instruction is a `git worktree add --detach`.
- **Four R-number collisions from writing a number before reading the registry**
  (R-F3880, R-F3884, R-F3890 caught pre-commit; R-F3851 had already shipped and
  needed R-F3895 to correct). §2 exists for exactly this, and I broke it four times
  in one session.

---

## Session 2026-08-11 (continuation 5) — the two delegated decisions, taken

Operator delegated both open items ("do what is best for ARIA"). Both are now closed,
and chasing them turned up three more dead mechanisms. **4 R-numbers shipped**
(R-F3893, R-F3894, R-F3896, R-F3899 @ `2b38556f`, live).

**DECISION 1 — the local git hook: KEEP IT ENABLED, and make activation durable.**
Rationale: it caught two real defects within an hour of being revived (my own §21a
wiring gap, then a stray file I misplaced with `cp`), it is fail-open so a tooling
bug can never wedge commits, and R-F3888 removed the one false positive found. But
"enabled" could not rest on a command I happened to run, so:
- **R-F3896 — `--install` pointed at the look-alike `scripts/githooks` and
  DE-INSTALLED the working hook.** R-F1958 diagnosed exactly this ("the installer
  pointed at the look-alike scripts/githooks/ dir"), fixed only half — it added a
  hook to `scripts/git-hooks/` and never touched `install_hook()`. That directory
  still holds a **frozen Aug-3 copy** of the old checker: no C-number gate, still
  carrying the R-F3886 NameError, without the R-F3888 fix. Observed live: running the
  documented install command overwrote a correct `core.hooksPath` with the orphan
  **while printing "Installed:"**. An installer that de-installs the working hook is
  the most expensive dead wiring there is, because USING THE TOOL is what breaks it.
  Not deleted (freeze §26 needs three proofs) — recorded and pinned by a test.

**DECISION 2 — the flake: ROOT-CAUSED, not muted.** §16 says bisect rather than add
an isolation fixture on a hypothesis. Bisected to a 2-file, 1.6s reproduction and
traced it end to end:
    rf1031 TestCostCap leaves `safety._memory_cost_spent` over-budget
      → `_reset_safety_memory()` ran at the START of seven tests, never after
      → `load_governor.cost_pressure()` reads it (load_governor.py:193, "ONE source
        of truth for the day's spend") → `should_shed_paid()`
      → `student.py:1406` gates Brave escalation on `not _paid_shed`
      → rf2392 sees no escalation and fails
  A stale global in a unit test silently switched off paid-search escalation in an
  unrelated module — R-F2961's cost-shed doing its job on a number that was fiction.
  **R-F3894**: autouse fixture resetting AFTER every test. A guarantee that depends
  on every future author remembering it is not a guarantee.
  - **Correcting my own first attempt**: my initial bisect matched on "any failure"
    and converged on `test_rf2172_no_cost_lost_in_coalescing`, a KNOWN §16 baseline
    entry, not the failure I was chasing. Re-run per-test.

**R-F3899 — found by the push itself, and it is a real allocator defect.** The
pre-push verifier failed from a git worktree while the identical pytest selection
passed in both trees by hand. Cause: **GIT_DIR overrides `cwd`**, and git exports it
for every hook, so `r_numbers_known_to_git()` scanned whichever repo the environment
named — defeating R-F3248's `_repo_root_for` entirely. `reserve()` called from inside
a hook (a verifier, or ARIA's coder under one) allocates against the wrong history:
the exact collision the module exists to prevent, failing toward OVER-skipping so the
symptom is a high next number rather than an error. Measured: GIT_DIR makes 4/12
registry tests fail; 12/12 with the fix.

**R-F3893 — my own test pinned a LIVE REGISTER COUNT** (`len(claims) == 26`) and broke
hours later when a peer legitimately added C-27 *through the allocator*. A test that
fails whenever the thing it guards is USED is worse than no test: the only way to
green it is to bump a magic number. Both assertions now check invariants, and a new
AST guard prevents re-coupling.

**Process note:** all commits in this block were made from an ISOLATED git worktree
(§16 pattern) per operator direction, because a peer agent is active in the main
checkout. That caught a real contamination risk — an early attempt swept the shared
`r_number_reservations.json` into my commit, and the verifier then failed on the
PEER's unshipped R-F3897.

---

## Session 2026-08-11 (continuation 6) — DD pass 2: CI was red, and it was mine

**6 R-numbers shipped** — R-F3900..R-F3905. The previous DD verified production and
declared done; this pass checked CI and found my own commit had turned it red. **A
local run and a live probe are not a full verification** — the third surface is CI,
and it was the one I had not looked at.

**Started at 11 NEW failures against the recorded baseline. Ended at 2, both named
in §16's documented known-flaky set.**

- **R-F3900 — `search_engine_health.py` SHIPPED DARK on its success branch.** It
  carried `wire_failure` on every error path and nothing on any success path, so the
  brain could see the health tracker BREAK but never see it WORK. Now `@wired`,
  which covers both branches by construction. Caught by the CI wiring audit, NOT the
  pre-commit hook — the hook scans only STAGED files and this module predated its
  activation. The identical defect in `brave_usage.py` WAS caught by the hook. Two
  enforcement points covering for each other, working as designed.
- **R-F3901 — Gate B, 5 violations, and the decorators were RIGHT.** `brave_usage`
  and `search_engine_health` were simply absent from `MODULE_GAP_TYPES`, so both fell
  to `_default = agent_cycle_failure`. Registered as `engine_failure`: a paid search
  API and a search-source health tracker fail as ENGINES, not as agent cycles.
  Verbatim the R-F3428 precedent, which refused to rewrite sixty vetting decorators
  to match a default that did not describe them.
- **R-F3902** — `c_number_registry` read as newly orphaned because its only in-tree
  importer is its test; the real entry point is in `scripts/`, outside the audit's
  scan boundary. Baselined WITH its reason, and explicitly not exempted from the
  wiring gates.
- Also caught by a repo guard (R-F3459): my `test_rf3886` bounded a subprocess at
  600s, above the 120s per-test budget — a hang would have killed pytest with no
  summary instead of failing the test. Now 90s.

**THE BIG ONE — THE ENTIRE NODE TIER HAD BEEN UNGATED, and finding it took three
steps, each of which only became possible after the previous:**

1. **R-F3903 — the gate refused for 8+ commits without saying why.** `npm test`
   produced "could not parse TAP totals" and one line more. Refusing is RIGHT;
   refusing in silence is not — nobody could act, so it stayed red across both
   agents' work. Taught it to print the byte count, the last 40 lines, and to say
   explicitly when the capture was EMPTY.
2. **ONE CI run later the cause was a single unmistakable line:**
   `Could not find '.../test/**/*.test.mjs'`. **R-F3904** — ci.yml pinned
   `node-version: "20"`, below this project's own `"engines": {"node": ">=22"}`,
   and Node 20's test runner DOES NOT EXPAND GLOBS. **CI had been running ZERO Node
   tests** while the same command ran 1833 locally. Pinned by a test that checks
   every workflow against the declared engine.
3. **R-F3905** — with the suite finally executing, the gate reported 2 NEW and 2
   FIXED: the SAME TWO FILES, differing only by `\` vs `/`. The baseline was recorded
   on Windows and compared as raw strings. Normalised on BOTH sides so the existing
   baseline keeps working — unlike the Python gate, whose second baseline
   (`suite_baseline.ci.json`) exists for a difference that is genuinely platform-real
   (89 vs 165). A slash is not, and a second file would have pinned the artefact.

**The lesson worth carrying:** every one of these was a guard that existed and did
nothing, and the sequence only unlocked because R-F3903 made one of them explain
itself. **A gate that cannot say WHY it could not measure is a gate nobody can fix.**

**Left open, honestly:** the 2 remaining baseline-gate failures
(`test_rf795_brain_hook_tier_timeout`, `test_store_fact_skip_rag`) are both in §16's
KNOWN-FLAKY set, which records what has already been ruled out (R-F3841/R-F3846) and
says to reproduce before adding another isolation fixture. I root-caused one flake
this session (R-F3894) by bisection; the brain-hook family is a separate documented
investigation and I did not open it at the end of a long session.
Also flagged, not changed: `services/wa-listener/Dockerfile` still uses
`node:20-alpine`, below `engines>=22` — a separate image with its own deploy path and
an active peer agent.
