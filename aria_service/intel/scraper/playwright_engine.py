"""Clean Playwright engine — no stealth, honest user-agent, circuit breaker.

Scope
─────
A browser-automation wrapper that sits alongside Lightpanda (light,
fast, Zig-based, covers ~70% of JS-heavy sites) as a heavier fallback
for sites Lightpanda can't render (complex React / Angular admin
panels, multi-step procurement-portal forms, tab-heavy SPAs).

What this does NOT do
─────────────────────
  - NO navigator.webdriver spoofing
  - NO Canvas / WebGL / AudioContext fingerprint randomisation
  - NO residential proxy rotation
  - NO Cloudflare challenge waiting / CAPTCHA handling
  - NO human-behaviour simulation (random delays, bezier mouse curves)

We identify honestly as `ARIA-Research-Crawler`. If a site blocks us,
we report `source=blocked_by_bot_detection` so the caller knows.
That's the DD-defensible pattern — every ARK-DD report must be able
to cite how the data was obtained.

Public API
──────────
  async fetch(url, *, wait_for="load", timeout=45.0) -> ScrapeResult
      Generic single-page fetch. Returns cleaned HTML + extracted text.

  async fetch_with_selectors(url, selectors: dict) -> dict
      For target-specific adapters. Returns a dict keyed by the
      selectors provided, with matched element text.

  async is_available() -> bool
      Health check for self_diagnostic.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any
from ..engine_wiring import wired  # R-F3557 (§21a)

logger = logging.getLogger("aria.scraper.playwright_engine")

# ── Configuration ──────────────────────────────────────────────────────────

_UA = "ARIA-Research-Crawler/1.0 (+research@arkmurus.com)"
_DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
_DEFAULT_TIMEOUT_S = 45.0
_DEFAULT_WAIT_FOR = "load"  # load | domcontentloaded | networkidle

# Bound concurrency so we don't OOM the fly.io machine — Chromium is
# ~300 MB resident per browser. At 4 GB total, 2 concurrent is the
# safe ceiling (headroom for the rest of the service).
_MAX_CONCURRENT_BROWSERS = int(os.getenv("ARIA_PLAYWRIGHT_MAX_CONCURRENT", "2"))
_BROWSER_SEMAPHORE: asyncio.Semaphore | None = None


def _semaphore() -> asyncio.Semaphore:
    global _BROWSER_SEMAPHORE
    if _BROWSER_SEMAPHORE is None:
        _BROWSER_SEMAPHORE = asyncio.Semaphore(_MAX_CONCURRENT_BROWSERS)
    return _BROWSER_SEMAPHORE


# ── Shapes ─────────────────────────────────────────────────────────────────

@dataclass
class ScrapeResult:
    ok: bool
    url: str
    final_url: str = ""
    status: int = 0
    html: str = ""
    text: str = ""
    title: str = ""
    error: str | None = None
    duration_ms: int = 0
    blocked: bool = False
    block_reason: str = ""
    browser: str = "chromium"


# ── Block detection — honest reporting, not evasion ────────────────────────
# We detect block pages so the caller can report them accurately.
# We do NOT attempt to bypass them.

_BLOCK_SIGNATURES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"Attention Required!.*Cloudflare", re.IGNORECASE | re.DOTALL),
     "Cloudflare block"),
    (re.compile(r"Please enable JavaScript and cookies to continue", re.IGNORECASE),
     "Cloudflare JS challenge"),
    (re.compile(r"DDoS protection by Cloudflare", re.IGNORECASE),
     "Cloudflare DDoS page"),
    (re.compile(r"challenge.cloudflare\.com|cdn-cgi/challenge-platform", re.IGNORECASE),
     "Cloudflare challenge"),
    (re.compile(r"DataDome|datadome\.co", re.IGNORECASE), "DataDome block"),
    (re.compile(r"PerimeterX.*block|px-captcha", re.IGNORECASE), "PerimeterX block"),
    (re.compile(r"Akamai.*bot manager", re.IGNORECASE), "Akamai Bot Manager block"),
    (re.compile(r"Access Denied.*You don'?t have permission", re.IGNORECASE | re.DOTALL),
     "generic 403 access denied"),
    (re.compile(r"captcha|recaptcha|hcaptcha", re.IGNORECASE), "CAPTCHA page"),
    (re.compile(r"Are you a robot\?|prove you'?re human", re.IGNORECASE), "bot-check page"),
]


def _detect_block(html: str, status: int) -> tuple[bool, str]:
    """Return (is_blocked, reason). Honest detection — we report blocks,
    we don't circumvent them."""
    if status in (403, 429, 503) and len(html) < 5000:
        # Small body + blocking status = likely a block page
        for pattern, reason in _BLOCK_SIGNATURES:
            if pattern.search(html):
                return True, reason
        return True, f"HTTP {status} with short body (likely block)"
    for pattern, reason in _BLOCK_SIGNATURES:
        if pattern.search(html[:10000]):
            return True, reason
    return False, ""


# ── Browser lifecycle ──────────────────────────────────────────────────────

async def _launch_browser():
    """Launch Chromium. Headless, honest UA, no stealth patches.

    Returns (playwright, browser, context, page) — caller is responsible
    for cleanup via _cleanup_browser.
    """
    from playwright.async_api import async_playwright

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(
        headless=True,
        args=[
            # Minimal args — NO --disable-blink-features=AutomationControlled
            # (that's a stealth patch). Leave Chrome defaults on.
            "--no-sandbox",  # required for fly.io containerised run
            "--disable-dev-shm-usage",  # prevents /dev/shm OOM
        ],
    )
    context = await browser.new_context(
        user_agent=_UA,
        viewport=_DEFAULT_VIEWPORT,
        locale="en-GB",
        timezone_id="Europe/London",
        # NOTE: we deliberately do NOT monkey-patch navigator.webdriver,
        # WebGL, Canvas, or any other fingerprint signal. Playwright's
        # default behaviour is fine.
    )
    page = await context.new_page()
    # Clear timeouts so page.goto() respects our outer timeout
    page.set_default_timeout(int(_DEFAULT_TIMEOUT_S * 1000))
    return playwright, browser, context, page


async def _cleanup_browser(playwright, browser, context, page) -> None:
    """Best-effort cleanup — swallow all errors so the caller's flow
    never depends on teardown succeeding."""
    for coro, label in [
        (page.close() if page else None, "page"),
        (context.close() if context else None, "context"),
        (browser.close() if browser else None, "browser"),
        (playwright.stop() if playwright else None, "playwright"),
    ]:
        if coro is None:
            continue
        try:
            await coro
        except Exception as e:
            logger.debug("[playwright] cleanup %s raised: %s", label, e)


# ── Public API ─────────────────────────────────────────────────────────────

@wired(module="playwright_engine", summary="Playwright fetch completed", gap_type="source_failure")
async def fetch(
    url: str,
    *,
    wait_for: str = _DEFAULT_WAIT_FOR,
    timeout: float = _DEFAULT_TIMEOUT_S,
    extract_selectors: dict[str, str] | None = None,
    cookies: list[dict[str, str]] | None = None,
) -> ScrapeResult:
    """Fetch a URL with a full Chromium browser. Bounded concurrency.

    Args:
        url: target URL
        wait_for: one of 'load' | 'domcontentloaded' | 'networkidle'.
            'networkidle' is the heaviest — waits until no network
            activity for 500ms. Best for SPA shells.
        timeout: hard ceiling on page.goto + wait-for, in seconds
        extract_selectors: optional dict of {name: css_selector}. If
            provided, returns the matched element text for each in
            ScrapeResult._extracted (attached as attribute).
        cookies: optional list of cookie dicts to inject before navigation.
            Each dict must have 'name', 'value', and optionally 'domain',
            'path'. Used for reading login-gated pages with stored creds.

    Returns ScrapeResult.
    """
    result = ScrapeResult(ok=False, url=url)
    t0 = time.time()

    async with _semaphore():
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = await _launch_browser()

            # R-F1103 — inject cookies before navigation for login-gated pages
            if cookies:
                try:
                    await context.add_cookies(cookies)
                except Exception as _ce:
                    logger.debug("[playwright] cookie injection failed: %s", _ce)

            # Navigate
            try:
                response = await page.goto(
                    url,
                    wait_until=wait_for,
                    timeout=int(timeout * 1000),
                )
            except Exception as e:
                result.error = f"goto: {type(e).__name__}: {str(e)[:200]}"
                return result

            if response:
                result.status = response.status
                result.final_url = page.url

            # Get HTML + block check
            try:
                html = await page.content()
            except Exception as e:
                result.error = f"content: {type(e).__name__}: {str(e)[:200]}"
                return result

            result.html = html
            blocked, reason = _detect_block(html, result.status)
            if blocked:
                result.blocked = True
                result.block_reason = reason
                result.error = f"blocked: {reason}"
                # Still return the HTML + status — caller can decide
                # what to do with the block page.
                return result

            # Extract page title
            try:
                result.title = await page.title() or ""
            except Exception:
                pass

            # Plain-text extraction via existing structured HTML helper
            try:
                from ..researcher import _extract_structured_html
                extracted = _extract_structured_html(html)
                result.text = extracted.get("text", "") or ""
            except Exception:
                # Fallback to naive tag strip
                result.text = re.sub(r"<[^>]+>", " ", html)[:20000]

            # Selector-based extraction if requested
            if extract_selectors:
                extracted_data: dict[str, Any] = {}
                for name, selector in extract_selectors.items():
                    try:
                        element = await page.query_selector(selector)
                        if element is None:
                            extracted_data[name] = None
                            continue
                        text = await element.inner_text()
                        extracted_data[name] = text.strip()
                    except Exception as e:
                        extracted_data[name] = None
                        logger.debug(
                            "[playwright] selector %s=%r failed: %s",
                            name, selector, e,
                        )
                result._extracted = extracted_data  # type: ignore

            result.ok = True
            return result

        except Exception as e:
            result.error = f"{type(e).__name__}: {str(e)[:200]}"
            return result
        finally:
            await _cleanup_browser(playwright, browser, context, page)
            result.duration_ms = int((time.time() - t0) * 1000)


async def fetch_with_selectors(
    url: str,
    selectors: dict[str, str],
    *,
    wait_for: str = _DEFAULT_WAIT_FOR,
    timeout: float = _DEFAULT_TIMEOUT_S,
    cookies: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Convenience wrapper for adapter use. Returns a dict keyed by
    selector name with extracted text + meta about the fetch."""
    result = await fetch(
        url,
        wait_for=wait_for,
        timeout=timeout,
        extract_selectors=selectors,
        cookies=cookies,
    )
    extracted = getattr(result, "_extracted", {}) or {}
    return {
        "ok": result.ok,
        "url": result.url,
        "final_url": result.final_url,
        "status": result.status,
        "title": result.title,
        "extracted": extracted,
        "blocked": result.blocked,
        "block_reason": result.block_reason,
        "error": result.error,
        "duration_ms": result.duration_ms,
    }


# ── Health check ───────────────────────────────────────────────────────────

async def is_available() -> bool:
    """Lightweight readiness check — Playwright importable + Chromium
    binary present on disk.

    Past gap (self_diagnostic 2026-04-19): the previous implementation
    launched a real Chromium with --no-sandbox + closed it. On fly.io's
    constrained CPU + cold cache, that routinely took >15s and tripped
    the diagnostic's smoke-check timeout, surfacing as
    "FAIL: is_available raised: TimeoutError" even though the engine
    works fine when actually called. Real fetch() calls have their own
    longer timeouts (45-120s depending on portal).

    The new check verifies:
      1. playwright.async_api importable (catches missing package)
      2. async_playwright().start() returns within 5s (catches broken
         install where the driver bundle is corrupt)
      3. The chromium executable_path exists on disk (catches the case
         where `playwright install chromium` was never run)

    No browser is actually launched. Sub-second execution. Zero RAM cost.
    """
    import asyncio as _aio
    import os as _os

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.debug("[playwright] is_available: package not installed")
        return False

    try:
        # Driver-spinup bound. On warm machines this is sub-2s; on fly.io
        # cold-cache first boot it routinely hits 15-25s while node
        # decompresses driver bundles from /usr/local/lib. Previously
        # capped at 5s — 2026-04-20 audit found this flipped
        # chromium_available=False for the first ~30s of every deploy
        # even though chromium was correctly installed. Real launches
        # (fetch calls) have their own 45-120s timeouts; this readiness
        # check only needs to confirm the driver *can* start eventually.
        playwright = await _aio.wait_for(
            async_playwright().start(), timeout=30.0,
        )
    except _aio.TimeoutError:
        logger.warning("[playwright] is_available: driver spinup > 30s — install may be broken")
        return False
    except Exception as e:
        logger.debug("[playwright] is_available: driver init failed: %s", e)
        return False

    try:
        # Resolve the bundled chromium binary path without launching it.
        # If the binary is missing, .executable_path is None or points
        # to a non-existent file (depends on Playwright version).
        exe = playwright.chromium.executable_path
        return bool(exe and _os.path.exists(exe))
    except Exception as e:
        logger.debug("[playwright] is_available: executable_path lookup failed: %s", e)
        return False
    finally:
        try:
            await _aio.wait_for(playwright.stop(), timeout=3.0)
        except Exception:
            pass


# ── R-F1651: JS-rendered form field detection ──────────────────────────────

async def submit_form(
    url: str,
    form_data: dict[str, str],
    *,
    submit_selector: str = '[type="submit"]',
    success_indicator: str = "",
    timeout: float = _DEFAULT_TIMEOUT_S,
    captcha_token: str | None = None,  # R-F1689: pre-solved CAPTCHA token
) -> dict[str, Any]:
    """R-F1652: Fill and submit a form through Playwright.

    Uses the browser to fill form fields, click submit, and wait for
    navigation. This carries the browser session, cookies, and CSRF
    tokens through submission — unlike httpx POST which loses the
    browser context.

    Args:
        url: The registration page URL.
        form_data: Dict of field name/selector -> value.
        submit_selector: CSS selector for the submit button.
        success_indicator: Text that indicates success in the resulting page.
        timeout: Max time for the whole operation.
        captcha_token: R-F1689 — pre-solved CAPTCHA token to inject into
            the page's g-recaptcha-response textarea before submission.

    Returns:
        {"success": bool, "final_url": str, "response_text": str,
         "error": str, "cookies": list[dict]}
    """
    async with _semaphore():
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = await _launch_browser()
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            await page.wait_for_timeout(1000)

            # R-F1689: Inject pre-solved CAPTCHA token before filling form
            if captcha_token:
                try:
                    # R-F1695: use .value (not innerHTML) for textarea, and
                    # JSON-escape the token to prevent JS injection.
                    # The reCAPTCHA response is a <textarea>, so innerHTML
                    # sets the HTML content (wrong) while .value sets the
                    # form field value (correct). Also escape via JSON so
                    # special chars in the token don't break the JS.
                    import json as _json1695
                    _escaped_token = _json1695.dumps(captcha_token)
                    await page.evaluate(f'''
                        () => {{
                            const ta = document.getElementById("g-recaptcha-response");
                            if (ta) {{
                                ta.value = {_escaped_token};
                                ta.style.display = "block";
                            }}
                            // Also try the invisible reCAPTCHA callback
                            if (typeof ___grecaptcha_cfg !== "undefined") {{
                                for (const [k, v] of Object.entries(___grecaptcha_cfg.clients)) {{
                                    if (v && v.callback) {{
                                        v.callback({_escaped_token});
                                    }}
                                }}
                            }}
                        }}
                    ''')
                    await page.wait_for_timeout(500)
                    logger.debug("[submit_form] R-F1689: injected CAPTCHA token")
                except Exception as e:
                    logger.debug(
                        "[submit_form] R-F1689: failed to inject CAPTCHA token: %s", e,
                    )

            # Fill each form field
            for field_name, value in form_data.items():
                try:
                    # Try by name first, then by id, then by label text
                    selector = f'[name="{field_name}"]'
                    el = await page.query_selector(selector)
                    if not el:
                        selector = f'#{field_name}'
                        el = await page.query_selector(selector)
                    if not el:
                        selector = f'[id="{field_name}"]'
                        el = await page.query_selector(selector)
                    if el:
                        # R-F1714: type-aware fill. A registration form is not
                        # all text inputs — terms checkboxes and entity-type
                        # radios must be CLICKED/CHECKED, not .fill()'d (fill
                        # raises on a checkbox and silently leaves terms
                        # unaccepted → registration rejected). Selects need
                        # select_option. Detect the control and act correctly.
                        el_type = (await el.get_attribute("type") or "").lower()
                        tag = (await el.evaluate("e => e.tagName")) or ""
                        if el_type == "checkbox":
                            want = str(value).strip().lower() in ("1", "true", "on", "yes", "checked")
                            await (el.check() if want else el.uncheck())
                        elif el_type == "radio":
                            await el.check()
                        elif tag.lower() == "select":
                            await el.select_option(value)
                        else:
                            await el.fill(value)
                        await page.wait_for_timeout(100)
                    else:
                        logger.debug("[submit_form] field %s not found on %s", field_name, url)
                except Exception as e:
                    logger.debug("[submit_form] failed to fill %s: %s", field_name, e)

            # Click submit and wait for navigation
            try:
                async with page.expect_navigation(timeout=20000):
                    await page.click(submit_selector)
                await page.wait_for_load_state("networkidle")
            except Exception as e:
                # Page may have already navigated or form submitted via JS
                logger.debug("[submit_form] navigation after submit: %s", e)
                await page.wait_for_timeout(3000)

            final_url = page.url
            response_text = await page.content()
            cookies = await context.cookies()

            # Check for success indicator
            success = False
            if success_indicator:
                success = success_indicator.lower() in response_text.lower()
            else:
                # No indicator specified — assume success if we got a response
                success = True

            return {
                "success": success,
                "final_url": final_url,
                "response_text": response_text[:50000],
                "cookies": cookies,
                "error": "",
            }
        except Exception as e:
            logger.debug("[submit_form] failed for %s: %s", url, e)
            return {
                "success": False,
                "final_url": "",
                "response_text": "",
                "cookies": [],
                "error": str(e),
            }
        finally:
            await _cleanup_browser(playwright, browser, context, page)


async def login_and_get_api_key(
    login_url: str,
    login_data: dict[str, str],
    api_key_url: str,
    *,
    key_selector: str = "",
    key_regex: str = "",
    submit_selector: str = '[type="submit"]',
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """R-F1712: log into a portal then read the API key from its dashboard.

    The missing back-half of autonomous onboarding. Logs in (same browser
    context carries the auth cookies), navigates to the API-key page, waits for
    JS to render, and extracts the key from the RENDERED DOM — `key_selector`
    first (read the element's value/text), else `key_regex` over the rendered
    HTML. (Rendered DOM, not static HTML: dashboards inject the key via JS, so a
    static httpx scan misses it — the R-F1707 dynamic-content lesson applied to
    retrieval.)

    Returns {"success": bool, "api_key": str, "final_url": str, "error": str}.
    success is True ONLY when a non-empty key was actually extracted — never a
    fabricated/assumed key (R-F1702 honesty).
    """
    async with _semaphore():
        playwright = browser = context = page = None
        try:
            playwright, browser, context, page = await _launch_browser()

            # 1. Log in (carries cookies through the shared context).
            await page.goto(login_url, wait_until="networkidle", timeout=int(timeout * 1000))
            await page.wait_for_timeout(800)
            for field_name, value in login_data.items():
                try:
                    el = (await page.query_selector(f'[name="{field_name}"]')
                          or await page.query_selector(f'#{field_name}')
                          or await page.query_selector(f'[id="{field_name}"]'))
                    if el:
                        await el.fill(value)
                        await page.wait_for_timeout(100)
                    else:
                        logger.debug("[login_and_get_api_key] login field %s not found", field_name)
                except Exception as e:
                    logger.debug("[login_and_get_api_key] fill %s failed: %s", field_name, e)
            try:
                async with page.expect_navigation(timeout=20000):
                    await page.click(submit_selector)
                await page.wait_for_load_state("networkidle")
            except Exception as e:
                logger.debug("[login_and_get_api_key] post-login nav: %s", e)
                await page.wait_for_timeout(2500)

            # 2. Navigate to the API-key page + let JS render.
            await page.goto(api_key_url, wait_until="networkidle", timeout=int(timeout * 1000))
            await page.wait_for_timeout(1200)
            rendered = await page.content()

            # 3. Extract the key from the RENDERED DOM. R-F1715: collect ALL
            #    candidates (selector result first, then every regex match) — a
            #    bare key-shaped regex can also match CSRF tokens / asset hashes
            #    on the page, so the caller VERIFIES each candidate against the
            #    portal API and keeps the one that actually works. Returning only
            #    the first match risked activating a wrong string as the key.
            candidates: list[str] = []

            def _add(c: str) -> None:
                c = (c or "").strip()
                if c and c not in candidates:
                    candidates.append(c)

            if key_selector:
                try:
                    el = await page.query_selector(key_selector)
                    if el:
                        _add((await el.get_attribute("value")) or (await el.inner_text()) or "")
                except Exception as e:
                    logger.debug("[login_and_get_api_key] selector extract failed: %s", e)
            if key_regex:
                try:
                    import re as _re
                    for m in _re.finditer(key_regex, rendered):
                        _add(m.group(1) if m.groups() else m.group(0))
                except Exception as e:
                    logger.debug("[login_and_get_api_key] regex extract failed: %s", e)

            return {
                "success": bool(candidates),
                "api_key": candidates[0] if candidates else "",
                "candidates": candidates[:10],
                "final_url": page.url,
                "error": "" if candidates else "no API key found on dashboard (selector/regex matched nothing)",
            }
        except Exception as e:
            logger.debug("[login_and_get_api_key] failed for %s: %s", login_url, e)
            return {"success": False, "api_key": "", "final_url": "", "error": str(e)}
        finally:
            await _cleanup_browser(playwright, browser, context, page)


async def detect_form_fields(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> list[tuple[str, str, str]]:
    """R-F1651: Extract registration form fields from a JS-rendered page.

    Uses Playwright to load the page, wait for JS to render, then evaluate
    JavaScript in the browser context to extract form field names and types
    from the rendered DOM. Handles SPAs (React, Angular, Vue) that don't
    include form fields in the raw HTML.

    Returns a list of (selector, field_type, value_source) tuples compatible
    with portal_registry._build_form_data.

    Falls back to empty list if Playwright is unavailable or the page has
    no detectable form — caller should fall back to the HTML-based detector.
    """
    async with _semaphore():
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = await _launch_browser()
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            # Extra wait for SPA frameworks to finish rendering
            await page.wait_for_timeout(2000)

            # Evaluate JS in the browser to extract form fields from the
            # rendered DOM — this catches React/Angular/Vue forms that
            # don't exist in the raw HTML.
            fields = await page.evaluate("""() => {
                const form = document.querySelector('form');
                if (!form) return [];
                const results = [];
                const seen = new Set();
                form.querySelectorAll('input, select, textarea').forEach(el => {
                    const name = el.name || el.id;
                    if (!name || seen.has(name)) return;
                    seen.add(name);
                    let type = 'text';
                    if (el.tagName === 'SELECT') type = 'select';
                    else if (el.tagName === 'TEXTAREA') type = 'text';
                    else if (el.type === 'email') type = 'email';
                    else if (el.type === 'password') type = 'password';
                    else if (el.type === 'checkbox') type = 'checkbox';
                    else if (el.type === 'radio') type = 'radio';
                    else if (el.type === 'hidden') type = 'hidden';
                    results.push([name, type, '']);
                });
                return results;
            }""")
            return [(f[0], f[1], f[2]) for f in fields] if fields else []
        except Exception as e:
            logger.debug("[playwright] detect_form_fields failed for %s: %s", url, e)
            return []
        finally:
            await _cleanup_browser(playwright, browser, context, page)


# ── R-F1651: Behavioural CAPTCHA detection ─────────────────────────────────

async def detect_captcha_type(
    url: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_S,
) -> dict[str, Any]:
    """R-F1651: Detect CAPTCHA type on a page.

    Uses Playwright to load the page and check for known CAPTCHA widgets:
      - reCAPTCHA v2/v3 (Google) — image-based, solvable via 2Captcha
      - hCAPTCHA (Cloudflare) — image-based, solvable via 2Captcha
      - Cloudflare Turnstile — behavioural, NOT solvable via 2Captcha
      - DataDome — behavioural, NOT solvable
      - PerimeterX (Human) — behavioural, NOT solvable

    Returns:
        {"has_captcha": bool, "captcha_type": str | None,
         "is_behavioural": bool, "details": str}
    """
    async with _semaphore():
        playwright = None
        browser = None
        context = None
        page = None
        try:
            playwright, browser, context, page = await _launch_browser()
            await page.goto(url, wait_until="networkidle", timeout=int(timeout * 1000))
            await page.wait_for_timeout(2000)

            # Check for known CAPTCHA widgets via JS evaluation
            result = await page.evaluate("""() => {
                // reCAPTCHA v2 — explicit div.g-recaptcha or iframe
                if (document.querySelector('.g-recaptcha') ||
                    document.querySelector('iframe[src*="recaptcha"]') ||
                    document.querySelector('div[data-sitekey]')) {
                    return {has: true, type: 'recaptcha', behavioural: false};
                }
                // hCAPTCHA
                if (document.querySelector('.h-captcha') ||
                    document.querySelector('iframe[src*="hcaptcha"]')) {
                    return {has: true, type: 'hcaptcha', behavioural: false};
                }
                // Cloudflare Turnstile
                if (document.querySelector('[data-turnstile]') ||
                    document.querySelector('cf-turnstile') ||
                    document.querySelector('div[data-widget="turnstile"]')) {
                    return {has: true, type: 'turnstile', behavioural: true};
                }
                // DataDome
                if (document.querySelector('[data-datadome]') ||
                    document.querySelector('script[src*="datadome"]')) {
                    return {has: true, type: 'datadome', behavioural: true};
                }
                // PerimeterX / Human
                if (document.querySelector('[data-px]') ||
                    document.querySelector('script[src*="perimeterx"]') ||
                    document.querySelector('script[src*="px-cdn"]')) {
                    return {has: true, type: 'perimeterx', behavioural: true};
                }
                // Generic CAPTCHA references in page text
                const body = document.body.innerText || '';
                if (/captcha|recaptcha|hcaptcha|turnstile/i.test(body)) {
                    return {has: true, type: 'unknown', behavioural: true};
                }
                return {has: false, type: null, behavioural: false};
            }""")

            return {
                "has_captcha": result.get("has", False),
                "captcha_type": result.get("type"),
                "is_behavioural": result.get("behavioural", False),
                "details": (
                    f"Detected {result.get('type', 'unknown')} "
                    f"({'behavioural' if result.get('behavioural') else 'image-based'})"
                    if result.get("has") else "No CAPTCHA detected"
                ),
            }
        except Exception as e:
            logger.debug("[playwright] detect_captcha_type failed for %s: %s", url, e)
            return {"has_captcha": False, "captcha_type": None,
                    "is_behavioural": False, "error": str(e)}
        finally:
            await _cleanup_browser(playwright, browser, context, page)
