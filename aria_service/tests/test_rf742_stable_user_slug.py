"""R-F742 (2026-05-20) — stable USER_ID_SLUG derivation across seenode deploys.

Root cause: lib/auth/users.mjs:120 — `generateId()` returns
randomBytes(6).toString('hex') (12-char random hex) on every
createUser(). Combined with [[seenode_disk_ephemeral]] (every deploy
wipes RUNS_DIR/users.json), the operator was re-registering after
each deploy and getting a NEW user.id — orphaning all prior
conversations stored on fly's persistent volume under the OLD user.id.

Live evidence: three different `user_id=...` values appeared in fly
logs across recent deploys for what appeared to be the same human:
  07ea659385d1 → 4287bc6b4ea9 → 588d5ca52108

R-F742 fix: switch the slug derivation to prefer EMAIL > USERNAME
> id. Email is user-controlled and stable across deploys. Username
is similarly stable. Only fall back to the ephemeral user.id when
nothing else is available.

Both call sites updated:
  - public/aria.html:638 (chat UI, drives sidebar conversation list)
  - lib/reports/routes.mjs:80 (PDF export ownership guard — must
    match aria.html derivation or every export 403s after a deploy)

These tests guard against regression — removing the email/username
preference, or only patching one of the two files, would fail here.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARIA_HTML = REPO / "public" / "aria.html"
REPORTS_ROUTES = REPO / "lib" / "reports" / "routes.mjs"


def test_aria_html_uses_email_first_in_user_slug():
    """R-F742: aria.html USER_ID_SLUG must prefer email over user.id."""
    src = ARIA_HTML.read_text(encoding="utf-8")
    # The slug derivation line — must put email FIRST in the chain
    assert "user.email || user.username || user.id" in src, (
        "R-F742 regression: aria.html USER_ID_SLUG must prefer "
        "email > username > id. Found something else."
    )
    # And NOT the old "user.id || user.username" pattern
    assert "user.id || user.username || 'anon'" not in src, (
        "R-F742 regression: legacy 'user.id || user.username' slug "
        "derivation still present in aria.html — will orphan past "
        "conversations on every seenode deploy."
    )


def test_reports_routes_userSlug_matches_aria_html_derivation():
    """R-F742: lib/reports/routes.mjs must match aria.html so PDF
    export ownership guard works across deploys."""
    src = REPORTS_ROUTES.read_text(encoding="utf-8")
    # New pattern: email > username > id
    assert "user.email || user.username || user.id" in src, (
        "R-F742 regression: reports/routes.mjs userSlug must match "
        "aria.html derivation (email > username > id) or PDF export "
        "ownership guard will 403 every legitimate request after "
        "a seenode deploy."
    )
    # Old pattern absent
    assert "user.id || user.username" not in src, (
        "R-F742 regression: reports/routes.mjs still uses legacy "
        "user.id-first slug. Will mismatch the aria.html slug."
    )


def test_rf742_marker_present_in_both_files():
    """R-F742: marker comments present so future contributors see
    the constraint."""
    aria_src = ARIA_HTML.read_text(encoding="utf-8")
    reports_src = REPORTS_ROUTES.read_text(encoding="utf-8")
    assert "R-F742" in aria_src, "R-F742 marker missing from aria.html"
    assert "R-F742" in reports_src, "R-F742 marker missing from reports/routes.mjs"


def test_no_other_user_id_first_slug_derivations_remain():
    """R-F742: scan the repo for any OTHER copies of the old slug
    derivation pattern that would create a divergent slug. If a third
    file does its own slug derivation with user.id first, this test
    catches it before deploy-time bugs surface."""
    import re

    # Walk a small set of relevant frontend / Node directories
    search_roots = [
        REPO / "public",
        REPO / "lib",
        REPO / "server.mjs",
    ]
    pattern = re.compile(r"user\.id\s*\|\|\s*user\.username")
    offenders = []
    for root in search_roots:
        if root.is_file():
            files = [root]
        elif root.is_dir():
            files = list(root.rglob("*.html")) + list(root.rglob("*.mjs")) + list(root.rglob("*.js"))
        else:
            files = []
        for f in files:
            try:
                text = f.read_text(encoding="utf-8")
            except Exception:
                continue
            if pattern.search(text):
                offenders.append(str(f.relative_to(REPO)))
    assert not offenders, (
        f"R-F742 regression: legacy 'user.id || user.username' slug "
        f"derivation found in additional files: {offenders}. "
        f"Each one creates a divergent slug that orphans data on "
        f"seenode deploys."
    )
