"""R-F2448 — regression guards for three latent boot-path defects in main.py.

These bugs live INSIDE the ``lifespan`` closure (background loops + shutdown), so
they can't be unit-executed in isolation; the practical protection is a source-
shape guard that fails if the exact defect class returns, plus the boot-import
smoke (test_imports / lifespan) that catches syntax/import breakage. Each guard
targets a CONFIRMED defect (verified by reading the source, §22/§23):

  * bare ``os.getenv`` in main.py (main.py imports ``os as _os`` only — bare
    ``os`` is undefined → NameError when _student_reading_loop ticks),
  * ``_research_loop`` referencing an unbound ``llm``,
  * crawler handles assigned in the nested ``_boot_continuation`` without
    ``nonlocal`` (so shutdown never sees them → swallowed NameError).
"""
from __future__ import annotations

import re
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[1] / "main.py"
_SRC = _MAIN.read_text(encoding="utf-8")


def test_no_bare_os_calls_in_main():
    """main.py imports `os as _os` (no bare `os`); a bare `os.<attr>()` call is a
    latent NameError. Comments/strings ('os.environ probe') are excluded."""
    offenders = []
    for i, line in enumerate(_SRC.splitlines(), 1):
        code = line.split("#", 1)[0]  # drop inline comments
        # a bare os.<call> not preceded by an identifier char / dot / quote
        for m in re.finditer(r"(?<![\w.\"'])os\.(getenv|environ|path|makedirs|remove|listdir)\b", code):
            offenders.append(f"{i}: {line.strip()}")
    assert not offenders, "bare os.* usages (main.py imports os as _os):\n" + "\n".join(offenders)


def test_research_loop_binds_llm():
    """_research_loop must bind `llm` before using it (was unbound → NameError)."""
    idx = _SRC.find("async def _research_loop")
    assert idx > 0, "_research_loop not found"
    body = _SRC[idx: idx + 6000]
    assert "llm = getattr(app.state" in body, \
        "R-F2448: _research_loop uses `llm` without binding it → latent NameError"


def test_crawler_handles_shared_with_shutdown():
    """Crawler handles must be lifespan-scope (nonlocal in _boot_continuation)
    so shutdown can stop the loop; else shutdown NameErrors are swallowed."""
    assert re.search(r"^\s*_crawler_stop_event = None", _SRC, re.M), \
        "lifespan-scope default for _crawler_stop_event missing"
    assert re.search(r"^\s*_crawler_task = None", _SRC, re.M), \
        "lifespan-scope default for _crawler_task missing"
    assert "nonlocal _crawler_stop_event, _crawler_task" in _SRC, \
        "R-F2448: _boot_continuation must declare the crawler handles nonlocal"


if __name__ == "__main__":
    import sys, pytest
    sys.exit(pytest.main([__file__, "-q"]))
