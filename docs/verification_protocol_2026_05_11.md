# ARIA Verification Protocol
**Canonical · 2026-05-11 · Binding for all behavior-changing commits**

The map → fix → verify → patch → verify loop. Born from the 2026-05-11
sweep that found a 29% defect rate (16 hidden bugs in 56 same-day fixes).
Every commit that ships behavior changes runs this protocol BEFORE the
operator-facing "shipped" claim. No exceptions; no overrides without
explicit operator authorisation citing this doc.

---

## The Loop

```
┌─────────────────────────────────────────────────────────────┐
│  1. MAP        — area mapped, R-number assigned             │
│  2. FIX        — code change committed                       │
│  3. VERIFY     — agent reads actual call sites + signatures │
│  4. PATCH      — every bug the agent finds → follow-up R-F  │
│  5. VERIFY     — agent re-checks the patch + originals      │
│  → repeat 4-5 until the agent reports clean                 │
└─────────────────────────────────────────────────────────────┘
```

Steps 1-2 are covered by `feedback_map_then_change`. This protocol
defines steps 3-5.

---

## When to run

| Trigger | Verification required |
|---|---|
| Single-file Python change | YES — 1 agent on that file |
| Multi-file commit (≥2 files) | YES — split into parallel agents by file slice |
| Multi-fix commit (≥3 R-numbers) | YES — at least 1 agent per ~5 fixes |
| HTML/CSS change | YES — verify selector / data-attribute / handler match |
| Comment-only change | NO |
| Test-only change | NO — but tests themselves must verify the fix they cover |
| Doc-only change | NO |

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

## How to invoke

Default invocation, after a commit:

```
Agent(
  description="Verify <commit short message>",
  subagent_type="general-purpose",
  prompt="<paste the agent-prompt template below>"
)
```

For multi-file commits, split into parallel agents:

```
Agent(slice 1: files A, B, C)  ┐
Agent(slice 2: files D, E, F)  ├─ in a single message
Agent(slice 3: files G, H)     ┘
```

### Agent prompt template

```
CRITICAL verification task. I shipped commit <hash> with the following
fixes: <R-numbers>. Read the actual code at these files and verify
each fix achieves what its claim says. Be brutal. No assumptions.

Working dir: C:\code\crucix
Files to audit:
  1. <path>
  2. <path>
  ...

For each fix, walk through the checklist in
docs/verification_protocol_2026_05_11.md sections A-H. Report:
  - PASS / FAIL / WARN per fix
  - Exact file:line evidence
  - If FAIL: what's broken and how to fix
  - If WARN: what's risky but might still work

Total report under 800 words. Brutal honesty. The operator confirmed
2026-05-11 that there is no room for mistakes here.
```

---

## What to do with findings

| Finding severity | Action |
|---|---|
| FAIL — function not callable, dead code, type mismatch | Patch in follow-up commit (`R-F<N+1>`); re-verify |
| WARN — edge case might fire | Add guard if low-cost; document if costly |
| PASS | Move on |

Never amend the original commit. The audit trail must survive — future
sessions need to see which commits caused which bugs to refine the
process further.

---

## Commit message convention

Every commit that ships behavior changes carries one of:

- `Verified-by: parallel-agents` (most common — agents ran after commit)
- `Verified-by: manual-read` (small change, manual checklist sufficed)
- `Verified-by: none — comment/doc only` (trivial change)

The trailer appears at the bottom of the commit message, above the
Co-Authored-By line. Future audits grep for this trailer to validate
the protocol was followed.

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

There are none. If the operator wants to skip verification on a specific
commit, they must say so explicitly AND cite a reason that this doc
rejects (e.g. "trivial typo fix"). The default is to verify.

If a verification agent finds zero bugs across a string of commits,
that's a data point — the codebase or the discipline has matured. It
is NOT a reason to skip verification.

---

## History

- 2026-05-11 — protocol born after 56-fix sweep found 16 bugs (29% rate)
- 2026-05-11 — operator authorisation: "we must have 100% verified proof"
- Future entries appended here as the discipline produces results
