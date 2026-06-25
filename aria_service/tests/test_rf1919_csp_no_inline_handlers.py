"""R-F1919 (G6b vaccine) — CSP `script-src-attr 'none'` + no inline event handlers.

The served pages relied on `script-src 'unsafe-inline'`, so an HTML-injection that
reached an innerHTML sink could execute, and inline `onclick=` handlers that
interpolate ids (the #3 DOM-XSS) ran freely. We migrated EVERY inline `on*=`
handler in the served HTML to delegated `addEventListener`, then added CSP
`script-src-attr 'none'` to block the whole inline-handler class.

THE GUARD: if anyone re-adds an inline `on*=` handler to a served page, it will be
silently blocked by the CSP at runtime (a dead button) — so this test fails the
build instead, forcing the delegated-listener pattern. (Inline `<script>` blocks
are still allowed — fully dropping `'unsafe-inline'` from script-src needs those
externalised, a tracked follow-up.)
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
PUBLIC = REPO / "public"

# matches an actual inline event-handler ATTRIBUTE (on...="), not the word in prose
_HANDLER_ATTR = re.compile(r"""\son(?:click|change|input|submit|load|keyup|keydown|keypress|mouseover|mouseout|mousedown|mouseup|focus|blur|error|dblclick|contextmenu|wheel|drag|drop)\s*=\s*["']""", re.IGNORECASE)


def _strip_js_comments(line: str) -> str:
    # drop // line comments so a comment mentioning onclick= isn't a false hit
    return line.split("//", 1)[0]


def test_csp_blocks_inline_event_handlers():
    csp = (REPO / "middleware" / "rateLimiter.mjs").read_text(encoding="utf-8", errors="ignore")
    # the CSP keyword 'none' is itself single-quoted inside the JS string ("'none'")
    assert re.search(r"scriptSrcAttr:\s*\[\s*[\"']'none'[\"']\s*\]", csp), \
        "CSP must set scriptSrcAttr: [\"'none'\"] to block inline event handlers"


def test_no_inline_event_handlers_in_served_html():
    offenders = []
    for html in sorted(PUBLIC.glob("*.html")):
        for i, raw in enumerate(html.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            line = _strip_js_comments(raw)
            if _HANDLER_ATTR.search(line):
                offenders.append(f"{html.name}:{i}: {raw.strip()[:90]}")
    assert not offenders, (
        "R-F1919: inline on*= handler(s) in served HTML — these are BLOCKED by CSP "
        "script-src-attr 'none' (dead buttons). Use data-action + addEventListener:\n"
        + "\n".join(offenders)
    )
