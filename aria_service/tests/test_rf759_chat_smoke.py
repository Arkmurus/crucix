"""R-F759 (2026-05-20) — chat smoke harness tests.

Validates the scripts/admin/chat_smoke.py harness:
  - probe matrix shape (label, grade, status, time, note)
  - grade thresholds (PASS / WARN / FAIL)
  - exit-code mapping (0 = all PASS, 1 = any FAIL, 2 = WARN no FAIL)
  - R-F738 marker check on frontend body
  - JSON output mode

Mocks `_http` so no live network calls in the unit suite.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

# Load the script as a module (it lives outside the package tree).
_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "admin" / "chat_smoke.py"
_spec = importlib.util.spec_from_file_location("chat_smoke", _SCRIPT)
chat_smoke = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(chat_smoke)


def _stub_http_factory(responses: dict):
    """Build a fake `_http` that returns (status, body, elapsed) per URL.
    `responses` maps URL suffix → (status, body_bytes, elapsed_s)."""
    def _fake(method, url, *, token=None, body=None, timeout=30.0):
        for suffix, ret in responses.items():
            if suffix in url:
                return ret
        return (0, b"", 0.0)
    return _fake


def test_grade_thresholds():
    """R-F759: grade fn enforces (warn_at, fail_at) per probe key."""
    g = chat_smoke._grade
    # health_live thresholds: warn=2.0, fail=8.0
    assert g(0.1, 200, 200, "health_live") == "PASS"
    assert g(3.0, 200, 200, "health_live") == "WARN"
    assert g(9.0, 200, 200, "health_live") == "FAIL"
    # Wrong status → FAIL regardless of speed
    assert g(0.1, 500, 200, "health_live") == "FAIL"


def test_grade_health_heavy_thresholds():
    """R-F759: heavyweight /health gets a longer budget."""
    g = chat_smoke._grade
    assert g(4.0, 200, 200, "health_heavy") == "PASS"
    assert g(10.0, 200, 200, "health_heavy") == "WARN"
    assert g(20.0, 200, 200, "health_heavy") == "FAIL"


def test_exit_code_all_pass():
    """R-F759: 0 = all PASS."""
    rows = [{"grade": "PASS"}, {"grade": "PASS"}]
    assert chat_smoke._exit_code(rows) == 0


def test_exit_code_warn_no_fail():
    """R-F759: 2 = at least one WARN, no FAIL."""
    rows = [{"grade": "PASS"}, {"grade": "WARN"}]
    assert chat_smoke._exit_code(rows) == 2


def test_exit_code_any_fail():
    """R-F759: 1 = at least one FAIL (overrides WARN)."""
    rows = [{"grade": "WARN"}, {"grade": "FAIL"}, {"grade": "PASS"}]
    assert chat_smoke._exit_code(rows) == 1


def test_run_smoke_all_pass(monkeypatch):
    """R-F759 happy path: all 5 probes hit, all return expected, all grade PASS."""
    body_with_marker = (
        b'<html>... ' + chat_smoke.R_F738_MARKER.encode("utf-8") + b' ...</html>'
    )
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'{"ok":true}', 0.15),
        "/api/aria/health":         (200, b'{"ok":true}', 1.5),
        "/api/aria/chat/stream":    (401, b'unauth',      0.8),
        "/api/aria/operating-mode": (401, b'unauth',      0.5),
        "/aria.html":               (200, body_with_marker, 1.2),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rows = chat_smoke.run_smoke("https://b", "https://f")
    assert len(rows) == 5
    grades = [r["grade"] for r in rows]
    assert all(g == "PASS" for g in grades), f"Unexpected non-PASS grades: {rows}"


def test_run_smoke_heavy_health_wedge_warns(monkeypatch):
    """R-F759: when /health takes 7s (between warn=5 and fail=15), grade is WARN
    + note mentions wedge_675."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (200, b'', 7.0),  # WARN window
        "/api/aria/chat/stream":    (401, b'', 0.8),
        "/api/aria/operating-mode": (401, b'', 0.5),
        "/aria.html":               (200, chat_smoke.R_F738_MARKER.encode("utf-8"), 1.0),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rows = chat_smoke.run_smoke("https://b", "https://f")
    heavy_row = next(r for r in rows if "heavy" in r["label"])
    assert heavy_row["grade"] == "WARN"
    assert "wedge_675" in heavy_row["note"]


def test_run_smoke_heavy_health_timeout_fails(monkeypatch):
    """R-F759: /health timing out (status 999 / 0) grades FAIL."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (0, b'', 20.0),  # connection error
        "/api/aria/chat/stream":    (401, b'', 0.8),
        "/api/aria/operating-mode": (401, b'', 0.5),
        "/aria.html":               (200, chat_smoke.R_F738_MARKER.encode("utf-8"), 1.0),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rows = chat_smoke.run_smoke("https://b", "https://f")
    heavy_row = next(r for r in rows if "heavy" in r["label"])
    assert heavy_row["grade"] == "FAIL"


def test_run_smoke_missing_rf738_marker_warns(monkeypatch):
    """R-F759: aria.html returns 200 but without the R-F738 marker →
    grade downgraded to WARN with 'seenode may be stale' note."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (200, b'', 1.0),
        "/api/aria/chat/stream":    (401, b'', 0.8),
        "/api/aria/operating-mode": (401, b'', 0.5),
        # Body is missing the R-F738 marker — stale frontend
        "/aria.html":               (200, b'<html>...Drop files here...</html>', 1.0),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rows = chat_smoke.run_smoke("https://b", "https://f")
    aria_row = next(r for r in rows if "aria.html" in r["label"])
    assert aria_row["grade"] == "WARN"
    assert "MISSING" in aria_row["note"] or "stale" in aria_row["note"]


def test_run_smoke_with_token_expects_200(monkeypatch):
    """R-F759: --token flips auth-gated probes from expecting 401 to 200."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (200, b'', 1.5),
        "/api/aria/chat/stream":    (200, b'data: ok', 1.0),
        "/api/aria/operating-mode": (200, b'{"mode":"ok"}', 0.4),
        "/aria.html":               (200, chat_smoke.R_F738_MARKER.encode("utf-8"), 1.2),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rows = chat_smoke.run_smoke("https://b", "https://f", token="fake-jwt")
    assert all(r["grade"] == "PASS" for r in rows), f"With token: {rows}"


def test_main_json_output(monkeypatch, capsys):
    """R-F759: --json flag emits machine-parsable JSON."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (200, b'', 1.0),
        "/api/aria/chat/stream":    (401, b'', 0.8),
        "/api/aria/operating-mode": (401, b'', 0.5),
        "/aria.html":               (200, chat_smoke.R_F738_MARKER.encode("utf-8"), 1.0),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rc = chat_smoke.main(["--json"])
    assert rc == 0
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "rows" in parsed
    assert parsed["exit_code"] == 0
    assert len(parsed["rows"]) == 5


def test_main_returns_fail_exit_when_health_dies(monkeypatch):
    """R-F759: exit code 1 when the heavyweight health timeouts."""
    fake = _stub_http_factory({
        "/api/aria/health/live":    (200, b'', 0.15),
        "/api/aria/health":         (0, b'', 25.0),  # timeout / network error
        "/api/aria/chat/stream":    (401, b'', 0.8),
        "/api/aria/operating-mode": (401, b'', 0.5),
        "/aria.html":               (200, chat_smoke.R_F738_MARKER.encode("utf-8"), 1.0),
    })
    monkeypatch.setattr(chat_smoke, "_http", fake)
    rc = chat_smoke.main(["--json"])
    assert rc == 1


def test_rf759_marker_present_in_script():
    """R-F759: marker in the script for revert-detection + traceability."""
    src = _SCRIPT.read_text(encoding="utf-8")
    assert "R-F759" in src
    assert "R-F738 marker" in src or "R_F738_MARKER" in src
