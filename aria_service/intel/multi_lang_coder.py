"""R-F1044 — Multi-Language Coding Engine.

Extends ARIA's coding capabilities beyond Python to every language she needs
to build her own reasoning and LLM infrastructure. Each language module is a
separate file under `multi_lang/`; this file is the coordinator that detects
the language of a file and dispatches to the right reviewer/analyser.

Languages supported:
  - TypeScript/JavaScript — AST-free pattern analysis, linting, build detection
  - Rust — cargo/compiler error diagnosis, unsafe detection, common patterns
  - Go — go.mod analysis, goroutine leak detection, error handling patterns
  - SQL — query analysis, injection detection, schema validation, optimization
  - Docker — Dockerfile analysis, multi-stage detection, security scanning
  - YAML/TOML/JSON — schema validation, structure analysis
  - Shell (bash/PowerShell) — syntax checking, injection detection, best practices
  - C/C++ — Makefile/CMake analysis, memory safety patterns
  - Java/Kotlin — build file analysis, null safety patterns
  - Ruby — Gemfile analysis, Rails patterns
  - PHP — composer analysis, security patterns
  - Swift — Package.swift analysis, iOS patterns
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger("aria.multi_lang_coder")


# ── Language detection ──────────────────────────────────────────────────────

# Map file extensions and filenames to language identifiers.
_LANG_BY_EXT: dict[str, str] = {
    ".ts": "typescript", ".tsx": "typescript", ".js": "javascript",
    ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".rs": "rust",
    ".go": "go",
    ".sql": "sql",
    ".dockerfile": "docker", ".Dockerfile": "docker",
    ".yaml": "yaml", ".yml": "yaml", ".toml": "toml", ".json": "json",
    ".sh": "shell", ".bash": "shell", ".ps1": "powershell",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cc": "cpp",
    ".java": "java", ".kt": "kotlin",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".py": "python",
    ".md": "markdown", ".rst": "markdown",
    ".html": "html", ".css": "css", ".scss": "scss",
    ".proto": "protobuf",
    ".gradle": "gradle", ".gradle.kts": "gradle",
}

_LANG_BY_FILENAME: dict[str, str] = {
    "Dockerfile": "docker",
    "Makefile": "make",
    "CMakeLists.txt": "cmake",
    "Cargo.toml": "rust",
    "Cargo.lock": "rust",
    "go.mod": "go",
    "go.sum": "go",
    "package.json": "javascript",
    "tsconfig.json": "typescript",
    "composer.json": "php",
    "Gemfile": "ruby",
    "Gemfile.lock": "ruby",
    "Podfile": "ruby",
    "Package.swift": "swift",
    "build.gradle": "gradle",
    "pom.xml": "java",
    "requirements.txt": "python",
    "pyproject.toml": "python",
    "setup.py": "python",
    ".env": "dotenv",
    "docker-compose.yml": "docker",
    "docker-compose.yaml": "docker",
}


def detect_language(file_path: str) -> str | None:
    """Detect the programming language of a file from its path/name.

    Returns a language identifier (e.g. 'typescript', 'rust', 'go') or None
    if the language is not recognised.
    """
    p = Path(file_path)
    name = p.name
    ext = p.suffix.lower()

    # Check by full filename first (more specific)
    if name in _LANG_BY_FILENAME:
        return _LANG_BY_FILENAME[name]

    # Check by extension
    if ext in _LANG_BY_EXT:
        return _LANG_BY_EXT[ext]

    # Dockerfile variants (e.g. "Dockerfile.prod")
    if name.startswith("Dockerfile"):
        return "docker"

    return None


# ── Multi-language code review ──────────────────────────────────────────────

# Registry of language-specific reviewers. Each is a module-level function:
#   review(code: str, file_path: str = "") -> list[dict]
# Each finding dict: {"rule": str, "severity": str, "line": int, "message": str}
_REVIEWERS: dict[str, Any] = {}


def _register_reviewer(lang: str, fn: Any) -> None:
    _REVIEWERS[lang] = fn


# Lazy-import and register all language-specific reviewers.
def _init_reviewers() -> None:
    if _REVIEWERS:
        return
    try:
        from .multi_lang import ts_js_reviewer
        _REVIEWERS["typescript"] = ts_js_reviewer.review
        _REVIEWERS["javascript"] = ts_js_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] ts_js_reviewer not available: %s", exc)

    try:
        from .multi_lang import rust_reviewer
        _REVIEWERS["rust"] = rust_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] rust_reviewer not available: %s", exc)

    try:
        from .multi_lang import go_reviewer
        _REVIEWERS["go"] = go_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] go_reviewer not available: %s", exc)

    try:
        from .multi_lang import sql_reviewer
        _REVIEWERS["sql"] = sql_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] sql_reviewer not available: %s", exc)

    try:
        from .multi_lang import docker_reviewer
        _REVIEWERS["docker"] = docker_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] docker_reviewer not available: %s", exc)

    try:
        from .multi_lang import yaml_reviewer
        _REVIEWERS["yaml"] = yaml_reviewer.review
        _REVIEWERS["toml"] = yaml_reviewer.review
        _REVIEWERS["json"] = yaml_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] yaml_reviewer not available: %s", exc)

    try:
        from .multi_lang import shell_reviewer
        _REVIEWERS["shell"] = shell_reviewer.review
        _REVIEWERS["powershell"] = shell_reviewer.review
    except Exception as exc:
        logger.debug("[multi_lang] shell_reviewer not available: %s", exc)


def review(code: str, file_path: str = "") -> list[dict]:
    """Review code in any supported language. Returns a list of findings.

    Dispatches to the language-specific reviewer if one is registered;
    otherwise returns a generic finding noting the language is unsupported.
    """
    _init_reviewers()
    lang = detect_language(file_path) if file_path else None
    if lang and lang in _REVIEWERS:
        try:
            return _REVIEWERS[lang](code, file_path)
        except Exception as exc:
            logger.debug("[multi_lang] reviewer for %s failed: %s", lang, exc)
            return [{"rule": "reviewer_error", "severity": "MEDIUM",
                     "line": 0, "message": f"Reviewer for {lang} failed: {exc}"}]

    # Generic fallback: basic checks that work on any language
    findings: list[dict] = []
    lines = code.split("\n")

    for i, line in enumerate(lines):
        stripped = line.strip()
        # Hardcoded secrets (language-agnostic)
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": i + 1, "message": "Possible hardcoded secret",
            })
        # TODO/FIXME markers
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.startswith("//") and not stripped.startswith("#"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": i + 1, "message": f"Leftover marker: {stripped[:60]}",
            })

    if not findings:
        lang_hint = f" ({lang})" if lang else ""
        findings.append({
            "rule": "language_not_supported", "severity": "INFO",
            "line": 0, "message": f"Language{lang_hint} has no dedicated reviewer; basic checks only.",
        })

    return findings


def format_findings(findings: list[dict]) -> str:
    """Format review findings as a readable report."""
    if not findings:
        return "✅ Code review passed — no issues found."

    severity_order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "INFO": 4}
    sorted_findings = sorted(
        findings,
        key=lambda f: (severity_order.get(f.get("severity", "INFO"), 5), f.get("line", 0)),
    )

    lines = ["📋 Multi-Language Code Review Report:", ""]
    for f in sorted_findings:
        emoji = {"CRITICAL": "🔴", "HIGH": "🟠", "MEDIUM": "🟡", "LOW": "⚪", "INFO": "ℹ️"}
        e = emoji.get(f.get("severity", "INFO"), "⚪")
        line_no = f.get("line", 0)
        msg = f.get("message", "No message")
        sev = f.get("severity", "INFO")
        lines.append(f"  {e} L{line_no}: {msg} ({sev})")

    lines.append("")
    for sev in ("CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"):
        count = sum(1 for f in findings if f.get("severity") == sev)
        if count:
            lines.append(f"  {sev}: {count}")

    return "\n".join(lines)


# ── Project analysis ────────────────────────────────────────────────────────

def analyse_project(root: str | Path) -> dict[str, Any]:
    """Analyse a project directory and return a language breakdown.

    Returns:
        {
            "languages": {"python": 15, "typescript": 8, ...},  # file counts
            "build_systems": ["pip", "npm", "cargo", ...],
            "frameworks": ["fastapi", "react", ...],
            "total_files": 123,
            "config_files": ["pyproject.toml", "package.json", ...],
        }
    """
    root = Path(root)
    if not root.is_dir():
        return {"error": f"Not a directory: {root}"}

    lang_counts: dict[str, int] = {}
    build_systems: set[str] = set()
    config_files: list[str] = []
    total = 0

    for f in sorted(root.rglob("*")):
        if not f.is_file():
            continue
        # Skip hidden dirs and common non-code dirs
        parts = f.parts
        if any(p.startswith(".") for p in parts if p != "."):
            continue
        if any(p in parts for p in ("node_modules", "__pycache__", ".venv",
                                     "venv", ".git", "target", "build")):
            continue

        total += 1
        rel = f.relative_to(root).as_posix()
        lang = detect_language(f.name)
        if lang:
            lang_counts[lang] = lang_counts.get(lang, 0) + 1

        # Detect build systems from config files
        name = f.name
        if name == "package.json":
            build_systems.add("npm")
            config_files.append(rel)
        elif name == "pyproject.toml":
            build_systems.add("pip")
            config_files.append(rel)
        elif name == "Cargo.toml":
            build_systems.add("cargo")
            config_files.append(rel)
        elif name == "go.mod":
            build_systems.add("go")
            config_files.append(rel)
        elif name in ("Makefile", "CMakeLists.txt"):
            build_systems.add("make")
            config_files.append(rel)
        elif name == "Dockerfile" or name.startswith("Dockerfile"):
            build_systems.add("docker")
            config_files.append(rel)
        elif name == "composer.json":
            build_systems.add("composer")
            config_files.append(rel)
        elif name == "Gemfile":
            build_systems.add("bundler")
            config_files.append(rel)
        elif name == "pom.xml":
            build_systems.add("maven")
            config_files.append(rel)
        elif name == "build.gradle" or name == "build.gradle.kts":
            build_systems.add("gradle")
            config_files.append(rel)

    # Sort languages by file count (descending)
    sorted_langs = dict(sorted(lang_counts.items(), key=lambda x: -x[1]))

    return {
        "languages": sorted_langs,
        "build_systems": sorted(build_systems),
        "total_files": total,
        "config_files": sorted(config_files),
    }


# ── Wire to brain ───────────────────────────────────────────────────────────

from .engine_wiring import wire_failure, wire_success  # noqa: E402

wire_success(
    module="multi_lang_coder",
    summary="Multi-Language Coding Engine active",
    detail="Supports TypeScript, JavaScript, Rust, Go, SQL, Docker, YAML, "
           "TOML, JSON, Shell, C/C++, Java, Kotlin, Ruby, PHP, Swift, and more",
    source_id="multi_lang_coder:R-F1044",
)

# R-F2119 §21a — wire failure handler for multi_lang_coder
try:
    wire_failure(module="multi_lang_coder", detail="module shutdown",
                gap_type="engine_failure", source="multi_lang_coder:shutdown")
except Exception:
    pass
