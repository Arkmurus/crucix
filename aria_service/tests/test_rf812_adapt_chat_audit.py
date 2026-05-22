"""R-F812 — Tests for scripts/train/adapt_chat_audit.py."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ is not on sys.path by default — add it for the import below.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT))

from scripts.train.adapt_chat_audit import (
    TIER_TO_SCORE,
    _extract_last_turn,
    _iter_chat_audit_files,
    adapt,
)


class TestExtractLastTurn:
    def test_simple_user_assistant_pair(self) -> None:
        record = {
            "messages": [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "world"},
            ],
        }
        result = _extract_last_turn(record)
        assert result == ("hello", "world")

    def test_multi_turn_extracts_last_pair(self) -> None:
        """For multi-turn sessions we want the LAST user→assistant pair,
        not the first."""
        record = {
            "messages": [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "first reply"},
                {"role": "user", "content": "second"},
                {"role": "assistant", "content": "second reply"},
            ],
        }
        result = _extract_last_turn(record)
        assert result == ("second", "second reply")

    def test_system_message_skipped(self) -> None:
        """System prompts at the start shouldn't pollute the extraction."""
        record = {
            "messages": [
                {"role": "system", "content": "you are aria"},
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ],
        }
        result = _extract_last_turn(record)
        assert result == ("hi", "hello")

    def test_no_assistant_returns_none(self) -> None:
        record = {"messages": [{"role": "user", "content": "lonely"}]}
        assert _extract_last_turn(record) is None

    def test_no_user_returns_none(self) -> None:
        record = {"messages": [{"role": "assistant", "content": "lonely"}]}
        assert _extract_last_turn(record) is None

    def test_empty_messages_returns_none(self) -> None:
        assert _extract_last_turn({"messages": []}) is None

    def test_missing_messages_key_returns_none(self) -> None:
        assert _extract_last_turn({"metadata": {}}) is None

    def test_non_string_content_returns_none(self) -> None:
        record = {
            "messages": [
                {"role": "user", "content": ["list", "not", "string"]},
                {"role": "assistant", "content": "reply"},
            ],
        }
        assert _extract_last_turn(record) is None


class TestAdapt:
    def _make_record(
        self,
        user: str = "What sanctions apply to Hikvision?",
        assistant: str = "A" * 200,  # >100 chars to pass min-length gate
        tier: str = "well_formed",
        grounded_rate: float = 0.0,
    ) -> dict:
        return {
            "messages": [
                {"role": "user", "content": user},
                {"role": "assistant", "content": assistant},
            ],
            "metadata": {
                "source": "chat_audit",
                "verification_tier": tier,
                "grounded_rate": grounded_rate,
                "trace_id": "abc-123",
            },
        }

    def test_happy_path(self) -> None:
        rec = self._make_record()
        adapted = adapt(rec)
        assert adapted is not None
        # The fields prepare_sft.py looks for
        assert "user_message" in adapted
        assert "response" in adapted
        assert "grounded_rate" in adapted
        assert "verification_status" in adapted
        assert adapted["user_message"] == "What sanctions apply to Hikvision?"
        assert len(adapted["response"]) == 200

    def test_well_formed_tier_scores_0_85(self) -> None:
        """well_formed tier maps to score 0.85 — clears R-F201's lower
        bar (0.5 + min-score-0.30) AND clears the default 0.80 cutoff."""
        adapted = adapt(self._make_record(tier="well_formed"))
        assert adapted is not None
        assert adapted["grounded_rate"] == 0.85
        assert adapted["verification_status"] == "well_formed"

    def test_excellent_tier_scores_0_95(self) -> None:
        adapted = adapt(self._make_record(tier="excellent"))
        assert adapted is not None
        assert adapted["grounded_rate"] == 0.95

    def test_partial_tier_scores_below_threshold(self) -> None:
        """partial / unknown tiers produce 0.55 — below the default
        SFT threshold of 0.80 → prepare_sft will reject them."""
        adapted = adapt(self._make_record(tier="partial"))
        assert adapted is not None
        assert adapted["grounded_rate"] == 0.55

    def test_short_response_rejected(self) -> None:
        """Responses < 100 chars are too short to teach anything — same
        gate as prepare_sft._harvest_to_sft."""
        rec = self._make_record(assistant="short")
        assert adapt(rec) is None

    def test_empty_user_rejected(self) -> None:
        rec = self._make_record(user="")
        assert adapt(rec) is None

    def test_meta_preserved(self) -> None:
        adapted = adapt(self._make_record())
        assert adapted is not None
        meta = adapted["meta"]
        assert meta["source"] == "chat_audit"
        assert meta["trace_id"] == "abc-123"
        assert meta["tier"] == "well_formed"

    def test_missing_tier_defaults_to_well_formed_label(self) -> None:
        """Records without a tier label fall through to default_score
        (0.85) and 'well_formed' label so prepare_sft's lower-bar path
        accepts them."""
        record = {
            "messages": [
                {"role": "user", "content": "q"},
                {"role": "assistant", "content": "A" * 200},
            ],
            # no metadata at all
        }
        adapted = adapt(record)
        assert adapted is not None
        # default_score is 0.85
        assert adapted["grounded_rate"] == 0.85
        assert adapted["verification_status"] == "well_formed"


class TestIterChatAuditFiles:
    def test_picks_up_yyyy_mm_dd_jsonl(self, tmp_path: Path) -> None:
        (tmp_path / "2026-05-22.jsonl").write_text("{}\n")
        (tmp_path / "2026-05-21.jsonl").write_text("{}\n")
        (tmp_path / "harvest-2026-05-22.jsonl").write_text("{}\n")
        (tmp_path / "manifest.json").write_text("{}")

        files = list(_iter_chat_audit_files(tmp_path))
        names = sorted([f.name for f in files])

        # Only YYYY-MM-DD.jsonl files — NOT harvest-* (those are
        # already in the right format) and NOT manifest.json.
        assert names == ["2026-05-21.jsonl", "2026-05-22.jsonl"]

    def test_empty_dir_returns_nothing(self, tmp_path: Path) -> None:
        assert list(_iter_chat_audit_files(tmp_path)) == []

    def test_missing_dir_returns_nothing(self, tmp_path: Path) -> None:
        ghost = tmp_path / "ghost"
        assert list(_iter_chat_audit_files(ghost)) == []


class TestTierToScore:
    def test_all_known_tiers_have_scores(self) -> None:
        # Regression guard — these three tier labels are produced by
        # training_export. A future rename would silently drop into
        # the `default_score` fallback path and could change the
        # acceptance ratio without an explicit decision.
        for tier in ("excellent", "well_formed", "partial"):
            assert tier in TIER_TO_SCORE

    def test_well_formed_clears_min_score_default(self) -> None:
        # default min-score is 0.80; well_formed must clear it
        assert TIER_TO_SCORE["well_formed"] >= 0.80
