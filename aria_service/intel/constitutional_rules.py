# -*- coding: utf-8 -*-
"""R-F2130 — canonical, structured constitutional/playbook rules for the coder.

The autonomous coder's `coding_constitutional` RAG collection was BUILT, TESTED,
but never populated (`index_constitutional_rules()` was only ever called in
tests), so the coder was grounded in *what the code looks like* (structure) and
*what failed before* (failures/fixes) but NOT in *how to write code correctly*.
That gap plausibly contributed to convention-violating autonomous edits (e.g. the
2026-06-28 annotation campaign that inserted comments mid-expression and shipped
31 syntax errors).

This module is the single source of truth: the CLAUDE.md / AGENTS.md coding
discipline distilled into the dict shape `coding_rag_indexer.index_constitutional_rules`
expects, so it can be semantically retrieved while the coder plans a fix. Edit
the rules here; `RULES_VERSION` changes automatically and the boot sync re-indexes.

Each rule: name, clause_number, description (the rule), constraint (the actionable
thing the coder must DO/NOT do), affected_modules, protected_files, consequence.
"""
from __future__ import annotations

import hashlib
import json

# Operator-protected files the coder must NEVER modify autonomously (R-F462/R-F902).
_PROTECTED = ["server.mjs", "main.py (boot/lifespan)", "fly.toml", "Dockerfile"]

CONSTITUTIONAL_RULES: list[dict] = [
    {
        "name": "root-cause-not-symptom",
        "clause_number": "CLAUDE.md §1",
        "description": "Never apply a band-aid (timeout increase, retry-count bump, "
                       "cooldown extension) without first doing a deep-dive to find the "
                       "real cause. Every fix must eliminate the failure CLASS, not hide it.",
        "constraint": "If you catch yourself raising a timeout or adding a retry, STOP and "
                      "ask what is actually slow/breaking and why; fix THAT. No symptom patches.",
        "consequence": "Reject — recurring failure, operator distrust.",
    },
    {
        "name": "verify-function-name-before-calling",
        "clause_number": "CLAUDE.md §3b",
        "description": "Before writing any call to module.function(), verify the function "
                       "exists (grep its def) and whether it is sync or async. Shipping a "
                       "wrong/nonexistent function name has crashed prod.",
        "constraint": "grep `def <fn>` / `async def <fn>` in the target module before calling "
                      "it; never await a sync function. Stdlib/well-known pkgs are exempt.",
        "consequence": "NameError/AttributeError at runtime — boot or request crash.",
    },
    {
        "name": "capability-test-required-per-fix",
        "clause_number": "CLAUDE.md §3c / §5",
        "description": "Every fix MUST include a capability test that invokes the actual "
                       "broken path and asserts the user-visible outcome — not just a helper. "
                       "Run it before the fix (fails) and after (passes).",
        "constraint": "Add a test that calls the real entry point with the operator's real "
                      "input shape and asserts the real symptom is gone. A green test on a "
                      "proxy path while the live flow stays broken is a WRONG test.",
        "consequence": "Verified-by is a lie; the symptom survives.",
    },
    {
        "name": "compile-before-commit-and-deploy",
        "clause_number": "CLAUDE.md §11c",
        "description": "Never commit or deploy without compiling the whole changed tree. An "
                       "autonomous annotation campaign once shipped 31 syntax errors to main "
                       "and reported it 'safe to deploy'.",
        "constraint": "py_compile every changed .py (and the whole tree before a deploy); "
                      "refuse on any SyntaxError. 'Safe to deploy' from any agent is not "
                      "sufficient — independently compile-verify.",
        "consequence": "Un-importable tree → guaranteed boot failure on deploy.",
    },
    {
        "name": "annotations-and-edits-never-mid-expression",
        "clause_number": "R-F2126/R-F2127",
        "description": "When inserting a comment/annotation or editing a line programmatically, "
                       "it MUST go at the END of the logical line and the file MUST still parse. "
                       "Inserting `# comment` mid-expression (e.g. inside `f(a  # x =1)`) breaks "
                       "the parse.",
        "constraint": "After any programmatic edit, ast.parse/py_compile the file and revert on "
                      "SyntaxError. Append annotations at end-of-line, never between tokens of an "
                      "expression or outside a string literal.",
        "consequence": "SyntaxError; the module and everything importing it dies.",
    },
    {
        "name": "map-then-change",
        "clause_number": "CLAUDE.md §8",
        "description": "Read the area of change before editing. Trace who calls this, what "
                       "state it writes, and what reads that state. Don't pile fixes without "
                       "tracing the chain or introduce abstractions beyond the task.",
        "constraint": "Before editing, map the call sites + downstream readers; keep the change "
                      "minimal and in the style of the surrounding code.",
        "consequence": "Hidden regressions in callers/readers.",
    },
    {
        "name": "lifespan-smoke-on-boot-path-change",
        "clause_number": "CLAUDE.md §9",
        "description": "For any change to main.py or a boot/lifespan path, smoke-test the "
                       "lifespan locally before push. 1109 unit tests once passed while lifespan "
                       "failed at boot (local-var scoping) and took prod down.",
        "constraint": "Import aria_service.main and exercise the boot path; never let heavy/"
                      "blocking work sit on the synchronous pre-yield critical path.",
        "affected_modules": ["main"],
        "consequence": "Green tests, dead boot.",
    },
    {
        "name": "wire-success-and-failure-to-brain",
        "clause_number": "CLAUDE.md §21a",
        "description": "A code path is 'wired' only if BOTH its success and failure branches "
                       "reach a brain sink (brain_hook.absorb / capability_gaps.record_gap / "
                       "mistake_ledger / a metric / brain signal). Console/except-pass/Telegram-"
                       "only is DARK, not wired.",
        "constraint": "Every new/touched intel module must call wire_success() on success AND "
                      "wire_failure() on every error path. Import both from engine_wiring.",
        "consequence": "Dark module — the brain can't see or self-heal it.",
    },
    {
        "name": "circuit-breaker-on-outbound-http",
        "clause_number": "CLAUDE.md §21b / pre-commit",
        "description": "Outbound HTTP backends need a circuit breaker, or an explicit, honest "
                       "'# no-breaker: <reason>' at the END of the client line documenting why.",
        "constraint": "Wrap outbound calls in the breaker, or annotate the line end with "
                      "'# no-breaker: <reason>'. Never silently omit it.",
        "consequence": "A flapping provider wedges the engine.",
    },
    {
        "name": "no-hardcoded-token-default-fail-closed",
        "clause_number": "CLAUDE.md §19 / audit H2",
        "description": "Auth tokens/secrets must fail closed — never a hardcoded default that "
                       "lets the system run unauthenticated.",
        "constraint": "Read secrets from env with NO permissive default; refuse to operate if "
                      "unset rather than defaulting to an embedded value.",
        "consequence": "Silent auth bypass.",
    },
    {
        "name": "ssrf-boundary-on-user-urls",
        "clause_number": "audit C2",
        "description": "Outbound fetch of a user-controlled URL must go through the url_safety "
                       "guard (safe_get), or carry a documented '# no-ssrf-check' exception.",
        "constraint": "Route user-URL fetches through url_safety.safe_get; never raw httpx on an "
                      "attacker-influenced URL.",
        "consequence": "SSRF to internal services.",
    },
    {
        "name": "shell-quote-every-arg",
        "clause_number": "R-F2095/R-F2128",
        "description": "Every value interpolated into a shell command (filenames, commit "
                       "messages, titles, paths) must be quoted/escaped. Raw f-string "
                       "interpolation lets backticks/$()/quotes inject or break the command.",
        "constraint": "Use the project shell-quote helper (_shq / shlex.quote) on EVERY "
                      "interpolated value, including paths and LLM-generated strings.",
        "consequence": "Shell injection / broken commands.",
    },
    {
        "name": "loop-never-blocks-offload-heavy-work",
        "clause_number": "scaling roadmap / R-F2044",
        "description": "The brain is single-process asyncio: one blocking/CPU-bound call on the "
                       "event loop freezes ALL users. GIL-heavy work (sentence-transformer "
                       "encode, large JSON, AST parse of big files) must not run on the loop.",
        "constraint": "Offload CPU/GIL-bound or long synchronous work with asyncio.to_thread / "
                      "the process-offload path; keep coroutines non-blocking.",
        "affected_modules": ["main", "rag_store", "neural_memory"],
        "consequence": "Event-loop wedge — every request times out.",
    },
    {
        "name": "stream-path-must-mirror-non-stream",
        "clause_number": "CLAUDE.md §13",
        "description": "aria_chat_stream is a subset-fork of aria_chat. Every new post-response "
                       "hook (guard, audit, capture) must be mirrored into BOTH paths.",
        "constraint": "When you add a hook to aria_chat, add the identical hook to "
                      "aria_chat_stream (and vice-versa).",
        "consequence": "Web stream silently misses guards the WA path enforces.",
    },
    {
        "name": "never-modify-protected-files",
        "clause_number": "CLAUDE.md §17 / R-F462/R-F902",
        "description": "The coder must not autonomously modify operator-protected files. These "
                       "stay under human control.",
        "constraint": "Refuse to edit server.mjs, fly.toml, Dockerfile, or the main.py boot/"
                      "lifespan path autonomously; escalate to the operator instead.",
        "protected_files": _PROTECTED,
        "consequence": "FATAL — constitutional validator rejects the change.",
    },
    {
        "name": "diagnose-from-evidence-never-fabricate",
        "clause_number": "CLAUDE.md §22",
        "description": "A root-cause or status claim is only allowed when backed by hard "
                       "evidence: code read at file:line, a live probe, or a real log line. "
                       "Absence of logs is not proof. Read the function before asserting its "
                       "behaviour.",
        "constraint": "Cite file:line or a probe for every causal claim; state UNKNOWN and go "
                      "get evidence rather than inferring. Verify a deploy by the target app's "
                      "live build_rev, not by 'it pushed'.",
        "consequence": "Fabricated diagnosis wastes the operator's time.",
    },
    {
        "name": "run-it-dont-claim-it",
        "clause_number": "CLAUDE.md §23",
        "description": "Never write 'fixed/done/passing' without running the command and showing "
                       "the real pass/fail count, reproducing the OPERATOR'S actual path (same "
                       "entry point + input), and cross-checking independently.",
        "constraint": "Paste the real test command + true counts; if you cannot run/reproduce "
                      "it, say so plainly. Don't pass an author's unverified claim through.",
        "consequence": "False 'fixed' — the live flow stays broken.",
    },
    {
        "name": "honest-verified-by",
        "clause_number": "CLAUDE.md §19d",
        "description": "Only claim 'Verified-by: tests' when a test in the diff calls the broken "
                       "function, you ran it, and it asserts the user-visible outcome. Otherwise "
                       "say 'Verified-by: manual-read' and state what you checked.",
        "constraint": "Match the Verified-by line to what you actually did. No overclaiming.",
        "consequence": "Erodes trust in every future claim.",
    },
    {
        "name": "r-number-discipline",
        "clause_number": "CLAUDE.md §2",
        "description": "Every change gets an R-number reserved via the registry before code. "
                       "Don't claim a number by writing it in a comment.",
        "constraint": "Reserve with scripts/admin/reserve_r_number.py before coding; mark "
                      "shipped at push.",
        "consequence": "R-number collisions, rename passes.",
    },
    {
        "name": "one-deploy-at-a-time",
        "clause_number": "R-F2126 incident",
        "description": "Multiple agents/CI racing the fly deploy lease SIGTERM each other's "
                       "in-flight boots and corrupt deploys. A slow boot is not a crash loop — "
                       "wait the full boot before diagnosing or restarting.",
        "constraint": "Coordinate one deploy at a time; never restart-spam a booting machine "
                      "(it resets the boot clock).",
        "consequence": "Self-inflicted multi-hour outage.",
    },
    {
        "name": "preserve-whole-files-no-truncation",
        "clause_number": "R-F903/R-F904",
        "description": "Autonomous fixes must emit COMPLETE files — never truncated stubs that "
                       "wipe core modules. Truncation/preservation guards exist for this reason.",
        "constraint": "Generate the whole file with all existing functionality preserved; never "
                      "a partial stub. The preservation guard will (and should) block stubs.",
        "consequence": "A truncating fix would erase a core module.",
    },
    {
        "name": "windows-and-cross-platform-compat",
        "clause_number": "CLAUDE.md §16 / pre-commit",
        "description": "Code runs on Windows (dev) and Linux (fly). Avoid POSIX-only assumptions "
                       "(signals, paths, /tmp, NUL) without a guard.",
        "constraint": "Use pathlib + os-agnostic paths; guard platform-specific calls; the "
                      "scratchpad/tmp dir, not hardcoded /tmp.",
        "consequence": "Works in dev, breaks in prod (or vice-versa).",
    },
    {
        "name": "no-eviction-infinite-memory",
        "clause_number": "CLAUDE.md §7",
        "description": "ARIA has infinite memory: no TTL on knowledge, no oldest-first prune, no "
                       "eviction. Overflow goes to cold storage, never delete. Self-study writes "
                       "must never be paired with a prune.",
        "constraint": "Never add a TTL/prune/eviction to a knowledge or memory write path.",
        "consequence": "Irreversible knowledge loss.",
    },
    {
        "name": "surface-stuck-or-undeployed-work",
        "clause_number": "CLAUDE.md §19e",
        "description": "The instant work is blocked or a commit is committed-but-not-live, say "
                       "so explicitly: what is DONE, what is STUCK, WHY, and the exact ACTION "
                       "needed. Every task that produces a commit ends with its deploy status.",
        "constraint": "End code-change reports with '✅ live on <app>, build_rev=<sha>' or "
                      "'⚠️ committed but NOT deployed because <reason> — needs <action>'.",
        "consequence": "The operator discovers undeployed work himself — the worst outcome.",
    },
    # ── R-F2133: coding convention rules ──────────────────────────────────
    {
        "name": "coding-convention-error-messages",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Error messages must be informative: include the operation that failed, "
                       "the reason (exception text), and any relevant context (key name, module). "
                       "Never log bare exceptions without context.",
        "constraint": "Every logger.error/warning call includes a descriptive prefix and the "
                      "relevant variables. Pattern: 'module: operation failed: {reason}'.",
        "consequence": "Bare exceptions in logs are undiagnosable in production.",
    },
    {
        "name": "coding-convention-import-ordering",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Imports follow a consistent order: standard library → third-party → "
                       "local. Groups separated by blank lines. No wildcard imports.",
        "constraint": "Within each file, imports are grouped: stdlib, then third-party, then "
                      "local. Use explicit names, not `from module import *`.",
        "consequence": "Mixed import orders make review harder and can hide circular imports.",
    },
    {
        "name": "coding-convention-type-hints",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Every public function and method has type hints on all parameters "
                       "and the return value. Internal helpers should also be typed.",
        "constraint": "def func(param: type, ...) -> return_type: — no bare `def func(param)` "
                      "without types. Use `from __future__ import annotations` for forward refs.",
        "consequence": "Untyped code is harder to review, refactor, and maintain.",
    },
    {
        "name": "coding-convention-docstrings",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Public functions and classes have docstrings explaining what they do, "
                       "not how. Args/returns documented for non-trivial interfaces.",
        "constraint": "Docstrings on every public callable. One-line is fine for simple "
                      "functions. Multi-line for complex: summary line, blank, then details.",
        "consequence": "Code without docstrings requires reading the implementation to understand intent.",
    },
    {
        "name": "coding-convention-test-structure",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Tests follow arrange-act-assert pattern. Test names describe the "
                       "scenario and expected outcome. Each test tests one thing.",
        "constraint": "test_<scenario>_<expected_outcome>(). Arrange inputs, act on the function "
                      "under test, assert the result. Avoid multiple assertions that test "
                      "different behaviours.",
        "consequence": "Poorly structured tests hide regressions and are hard to debug when they fail.",
    },
    {
        "name": "coding-convention-no-dead-code",
        "clause_number": "AGENTS.md §8.7 / R-F2133",
        "description": "Every code path added is reachable and handled. No commented-out code, "
                       "no TODO markers that are never actioned, no speculative abstractions.",
        "constraint": "Before adding a function, ask: 'Is this called anywhere? Is the return "
                      "value used?' Remove dead imports, unused variables, and stub functions.",
        "consequence": "Dead code accumulates until it blocks a deploy (R-F2126: 31 syntax errors).",
    },
]


def rules_version() -> str:
    """Stable hash of the rules content — changes whenever a rule is edited, so the
    boot sync knows to re-index."""
    blob = json.dumps(CONSTITUTIONAL_RULES, sort_keys=True, ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


RULES_VERSION = rules_version()
