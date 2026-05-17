"""R-F672 — promote silent except:pass in main.py lifespan to logger.

Audit 2026-05-17 closing-session report flagged:

  "Promote 9 silent except: pass in lifespan to logger.warning(...) so
   degradation surfaces."

Three were actually in the lifespan body (boot-snapshot lpush/ltrim,
brain_hook regression-absorb, WhatsApp watchlist notification). The
others were in non-lifespan handlers. All three lifespan ones are now
either logger.warning (real failure) or logger.debug (by-design quiet
case) — but never silent.

Pin tests assert the R-F672 marker is present and no silent
`except Exception: pass` block remains inside the lifespan body.
"""
from __future__ import annotations

import re
from pathlib import Path


_SRC = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")


def _lifespan_body() -> str:
    m = re.search(r"async def lifespan\(", _SRC)
    assert m, "main.py lifespan() not found"
    start = m.start()
    rest = _SRC[m.end():]
    m2 = re.search(r"\nasync def \w+\(|\ndef \w+\(|\nclass \w+", rest)
    end = m.end() + (m2.start() if m2 else len(rest))
    return _SRC[start:end]


def test_rf672_marker_present():
    assert "R-F672" in _SRC, "R-F672 marker missing"


def test_rf672_no_silent_except_pass_in_lifespan():
    """Within the lifespan function, no `except Exception: pass` block
    may remain (with or without a trailing comment)."""
    body = _lifespan_body()
    pattern = re.compile(
        r"except\s+Exception(?:\s+as\s+\w+)?:\s*\n\s+pass\b",
    )
    offenders = list(pattern.finditer(body))
    if offenders:
        # Print line offsets relative to file for actionable failure
        lines = [_SRC[: _SRC.find(body) + m.start()].count("\n") + 1 for m in offenders]
        assert False, (
            f"R-F672: {len(offenders)} silent except:pass remain in lifespan: "
            f"lines {lines}"
        )


def test_rf672_boot_snapshot_failure_logs_warning():
    """The specific boot-snapshot failure path must call logger.warning
    with the R-F672 tag so a fly-log grep finds it."""
    body = _lifespan_body()
    assert "R-F672: boot snapshot persistence failed" in body, (
        "R-F672 regression: boot-snapshot failure no longer logged"
    )


def test_rf672_boot_diff_absorb_failure_logs_warning():
    body = _lifespan_body()
    assert "R-F672: brain_hook.absorb for boot_state" in body
