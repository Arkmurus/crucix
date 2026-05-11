# ARIA Verification Protocol
**Canonical · 2026-05-11 · Binding for ALL commits — TWO-PASS, NO EXCEPTIONS**

The map → fix → VERIFY → patch → RE-VERIFY → commit loop. Locked in
2026-05-11 PM after the operator caught a real production regex bug
on the SECOND verification pass — pass-1 had approved it. Two passes
is the floor; more if either pass finds issues.

Operator directives that made this binding:
- "we cannot carry on making mistakes"
- "nothing gets signed off before tested and re-tested only then gets
  a green light"
- "the whole chain needs to be test to ensure it produces what is asked"
- "follow the rules and principals set on this project"

NO EXCEPTIONS. NO OVERRIDES. Applies to:
- Code changes (Python, Node, HTML/JS, shell)
- Doc changes (URLs, command examples, paths)
- Config changes (env vars, requirements, settings.json)
- Test files (verify the test would catch the bug it claims to catch)
- Memory/rule changes (verify the rule text matches the directive verbatim)

---

## The Loop (LOCKED 2026-05-11 PM)

```
┌─────────────────────────────────────────────────────────────┐
│  1. MAP            — area mapped, R-number assigned         │
│  2. FIX            — code change lands in WORKING TREE       │
│                      (NOT yet `git add`-ed)                 │
│  3. VERIFY PASS 1  — agent reads actual call sites + sigs   │
│  4. PATCH          — every bug pass-1 finds → fix in tree   │
│  5. RE-VERIFY      — FRESH agent (no pass-1 context)        │
│                      re-tests the whole file set            │
│  6. (if pass-2 finds anything → patch → pass-3 → ...)       │
│  7. COMMIT         — only after the latest pass is GREEN    │
│  8. PUSH           — auto-deploy to fly.io + seenode        │
│  9. SMOKE          — curl probes against the live deploy    │
└─────────────────────────────────────────────────────────────┘
```

**CRITICAL: Steps 3 + 5 are BOTH mandatory.** Pass-1 catches the
obvious; pass-2 catches what pass-1's patch introduced AND what
pass-1's fixation missed. The 2026-05-11 PM session caught a real
production regex bug on pass-2 that pass-1 had approved.

Steps 1-2 are covered by `feedback_map_then_change`. This protocol
defines steps 3-9.

---

## When to run

**TL;DR — ALWAYS run both passes. The only commits exempt from this
protocol are commits to `MEMORY.md` and `*.pyc` files (auto-generated).**

| Trigger | Pass 1 required | Pass 2 (RE-TEST) required |
|---|---|---|
| Single-file Python change | YES | YES |
| Multi-file commit (≥2 files) | YES — split parallel | YES — split parallel |
| Multi-fix commit (≥3 R-numbers) | YES — ≥1 agent per 5 fixes | YES |
| HTML/CSS change | YES — selectors / handlers | YES |
| Comment-only change | YES (verify comment matches code) | YES |
| Test-only change | YES (tests must catch what they claim) | YES |
| Doc change | YES (URLs / commands / paths must work) | YES |
| Config change (requirements.txt etc.) | YES | YES |
| Memory rule change | YES (rule text matches directive verbatim) | YES |
| Hot-fix to production | YES — TIME IS NOT AN EXCUSE | YES |

The 2026-05-11 doc-fix that caused R-F253 (operator hit a 404 because
the triage doc said `run-weekly` but the route was `run_weekly`) was
a DOC-ONLY commit that this protocol would have caught.

---

## What the verification agent must check

For every code change, the agent reads the actual modified code AND the
modules it interacts with. The checklist below is non-negotiable.

### A. Function call sites

For every function the change CALLS:
1. Grep for the function definition. Confirm it exists in the imported
   module (not a different module with the same name).
2. Read the signature. Confirm every kwarg passed at the call site is
   actually accepted (positional vs kw-only, type, optional vs required).
3. Read the return shape. Confirm the caller uses the return value
   correctly (e.g., `result.get("foo")` only works if the function
   returns a dict that has `"foo"`).

### B. New function definitions

For every function the change DEFINES:
1. Grep for callers across the entire codebase (not just the file).
2. If no caller exists, the function is dead code OR the wiring is
   incomplete. Flag as FAIL.
3. If the function is intentionally exported for future use, that must
   be stated explicitly in the docstring + commit message.

### C. Field access

For every `.get("foo")`, `obj.foo`, `dict["foo"]`:
1. Confirm something writes that field somewhere in the codebase.
2. For Redis-stored dicts, confirm the writer schema matches the reader
   schema (recent bug: chat_audit_log entries have `user_message`,
   not `tags`).
3. For dataclass / pydantic models, confirm the field is declared.

### D. Conditions and edge cases

For every condition on a number, list, or string:
1. NaN: does `nan == 0` matter? Does `nan > 0`? (R-F206 root cause)
2. 0.0 vs falsy: does `if x else None` collapse legitimate zero?
   Use `if x is not None` everywhere unless you specifically want
   falsy-zero behavior. (R-F208 root cause)
3. Empty list / dict / string: does the branch handle len==0?
4. None: does the branch handle missing-data case?
5. Negative numbers: when does `bump < 0` matter vs `bump <= 0`?

### E. Regex

For every regex:
1. Test mentally against an empty string.
2. Test against a typical case.
3. Test against an edge case (end-of-string vs end-of-line; case
   sensitivity; multi-line flag).
4. For substitution regexes, ensure the captured groups round-trip
   correctly.

### F. Concurrency

For every async function that reads-then-writes shared state:
1. Could two concurrent callers both observe the same condition?
2. Is there a lock / SETNX / single-flight gate?
3. What's the worst case if the race fires? (R-F210 root cause was
   multi-penalty mastery write.)

### G. Env flags

For every new env-gated behavior:
1. Confirm the flag has a DIFFERENT effect from any other flag.
2. Confirm the default-off behavior is the previous behavior (no
   silent breakage when flag is unset).
3. Confirm the flag name is documented somewhere the operator can
   find it.

### H. Imports

For every new `from X import Y`:
1. Confirm `X.Y` exists at the path.
2. For relative imports inside functions (the common pattern in this
   codebase), confirm the relative depth is correct.
3. For scope-local imports, confirm the import isn't already at module
   level (avoid shadowing).

---

## How to invoke (LOCKED 2026-05-11 PM — TWO PASSES, BEFORE COMMIT)

**Pass 1** — runs AFTER the fix lands in the working tree but BEFORE
`git add`. Single agent for single-file changes; parallel agents
split by file slice for ≥4-file commits.

```
Agent(
  description="Pass 1: verify <change short summary>",
  subagent_type="general-purpose",
  prompt="<paste Pass 1 template below>"
)
```

**Pass 2 (RE-TEST)** — runs AFTER pass-1 patches land in the working
tree, BEFORE `git add`. Fresh agent (no pass-1 context). Brief
explicitly states "first pass already ran and caught X — now re-test
the whole chain".

```
Agent(
  description="Pass 2 RE-TEST: <change short summary>",
  subagent_type="general-purpose",
  prompt="<paste Pass 2 template below — emphasise whole-chain regress>"
)
```

If pass-2 finds anything → patch → pass-3 → loop until GREEN.

### Pass 1 prompt template

```
PASS 1 verification per docs/verification_protocol_2026_05_11.md.
Working tree changes NOT YET committed. Read the actual code at
these files and verify each change achieves what its claim says.

Working dir: C:\code\crucix
Files to audit (working tree, not committed):
  1. <path>
  2. <path>
  ...

Walk the 8-section checklist (A-H) per the protocol doc. Report:
  - PASS / FAIL / WARN per check
  - Exact file:line evidence
  - If FAIL: what's broken and how to fix

Under 600 words. The operator wants every issue caught BEFORE the
commit lands. Be brutal — there's no follow-up commit to clean up
after; everything ships together or not at all.
```

### Pass 2 (RE-TEST) prompt template

```
PASS 2 RE-VERIFICATION per docs/verification_protocol_2026_05_11.md.
Pass 1 already ran and caught: <summary of what pass 1 found>.
The working tree now contains the patches. Re-test the WHOLE chain:
the original change + the pass-1 patches together.

Working dir: C:\code\crucix
Files to re-audit:
  1. <path>
  2. <path>
  ...

Specifically check:
1. The pass-1 patches actually do what they claim
2. The patches didn't introduce NEW issues
3. The wider chain hasn't regressed (look at modules that import the
   changed code)
4. Any edge cases pass-1 fixated past

Under 500 words. GREEN ONLY — operator does not sign off on WARN.
If you find anything, name it specifically.
```

---

## What to do with findings

| Finding severity | Action (UNDER THE NEW TWO-PASS RULE) |
|---|---|
| FAIL — broken code | Patch in the WORKING TREE (NOT a follow-up commit); re-verify (next pass) |
| WARN — edge case might fire | Either fix in working tree + re-verify, OR explicitly document with operator acknowledgement before commit |
| PASS on both passes | Commit + push + smoke |

**The single-commit pattern (replaces follow-up-commit pattern):**
Pre-2026-05-11-PM convention was to commit the original, run
verification, then commit `R-F<N+1>` as a follow-up. That's OBSOLETE.
Now the patches travel WITH the original change in ONE commit. The
commit message documents BOTH verification passes and what each
caught + confirmed.

If pass-2 (or pass-3, pass-4) finds something, patch in the working
tree and re-verify. The git index doesn't see the commit until both
passes are GREEN.

---

## Commit message convention

Every commit that ships behavior changes carries:

- `Verified-by: parallel-agents (2 passes)` — when both pre-commit
  passes ran clean OR the patches landed in the working tree before
  commit
- `Verified-by: manual-read (2 passes)` — when the change is small
  enough for two manual readings (rare; usually agent-assisted)

The "1 pass" variant is GONE. Every commit needs at least two passes.

Optional: include a brief `Verification trail:` block in the commit
body documenting what pass-1 caught + what pass-2 confirmed.

Trailer appears above the Co-Authored-By line. Future audits grep
for `Verified-by: ` to confirm two-pass discipline was followed.

---

## Cost / time budget

Per the 2026-05-11 measurement:
- One verification agent on a multi-file commit: ~30k-60k tokens, ~2-3
  minutes wall clock
- Two parallel agents on a 14-file commit: ~120k tokens combined, ~5
  minutes wall clock
- Manual production debug of one missed bug: ~1-2 hours operator time
- Production-visible incorrect-behavior incident: priceless reputational
  cost in defence-DD

The math is clear: ALWAYS verify.

---

## Exceptions

**There are none.** Both passes are mandatory. Operator-side override
is unavailable; if a future operator says "skip the protocol for this
quick fix", the correct response is: "the rule is no exceptions —
let me run the two passes, which takes ~3 minutes, before pushing".

If a verification agent finds zero bugs across a string of commits,
that's a data point — the codebase or the discipline has matured. It
is NOT a reason to skip verification.

Hot-fix to production is NOT an exception. Time is not an excuse.
The full two-pass round takes ~3-5 minutes total; any incident that
gives the operator <5 minutes to ship is an architectural problem
that needs solving separately, not a verification-skip licence.

---

## History

- 2026-05-11 AM — protocol born after 56-fix sweep found 16 bugs
  (29% defect rate)
- 2026-05-11 (mid) — operator authorisation: "we must have 100% verified
  proof"
- 2026-05-11 PM — LOCKED with two-pass requirement after pass-2 caught
  a real production regex bug (R-F246's regex) that pass-1 had approved.
  Operator directive: "nothing gets signed off before tested and
  re-tested only then gets a green light...the whole chain needs to
  be test to ensure it produces what is asked".
- 2026-05-11 PM — "test and re-test is a new rule moving forward and
  must be done no exception" — operator made the rule permanent.
- Future entries appended here as the discipline produces results.
