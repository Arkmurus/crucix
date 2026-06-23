"""R-F1816 — investigated people + citations reach the DD report (web + wa).

R-F1812 computed per-person dossiers but dd_orchestrator dropped them and
dd_schema had no renderer — so neither WhatsApp nor web ever showed the named
individuals ("Zero named individuals"). This wires digital.people into the
report and renders it in BOTH concise (WhatsApp) and full (web) markdown.
"""
from aria_service.intel.dd_schema import ARKDDReport, DigitalSection, LayerStatus


def _report_with_people():
    r = ARKDDReport()
    r.digital.meta.status = LayerStatus.OK.value
    r.digital.people = [
        {"name": "Maria Silva", "role": "Director",
         "dossier": {"risk_assessment": "HIGH", "pep_status": "Possible PEP",
                     "red_flags": ["linked to a sanctioned entity", "shell-co ties"]}},
        {"name": "João Costa", "role": "Owner", "dossier": {"risk_assessment": "LOW"}},
    ]
    return r


def test_digital_section_has_people_field():
    assert hasattr(DigitalSection(), "people")
    assert DigitalSection().people == []


def test_people_render_in_whatsapp_concise():
    md = _report_with_people().render_markdown(concise=True)
    assert "People investigated: 2" in md
    assert "Maria Silva" in md and "João Costa" in md
    assert "risk=HIGH" in md and "Possible PEP" in md
    assert "linked to a sanctioned entity" in md  # red flag surfaced


def test_people_render_in_web_full():
    md = _report_with_people().render_markdown(concise=False)
    assert "People investigated: 2" in md
    assert "Maria Silva" in md


def test_no_people_section_when_empty():
    r = ARKDDReport()
    r.digital.meta.status = LayerStatus.OK.value
    md = r.render_markdown(concise=True)
    assert "People investigated" not in md
