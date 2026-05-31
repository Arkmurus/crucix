#!/usr/bin/env python3
"""R-F1215/R-F1222: Structural guard — wiring audit + file-size CI check.

Ensures:
1. No substantial module (>100 lines) in aria_service/ is completely dark
   (zero brain hook, zero wire_success/wire_failure, zero metrics,
   zero mistake_ledger, zero capability_gaps).
2. No file exceeds MAX_LINES (2000) — prevents monolithic files that hide
   bugs and are hard to test/review.
3. No module has wire_success without wire_failure (one-sided wiring).

This is a CI gate, not a linter. It fails the build if a new violation
is added or an existing one crosses the threshold without wiring.

Usage:
    python scripts/check_wiring.py          # check all modules
    python scripts/check_wiring.py --ci     # exit 1 on any violation

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
    
    MAX_LINES = 2000  # R-F1222: no file should exceed this
    dark_modules: list[tuple[str, int, list[str]]] = []
    onesided_modules: list[tuple[str, int]] = []
    oversized_files: list[tuple[int, str]] = []
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
        
        # R-F1222: flag files over MAX_LINES
        if lines > MAX_LINES:
            oversized_files.append((lines, rel_path))
        
        # R-F1221: flag modules with wire_success but no wire_failure
        try:
            text = filepath.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        has_success = "wire_success" in text
        has_failure = "wire_failure" in text
        has_wired = "@wired" in text or ("from .engine_wiring import" in text and "wired" in text)
        if has_success and not has_failure and not has_wired:
            onesided_modules.append((rel_path, lines))
    
    print(f"\n=== Wiring Audit (R-F1215/R-F1221/R-F1222) ===")
    print(f"Modules checked: {total_checked}")
    print(f"Dark modules (0 wiring tokens): {total_dark}")
    print(f"One-sided modules (wire_success but no wire_failure): {len(onesided_modules)}")
    print(f"Oversized files (>{MAX_LINES} lines): {len(oversized_files)}")
    
    if dark_modules:
        print(f"\n--- DARK MODULES (no brain wiring) ---")
        for rel_path, lines, missing in sorted(dark_modules, key=lambda x: -x[1]):
            print(f"  {lines:>5}L  {rel_path}")
            print(f"         missing: {', '.join(missing)}")
    
    if onesided_modules:
        print(f"\n--- ONE-SIDED MODULES (wire_success but no wire_failure) ---")
        print(f"  Use @wired decorator or add wire_failure calls.")
        for rel_path, lines in sorted(onesided_modules, key=lambda x: -x[1])[:20]:
            print(f"  {lines:>5}L  {rel_path}")
    
    if oversized_files:
        print(f"\n--- OVERSIZED FILES (>{MAX_LINES} lines — refactor recommended) ---")
        for lines, rel_path in sorted(oversized_files, reverse=True)[:20]:
            print(f"  {lines:>6}L  {rel_path}")
    
    if ci_mode and dark_modules:
        print(f"\n[FAIL] {total_dark} dark modules found — wire them to the brain before shipping.")
        return 1
    
    if ci_mode and onesided_modules:
        print(f"\n[FAIL] {len(onesided_modules)} one-sided modules found — add wire_failure or use @wired.")
        return 1
    
    if ci_mode and oversized_files:
        print(f"\n[FAIL] {len(oversized_files)} oversized files found — refactor to under {MAX_LINES} lines.")
        return 1
    
    if dark_modules:
        print(f"\n[WARN] {total_dark} dark modules exist — not blocking (use --ci to enforce).")
    elif onesided_modules:
        print(f"\n[WARN] {len(onesided_modules)} one-sided modules exist — not blocking (use --ci to enforce).")
    else:
        print(f"\n[PASS] All modules have brain wiring.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
