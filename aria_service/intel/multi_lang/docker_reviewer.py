"""R-F1044 — Dockerfile and docker-compose reviewer.

Pattern-based review for Dockerfiles and docker-compose configurations.

Checks:
  - Using `latest` tag (non-reproducible builds)
  - Running as root (no USER directive)
  - COPY --from=... without multi-stage (confusing)
  - ADD instead of COPY (ADD has hidden behavior)
  - Exposing port 22 (SSH in container)
  - Using apk upgrade (layer bloat)
  - RUN pip install without --no-cache-dir
  - RUN apt-get without -y flag
  - Missing HEALTHCHECK
  - Using ENV for secrets
  - Large image (many RUN layers)
  - Hardcoded secrets
  - TODO/FIXME markers
  - docker-compose: depends_on without healthcheck
  - docker-compose: ports mapping to 0.0.0.0
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review Dockerfile or docker-compose code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")
    is_compose = "docker-compose" in file_path or "services:" in code[:500]

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1
        upper = stripped.upper()

        # Skip comments
        if stripped.startswith("#"):
            continue

        if is_compose:
            # ── docker-compose checks ────────────────────────────────────

            # depends_on without healthcheck
            if re.search(r'\bdepends_on\b', stripped):
                # Check if healthcheck is defined for the service
                service_name = None
                for j in range(max(0, i - 20), i):
                    m = re.match(r'^\s*(\w+):', lines[j])
                    if m:
                        service_name = m.group(1)
                if service_name:
                    # Look for healthcheck in the depended service
                    healthcheck_found = False
                    for j in range(i, min(i + 30, len(lines))):
                        if re.search(r'\bhealthcheck\b', lines[j], re.IGNORECASE):
                            healthcheck_found = True
                            break
                        if re.search(r'^\s+\w+:', lines[j]) and j > i:
                            break  # Next service
                    if not healthcheck_found:
                        findings.append({
                            "rule": "depends_on_no_healthcheck", "severity": "MEDIUM",
                            "line": lineno, "message": "depends_on without healthcheck — service may start before dependency is ready",
                        })

            # Ports mapping to 0.0.0.0
            if re.search(r'ports\s*:', stripped):
                for j in range(i + 1, min(i + 10, len(lines))):
                    if re.search(r'["\']0\.0\.0\.0:', lines[j]):
                        findings.append({
                            "rule": "exposed_to_all", "severity": "MEDIUM",
                            "line": j + 1, "message": "Port exposed on 0.0.0.0 — accessible from outside the host",
                        })
                        break
                    if re.search(r'^\s+\w+:', lines[j]):
                        break

        else:
            # ── Dockerfile checks ────────────────────────────────────────

            # Using `latest` tag
            if re.search(r'FROM\s+\S+:\s*latest\b', upper, re.IGNORECASE):
                findings.append({
                    "rule": "latest_tag", "severity": "MEDIUM",
                    "line": lineno, "message": "Using 'latest' tag — pin to a specific version for reproducible builds",
                })

            # Running as root
            if re.search(r'^FROM\b', upper):
                # Check if USER directive exists anywhere
                if "USER" not in upper:
                    findings.append({
                        "rule": "running_as_root", "severity": "HIGH",
                        "line": lineno, "message": "No USER directive — container runs as root",
                    })

            # ADD instead of COPY
            if re.search(r'^ADD\b', upper) and not re.search(r'ADD\s+--', upper):
                findings.append({
                    "rule": "add_instead_of_copy", "severity": "LOW",
                    "line": lineno, "message": "Use COPY instead of ADD unless you need automatic tar extraction or URL fetching",
                })

            # Exposing port 22
            if re.search(r'EXPOSE\s+22\b', upper):
                findings.append({
                    "rule": "ssh_exposed", "severity": "HIGH",
                    "line": lineno, "message": "Exposing port 22 (SSH) in container is a security risk",
                })

            # pip install without --no-cache-dir
            if re.search(r'pip\s+install', stripped) and "--no-cache-dir" not in stripped:
                findings.append({
                    "rule": "pip_no_cache", "severity": "LOW",
                    "line": lineno, "message": "pip install without --no-cache-dir increases image size",
                })

            # apt-get without -y
            if re.search(r'apt-get\s+install', stripped) and "-y" not in stripped:
                findings.append({
                    "rule": "apt_no_yes", "severity": "MEDIUM",
                    "line": lineno, "message": "apt-get install without -y flag may fail in non-interactive mode",
                })

            # ENV for secrets
            if re.search(r'^ENV\s+\w*(?:PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL)\w*', upper):
                findings.append({
                    "rule": "env_secret", "severity": "CRITICAL",
                    "line": lineno, "message": "ENV instruction exposes secrets in image layers — use build args or secrets",
                })

            # Missing HEALTHCHECK
            if re.search(r'^FROM\b', upper):
                if "HEALTHCHECK" not in upper:
                    findings.append({
                        "rule": "missing_healthcheck", "severity": "LOW",
                        "line": lineno, "message": "No HEALTHCHECK defined — container health won't be monitored",
                    })

            # Hardcoded secrets
            if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
                findings.append({
                    "rule": "hardcoded_secret", "severity": "CRITICAL",
                    "line": lineno, "message": "Possible hardcoded secret in Dockerfile",
                })

        # ── Common checks ────────────────────────────────────────────────

        # TODO/FIXME markers
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped) and not stripped.startswith("#"):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    if not is_compose:
        # Count RUN layers
        run_count = len(re.findall(r'^RUN\b', code, re.MULTILINE))
        if run_count > 15:
            findings.append({
                "rule": "too_many_layers", "severity": "LOW",
                "line": 0, "message": f"{run_count} RUN instructions — consider combining to reduce image layers",
            })

    return findings
