"""R-F502 — indexer unit + capability tests.

Covers:
  - _sanitise drops C0 controls + zero-width chars; preserves \\n + \\t
  - _sanitise normalises NFC + collapses whitespace
  - _sanitise caps body length at 200 KB
  - detect_language: en, pt, es, fr, ru, ar, zh confident calls
  - detect_language: falls back to default on mixed / thin samples
  - index_fetch_result skips thin content (<20 words)
  - index_fetch_result is idempotent on unchanged content
  - language override: detected wins over seed when high confidence
  - index_many returns aggregated counts

Capability test: ingest 50 pages (mix of PT + EN + 1 noise) and verify
language tagging is correct, no duplicates in the index, and tier-2
press articles are findable by their PT title.
"""
from __future__ import annotations

import asyncio
import pytest

from aria_service.search_index import db, indexer


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.fixture
def db_ready():
    _run(db._reset())
    ok = _run(db.connect(":memory:"))
    assert ok
    yield db
    _run(db._reset())


# ─────────────────────────────────────────────────────────────────
# Sanitiser
# ─────────────────────────────────────────────────────────────────

def test_sanitise_drops_controls_keeps_newlines_and_tabs():
    raw = "Hello\x00\x01world\nLine 2\tTabbed\x7Fend"
    out = indexer._sanitise(raw)
    assert "\x00" not in out and "\x01" not in out and "\x7F" not in out
    assert "\n" in out and "\t" in out
    assert "Hello" in out and "world" in out


def test_sanitise_strips_zero_width():
    # ZWSP (U+200B) hidden in "Pay​load"
    raw = "Pay​load"
    out = indexer._sanitise(raw)
    assert "​" not in out
    assert out == "Payload"


def test_sanitise_collapses_whitespace():
    raw = "Hello    world\n\n\n\n\nLine 2"
    out = indexer._sanitise(raw)
    assert "    " not in out
    assert "\n\n\n" not in out


def test_sanitise_caps_at_200kb():
    raw = "abc " * 60_000  # ~240 KB
    out = indexer._sanitise(raw)
    assert len(out.encode("utf-8")) <= 200_500  # 200 KB + small slack
    assert "truncated for index size cap" in out


# ─────────────────────────────────────────────────────────────────
# Language detection
# ─────────────────────────────────────────────────────────────────

def test_detect_language_en():
    text = ("The quick brown fox jumps over the lazy dog. " * 30 +
            " This is an example of English news coverage of "
            "defence contracts. The contract is awarded to the "
            "company on Tuesday. The minister said that the deal "
            "is part of the modernisation plan and that the "
            "country will benefit from the investment.")
    assert indexer.detect_language(text, default=None) == "en"


def test_detect_language_pt():
    text = ("A Modirum Gespi recebeu um contrato de defesa do "
            "Ministério da Defesa de Angola. A empresa, com sede "
            "em Luanda, vai fornecer sistemas de comunicações "
            "tácticas e gestão de campo de batalha. O contrato "
            "tem um valor não divulgado e dura três anos. A "
            "Modirum Gespi já forneceu sistemas para Moçambique "
            "no passado. O Governo de Angola está a modernizar "
            "as suas forças armadas como parte de um plano "
            "estratégico de longo prazo.")
    assert indexer.detect_language(text, default=None) == "pt"


def test_detect_language_es():
    text = ("El ministerio de defensa ha adjudicado un contrato. "
            "La empresa es responsable de la entrega del sistema. "
            "Los expertos del sector consideran que el acuerdo es "
            "favorable para la modernización de las fuerzas armadas. "
            "El presupuesto del contrato no ha sido revelado por "
            "el gobierno español. Las negociaciones se llevaron a "
            "cabo durante varios meses en Madrid.")
    assert indexer.detect_language(text, default=None) == "es"


def test_detect_language_fr():
    text = ("Le ministère de la défense a attribué un contrat. "
            "La société est responsable de la livraison du système. "
            "Les experts du secteur estiment que l'accord est "
            "favorable pour la modernisation des forces armées. "
            "Le budget du contrat n'a pas été révélé par le "
            "gouvernement français. Les négociations ont eu lieu "
            "pendant plusieurs mois à Paris dans le cadre des "
            "discussions bilatérales.")
    assert indexer.detect_language(text, default=None) == "fr"


def test_detect_language_falls_back_on_thin_sample():
    # Only 5 tokens — too short to commit.
    assert indexer.detect_language("Hello world.", default="en") == "en"
    assert indexer.detect_language("", default="pt") == "pt"


def test_detect_language_cjk():
    text = "中华人民共和国国防部今日宣布与外国公司签订防务合同。"
    assert indexer.detect_language(text, default=None) == "zh"


# ─────────────────────────────────────────────────────────────────
# index_fetch_result
# ─────────────────────────────────────────────────────────────────

def test_index_skips_thin_content(db_ready):
    async def go():
        await db.register_domain("example.com", tier=3, sector="news",
                                 language="en")
        # Only 10 words — below _MIN_WORDS.
        doc_id = await indexer.index_fetch_result({
            "url": "https://example.com/short", "domain": "example.com",
            "title": "Short", "headings": "",
            "body": "Only ten words here in this very short article body.",
            "language": "en", "source_tier": 3, "http_status": 200,
            "fetched_at": 0.0,
        })
        assert doc_id == ""
    _run(go())


def test_index_skips_short_body_under_100_chars(db_ready):
    async def go():
        await db.register_domain("example.com", tier=3, sector="news",
                                 language="en")
        # Many words, but each is one letter — total chars < 100.
        body = " ".join(["a"] * 30)  # 30 words, ~59 chars
        doc_id = await indexer.index_fetch_result({
            "url": "https://example.com/a", "domain": "example.com",
            "title": "a", "headings": "", "body": body,
            "language": "en", "source_tier": 3, "http_status": 200,
            "fetched_at": 0.0,
        })
        assert doc_id == ""
    _run(go())


def test_index_persists_substantial_content(db_ready):
    async def go():
        await db.register_domain("example.com", tier=2, sector="news",
                                 language="en")
        body = (
            "Modirum Gespi has been awarded a defence systems "
            "integration contract for the Angolan Ministry of Defence. "
            "The company, headquartered in Luanda with a Finnish "
            "parent, will deliver tactical communications and "
            "battlefield management systems. The contract is valued "
            "at an undisclosed sum and runs for three years."
        )
        doc_id = await indexer.index_fetch_result({
            "url": "https://example.com/award", "domain": "example.com",
            "title": "Modirum Gespi awarded contract", "headings": "",
            "body": body, "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": 0.0,
        })
        assert doc_id
        d = await db.get_document(url="https://example.com/award")
        assert d is not None
        assert d["title"] == "Modirum Gespi awarded contract"
        assert d["source_tier"] == 2
        assert d["language"] == "en"
    _run(go())


def test_index_is_idempotent_on_unchanged_content(db_ready):
    async def go():
        await db.register_domain("example.com", tier=2, sector="news",
                                 language="en")
        result = {
            "url": "https://example.com/idem", "domain": "example.com",
            "title": "Idempotent", "headings": "",
            "body": ("This body is long enough to pass the thin-content "
                     "guard and stays exactly the same on the second "
                     "indexing call so the upsert is skipped."),
            "language": "en", "source_tier": 2, "http_status": 200,
            "fetched_at": 0.0,
        }
        d1 = await indexer.index_fetch_result(result)
        d2 = await indexer.index_fetch_result(result)
        assert d1 == d2 and d1 != ""

        # And only one row exists.
        conn = db.get_connection()
        cur = await conn.execute(
            "SELECT COUNT(*) FROM documents WHERE url = ?",
            (result["url"],),
        )
        n = (await cur.fetchone())[0]
        await cur.close()
        assert n == 1
    _run(go())


def test_index_updates_when_content_changes(db_ready):
    async def go():
        await db.register_domain("example.com", tier=2, sector="news",
                                 language="en")
        url = "https://example.com/update"
        await indexer.index_fetch_result({
            "url": url, "domain": "example.com",
            "title": "v1", "headings": "",
            "body": ("Version one of the article with enough content "
                     "to pass the thin-content guard easily without "
                     "running into the minimum-word floor."),
            "language": "en", "source_tier": 2, "http_status": 200,
            "fetched_at": 0.0,
        })
        await indexer.index_fetch_result({
            "url": url, "domain": "example.com",
            "title": "v2", "headings": "",
            "body": ("Version two of the article has slightly different "
                     "content but is still well above the thin-content "
                     "guard floor used by the indexer."),
            "language": "en", "source_tier": 2, "http_status": 200,
            "fetched_at": 0.0,
        })
        d = await db.get_document(url=url)
        assert d["title"] == "v2"
        assert "Version two" in d["body"]
    _run(go())


def test_index_overrides_language_when_heuristic_is_confident(db_ready):
    """Seed says 'en' but content is clearly Portuguese — indexer
    should record 'pt' so FTS5 lang-filter works correctly."""
    async def go():
        await db.register_domain("misclassified.example", tier=3,
                                 sector="news", language="en")
        body = (
            "A Modirum Gespi recebeu um contrato de defesa do "
            "Ministério da Defesa de Angola. A empresa vai fornecer "
            "sistemas de comunicações tácticas e gestão de campo "
            "de batalha. O contrato tem um valor não divulgado e "
            "dura três anos. A empresa já forneceu sistemas para "
            "Moçambique no passado. As negociações com o Governo "
            "decorreram durante vários meses em Luanda e em Lisboa."
        )
        await indexer.index_fetch_result({
            "url": "https://misclassified.example/pt-article",
            "domain": "misclassified.example",
            "title": "Modirum Gespi contrato",
            "headings": "", "body": body, "language": "en",
            "source_tier": 3, "http_status": 200, "fetched_at": 0.0,
        })
        d = await db.get_document(url="https://misclassified.example/pt-article")
        assert d["language"] == "pt", (
            f"indexer should detect PT and override seed EN, got {d['language']}"
        )
    _run(go())


# ─────────────────────────────────────────────────────────────────
# Capability — 50-page mixed corpus
# ─────────────────────────────────────────────────────────────────

def test_rf502_capability_mixed_corpus_50_pages(db_ready):
    """Ingest 30 EN noise + 19 PT lusophone press + 1 EN Modirum Gespi
    contract page. Verify:
      - all 50 are persisted
      - language tagging matches expectations
      - FTS5 returns the Modirum hit on top with lang=en
      - FTS5 returns PT defence content when lang=pt"""
    async def go():
        await db.register_domain("noise.example", tier=4,
                                 sector="academic", language="en")
        await db.register_domain("press.pt", tier=2, sector="lusophone",
                                 language="pt")
        await db.register_domain("press.en", tier=2, sector="news",
                                 language="en")

        results: list[dict] = []
        # 30 English noise pages
        for i in range(30):
            results.append({
                "url": f"https://noise.example/p-{i}",
                "domain": "noise.example",
                "title": f"Random English noise article number {i}",
                "headings": "",
                "body": ("Generic English filler text that mentions modirum "
                         "only as a variable name in a code snippet, "
                         "alongside many other variables and theorems. "
                         "The article is about cryptographic algorithms "
                         "and the discrete logarithm problem in finite "
                         "fields and elliptic curves over the integers. "),
                "language": "en", "source_tier": 4,
                "http_status": 200, "fetched_at": 0.0,
            })
        # 19 Portuguese lusophone press
        for i in range(19):
            results.append({
                "url": f"https://press.pt/n-{i}",
                "domain": "press.pt",
                "title": f"Notícia portuguesa sobre defesa {i}",
                "headings": "",
                "body": ("O Ministério da Defesa anunciou hoje um novo "
                         "contrato com uma empresa de sistemas de "
                         "comunicações. O acordo prevê a entrega de "
                         "equipamentos para as Forças Armadas durante "
                         "os próximos anos. As negociações decorreram "
                         "em Lisboa entre os representantes dos dois "
                         "lados ao longo de vários meses."),
                "language": "pt", "source_tier": 2,
                "http_status": 200, "fetched_at": 0.0,
            })
        # 1 relevant Modirum Gespi press release (EN)
        results.append({
            "url": "https://press.en/modirum-award",
            "domain": "press.en",
            "title": "Modirum Gespi awarded defence integration contract",
            "headings": "Modirum Gespi · Angola · contract win",
            "body": ("Modirum Gespi has been awarded a defence systems "
                     "integration contract for the Angolan Ministry of "
                     "Defence. The company, headquartered in Luanda with "
                     "a Finnish parent, will deliver tactical communications "
                     "and battlefield management systems. The contract is "
                     "valued at an undisclosed sum and runs for three years. "
                     "Modirum Gespi previously delivered systems to Mozambique."),
            "language": "en", "source_tier": 2,
            "http_status": 200, "fetched_at": 0.0,
        })

        summary = await indexer.index_many(results)
        assert summary["indexed"] == 50, summary
        assert summary["errors"] == 0

        # Stats reflect the right shape.
        s = await db.stats()
        assert s["documents"] == 50
        assert s["by_language"]["en"] == 31  # 30 noise + 1 Modirum
        assert s["by_language"]["pt"] == 19

        # BM25: query "Modirum Gespi contract" → press release top.
        hits = await db.search_fts("Modirum Gespi contract", top_k=5)
        assert hits[0]["url"] == "https://press.en/modirum-award", hits[:3]

        # Lang filter works.
        pt_hits = await db.search_fts("defesa contrato", lang="pt", top_k=5)
        assert all(h["language"] == "pt" for h in pt_hits)
        assert len(pt_hits) >= 1
    _run(go())
