"""R-F802 — Constitutional validator for ARIA-generated code.

AST-level + regex-level safety validator. Every piece of code that
the autonomous self-coder writes must pass through `validate()` before
being staged for deployment.

The validator is itself in the PROTECTED set — ARIA's self-coder can
never modify it. Only operator + developer review can change this file.

Three check categories
──────────────────────
  FATAL   — protected-file modification → immediate reject
  BLOCK   — dangerous imports / weakening patterns / guard removal
  WARN    — setattr / suspicious patterns (logged, not blocked)

This implements the deterministic safety layer that R-F462 calls for:
no LLM-judged risk routing, no "is this safe?" prompted to a model.
AST walks + regex patterns are reproducible, auditable, and version-
controlled.

Lineage
───────
Architecture from `aria_autonomous_engine.zip` (operator-shared, 2026-05-22).
R-F462 PROTECTED_FILES list merged in from
`aria_service/intel/self_improve.py:92-101`. Redis namespaces switched
from `aria:` to `crucix:` to match current prod conventions.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.autonomous.constitutional_validator")


# ── PROTECTED FILES ──────────────────────────────────────────────────────────
# Files self-written code cannot modify. Combines:
#   (a) the validator itself + the constitutional-enforcement core
#   (b) R-F462 (2026-05-14) protected list from self_improve.py:92-101
# Operator + developer review required to modify any of these.
PROTECTED_FILES: frozenset[str] = frozenset({
    # (a) Validator + enforcement core
    "aria_service/autonomous/constitutional_validator.py",  # this file
    "aria_service/autonomous/fly_deployer.py",              # deploy pipeline
    "aria_service/autonomous/self_coder.py",                # the coder itself
    "aria_service/autonomous/safety.py",                    # cost/rate guardrails
    "aria_service/aria_engine.py",                          # constitution enforcement
    "aria_service/intel/source_verifier.py",                # grounding guard
    "aria_service/intel/verification_gate.py",              # 2-provider guard
    "aria_service/intel/response_verifier.py",              # response guard
    "aria_service/intel/stream_guard_observer.py",          # stream guards
    "aria_service/intel/adversarial_challenge.py",          # adversarial suite
    "aria_service/intel/chat_audit_log.py",                 # tamper-evident audit
    "aria_service/intel/self_improve.py",                   # the existing staging system
    # (b) R-F462 protected list — operator's audit mandate 2026-05-14
    "server.mjs",                                           # Node core
    "lib/auth/users.mjs",                                   # authentication
    "lib/auth/email.mjs",                                   # email auth
    "lib/auth/audit.mjs",                                   # audit logging
    "middleware/rateLimiter.mjs",                           # security
    ".env",                                                 # secrets
    "Dockerfile",                                           # deployment
    "aria_service/Dockerfile",                              # Python deployment
    "package.json",                                         # dependencies
    "fly.toml",                                             # fly config
})

# Function/method names that must never be removed or stubbed-out.
PROTECTED_FUNCTIONS: frozenset[str] = frozenset({
    "check_constitution",
    "verify_sources",
    "run_verification_gate",
    "enforce_clause",
    "guard_stream",
    "constitutional_validator",
    "validate_before_deploy",
    "run_adversarial",
    "_check_grounding",
    "_verify_claims",
})

# Imports that indicate security risks in self-written code.
DANGEROUS_IMPORTS: frozenset[str] = frozenset({
    "subprocess",                            # use fly_deployer instead
    "os.system",
    "pickle", "shelve", "marshal",           # deserialisation attacks
    "ctypes", "cffi",                        # C-level memory access
    "paramiko",                              # SSH — not needed
    "cryptography.hazmat.primitives.ciphers.algorithms.ARC4",  # weak crypto
})

# Regex patterns indicating attempts to weaken safety. Each tuple is
# (pattern, label). Patterns are matched case-insensitive, multiline.
WEAKENING_PATTERNS: list[tuple[str, str]] = [
    (r"skip_verification\s*=\s*True",         "Disabling verification flag"),
    (r"bypass_guards?\s*=\s*True",            "Disabling guard flag"),
    (r"ENABLE_GUARDRAILS\s*=\s*False",        "Disabling guardrails"),
    (r"constitution\s*=\s*\[\s*\]",           "Clearing constitution clauses"),
    (r"del\s+\w*guard\w*",                    "Deleting guard object"),
    (r"#\s*noqa.*guard",                      "Suppressing guard linting"),
    (r"jwt\.decode.*verify\s*=\s*False",      "Disabling JWT verification"),
    (r"allow_all\s*=\s*True",                 "Enabling allow-all mode"),
    (r"flyctl\s+secrets\s+(set|unset)",       "Modifying fly secrets from code"),
    (r"os\.environ\[.ARIA_INTERNAL_TOKEN.?\]\s*=", "Overwriting internal token"),
    (r"PROTECTED_FILES\s*=\s*(frozenset\(\)|set\(\)|\{\s*\}|\[\s*\])",
     "Clearing protected-files set"),
]


# ── RESULT TYPES ─────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    risk_score: float = 0.0   # 0.0 safe → 1.0 critical

    def add_violation(self, msg: str, score_delta: float = 0.3) -> None:
        self.violations.append(msg)
        self.passed = False
        self.risk_score = min(1.0, self.risk_score + score_delta)

    def add_warning(self, msg: str, score_delta: float = 0.1) -> None:
        self.warnings.append(msg)
        self.risk_score = min(1.0, self.risk_score + score_delta)


# ── MAIN VALIDATOR ───────────────────────────────────────────────────────────

class ConstitutionalValidator:
    """AST + regex validator for all ARIA-generated code.

    Runs before any code is tested or deployed. Cannot be modified by
    ARIA-Coder autonomously — this file is in PROTECTED_FILES.
    """

    def validate(
        self,
        code: str,
        target_file: str,
        patch_context: Optional[str] = None,
    ) -> ValidationResult:
        """Validate generated code against constitutional constraints."""
        result = ValidationResult(passed=True)

        # 1. Protected-file check (FATAL — short-circuits everything else)
        if target_file in PROTECTED_FILES:
            result.add_violation(
                f"FATAL: {target_file} is in PROTECTED_FILES — "
                f"self-written code cannot modify constitutional/security "
                f"components. Operator + developer review required.",
                score_delta=1.0,
            )
            return result

        # 2. AST-level analysis (requires valid Python)
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            result.add_violation(
                f"Syntax error in generated code: {e}",
                score_delta=0.5,
            )
            return result

        self._check_ast(result, tree)

        # 3. Regex pattern checks (catches non-AST safety patterns)
        self._check_patterns(result, code)

        # 4. Protected-function integrity if patching a guard file
        if "guard" in target_file.lower() or "verifier" in target_file.lower():
            self._check_protected_functions(result, tree)

        # 5. Import safety
        self._check_imports(result, tree)

        logger.info(
            "[constitutional_validator] %s passed=%s risk=%.2f "
            "violations=%d warnings=%d",
            target_file, result.passed, result.risk_score,
            len(result.violations), len(result.warnings),
        )
        return result

    # ── AST WALKS ────────────────────────────────────────────────────────────

    def _check_ast(self, result: ValidationResult, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            # Dynamic code execution
            if isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr

                if func_name in ("eval", "exec", "compile"):
                    result.add_violation(
                        f"Dynamic code execution detected: {func_name}()",
                        score_delta=0.8,
                    )

                # Shell execution via os/subprocess
                if func_name in ("system", "popen", "run", "call", "check_output"):
                    if isinstance(node.func, ast.Attribute) and \
                       isinstance(node.func.value, ast.Name) and \
                       node.func.value.id in ("os", "subprocess"):
                        result.add_violation(
                            f"Shell execution via {node.func.value.id}.{func_name}() "
                            "— use fly_deployer or aria_api instead.",
                            score_delta=0.7,
                        )

                # setattr can override protected attrs — warn only (legitimate uses exist)
                if isinstance(node.func, ast.Name) and node.func.id == "setattr":
                    result.add_warning(
                        "setattr() detected — verify this is not bypassing guards.",
                    )

            # Deletion of guard-related attributes
            if isinstance(node, ast.Delete):
                for target in node.targets:
                    if isinstance(target, ast.Attribute):
                        if any(p in target.attr.lower()
                               for p in ("guard", "verify", "check", "clause")):
                            result.add_violation(
                                f"Deletion of safety attribute: {target.attr}",
                                score_delta=0.9,
                            )

    def _check_patterns(self, result: ValidationResult, code: str) -> None:
        for pattern, label in WEAKENING_PATTERNS:
            if re.search(pattern, code, re.IGNORECASE | re.MULTILINE):
                result.add_violation(
                    f"Safety-weakening pattern: {label}",
                    score_delta=0.7,
                )

    def _check_protected_functions(
        self, result: ValidationResult, tree: ast.AST,
    ) -> None:
        """Verify protected functions still have non-trivial bodies."""
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name in PROTECTED_FUNCTIONS:
                    body_stmts = [
                        s for s in node.body
                        if not isinstance(s, (ast.Pass, ast.Return, ast.Expr))
                    ]
                    if not body_stmts:
                        result.add_violation(
                            f"Protected function {node.name} has empty or "
                            "pass-only body — appears to have been gutted.",
                            score_delta=0.9,
                        )

    def _check_imports(self, result: ValidationResult, tree: ast.AST) -> None:
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # from X import Y — flag if X starts with a dangerous root
                module = node.module or ""
                names = [module]
            else:
                continue

            for name in names:
                if not name:
                    continue
                for dangerous in DANGEROUS_IMPORTS:
                    # Match top-level "subprocess" or "subprocess.run" both
                    if name == dangerous or name.startswith(dangerous + "."):
                        result.add_violation(
                            f"Dangerous import: {name} — use ARIA-approved "
                            "wrappers (httpx, fly_deployer, aria_api).",
                            score_delta=0.5,
                        )


# ── DIFF VALIDATOR ───────────────────────────────────────────────────────────

class DiffValidator:
    """Validates unified-diff patches to catch silent removal of safety lines."""

    CRITICAL_LINE_PATTERNS: list[str] = [
        r"constitution\.enforce",
        r"source_verifier\.verify",
        r"verification_gate\.check",
        r"response_verifier\.validate",
        r"brain_hook\.absorb",
        r"audit_log\.write",
        r"hallucination.*guard",
        r"grounded_rate",
        r"PROTECTED_FILES",
        r"_R462_AUTO_DEPLOY_DEFAULT",
    ]

    def validate_diff(self, unified_diff: str) -> ValidationResult:
        result = ValidationResult(passed=True)
        removed_lines = [
            line[1:] for line in unified_diff.split("\n")
            if line.startswith("-") and not line.startswith("---")
        ]
        for removed in removed_lines:
            for pattern in self.CRITICAL_LINE_PATTERNS:
                if re.search(pattern, removed, re.IGNORECASE):
                    result.add_violation(
                        f"Critical safety line removed: '{removed.strip()}' "
                        f"(matched: {pattern})",
                        score_delta=0.8,
                    )
        return result
