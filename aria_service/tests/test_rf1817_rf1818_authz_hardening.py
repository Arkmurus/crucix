"""R-F1817 / R-F1818 — Phase-2 authz hardening.

H2 (R-F1817): no hardcoded 'aria-internal' token default may remain anywhere — an
unset ARIA_INTERNAL_TOKEN must fail closed, not fall back to a repo-public string.
H4 (R-F1818): destructive/control proxy routes must be requireAdmin, not requireAuth.

These endpoints live in self-starting Node modules (not unit-importable in pytest),
so this asserts the security WIRING at source level — same convention as
test_proof_footer_rf403.
"""
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]

H2_FILES = [
    "server.mjs", "services/wa-listener/aria_wa_listener.mjs",
    "lib/aria/backup.mjs", "lib/aria/emailReader.mjs", "lib/aria/linkedinIntel.mjs",
    "lib/aria/pipeline.mjs", "lib/aria/proactive.mjs", "lib/whatsapp/ariaWhatsApp.mjs",
    "services/aria_zoom_service.py",
]


def test_h2_no_hardcoded_token_default():
    for rel in H2_FILES:
        s = (REPO / rel).read_text(encoding="utf-8", errors="ignore")
        assert "|| 'aria-internal'" not in s, f"{rel}: JS hardcoded token default remains"
        assert not re.search(r'ARIA_INTERNAL_TOKEN"\s*,\s*"aria-internal"', s), \
            f"{rel}: Python hardcoded token default remains"


def test_h2_wa_requireauth_fails_closed():
    s = (REPO / "services/wa-listener/aria_wa_listener.mjs").read_text(encoding="utf-8")
    # An empty INT_TOKEN must reject all (truthy-token guard), not auth-everything.
    assert "token && token === INT_TOKEN" in s, "wa requireAuth must require a truthy token (fail-closed)"


H4_ROUTES = [
    "/api/aria/self/code", "/api/aria/operating-mode/set", "/api/aria/knowledge/fact",
    "/api/aria/admin/purge-cases", "/api/aria/admin/purge-signals",
]


def test_h4_destructive_routes_require_admin():
    s = (REPO / "server.mjs").read_text(encoding="utf-8")
    for route in H4_ROUTES:
        idx = s.find(f"app.post('{route}'")
        assert idx > 0, f"route {route} not found in server.mjs"
        block = s[idx:idx + 400]
        head = block.split("=>")[0].split("(req")[0]  # registration head, before the handler
        head = re.sub(r"//.*", "", head)              # strip line comments (may mention requireAuth)
        assert "requireAdmin" in head, f"{route} is NOT admin-gated"
        assert "requireAuth" not in head, f"{route} still uses requireAuth"
