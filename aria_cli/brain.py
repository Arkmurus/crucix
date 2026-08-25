"""R-F988 — best-effort brain wiring for the ARIA Coder CLI.

Per CLAUDE.md §21 (everything wired to the brain) and §15 (pay-once-remember-
forever): when ARIA codes in her own ecosystem, the session — what was asked,
what changed, whether it succeeded — should reach the live brain so she learns
from it. We POST to the same ``/api/aria/brain/signal`` endpoint the WA/Node
tiers use.

This is strictly best-effort: the CLI is a local tool and the brain may be
unreachable (offline, no token). Failures are swallowed; they never block the
coding session. Reachability is checked live, not assumed.
"""
from __future__ import annotations

import os

import httpx


def _service_url() -> str:
    return (
        os.getenv("ARIA_SERVICE_URL")
        or os.getenv("ARIA_CODER_BRAIN_URL")
        or "https://aria-intel.fly.dev"
    ).rstrip("/")


def _token() -> str:
    return (os.getenv("ARIA_INTERNAL_TOKEN") or "").strip()


def brain_enabled(self_mode: bool) -> bool:
    """Wire to the brain only when editing ARIA's own ecosystem AND we have a
    token. An explicit ARIA_CODER_BRAIN_DISABLED=1 turns it off entirely."""
    if (os.getenv("ARIA_CODER_BRAIN_DISABLED") or "").strip() in {"1", "true", "yes"}:
        return False
    return self_mode and bool(_token())


def report_session(*, task: str, success: bool, changed_files: list[str],
                   summary: str, self_mode: bool) -> str:
    """Post a session signal to the brain. Returns a short status string for the
    CLI to print; never raises."""
    if not brain_enabled(self_mode):
        return "brain: not wired (general mode or no token)"

    signal_type = "aria_cli_session" if success else "aria_cli_session_failed"
    content = (
        f"ARIA Coder CLI session. Task: {task[:400]}. "
        f"Outcome: {'success' if success else 'incomplete'}. "
        f"Files changed: {', '.join(changed_files) or 'none'}. "
        f"Summary: {summary[:600]}"
    )
    payload = {
        "content": content,
        "source": "aria_cli",
        "signal_type": signal_type,
        "metadata": {
            "channel": "cli",
            "changed_files": changed_files[:50],
            "success": success,
        },
    }
    url = f"{_service_url()}/api/aria/brain/signal"
    try:
        resp = httpx.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=8.0,
        )
        if resp.status_code < 400:
            return f"brain: signal accepted ({signal_type})"
        return f"brain: endpoint returned HTTP {resp.status_code} (non-fatal)"
    except Exception as exc:  # noqa: BLE001 — never block on the brain
        return f"brain: unreachable, skipped ({type(exc).__name__})"


def report_signal(*, signal_type: str, content: str, self_mode: bool,
                  metadata: dict | None = None) -> str:
    """Post an arbitrary brain signal (used by hooks and sub-agents).

    Same best-effort contract as ``report_session``: never raises, returns a
    short status string. ``signal_type`` is the brain signal type (e.g.
    ``aria_cli_hook_failed``, ``aria_cli_subagent``).
    """
    if not brain_enabled(self_mode):
        return "brain: not wired (general mode or no token)"
    payload = {
        "content": content[:800],
        "source": "aria_cli",
        "signal_type": signal_type,
        "metadata": dict(metadata or {}),
    }
    url = f"{_service_url()}/api/aria/brain/signal"
    try:
        resp = httpx.post(
            url, json=payload,
            headers={"Authorization": f"Bearer {_token()}"},
            timeout=8.0,
        )
        if resp.status_code < 400:
            return f"brain: signal accepted ({signal_type})"
        return f"brain: endpoint returned HTTP {resp.status_code} (non-fatal)"
    except Exception as exc:  # noqa: BLE001 — never block on the brain
        return f"brain: unreachable, skipped ({type(exc).__name__})"
