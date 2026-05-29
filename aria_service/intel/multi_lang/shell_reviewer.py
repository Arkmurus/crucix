"""R-F1044 — Shell (bash/PowerShell) code reviewer.

Pattern-based review for shell scripts.

Checks:
  - Unquoted variables (word splitting risk)
  - Using `eval` (security risk)
  - Using backticks instead of $()
  - Missing `set -e` / `set -o errexit`
  - Missing `set -u` / `set -o nounset`
  - Using `sudo` in scripts
  - Hardcoded secrets
  - TODO/FIXME markers
  - `rm -rf` without safety check
  - `curl ... | bash` pattern (security risk)
  - Using `cd` without error checking
  - PowerShell: Using `Write-Host` instead of `Write-Output`
  - PowerShell: Missing `$ErrorActionPreference`
  - Large files (>500 lines)
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review shell script code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")
    is_powershell = file_path.endswith((".ps1", ".psm1")) if file_path else False
    is_bash = not is_powershell

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1

        # Skip comments
        if stripped.startswith("#") or stripped.startswith("//"):
            continue

        if is_bash:
            # ── Bash checks ──────────────────────────────────────────────

            # Unquoted variables
            if re.search(r'\$\w+', stripped) and '"' not in stripped and "'" not in stripped:
                # Exclude common safe patterns
                if not re.search(r'\$\(', stripped) and not re.search(r'\$\{?\w+\}?=', stripped):
                    if not re.search(r'\b(echo|printf|export|local|readonly)\s', stripped):
                        findings.append({
                            "rule": "unquoted_variable", "severity": "MEDIUM",
                            "line": lineno, "message": "Unquoted variable — may cause word splitting",
                        })

            # eval
            if re.search(r'\beval\b', stripped):
                findings.append({
                    "rule": "eval_usage", "severity": "CRITICAL",
                    "line": lineno, "message": "eval is a security risk — avoid if possible",
                })

            # Backticks
            if re.search(r'`[^`]+`', stripped):
                findings.append({
                    "rule": "backticks", "severity": "LOW",
                    "line": lineno, "message": "Use $() instead of backticks for command substitution",
                })

            # sudo in scripts
            if re.search(r'\bsudo\b', stripped):
                findings.append({
                    "rule": "sudo_in_script", "severity": "MEDIUM",
                    "line": lineno, "message": "sudo in script may fail in non-interactive environments",
                })

            # rm -rf without safety
            if re.search(r'\brm\s+-rf\b', stripped):
                # Check if there's a safety check nearby
                has_safety = False
                for j in range(max(0, i - 5), i):
                    if re.search(r'\b(if|test|\[\[)', lines[j]):
                        has_safety = True
                        break
                if not has_safety:
                    findings.append({
                        "rule": "rm_rf_safety", "severity": "HIGH",
                        "line": lineno, "message": "rm -rf without safety check — consider adding a guard",
                    })

            # curl ... | bash
            if re.search(r'\bcurl\b', stripped) and re.search(r'\|\s*(bash|sh)\b', stripped):
                findings.append({
                    "rule": "curl_pipe_bash", "severity": "CRITICAL",
                    "line": lineno, "message": "Piping curl to bash is a security risk — download and verify first",
                })

            # cd without error check
            if re.search(r'^cd\s+', stripped) and not re.search(r'^cd\s+\S+\s*&&', stripped) and not re.search(r'^cd\s+\S+\s*$', stripped):
                # Check if next line checks $?
                if i + 1 < len(lines) and not re.search(r'\$\?|\|\|', lines[i + 1]):
                    findings.append({
                        "rule": "cd_no_check", "severity": "MEDIUM",
                        "line": lineno, "message": "cd without error checking — script continues in wrong directory if cd fails",
                    })

        if is_powershell:
            # ── PowerShell checks ────────────────────────────────────────

            # Write-Host (should use Write-Output)
            if re.search(r'\bWrite-Host\b', stripped):
                findings.append({
                    "rule": "write_host", "severity": "LOW",
                    "line": lineno, "message": "Write-Host bypasses the output stream — use Write-Output",
                })

            # Missing $ErrorActionPreference
            if i == 0 and not re.search(r'\$ErrorActionPreference', stripped):
                if "ErrorActionPreference" not in code[:500]:
                    findings.append({
                        "rule": "missing_error_action", "severity": "MEDIUM",
                        "line": lineno, "message": "Missing $ErrorActionPreference — errors may be silently ignored",
                    })

            # Invoke-Expression (eval equivalent)
            if re.search(r'\bInvoke-Expression\b', stripped):
                findings.append({
                    "rule": "invoke_expression", "severity": "CRITICAL",
                    "line": lineno, "message": "Invoke-Expression is a security risk — avoid if possible",
                })

        # ── Common checks ────────────────────────────────────────────────

        # Hardcoded secrets
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret",
            })

        # TODO/FIXME markers
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.startswith("#"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    if is_bash:
        # Missing set -e
        if "set -e" not in code and "set -o errexit" not in code and "set -o pipefail" not in code:
            findings.append({
                "rule": "missing_set_e", "severity": "MEDIUM",
                "line": 0, "message": "Missing 'set -e' — script continues after errors",
            })

        # Missing set -u
        if "set -u" not in code and "set -o nounset" not in code:
            findings.append({
                "rule": "missing_set_u", "severity": "MEDIUM",
                "line": 0, "message": "Missing 'set -u' — unset variables are silently ignored",
            })

    # Large file
    if len(lines) > 500:
        findings.append({
            "rule": "large_file", "severity": "LOW",
            "line": 0, "message": f"File is {len(lines)} lines — consider splitting",
        })

    return findings
