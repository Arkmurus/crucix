"""R-F829 + R-F830 + R-F831 — audit security backlog (2026-05-23).

These three were reserved 2026-05-23 by another agent but never shipped.
Each addresses a specific audit finding flagged in the wider 2026-05
security sweep.

The Node-side fixes (CORS allowlist, Telegram webhook strict mode,
localhost-bypass gate, persist halt-don't-zero) live in JS files —
verified here via pattern checks (no Node runtime in the Python venv).

R-F829: socket.io was configured with `cors: { origin: '*' }`, which
        is incompatible with credentials and lets any browser origin
        connect to the chat socket. Replace with an explicit allowlist
        (APP_URL + intel.imaria.io + aria-web.fly.dev + localhost).

R-F830: lib/persist/store.mjs silently called `starting fresh` on
        corrupt JSON — for users.json / sessions.json that's total
        account wipe with no rollback. New behaviour: quarantine the
        corrupt file + throw, halting boot until operator restores
        from snapshot (or sets PERSIST_ALLOW_ZERO=1 to accept reset).

R-F831: Telegram webhook secret was soft-rollout (accept unsigned
        with warning). Tightened to refuse unsigned in production.
        Also gated the requireAuth localhost-bypass behind
        ARIA_DISABLE_LOCALHOST_BYPASS so operator can lock it down.
"""
from __future__ import annotations

import pathlib


_ROOT = pathlib.Path(__file__).parent.parent.parent
_SERVER = _ROOT / "server.mjs"
_PERSIST = _ROOT / "lib" / "persist" / "store.mjs"


# ── R-F829 ────────────────────────────────────────────────────────────


def test_rf829_socketio_cors_not_wildcard():
    """socket.io CORS must not be 'origin: *' — that's the audit finding."""
    text = _SERVER.read_text(encoding="utf-8")
    # The OLD line was: cors: { origin: '*', methods: ['GET', 'POST'] }
    # New must be a function or an allowlist.
    assert "cors: { origin: '*'" not in text, (
        "Socket.io still configured with origin:'*' — audit W4 unfixed."
    )


def test_rf829_socketio_uses_origin_allowlist():
    text = _SERVER.read_text(encoding="utf-8")
    assert "_io_allowed_origins" in text, "Missing R-F829 allowlist variable"
    # The allowlist must include at least the canonical public host.
    assert "intel.imaria.io" in text, (
        "Allowlist must include the canonical production host"
    )


# ── R-F830 ────────────────────────────────────────────────────────────


def test_rf830_corrupt_file_no_longer_silently_zeros():
    """`starting fresh` on corrupt JSON was the data-loss vector. The
    new code must throw (halt boot) unless PERSIST_ALLOW_ZERO=1."""
    text = _PERSIST.read_text(encoding="utf-8")
    # The catch block must throw on corruption by default.
    assert "throw new Error" in text, (
        "lib/persist/store.mjs init() must throw on corruption — R-F830"
    )
    # The corrupt-file quarantine path must exist.
    assert ".corrupt-" in text, (
        "Missing corrupt-file quarantine rename in R-F830 init()"
    )
    # The escape hatch env var must exist for first-boot / non-critical cases.
    assert "PERSIST_ALLOW_ZERO" in text, (
        "Missing PERSIST_ALLOW_ZERO escape-hatch env var"
    )


# ── R-F831 ────────────────────────────────────────────────────────────


def test_rf831_telegram_webhook_strict_in_prod():
    """In prod (NODE_ENV=production OR FLY_APP_NAME set), an unsigned
    Telegram webhook must be REFUSED, not soft-accepted."""
    text = _SERVER.read_text(encoding="utf-8")
    # Find the webhook handler chunk
    wh_start = text.find("app.post('/webhook'")
    assert wh_start > 0, "Webhook handler missing — refactor?"
    chunk = text[wh_start : wh_start + 3000]
    assert "NODE_ENV === 'production'" in chunk or "FLY_APP_NAME" in chunk, (
        "R-F831 webhook strict-mode prod detection missing"
    )
    assert "return res.sendStatus(503)" in chunk or "return res.sendStatus(401)" in chunk, (
        "R-F831 must refuse unsigned webhooks in prod (503 or 401)"
    )


def test_rf831_localhost_bypass_gated_by_env():
    """The localhost auth bypass must be disable-able via env var."""
    text = _SERVER.read_text(encoding="utf-8")
    assert "ARIA_DISABLE_LOCALHOST_BYPASS" in text, (
        "R-F831 missing env-driven gate for the localhost auth bypass"
    )
