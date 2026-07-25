"""R-F3079 — the chat sidebar must show a readable title for a document upload.

BROKEN PATH (reproduced live 2026-07-25): uploading a file sends
`[ATTACHED DOCUMENT: <name> · <method> · <n>%]` + the whole extracted document +
the user's question as ONE message. create_conversation titled on that raw
string, so the sidebar row read:

    [ATTACHED DOCUMENT: test_nda.txt · PLAINTEXT · 99%]
    MUTUAL N...

— the internal scaffold plus a slab of the document, across two lines.
"""
from aria_service.intel.conversation_store import _title_from_message


DOC = ("[ATTACHED DOCUMENT: test_nda.txt · PLAINTEXT · 99%]\n"
       "MUTUAL NON-DISCLOSURE AGREEMENT\n\nThis Agreement is made between "
       "Alpha Defence Ltd and Beta Systems GmbH.\n[/ATTACHED DOCUMENT]\n\n")


def test_document_upload_titles_on_the_users_question():
    title = _title_from_message(DOC + "Review this NDA and give me feedback")
    assert title == "Review this NDA and give me feedback", (
        f"got {title!r} — the sidebar must show what the human asked, not the "
        "extraction scaffold"
    )


def test_document_only_upload_falls_back_to_the_file_name():
    title = _title_from_message(DOC)
    assert title == "test_nda.txt", (
        f"got {title!r} — with no question, the document's NAME is the honest "
        "title for that turn"
    )


def test_unterminated_document_block_is_still_stripped():
    """A truncated upload may never emit the closing marker."""
    title = _title_from_message(
        "[ATTACHED DOCUMENT: contract.pdf · OCR · 71%]\nsome text that runs on")
    assert title == "contract.pdf", f"got {title!r}"


def test_a_normal_message_is_untouched():
    assert _title_from_message("what are the UK export control rules") == \
        "what are the UK export control rules"


def test_multi_line_first_message_collapses_to_one_row():
    title = _title_from_message("first line\nsecond line\n\nthird")
    assert "\n" not in title, "a newline in the title breaks the sidebar row"
    assert title == "first line second line third"


def test_empty_stays_empty_so_the_caller_can_default():
    assert _title_from_message("") == ""
    assert _title_from_message("   \n  ") == ""


def test_long_title_is_truncated_by_create_conversation(monkeypatch):
    """The 60-char cap still applies AFTER cleaning, not before."""
    import asyncio
    from aria_service.intel import conversation_store as cs

    captured = {}

    async def _fake_hset(key, mapping):
        if ":conv:meta:" in key:
            captured.update(mapping)

    async def _fake_zadd(*a, **k):
        return None

    monkeypatch.setattr(cs.rs, "hset", _fake_hset)
    monkeypatch.setattr(cs.rs, "zadd", _fake_zadd)

    long_q = "please review " + ("x" * 200)
    asyncio.run(cs.create_conversation("u1", "s1", DOC + long_q))
    assert captured["title"].endswith("...")
    assert len(captured["title"]) == 63          # 60 + "..."
    assert captured["title"].startswith("please review "), \
        "truncation must apply to the CLEANED question, not the raw scaffold"
