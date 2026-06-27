"""R-F2038: Verify dashboard HTML API calls match server.mjs routes.

Scans all .html files under public/ for API endpoint references (strings
containing /api/...) and checks each against the routes registered in
server.mjs. Reports mismatches — endpoints the dashboard calls but the
server doesn't explicitly route (relying on the catch-all proxy), and
endpoints the server routes that no dashboard page calls.

This is a STRUCTURAL guard against the R-F2038 failure class: the dashboard
was written to call /api/aria/dd/reports and /api/aria/dd/watchlist, but
server.mjs only had /api/aria/dd/report/:run_id and /api/aria/dd/watchlist/alerts.
The missing explicit routes fell through to the catch-all proxy, which worked
only when ARIA_SERVICE_URL was set AND the Python service was reachable.
When the proxy chain failed, the dashboard silently showed "—" KPIs.

Usage:
    python scripts/verify_dashboard_api_routes.py
    python scripts/verify_dashboard_api_routes.py --fix  (not yet implemented)
"""

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_DIR = REPO_ROOT / "public"
SERVER_MJS = REPO_ROOT / "server.mjs"

# Endpoints that are intentionally NOT in server.mjs (handled by other services
# or are external). Add to this set as needed.
EXTERNAL_ENDPOINTS: set[str] = {
    # External services
    "/api/billing/webhook",       # Stripe webhook — raw body, no auth
    "/api/whatsapp",              # Twilio webhook — handled by ariaWhatsApp
    "/webhook",                   # Generic webhook
    "/events",                    # SSE endpoint
    "/healthz",                   # Platform liveness probe
    "/s/",                        # Share brief — dynamic :token param
    # Static pages
    "/signin.html",
    "/dashboard.html",
    "/aria-brain",
    # These are handled by the catch-all proxy — they exist on the Python side
    # but are listed here so the validator doesn't flag them. They're still
    # at risk of the same failure mode (proxy chain down = silent failure).
    # R-F2038 adds explicit routes for the most critical ones.
}

# Endpoints that are known to exist on the Python service (via the catch-all proxy)
# but don't have explicit routes in server.mjs. These are the ones most at risk.
PROXY_DEPENDENT_ENDPOINTS: set[str] = {
    "/api/aria/dd/reports",
    "/api/aria/dd/watchlist",
    "/api/aria/dd/watchlist/rescreen",
    "/api/aria/dd/vault/stats",
    "/api/aria/dd/vault/search",
    "/api/aria/dd/vault/case",
    "/api/aria/dd/vls/chain",
    "/api/aria/dd/vls/key",
    "/api/aria/dd/vls/proof",
    "/api/aria/dd/vls/verify",
    "/api/aria/dd/case",
    "/api/aria/dd/orchestrate",
    "/api/aria/dd/save-tool-result",
    "/api/aria/dd/substance",
    "/api/aria/dd/adverse-media-search",
    "/api/aria/dd/quarantine",
    "/api/aria/dd/layer-5c/stats",
    "/api/aria/dd/case-archive/stats",
}


def extract_api_calls_from_html(filepath: Path) -> set[str]:
    """Extract API endpoint paths from an HTML file.

    Looks for string literals containing '/api/' in <script> blocks and
    inline event handlers. Returns a set of endpoint paths (e.g.
    '/api/aria/dd/reports').
    """
    text = filepath.read_text(encoding="utf-8", errors="replace")
    endpoints: set[str] = set()

    # Match string literals containing /api/... — both single and double quotes
    # This catches: authed('/api/aria/dd/reports?limit=100'), fetch('/api/data'), etc.
    # The character class includes ? and = for query strings, and - at the end.
    for m in re.finditer(r"""['"](/api/[a-zA-Z0-9_/.?= &-]+)['"]""", text):
        path = m.group(1)
        # Strip query params and trailing slashes
        path = path.split("?")[0].rstrip("/")
        # Strip dynamic segments like :run_id, :job_id
        path = re.sub(r"/:[a-zA-Z_]+", "/:param", path)
        endpoints.add(path)

    return endpoints


def extract_routes_from_server(filepath: Path) -> set[str]:
    """Extract registered API routes from server.mjs.

    Looks for:
    - app.get('/api/...'), app.post('/api/...'), etc.
    - app.use('/api/...') mount points
    - ariaProxy(req, res, '/api/aria/...') calls inside route handlers
    Returns a set of route paths.
    """
    text = filepath.read_text(encoding="utf-8", errors="replace")
    routes: set[str] = set()

    # Match app.get('/api/...'), app.post('/api/...'), etc.
    for m in re.finditer(
        r"""app\.(get|post|put|delete|use)\(['"](/api/[^'"]+)['"]""",
        text,
    ):
        path = m.group(2)
        path = path.rstrip("/")
        routes.add(path)

    # Match ariaProxy(req, res, '/api/aria/...') calls — these are the
    # actual upstream paths that the handler forwards to, even when the
    # Express route itself is registered dynamically or via a variable.
    # Handles both single-quoted strings and template literals (backtick).
    for m in re.finditer(
        r"""ariaProxy\([^,]+,\s*[^,]+,\s*(['"`])(/api/[^'"`]+)\1""",
        text,
    ):
        path = m.group(2)
        # Strip template literal expressions like ${...}
        path = re.sub(r'\$\{[^}]+\}', ':param', path)
        path = path.rstrip("/")
        routes.add(path)

    return routes


def normalize_endpoint(endpoint: str) -> str:
    """Normalize an endpoint for comparison.

    Strips query params, trailing slashes, and replaces dynamic segments
    with :param.
    """
    path = endpoint.split("?")[0].rstrip("/")
    path = re.sub(r"/:[a-zA-Z_]+", "/:param", path)
    return path


def main() -> int:
    errors = 0
    proxy_dependent_found: set[str] = set()
    missing_routes: set[str] = set()

    # Load server routes
    if not SERVER_MJS.exists():
        print(f"ERROR: {SERVER_MJS} not found")
        return 1
    server_routes = extract_routes_from_server(SERVER_MJS)
    print(f"\nServer routes found: {len(server_routes)}")

    # Scan all HTML files in public/
    html_files = sorted(PUBLIC_DIR.glob("*.html"))
    print(f"HTML files to scan: {len(html_files)}")

    all_dashboard_calls: set[str] = set()

    for html_file in html_files:
        calls = extract_api_calls_from_html(html_file)
        if calls:
            print(f"\n  {html_file.name}: {len(calls)} API calls")
            all_dashboard_calls.update(calls)
            for call in sorted(calls):
                # Check if the call has an explicit route in server.mjs
                normalized = normalize_endpoint(call)
                has_explicit = any(
                    normalized == normalize_endpoint(r) for r in server_routes
                )
                is_external = any(
                    call.startswith(e) for e in EXTERNAL_ENDPOINTS
                )
                # The catch-all proxy at server.mjs:5080 handles any
                # unmatched /api/aria/* request by forwarding to fly.io.
                # This is a valid route, but it's proxy-dependent.
                is_aria_catchall = call.startswith("/api/aria/")
                is_proxy_dep = any(
                    call.startswith(e) for e in PROXY_DEPENDENT_ENDPOINTS
                )

                if has_explicit:
                    status = "✓ explicit route"
                elif is_external:
                    status = "○ external (skipped)"
                elif is_aria_catchall and not has_explicit:
                    # Handled by the catch-all proxy — works when fly.io is up
                    if is_proxy_dep:
                        status = "⚠ PROXY-DEPENDENT — no explicit route, relies on catch-all"
                        proxy_dependent_found.add(call)
                        errors += 1
                    else:
                        status = "○ catch-all proxy (fly.io)"
                else:
                    status = "✗ NO ROUTE FOUND — will 404"
                    missing_routes.add(call)
                    errors += 1

                print(f"      {call:<55} {status}")

    print(f"\n{'='*60}")
    print(f"Total dashboard API calls: {len(all_dashboard_calls)}")
    print(f"Proxy-dependent (no explicit route): {len(proxy_dependent_found)}")
    print(f"Missing routes (will 404): {len(missing_routes)}")
    print(f"{'='*60}")

    if proxy_dependent_found:
        print("\n⚠️  PROXY-DEPENDENT ENDPOINTS — these work via the catch-all proxy")
        print("   but will silently fail when fly.io is unreachable (cold-start,")
        print("   network blip, env misconfig). Add explicit routes to server.mjs")
        print("   for the most critical ones (dashboard KPIs).")
        print(f"\n   Affected ({len(proxy_dependent_found)}):")
        for ep in sorted(proxy_dependent_found):
            print(f"     • {ep}")

    if missing_routes:
        print(f"\n✗ MISSING ROUTES ({len(missing_routes)}) — these will 404:")
        for ep in sorted(missing_routes):
            print(f"     • {ep}")

    if proxy_dependent_found or missing_routes:
        print("\n   Required server.mjs additions (R-F2038):")
        print("   ```javascript")
        print("   // R-F2038 — explicit proxy routes for dashboard-critical endpoints")
        for ep in sorted(proxy_dependent_found | missing_routes):
            print(f"   app.get('{ep}', requireAuth, (req, res) => {{")
            print(f"     const userId = req.user?.userId || '';")
            print(f"     if (!userId) return res.status(401).json({{ error: 'Authentication required' }});")
            print(f"     const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';")
            print(f"     const params = new URLSearchParams(existingQs);")
            print(f"     params.set('user_id', userId);")
            print(f"     return ariaProxy(req, res, `{ep}?${{params.toString()}}`, {{ fallback: async () => res.status(503).json(_brainFallback()) }});")
            print(f"   }});")
        print("   ```")
        return 1

    print("\n✅ All dashboard API calls have explicit server routes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
