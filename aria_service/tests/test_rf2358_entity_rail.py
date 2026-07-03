"""R-F2358 — the streaming chat must emit a `progress` event carrying the RESOLVED entity,
so the aria.html entity rail (R-F735 reads `evt.entity` off a `progress` event) shows the
entity instead of staying stuck on "No active entity".

ARIA's original diagnosis proposed a separate `entity` SSE event — but the frontend has NO
handler for `type:'entity'` (it reads evt.entity off the `progress` event), so that would
have been a no-op. This asserts BOTH sides of the real contract; the live smoke (operator
sees the rail populate) is the definitive proof.
"""
import inspect
from pathlib import Path


def test_stream_emits_entity_on_progress_event():
    from aria_service import aria_engine
    src = inspect.getsource(aria_engine._aria_chat_stream_impl)
    assert "R-F2358" in src                       # wiring present (catches revert)
    assert ('_emit("progress"' in src) or ("_emit('progress'" in src)
    assert "entity=_rail_entity" in src           # the progress event carries the entity
    # uses the resolver's CANONICAL/query — NOT message.split(...)[0] (the fragile approach
    # I flagged to ARIA): the rail must show the entity, not the user's raw sentence.
    assert "_resolved_s" in src and "canonical" in src
    # R-F2358 follow-up: plausibility guard so the resolver's message-echo doesn't show the
    # whole sentence — require a known type AND a short, name-like value.
    assert '("person", "company")' in src
    assert "_rail_entity.split()" in src


def test_frontend_reads_entity_from_progress_event():
    html = Path("public/aria.html").read_text(encoding="utf-8")
    # receiving side: the progress handler pulls evt.entity into the rail
    assert "evt.type === 'progress'" in html
    assert "evt.entity" in html
    assert "_ariaCurrentEntity" in html
    # and the rail shows when EITHER an entity name OR sources exist (OR, not AND —
    # correcting ARIA's "requires BOTH" reading).
    assert "entity.name || (sources && sources.length)" in html
