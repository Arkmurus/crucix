"""
R-F2064 — Adaptive Portal Registration Agent.

ARIA registers herself for data portals using a Playwright browser that
behaves like a human: reads the page, understands form structure, solves
captchas, adapts to errors, and learns from failures.

Core capabilities:
  1. Human-like interaction — random typing delays, mouse movements, pauses
  2. Dynamic form detection — finds fields by heuristics, not hardcoded selectors
  3. Multi-captcha support — reCAPTCHA v2/v3, hCaptcha, Turnstile via 2captcha
  4. Adaptive retry — learns from failures, adjusts strategy
  5. Email alias fallback — retries with +alias when email is taken
  6. Credential persistence — stores API keys in the vault
  7. Brain wiring — success/failure both reach the brain

Usage:
    from aria_service.intel.portal_agent import AdaptivePortalAgent

    async with AdaptivePortalAgent() as agent:
        result = await agent.register("newsapi")
        # {"success": True, "api_key": "...", "portal_id": "newsapi"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urljoin

from .portal_knowledge import RegistrationKnowledge

logger = logging.getLogger("aria.portal_agent")

# ── Browser configuration ─────────────────────────────────────────────────
_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]
_PAGE_TIMEOUT = 30_000  # ms
_SUBMIT_TIMEOUT = 20_000  # ms
_CAPTCHA_POLL_INTERVAL = 1  # seconds — poll faster
_CAPTCHA_MAX_POLLS = 60  # ~60 seconds max

# ARIA's identity for registrations
_ARIA_EMAIL = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
_ARIA_NAME = os.getenv("ARIA_PORTAL_NAME", "ARIA Research (Arkmurus Group)")
_ARIA_ORG = "Arkmurus Group Ltd"
_ARIA_WEBSITE = "https://arkmurus.com"

# ── Field detection patterns ──────────────────────────────────────────────
_FIELD_PATTERNS: dict[str, list[str]] = {
    "email": ["email", "e-mail", "mail", "user_email", "username"],
    "password": ["password", "pass", "pwd", "passwd"],
    "first_name": ["first_name", "firstname", "fname", "given_name", "first"],
    "last_name": ["last_name", "lastname", "lname", "surname", "family_name", "last"],
    "full_name": ["full_name", "fullname", "name", "your_name", "displayname", "your name"],
    "company": ["company", "organization", "org", "organisation", "firm", "business", "employer"],
    "website": ["website", "url", "homepage", "site", "web"],
    "phone": ["phone", "mobile", "telephone", "tel", "cell"],
    "address": ["address", "addr", "street"],
    "city": ["city", "town"],
    "state": ["state", "province", "region"],
    "zip": ["zip", "postal", "postcode"],
    "country": ["country", "nation"],
    "agree_terms": ["agree", "terms", "accept", "consent", "privacy", "condition"],
}


class AdaptivePortalAgent:
    """Browser-based portal registration agent with human-like adaptation."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._diagnostics: list[dict] = []
        self._attempt = 0
        self._knowledge = RegistrationKnowledge()
        self._domain = ""
        self._portal_id = ""

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """Launch the Playwright browser with human-like viewport."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed")
            raise

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=_BROWSER_ARGS,
        )
        # Randomize viewport to avoid fingerprinting
        width = random.randint(1200, 1600)
        height = random.randint(800, 1000)
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": width, "height": height},
        )
        self._page = await self._context.new_page()
        self._log("browser_started", f"Browser launched ({width}x{height})")

    async def close(self) -> None:
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass

    # ── Human-like interaction helpers ─────────────────────────────────────

    async def _human_type(self, selector: str, text: str) -> None:
        """Type text with random delays between keystrokes like a human."""
        element = await self._page.query_selector(selector)
        if not element:
            return
        await element.click()
        await self._pause(0.1, 0.3)
        # Clear existing text
        await element.press("Control+A")
        await self._pause(0.05, 0.1)
        await element.press("Backspace")
        await self._pause(0.1, 0.2)
        # Type character by character with random delays
        for char in text:
            await element.type(char, delay=random.uniform(0.03, 0.12))
            # Occasionally pause longer (like a human thinking)
            if random.random() < 0.03:
                await self._pause(0.2, 0.5)

    async def _human_click(self, selector: str) -> None:
        """Click an element with human-like mouse movement."""
        element = await self._page.query_selector(selector)
        if not element:
            return
        box = await element.bounding_box()
        if box:
            target_x = box["x"] + box["width"] / 2 + random.randint(-5, 5)
            target_y = box["y"] + box["height"] / 2 + random.randint(-5, 5)
            await self._page.mouse.move(target_x, target_y, steps=random.randint(5, 15))
            await self._pause(0.2, 0.4)
        await element.click()

    async def _random_scroll(self) -> None:
        """Perform random scrolling to simulate human reading."""
        scrolls = random.randint(1, 3)
        for _ in range(scrolls):
            distance = random.randint(100, 400)
            direction = random.choice([-1, 1])
            await self._page.evaluate(f"window.scrollBy(0, {distance * direction})")
            await self._pause(0.3, 0.8)

    async def _pause(self, min_s: float = 0.3, max_s: float = 1.0) -> None:
        """Pause like a human between actions."""
        await asyncio.sleep(random.uniform(min_s, max_s))

    # ── Public API ─────────────────────────────────────────────────────────

    async def register(self, portal_id: str) -> dict[str, Any]:
        """Register for a portal. Adapts to whatever form structure the site has.

        Args:
            portal_id: Portal ID from portal_registry.PORTALS.

        Returns:
            Dict with success, api_key, portal_id, diagnostics.
        """
        from aria_service.intel.portal_registry import PORTALS

        portal = next((p for p in PORTALS if p.id == portal_id), None)
        if not portal:
            return {"success": False, "error": f"Unknown portal: {portal_id}"}

        self._diagnostics = []
        self._attempt += 1
        t0 = time.time()
        self._portal_id = portal_id
        from urllib.parse import urlparse
        self._domain = urlparse(portal.url).netloc

        # Check knowledge base for prior patterns
        known_fields = self._knowledge.get_field_patterns(self._domain)
        if known_fields:
            self._log("knowledge", f"Found {len(known_fields)} known field patterns for {self._domain}")

        try:
            # Step 1: Navigate to the registration page
            reg_url = self._build_url(portal.url, portal.register_path or "/register")
            self._log("navigate", f"Loading {reg_url}")
            await self._safe_goto(reg_url)
            await self._pause(1, 3)

            # Step 2: Read the page — detect ALL form fields dynamically
            fields = await self._detect_fields()
            self._log("form_detected", f"Found {len(fields)} field types: {list(fields.keys())}")

            if not fields:
                return self._finish(portal_id, False, t0, "No form fields detected")

            # Step 3: Fill the form with human-like typing
            await self._random_scroll()
            await self._fill_fields(fields)
            self._log("form_filled", "All fields filled")

            # Step 4: Handle captcha
            await self._pause(0.5, 1.5)
            captcha_ok = await self._handle_captcha()
            if captcha_ok:
                self._log("captcha", "Captcha solved and injected")
            else:
                self._log("captcha", "No captcha detected or solving deferred")

            # Step 5: Capture pre-submit state for robust verification
            pre_submit_url = self._page.url
            pre_submit_html = await self._page.content()
            self._log("verify", "Captured pre-submit state for change detection")

            # Submit the form
            await self._pause(0.5, 1.0)
            submit_result = await self._submit_form()
            self._log("submitted", f"URL after submit: {submit_result['url']}")

            # Step 6: Robust verification — wait for URL change, error, or timeout
            await self._pause(1, 2)
            response = await self._verify_submission(pre_submit_html, pre_submit_url)
            self._log("response", f"Response: success={response['success']}, errors={response.get('errors', [])}, dup_email={response.get('dup_email', False)}")

            # Step 7: Handle "email already in use" — retry with alias
            if response.get("dup_email"):
                self._log("retry", "Email already registered — trying alias email")
                local, at, domain = _ARIA_EMAIL.partition("@")
                alias_email = f"{local}+{portal_id}@{domain}"

                # Re-fill email field with alias
                if "email" in fields:
                    name_attr = await fields["email"].get_attribute("name") or ""
                    id_attr = await fields["email"].get_attribute("id") or ""
                    selector = f'input[name="{name_attr}"]' if name_attr else f'input#{id_attr}'
                    await self._human_type(selector, alias_email)
                    self._log("retry", f"Changed email to {alias_email}")

                # Re-solve captcha (it was consumed)
                captcha_ok = await self._handle_captcha()
                if captcha_ok:
                    self._log("captcha", "Captcha re-solved for retry")

                # Re-submit
                await self._pause(0.5, 1.0)
                pre_submit_url2 = self._page.url
                pre_submit_html2 = await self._page.content()
                submit_result2 = await self._submit_form()
                self._log("submitted", f"URL after retry: {submit_result2['url']}")

                await self._pause(1, 2)
                response2 = await self._verify_submission(pre_submit_html2, pre_submit_url2)
                self._log("response", f"Retry response: success={response2['success']}, errors={response2.get('errors', [])}")

                if response2["success"]:
                    api_key = await self._extract_api_key(portal)
                    if api_key:
                        self._log("api_key", f"API key obtained on retry")
                        await self._store_credentials(portal_id, api_key)
                        return self._finish(portal_id, True, t0, "Registered with alias email", api_key=api_key)
                    return self._finish(portal_id, True, t0, "Registered with alias but no API key")

                if response2.get("errors"):
                    return self._finish(portal_id, False, t0,
                                        f"Retry failed: {'; '.join(response2['errors'][:3])}",
                                        errors=response2["errors"])
                return self._finish(portal_id, False, t0, "Retry failed — unknown state")

            # Step 8: Handle other errors
            if not response["success"] and response.get("errors"):
                err_msg = "; ".join(response["errors"][:3])
                return self._finish(portal_id, False, t0,
                                    f"Form rejected: {err_msg}",
                                    errors=response["errors"])

            # Step 9: Success — extract and store API key
            if response["success"]:
                api_key = await self._extract_api_key(portal)
                if api_key:
                    self._log("api_key", f"API key obtained and verified")
                    await self._store_credentials(portal_id, api_key)
                    return self._finish(portal_id, True, t0, "Registered successfully", api_key=api_key)
                else:
                    self._log("api_key", "No API key found on account page")
                    return self._finish(portal_id, True, t0, "Registered but no API key found")

            return self._finish(portal_id, False, t0, "Unknown registration state")

        except Exception as e:
            self._log("exception", f"{type(e).__name__}: {e}")
            return self._finish(portal_id, False, t0, str(e))

    # ── Dynamic Form Detection ─────────────────────────────────────────────

    async def _detect_fields(self) -> dict[str, Any]:
        """Dynamically detect form fields using heuristics.

        Returns a dict mapping field_type -> ElementHandle.
        """
        fields: dict[str, Any] = {}

        elements = await self._page.query_selector_all("input, textarea, select")
        for element in elements:
            # Skip hidden elements
            is_hidden = await element.is_hidden()
            if is_hidden:
                continue

            element_type = (await element.get_attribute("type") or "").lower()
            if element_type == "submit" or element_type == "hidden":
                continue

            # Get identifying attributes
            name = (await element.get_attribute("name") or "").lower()
            eid = (await element.get_attribute("id") or "").lower()
            placeholder = (await element.get_attribute("placeholder") or "").lower()
            aria_label = (await element.get_attribute("aria-label") or "").lower()
            label_text = await self._get_label_text(element)

            # Combine all text signals for matching
            combined = f"{name} {eid} {placeholder} {aria_label} {label_text}"

            # Determine field type by pattern matching
            field_type = None
            if element_type == "password":
                field_type = "password"
            elif element_type == "email":
                field_type = "email"
            elif element_type in ("checkbox", "radio"):
                if any(kw in combined for kw in ["agree", "terms", "accept", "consent", "privacy"]):
                    field_type = "agree_terms"
                continue  # Skip other checkboxes/radios for now
            elif element_type == "tel":
                field_type = "phone"
            else:
                # Match against known patterns
                for ftype, patterns in _FIELD_PATTERNS.items():
                    for pattern in patterns:
                        # Check if pattern appears as a whole word in the combined text
                        if re.search(rf"\b{re.escape(pattern)}\b", combined):
                            field_type = ftype
                            break
                    if field_type:
                        break

            if field_type and field_type not in fields:
                fields[field_type] = element

        return fields

    async def _get_label_text(self, element) -> str:
        """Get the label text for a form field using multiple strategies."""
        # Try label[for=id]
        element_id = await element.get_attribute("id") or ""
        if element_id:
            label = await self._page.query_selector(f'label[for="{element_id}"]')
            if label:
                text = await label.inner_text()
                if text:
                    return text.strip().lower()

        # Try parent label
        parent = await self._page.evaluate("""
            el => {
                const parent = el.closest('.form-group, .field, div');
                if (parent) {
                    const label = parent.querySelector('label');
                    return label ? label.innerText.trim() : '';
                }
                return '';
            }
        """, element)
        if parent:
            return parent.lower()

        # Try preceding text node
        prev_text = await self._page.evaluate("""
            el => {
                const prev = el.previousSibling;
                if (prev && prev.nodeType === 3) {
                    return prev.textContent.trim().replace(/[:*]$/, '');
                }
                return '';
            }
        """, element)
        if prev_text:
            return prev_text.lower()

        return ""

    # ── Form Filling ───────────────────────────────────────────────────────

    async def _fill_fields(self, fields: dict[str, Any]) -> None:
        """Fill detected form fields with appropriate values."""
        for field_type, element in fields.items():
            value = self._get_value_for_field(field_type)
            if value is None:
                continue

            name_attr = await element.get_attribute("name") or ""
            id_attr = await element.get_attribute("id") or ""
            selector = f'input[name="{name_attr}"]' if name_attr else f'input#{id_attr}'

            tag_name = await element.evaluate("el => el.tagName.toLowerCase()")
            if tag_name == "select":
                await element.select_option(str(value))
            elif await element.get_attribute("type") in ("checkbox", "radio"):
                if value in (True, "true", "1"):
                    await self._human_click(selector)
            else:
                await self._human_type(selector, str(value))

            await self._pause(0.2, 0.5)

    def _get_value_for_field(self, field_type: str) -> str | bool | None:
        """Get the appropriate value for a detected field type."""
        mapping = {
            "email": _ARIA_EMAIL,
            "password": "Ax" + secrets.token_urlsafe(12) + "7!",
            "first_name": "ARIA",
            "last_name": "Research",
            "full_name": _ARIA_NAME,
            "company": _ARIA_ORG,
            "website": _ARIA_WEBSITE,
            "phone": None,  # Skip phone
            "address": "London, United Kingdom",
            "city": "London",
            "state": None,
            "zip": "EC1A 1BB",
            "country": None,  # Skip country (let default stand)
            "agree_terms": True,
        }
        return mapping.get(field_type)

    # ── Captcha Handling ───────────────────────────────────────────────────

    async def _handle_captcha(self) -> bool:
        """Detect and solve any captcha on the page using 2captcha."""
        # Detect captcha type
        captcha_info = await self._page.evaluate("""
            () => {
                // Check for reCAPTCHA v2
                const recaptcha = document.querySelector('.g-recaptcha');
                if (recaptcha) {
                    return {
                        type: 'recaptcha_v2',
                        sitekey: recaptcha.getAttribute('data-sitekey') || ''
                    };
                }
                // Check for any element with data-sitekey (generic)
                const anyKey = document.querySelector('[data-sitekey]');
                if (anyKey) {
                    return {
                        type: 'recaptcha',
                        sitekey: anyKey.getAttribute('data-sitekey') || ''
                    };
                }
                // Check for Turnstile
                const turnstile = document.querySelector('.cf-turnstile');
                if (turnstile) {
                    return {
                        type: 'turnstile',
                        sitekey: turnstile.getAttribute('data-sitekey') || ''
                    };
                }
                // Check for hCaptcha
                const hcaptcha = document.querySelector('.h-captcha');
                if (hcaptcha) {
                    return {
                        type: 'hcaptcha',
                        sitekey: hcaptcha.getAttribute('data-sitekey') || ''
                    };
                }
                return null;
            }
        """)

        if not captcha_info:
            return True  # No captcha = success

        site_key = captcha_info.get("sitekey", "")
        captcha_type = captcha_info.get("type", "recaptcha_v2")
        if not site_key:
            logger.warning("Captcha detected but no site key found")
            return False

        logger.info("Captcha detected: %s, site key: %s...", captcha_type, site_key[:12])

        api_key = os.environ.get("ARIA_TWOCAPTCHA_API_KEY", "")
        if not api_key:
            logger.warning("ARIA_TWOCAPTCHA_API_KEY not set")
            return False

        import httpx
        page_url = self._page.url

        # Map captcha type to 2captcha method
        method_map = {
            "recaptcha_v2": "userrecaptcha",
            "recaptcha": "userrecaptcha",
            "hcaptcha": "hcaptcha",
            "turnstile": "turnstile",
        }
        method = method_map.get(captcha_type, "userrecaptcha")

        async with httpx.AsyncClient(timeout=30) as client:
            # Submit to 2captcha
            submit = await client.post("https://2captcha.com/in.php", data={
                "key": api_key,
                "method": method,
                "googlekey": site_key,
                "pageurl": page_url,
                "json": 1,
            })
            result = submit.json()
            if result.get("status") != 1:
                logger.warning("2captcha submit failed: %s", result.get("request", ""))
                return False

            request_id = result["request"]
            # Poll for result
            for i in range(_CAPTCHA_MAX_POLLS):
                await asyncio.sleep(_CAPTCHA_POLL_INTERVAL)
                poll = await client.get("https://2captcha.com/res.php", params={
                    "key": api_key, "action": "get", "id": request_id, "json": 1,
                })
                poll_result = poll.json()
                if poll_result.get("status") == 1:
                    token = poll_result["request"]
                    # Inject token into the page
                    await self._page.evaluate(f"""
                        () => {{
                            const ta = document.getElementById('g-recaptcha-response');
                            if (ta) {{
                                ta.style.display = 'block';
                                ta.value = '{token}';
                                ta.style.display = 'none';
                            }}
                            // Trigger callback if available
                            if (typeof ___grecaptcha_cfg !== 'undefined') {{
                                try {{
                                    for (const k in ___grecaptcha_cfg.clients) {{
                                        const client = ___grecaptcha_cfg.clients[k];
                                        if (client && client.callback) {{
                                            client.callback('{token}');
                                        }}
                                    }}
                                }} catch(e) {{}}
                            }}
                        }}
                    """)
                    return True
                elif poll_result.get("status") == 0:
                    continue
                else:
                    logger.warning("2captcha error: %s", poll_result.get("request", ""))
                    return False

        logger.warning("2captcha timeout after %d polls", _CAPTCHA_MAX_POLLS)
        return False

    # ── Form Submission ────────────────────────────────────────────────────

    async def _submit_form(self) -> dict:
        """Find and click the submit button with human-like interaction."""
        result = {"clicked": False, "url": self._page.url}

        submit_selectors = [
            'button[type="submit"]',
            'input[type="submit"]',
            'button:has-text("Register")',
            'button:has-text("Sign Up")',
            'button:has-text("Create Account")',
            'button:has-text("Submit")',
            'button:has-text("Continue")',
            'button:has-text("Get Started")',
            'button:has-text("Join")',
            'button:has-text("Subscribe")',
            'button:has-text("Create")',
            'form button',
            '.submit-btn',
            '#submit',
        ]

        for selector in submit_selectors:
            try:
                btn = await self._page.query_selector(selector)
                if btn and await btn.is_visible():
                    await self._human_click(selector)
                    result["clicked"] = True
                    break
            except Exception:
                continue

        if not result["clicked"]:
            # Fallback: press Enter on the last input
            try:
                last_input = await self._page.query_selector("input:last-of-type")
                if last_input:
                    await last_input.press("Enter")
                    result["clicked"] = True
            except Exception:
                pass

        # Wait for navigation/response
        await self._pause(0.5, 1.0)
        try:
            await self._page.wait_for_load_state("networkidle", timeout=_SUBMIT_TIMEOUT)
        except Exception:
            pass
        await self._pause(1, 2)
        result["url"] = self._page.url

        return result

    # ── Response Reading ───────────────────────────────────────────────────

    async def _read_response(self) -> dict:
        """Read the response page and determine if registration succeeded."""
        url = self._page.url

        # Check for success indicators in URL
        success_url = any(kw in url.lower() for kw in
                          ["success", "welcome", "account", "dashboard", "thanks"])

        # Read validation errors
        errors = await self._page.evaluate("""
            () => {
                const errors = [];
                // ASP.NET validation summary
                document.querySelectorAll('.validation-summary-errors li, .validation-summary-valid li').forEach(el => {
                    const t = el.innerText.trim();
                    if (t && t !== '&#x200E;') errors.push(t);
                });
                // Field-level errors
                document.querySelectorAll('.field-validation-error, span[data-valmsg-for]').forEach(el => {
                    const t = el.innerText.trim();
                    if (t) errors.push(t);
                });
                // Alert/error messages
                document.querySelectorAll('.alert-danger, .alert-error, .error, .message-error, .text-danger').forEach(el => {
                    const t = el.innerText.trim();
                    if (t) errors.push(t);
                });
                return [...new Set(errors)];
            }
        """)

        # Check for success indicators in page content
        page_text = (await self._page.evaluate("document.body.innerText") or "").lower()
        has_api_key = "your api key" in page_text or "api key is" in page_text
        success_text = any(kw in page_text for kw in
                          ["account created", "registration complete", "welcome",
                           "api key", "your api key", "dashboard"])

        # Only report success if the URL changed to a success page OR
        # the page explicitly shows an API key. A re-rendered form with
        # "email already in use" is NOT success even if it contains
        # generic welcome text.
        is_success = False
        if success_url and (success_text or has_api_key):
            is_success = True
        elif has_api_key:
            is_success = True
        elif "/register/success" in url.lower():
            is_success = True

        return {
            "success": is_success,
            "errors": errors,
            "url": url,
        }

    async def _verify_submission(self, pre_html: str, pre_url: str) -> dict:
        """Robust verification: wait for URL change, error, or timeout.

        Compares page state before and after submission to detect:
        - URL changed to success page -> real success
        - Error messages (including duplicate email) -> failure
        - Form disappeared + success text appeared -> AJAX success
        - No change after timeout -> failure

        Returns:
            {"success": bool, "errors": [str], "dup_email": bool, "url": str}
        """
        success_indicators = ["welcome", "dashboard", "success", "registration complete",
                             "account created", "api key", "your api key"]
        duplicate_patterns = ["already registered", "already taken", "email exists",
                             "already in use", "email address is already"]
        error_indicators = ["error", "invalid", "failed", "try again", "incorrect"]

        timeout = 20  # seconds to wait for a change
        start = time.time()

        while time.time() - start < timeout:
            current_url = self._page.url
            current_html = await self._page.content()
            lower_html = current_html.lower()

            # 1. Check if URL changed to a success route
            if current_url != pre_url:
                for ind in success_indicators:
                    if ind.lower() in current_url.lower():
                        return {"success": True, "errors": [], "dup_email": False, "url": current_url}

            # 2. Check for duplicate email patterns
            for dup in duplicate_patterns:
                if dup in lower_html:
                    return {"success": False, "errors": [dup], "dup_email": True, "url": current_url}

            # 3. Check for other error messages
            for err in error_indicators:
                if err in lower_html:
                    # Only flag as error if it's NEW content (not in pre-submit)
                    if err not in pre_html.lower():
                        return {"success": False, "errors": [f"Error: {err}"], "dup_email": False, "url": current_url}

            # 4. Check if form disappeared and success text appeared (AJAX)
            if any(ind.lower() in lower_html for ind in success_indicators):
                # Check if submit button is gone
                submit_btn = await self._page.query_selector(
                    "button[type='submit'], input[type='submit']"
                )
                submit_gone = not submit_btn or not await submit_btn.is_visible()
                if submit_gone:
                    # Verify content actually changed (not cached)
                    if len(current_html) != len(pre_html) or current_html != pre_html:
                        return {"success": True, "errors": [], "dup_email": False, "url": current_url}

            await asyncio.sleep(1)

        # Timeout — no clear signal
        return {"success": False, "errors": ["Verification timeout"], "dup_email": False, "url": self._page.url}

    # ── API Key Extraction ─────────────────────────────────────────────────

    async def _extract_api_key(self, portal) -> str | None:
        """Navigate to the account page and extract the API key."""
        # Try the portal's API key path
        if portal.api_key_path:
            try:
                acct_url = self._build_url(portal.url, portal.api_key_path)
                await self._safe_goto(acct_url)
                await self._pause(1, 2)
            except Exception:
                pass

        # Try login if redirected to login page
        if portal.login_path and "login" in self._page.url.lower():
            try:
                await self._handle_login(portal)
                if portal.api_key_path:
                    acct_url = self._build_url(portal.url, portal.api_key_path)
                    await self._safe_goto(acct_url)
                    await self._pause(1, 2)
            except Exception:
                pass

        # Extract API key using portal's regex
        page_text = await self._page.content()
        if portal.api_key_regex:
            for m in re.finditer(portal.api_key_regex, page_text):
                candidate = (m.group(1) if m.groups() else m.group(0)).strip()
                if await self._verify_api_key(portal, candidate):
                    return candidate

        # Fallback: look for 32-char hex keys (common API key format)
        for m in re.finditer(r"\b[0-9a-f]{32}\b", page_text):
            if await self._verify_api_key(portal, m.group()):
                return m.group()

        return None

    async def _handle_login(self, portal) -> None:
        """Log into an existing account to access API key page."""
        login_url = self._build_url(portal.url, portal.login_path or "/login")
        await self._safe_goto(login_url)
        await self._pause(1, 2)

        fields = await self._detect_fields()
        for ftype, element in fields.items():
            name_attr = await element.get_attribute("name") or ""
            selector = f'input[name="{name_attr}"]'
            if ftype == "email":
                await self._human_type(selector, _ARIA_EMAIL)
            elif ftype == "password":
                try:
                    from aria_service.intel.portal_registry import get_credential
                    cred = await get_credential(portal.id) or {}
                    pwd = cred.get("password", "")
                    if pwd:
                        await self._human_type(selector, pwd)
                except Exception:
                    pass

        await self._submit_form()

    async def _verify_api_key(self, portal, key: str) -> bool:
        """Verify an API key by making a test request."""
        if not portal.api_key_test_url:
            return True
        test_url = portal.api_key_test_url.replace("{key}", key)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(test_url)
                return r.status_code == 200
        except Exception:
            return False

    async def _store_credentials(self, portal_id: str, api_key: str) -> None:
        """Store credentials in the vault."""
        try:
            from aria_service.intel.portal_registry import store_credential, mark_registered
            await store_credential(portal_id, {
                "email": _ARIA_EMAIL,
                "api_key": api_key,
            })
            await mark_registered(portal_id)
            self._log("stored", f"Credentials stored for {portal_id}")
        except Exception as e:
            logger.warning("Failed to store credentials for %s: %s", portal_id, e)

    # ── Helpers ────────────────────────────────────────────────────────────

    async def _safe_goto(self, url: str) -> None:
        """Navigate to a URL with timeout handling."""
        try:
            await self._page.goto(url, wait_until="networkidle", timeout=_PAGE_TIMEOUT)
        except Exception:
            try:
                await self._page.goto(url, wait_until="load", timeout=_PAGE_TIMEOUT)
            except Exception:
                await self._page.goto(url, timeout=_PAGE_TIMEOUT)

    def _build_url(self, base: str, path: str) -> str:
        base = base.rstrip("/")
        path = path.lstrip("/")
        return f"{base}/{path}"

    def _log(self, step: str, message: str) -> None:
        entry = {"step": step, "message": message, "time": time.time()}
        self._diagnostics.append(entry)
        logger.info("[portal_agent] %s: %s", step, message)

    def _finish(self, portal_id: str, success: bool, t0: float,
                message: str, **kwargs) -> dict:
        result = {
            "success": success,
            "portal_id": portal_id,
            "message": message,
            "duration_s": time.time() - t0,
            "diagnostics": self._diagnostics,
            **kwargs,
        }
        try:
            from aria_service.intel.engine_wiring import wire_success, wire_failure
            if success:
                wire_success(
                    module="portal_agent",
                    summary=f"Registered for {portal_id}: {message[:200]}",
                    source_id=f"portal_agent:{portal_id}",
                )
            else:
                wire_failure(
                    module="portal_agent",
                    detail=f"Registration failed for {portal_id}: {message[:300]}",
                    gap_type="source_failure",
                    source=f"portal_agent:{portal_id}",
                )
        except Exception:
            pass

        # Record to knowledge base
        try:
            api_key = kwargs.get("api_key", "")
            self._knowledge.update_site(
                domain=self._domain,
                success=success,
                duration=time.time() - t0,
                config={"portal_id": portal_id},
                error=message if not success else None,
            )
            self._knowledge.record_attempt(
                domain=self._domain,
                portal_id=portal_id,
                success=success,
                duration=time.time() - t0,
                error=message if not success else None,
                captcha_solved=any(s.get("step") == "captcha" and "solved" in s.get("message", "").lower()
                                   for s in self._diagnostics),
                email_used=_ARIA_EMAIL,
                api_key_obtained=bool(api_key),
                config={"portal_id": portal_id},
                diagnostics=self._diagnostics,
            )
        except Exception as e:
            logger.debug("Failed to record knowledge: %s", e)

        return result
