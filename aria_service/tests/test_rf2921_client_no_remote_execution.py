"""R-F2921 — the Windows client must never download and execute code at runtime.

2026-07-23: Kaspersky File Anti-Virus deleted aria_service/static/aria_client/aria.bat
with verdict "Trojan" (initiator git.exe), and its web protection blocked the same file
on download with an HTTP 499 block page — so a customer running Kaspersky could not
obtain the client at all.

The detection was CORRECT in pattern. The launcher ran:
    powershell "New-Object Net.WebClient; $w.DownloadFile('<server>/download/aria.py','aria.py')"
    python aria.py
i.e. fetch a script over the network and execute it, with `catch{}` swallowing failure
and NO hash, signature or integrity check of any kind. Anyone able to MITM or
compromise that endpoint had arbitrary code execution on a customer's Windows machine.
Adding an antivirus exclusion would have silenced an accurate detector on a real
weakness.

Fix: /download/client ships the Python client INSIDE the ZIP, so there is nothing to
fetch at runtime. These tests keep it that way.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

CLIENT_DIR = Path(__file__).resolve().parents[1] / "static" / "aria_client"
BAT = CLIENT_DIR / "aria.bat"
MAIN = Path(__file__).resolve().parents[1] / "main.py"


def _code_lines(text: str) -> list[str]:
    """Batch comment lines start with `::` or `REM` — a rationale comment naming the
    old pattern must not fail the test that bans the pattern."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("::") or s.upper().startswith("REM "):
            continue
        out.append(line)
    return out


@pytest.mark.parametrize("pattern,why", [
    (r"New-Object\s+Net\.WebClient", "WebClient download"),
    (r"DownloadFile\s*\(", "DownloadFile"),
    (r"DownloadString\s*\(", "DownloadString (fetch-and-eval)"),
    (r"Invoke-WebRequest[^\n]*-OutFile", "Invoke-WebRequest -OutFile"),
    (r"Invoke-Expression", "Invoke-Expression (eval)"),
    (r"\bIEX\b", "IEX (eval alias)"),
    (r"bitsadmin", "bitsadmin transfer"),
    (r"certutil[^\n]*-urlcache", "certutil download trick"),
])
def test_rf2921_launcher_has_no_remote_code_fetch(pattern, why):
    """No mechanism for fetching executable content at runtime, in EXECUTED lines."""
    code = "\n".join(_code_lines(BAT.read_text(encoding="utf-8", errors="ignore")))
    assert not re.search(pattern, code, re.I), (
        f"aria.bat can fetch remote code via {why} — this is the dropper pattern that "
        "got the client classified as Trojan and blocked for customers"
    )


def test_rf2921_launcher_still_talks_to_the_api():
    """The fix must not neuter the client: it still calls the chat API and health.
    Sending DATA to an API is categorically different from fetching CODE to run."""
    text = BAT.read_text(encoding="utf-8", errors="ignore")
    assert "/api/aria/chat" in text
    assert "/health/live" in text


def test_rf2921_bundle_ships_the_python_client():
    """The ZIP must contain the .py files, otherwise the launcher has nothing to run
    locally and the pressure to re-add a runtime download comes straight back."""
    src = MAIN.read_text(encoding="utf-8", errors="ignore")
    start = src.find("async def download_aria_client")
    assert start > -1, "the /download/client endpoint is gone"
    # Window sized past the rationale comment that documents WHY the skip was removed;
    # a short slice would stop before the code it asserts on and pass vacuously.
    body = src[start:start + 4000]

    assert 'f.endswith(".pyc")' in body, "build artefacts should still be excluded"
    assert not re.search(r'f\.endswith\("\.py"\)\s*or', body), (
        "the bundle still skips .py files — the launcher would have to download them "
        "at runtime, which is exactly what R-F2921 removed"
    )


def test_rf2921_python_client_files_exist_to_be_bundled():
    """The bundle can only ship what is in the tree — and R-F2919/R-F2920 exist
    because files do go missing from this tree."""
    assert (CLIENT_DIR / "aria.py").exists(), "aria.py missing from the client folder"
    assert (CLIENT_DIR / "aria_tui.py").exists(), "aria_tui.py missing from the client folder"
    assert BAT.exists(), "aria.bat missing from the client folder"


def test_rf2921_launcher_explains_itself_when_files_are_absent():
    """If the .py files are not next to the .bat it must SAY so and continue, never
    silently re-download."""
    text = BAT.read_text(encoding="utf-8", errors="ignore")
    assert "/download/client" in text, "no guidance pointing at the full bundle"
