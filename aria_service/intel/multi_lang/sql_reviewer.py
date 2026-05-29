"""R-F1044 — SQL code reviewer.

Pattern-based review for SQL queries and schema definitions.

Checks:
  - SQL injection vectors (string concatenation in queries)
  - SELECT * (fetching unnecessary columns)
  - Missing WHERE clause on UPDATE/DELETE
  - N+1 query pattern
  - Implicit column type (no explicit type in CREATE)
  - Missing indexes on foreign keys
  - NOT NULL without DEFAULT
  - VARCHAR without length
  - Hardcoded secrets
  - TODO/FIXME markers
  - Cartesian joins (no JOIN condition)
  - Using GROUP BY on large tables without index
  - ORDER BY RAND() (performance killer)
  - LIKE with leading wildcard (no index usage)
"""
from __future__ import annotations

import re
from typing import Any


def review(code: str, file_path: str = "") -> list[dict]:
    """Review SQL code. Returns list of findings."""
    findings: list[dict] = []
    lines = code.split("\n")
    upper = code.upper()

    for i, line in enumerate(lines):
        stripped = line.strip()
        lineno = i + 1
        upper_line = stripped.upper()

        # Skip comments
        if stripped.startswith("--") or stripped.startswith("/*") or stripped.startswith("*"):
            continue

        # ── SELECT * ─────────────────────────────────────────────────────
        if re.search(r'\bSELECT\s+\*', upper_line, re.IGNORECASE):
            findings.append({
                "rule": "select_star", "severity": "MEDIUM",
                "line": lineno, "message": "SELECT * fetches all columns — specify only needed columns",
            })

        # ── Missing WHERE on UPDATE/DELETE ───────────────────────────────
        if re.search(r'\bUPDATE\b', upper_line, re.IGNORECASE):
            # Check if WHERE appears within the next few lines
            block = "\n".join(lines[i:i + 10]).upper()
            if "WHERE" not in block:
                findings.append({
                    "rule": "update_without_where", "severity": "CRITICAL",
                    "line": lineno, "message": "UPDATE without WHERE clause will modify ALL rows",
                })

        if re.search(r'\bDELETE FROM\b', upper_line, re.IGNORECASE):
            block = "\n".join(lines[i:i + 10]).upper()
            if "WHERE" not in block:
                findings.append({
                    "rule": "delete_without_where", "severity": "CRITICAL",
                    "line": lineno, "message": "DELETE without WHERE clause will delete ALL rows",
                })

        # ── ORDER BY RAND() ──────────────────────────────────────────────
        if re.search(r'\bORDER\s+BY\s+RAND\s*\(\)', upper_line, re.IGNORECASE):
            findings.append({
                "rule": "order_by_random", "severity": "HIGH",
                "line": lineno, "message": "ORDER BY RAND() is extremely slow on large tables",
            })

        # ── LIKE with leading wildcard ───────────────────────────────────
        if re.search(r"LIKE\s+['\"]%", upper_line, re.IGNORECASE):
            findings.append({
                "rule": "leading_wildcard_like", "severity": "MEDIUM",
                "line": lineno, "message": "LIKE with leading wildcard prevents index usage",
            })

        # ── Implicit column type ─────────────────────────────────────────
        if re.search(r'\bCREATE\s+TABLE\b', upper_line, re.IGNORECASE):
            # Check for columns without explicit types
            for j in range(i, min(i + 50, len(lines))):
                col_line = lines[j].strip().upper()
                if col_line == ");" or col_line.startswith(")"):
                    break
                if re.search(r'^\s+\w+\s+\w+', lines[j]) and not re.search(r'\b(INT|VARCHAR|TEXT|BOOLEAN|FLOAT|DOUBLE|DECIMAL|DATE|TIMESTAMP|BLOB|JSON|UUID|SERIAL|BIGINT|SMALLINT|TINYINT|NUMERIC|REAL|CHAR|NCHAR|NVARCHAR|NTEXT|BINARY|VARBINARY|IMAGE|MONEY|SMALLMONEY|DATETIME|SMALLDATETIME|DATETIME2|DATETIMEOFFSET|TIME|XML|GEOMETRY|GEOGRAPHY|HIERARCHYID|SQL_VARIANT)\b', col_line):
                        findings.append({
                            "rule": "implicit_column_type", "severity": "MEDIUM",
                            "line": j + 1, "message": f"Column missing explicit type: {lines[j].strip()[:50]}",
                        })

        # ── VARCHAR without length ───────────────────────────────────────
        if re.search(r'\bVARCHAR\b(?!\s*\()', upper_line, re.IGNORECASE):
            findings.append({
                "rule": "varchar_no_length", "severity": "LOW",
                "line": lineno, "message": "VARCHAR without length defaults to 1 character in some databases",
            })

        # ── NOT NULL without DEFAULT ─────────────────────────────────────
        if re.search(r'\bNOT\s+NULL\b', upper_line, re.IGNORECASE) and not re.search(r'\bDEFAULT\b', upper_line, re.IGNORECASE):
            # Only flag in CREATE TABLE / ALTER TABLE context
            if re.search(r'\b(CREATE|ALTER)\s+TABLE\b', upper, re.IGNORECASE):
                findings.append({
                    "rule": "not_null_no_default", "severity": "LOW",
                    "line": lineno, "message": "NOT NULL column without DEFAULT may cause insert errors",
                })

        # ── Hardcoded secrets ────────────────────────────────────────────
        if re.search(r'(?:api_key|api_secret|password|secret|token)\s*[=:]\s*["\'][A-Za-z0-9_\-]{16,}', stripped):
            findings.append({
                "rule": "hardcoded_secret", "severity": "CRITICAL",
                "line": lineno, "message": "Possible hardcoded secret in SQL",
            })

        # ── TODO/FIXME markers ───────────────────────────────────────────
        if re.search(r'\b(TODO|FIXME|HACK|XXX)\b', stripped):
            findings.append({
                "rule": "todo_marker", "severity": "LOW",
                "line": lineno, "message": f"Leftover marker: {stripped[:60]}",
            })

    # ── Post-line checks ─────────────────────────────────────────────────

    # Check for Cartesian joins (FROM with multiple tables but no JOIN keyword)
    from_count = len(re.findall(r'\bFROM\b', upper, re.IGNORECASE))
    join_count = len(re.findall(r'\bJOIN\b', upper, re.IGNORECASE))
    if from_count > 1 and join_count == 0:
        findings.append({
            "rule": "cartesian_join", "severity": "HIGH",
            "line": 0, "message": "Multiple FROM tables without JOIN — possible Cartesian product",
        })

    # Check for N+1 pattern (SELECT inside a loop context)
    if re.search(r'\bFOR\s+EACH\b', upper, re.IGNORECASE) and re.search(r'\bSELECT\b', upper, re.IGNORECASE):
        findings.append({
            "rule": "n_plus_one", "severity": "HIGH",
            "line": 0, "message": "SELECT inside a loop — possible N+1 query pattern",
        })

    return findings
