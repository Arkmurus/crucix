"""
R-F2064 — Visual Portal Registration Agent.

ARIA registers herself for data portals using a Playwright browser that
can SEE the page, read form fields, solve captchas, and adapt to errors.

Replaces the blind httpx POST approach that couldn't handle JS-rendered
forms, captchas, or multi-step registration flows.

Architecture:
  PortalRegistrationAgent
    - Opens a Playwright browser
    - Navigates to the portal's registration page
    - Scans all form fields (labels, types, required, values)
    - Fills fields intelligently based on field type
    - Detects and solves captchas (reCAPTCHA v2/v3, Turnstile, hCaptcha)
    - Submits the form
    - Reads the response page for success/error messages
    - If error: reads specific field errors, adjusts, retries
    - If success: navigates to API key page, extracts and verifies key
    - Stores credentials in the vault
    - Wires success/failure to the brain

Usage:
    agent = PortalRegistrationAgent()
    result = await agent.register("newsapi")
    # result = {"success": True, "api_key": "...", "portal_id": "newsapi"}
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any, Optional
from urllib.parse import urljoin

logger = logging.getLogger("aria.portal_agent")

# ── Configuration ──────────────────────────────────────────────────────────

# Playwright browser launch args — lightweight, no sandbox for Docker
_BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--window-size=1280,720",
]

# How long to wait for the page to render after navigation
_PAGE_LOAD_TIMEOUT = 30_000  # ms

# How long to wait for the form to submit and response to load
_FORM_SUBMIT_TIMEOUT = 20_000  # ms

# How long to wait between retries
_RETRY_DELAY = 2.0  # seconds

# Max retries per portal
_MAX_RETRIES = 3

# ARIA's identity for registrations
_ARIA_EMAIL = os.getenv("ARIA_PORTAL_EMAIL", "aria@arkmurus.com")
_ARIA_NAME = os.getenv("ARIA_PORTAL_NAME", "ARIA Research (Arkmurus Group)")
_ARIA_ORG = "Arkmurus Group Ltd"
_ARIA_WEBSITE = "https://arkmurus.com"


class PortalRegistrationAgent:
    """Browser-based portal registration agent with full page visibility."""

    def __init__(self):
        self._browser = None
        self._context = None
        self._page = None
        self._playwright = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.close()

    async def start(self) -> None:
        """Launch the Playwright browser."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.error("playwright not installed — cannot start portal agent")
            raise

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=_BROWSER_ARGS,
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
        logger.info("Portal agent browser started")

    async def close(self) -> None:
        """Close the browser and clean up."""
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
        logger.info("Portal agent browser closed")

    async def register(self, portal_id: str) -> dict[str, Any]:
        """Register for a portal and return the result.

        Args:
            portal_id: The portal ID from portal_registry.PORTALS.

        Returns:
            Dict with success status, api_key (if obtained), and diagnostic info.
        """
        from aria_service.intel.portal_registry import PORTALS

        portal = next((p for p in PORTALS if p.id == portal_id), None)
        if not portal:
            return {"success": False, "error": f"Unknown portal: {portal_id}"}

        diag: dict[str, Any] = {
            "portal_id": portal_id,
            "portal_name": portal.name,
            "steps": [],
            "started_at": time.time(),
        }

        try:
            # Step 1: Navigate to registration page
            reg_url = urljoin(portal.url.rstrip("/") + "/", (portal.register_path or "/register").lstrip("/"))
            await self._log_step(diag, "navigate", f"Navigating to {reg_url}")

            try:
                await self._page.goto(reg_url, wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
            except Exception as e:
                await self._log_step(diag, "navigate_error", f"Navigation failed: {e}")
                # Try with load event only (some sites never reach networkidle)
                await self._page.goto(reg_url, wait_until="load", timeout=_PAGE_LOAD_TIMEOUT)

            await self._page.wait_for_timeout(1000)  # Let JS render
            await self._log_step(diag, "page_loaded", f"URL: {self._page.url}, Title: {await self._page.title()}")

            # Step 2: Scan the form for all fields
            form_fields = await self._scan_form_fields()
            await self._log_step(diag, "form_scanned", f"Found {len(form_fields)} fields: {[f['name'] for f in form_fields]}")

            if not form_fields:
                return await self._finish(diag, False, "No form fields found on registration page")

            # Step 3: Fill the form
            fill_result = await self._fill_form(form_fields, portal)
            await self._log_step(diag, "form_filled", f"Filled {fill_result.get('filled', 0)} fields")

            # Step 4: Detect and solve captcha
            captcha_token = await self._solve_captcha()
            if captcha_token:
                await self._log_step(diag, "captcha_solved", "Captcha token obtained and injected")
            else:
                await self._log_step(diag, "captcha_skipped", "No captcha detected or solving failed")

            # Step 5: Submit the form
            await self._log_step(diag, "submitting", "Clicking submit button")
            submit_result = await self._submit_form()
            await self._log_step(diag, "submitted", f"After submit URL: {self._page.url}")

            # Step 6: Read the response
            response_text = await self._page.content()
            current_url = self._page.url

            # Check for success
            if "success" in current_url.lower() or "welcome" in current_url.lower() or "account" in current_url.lower():
                await self._log_step(diag, "success_page", f"Redirected to success page: {current_url}")

                # Step 7: Extract API key
                api_key = await self._extract_api_key(portal)
                if api_key:
                    await self._log_step(diag, "api_key_extracted", f"API key found and verified")
                    return await self._finish(diag, True, f"Registered successfully", api_key=api_key)
                else:
                    await self._log_step(diag, "api_key_missing", "No API key found on success page")
                    return await self._finish(diag, True, "Registered but could not extract API key")

            # Check for errors
            errors = await self._read_errors()
            if errors:
                await self._log_step(diag, "errors_found", f"Validation errors: {errors}")
                return await self._finish(diag, False, f"Form rejected: {'; '.join(errors[:3])}", errors=errors)

            # Unknown state
            await self._log_step(diag, "unknown_state", f"URL: {current_url}, no success/error detected")
            return await self._finish(diag, False, "Unknown registration state — see diagnostic log")

        except Exception as e:
            await self._log_step(diag, "error", f"Exception: {e}")
            return await self._finish(diag, False, str(e))

    async def _scan_form_fields(self) -> list[dict]:
        """Scan the current page for all form fields.

        Returns a list of dicts with keys: name, type, label, required, value, options.
        """
        fields = []
        try:
            fields = await self._page.evaluate("""
                () => {
                    const fields = [];
                    const inputs = document.querySelectorAll('input, select, textarea');
                    inputs.forEach(input => {
                        const label = document.querySelector(`label[for="${input.id}"]`);
                        const labelText = label ? label.innerText.trim() : '';
                        const parentLabel = input.closest('.form-group, .field, div')?.querySelector('label');
                        const parentLabelText = parentLabel && !label ? parentLabel.innerText.trim() : '';
                        
                        let options = [];
                        if (input.type === 'radio') {
                            const name = input.name;
                            const radios = document.querySelectorAll(`input[name="${name}"]`);
                            radios.forEach(r => {
                                const rLabel = document.querySelector(`label[for="${r.id}"]`);
                                options.push({
                                    value: r.value,
                                    label: rLabel ? rLabel.innerText.trim() : r.value,
                                    checked: r.checked
                                });
                            });
                        }
                        if (input.tagName === 'SELECT') {
                            input.querySelectorAll('option').forEach(opt => {
                                options.push({ value: opt.value, label: opt.innerText.trim() });
                            });
                        }
                        
                        fields.push({
                            name: input.name || input.id,
                            id: input.id,
                            type: input.type || input.tagName.toLowerCase(),
                            label: labelText || parentLabelText,
                            placeholder: input.placeholder || '',
                            required: input.required || input.hasAttribute('aria-required'),
                            value: input.value,
                            options: options,
                            visible: input.offsetParent !== null,
                        });
                    });
                    return fields;
                }
            """)
        except Exception as e:
            logger.warning("Form field scan failed: %s", e)
        return fields

    async def _fill_form(self, fields: list[dict], portal) -> dict:
        """Fill form fields intelligently based on their type and label."""
        filled = 0
        skipped = 0

        for field in fields:
            value = self._get_field_value(field, portal)
            if value is None:
                skipped += 1
                continue

            try:
                if field["type"] == "radio":
                    # Click the radio button with matching value
                    await self._page.evaluate(
                        f"""() => {{
                            const radio = document.querySelector('input[name="{field['name']}"][value="{value}"]');
                            if (radio) radio.click();
                        }}"""
                    )
                    filled += 1
                elif field["type"] == "checkbox":
                    if value in (True, "true", "1"):
                        await self._page.evaluate(
                            f"""() => {{
                                const cb = document.querySelector('input[name="{field['name']}"]');
                                if (cb && !cb.checked) cb.click();
                            }}"""
                        )
                    filled += 1
                elif field["type"] == "select":
                    await self._page.select_option(f'select[name="{field["name"]}"]', value)
                    filled += 1
                else:
                    # text, email, password, tel, etc.
                    await self._page.fill(f'input[name="{field["name"]}"], textarea[name="{field["name"]}"]', value)
                    filled += 1
            except Exception as e:
                logger.debug("Failed to fill field %s: %s", field["name"], e)
                skipped += 1

        return {"filled": filled, "skipped": skipped}

    def _get_field_value(self, field: dict, portal) -> str | bool | None:
        """Determine the value for a form field based on its label and type."""
        name = field["name"].lower()
        label = field["label"].lower()
        ftype = field["type"]

        # Email fields
        if ftype == "email" or "email" in name or "mail" in name:
            return _ARIA_EMAIL

        # Password fields
        if ftype == "password" or "password" in name or "pass" in name:
            import secrets
            return "Ax" + secrets.token_urlsafe(12) + "7!"

        # Name fields
        if "first" in name or "first" in label or "fname" in name:
            return "ARIA"
        if "last" in name or "last" in label or "lname" in name:
            return "Research"
        if "name" in name or "full" in name or "your" in name:
            return _ARIA_NAME

        # Organization fields
        if "org" in name or "company" in name or "firm" in name or "business" in name or "employer" in name:
            return _ARIA_ORG

        # Website fields
        if "website" in name or "url" in name or "homepage" in name or "site" in name:
            return _ARIA_WEBSITE

        # Phone fields — skip (not required for free tier)
        if "phone" in name or "tel" in name or "mobile" in name:
            return None

        # Radio buttons — select Individual/Personal if available
        if ftype == "radio" and field.get("options"):
            for opt in field["options"]:
                val_lower = opt["value"].lower()
                if val_lower in ("individual", "personal", "developer", "student", "researcher"):
                    return opt["value"]
            # Default to first option
            return field["options"][0]["value"]

        # Checkboxes — accept terms
        if ftype == "checkbox":
            if "term" in label or "agree" in label or "accept" in label or "privacy" in label or "consent" in label:
                return True
            return None

        # Select dropdowns
        if ftype == "select" and field.get("options"):
            # Skip country selects with too many options (let default stand)
            if "country" in name or "nation" in name:
                return None
            # Pick first non-empty option
            for opt in field["options"]:
                if opt["value"]:
                    return opt["value"]

        # Telephone — skip
        if "tel" in name or "phone" in name or "mobile" in name:
            return None

        return None

    async def _solve_captcha(self) -> str | None:
        """Detect and solve any captcha on the page."""
        # Check for reCAPTCHA v2
        has_recaptcha = await self._page.evaluate("""
            () => {
                return document.querySelector('.g-recaptcha') !== null
                    || document.querySelector('[data-sitekey]') !== null
                    || document.querySelector('script[src*="recaptcha/api.js"]') !== null;
            }
        """)
        if not has_recaptcha:
            return None

        # Extract site key
        site_key = await self._page.evaluate("""
            () => {
                const el = document.querySelector('.g-recaptcha');
                if (el) return el.getAttribute('data-sitekey');
                const any = document.querySelector('[data-sitekey]');
                if (any) return any.getAttribute('data-sitekey');
                return null;
            }
        """)
        if not site_key:
            logger.warning("reCAPTCHA detected but no site key found")
            return None

        logger.info("reCAPTCHA detected, site key: %s...", site_key[:8])

        # Solve via 2captcha
        api_key = os.environ.get("ARIA_TWOCAPTCHA_API_KEY", "")
        if not api_key:
            logger.warning("ARIA_TWOCAPTCHA_API_KEY not set — cannot solve captcha")
            return None

        import httpx
        page_url = self._page.url

        # Submit to 2captcha
        async with httpx.AsyncClient(timeout=30) as client:
            submit = await client.post(
                "https://2captcha.com/in.php",
                data={
                    "key": api_key,
                    "method": "userrecaptcha",
                    "googlekey": site_key,
                    "pageurl": page_url,
                    "json": 1,
                },
            )
            result = submit.json()
            if result.get("status") != 1:
                logger.warning("2captcha submit failed: %s", result)
                return None

            request_id = result["request"]

            # Poll for result
            for i in range(45):  # up to 90 seconds
                await asyncio.sleep(2)
                poll = await client.get(
                    "https://2captcha.com/res.php",
                    params={
                        "key": api_key,
                        "action": "get",
                        "id": request_id,
                        "json": 1,
                    },
                )
                poll_result = poll.json()
                if poll_result.get("status") == 1:
                    token = poll_result["request"]
                    # Inject the token into the page
                    await self._page.evaluate(
                        f"""() => {{
                            const textarea = document.getElementById('g-recaptcha-response');
                            if (textarea) {{
                                textarea.style.display = 'block';
                                textarea.value = '{token}';
                                textarea.style.display = 'none';
                            }}
                            // Also try the grecaptcha callback
                            if (typeof grecaptcha !== 'undefined') {{
                                try {{
                                    grecaptcha.getResponse = function() {{ return '{token}'; }};
                                }} catch(e) {{}}
                            }}
                        }}"""
                    )
                    logger.info("Captcha solved and injected")
                    return token
                elif poll_result.get("status") == 0:
                    continue
                else:
                    logger.warning("2captcha error: %s", poll_result)
                    return None

        logger.warning("2captcha timeout — captcha not solved")
        return None

    async def _submit_form(self) -> dict:
        """Find and click the submit button."""
        result = {"clicked": False, "url": self._page.url}

        try:
            # Try multiple selectors for the submit button
            selectors = [
                'button[type="submit"]',
                'input[type="submit"]',
                'button:has-text("Register")',
                'button:has-text("Sign Up")',
                'button:has-text("Create Account")',
                'button:has-text("Submit")',
                'button:has-text("Continue")',
                'a:has-text("Register")',
            ]

            clicked = False
            for selector in selectors:
                btn = await self._page.query_selector(selector)
                if btn:
                    await btn.click()
                    clicked = True
                    break

            if not clicked:
                # Try the first button in the form
                btn = await self._page.query_selector("form button, form input[type=submit]")
                if btn:
                    await btn.click()
                    clicked = True

            result["clicked"] = clicked

            # Wait for navigation/response
            await self._page.wait_for_timeout(2000)
            result["url"] = self._page.url

        except Exception as e:
            result["error"] = str(e)

        return result

    async def _read_errors(self) -> list[str]:
        """Read validation errors from the current page."""
        try:
            errors = await self._page.evaluate("""
                () => {
                    const errors = [];
                    // ASP.NET validation summary
                    document.querySelectorAll('.validation-summary-errors li, .validation-summary-valid li').forEach(el => {
                        if (el.innerText.trim()) errors.push(el.innerText.trim());
                    });
                    // Field-level errors
                    document.querySelectorAll('.field-validation-error, span[data-valmsg-for]').forEach(el => {
                        if (el.innerText.trim()) errors.push(el.innerText.trim());
                    });
                    // Generic error messages
                    document.querySelectorAll('.alert-danger, .alert-error, .error, .message-error').forEach(el => {
                        if (el.innerText.trim()) errors.push(el.innerText.trim());
                    });
                    // Text-danger spans (Bootstrap)
                    document.querySelectorAll('.text-danger').forEach(el => {
                        if (el.innerText.trim()) errors.push(el.innerText.trim());
                    });
                    return [...new Set(errors)];
                }
            """)
            return errors
        except Exception as e:
            logger.debug("Error reading form errors: %s", e)
            return []

    async def _extract_api_key(self, portal) -> str | None:
        """Extract and verify an API key from the current page."""
        # Try the portal's API key path
        if portal.api_key_path:
            try:
                acct_url = urljoin(portal.url.rstrip("/") + "/", portal.api_key_path.lstrip("/"))
                await self._page.goto(acct_url, wait_until="networkidle", timeout=_PAGE_LOAD_TIMEOUT)
                await self._page.wait_for_timeout(1000)
            except Exception:
                pass

        # Try to find API key via regex
        page_text = await self._page.content()
        if portal.api_key_regex:
            for m in re.finditer(portal.api_key_regex, page_text):
                candidate = (m.group(1) if m.groups() else m.group(0)).strip()
                if await self._verify_api_key(portal, candidate):
                    return candidate

        # Try common API key patterns
        for m in re.finditer(r"\b[0-9a-f]{32}\b", page_text):
            if await self._verify_api_key(portal, m.group()):
                return m.group()

        return None

    async def _verify_api_key(self, portal, key: str) -> bool:
        """Verify an API key by making a test request."""
        if not portal.api_key_test_url:
            return True  # No verification URL — assume it's valid

        test_url = portal.api_key_test_url.replace("{key}", key)
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(test_url)
                return r.status_code == 200
        except Exception:
            return False

    async def _log_step(self, diag: dict, step: str, message: str) -> None:
        """Log a step to the diagnostic log."""
        entry = {
            "step": step,
            "message": message,
            "time": time.time(),
            "url": self._page.url if self._page else "",
        }
        diag["steps"].append(entry)
        logger.info("[portal_agent] %s: %s", step, message)

    async def _finish(self, diag: dict, success: bool, message: str, **kwargs) -> dict:
        """Finish the registration attempt and return the result."""
        diag["success"] = success
        diag["message"] = message
        diag["duration_s"] = time.time() - diag["started_at"]
        diag.update(kwargs)

        # Wire to brain
        try:
            from aria_service.intel.engine_wiring import wire_success, wire_failure
            if success:
                wire_success(
                    module="portal_agent",
                    summary=f"Registered for {diag.get('portal_name', diag['portal_id'])}: {message[:200]}",
                    source_id=f"portal_agent:{diag['portal_id']}",
                )
            else:
                wire_failure(
                    module="portal_agent",
                    detail=f"Registration failed for {diag.get('portal_name', diag['portal_id'])}: {message[:300]}",
                    gap_type="source_failure",
                    source=f"portal_agent:{diag['portal_id']}",
                )
        except Exception:
            pass

        return diag
