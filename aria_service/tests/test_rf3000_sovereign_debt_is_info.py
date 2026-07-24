"""R-F3000 — sovereign macro debt is INFO context, not an AMBER risk flag.

On the Silverbrook run, UK central-government debt (130.7% of GDP) was flagged
AMBER against a UK PRIVATE company — analytically inert (it fires for the UK, US
and Japan alike) and it inflated the risk picture with a signal the reviewer then
had to discount. Source-read lock (repo convention for findings generated deep in
a layer, cf. web-security-rf2094): the sovereign-debt finding must be emitted as
severity 'info', never 'amber'.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "intel" / "dd_orchestrator.py"


def _sovereign_debt_finding_block() -> str:
    src = SRC.read_text(encoding="utf-8")
    # the Finding(...) call whose title is the sovereign-debt line
    idx = src.index("central-govt debt")
    start = src.rfind("report.compliance.findings.append(Finding(", 0, idx)
    assert start != -1, "sovereign-debt Finding block not found"
    end = src.index("))", idx)
    return src[start:end]


def test_rf3000_sovereign_debt_finding_is_info_not_amber():
    block = _sovereign_debt_finding_block()
    assert 'severity="info"' in block, "sovereign-debt macro finding must be INFO context"
    assert 'severity="amber"' not in block, "sovereign-debt macro must not be an AMBER risk flag"


def test_rf3000_title_is_neutral_context_not_elevated_alarm():
    block = _sovereign_debt_finding_block()
    # neutral 'context' framing, not the old 'Sovereign debt elevated:' alarm title
    assert "Sovereign macro context" in block
    assert "Sovereign debt elevated" not in block
