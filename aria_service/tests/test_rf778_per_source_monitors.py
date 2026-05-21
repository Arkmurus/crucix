"""R-F778 — per-source dedicated monitor tasks (Phase 1, 8 monitors).

Operator mandate: "she needs to be able to create her own bots that will
seat and monitor a specific website, specific source of intel etc."

Phase 1 ships eight focused monitor tasks each watching one high-value
source from the R-F777 expansion: CISA KEV (cyber/defence-sector),
LME copper (commodities pricing), Equasis (dark-fleet sanctions),
Polymarket (geopolitical foresight), ransomware.live (defence-sector
cyber), MITRE ATT&CK (threat-actor publishing), Bellingcat (specialist
OSINT), OPEC MMR (producer-state defence budgets).

Each monitor:
  - Uses the existing deep_research tool so the constitutional pipeline
    (clauses 17/18/19) applies on every fire
  - Has a conservative cron cadence (no monitor fires more than every 6h)
  - Carries a focused escalation keyword set
  - Routes to [mem0, intel_ledger]; WA is intentionally empty
  - Is enabled by default (the engine itself is still gated by
    ARIA_AUTONOMOUS_ENABLED, and dry-run routes output to the R-F774
    visibility ledger so nothing reaches the outside world)

This regression test pins the eight tasks so a future tasks.yaml refactor
can't silently delete them. It also confirms the existing tasks.py loader
can parse them — proves the monitor pattern uses no new infrastructure.
"""
from __future__ import annotations

from pathlib import Path

import yaml


_TASKS_YAML = Path(__file__).resolve().parents[1] / "autonomous" / "tasks.yaml"

# (task_id, expected_cron, expected_priority, must_have_mem0_tag, must_have_escalate_substr)
_R_F778_MONITORS = [
    ("MONITOR-CISA-KEV",            "0 6,18 * * *",   "HIGH",   "defence_sector",  "actively exploited"),
    ("MONITOR-LME-COPPER",          "0 9 * * mon-fri","MEDIUM", "copper",          "repricing"),
    ("MONITOR-EQUASIS-DARK-FLEET",  "0 7 * * mon",    "HIGH",   "dark_fleet",      "AIS spoofing"),
    ("MONITOR-POLYMARKET-GEO",      "30 */6 * * *",   "MEDIUM", "polymarket",      "regime change"),
    ("MONITOR-RANSOMWARE-DEFENCE",  "0 10 * * *",     "HIGH",   "ransomware",      "defence contractor"),
    ("MONITOR-MITRE-ATTACK",        "0 8 * * tue",    "MEDIUM", "mitre",           "APT"),
    ("MONITOR-BELLINGCAT-WAGNER",   "0 14 * * *",     "MEDIUM", "bellingcat",      "Wagner"),
    ("MONITOR-OPEC-MMR",            "0 11 16 * *",    "LOW",    "opec",            "production cut"),
]


def _parse_tasks_yaml() -> dict:
    """Read + parse tasks.yaml directly. Bypassing tasks.load_tasks() so
    this test can run without the full aria_service import chain — the
    only thing under test is the YAML file itself."""
    with open(_TASKS_YAML, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _tasks_by_id() -> dict:
    data = _parse_tasks_yaml()
    return {t["id"]: t for t in (data.get("tasks") or []) if isinstance(t, dict) and t.get("id")}


def test_rf778_all_eight_monitors_present():
    """Every R-F778 monitor must be in tasks.yaml."""
    tasks = _tasks_by_id()
    missing = [tid for tid, *_ in _R_F778_MONITORS if tid not in tasks]
    assert not missing, (
        f"R-F778 regression: monitor tasks dropped from tasks.yaml: {missing}"
    )


def test_rf778_crons_correct():
    """Each monitor must carry its expected cron expression."""
    tasks = _tasks_by_id()
    mismatches = []
    for tid, expected_cron, _prio, _tag, _esc in _R_F778_MONITORS:
        actual = tasks.get(tid, {}).get("cron")
        if actual != expected_cron:
            mismatches.append((tid, expected_cron, actual))
    assert not mismatches, f"R-F778 cron drift: {mismatches}"


def test_rf778_priorities_correct():
    """Each monitor carries its tier priority — HIGH ones surface first."""
    tasks = _tasks_by_id()
    mismatches = []
    for tid, _cron, expected_prio, _tag, _esc in _R_F778_MONITORS:
        actual = tasks.get(tid, {}).get("priority")
        if actual != expected_prio:
            mismatches.append((tid, expected_prio, actual))
    assert not mismatches, f"R-F778 priority drift: {mismatches}"


def test_rf778_mem0_tag_present():
    """Each monitor carries its anchor mem0 tag so the brain can group
    autonomous outputs by source."""
    tasks = _tasks_by_id()
    missing = []
    for tid, _cron, _prio, expected_tag, _esc in _R_F778_MONITORS:
        tags = tasks.get(tid, {}).get("mem0_tags") or []
        if expected_tag not in tags:
            missing.append((tid, expected_tag, tags))
    assert not missing, f"R-F778 mem0_tag drift: {missing}"


def test_rf778_escalation_keyword_present():
    """Each monitor carries at least one source-specific escalation
    keyword. A monitor with no escalation is just a poller — the
    keyword is what makes it actionable."""
    tasks = _tasks_by_id()
    missing = []
    for tid, _cron, _prio, _tag, expected_esc in _R_F778_MONITORS:
        esc_list = tasks.get(tid, {}).get("escalate_if") or []
        if not any(expected_esc.lower() in (e or "").lower() for e in esc_list):
            missing.append((tid, expected_esc, esc_list))
    assert not missing, f"R-F778 escalation keyword missing: {missing}"


def test_rf778_uses_deep_research_tool():
    """Each monitor's tool_chain must use the existing deep_research
    tool (not a new tool type). Proves R-F778 ships no new tool
    infrastructure — just denser config."""
    tasks = _tasks_by_id()
    wrong_tool = []
    for tid, *_ in _R_F778_MONITORS:
        chain = tasks.get(tid, {}).get("tool_chain") or []
        if not chain or (chain[0] or {}).get("tool") != "deep_research":
            wrong_tool.append((tid, chain))
    assert not wrong_tool, (
        f"R-F778 regression: monitors must use deep_research tool: {wrong_tool}"
    )


def test_rf778_dry_run_safe_no_whatsapp_group():
    """Every monitor must leave whatsapp_group_id empty. The R-F774
    dry-run guard suppresses real WA delivery, but a populated group_id
    would still route through the WA delivery code path on dry-run-off.
    Defence-in-depth: keep the slot empty so an accidental dry-run flip
    doesn't accidentally start sending messages."""
    tasks = _tasks_by_id()
    populated = []
    for tid, *_ in _R_F778_MONITORS:
        gid = tasks.get(tid, {}).get("whatsapp_group_id")
        if gid:
            populated.append((tid, gid))
    assert not populated, (
        f"R-F778 monitors must not pre-configure WA group: {populated}"
    )


def test_rf778_cost_caps_within_budget():
    """No monitor may carry a cost_cap above $0.30. The daily cap
    (ARIA_AUTONOMOUS_DAILY_COST_CAP_USD, default $1) absorbs the fleet
    only if individual tasks stay small."""
    tasks = _tasks_by_id()
    over_budget = []
    for tid, *_ in _R_F778_MONITORS:
        cap = float(tasks.get(tid, {}).get("cost_cap_usd") or 0)
        if cap > 0.30:
            over_budget.append((tid, cap))
    assert not over_budget, (
        f"R-F778 monitor cost caps over budget (>$0.30): {over_budget}"
    )


def test_rf778_tasks_module_can_load_yaml():
    """End-to-end: the existing tasks.py loader must parse the new
    monitors without error. This is the proof that R-F778 ships no
    new infrastructure — the engine accepts the new tasks as-is."""
    from aria_service.autonomous import tasks as tasks_mod
    loaded = tasks_mod.load_tasks()
    for tid, *_ in _R_F778_MONITORS:
        assert tid in loaded, (
            f"R-F778 regression: tasks_mod.load_tasks() did not return {tid}. "
            f"Loaded count: {len(loaded)}"
        )
