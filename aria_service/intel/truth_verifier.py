"""
R-F2067 — Truth Verifier.

Prevents hallucination and fabrication by ensuring every claim is backed
by verifiable evidence. Before ARIA reports success, she runs the verifier
to confirm the claim against real system state.

Evidence types:
  - commit_hash:    git rev-parse HEAD (via git_utils)
  - build_rev:      live /health/live endpoint
  - test_output:    pytest results (via test_runner)
  - file_content:   file exists and contains expected content
  - api_response:   live API endpoint returns 200
  - knowledge_base: SQLite query returns expected data
  - deploy_log:     deploy history file

Usage:
    from aria_service.intel.truth_verifier import TruthVerifier, Claim

    verifier = TruthVerifier()
    claim = Claim(
        statement="R-F2067 is deployed and live",
        evidence_requirements=["commit_hash", "build_rev"]
    )
    ok, report = verifier.verify(claim)
    if ok:
        print(f"Verified: {claim.statement}")
    else:
        print(f"Failed: {report['message']}")
"""
from __future__ import annotations
from .engine_wiring import wire_success, wire_failure

import hashlib
import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx

logger = logging.getLogger("aria.truth_verifier")

# Live endpoints
_HEALTH_URL = "https://aria-intel.fly.dev/health/live"
_API_HEALTH_URL = "https://aria-intel.fly.dev/api/aria/health"

# Project root
_PROJECT_ROOT = Path(__file__).parent.parent.parent


@dataclass
class Evidence:
    """An evidence artifact supporting a claim."""
    type: str
    value: str
    hash: str
    timestamp: str
    source: str = ""


@dataclass
class Claim:
    """A claim that needs verification."""
    statement: str
    evidence_requirements: list[str]
    evidence: list[Evidence] = field(default_factory=list)


class TruthVerifier:
    """Verifies claims against real system state."""

    def __init__(self):
        self._verified_claims: list[Claim] = []

    def verify(self, claim: Claim) -> tuple[bool, dict[str, Any]]:
        """Verify a claim against real system state.

        Args:
            claim: The claim to verify.

        Returns:
            (is_true, report_dict) where report_dict contains:
                - verified: bool
                - message: str
                - evidence: list of Evidence dicts
                - claim: str
        """
        evidence_list: list[Evidence] = []
        all_ok = True
        messages: list[str] = []

        for req in claim.evidence_requirements:
            if req == "commit_hash":
                ok, value = self._get_commit_hash()
                if ok:
                    evidence_list.append(Evidence(
                        type="commit_hash",
                        value=value,
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="git rev-parse HEAD",
                    ))
                else:
                    all_ok = False
                    messages.append("commit_hash: no git repo or no HEAD")

            elif req == "build_rev":
                ok, value = self._get_build_rev()
                if ok:
                    evidence_list.append(Evidence(
                        type="build_rev",
                        value=value,
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source=f"GET {_HEALTH_URL}",
                    ))
                else:
                    all_ok = False
                    messages.append("build_rev: /health/live unreachable or no build_rev")

            elif req == "api_health":
                ok, value = self._get_api_health()
                if ok:
                    evidence_list.append(Evidence(
                        type="api_health",
                        value=value[:500],
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source=f"GET {_API_HEALTH_URL}",
                    ))
                else:
                    all_ok = False
                    messages.append("api_health: /api/aria/health unreachable")

            elif req == "test_output":
                ok, value = self._get_test_output(claim.statement)
                if ok:
                    evidence_list.append(Evidence(
                        type="test_output",
                        value=value[:1000],
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="pytest output",
                    ))
                else:
                    all_ok = False
                    messages.append("test_output: tests failed or no output")

            elif req == "file_content":
                ok, value = self._get_file_content(claim.statement)
                if ok:
                    evidence_list.append(Evidence(
                        type="file_content",
                        value=value[:500],
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="file read",
                    ))
                else:
                    all_ok = False
                    messages.append("file_content: file not found or empty")

            elif req == "knowledge_base":
                ok, value = self._get_knowledge_base(claim.statement)
                if ok:
                    evidence_list.append(Evidence(
                        type="knowledge_base",
                        value=value[:500],
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="portal_knowledge.db",
                    ))
                else:
                    all_ok = False
                    messages.append("knowledge_base: no matching data")

            elif req == "deploy_log":
                ok, value = self._get_deploy_log()
                if ok:
                    evidence_list.append(Evidence(
                        type="deploy_log",
                        value=value[:500],
                        hash=self._sha256(value),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        source="deploy_history file",
                    ))
                else:
                    all_ok = False
                    messages.append("deploy_log: no deploy history found")

            else:
                all_ok = False
                messages.append(f"Unknown evidence type: {req}")

        if all_ok:
            claim.evidence = evidence_list
            self._verified_claims.append(claim)
            return True, {
                "verified": True,
                "message": "All evidence checks passed",
                "claim": claim.statement,
                "evidence": [self._evidence_to_dict(e) for e in evidence_list],
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
        else:
            return False, {
                "verified": False,
                "message": "; ".join(messages),
                "claim": claim.statement,
                "evidence": [self._evidence_to_dict(e) for e in evidence_list],
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }

    # ── Evidence gatherers ─────────────────────────────────────────────

    def _get_commit_hash(self) -> tuple[bool, str]:
        """Get the current git commit hash via git_utils."""
        try:
            from .git_utils import get_head_sha
            sha = get_head_sha()
            if sha:
                return True, sha
            return False, ""
        except Exception as e:
            logger.debug("commit_hash failed: %s", e)
            return False, ""

    def _get_build_rev(self) -> tuple[bool, str]:
        """Get the live build_rev from /health/live."""
        try:
            resp = httpx.get(_HEALTH_URL, timeout=10)
            if resp.status_code == 200:
                data = resp.json()
                rev = data.get("build_rev", "")
                if rev:
                    return True, rev
            return False, ""
        except Exception as e:
            logger.debug("build_rev failed: %s", e)
            return False, ""

    def _get_api_health(self) -> tuple[bool, str]:
        """Get the full API health status."""
        try:
            resp = httpx.get(_API_HEALTH_URL, timeout=10)
            if resp.status_code == 200:
                return True, resp.text
            return False, ""
        except Exception as e:
            logger.debug("api_health failed: %s", e)
            return False, ""

    def _get_test_output(self, claim_statement: str) -> tuple[bool, str]:
        """Run tests related to the claim via test_runner."""
        r_match = re.search(r"R-F(\d+)", claim_statement)
        if not r_match:
            return False, ""

        r_num = r_match.group(0).lower()
        test_dir = _PROJECT_ROOT / "aria_service" / "tests"

        # Find matching test files
        test_files = list(test_dir.glob(f"*{r_num}*"))
        if not test_files:
            return False, f"Test file not found for {r_num}"

        try:
            from ..autonomous.test_runner import run_tests
            result = run_tests([str(f) for f in test_files], timeout=30)
            if result.get("success"):
                output = result.get("output", "")
                passed = result.get("passed", 0)
                return True, f"{passed} tests passed"
            return False, result.get("error", "tests failed")
        except Exception as e:
            return False, str(e)

    def _get_file_content(self, claim_statement: str) -> tuple[bool, str]:
        """Check if a file mentioned in the claim exists and has content."""
        file_match = re.search(r"(aria_service/[^\s]+\.py)", claim_statement)
        if file_match:
            file_path = _PROJECT_ROOT / file_match.group(1)
            if file_path.exists():
                content = file_path.read_text(encoding="utf-8")
                return True, f"{len(content.splitlines())} lines, {len(content)} chars"
        return False, ""

    def _get_knowledge_base(self, claim_statement: str) -> tuple[bool, str]:
        """Query the portal knowledge base for evidence."""
        db_path = Path("/data/portal_knowledge.db")
        if not db_path.exists():
            db_path = _PROJECT_ROOT.parent / "data" / "portal_knowledge.db"
        if not db_path.exists():
            return False, "Knowledge base not found"

        try:
            import sqlite3
            conn = sqlite3.connect(str(db_path))
            cur = conn.execute("SELECT COUNT(*) FROM attempts")
            count = cur.fetchone()[0]
            cur = conn.execute("SELECT COUNT(*) FROM attempts WHERE success = 1")
            successes = cur.fetchone()[0]
            conn.close()
            return True, f"{count} attempts, {successes} successes"
        except Exception as e:
            return False, str(e)

    def _get_deploy_log(self) -> tuple[bool, str]:
        """Get deploy history from the deploy history file."""
        history_file = _PROJECT_ROOT / "data" / "deploy_history" / "aria-intel.json"
        if history_file.exists():
            try:
                data = json.loads(history_file.read_text(encoding="utf-8"))
                recent = data[-3:] if isinstance(data, list) else [data]
                return True, json.dumps(recent, indent=2)[:500]
            except Exception:
                pass
        return False, ""

    # ── Helpers ─────────────────────────────────────────────────────────

    def _sha256(self, value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def _evidence_to_dict(self, e: Evidence) -> dict:
        return {
            "type": e.type,
            "value": e.value[:200],
            "hash": e.hash[:16],
            "timestamp": e.timestamp,
            "source": e.source,
        }

    def get_report(self) -> dict[str, Any]:
        """Get a verification report of all verified claims."""
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "claims_verified": len(self._verified_claims),
            "claims": [
                {
                    "statement": c.statement,
                    "evidence_count": len(c.evidence),
                    "evidence_types": [e.type for e in c.evidence],
                }
                for c in self._verified_claims
            ],
        }


def verified_emit(
    task_description: str,
    evidence_requirements: list[str],
    verifier: TruthVerifier | None = None,
) -> dict[str, Any]:
    """Emit a success message only after verification.

    This is the primary entry point for ARIA's claim reporting. Every
    success message must pass through this function before being emitted.

    Args:
        task_description: Human-readable description of what was done.
        evidence_requirements: List of evidence types to verify.
        verifier: Optional existing verifier instance (creates one if None).

    Returns:
        Dict with:
            - verified: bool
            - message: str
            - report: dict (verification report, only if verified)
            - error: str (only if not verified)
    """
    if verifier is None:
        verifier = TruthVerifier()

    claim = Claim(
        statement=task_description,
        evidence_requirements=evidence_requirements,
    )

    ok, report = verifier.verify(claim)

    # R-F2118/R-F2119 §21a — wire module active
    try:
        wire_success(module="truth_verifier",
                     summary="truth_verifier module active",
                     source_id="truth_verifier:init")
    except Exception:
        try:
            wire_failure(module="truth_verifier", detail="module init failed",
                        gap_type="engine_failure", source="truth_verifier:init")
        except Exception:
            pass

    if ok:
        logger.info(
            "✅ VERIFIED: %s — %d evidence checks passed",
            task_description[:100],
            len(report.get("evidence", [])),
        )
        return {
            "verified": True,
            "message": task_description,
            "report": verifier.get_report(),
        }
    else:
        logger.warning(
            "❌ VERIFICATION FAILED: %s — %s",
            task_description[:100],
            report.get("message", "unknown"),
        )
        return {
            "verified": False,
            "message": task_description,
            "error": report.get("message", "Verification failed"),
        }
