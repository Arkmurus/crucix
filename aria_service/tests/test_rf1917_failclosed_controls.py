"""R-F1917 (G6 vaccine) — security controls must not silently fail OPEN, and the
zip-bomb decompress bound must stay proportional to the ingress cap.

- The content-scan decompress ceiling was 500MB vs a 50MB ingress, so a ~40MB
  DOCX could inflate to ~400MB and OOM the single-process brain while still
  "passing" the bomb check. Now <=100MB, env-tunable.
- The upload scan handler failed OPEN on any scanner exception — so inducing an
  exception (e.g. a bomb crashing the scanner) bypassed the DoS guard entirely.
  Now fails CLOSED for large inputs (the plausible-bomb case).
"""
from __future__ import annotations

import importlib
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_decompress_bound_is_proportional_to_ingress(monkeypatch):
    monkeypatch.delenv("ARIA_MAX_DECOMPRESSED_MB", raising=False)
    import aria_service.intel.content_scanner as cs
    importlib.reload(cs)
    try:
        assert cs.MAX_DECOMPRESSED_SIZE <= 100 * 1024 * 1024, \
            "decompress bound must be <=100MB (was 500MB — zip-bomb blast radius)"
        # the bound is actually enforced in the scan path
        src = (REPO / "aria_service" / "intel" / "content_scanner.py").read_text(
            encoding="utf-8", errors="ignore")
        assert "total_decompressed > MAX_DECOMPRESSED_SIZE" in src
    finally:
        importlib.reload(cs)


def test_upload_scan_fails_closed_on_large_input_sourcepin():
    src = (REPO / "aria_service" / "routes" / "aria.py").read_text(encoding="utf-8", errors="ignore")
    # the scanner-exception handler must reject large inputs (422) rather than
    # silently failing open for everything
    assert "ARIA_SCAN_FAILCLOSE_BYTES" in src
    assert "failing CLOSED (possible bomb)" in src
    assert 'detail="document blocked: content scan could not verify a large upload"' in src
