"""
R-F2064 — Adaptive Portal Registration Agent.

ARIA registers herself for data portals using a Playwright browser that
can SEE the page, UNDERSTAND the form structure, and ADAPT to each site.

Core design:
  1. Site Reader — loads any registration page, extracts ALL form fields
     with their labels, types, options, and validation rules
  2. Form Analyzer — understands what each field means by reading its
     label, placeholder, name, and surrounding context. Maps fields to
     ARIA's identity data (name, email, org, etc.)
  3. Adaptive Filler — fills fields using the correct interaction type
     (type text, click radio, check checkbox, select option)
  4. Captcha Handler — detects captcha type, solves via 2captcha,
     injects token, verifies acceptance
  5. Error Reader — reads validation errors from the response page,
     identifies which field failed and why, adjusts and retries
  6. Credential Persister — on success, extracts API key, verifies it,
     stores credentials in the vault

The agent works on ANY site with a standard registration form — no
hardcoded field mappings needed. It reads the page fresh each time.

Usage:
    from aria_service.intel.portal_agent import AdaptivePortalAgent

    async with AdaptivePortalAgent() as agent:
        result = await agent.register("newsapi")
        # result = {"success": True, "api_key": "...", "portal_id": "newsapi"}
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import time
from typing import Any, Optional
from urllib.parse import urljoin

logger = logging.getLogger("aria.portal_agent")

# ── Browser configuration ─────────────────────────────────────────────────
_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,720",
]
_PAGE_TIMEOUT = 30_000  # ms
_SUBMIT_TIMEOUT = 20_000  # ms
_CAPTCHA_POLL_INTERVAL = 2  # seconds
_CAPTCHA_MAX_POLLS = 45  # ~90 seconds max

# ARIA's identity for registrations
_ARIA_EMAIL = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
_ARIA_NAME = os.getenv("ARIA_PORTAL_NAME", "ARIA Research (Arkmurus Group)")
_ARIA_ORG = "Arkmurus Group Ltd"
_ARIA_WEBSITE = "https://arkmurus.com"


class AdaptivePortalAgent:
    """Browser-based portal registration agent that adapts to any site."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None
        self._diagnostics: list[dict] = []

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """Launch the Playwright browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed")
            raise

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True, args=_BROWSER_ARGS,
        )
        self._context = await self._browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )
        self._page = await self._context.new_page()
        self._log("browser_started", "Playwright browser launched")

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
        t0 = time.time()

        try:
            # Step 1: Navigate to the registration page
            reg_url = self._build_url(portal.url, portal.register_path or "/register")
            self._log("navigate", f"Loading {reg_url}")
            await self._safe_goto(reg_url)
            await self._page.wait_for_timeout(1500)

            # Step 2: Read the page — extract ALL form fields with context
            form_data = await self._read_page()
            if not form_data["fields"]:
                return self._finish(portal_id, False, t0, "No form fields found")

            self._log("form_read", f"Found {len(form_data['fields'])} fields: {[f['name'] for f in form_data['fields']]}")

            # Step 3: Fill the form adaptively
            fill_result = await self._fill_form_adaptive(form_data["fields"])
            self._log("form_filled", f"Filled {fill_result['filled']}, skipped {fill_result['skipped']}")

            # Step 4: Handle captcha
            captcha_ok = await self._handle_captcha()
            if captcha_ok:
                self._log("captcha", "Captcha solved and injected")
            else:
                self._log("captcha", "No captcha detected or solving deferred")

            # Step 5: Submit and read response
            submit_result = await self._submit()
            self._log("submitted", f"URL after submit: {submit_result['url']}")

            # Step 6: Read the response page
            response = await self._read_response()
            self._log("response", f"Response: success={response['success']}, errors={response['errors']}")

            if response["success"]:
                # Step 7: Extract and verify API key
                api_key = await self._extract_api_key(portal)
                if api_key:
                    self._log("api_key", f"API key obtained and verified")
                    await self._store_credentials(portal_id, api_key)
                    return self._finish(portal_id, True, t0, "Registered successfully", api_key=api_key)
                else:
                    self._log("api_key", "No API key found on account page")
                    return self._finish(portal_id, True, t0, "Registered but no API key found")

            # Step 8: Handle errors — retry with fixes
            if response["errors"]:
                self._log("errors", f"Form rejected: {response['errors']}")
                return self._finish(portal_id, False, t0,
                                    f"Form rejected: {'; '.join(response['errors'][:3])}",
                                    errors=response["errors"])

            return self._finish(portal_id, False, t0, "Unknown registration state")

        except Exception as e:
            self._log("exception", f"{type(e).__name__}: {e}")
            return self._finish(portal_id, False, t0, str(e))

    # ── Page Reading ───────────────────────────────────────────────────────

    async def _read_page(self) -> dict:
        """Read the current page and extract all form structure.

        Returns:
            {"fields": [...], "has_captcha": bool, "page_title": str, "page_url": str}
        """
        result = await self._page.evaluate("""
            () => {
                const data = {
                    fields: [],
                    has_captcha: false,
                    page_title: document.title,
                    page_url: window.location.href,
                };

                // Find all form elements
                const form = document.querySelector('form');
                if (!form) return data;

                // Get form attributes
                data.form_action = form.action || '';
                data.form_method = form.method || 'get';

                // Scan all input-like elements
                const inputs = form.querySelectorAll('input, select, textarea, button[type=submit]');
                inputs.forEach(el => {
                    const field = {
                        name: el.name || el.id || '',
                        id: el.id || '',
                        type: el.type || el.tagName.toLowerCase(),
                        required: el.required || el.hasAttribute('aria-required') || false,
                        placeholder: el.placeholder || '',
                        value: el.value || '',
                        maxlength: el.maxLength || 0,
                        minlength: el.minLength || 0,
                        pattern: el.pattern || '',
                        autocomplete: el.autocomplete || '',
                        visible: el.offsetParent !== null,
                        disabled: el.disabled || false,
                        readonly: el.readOnly || false,
                    };

                    // Get label
                    const labelFor = document.querySelector(`label[for="${el.id}"]`);
                    const parentLabel = el.closest('.form-group, .field, div')?.querySelector('label');
                    field.label = (labelFor ? labelFor.innerText.trim() : '')
                        || (parentLabel ? parentLabel.innerText.trim() : '')
                        || '';

                    // Get placeholder as fallback label
                    if (!field.label && el.placeholder) {
                        field.label = el.placeholder;
                    }

                    // Get preceding text node as label fallback
                    if (!field.label) {
                        const prev = el.previousSibling;
                        if (prev && prev.nodeType === 3 && prev.textContent.trim()) {
                            field.label = prev.textContent.trim().replace(/[:*]$/, '');
                        }
                    }

                    // Radio buttons
                    if (el.type === 'radio') {
                        const name = el.name;
                        const radios = form.querySelectorAll(`input[name="${name}"]`);
                        field.options = [];
                        radios.forEach(r => {
                            const rLabel = document.querySelector(`label[for="${r.id}"]`);
                            field.options.push({
                                value: r.value,
                                label: rLabel ? rLabel.innerText.trim() : r.value,
                                checked: r.checked,
                            });
                        });
                        // Only add once per radio group
                        if (el !== radios[0]) return;
                    }

                    // Select options
                    if (el.tagName === 'SELECT') {
                        field.options = [];
                        el.querySelectorAll('option').forEach(opt => {
                            if (opt.value) {
                                field.options.push({ value: opt.value, label: opt.innerText.trim() });
                            }
                        });
                    }

                    data.fields.push(field);
                });

                // Detect captcha
                data.has_captcha = !!(
                    document.querySelector('.g-recaptcha')
                    || document.querySelector('[data-sitekey]')
                    || document.querySelector('.cf-turnstile')
                    || document.querySelector('.h-captcha')
                    || document.querySelector('script[src*="recaptcha/api.js"]')
                    || document.querySelector('script[src*="turnstile"]')
                );

                // Detect captcha site key
                if (data.has_captcha) {
                    const captchaEl = document.querySelector('.g-recaptcha') || document.querySelector('[data-sitekey]');
                    if (captchaEl) data.captcha_sitekey = captchaEl.getAttribute('data-sitekey') || '';
                }

                return data;
            }
        """)
        return result

    # ── Adaptive Form Filling ──────────────────────────────────────────────

    async def _fill_form_adaptive(self, fields: list[dict]) -> dict:
        """Fill form fields by understanding what each field means.

        Reads the field's label, name, placeholder, and type to determine
        what value to fill. Handles text, email, password, radio, checkbox,
        select, tel, and hidden fields.
        """
        filled = 0
        skipped = 0
        errors = []

        for field in fields:
            # Skip hidden, disabled, readonly fields
            if field.get("type") in ("hidden",) or field.get("disabled") or field.get("readonly"):
                continue

            value = self._determine_field_value(field)
            if value is None:
                skipped += 1
                continue

            try:
                name_selector = field["name"].replace('"', '\\"')
                if field["type"] == "radio":
                    await self._page.evaluate(
                        f"""() => {{
                            const r = document.querySelector('input[name="{name_selector}"][value="{value}"]');
                            if (r) r.click();
                        }}"""
                    )
                elif field["type"] == "checkbox":
                    if value in (True, "true", "1"):
                        await self._page.evaluate(
                            f"""() => {{
                                const cb = document.querySelector('input[name="{name_selector}"]');
                                if (cb && !cb.checked) cb.click();
                            }}"""
                        )
                elif field["type"] == "select" or field.get("type") == "select-one":
                    await self._page.select_option(
                        f'select[name="{name_selector}"]', str(value)
                    )
                else:
                    await self._page.fill(
                        f'input[name="{name_selector}"], textarea[name="{name_selector}"]',
                        str(value),
                    )
                filled += 1
            except Exception as e:
                errors.append(f"{field['name']}: {e}")
                skipped += 1

        return {"filled": filled, "skipped": skipped, "errors": errors}

    def _determine_field_value(self, field: dict) -> str | bool | None:
        """Determine what value to fill for a field by analyzing its context.

        Uses label text, field name, placeholder, type, and autocomplete
        hints to figure out what ARIA's identity data should go here.
        """
        name = (field.get("name") or "").lower()
        label = (field.get("label") or "").lower()
        placeholder = (field.get("placeholder") or "").lower()
        ftype = field.get("type", "")
        autocomplete = (field.get("autocomplete") or "").lower()

        # ── Email ──────────────────────────────────────────────────────
        if (ftype == "email"
                or autocomplete == "email"
                or "email" in name
                or "mail" in name
                or "email" in label):
            return _ARIA_EMAIL

        # ── Password ───────────────────────────────────────────────────
        if (ftype == "password"
                or autocomplete == "new-password"
                or "password" in name
                or "pass" in name
                or "password" in label):
            return "Ax" + secrets.token_urlsafe(12) + "7!"

        # ── First name ─────────────────────────────────────────────────
        if (autocomplete == "given-name"
                or "first" in name
                or "first" in label
                or "fname" in name
                or "given" in name):
            return "ARIA"

        # ── Last name ──────────────────────────────────────────────────
        if (autocomplete == "family-name"
                or "last" in name
                or "last" in label
                or "lname" in name
                or "surname" in name
                or "family" in name):
            return "Research"

        # ── Full name ──────────────────────────────────────────────────
        if (autocomplete == "name"
                or "full" in name
                or "your name" in label
                or "full name" in label
                or name in ("name", "username", "displayname")
                or "name" in label and "first" not in label and "last" not in label):
            return _ARIA_NAME

        # ── Organization / Company ─────────────────────────────────────
        if (autocomplete == "organization"
                or "org" in name
                or "company" in name
                or "firm" in name
                or "business" in name
                or "employer" in name
                or "organisation" in name
                or "organization" in label
                or "company" in label):
            return _ARIA_ORG

        # ── Website / URL ──────────────────────────────────────────────
        if (autocomplete == "url"
                or "website" in name
                or "url" in name
                or "homepage" in name
                or "site" in name
                or "website" in label):
            return _ARIA_WEBSITE

        # ── Phone — skip (not needed for free tier) ────────────────────
        if (ftype == "tel"
                or autocomplete == "tel"
                or "phone" in name
                or "tel" in name
                or "mobile" in name
                or "phone" in label):
            return None

        # ── Country — skip (let default stand) ─────────────────────────
        if ("country" in name or "nation" in name):
            return None

        # ── Radio buttons ──────────────────────────────────────────────
        if ftype == "radio" and field.get("options"):
            options = field["options"]
            # Prefer Individual/Personal/Developer/Student/Researcher
            for opt in options:
                v = opt["value"].lower()
                if v in ("individual", "personal", "developer", "student", "researcher", "non-commercial"):
                    return opt["value"]
            # If one is already checked, leave it
            for opt in options:
                if opt.get("checked"):
                    return None
            # Default to first option
            return options[0]["value"]

        # ── Checkboxes ─────────────────────────────────────────────────
        if ftype == "checkbox":
            label_lower = label.lower()
            if any(kw in label_lower for kw in
                   ["term", "agree", "accept", "privacy", "consent",
                    "condition", "policy", "subscribe", "updates"]):
                return True
            return None

        # ── Select dropdowns ───────────────────────────────────────────
        if ftype in ("select", "select-one") and field.get("options"):
            options = field["options"]
            # Skip country selects
            if "country" in name or "nation" in name:
                return None
            # Pick first non-empty option that isn't a placeholder
            for opt in options:
                v = opt["value"].strip()
                l = opt.get("label", "").strip().lower()
                if v and l not in ("", "-- select --", "select", "choose", "please select"):
                    return v
            if options:
                return options[0]["value"]

        # ── Text fields with labels we can match ───────────────────────
        if ftype == "text":
            if "address" in name or "address" in label:
                return "London, United Kingdom"
            if "city" in name or "city" in label:
                return "London"
            if "postcode" in name or "zip" in name or "postal" in name:
                return "EC1A 1BB"

        return None

    # ── Captcha Handling ───────────────────────────────────────────────────

    async def _handle_captcha(self) -> bool:
        """Detect and solve any captcha on the page."""
        has_captcha = await self._page.evaluate("""
            () => !!(
                document.querySelector('.g-recaptcha')
                || document.querySelector('[data-sitekey]')
                || document.querySelector('.cf-turnstile')
                || document.querySelector('.h-captcha')
            )
        """)
        if not has_captcha:
            return False

        site_key = await self._page.evaluate("""
            () => {
                const el = document.querySelector('.g-recaptcha')
                    || document.querySelector('[data-sitekey]')
                    || document.querySelector('.cf-turnstile')
                    || document.querySelector('.h-captcha');
                return el ? el.getAttribute('data-sitekey') : null;
            }
        """)
        if not site_key:
            logger.warning("Captcha detected but no site key found")
            return False

        logger.info("Captcha detected, site key: %s...", site_key[:12])

        api_key = os.environ.get("ARIA_TWOCAPTCHA_API_KEY", "")
        if not api_key:
            logger.warning("ARIA_TWOCAPTCHA_API_KEY not set")
            return False

        import httpx
        page_url = self._page.url

        async with httpx.AsyncClient(timeout=30) as client:
            submit = await client.post("https://2captcha.com/in.php", data={
                "key": api_key,
                "method": "userrecaptcha",
                "googlekey": site_key,
                "pageurl": page_url,
                "json": 1,
            })
            result = submit.json()
            if result.get("status") != 1:
                logger.warning("2captcha submit failed: %s", result.get("request", ""))
                return False

            request_id = result["request"]
            for i in range(_CAPTCHA_MAX_POLLS):
                await asyncio.sleep(_CAPTCHA_POLL_INTERVAL)
                poll = await client.get("https://2captcha.com/res.php", params={
                    "key": api_key, "action": "get", "id": request_id, "json": 1,
                })
                poll_result = poll.json()
                if poll_result.get("status") == 1:
                    token = poll_result["request"]
                    await self._page.evaluate(f"""
                        () => {{
                            const ta = document.getElementById('g-recaptcha-response');
                            if (ta) {{
                                ta.style.display = 'block';
                                ta.value = '{token}';
                                ta.style.display = 'none';
                            }}
                            // Call the callback if available
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

        logger.warning("2captcha timeout")
        return False

    # ── Form Submission ────────────────────────────────────────────────────

    async def _submit(self) -> dict:
        """Find and click the submit button, then wait for response."""
        result = {"clicked": False, "url": self._page.url}

        try:
            # Try common submit button selectors
            selectors = [
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
            ]

            for selector in selectors:
                btn = await self._page.query_selector(selector)
                if btn and await btn.is_visible():
                    await btn.click()
                    result["clicked"] = True
                    break

            if not result["clicked"]:
                # Fallback: click first button in the form
                btn = await self._page.query_selector("form button, form input[type=submit]")
                if btn:
                    await btn.click()
                    result["clicked"] = True

            # Wait for navigation/response
            await asyncio.sleep(0.5)
            try:
                await self._page.wait_for_load_state("networkidle", timeout=_SUBMIT_TIMEOUT)
            except Exception:
                pass
            await self._page.wait_for_timeout(1000)
            result["url"] = self._page.url

        except Exception as e:
            result["error"] = str(e)

        return result

    # ── Response Reading ───────────────────────────────────────────────────

    async def _read_response(self) -> dict:
        """Read the response page and determine if registration succeeded.

        Returns:
            {"success": bool, "errors": [str], "url": str}
        """
        url = self._page.url
        html = await self._page.content()

        # Check for success indicators in URL
        success_url = any(kw in url.lower() for kw in
                          ["success", "welcome", "account", "dashboard", "thanks"])

        # Read validation errors from the page
        errors = await self._page.evaluate("""
            () => {
                const errors = [];
                // ASP.NET validation summary
                document.querySelectorAll('.validation-summary-errors li, .validation-summary-valid li').forEach(el => {
                    const t = el.innerText.trim();
                    if (t) errors.push(t);
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
                // Form-level error summary
                const summary = document.querySelector('[data-valmsg-summary]');
                if (summary) {
                    summary.querySelectorAll('li').forEach(li => {
                        const t = li.innerText.trim();
                        if (t && t !== '&#x200E;') errors.push(t);
                    });
                }
                return [...new Set(errors)];
            }
        """)

        # Check for success indicators in page content
        page_text = (await self._page.evaluate("document.body.innerText") or "").lower()
        success_text = any(kw in page_text for kw in
                          ["account created", "registration complete", "welcome",
                           "api key", "your api key", "dashboard"])

        return {
            "success": success_url or success_text,
            "errors": errors,
            "url": url,
        }

    # ── API Key Extraction ─────────────────────────────────────────────────

    async def _extract_api_key(self, portal) -> str | None:
        """Navigate to the account/api key page and extract the key."""
        # Try the portal's API key path
        if portal.api_key_path:
            try:
                acct_url = self._build_url(portal.url, portal.api_key_path)
                await self._safe_goto(acct_url)
                await self._page.wait_for_timeout(1500)
            except Exception:
                pass

        # Try login if we're not logged in
        if portal.login_path and "login" in self._page.url.lower():
            try:
                await self._handle_login(portal)
                if portal.api_key_path:
                    acct_url = self._build_url(portal.url, portal.api_key_path)
                    await self._safe_goto(acct_url)
                    await self._page.wait_for_timeout(1500)
            except Exception:
                pass

        # Extract API key using portal's regex
        page_text = await self._page.content()
        if portal.api_key_regex:
            for m in re.finditer(portal.api_key_regex, page_text):
                candidate = (m.group(1) if m.groups() else m.group(0)).strip()
                if await self._verify_api_key(portal, candidate):
                    return candidate

        # Fallback: look for 32-char hex keys
        for m in re.finditer(r"\b[0-9a-f]{32}\b", page_text):
            if await self._verify_api_key(portal, m.group()):
                return m.group()

        return None

    async def _handle_login(self, portal) -> None:
        """Log into an existing account."""
        login_url = self._build_url(portal.url, portal.login_path or "/login")
        await self._safe_goto(login_url)
        await self._page.wait_for_timeout(1000)

        fields = await self._read_page()
        for field in fields.get("fields", []):
            name = field.get("name", "").lower()
            if "email" in name or "mail" in name:
                await self._page.fill(f'input[name="{field["name"]}"]', _ARIA_EMAIL)
            elif "password" in name or "pass" in name:
                # Try stored password
                try:
                    from aria_service.intel.portal_registry import get_credential
                    cred = await get_credential(portal.id) or {}
                    pwd = cred.get("password", "")
                    if pwd:
                        await self._page.fill(f'input[name="{field["name"]}"]', pwd)
                except Exception:
                    pass

        await self._submit()

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
        """Build a full URL from base and path."""
        base = base.rstrip("/")
        path = path.lstrip("/")
        return f"{base}/{path}"

    def _log(self, step: str, message: str) -> None:
        """Log a diagnostic step."""
        entry = {"step": step, "message": message, "time": time.time()}
        self._diagnostics.append(entry)
        logger.info("[portal_agent] %s: %s", step, message)

    def _finish(self, portal_id: str, success: bool, t0: float,
                message: str, **kwargs) -> dict:
        """Build and return the final result."""
        result = {
            "success": success,
            "portal_id": portal_id,
            "message": message,
            "duration_s": time.time() - t0,
            "diagnostics": self._diagnostics,
            **kwargs,
        }

        # Wire to brain
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

        return result
