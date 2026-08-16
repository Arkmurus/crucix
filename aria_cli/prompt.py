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


def _clip_guidance(text: str, cap: int) -> str:
    """Bound a guidance file to ``cap`` chars. Under cap → unchanged. Over cap →
    head (60%) + elision marker + tail (40%), so neither the binding floor at the
    top nor the operational rules at the bottom are lost."""
    if len(text) <= cap:
        return text
    head = int(cap * 0.6)
    tail = cap - head
    return (
        text[:head]
        + "\n\n…(MIDDLE ELIDED to fit — read the full file with read_file for the "
          "complete rules)…\n\n"
        + text[-tail:]
    )

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
    chunks: list[str] = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        f = repo_root / name
        if f.is_file():
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                continue
            text = _clip_guidance(text, _GUIDANCE_MAX_CHARS)
            chunks.append(f"----- {name} -----\n{text}")
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
