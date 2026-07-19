"""R-F2795 (D10) — RENDERED browser E2E for aria-web.

WHY THIS EXISTS
---------------
The R-F2757 ecosystem DD (docs/ARIA_ECOSYSTEM_360_DD_2026_07_18_R-F2757.md:165,181)
could not close rendered E2E across three cycles: its browser runtime "returned an
empty provider inventory (``[]``)". It correctly refused to substitute source/build
checks for rendered evidence, so hydration, console errors, interaction,
authenticated navigation, responsive layout and browser-observed network traffic
were all left **UNVERIFIED**. That was a tooling gap, never a passing gate.

This closes it with a genuinely attached Chromium (already present in .venv — no
new third-party dependency, per CLAUDE.md §6): it boots aria-web locally and drives
it in a real browser.

It is also the ONLY honest test of the R-F2774 page gate. That gate keys off an
httpOnly cookie and guards a page NAVIGATION — neither a curl smoke nor a unit test
exercises the real browser navigation + cookie path. Every other test we have
asserts the decision function; this asserts what a user actually gets.

Deliberately NOT in the default pytest gate: it boots a server, binds a port and
launches a browser. Run it on demand.

Run:  .venv/Scripts/python.exe scripts/e2e/browser_e2e_rf2795.py
Exit: 0 = all checks green.
"""
from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]

_failures = 0
_total = 0


def check(name: str, cond: bool, detail: str = "") -> None:
    global _failures, _total
    _total += 1
    ok = bool(cond)
    if not ok:
        _failures += 1
    line = f"{'ok  ' if ok else 'FAIL'} - {name}"
    if detail and not ok:
        line += f" :: {detail}"
    print(line, flush=True)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_health(base: str, timeout_s: float = 120.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1.0)
    return False


def main() -> int:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"

    env = dict(os.environ)
    env.update({
        "NODE_ENV": "test",
        "PORT": str(port),
        # server.mjs refuses to boot on a JWT_SECRET under 32 chars.
        "JWT_SECRET": secrets.token_hex(48),
        # Without this every loopback request takes the same-process bypass and
        # the R-F2775 role gates never evaluate — the test would prove nothing.
        "ARIA_DISABLE_LOCALHOST_BYPASS": "1",
    })
    for k in ("ARIA_API_TOKEN", "ARIA_INTERNAL_TOKEN", "TELEGRAM_BOT_TOKEN", "REDIS_URL"):
        env.pop(k, None)

    server = subprocess.Popen(
        ["node", "server.mjs"], cwd=str(ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    browser = None
    try:
        up = _wait_health(base)
        check("aria-web boots and serves /healthz", up)
        if not up:
            return 1

        with sync_playwright() as pw:
            browser = pw.chromium.launch()

            # ── HYDRATION + CONSOLE + NETWORK ─────────────────────────────
            # All three were explicitly UNVERIFIED in R-F2757.
            for path in ["/", "/index.html", "/dashboard.html", "/signin.html", "/model-card.html"]:
                ctx = browser.new_context()
                page = ctx.new_page()
                page_errors: list[str] = []
                console_errors: list[str] = []
                bad_responses: list[str] = []
                page.on("pageerror", lambda e: page_errors.append(str(e)))
                page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
                # Same-origin only: third-party/CDN noise is not this app's contract.
                page.on("response", lambda r: bad_responses.append(f"{r.status} {r.url}")
                        if r.url.startswith(base) and r.status >= 500 else None)

                resp = page.goto(f"{base}{path}", wait_until="domcontentloaded", timeout=30_000)
                check(f"HYDRATION {path} responds 2xx/3xx",
                      resp is not None and resp.status < 400,
                      f"status={resp.status if resp else 'none'}")

                # Rendered, not merely returned.
                body_len = page.evaluate("() => (document.body && document.body.innerText || '').trim().length")
                check(f"HYDRATION {path} renders non-empty body", body_len > 20, f"len={body_len}")
                check(f"PAGEERROR {path} no uncaught script errors",
                      not page_errors, " | ".join(page_errors[:2]))
                check(f"NETWORK {path} no same-origin 5xx",
                      not bad_responses, " | ".join(bad_responses[:2]))
                # Console errors are SURFACED, not failed on: an unauthenticated
                # page legitimately logs failed auth probes. Reporting beats a
                # false gate that everyone learns to ignore.
                if console_errors:
                    print(f"     note {path}: {len(console_errors)} console error(s); "
                          f"first: {console_errors[0][:120]}", flush=True)
                ctx.close()

            # ── AUTHENTICATED NAVIGATION — the R-F2774 gate, for real ─────
            # The distinction that matters is WHERE the redirect comes from.
            # R-F2774's whole point is that the SERVER gate is the real gate and
            # the in-page Auth.require*() calls are cosmetic — they run only after
            # the HTML has already been delivered, so they protect nothing.
            #
            # So: an operator page must be redirected by the SERVER (a redirect in
            # the document's own response chain), whereas a customer page must be
            # SERVED 200 by the server. A customer page's client-side guard may
            # then bounce an anonymous visitor to signin — that is correct product
            # behaviour and must NOT be conflated with a server gate. An earlier
            # version of this test made exactly that conflation and reported
            # /dashboard.html as "gated" when the server had served it 200.
            ctx = browser.new_context()
            page = ctx.new_page()
            for p in ["/vault.html", "/admin.html", "/aria-brain", "/sources.html", "/wa-connections.html"]:
                resp = page.goto(f"{base}{p}", wait_until="domcontentloaded")
                landed = urllib.parse.urlparse(page.url).path
                server_redirected = resp is not None and resp.request.redirected_from is not None
                check(f"AUTH operator page {p} is redirected BY THE SERVER",
                      server_redirected, "no redirect in the document response chain")
                check(f"AUTH operator page {p} lands on signin",
                      landed == "/signin.html", f"landed on {landed}")
            for p in ["/dashboard.html", "/index.html"]:
                resp = page.goto(f"{base}{p}", wait_until="commit")
                check(f"AUTH customer page {p} is SERVED by the server (not server-gated)",
                      resp is not None and resp.status == 200 and resp.request.redirected_from is None,
                      f"status={resp.status if resp else 'none'} "
                      f"redirected={resp.request.redirected_from is not None if resp else '?'}")
            ctx.close()

            # ── INTERACTION ───────────────────────────────────────────────
            ctx = browser.new_context()
            page = ctx.new_page()
            page_errors = []
            page.on("pageerror", lambda e: page_errors.append(str(e)))
            page.goto(f"{base}/signin.html", wait_until="domcontentloaded")
            clickable = page.locator("button:visible, a[href]:visible").count()
            check("INTERACTION signin exposes clickable controls", clickable > 0, f"count={clickable}")
            submit = page.locator("button:visible").first
            if submit.count():
                try:
                    submit.click(timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(400)
            still = page.evaluate("() => (document.body && document.body.innerText || '').trim().length")
            check("INTERACTION click does not blank the page", still > 20, f"len={still}")
            check("INTERACTION click raises no uncaught error", not page_errors, " | ".join(page_errors[:1]))
            ctx.close()

            # ── RESPONSIVE LAYOUT ─────────────────────────────────────────
            # Horizontal overflow at mobile width is the classic broken-layout tell.
            for label, width, height in [("mobile", 390, 844), ("tablet", 820, 1180), ("desktop", 1440, 900)]:
                ctx = browser.new_context(viewport={"width": width, "height": height})
                page = ctx.new_page()
                page.goto(f"{base}/dashboard.html", wait_until="domcontentloaded")
                overflow = page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                check(f"RESPONSIVE dashboard no horizontal overflow @{label} ({width}px)",
                      overflow <= 2, f"overflow={overflow}px")
                ctx.close()
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass
        server.terminate()
        try:
            server.wait(timeout=15)
        except Exception:
            server.kill()

    if _failures == 0:
        print(f"\nPASS — rendered E2E green ({_total} checks). Hydration, console, "
              "interaction, auth navigation, responsive layout and browser-observed "
              "network are MEASURED, no longer UNVERIFIED.")
        return 0
    print(f"\nFAIL — {_failures} of {_total} checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
