# 360 Prospector sweep — 2026-08-03

Full static-analysis pass over **both tiers**: the Python brain (`aria_service/`)
with Prospector at `strictness: veryhigh`, and the Node tier (`server.mjs`,
`lib/`, `apis/`, `services/`, `public/`) with ESLint.

## How it was run

**Backend** — `.prospector.yml` was already in the repo but Prospector was not
installed in any venv, so this profile had never actually run. Installed
`prospector[with_bandit,with_mypy,with_vulture]` into `.venv` (dev-only tooling,
same footing as `requirements-dev.txt`; §6 governs ARIA's *runtime* deps).

```
python -m prospector --profile .prospector.yml --output-format json aria_service
```

9 tools ran: pylint 4.0.6, mypy 2.3.0, bandit 1.9.4, vulture, dodgy, mccabe,
pycodestyle, pyflakes, profile-validator.
**32,313 messages / 2,250 files / 429s.**

**Frontend** — the repo has no ESLint. `npm run lint` (`scripts/lint.mjs`) is a
`node --check` **syntax** gate only, and it passes 506/506. A temporary ESLint
flat config (`js.configs.recommended` + correctness rules) was used for the
sweep and then removed. Vendored/minified assets excluded.
**696 problems / 519 files.**

## The headline

> **Every Node defect below produces perfectly valid JavaScript.**
> `node --check` parses all of them, so `npm run lint` was green throughout.
> Three of the four are latent `ReferenceError`s that only fire at runtime, on
> paths no test exercised.

The Python defects share one family too: **a call that could never succeed,
sitting behind a swallowing `except Exception`.** The failure never surfaced as
an error — it surfaced as *no result*, which reads as "nothing found".

---

## Fixed in this pass

| R-number | Tier | Defect |
|---|---|---|
| R-F3644 | brain | `admin_llm_cooldown_clear_ep` returned `JSONResponse` on both failure arms, but `routes/aria.py` never imports it at module level → `NameError`. §17 documents this endpoint as *the* remedy for a 24h LLM billing cooldown, so the operator got an opaque 500 exactly when the lever was needed. The success path returns a plain dict, which is why it survived unnoticed. |
| R-F3645 | brain | `redis_store.py` defines `async def set(key, value, …)` at module scope, **shadowing the builtin**. `lrem()`'s in-memory fallback called a bare `set()` → `TypeError: set() missing 2 required positional arguments`. Reached whenever no sqlite/redis backend is active **and** on the fall-through taken when a live Redis `LREM` raises — so a transient Redis blip became a hard TypeError. |
| R-F3646 | brain | Two `wire_failure(...)` calls in `news_monitor.py` passed `summary=` / `source_id=`, which that function does not accept (they belong to `wire_success`). Both raised `TypeError` into `except: pass`. A vault source auto-suspending after N failures, and a vault website scraping empty, **never reached the brain** — while looking wired to any grep (§21a). An AST sweep of all 581 wiring call sites found **only these two**. |
| R-F3647 | brain | `dd_orchestrator` passed the whole transaction dict as one positional arg to `tbml_detection.analyze_transaction`, which is **keyword-only** → `TypeError` on every call, swallowed by `except Exception: continue`. `_tbml_results` was therefore **always empty**, so the trade-based-money-laundering screen silently produced nothing on every DD that supplied transactions. R-F2496 built a never-false-clean rollup for this data; its input was always `[]`. Now also counts supplied-but-unscreenable transactions so an empty screen cannot read as a clean one. |
| R-F3648 | brain | `_launch_deep_dd_bg` called `canonical_entity_id` positionally; it is keyword-only → `TypeError` every time, and the `except` silently downgraded the de-dup key to a lowercased raw name. Two spellings of one entity no longer collided, so the in-flight guard could stack concurrent 840s deep-DD jobs. Same defect as R-F1842. |
| R-F3651 | web | **`handleAriaMention` could not reply at all.** R-F1770 (`6ff63e20`) merged its own comment into the `const … isLong` declaration and took R-F1760's entire self-healing strategy loop with it — `progressCb`, the `conversationManager` loop, `rawReply`, `reply`, and the honest all-strategies-failed fallback. The function then reached `rawReply` and threw `ReferenceError: rawReply is not defined` on **every @-mention**. The leftover unused `conversationManager` import is what made the deletion detectable. Restored, with `progressCb` refreshing the composing presence instead of sending interim messages (honouring R-F1770's intent). |
| R-F3652 | web | `lib/whatsapp/waListener.mjs` calls `reportOutcome?.(…)` on five branches of two HTTP routes, but **nothing in the module ever defined or imported it**. Optional-call syntax does not help: `foo?.()` guards a null/undefined *value*, not an undeclared *binding* — it still throws `ReferenceError`. Both routes threw on every path, including success, so the §25 delivery wire they were added for (R-F2113/R-F2107) never reported once. |
| R-F3653 | web | Two `case 'investigate'` labels in one switch; JS runs only the first, so the second was dead. Its unique input sanitisation (stripping WhatsApp JID mentions like `@201219301748858`, which the protocol substitutes for display-name @-mentions) has been moved into the live alias handler — where it now covers `/dd`, `/background`, `/profile` too — and the dead block removed. This switch had already hit this exact bug once with `/feedback`. |
| R-F3654 | web | `else if (status === 'active' && existingUser.status === 'suspended')` sat **behind** a bare `else if (status === 'active')`, so it could never execute. Every reactivation of a suspended user sent the **welcome** email instead of the reactivation email and wrote `approve` into the admin audit log instead of `unsuspend` — a quietly falsified audit trail. |

### Tests

- `aria_service/tests/test_rf3644_3648_prospector_360.py` — 11 tests.
- `test/prospector-360-rf3651-rf3654.test.mjs` — 11 tests.
- `aria_service/tests/test_rf2212_2214_sources.py` — **corrected**. Its spy was
  `lambda **kw: calls.append(kw)`, which accepts any keyword, so it stayed green
  while the real `wire_failure` call threw. It now binds the real signature, so a
  bad call shape fails the test (§23: a test that is green while the live path
  fails is a *wrong test*).

---

## Verified NOT bugs (recorded so they are not "fixed" later)

- `brain_hook.py:910 _dropped_absorb` — pylint `undefined-variable` is a **false
  positive**; `global _dropped_absorb` is declared at line 851 and the module
  assignment is at 1567 (after the function, which is fine at runtime).
- `openai_compat.py:317 raise _last_truncation` — guarded by
  `is not None`; pylint does not narrow.
- `redis_store.py` `set[int]` **annotation** — inert because
  `from __future__ import annotations` makes annotations strings. Qualified to
  `builtins.set[int]` anyway, since anything resolving them at runtime would
  subscript the coroutine function.
- **The R-F2118/R-F2119 §21a wiring blocks are NOT systemically dead.** 68 files
  carry the block; an AST audit of module-level `wire_*` calls found only
  `geoip_lookup.py` with its `wire_success` nested inside an `except: pass`. Even
  there the module is still wired — the earlier `_ws(...)` call fires on the
  normal path — so it is dead code, not a dark module. Left alone.
- `proactive_lead_hunt_ep` defined twice in `routes/aria.py` (5435 GET / 21464
  POST) — a Python name collision only; FastAPI captured each function object at
  decoration, so both routes work.

---

## Open — not fixed, ranked

### 1. There is no semantic lint gate on the Node tier (root cause of 4 of the 9 above)

`npm run lint` is `node --check` only. It cannot see an undefined identifier, a
dead `else if`, or a duplicate `case`. All four Node defects were invisible to it
and to the full test suite.

**Remediation:** add `eslint` + `@eslint/js` to `devDependencies` with a flat
config. **Do not wire it into `npm run lint` immediately** — the current tree has
**696 problems / 180 files**, so a hard gate would fail CI on day one. Land it
as a reporting step, burn the backlog down, then make it blocking. Not done here
because adding a dependency and changing the CI gate is the operator's call.

Highest-signal ESLint rules on the current tree (vendored assets excluded):

| Count | Rule | Why it matters |
|---|---|---|
| 116 | `require-atomic-updates` | genuine async read-modify-write races on module caches (`apis/sources/gdelt.mjs`, `acled.mjs`) |
| 69 | `no-promise-executor-return` | value returned from a Promise executor is silently discarded |
| 34 | `no-undef` | **all now fixed** — was the WA bug class |
| 13 | `no-useless-assignment` | computed-then-discarded values, incl. `lib/compliance/screen.mjs:198 itarStatus` (benign — exhaustive reassign) and `scripts/monitor/dashboard_honesty_probe.mjs:89 crossFlyOk` (worth a look) |
| 6 | `preserve-caught-error` | rethrows without `cause`, losing the root error |
| 4 | `no-misleading-character-class` | emoji/surrogate-pair regexes in `lib/aria/proactive.mjs:650`, `lib/self/bd_intelligence.mjs:32` |

### 2. Two root-level scripts do not parse

- `create-acled.js:54` — `Parsing error: Unexpected token create`
- `create-modules.js:73` — `Parsing error: Unterminated template`

`scripts/lint.mjs` deliberately scopes to `*.mjs`, so these are outside every
gate. They are one-off generators, not runtime code — but they are broken.

### 3. Backend correctness backlog (not triaged individually)

From the 32,313 messages, **2,260** are correctness-class. After removing the
categories dispositioned above, the notable untouched clusters are:

- `mypy:unreachable` ×158 — a mix of defensive `isinstance` checks mypy can prove
  dead and genuinely dead branches. Needs per-site judgement; some are real.
- `pylint:cell-var-from-loop` ×15 — closures capturing the loop variable; a
  classic late-binding bug class.
- `pylint:no-value-for-parameter` / `unexpected-keyword-arg` — remaining sites
  not fixed here: `dd_layer_extensions.py:566` (`_run_tbml_classifier` missing
  `low`/`high`), `routes/aria.py:23137` (`agents_send_message_ep` passes
  `sender_id`, target wants `from_agent`/`to_agent`/`payload`),
  `web_explorer.py:734`, `bd_strategy.py:93`, `defence_source_seed.py:629`,
  `routes/aria.py:4963`. **Each is the same always-TypeError family as R-F3647
  and R-F3648 and should be assumed broken until checked.**
- `pylint:undefined-variable` ×4 in `portal_registry.py` (`get_solver`,
  `detect_and_solve_captcha`, `_dsc1707`, `_get_solver1719`) — mypy reports the
  same lines unreachable, so they are probably behind an early return; confirm
  before acting.
- `bandit` B105/B106 ×49 (hardcoded-password heuristics) — untriaged, mostly
  expected to be field-name false positives, but unreviewed.

---

## Live log sweep — 15 cycles, 2026-08-03 18:31–18:40Z

Method: 15 cycles; each captured a 20s `flyctl logs -a aria-intel --no-tail` window
plus a `/health` snapshot; every 5th cycle also swept `aria-web` and `aria-wa`.
**2,047 raw aria-intel lines, 300 each for web/wa.**

**Estate state: healthy.** `/health` = `operational`, diagnostic **GREEN (76 pass /
0 warn / 0 fail / 2 deferred)**, event loop p50 0.2ms / p95 1.0ms, state backend
sqlite green, autonomous engine **enabled, running, L3, 98 tasks**. `aria-wa`
heartbeating every 3 min, `connected=true`, one clean 428 reconnect at 15:40Z.
**Zero ERROR, zero CRITICAL, zero traceback in the entire window.**

### Fixed from this sweep

| R-number | Finding |
|---|---|
| **R-F3655** | **ARIA's curiosity-exploration loop is completely dead.** `lib/self/explorerScheduler.mjs` made **all 7** of its brain calls with **no Authorization header** (`/api/aria/curiosity`, `/think` ×3, `/curiosity/resolve`, `/brain/signal`). Live log: `GET /api/aria/curiosity … 401 Unauthorized` from an internal `fdaa:` 6PN address. The 401 threw → `recordFailure(_BRAIN_CIRCUIT)` → after 2 failures the circuit **opened**, after which every 3h tick logged `"Brain circuit open — skipping run"`. **A permanent auth bug presented as an intermittently unreachable brain** — the log named the wrong cause (§22), which is why it survived. All calls now route through an authed `brainFetch` helper. |
| **R-F3656** | Same defect in `lib/telegram/telegramCommands.mjs` (8 brain calls). Here the fallback was *to the local LLM*, so `/identity` and `/curiosity` silently degraded to local answers instead of the brain's — indistinguishable from "the brain had nothing to say". |

Token chain mirrors `lib/self/learning_store.mjs:420`, the working sibling in the
same directory. Tests: `test/brain-auth-rf3655-rf3656.test.mjs` (9, green).

**Corroboration:** the ESLint pass had independently flagged
`explorerScheduler.mjs:54 no-useless-assignment` — the same function. Static
analysis and production logs converged on one dead path.

### Open findings from the sweep — evidence, not yet actioned

1. **`ARIA_CODER_ENABLED=0` — the self-coding loop sees gaps and cannot act.**
   Live log: `[aria_coder] fix_gap REFUSED for … — ARIA_CODER_ENABLED='0'` then
   `gap … not fixed: coder_disabled`. Confirmed twice over: the log line, and the
   fly secret digest `9048cdb637b2dd86`, identical to `ARIA_AUTONOMOUS_ENABLED`
   whose value the gate probe reports as `"0"`. **§21c calls exactly this a P0**
   ("if it can see gaps but can't act, that's a P0") while §17 records the coder
   as DORMANT — the two sections disagree, and production follows §17. Decision
   needed: enable under the existing guardrails, or stop the detector queuing
   work nothing will ever consume.
2. **No LLM vendor redundancy.** Chain is `["deepseek", "deepseek_backup"]` —
   `general_vendor_depth: 1`. During the window **both** timed out
   (`Provider deepseek failed: timeout`, `Provider deepseek_backup failed: timeout`).
   Anthropic is `preference_only`, un-topped-up (§18). A DeepSeek outage takes
   ARIA's reasoning down entirely.
3. **Event-loop stalls:** 4 × `Main loop heartbeat stale for N s — possible
   event-loop stall (stall #N)` in a 9-minute window, despite p95 = 1.0ms. The
   profiler captures the loop-thread stack — worth reading before it recurs.
4. **`brain_hook` absorb concurrency cap is being hit** in production
   (`brain_hook(verified_intel): N errors — absorb: concurrency cap (>Ns wait)`).
   This is the `_over_cap` branch audited above; it is correct code, but the
   backpressure is real and facts are being deferred to the WAL under load.
5. **Hallucination guard is flagging and shipping anyway:** `hallucination guard
   FLAGGED response — N medium-severity red flags. Response shipped with warning`,
   the flags being *"Numerical claim without source citation"* and *"CONFIRMED
   claim without inline citation"*. Under the design doctrine "evidence owns
   truth", shipping a CONFIRMED-labelled claim with no citation is the exact
   failure the contract exists to prevent.
6. **`POST /api/aria/report` → 400 Bad Request ×8** from a public address, and
   **`/api/brain/brief` 404s on the brain** (probed directly) — it is an aria-web
   route being called on aria-intel from `telegramCommands.mjs`, so it has never
   returned data. Left in place with a note; re-hosting it needs its own check.
7. **`memory_leak_detector` RSS exceeded its 6144MB threshold** and triggered GC.

### 4. Suite/environment notes

- `aria_service/tests/test_rf2151_rag_retry_cooldown.py` fails to **collect** on
  this box (`chromadb` has no win-arm64 wheel — see §16).
- 15 `inspect.getsource`-based tests (`rf636`, `rf615`, `rf1561`, `rf1845`,
  `rf940`) fail at **pristine HEAD** in a clean worktree — pre-existing, not
  caused by this work. Verified by running them in a detached worktree at HEAD.
- 5 Node tests fail, all outside this diff. `twofactor-otplib-v13-rf3086` expects
  3 `await verifyTotpCode(` call sites and finds 4 — a fourth route was added
  without updating the test.
