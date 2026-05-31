#!/usr/bin/env python3
"""R-F1215: Structural guard — wiring audit CI check.

Ensures no substantial module (>100 lines) in aria_service/ is completely
dark (zero brain hook, zero wire_success/wire_failure, zero metrics,
zero mistake_ledger, zero capability_gaps).

This is a CI gate, not a linter. It fails the build if a new dark module
is added or an existing one crosses the threshold without wiring.

Usage:
    python scripts/check_wiring.py          # check all modules
    python scripts/check_wiring.py --ci     # exit 1 on any dark module

Exemptions (explicitly listed):
- __init__.py files
- config.py
- test_* files
- scripts/ and data/ directories
- aria_cli/ (CLI tool — less critical)
"""
import ast
import sys
import re
from pathlib import Path

ROOT = Path(__file__).parent.parent

# Modules that are explicitly exempt from wiring requirements
EXEMPT_MODULES: set[str] = {
    # CLI tools — not part of the brain ecosystem
    "aria_cli",
    # Config and boot
    "aria_service/config.py",
    # Test files
}

# Thresholds
MIN_LINES_FOR_WIRING = 100
WIRING_TOKENS = {
    "brain_hook",
    "wire_success",
    "wire_failure",
    "brain_signal",
    "mistake_ledger",
    "capability_gaps",
    "record_gap",
}


def check_module(filepath: Path) -> tuple[str, int, int, list[str]]:
    """Check a single module for brain wiring.
    
    Returns: (relative_path, line_count, wiring_token_count, missing_tokens)
    """
    rel = str(filepath.relative_to(ROOT)).replace("\\", "/")
    
    # Skip exempt paths
    for exempt in EXEMPT_MODULES:
        if rel.startswith(exempt):
            return (rel, 0, 99, [])
    
    # Skip test files, __init__.py, config
    if rel.startswith("test_") or "/test_" in rel:
        return (rel, 0, 99, [])
    if filepath.name == "__init__.py":
        return (rel, 0, 99, [])
    if filepath.name == "config.py":
        return (rel, 0, 99, [])
    if rel.startswith("scripts/") or rel.startswith("data/"):
        return (rel, 0, 99, [])
    
    try:
        text = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return (rel, 0, 99, [])
    
    lines = len(text.splitlines())
    if lines < MIN_LINES_FOR_WIRING:
        return (rel, lines, 99, [])
    
    found_tokens = []
    missing_tokens = []
    for token in WIRING_TOKENS:
        if token in text:
            found_tokens.append(token)
        else:
            missing_tokens.append(token)
    
    return (rel, lines, len(found_tokens), missing_tokens)


def main() -> int:
    ci_mode = "--ci" in sys.argv
    
    dark_modules: list[tuple[str, int, list[str]]] = []
    total_checked = 0
    total_dark = 0
    
    for filepath in sorted(ROOT.rglob("*.py")):
        rel = str(filepath.relative_to(ROOT))
        if ".venv" in rel or "__pycache__" in rel or ".git" in rel:
            continue
        
        rel_path, lines, token_count, missing = check_module(filepath)
        if token_count == 99:  # exempt or too small
            continue
        
        total_checked += 1
        if token_count == 0:
            dark_modules.append((rel_path, lines, missing))
            total_dark += 1
    
    print(f"\n=== Wiring Audit (R-F1215) ===")
    print(f"Modules checked: {total_checked}")
    print(f"Dark modules (0 wiring tokens): {total_dark}")
    
    if dark_modules:
        print(f"\n--- DARK MODULES (no brain wiring) ---")
        for rel_path, lines, missing in sorted(dark_modules, key=lambda x: -x[1]):
            print(f"  {lines:>5}L  {rel_path}")
            print(f"         missing: {', '.join(missing)}")
    
    if ci_mode and dark_modules:
        print(f"\n[FAIL] {total_dark} dark modules found — wire them to the brain before shipping.")
        return 1
    
    if dark_modules:
        print(f"\n[WARN] {total_dark} dark modules exist — not blocking (use --ci to enforce).")
    else:
        print(f"\n[PASS] All modules have brain wiring.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
