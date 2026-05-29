"""R-F1044 — Multi-language code reviewers.

Each module exposes a `review(code: str, file_path: str = "") -> list[dict]`
function that returns findings in the standard format:
    {"rule": str, "severity": str, "line": int, "message": str}

Severity levels: CRITICAL, HIGH, MEDIUM, LOW, INFO.
"""
