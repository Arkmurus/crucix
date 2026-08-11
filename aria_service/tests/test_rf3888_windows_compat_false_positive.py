r"""R-F3888 — the Windows-compat check matched `pty` inside the word "empty".

MEASURED: the pre-commit hook, minutes after being revived (R-F3885/R-F3886),
BLOCKED a legitimate commit whose only offence was a comment ending

    "...and the endpoint STILL read empty. Two independent causes for one symptom"

`WINDOWS_INCOMPATIBLE_PATTERNS` carried `r"pty\."`, which matches the substring in
"empty.". `resource\.` and `fcntl\.` had the same shape.

TWO INDEPENDENT FAULTS, and both had to be fixed:
  1. NO LEFT BOUNDARY on the module-prefix patterns.
  2. IT SCANNED COMMENTS AS CODE. A check for "does this file USE an unavailable
     module" must read code; a comment discussing the module is not a use. The
     file's own `_strip_comment` helper — quote-aware, written for exactly this —
     existed and was never applied here.

`(?<![\w.])` is the right guard rather than `\b`: a bare `\b` still matches
`self.resource.x`, because the preceding `.` is a non-word character and so a
boundary exists there. Excluding a preceding dot too means only a bare module
reference matches.

WHY A FALSE POSITIVE IS NOT A SMALL BUG HERE. The hook stands between a defect and
main, so the first instinct on a bogus block is `git commit --no-verify` — and a
guard people routinely bypass protects nothing. That is R-F3858's lesson from the
other side: a guard must be able to come back clean.
"""
from __future__ import annotations

import importlib.util

from aria_service.tests._source_probe import repo_path

_spec = importlib.util.spec_from_file_location(
    "_pcc_rf3888", repo_path("scripts/pre_commit_checks.py"))
pcc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcc)


def _hits(line: str) -> list[str]:
    import re
    code = pcc._strip_comment(line)
    return [m for p, m in pcc.WINDOWS_INCOMPATIBLE_PATTERNS if re.search(p, code)]


def test_the_word_empty_is_not_a_pty_reference():
    """THE EXACT LINE THAT BLOCKED A REAL COMMIT."""
    line = "    # and the endpoint STILL read empty. Two independent causes for one symptom, and"
    assert _hits(line) == [], f"false positive on ordinary prose: {_hits(line)}"


def test_other_substring_traps_are_closed():
    for line in ("myresource.close()", "self.resource.release()",
                 "value = registry.property.get()", "x = crypty.hash()"):
        assert _hits(line) == [], f"false positive on {line!r}: {_hits(line)}"


def test_a_comment_discussing_an_api_is_not_a_use():
    """A check for 'does this file USE an unavailable module' must read code."""
    assert _hits("    # os.fork() is not available on Windows — do not use it") == []
    assert _hits("    # we deliberately avoid signal.signal() here") == []


def test_the_guard_still_catches_a_genuine_use():
    """R-F3858 — the control. Narrowing must not blind it; a guard that cannot fire
    is worse than the false positive it replaced."""
    assert _hits("    pid = os.fork()"), "os.fork() must still be caught"
    assert _hits("    import pty; pty.spawn(cmd)"), "a real pty use must still be caught"
    assert _hits("    fcntl.flock(f, fcntl.LOCK_EX)"), "fcntl must still be caught"
    assert _hits("    resource.getrlimit(resource.RLIMIT_NOFILE)"), "resource must still be caught"
    assert _hits("    signal.signal(signal.SIGTERM, h)"), "signal.signal must still be caught"


def test_the_matcher_reads_stripped_code_not_the_raw_line():
    """Pinned at the call site so the comment-stripping cannot be quietly dropped."""
    from aria_service.tests._source_probe import function_source

    src = function_source(pcc, "check_windows_compat")
    assert "_strip_comment(line)" in src
    assert "re.search(pattern, line)" not in src, (
        "check_windows_compat must match the stripped CODE, not the raw line")
