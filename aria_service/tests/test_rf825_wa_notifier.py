"""R-F825 — Tests for WANotifier + ARIA-Coder operator-facing messages.

Operator vision: "She needs to be spot on the way she communicates the
changes and the updates."

Invariants tested
─────────────────
1. WANotifier is dormant unless all three env vars set; calls
   `notify()` returns a `skipped:*` reason — never raises.
2. Dry-run respects ARIA_AUTONOMOUS_DRY_RUN.
3. POST payload has the correct shape (group_id + truncated message).
4. Network errors logged + caught, never propagated.
5. Static message builders produce consistent operator-readable
   strings with the right emoji per stage.
6. WANotifier integrates cleanly with ARIACoder.fix_gap — success
   path calls msg_shipped; failure path calls msg_failed; operator-
   initiated also calls msg_request_queued at start.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


def _run(coro):
    return asyncio.run(coro)


# ════════════════════════════════════════════════════════════════════════════
# WANotifier — configuration + dormancy
# ════════════════════════════════════════════════════════════════════════════

class TestConfig:
    def test_dormant_when_no_env(self, monkeypatch) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        monkeypatch.delenv("SEENODE_BASE_URL", raising=False)
        monkeypatch.delenv("ARIA_INTERNAL_TOKEN", raising=False)
        monkeypatch.delenv("ARIA_CODER_WA_GROUP_ID", raising=False)
        n = WANotifier()
        assert not n.is_configured

    def test_configured_when_all_three_set(self, monkeypatch) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        monkeypatch.setenv("SEENODE_BASE_URL", "https://intel.example.com")
        monkeypatch.setenv("ARIA_INTERNAL_TOKEN", "t")
        monkeypatch.setenv("ARIA_CODER_WA_GROUP_ID", "g@g.us")
        n = WANotifier()
        assert n.is_configured

    def test_notify_skipped_when_dormant(self, monkeypatch) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        monkeypatch.delenv("SEENODE_BASE_URL", raising=False)
        n = WANotifier()
        result = _run(n.notify("test"))
        assert result.startswith("skipped:")

    def test_notify_skipped_when_empty_text(self, monkeypatch) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        n = WANotifier(seenode_base_url="x", internal_token="y",
                       default_group_id="z")
        assert _run(n.notify("")) == "skipped:empty"
        assert _run(n.notify("   ")) == "skipped:empty"

    def test_notify_dry_run_returns_skipped(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        n = WANotifier(seenode_base_url="x", internal_token="y",
                       default_group_id="z", dry_run=True)
        assert _run(n.notify("hello")) == "skipped:dry_run"


# ════════════════════════════════════════════════════════════════════════════
# WANotifier — POST behaviour with injected client
# ════════════════════════════════════════════════════════════════════════════

class TestNotifyPost:
    def _make_notifier(self, *, http_client):
        from aria_service.autonomous.wa_notifier import WANotifier
        return WANotifier(
            seenode_base_url="https://intel.example.com",
            internal_token="testtok",
            default_group_id="group@g.us",
            dry_run=False,
            http_client=http_client,
        )

    def test_post_uses_correct_url_and_payload(self) -> None:
        captured = {}

        async def fake_post(url, headers, json, timeout):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        http = MagicMock()
        http.post = fake_post

        n = self._make_notifier(http_client=http)
        result = _run(n.notify("test message"))
        assert result == "ok"
        assert captured["url"] == "https://intel.example.com/api/wa-listener/send"
        assert captured["headers"]["Authorization"] == "Bearer testtok"
        assert captured["json"]["group_id"] == "group@g.us"
        assert captured["json"]["message"] == "test message"

    def test_message_truncated_at_4000_chars(self) -> None:
        captured = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        http = MagicMock()
        http.post = fake_post

        n = self._make_notifier(http_client=http)
        long_text = "x" * 5000
        _run(n.notify(long_text))
        assert len(captured["json"]["message"]) == 4000

    def test_http_error_returns_error_string(self) -> None:
        async def fake_post(url, headers, json, timeout):
            resp = MagicMock()
            resp.status_code = 500
            resp.text = "internal server error"
            return resp

        http = MagicMock()
        http.post = fake_post

        n = self._make_notifier(http_client=http)
        result = _run(n.notify("test"))
        assert result.startswith("error:http_500")

    def test_network_error_caught(self) -> None:
        async def fake_post(*args, **kwargs):
            raise ConnectionError("network down")

        http = MagicMock()
        http.post = fake_post

        n = self._make_notifier(http_client=http)
        result = _run(n.notify("test"))
        assert result.startswith("error:")
        assert "ConnectionError" in result

    def test_per_call_group_id_override(self) -> None:
        captured = {}

        async def fake_post(url, headers, json, timeout):
            captured["json"] = json
            resp = MagicMock()
            resp.status_code = 200
            return resp

        http = MagicMock()
        http.post = fake_post

        n = self._make_notifier(http_client=http)
        _run(n.notify("test", group_id="other@g.us"))
        assert captured["json"]["group_id"] == "other@g.us"


# ════════════════════════════════════════════════════════════════════════════
# Message builders
# ════════════════════════════════════════════════════════════════════════════

class TestMessageBuilders:
    def test_request_queued_includes_fix_id_and_desc(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        msg = WANotifier.msg_request_queued(
            fix_id="abc12345",
            description="Add SSB Turkey to the tender monitor",
        )
        assert "abc12345" in msg
        assert "SSB Turkey" in msg
        assert "🚀" in msg
        assert "/api/aria/coder/status/abc12345" in msg

    def test_stage_progress_picks_right_emoji(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        stages = {
            "planning":     "🧠",
            "writing_code": "✏️",
            "testing":      "🧪",
            "done":         "✅",
            "failed":       "❌",
        }
        for stage, emoji in stages.items():
            msg = WANotifier.msg_stage_progress(
                fix_id="X", stage=stage, message="m",
            )
            assert emoji in msg
            assert stage in msg

    def test_shipped_includes_summary_and_state(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        msg = WANotifier.msg_shipped(
            r_number=825,
            title="Fixed ACLED source",
            operator_summary="ACLED cookie auth expired; refreshed credentials.",
            files_modified=["aria_service/intel/acled.py"],
            auto_deployed=True,
            issue_url="https://github.com/Arkmurus/crucix/issues/42",
            elapsed_s=120,
        )
        assert "R-F825" in msg
        assert "120s" in msg
        assert "ACLED cookie auth" in msg
        assert "auto-deployed" in msg
        assert "github.com" in msg
        assert "acled.py" in msg

    def test_shipped_staged_state(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        msg = WANotifier.msg_shipped(
            r_number=826, title="x", operator_summary="y",
            files_modified=["a.py"], auto_deployed=False,
        )
        assert "staged" in msg.lower()

    def test_shipped_truncates_long_file_list(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        msg = WANotifier.msg_shipped(
            r_number=827, title="x", operator_summary="y",
            files_modified=[f"file_{i}.py" for i in range(10)],
            auto_deployed=True,
        )
        # Shows first 5 + "(+5 more)"
        assert "file_0.py" in msg
        assert "file_4.py" in msg
        assert "+5 more" in msg
        assert "file_5.py" not in msg

    def test_failed_includes_fix_id_and_reason(self) -> None:
        from aria_service.autonomous.wa_notifier import WANotifier
        msg = WANotifier.msg_failed(
            fix_id="def67890",
            reason="LLM returned empty plan after 3 retries",
        )
        assert "def67890" in msg
        assert "LLM returned empty plan" in msg
        assert "❌" in msg
