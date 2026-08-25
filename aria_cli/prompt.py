"""R-F988 — system prompt for the ARIA Coder CLI.

ARIA's identity (Rule Zero — a team member, not a passive tool) plus a
Claude-Code-style operating contract: work autonomously through the tools,
match the project's conventions, verify before claiming done, never truncate
files. In self-mode the prompt also names the crucix guardrails so ARIA edits
her own ecosystem the same disciplined way the autonomous coder does.

R-F2145/R-F2147: the CLI coder now queries the live coding RAG (constitutional
rules, codebase structure, past fixes) at prompt-build time, so every session
starts grounded in accumulated coding knowledge — same as the autonomous coder.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path

# Per-file cap on injected guidance. R-F2160: the default now fits CLAUDE.md and
# AGENTS.md WHOLE (~38KB each today). The prior 16000 cap silently dropped ~58%
# of each file — and the dropped half is exactly where the load-bearing coding
# rules live (CLAUDE.md §20-§25 incl. §21e which mandates this very injection;
# AGENTS.md laws 11-20 incl. law 19 "PowerShell is not bash" and the shipping
# sequence). When a file genuinely exceeds the cap we keep the HEAD and the TAIL
# (so the top floor AND the bottom operational rules both survive, eliding only
# the middle) and mark the elision — never a silent head-only truncation.
# R-F4080 (C-129) — 40000 ROTTED, exactly as 16000 did before it.
#
# Measured 2026-08-16: CLAUDE.md is 120,871 chars against a 40,000 cap, so 67%
# was elided — MORE than the ~58% loss R-F2160 raised the cap to fix. The
# elided middle is the operating core (§22 verification discipline, §23
# cross-check, §25 proprioception, §21e which mandates this injection), and
# probing showed even §26 CURE MODE — the rules governing what may be changed
# at all — never reached the agent.
#
# A fixed cap against a monotonically growing file rots BY CONSTRUCTION, and §7
# forbids eviction so this file only accretes. The number alone cannot be the
# fix: `test_rf4080_cli_guidance_not_elided` now FAILS the moment a guidance
# file outgrows the cap, so the next overflow is a decision someone makes rather
# than a silent two-thirds loss.
#
# Affordable because `load_repo_guidance` is called from `build_system_prompt` —
# ONCE per session, not per turn.
_GUIDANCE_MAX_CHARS = int(os.getenv("ARIA_CODER_GUIDANCE_MAX_CHARS", "200000"))

# R-F4319 (C-267) — 200,000 chars ROTTED THE OTHER WAY, and it took a third
# reading of the same shape to see it.
#
# R-F2160 raised the cap from 16,000 and R-F4080 from 40,000, each because a
# growing CLAUDE.md was being silently elided. Both were right. But a cap only
# ever asked "does the FILE fit under it?" and never "does the PROMPT fit the
# MODEL?" — and the moment the CLI was pointed at the 16,384-token sovereign the
# answer was no, by a factor of three:
#
#     system prompt   188,814 chars  ~47,203 tokens
#     tool schemas     15,486 chars  ~ 3,871 tokens
#     window                           16,384 tokens
#
# vLLM answered HTTP 400 on `status` — the first command, no history — so it was
# never accumulated conversation, and compaction could never have helped: it
# stubs old TOOL OUTPUT, and the hog is the prompt itself.
#
# This is the same defect as COMPACT_CHAR_BUDGET=180000 and max_tokens=8192
# (R-F4318): a constant that silently encodes one vendor's capacity. The window
# is a property of the MODEL, so the budget is derived from the model.
#
# The budget below is the TOTAL for all guidance, distributed proportionally in
# `load_repo_guidance` rather than applied as a flat per-file cap. A flat cap is
# what forced the choice between clipping CLAUDE.md (132,540 chars) and letting
# AGENTS.md (37,308) sit far under its share; proportional distribution spends
# whatever room exists where the text actually is.

#: FALLBACK only — the real figure is MEASURED by `_tool_schema_tokens()` below.
#:
#: R-F4321, second pass. The first version of this hardcoded 3,000 from a
#: measurement of `TOOL_SCHEMAS` (14 tools, 7,147 chars, 1,936 tok). That is
#: NOT what goes on the wire. `agent.py:255` sends
#: `_dedup_tool_schemas(TOOL_SCHEMAS + CODER_TOOL_SCHEMAS)` — 31 tools, 15,486
#: chars, **4,230 tokens** measured against the served tokenizer. The reserve
#: was short by ~1,230 on every call.
#:
#: The test written to pin it measured the same wrong set, so it went green
#: while the budget was under — a guard aimed at the wrong object certifies
#: nothing, which is the exact shape this whole line of work keeps finding.
#: Caught by a peer review, not by me.
#:
#: So the number is no longer hand-maintained: adding a tool now moves the
#: budget by itself. A constant here would rot the moment someone adds tool 32,
#: exactly as _GUIDANCE_MAX_CHARS rotted three times.
_TOOL_SCHEMA_RESERVE_FALLBACK = 5000


def _tool_schema_tokens() -> int:
    """Tokens the tool schemas occupy on the wire, measured from the real set.

    Lazily imported: `agent` imports this module, so a top-level import would
    cycle. Any failure falls back to a figure ABOVE the measured 4,230 — a
    reserve that guesses low re-creates the overflow, so the safe direction is
    to over-reserve and lose a little guidance.
    """
    global _TOOL_SCHEMA_TOKENS_CACHE
    if _TOOL_SCHEMA_TOKENS_CACHE is not None:
        return _TOOL_SCHEMA_TOKENS_CACHE
    tokens = _TOOL_SCHEMA_RESERVE_FALLBACK
    try:
        import json

        from aria_cli.agent import TOOL_SCHEMAS, _dedup_tool_schemas
        from aria_cli.coder_tools import CODER_TOOL_SCHEMAS

        wire = _dedup_tool_schemas(list(TOOL_SCHEMAS) + list(CODER_TOOL_SCHEMAS))
        # No extra safety multiplier: _CHARS_PER_TOKEN is 3 and schemas actually
        # tokenize at 3.69 chars/token, so this already over-reserves by ~22%
        # (15,486/3 = 5,162 against a measured 4,230). Stacking another margin
        # on top would spend a fifth of a 16k window on nothing, and an
        # over-tight budget silently costs guidance the model had room for.
        tokens = max(int(len(json.dumps(wire)) / _CHARS_PER_TOKEN), 1000)
    except Exception:  # noqa: BLE001
        pass
    _TOOL_SCHEMA_TOKENS_CACHE = tokens
    return tokens


_TOOL_SCHEMA_TOKENS_CACHE: int | None = None
#: Room for the conversation itself — a prompt that fits with nothing left to
#: say has not fitted.
_CONVERSATION_RESERVE_TOKENS = 2000
#: Everything in the system prompt that is NOT guidance: identity, operating
#: contract, engineering rules, the PowerShell rule, self-mode, _ESSENTIALS, the
#: RAG block, the persona catalog and the environment footer.
#:
#: MEASURED 2026-08-25 at a 32,768 window: total prompt 16,369 tok, of which
#: 9,945 was guidance -> 6,424 of overhead. The previous 5,000 was already low
#: and went further out of date the moment _ESSENTIALS was added, which is the
#: hazard with any hand-maintained constant in this file. 7,000 covers the
#: measurement with margin; if the fixed blocks grow again, the R-F4321 test
#: `test_the_prompt_still_fits_after_the_floor_was_added` fails rather than the
#: model 400ing in production.
_PROMPT_OVERHEAD_TOKENS = 7000
#: Never ship an EMPTY constitution: elided rules beat no rules.
_GUIDANCE_FLOOR_CHARS = 2000
#: MEASURED against the served tokenizer 2026-08-25 (vLLM /tokenize), not
#: assumed. The usual "4 chars per token" rule of thumb is ~25% optimistic for
#: markdown and code, and that gap IS the overflow:
#:
#:     CLAUDE.md   132,540 chars -> 42,778 tok  = 3.10 chars/tok
#:     AGENTS.md    37,308 chars -> 10,964 tok  = 3.40
#:     system prompt 71,363 chars -> 22,241 tok = 3.21
#:     tool schemas   7,147 chars ->  1,936 tok = 3.69
#:
#: A budget computed at 4 produced a prompt of 28,264 tokens against a 32,768
#: window and 400'd live. 3 sits below the lowest measured ratio, so the budget
#: errs toward fitting - the direction that fails safe.
_CHARS_PER_TOKEN = 3


def model_window_tokens() -> tuple[int, int]:
    """(context window, completion reserve) for the model actually in use.

    ARIA_LLM_MAX_MODEL_LEN is consulted FIRST, and that ordering is load-bearing
    rather than a convenience. It is the authoritative statement of the served
    window — it must match the vLLM `--max-model-len` — and reading it needs no
    config object, so it still answers when the config cannot be built.

    That case is real and this fix originally got it wrong. `LLMConfig.from_env`
    RAISES for provider 'aria-llm' with no ARIA_LLM_URL ("Refusing to fall back
    to another model"), which is correct of it. But catching that and falling
    back to a large window handed a MISCONFIGURED sovereign the biggest budget
    of all — 169,894 chars of guidance into a 16,384-token window, i.e. exactly
    the overflow this function exists to prevent, reached through its own error
    path. A fallback must not be more permissive than the thing it stands in for.

    Otherwise resolved through `LLMConfig.from_env` so there is ONE answer to
    "how big is the window"; a second copy would drift from the client that has
    to live with it. Imported lazily — `agent` imports both modules and a
    top-level import would make the cycle. Only when NOTHING states a window do
    we assume a large one, because a budget that collapses on an unreadable
    config would strip the constitution for no reason.
    """
    def _completion_for(win: int) -> int:
        # Mirrors llm.py's clamp: at most a quarter of the window for the answer.
        try:
            want = int((os.getenv("ARIA_CODER_LLM_MAX_TOKENS") or "8192").strip())
        except (TypeError, ValueError):
            want = 8192
        return max(256, min(want, win // 4))

    try:
        explicit = int((os.getenv("ARIA_LLM_MAX_MODEL_LEN") or "").strip())
    except (TypeError, ValueError):
        explicit = 0
    if explicit >= 512:
        return explicit, _completion_for(explicit)

    try:
        from aria_cli.llm import LLMConfig  # local: avoids an import cycle

        cfg = LLMConfig.from_env()
        return int(cfg.max_model_len), int(cfg.max_tokens)
    except Exception:  # noqa: BLE001
        return 65536, 8192


#: Share of the leftover window given to repo guidance; the rest is conversation
#: history. Guidance is the larger share because it is the standing instruction
#: set, but it must NOT be greedy: taking all the slack leaves an agent that has
#: read the constitution and cannot hold a conversation about it.
_GUIDANCE_SLACK_SHARE = 0.6


def context_budget(*, window_tokens: int | None = None,
                   completion_tokens: int | None = None) -> dict:
    """THE budget. Every consumer derives from this and none does its own sums.

    R-F4321 (C-269) — there were TWO budgets and they disagreed, which is the
    §1/R-F2639 "one measure, do not fork it" shape inside a single feature.
    `guidance_budget_chars` reserved 4,000 tokens for tool schemas;
    `compact_budget_chars` reserved NOTHING and used a hardcoded 4 chars/token
    while this module had already been recalibrated to 3. Measured on the tree
    that shipped, sovereign at 16,384:

        history budget   14,745 tok
        completion        4,096
        tool schemas      2,382   (sent on EVERY call, reserved by neither)
        --------------------------
        total            21,223   vs a 16,384 window -> overflow 4,839

    So the headline symptom this whole line of work exists to stop could still
    recur through the other budget. Worse, R-F4318's guard encoded the same
    omission - `budget <= (max_model_len - max_tokens) * 4` - so it could never
    have caught it. A guard that shares the defect's assumption is not a guard.

    The window is now allocated ONCE and exhaustively:

        window = completion + tool schemas + prompt overhead
                 + guidance + history

    Everything after the fixed costs is `slack`, split between guidance and
    history so BOTH grow with the model rather than guidance taking it all.
    """
    if window_tokens is None or completion_tokens is None:
        w, c = model_window_tokens()
        window_tokens = window_tokens if window_tokens is not None else w
        completion_tokens = completion_tokens if completion_tokens is not None else c

    window = int(window_tokens)
    completion = int(completion_tokens)
    tools = _tool_schema_tokens()
    slack = max(0, window - completion - tools - _PROMPT_OVERHEAD_TOKENS)
    guidance_tok = int(slack * _GUIDANCE_SLACK_SHARE)
    history_tok = slack - guidance_tok
    # `fits` is reported rather than silently absorbed. The FIXED costs — the
    # completion reserve, the tool schemas, and the non-guidance system prompt —
    # can exceed a small window on their own (an 8,192-token model: 2,048 +
    # 3,000 + 5,000 = 10,048). No budget split repairs that; the model is too
    # small to host this CLI, and saying so is more useful than shipping a
    # zero-guidance prompt that 400s anyway. Tri-state discipline (§1): a caller
    # can act on "cannot fit" but not on a silently truncated number.
    fixed = completion + tools + _PROMPT_OVERHEAD_TOKENS
    return {
        "window": window,
        "completion": completion,
        "tools": tools,
        "overhead": _PROMPT_OVERHEAD_TOKENS,
        "guidance_tokens": guidance_tok,
        "history_tokens": history_tok,
        "guidance_chars": max(_GUIDANCE_FLOOR_CHARS, guidance_tok * _CHARS_PER_TOKEN),
        "history_chars": history_tok * _CHARS_PER_TOKEN,
        "fixed_tokens": fixed,
        "fits": fixed < window,
    }


def guidance_budget_chars(*, window_tokens: int | None = None,
                          completion_tokens: int | None = None) -> int:
    """TOTAL chars of repo guidance the model can afford, derived from its window.

    An explicit ARIA_CODER_GUIDANCE_MAX_CHARS still wins — deriving a default is
    not the same as removing the operator's lever.
    """
    override = (os.getenv("ARIA_CODER_GUIDANCE_MAX_CHARS") or "").strip()
    if override:
        try:
            return max(_GUIDANCE_FLOOR_CHARS, int(override))
        except (TypeError, ValueError):
            pass
    return context_budget(window_tokens=window_tokens,
                          completion_tokens=completion_tokens)["guidance_chars"]


def _guidance_toc(text: str) -> list[str]:
    """Markdown section headings, in order. Cheap: ~40 lines for CLAUDE.md."""
    return [ln.strip() for ln in text.splitlines()
            if ln.startswith(("# ", "## ", "### "))]


def _clip_guidance(text: str, cap: int) -> str:
    """Bound a guidance file to ``cap`` chars. Under cap → unchanged. Over cap →
    head (60%) + a marker carrying the FULL TABLE OF CONTENTS + tail (40%).

    R-F4319 (C-267) — the table of contents is the part that matters now, and it
    exists because the arithmetic changed under R-F4080's feet.

    R-F4080 required the constitution to arrive WHOLE, and was right to: the
    elided middle is the operating core (§22 verification discipline, §23
    cross-check-before-claiming-fixed, §25 proprioception, §21e). But measured
    against the real tokenizer, CLAUDE.md + AGENTS.md are ~53,700 tokens. With
    tool schemas and a completion reserve that does not fit ANY window we serve
    — not the sovereign's 32,768, and not DeepSeek's 65,536. "Inject it whole"
    stopped being an option that exists, rather than one we chose against.

    So the guarantee is weakened deliberately and in one specific way: every
    section is at least NAMED, even when its body is elided. An agent that can
    see "§23 Cross-check + FULL-test before any 'fixed' claim" in the contents
    knows the rule exists and can `read_file` it. An agent shown nothing
    concludes there is no such rule — which is the absence-reads-as-health shape
    this repo keeps paying for, aimed at the constitution itself.

    The real fix is to stop injecting the whole document and retrieve sections
    on demand; this makes the interim state honest rather than silent.
    """
    if len(text) <= cap:
        return text

    toc = _guidance_toc(text)
    toc_block = ""
    if toc:
        toc_block = ("\nEVERY SECTION IN THIS FILE (bodies elided here — use "
                     "read_file to pull any of them in full):\n"
                     + "\n".join("  " + h for h in toc) + "\n")
    marker = ("\n\n…(MIDDLE ELIDED to fit the model's context window)…\n"
              + toc_block + "…\n\n")

    room = cap - len(marker)
    if room < 500:
        # Not even room for the contents: fall back to the bare marker so the
        # elision is still announced rather than silent.
        marker = "\n\n…(MIDDLE ELIDED to fit — read the full file with read_file)…\n\n"
        room = max(0, cap - len(marker))

    head = int(room * 0.6)
    tail = room - head
    return text[:head] + marker + (text[-tail:] if tail > 0 else "")

_IDENTITY = """You are ARIA — the Arkmurus Research Intelligence Agent — operating as an \
autonomous coding agent on the operator's machine, alongside Claude Code. You have \
the same class of abilities as Claude Code, and you operate with the same autonomy: \
you read and edit files, run any shell/cmd command, run tests, use git, deploy, and \
build whatever the task requires — without asking permission for each step. You are \
a team member (Rule Zero), not a passive tool. You take initiative, drive the task \
to a fully working and verified result, and you always find a path."""

_OPERATING_CONTRACT = """
OPERATING CONTRACT — you are an exceptional, autonomous software engineer; hold that \
bar with no exceptions.
- FULL AUTONOMY. You have free rein to act. Do NOT ask the operator yes/no \
permission to read, write, edit, run shell/cmd commands, install packages, run \
tests, commit, or deploy — just do the work. Keep going until the task is fully \
resolved; don't hand back a half-done task or ask "should I continue?". The only \
time to stop and ask is when the REQUIREMENTS are genuinely ambiguous, or a choice \
is truly the operator's to make (not a routine engineering decision you can make \
yourself). When you must ask, do the safe parts first and batch your questions.
- Investigate before you edit. Read the relevant files, grep for call sites, and \
understand the conventions. Then write code that reads like the code around it — \
same naming, structure, error handling, and async style.
- Fix the root cause, not the symptom. Make the smallest focused change that fully \
solves the task; don't add abstractions or drive-by rewrites the task doesn't need.
- Prefer edit_file for targeted changes; use write_file only for new files or a \
genuine full rewrite. NEVER emit a truncated or stubbed file — a guard blocks \
writes that collapse a file's size, and that block means your content was \
incomplete, not that the guard is wrong. Type hints + docstrings on public callables.
- Verify everything yourself. Use run for tests, git, builds, and package managers. \
After a change, actually run the tests or build and READ the output before claiming \
success. A change you haven't verified is not done. Where it fits, add both a unit \
test (the function's contract) and a capability test (the user-visible behaviour).
- If a tool errors, read it and recover — fix the path, the match string, or the \
command. Don't repeat the same failing call; try a different approach.
- Self-reason and optimise the task, like a Claude Code session. For anything \
non-trivial: think briefly about the approach and trade-offs, pick the most \
efficient path, and call update_plan to lay out the steps and keep it current \
(exactly one step in_progress). Re-plan when you learn something new. Use fetch_url \
to read docs/APIs/raw files; use ask_claude for guidance (north star, a design call, \
a hard bug, a review) and check_claude to read replies.
- SHOW YOUR WORK. Before each batch of tool calls, write one short line saying what \
you're about to do and why — visible reasoning, so the operator always sees you are \
thinking and what you're doing. Keep it tight (a sentence, not paragraphs); let the \
tools do the work, but never go silent for long stretches.
- Drive the task yourself and don't stop early. If a step fails, diagnose and try \
another way; if you stall, re-plan and keep going. Self-review before declaring \
done. When done, summarise what changed and exactly how you verified it. Report \
honestly: if tests fail or you skipped a step, say so. Never claim a fabricated \
success.
- Use judgement on irreversible actions. You may run cmd commands, commit, push, and \
deploy freely as part of the task — but think before something hard to undo \
(force-push, mass/recursive delete, dropping data, production deploy of an unverified \
change): verify first, and prefer the reversible path. The truncation guard still protects the codebase from accidental destructive full-file replacements \
work with it, never around it.
- STAY ON THE OPERATOR'S TASK — this is the #1 rule. Work ONLY on what the \
operator asked. Do NOT drift into unrelated work: platform gaps, the punch-list, \
session rituals, draining queues/bridges, or anything else you happen to notice — \
unless the operator explicitly tells you to. A note or reminder from another channel \
(a Claude bridge message, a queued item) is REFERENCE, not a new task: read it, but \
keep doing what the operator asked. If you spot something else worth doing, FINISH \
the current task first, then mention it in your summary — do not go do it.
- DON'T STOP MID-TASK. A turn ends only when the operator's task is genuinely \
COMPLETE and verified. Never end a message by merely DESCRIBING what you will do next \
("Next I'll…", "Let me now…") and then stopping — if there's a next step, take it in \
the same turn with a tool call. Producing a tool-call-free message is your signal that \
the work is finished; only do it when it actually is.
- After finishing a task, signal readiness for the next instruction. Say "Done — what's next?" or similar. Do NOT go silent — the operator should never have to wonder whether you've finished or stalled. Do not invent extra work beyond what was asked.
"""

_ENGINEERING = """
ENGINEERING STANDARD (state-of-the-art — hold it on every change)
- Architecture: separation of concerns, small focused modules/functions, clear
  interfaces, dependency injection over globals, single source of truth. Don't add
  abstraction the task doesn't need; don't duplicate logic — reuse what exists.
- Reliability by construction: every I/O call gets a timeout; retry transient
  failures with bounded exponential backoff; use circuit breakers for flaky
  externals; make operations idempotent; fire-and-forget side effects must NEVER
  block the main path; fail safe and degrade gracefully (never crash the loop).
- Correctness: handle errors explicitly (no bare excepts that hide bugs), validate
  inputs at boundaries, no race conditions on shared state (lock or make atomic),
  no resource leaks (close clients/files), preserve backward compatibility.
- Quality: type hints + docstrings on public callables; names that read like the
  surrounding code; comments explain WHY, not what; keep diffs small and reviewable.
- Testing: prove behaviour, not implementation — a unit test for the contract AND a
  capability test for the user-visible symptom; tests must be fast and deterministic
  (no live network/sleep races); run them and read the output before claiming done.
- Security: never log or commit secrets; least privilege; sanitise external input;
  no eval/exec of untrusted data.
- Delivery: small, verifiable increments; each lands working + tested before the
  next; commit messages explain intent.
"""

_POWERSHELL = """
SHELL DIALECT — YOU ARE ON WINDOWS POWERSHELL (run uses pwsh/powershell, never bash)
Your `run` tool executes every command through PowerShell on this machine. Emit
PowerShell, not bash — bash-isms are fed verbatim to PowerShell and fail or do the
wrong thing. Concrete rules (AGENTS.md anti-hallucination law 19):
- `&&` / `||` chaining is NOT reliable (Windows PowerShell 5.1 lacks it). Sequence
  with `;`, or check `$LASTEXITCODE` / `if (-not $?) { … }` between steps.
- `curl` is a PowerShell ALIAS for Invoke-WebRequest, not real curl — use
  `curl.exe` when you want curl, or `Invoke-RestMethod`.
- Redirection: `2>$null` not `2>/dev/null`; `$null` not `/dev/null`.
- File ops: `Remove-Item` (not rm -rf — though rm is aliased), `Get-Content`/`gc`
  (cat is aliased), `Get-ChildItem`/`ls`, `Select-String` (not grep — but your
  `grep` TOOL is better, use it). Prefer your read_file/grep/glob TOOLS over
  shelling out to Get-Content/Select-String.
- Env vars: `$env:NAME = 'x'` (not `export NAME=x`); read with `$env:NAME`.
- Avoid unescaped `()` inside double-quoted strings — PowerShell evaluates them;
  single-quote literal strings.
- Pipe `python`/`pytest`/`git` through directly; they work the same. Use
  `git --no-pager …` so paged output never blocks.
- Paths: forward OR backslashes both work in PowerShell; quote paths with spaces.
"""

#: R-F4321 (C-269) — the few rules that must reach the agent AT ANY WINDOW SIZE.
#:
#: These live in CLAUDE.md, and until now the CLI relied on the whole file being
#: injected. Once the guidance budget started tracking the model window that
#: stopped being true: `test_self_mode_prompt_covers_shipping_and_excellence`
#: went red because "git push origin main" appears EXACTLY ONCE in the repo — in
#: CLAUDE.md §11 — and landed in the elided middle. An agent that cannot see the
#: shipping sequence cannot ship, and would have invented one.
#:
#: This is not a new pattern: R-F2163 already pins the PowerShell rule here for
#: precisely this reason ("otherwise only reachable if the truncated
#: constitution happens to include it"). What changed is that clipping went from
#: an edge case to the normal case, so the same treatment is owed to the rest of
#: the operating core.
#:
#: Keep this SHORT. It is paid for on every call at every window size, and the
#: full text remains one `read_file` away — the injected table of contents names
#: every section.
_ESSENTIALS = """
NON-NEGOTIABLE OPERATING RULES (these are the floor; the full text is in
CLAUDE.md / AGENTS.md, which are summarised above with a table of contents —
use read_file to pull any section in full):

1. R-NUMBER DISCIPLINE. Every change gets an R-number, reserved BEFORE writing
   code: `python scripts/admin/reserve_r_number.py reserve "short title"`.
   Mark it shipped at push: `... ship R-F<n> <sha>`. Never claim a number by
   writing it in a comment.

2. ROOT CAUSE, NOT SYMPTOM. Never raise a timeout, add a retry, or extend a
   cooldown to make a failure go away. Find what is actually breaking and fix
   that. If you catch yourself bumping a number, stop and investigate.

3. VERIFY BEFORE YOU CLAIM. Never write "fixed / done / passing" without
   running the thing and reporting the real pass/fail count. Every fix needs a
   capability test that invokes the BROKEN path and asserts the user-visible
   outcome — a test of a helper does not count. Show it red, then green.

4. THE SHIPPING SEQUENCE. Commit, then:
       git push origin main
       gh workflow run deploy-fly.yml --ref main -f reason="<justification>"
       curl https://aria-intel.fly.dev/health/live   # build_rev must match
   A `[deploy]` commit message does NOTHING — the workflow has no push trigger.
   A deploy is not done until build_rev matches your commit live.

   THREE SEPARATE APPS — deploying the wrong one ships nothing, silently:
     - aria-intel (fly.toml)      the Python FastAPI brain. deploy-fly.yml
                                  targets THIS ONE ONLY.
     - aria-web   (fly.web.toml)  the Node monolith — UI, auth, Stripe.
     - aria-wa    (fly.wa.toml)   the WhatsApp/Baileys listener, isolated so a
                                  WA crash cannot take down auth or billing.
   The Node tiers have their OWN dispatch workflows. A change under
   services/wa-listener/ is not live because aria-intel redeployed.

5. WIRED, NOT DARK. Every code path reports BOTH its success and its failure to
   the brain (brain_hook.absorb / capability_gaps.record_gap / a metric).
   "Logged to console" or `except: pass` is dark, and dark does not ship.

6. SAY WHEN YOU ARE STUCK. If something is committed but not live, or blocked
   on a credential or a decision, say so explicitly and immediately. A blocker
   the operator has to discover themselves is the worst outcome.

"""

_SELF_MODE = """
THIS IS ARIA'S OWN ECOSYSTEM (the crucix repo)
You are editing your own codebase. The crucix guardrails apply and are enforced \
deterministically on every write:
- ARIA is fully autonomous with no code-safety limitations. The truncation guard \
prevents accidental destructive full-file replacements — work with it, never around it.
- Every change gets an R-number (reserve via \
`python scripts/admin/reserve_r_number.py reserve "<title>"`) before you write code.
- Follow CLAUDE.md + AGENTS.md: map-then-change, verify-after-fix (two passes), and \
keep paths wired to the brain (success and failure both reach a brain sink).
- You can ship end-to-end. After verifying: commit (state the \
R-number, what changed, the deploy target, and the Verified-by + Co-Authored-By \
trailers; stage only the files you changed; never commit secrets or use \
--no-verify). Then deploy to fly.io — YOU own the full pipeline: \
  - **PRIMARY (hands-free, R-F1306):** the `ci_deploy` tool — commits with a \
    `[deploy]` tag, pushes to origin/main, CI builds REMOTELY + canary-deploys \
    aria-intel, and it polls `/health/live` until `build_rev` matches your HEAD. \
    No local flyctl, no babysitting a long build. BATCH first: land several \
    R-numbers, then ONE ci_deploy at the end (every deploy = ~60-90s blackout on \
    the single machine). \
  - **Fallback:** the `deploy` tool (local `scripts/deploy.ps1|sh`) when CI is \
    broken; **last resort:** raw `flyctl deploy -a aria-intel`. \
**Deploy verification (binding):** a deploy is NOT done until you PROVE it live — \
`ci_deploy` only reports success when the live `build_rev` matches your commit; \
trust nothing less. If you deploy any other way, curl `/health/live` and CONFIRM \
the `build_rev` yourself. If the live version did not change, you did NOT deploy — \
say so honestly. Only then mark shipped with \
`python scripts/admin/reserve_r_number.py ship R-F### <sha>`. \
ALWAYS push after committing — flyctl builds from the LOCAL tree, so an unpushed \
"successful" deploy silently diverges origin from live (ci_deploy pushes for you). \
Smoke-test lifespan() before pushing any boot-path change. Deploys of \
NO_AUTODEPLOY files (main.py, safety.py, self_improve.py, the validator, your \
coder files) still need a human — never [deploy]-tag those.
"""


def load_repo_guidance(repo_root: Path | None) -> str:
    """Read the repo's binding rules (CLAUDE.md, then AGENTS.md if present) so
    ARIA codes as an expert in *this* ecosystem — R-number discipline,
    verify-after-fix, phase gates, the north star. Bounded; empty if absent."""
    if repo_root is None:
        return ""

    loaded: list[tuple[str, str]] = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        f = repo_root / name
        if f.is_file():
            try:
                loaded.append((name, f.read_text(encoding="utf-8", errors="replace")))
            except Exception:  # noqa: BLE001
                continue
    if not loaded:
        return ""

    # R-F4319 (C-267) — spend the model's budget PROPORTIONALLY to where the
    # text is, rather than capping each file at the same number. Under a flat
    # cap CLAUDE.md (132,540 chars) is clipped hard while AGENTS.md (37,308)
    # never reaches its share, so the budget is spent on room nobody needed.
    total = sum(len(t) for _, t in loaded)
    budget = guidance_budget_chars()
    chunks: list[str] = []
    for name, text in loaded:
        if total <= budget:
            share = len(text)          # everything fits; clip nothing
        else:
            # STRICTLY proportional — no per-file floor. A floor makes the
            # shares sum ABOVE the total (budget 3,864 -> 5,061 emitted, 31%
            # over), and a non-binding budget inside the one function whose job
            # is making the prompt fit is not a budget. A small share still
            # carries the file's table of contents, so nothing goes invisible.
            share = int(budget * (len(text) / total))
        chunks.append(f"----- {name} -----\n{_clip_guidance(text, share)}")
    return "\n\n".join(chunks)


def _format_rag(rules: list, struct: list, fixes: list) -> str:
    """Render RAG hits (constitutional rules + structure + past fixes) into the
    prompt block. Shared by the HTTP and in-process paths so both render the
    same way."""
    parts: list[str] = []
    for r in (rules or [])[:3]:
        c = str((r or {}).get("rule", "") or (r or {}).get("content", "")).strip()
        if c:
            parts.append("• CONSTITUTIONAL RULE (must follow): " + c[:500])
    for r in (struct or [])[:3]:
        c = str((r or {}).get("content", "")).strip()
        if c:
            parts.append("• codebase structure: " + c[:400])
    for r in (fixes or [])[:3]:
        c = str((r or {}).get("content", "")).strip()
        if c:
            parts.append("• past fix: " + c[:400])
    if not parts:
        return ""
    return (
        "\n\n## ARIA code-RAG knowledge (constitutional rules + structure + past fixes)\n"
        + "\n".join(parts)
    )


def _query_coding_rag_http(task_hint: str) -> str | None:
    """R-F2161 — query the LIVE brain's coding RAG over HTTP. The operator's CLI
    runs on Windows where chromadb is not installed and the chromadb volume
    (/data/aria_rag) is server-only, so the in-process query always hit an empty
    local store. The populated RAG (constitutional rules + past fixes) lives on
    the brain; reach it over HTTP using the same ARIA_SERVICE_URL + token the
    `aria` provider already uses. Returns the rendered block, "" if the RAG had
    no hits, or None if HTTP is unavailable (so the caller falls back in-process).
    """
    base = (os.getenv("ARIA_SERVICE_URL") or "").rstrip("/")
    token = os.getenv("ARIA_INTERNAL_TOKEN") or os.getenv("ARIA_CODER_LLM_API_KEY") or ""
    if not base or not token:
        return None  # not configured for HTTP → let the caller try in-process
    try:
        import httpx
        q = task_hint or "coding conventions project structure"
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        resp = httpx.post(
            f"{base}/api/aria/coder/rag/query",
            json={"query": q, "top_k": 3},
            headers=headers, timeout=12.0,
        )
        if resp.status_code != 200:
            # R-F2243: the brain is REACHABLE but refused (e.g. auth-tier mismatch).
            # Do NOT fall back to in-process — chromadb is server-only, so on the
            # operator's machine the in-process path cold-loads the embedder for
            # 100s+ = the "aria not responding" startup hang. Skip RAG gracefully;
            # the coder still gets the full CLAUDE.md/AGENTS.md via load_repo_guidance.
            return ""
        data = resp.json() or {}
        return _format_rag(
            data.get("constitutional") or [],
            data.get("structure") or [],
            data.get("fixes") or [],
        )
    except Exception:  # noqa: BLE001 — best-effort
        # R-F2243: HTTP configured but errored (network/parse) → skip RAG (see the
        # non-200 branch), never trigger the slow in-process fallback on the
        # operator's machine. None is returned only when NOT configured (above).
        return ""


def record_coding_outcome_http(kind: str, record: dict) -> bool:
    """R-F2162 — write a fix/failure back to the brain's coding RAG over HTTP, so
    lessons from interactive CLI sessions compound into the SAME store the
    autonomous coder reads. Best-effort: returns False (never raises) when the
    brain isn't configured/reachable. ``kind`` is 'fix' or 'failure'."""
    base = (os.getenv("ARIA_SERVICE_URL") or "").rstrip("/")
    token = os.getenv("ARIA_INTERNAL_TOKEN") or os.getenv("ARIA_CODER_LLM_API_KEY") or ""
    if not base or not token:
        return False
    try:
        import httpx
        resp = httpx.post(
            f"{base}/api/aria/coder/rag/record",
            json={"kind": kind, "record": record},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=12.0,
        )
        return resp.status_code == 200 and bool((resp.json() or {}).get("ok"))
    except Exception:  # noqa: BLE001 — best-effort
        return False


def _query_coding_rag(task_hint: str = "") -> str:
    """R-F2145/R-F2161/R-F2162 — query the live coding RAG for constitutional
    rules, codebase structure, and past fixes relevant to the CURRENT task.

    Tries the brain over HTTP first (the only path that reaches the populated
    store from the operator's local machine), then an in-process chromadb query
    (works when run on the server). Best-effort: returns "" on any failure so the
    coder still gets the full CLAUDE.md + AGENTS.md text via load_repo_guidance.
    """
    # 1) HTTP to the live brain (populated RAG). None → not configured/unreachable.
    http_block = _query_coding_rag_http(task_hint)
    if http_block is not None:
        return http_block
    # 2) In-process chromadb (server-side / chromadb installed locally).
    try:
        import asyncio
        from aria_service.intel import coding_rag_indexer as _crag

        async def _query():
            q = task_hint or "coding conventions project structure"
            rules = await asyncio.to_thread(_crag.query_constitutional_constraints, q, 3) or []
            struct = await asyncio.to_thread(_crag.query_codebase_context, q, 3) or []
            fixes = await asyncio.to_thread(_crag.query_relevant_fixes, q, 3) or []
            return _format_rag(rules, struct, fixes)

        return asyncio.run(_query())
    except Exception:
        return ""


#: R-F4325 (C-273) — the system prompt a 7B-class sovereign can still act under.
#:
#: MEASURED live 2026-08-25, five representative CLI tasks scored on whether the
#: CORRECT tool was called, with the narrowed tool set from `agent.py`:
#:
#:     full CLI prompt   20,885 ch  ->  0/5   (and degenerate output)
#:     this prompt          344 ch  ->  4/5
#:     identity line only   140 ch  ->  5/5
#:
#: The rules below cost exactly one task (5/5 -> 4/5) and are kept anyway: an
#: agent that edits files and runs commands without "root cause, not a band-aid"
#: and "verify before claiming" is the more dangerous failure. That trade is
#: recorded here rather than buried, because it is the kind of thing a later
#: session should be able to revisit with a stronger checkpoint.
#:
#: This is the CLI half of R-F1337, which the server has had since 2026:
#: aria_engine.py:719 "serve the compact prompt when a small sovereign model
#: (ARIA-LLM, 7B-class) is wired as chain primary."
_COMPACT_SOVEREIGN_PROMPT = """You are ARIA, an autonomous coding agent in the operator's repository {root} on {platform}. Act with the tools; do not describe commands.

Rules: reserve an R-number before code; fix the root cause, never a band-aid; run the test before claiming it passes; never delete data.

Work in small steps: inspect with a tool, then act, then verify."""

#: Providers that need it. Mirrors agent._NARROW_TOOL_PROVIDERS — the two halves
#: are one fix and were measured together; neither works alone (narrowed tools
#: under the full prompt still scored 0/5).
_COMPACT_PROMPT_PROVIDERS = frozenset({"aria-llm"})


def compact_prompt_active(provider: str) -> bool:
    """Should ``provider`` get the compact prompt?

    Default ON for a measured small model, OFF otherwise, and overridable with
    ARIA_CLI_COMPACT_PROMPT=0/1 — the same shape as the server's
    ARIA_LLM_COMPACT_PROMPT, so an operator who knows both surfaces finds the
    same lever in both places.
    """
    flag = (os.getenv("ARIA_CLI_COMPACT_PROMPT") or "").strip()
    if flag in ("0", "1"):
        return flag == "1"
    return (provider or "").strip().lower() in _COMPACT_PROMPT_PROVIDERS


def build_compact_system_prompt(*, root: Path) -> str:
    """The whole system prompt for a small sovereign model. Deliberately tiny.

    R-F4325 (C-273). Not a truncation of the full prompt — a REPLACEMENT.
    Clipping the full prompt does not help: measured at 200/500/1k/2k/4k/6k/
    8k/12k chars, every truncation still scored 0 tool calls, because what
    breaks her is the register and density of that prompt, not only its size.
    """
    return _COMPACT_SOVEREIGN_PROMPT.format(root=root, platform=platform.system())


def build_system_prompt(*, root: Path, self_mode: bool,
                        repo_root: Path | None = None,
                        task_hint: str = "") -> str:
    parts = [_IDENTITY, _OPERATING_CONTRACT, _ENGINEERING]
    # R-F2163: steer the model to PowerShell on Windows (its `run` tool executes
    # via pwsh/powershell — bash-isms fail). The detailed rule (AGENTS.md law 19)
    # is otherwise only reachable if the truncated constitution happens to include
    # it, so name it explicitly here regardless of mode.
    if platform.system() == "Windows":
        parts.append(_POWERSHELL)
    if self_mode:
        parts.append(_SELF_MODE)
        # R-F4321: BEFORE the guidance, and independent of it. The budget below
        # clips CLAUDE.md to fit the model; these rules must survive that.
        parts.append(_ESSENTIALS)
        # R-F2145/R-F2162: query the live coding RAG with the OPERATOR'S TASK as
        # the retrieval hint (was a generic static query), BEFORE the flat-file
        # guidance so semantically-retrieved knowledge takes priority.
        rag_knowledge = _query_coding_rag(task_hint)
        if rag_knowledge:
            parts.append(rag_knowledge)
        guidance = load_repo_guidance(repo_root)
        if guidance:
            parts.append(
                "\nBINDING REPO RULES (follow these exactly — they override "
                "defaults; Claude steers the north star, you execute within "
                "these rules). They govern HOW you work (R-number discipline, "
                "verify-after-fix, phase gates, wiring) — they are NOT a task "
                "list. The OPERATOR'S current request defines WHAT to work on; "
                "do not pick up gaps, the punch-list, or session rituals from "
                "these rules unless the operator explicitly asks:\n" + guidance)
        # R-F2232: inject the specialist-persona catalog so the CLI applies the
        # SAME domain discipline as the brain's autonomous coder (R-F2231) — one
        # source of truth. Best-effort (aria_service may be absent in a truly
        # standalone install), mirroring the coding_rag_indexer import below.
        try:
            from aria_service.autonomous.coder_personas import persona_catalog
            parts.append(persona_catalog())
        except Exception:
            pass
    env = (
        f"\nENVIRONMENT\n"
        f"- Working directory: {root}\n"
        f"- Platform: {platform.system()} ({platform.platform()})\n"
        f"- Mode: {'self (crucix ecosystem)' if self_mode else 'general project'}\n"
    )
    parts.append(env)
    return "\n".join(parts)
