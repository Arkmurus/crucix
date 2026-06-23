"""R-F1791 — pre-commit guard: outbound HTTP backends must have a circuit breaker.

Root-cause fix (CLAUDE.md §1) for the breaker-gap class that R-F1790 fixed three
instances of (cross-check item #40). The guard flags any changed intel module
that constructs an HTTP client but references no circuit breaker.

Capability: drive the REAL check_circuit_breaker() — assert it FLAGS an
unguarded HTTP module, CLEARS when a breaker (or the documented opt-out) is
present, ignores non-intel files, and PASSES on the actual files R-F1790 fixed.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
from pre_commit_checks import check_circuit_breaker  # noqa: E402

REPO = Path(__file__).resolve().parents[2]


def _mk(tmp_path, name, body):
    d = tmp_path / "aria_service" / "intel"
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text(body, encoding="utf-8")
    return f


def test_flags_http_without_breaker(tmp_path):
    f = _mk(tmp_path, "bad_backend.py",
            "import httpx\n"
            "async def go():\n"
            "    async with httpx.AsyncClient() as c:\n"
            "        return await c.get('https://api.example.com')\n")
    issues = check_circuit_breaker([f])
    assert len(issues) == 1 and "NO circuit breaker" in issues[0]


def test_passes_http_with_breaker(tmp_path):
    f = _mk(tmp_path, "good_backend.py",
            "import httpx\n"
            "from .circuit_breaker import get_breaker\n"
            "async def go():\n"
            "    cb = get_breaker('x')\n"
            "    if cb.is_open():\n"
            "        return None\n"
            "    async with httpx.AsyncClient() as c:\n"
            "        return await c.get('https://api.example.com')\n")
    assert check_circuit_breaker([f]) == []


def test_passes_with_opt_out(tmp_path):
    f = _mk(tmp_path, "internal_call.py",
            "import httpx\n"
            "async def go():\n"
            "    async with httpx.AsyncClient() as c:  # no-breaker: internal fly call\n"
            "        return await c.get('http://aria-web.internal:3117/x')\n")
    assert check_circuit_breaker([f]) == []


def test_ignores_non_intel_files(tmp_path):
    d = tmp_path / "scripts"
    d.mkdir(parents=True, exist_ok=True)
    f = d / "tool.py"
    f.write_text("import httpx\nhttpx.get('https://x')\n", encoding="utf-8")
    assert check_circuit_breaker([f]) == []


def test_real_rf1790_files_now_pass():
    """The three files R-F1790 fixed must satisfy the new guard (no regression)."""
    files = [
        REPO / "aria_service" / "intel" / "researcher.py",
        REPO / "aria_service" / "intel" / "web_search.py",
        REPO / "aria_service" / "intel" / "crawl_enhancements.py",
    ]
    assert check_circuit_breaker(files) == []
