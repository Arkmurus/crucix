"""R-F4082 (C-130) — read-only page INSPECTION for security and DD analysis.

ARIA could already fetch (`crawler/fetcher`), render JS (`intel/headless`,
driving Lightpanda over CDP) and extract text (trafilatura). None of that
answers the questions a security or due-diligence reviewer actually asks,
because the answers are not in the prose:

  * which security headers are set — CSP, HSTS, X-Frame-Options, …
  * which THIRD-PARTY domains does the page contact
  * what does the console say (errors, stack traces, leaked values)
  * where did it finally land after redirects

This module answers exactly those, and nothing else.

WHAT IT DELIBERATELY IS NOT
===========================
Read-only navigation and observation. No stealth or anti-bot evasion, no CAPTCHA
handling, no form submission, no login. §27 is explicit: evading controls to
take data a provider is refusing us is untenable for a due-diligence product —
the same reasoning that stopped us scraping TrustOnline and using Find Case Law
unlicensed. `test_rf4081_page_inspect` pins that boundary in source so a later
"just add stealth for site X" cannot pass quietly.

It identifies itself. §27b measured the difference: `python-requests/2.0` got
HTTP 403 from the Wikipedia API while a descriptive UA got 200 — same IP, same
second. Being honest about who we are is what unblocks legitimate sources.

UNKNOWN IS NOT CLEAN
====================
When no browser is available every finding field is ``None``, never ``{}`` or
``[]``. On a security surface, rendering "could not measure" as "measured and
found nothing" is a false all-clear — the §1 collapse this repo has paid for
three times.
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

from .engine_wiring import wire_failure, wire_success

logger = logging.getLogger("aria.page_inspect")

#: Identify ourselves (§27b). Never impersonate a consumer browser.
USER_AGENT = os.getenv(
    "ARIA_PAGE_INSPECT_UA",
    "AriaIntelligence/1.0 (+https://imaria.io; aria@arkmurus.com) page-inspector",
)

#: The headers a reviewer asks about. Absence of one IS the finding, so the
#: report always carries every name with an explicit present flag.
SECURITY_HEADERS = (
    "content-security-policy",
    "strict-transport-security",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
)

#: Bounds. A page can issue thousands of requests; an inspector that captures
#: them all turns one call into a memory incident on a box this session spent
#: considerable effort keeping responsive.
MAX_REQUESTS = int(os.getenv("ARIA_PAGE_INSPECT_MAX_REQUESTS", "300"))
MAX_CONSOLE = int(os.getenv("ARIA_PAGE_INSPECT_MAX_CONSOLE", "100"))
DEFAULT_TIMEOUT_S = float(os.getenv("ARIA_PAGE_INSPECT_TIMEOUT_S", "25"))


def _browser_available() -> bool:
    """Is a driveable CHROMIUM present? Separate function so tests can pin it.

    R-F4082 — deliberately NOT `headless.is_available()`. That checks for the
    Lightpanda binary, which `intel/headless` drives over CDP for cheap DOM
    rendering. This module launches CHROMIUM instead, because console messages,
    request interception and response headers are what the security questions
    need and Lightpanda's CDP surface does not expose them the same way.
    Gating on Lightpanda would have made this capability refuse to run on a box
    that had chromium — coupling a feature to an unrelated binary, which is the
    "gate on the wrong thing" shape §1 keeps recording.
    """
    try:
        import playwright  # noqa: F401
    except Exception:
        return False

    # Chromium ships as downloaded binaries, not with the wheel. Look where
    # playwright actually puts them (override honoured) rather than assume.
    candidates = []
    override = os.getenv("PLAYWRIGHT_BROWSERS_PATH")
    if override and override not in ("0",):
        candidates.append(override)
    home = os.path.expanduser("~")
    candidates += [
        os.path.join(home, ".cache", "ms-playwright"),          # Linux
        os.path.join(home, "AppData", "Local", "ms-playwright"),  # Windows
        os.path.join(home, "Library", "Caches", "ms-playwright"),  # macOS
    ]
    for root in candidates:
        try:
            if os.path.isdir(root) and any(
                n.startswith("chromium") for n in os.listdir(root)
            ):
                return True
        except OSError:
            continue
    return False


def _registrable(host: str) -> str:
    """Best-effort registrable domain — last two labels.

    Deliberately simple and deliberately NOT a PSL lookup: §6 says do not take a
    dependency Claude Code would not. It over-groups a few multi-part TLDs
    (co.uk), which errs toward calling something FIRST party — the conservative
    direction for a "third-party contact" finding, since it under-reports rather
    than inventing one.
    """
    host = (host or "").lower().strip(".")
    parts = [p for p in host.split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _is_third_party(url: str, page_domain: str) -> bool:
    try:
        host = urlparse(url).hostname or ""
    except Exception:
        return False
    if not host:
        return False
    return _registrable(host) != _registrable(page_domain)


def _build_header_report(headers: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Every tracked header, present or not.

    A missing security header is the finding. Omitting it would make "no HSTS"
    indistinguishable from "not checked".
    """
    lowered = {str(k).lower(): v for k, v in (headers or {}).items()}
    return {
        name: {"present": name in lowered, "value": lowered.get(name)}
        for name in SECURITY_HEADERS
    }


def inspect_page_unavailable_result(url: str) -> dict[str, Any]:
    """The honest shape when there is no browser: findings are UNKNOWN."""
    return {
        "ok": False,
        "available": False,
        "url": url,
        "final_url": None,
        "status": None,
        "title": None,
        "security_headers": None,
        "console_errors": None,
        "third_party_domains": None,
        "error": "no browser available (playwright/headless not installed)",
    }


async def inspect_page(url: str, *, timeout_s: float | None = None) -> dict[str, Any]:
    """Navigate read-only and report security-relevant observations.

    Never raises: returns a dict whose `ok` says whether the observation
    succeeded, and whose finding fields are None when they could not be measured.
    """
    timeout = float(timeout_s or DEFAULT_TIMEOUT_S)

    if not _browser_available():
        out = inspect_page_unavailable_result(url)
        try:
            wire_failure(
                module="page_inspect",
                detail=f"page inspection unavailable for {url[:120]}: no browser",
                gap_type="missing_capability",
                source="page_inspect:inspect_page",
            )
        except Exception:      # pragma: no cover - telemetry never blocks
            logger.debug("[R-F4082] unavailable wiring failed")
        return out

    console_errors: list[str] = []
    requests_seen: list[str] = []
    page_domain = urlparse(url).hostname or ""

    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            try:
                context = await browser.new_context(user_agent=USER_AGENT)
                page = await context.new_page()

                def _on_console(msg) -> None:
                    if len(console_errors) >= MAX_CONSOLE:
                        return
                    if msg.type in ("error", "warning"):
                        console_errors.append(f"{msg.type}: {str(msg.text)[:300]}")

                def _on_request(req) -> None:
                    if len(requests_seen) >= MAX_REQUESTS:
                        return
                    requests_seen.append(req.url)

                page.on("console", _on_console)
                page.on("request", _on_request)

                response = await page.goto(
                    url, wait_until="domcontentloaded",
                    timeout=int(timeout * 1000),
                )
                headers = dict(response.headers) if response is not None else {}
                status = response.status if response is not None else None
                final_url = page.url
                title = await page.title()
            finally:
                await browser.close()
    except Exception as exc:
        try:
            wire_failure(
                module="page_inspect",
                detail=f"page inspection failed for {url[:120]}: "
                       f"{type(exc).__name__}: {exc}",
                gap_type="source_failure",
                source="page_inspect:inspect_page",
            )
        except Exception:      # pragma: no cover
            logger.debug("[R-F4082] failure wiring failed")
        return {
            "ok": False,
            "available": True,
            "url": url,
            "final_url": None,
            "status": None,
            "title": None,
            "security_headers": None,
            "console_errors": None,
            "third_party_domains": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    third_party = sorted({
        _registrable(urlparse(u).hostname or "")
        for u in requests_seen if _is_third_party(u, page_domain)
    } - {""})

    result = {
        "ok": True,
        "available": True,
        "url": url,
        "final_url": final_url,
        "redirected": final_url != url,
        "status": status,
        "title": title,
        "security_headers": _build_header_report(headers),
        "console_errors": console_errors,
        "third_party_domains": third_party,
        "requests_captured": len(requests_seen),
        "requests_truncated": len(requests_seen) >= MAX_REQUESTS,
        "error": None,
    }

    try:
        missing = [n for n, v in result["security_headers"].items()
                   if not v["present"]]
        wire_success(
            module="page_inspect",
            summary=(
                f"inspected {final_url[:80]} — status {status}, "
                f"{len(missing)} security headers absent, "
                f"{len(third_party)} third-party domains, "
                f"{len(console_errors)} console errors"
            ),
            source_id="page_inspect:inspect_page",
        )
    except Exception:      # pragma: no cover - telemetry never blocks
        logger.debug("[R-F4082] success wiring failed")

    return result
