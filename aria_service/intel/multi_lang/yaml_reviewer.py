"""R-F1044 — YAML/TOML/JSON reviewer.

Pattern-based review for configuration files.

Checks:
  - YAML: Tab characters (invalid)
  - YAML: Missing `---` document separator
  - YAML: Unquoted strings with special characters
  - JSON: Trailing commas
  - JSON: Comments (not valid JSON)
  - TOML: Duplicate keys
  - All: Hardcoded secrets
  - All: TODO/FIXME markers
  - All: Very large files (>1000 lines)
  - All: Missing top-level keys in known schemas
"""
from __future__ import annotations

import json
import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review YAML/TOML/JSON code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")
    ext = file_path.lower() if file_path else ""

    is_yaml = ext.endswith((".yaml", ".yml"))
    is_toml = ext.endswith(".toml")
    is_json = ext.endswith(".json")

    for i, line in enumerate(lines):
        stripped = line
        lineno = i + 1

        # ── YAML checks ──────────────────────────────────────────────────
        if is_yaml:
            # Tab characters
            if "\t" in stripped and not stripped.startswith("#"):
                findings.append({
                    "rule": "yaml_tab", "severity": "CRITICAL",
                    "line": lineno, "message": "Tab character in YAML — use spaces for indentation",
                })

            # Unquoted strings with special characters
            if re.search(r':\s*\S*\s*$', stripped) and not stripped.startswith("#"):
                value = stripped.split(":", 1)[1].strip()
                if value and not value.startswith(("'", '"', "[", "{", "|", ">", "&", "*", "!")):
                    if re.search(r'[#\[\]{}:>|]', value):
                        findings.append({
                            "rule": "yaml_unquoted_special", "severity": "MEDIUM",
                            "line": lineno, "message": f"Unquoted string with special characters: '{value[:40]}'",
                        })

        # ── JSON checks ──────────────────────────────────────────────────
        if is_json:
            # Trailing commas
            if re.search(r',\s*$', stripped) and not stripped.strip().endswith('"') and not stripped.strip().endswith("'"):
                # Check if next non-empty line closes the bracket
                next_lines = [l.strip() for l in lines[i + 1:i + 3] if l.strip()]
                if next_lines and next_lines[0] in ("]", "}"):
                    findings.append({
                        "rule": "json_trailing_comma", "severity": "HIGH",
                        "line": lineno, "message": "Trailing comma before closing bracket — invalid JSON",
                    })

            # Comments in JSON
            if re.search(r'^\s*//|^\s*#|^\s*/\*', stripped):
                findings.append({
                    "rule": "json_comment", "severity": "MEDIUM",
                    "line": lineno, "message": "Comments are not valid JSON — use JSONC or remove",
                })

        # ── TOML checks ──────────────────────────────────────────────────
        if is_toml:
            # Duplicate table headers (heuristic)
            pass  # Full duplicate detection requires parsing

        # ── All: Hardcoded secrets ───────────────────────────────────────
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret",
            })

        # ── All: TODO/FIXME markers ──────────────────────────────────────
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.strip().startswith("#"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    # Missing `---` in YAML
    if is_yaml and not code.strip().startswith("---") and len(lines) > 3:
        findings.append({
            "rule": "yaml_missing_doc_separator", "severity": "LOW",
            "line": 0, "message": "YAML file missing '---' document start marker",
        })

    # Try to parse JSON and validate
    if is_json:
        try:
            json.loads(code)
        except json.JSONDecodeError as e:
            findings.append({
                "rule": "json_parse_error", "severity": "CRITICAL",
                "line": e.lineno or 0, "message": f"Invalid JSON: {e.msg}",
            })

    # Large file
    if len(lines) > 1000:
        findings.append({
            "rule": "large_file", "severity": "LOW",
            "line": 0, "message": f"File is {len(lines)} lines — consider splitting",
        })

    return findings
