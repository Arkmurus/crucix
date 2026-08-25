"""build_export_control_curriculum — Claude-authored multilingual export-control
curriculum for ARIA-LLM (R-F4334 / C-280).

WHY THIS EXISTS. The 500-Q eval scores 110 questions (22%) in eleven languages
about national defence export-control regimes, and v0.7 scored 6/110. Three
successive grounded cycles plateaued at ~0.308 because of a fact nobody had
measured: THE TEACHER DOES NOT KNOW THE MATERIAL. Asked directly, DeepSeek —
which generates the grounded corpus and judges the eval — answers:

    CIEEMG UNKNOWN   SBDU UNKNOWN   JIMDDU UNKNOWN
    ANCEX  UNKNOWN   SSB  UNKNOWN   BAFA   KNOWS

5 of 6. A curriculum distilled from a teacher cannot contain what the teacher
lacks, so no amount of DeepSeek-generated data could ever close that gap.

And it is not a rubric artifact. Re-judging those questions with the strict
grounding rubric LIFTED (R-F4332/R-F4333) still scored 36/175 = 0.206 — she
genuinely does not know this material.

OPERATOR DIRECTIVE 2026-08-26: "we dont want deepseek to train aria anymore we
want you to train aria." Claude authors the curriculum HERE, in-session, and
writes it to a corpus file. This does NOT point aria-intel at the Anthropic API
— that would breach RULE ONE (CLAUDE.md section 17, anthropic is DD-only),
which is operator-codified and took DD down when it was broken. ARIA's runtime
stays Anthropic-free; only the training DATA is Claude-authored.

CONTAMINATION DISCIPLINE — read before editing this file.
  * The facts below are authored from general domain knowledge of national
    export-control regimes. They are NOT lifted from the eval's
    `expected_answer` fields, and no eval question is reproduced.
  * `training_corpus_manifest.py` checks PROMPT overlap only. It cannot see
    answer-level contamination, so that discipline is human, not mechanical.
  * The overlap between this material and the eval is EXPECTED and legitimate:
    the eval measures knowledge of this domain. Teaching the domain is not
    teaching the test. Reproducing its question/answer pairs would be.

WHY THE ANSWERS ARE AUTHORED AS DATA, NOT TEMPLATED. The first version of this
file templated a per-language lead sentence and left the body in English,
producing code-switched rows like

    "Yetkili kurum Savunma Sanayii Baskanligi (SSB), export licences are
     issued with the Ministry of National Defence..."

Training on that teaches CODE-SWITCHING, which is worse than not training at
all: the eval asks in-language and grades an in-language answer. It was caught
by READING the generated rows. Every answer below is therefore written whole in
its own language.

COVERAGE IS NARROW AND CORRECT, NOT BROAD AND WRONG. Only regimes whose facts
are high-confidence and only languages whose text is authored in full. A
missing row costs a mark; a wrong row trains a fabrication into a
due-diligence product. Section 24 requires a real dataset-quality review before
any paid cycle — a domain reviewer should sign these off before they become
canonical, and `confidence` is on every row so that review is possible.

Usage:
  python scripts/train/build_export_control_curriculum.py \
      --out data/training/aria_export_control_v1.jsonl
  python scripts/admin/training_corpus_manifest.py --record   # MUST pass
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

SYSTEM = (
    "You are ARIA, a due-diligence intelligence agent. Answer in the SAME "
    "language the question is asked in. Name the specific authority, its "
    "acronym and the governing instrument. If you do not know, say so — never "
    "invent an agency, a statute or a treaty status."
)

PAIRS: list[dict] = [
    # --- each country in its own language --------------------------------
    {
        "id": "fr_fr", "lang": "fr", "confidence": "high",
        "q": "En français : quelle autorité contrôle les exportations de matériel de guerre en France, et sur quelle base juridique ?",
        "a": (
            "En France, les exportations de matériel de guerre sont autorisées par la CIEEMG "
            "(Commission Interministérielle pour l'Étude des Exportations de Matériels de Guerre), "
            "dont les travaux sont coordonnés par le SGDSN (Secrétariat général de la défense et "
            "de la sécurité nationale).\n\n"
            "La licence délivrée est une AEMG (Autorisation d'Exportation de Matériels de Guerre), "
            "régie par le Code de la défense.\n\n"
            "Les biens à double usage relèvent d'un circuit distinct, le SBDU (Service des Biens à "
            "Double Usage), au titre du règlement (UE) 2021/821.\n\n"
            "La France est État Partie au Traité sur le commerce des armes (TCA)."
        ),
    },
    {
        "id": "es_es", "lang": "es", "confidence": "high",
        "q": "En español: ¿qué autoridad controla las exportaciones de material de defensa de España y bajo qué norma?",
        "a": (
            "En España la autoridad competente es la JIMDDU (Junta Interministerial Reguladora del "
            "Comercio Exterior de Material de Defensa y de Doble Uso), adscrita a la Secretaría de "
            "Estado de Comercio.\n\n"
            "El régimen se rige por la Ley 53/2007, de control del comercio exterior de material "
            "de defensa y de doble uso, desarrollada por el Real Decreto 679/2014.\n\n"
            "Los productos de doble uso se rigen además por el Reglamento (UE) 2021/821.\n\n"
            "España es Estado Parte del Tratado sobre el Comercio de Armas (TCA)."
        ),
    },
    {
        "id": "de_de", "lang": "de", "confidence": "high",
        "q": "Auf Deutsch: Welche Behörde kontrolliert die deutschen Rüstungsexporte und auf welcher Rechtsgrundlage?",
        "a": (
            "Zuständig ist das BAFA (Bundesamt für Wirtschaft und Ausfuhrkontrolle) im "
            "Geschäftsbereich des Bundesministeriums für Wirtschaft.\n\n"
            "Rechtsgrundlage sind das Außenwirtschaftsgesetz (AWG) und die "
            "Außenwirtschaftsverordnung (AWV). Kriegswaffen unterliegen zusätzlich dem "
            "Kriegswaffenkontrollgesetz (KrWaffKontrG).\n\n"
            "Güter mit doppeltem Verwendungszweck fallen unter die EU-Verordnung 2021/821.\n\n"
            "Deutschland ist Vertragsstaat des Vertrags über den Waffenhandel (ATT)."
        ),
    },
    {
        "id": "ro_ro", "lang": "ro", "confidence": "high",
        "q": "În română: ce autoritate controlează exporturile de produse militare din România și în baza cărui cadru legal?",
        "a": (
            "Autoritatea competentă este ANCEX (Agenția Națională de Control al Exporturilor), "
            "aflată în subordinea Ministerului Afacerilor Externe.\n\n"
            "ANCEX emite licențele de export pentru produsele militare și pentru produsele cu "
            "dublă utilizare, aplicând Poziția comună 2008/944/PESC a Uniunii Europene.\n\n"
            "Produsele cu dublă utilizare intră sub incidența Regulamentului (UE) 2021/821.\n\n"
            "România este stat parte la Tratatul privind comerțul cu arme (TCA)."
        ),
    },
    {
        "id": "tr_tr", "lang": "tr", "confidence": "high",
        "q": "Türkçe: Türkiye'nin savunma sanayii ihracat rejimini hangi kurum yönetir ve hangi mevzuata dayanır?",
        "a": (
            "Türkiye'de savunma sanayii ihracatı SSB (Savunma Sanayii Başkanlığı) tarafından "
            "yürütülür; ihracat lisansları MSB (Millî Savunma Bakanlığı) ile birlikte "
            "düzenlenir.\n\n"
            "Hukuki dayanak, harp araç ve gereçleri üreten özel sanayi kuruluşlarının denetimine "
            "ilişkin 5201 sayılı Kanun'dur.\n\n"
            "Çift kullanımlı ürünler Ticaret Bakanlığı tarafından idare edilir.\n\n"
            "Türkiye Silah Ticareti Antlaşması'nı (ATT) 2013'te imzalamış ancak onaylamamıştır; "
            "bu nedenle taraf devlet değil, imzacı devlettir."
        ),
    },
    # --- cross-cutting regimes, asked in several languages ---------------
    {
        "id": "us_fr", "lang": "fr", "confidence": "high",
        "q": "En français : comment les États-Unis contrôlent-ils leurs exportations de défense ?",
        "a": (
            "Aux États-Unis, le matériel inscrit à l'USML relève de l'ITAR (International Traffic "
            "in Arms Regulations), administré par la DDTC (Directorate of Defense Trade Controls) "
            "du Département d'État.\n\n"
            "Les biens à double usage relèvent de l'EAR (Export Administration Regulations), "
            "administré par le BIS (Bureau of Industry and Security) du Département du Commerce, "
            "au titre de la CCL.\n\n"
            "Les sanctions économiques et la liste SDN sont gérées par l'OFAC (Office of Foreign "
            "Assets Control) du Trésor.\n\n"
            "Les États-Unis ont signé le TCA en 2013 puis retiré leur signature en 2019 : ils n'en "
            "sont pas État Partie."
        ),
    },
    {
        "id": "us_es", "lang": "es", "confidence": "high",
        "q": "En español: ¿cómo controla Estados Unidos las exportaciones de defensa?",
        "a": (
            "En Estados Unidos el material incluido en la USML se rige por ITAR (International "
            "Traffic in Arms Regulations), administrado por la DDTC (Directorate of Defense Trade "
            "Controls) del Departamento de Estado.\n\n"
            "Los productos de doble uso se rigen por EAR (Export Administration Regulations), "
            "administrado por BIS (Bureau of Industry and Security) del Departamento de Comercio, "
            "conforme a la CCL.\n\n"
            "Las sanciones y la lista SDN corresponden a OFAC (Office of Foreign Assets Control) "
            "del Tesoro.\n\n"
            "Estados Unidos firmó el TCA en 2013 y retiró su firma en 2019; no es Estado Parte."
        ),
    },
    {
        "id": "us_de", "lang": "de", "confidence": "high",
        "q": "Auf Deutsch: Wie kontrollieren die USA ihre Rüstungsexporte?",
        "a": (
            "In den USA unterliegen Güter der USML den ITAR (International Traffic in Arms "
            "Regulations), verwaltet von der DDTC (Directorate of Defense Trade Controls) im "
            "Außenministerium.\n\n"
            "Dual-Use-Güter fallen unter die EAR (Export Administration Regulations), verwaltet "
            "vom BIS (Bureau of Industry and Security) im Handelsministerium, auf Grundlage der "
            "CCL.\n\n"
            "Sanktionen und die SDN-Liste verantwortet das OFAC (Office of Foreign Assets Control) "
            "im Finanzministerium.\n\n"
            "Die USA haben den ATT 2013 unterzeichnet, die Unterschrift 2019 zurückgezogen und "
            "sind kein Vertragsstaat."
        ),
    },
    {
        "id": "eu_ro", "lang": "ro", "confidence": "high",
        "q": "În română: cum reglementează Uniunea Europeană exporturile de armament?",
        "a": (
            "Uniunea Europeană nu are o autoritate unică de licențiere: fiecare stat membru emite "
            "propriile licențe.\n\n"
            "Cadrul comun este Poziția comună 2008/944/PESC, care stabilește cele opt criterii "
            "pentru exporturile militare.\n\n"
            "Produsele cu dublă utilizare sunt reglementate de Regulamentul (UE) 2021/821.\n\n"
            "UE menține de asemenea o listă consolidată de sancțiuni, aplicată de toate statele "
            "membre. Toate statele membre ale UE sunt state părți la Tratatul privind comerțul cu "
            "arme (TCA)."
        ),
    },
    {
        "id": "eu_es", "lang": "es", "confidence": "high",
        "q": "En español: ¿cómo regula la Unión Europea las exportaciones de armamento?",
        "a": (
            "La Unión Europea no tiene una autoridad única de licencias: cada Estado miembro "
            "concede las suyas.\n\n"
            "El marco común es la Posición Común 2008/944/PESC, que fija los ocho criterios para "
            "las exportaciones militares.\n\n"
            "Los productos de doble uso se rigen por el Reglamento (UE) 2021/821.\n\n"
            "La UE mantiene además una lista consolidada de sanciones aplicada por todos los "
            "Estados miembros."
        ),
    },
    {
        "id": "att_tr", "lang": "tr", "confidence": "high",
        "q": "Türkçe: Silah Ticareti Antlaşması (ATT) nedir ve bir ihracatçı için ne anlama gelir?",
        "a": (
            "ATT (Silah Ticareti Antlaşması), konvansiyonel silahların uluslararası ticaretini "
            "düzenleyen ve Aralık 2014'te yürürlüğe giren bir antlaşmadır; sekretaryası "
            "Cenevre'dedir.\n\n"
            "Taraf devletlerden ihracat riski değerlendirmesi yapmalarını ve yıllık raporlama "
            "sunmalarını ister.\n\n"
            "ATT'nin kendisi lisans vermez; lisanslar ulusal makamlarca verilir.\n\n"
            "Taraf olmayan bir ihracatçı devlet, kendi ulusal mevzuatına ve geçerli BM Güvenlik "
            "Konseyi ambargolarına tabi olmaya devam eder."
        ),
    },
    {
        "id": "att_fr", "lang": "fr", "confidence": "high",
        "q": "En français : qu'est-ce que le Traité sur le commerce des armes et qu'implique-t-il pour un exportateur ?",
        "a": (
            "Le TCA (Traité sur le commerce des armes) encadre le commerce international des armes "
            "classiques ; il est entré en vigueur en décembre 2014 et son secrétariat siège à "
            "Genève.\n\n"
            "Il impose aux États Parties d'évaluer le risque à l'exportation et de remettre un "
            "rapport annuel.\n\n"
            "Le TCA ne délivre pas lui-même de licence : l'autorisation reste nationale.\n\n"
            "Un État exportateur non Partie demeure soumis à son propre contrôle national et à "
            "tout embargo du Conseil de sécurité des Nations unies."
        ),
    },
    {
        "id": "multi_de", "lang": "de", "confidence": "high",
        "q": "Auf Deutsch: Was sind das Wassenaar-Abkommen, das MTCR, die NSG und die Australische Gruppe?",
        "a": (
            "Das sind multilaterale Exportkontrollregime — politische Absprachen, keine "
            "völkerrechtlichen Verträge.\n\n"
            "Das Wassenaar-Abkommen erfasst konventionelle Waffen und Dual-Use-Güter. Das MTCR "
            "(Missile Technology Control Regime) betrifft Trägersysteme. Die NSG (Nuclear "
            "Suppliers Group) betrifft nukleare Güter. Die Australische Gruppe betrifft chemische "
            "und biologische Güter.\n\n"
            "Ihre Kontrolllisten werden über nationales Recht umgesetzt — erst dadurch binden sie "
            "den Exporteur.\n\n"
            "Eine Mitgliedschaft ersetzt keine nationale Genehmigung."
        ),
    },
    {
        "id": "uk_fr", "lang": "fr", "confidence": "high",
        "q": "En français : quelle autorité délivre les licences d'exportation de défense au Royaume-Uni ?",
        "a": (
            "Au Royaume-Uni, les licences sont délivrées par l'ECJU (Export Control Joint Unit), "
            "rattachée au Department for Business and Trade.\n\n"
            "La base juridique est l'Export Control Act 2002 et l'Export Control Order 2008, "
            "appliqués au moyen des listes stratégiques britanniques et des Strategic Licensing "
            "Criteria.\n\n"
            "Les sanctions financières relèvent séparément de l'OFSI (Office of Financial "
            "Sanctions Implementation), au sein du HM Treasury.\n\n"
            "Le Royaume-Uni est État Partie au Traité sur le commerce des armes (TCA)."
        ),
    },
]


def _rows(include_medium: bool) -> list[dict]:
    out: list[dict] = []
    for p in PAIRS:
        if p["confidence"] != "high" and not include_medium:
            continue
        out.append({
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": p["q"]},
                {"role": "assistant", "content": p["a"]},
            ],
            "topic": f"export_control_{p['id']}",
            "language": p["lang"],
            "confidence": p["confidence"],
            "source": "claude_authored:R-F4334",
        })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--include-medium", action="store_true",
                    help="also emit rows whose facts are only medium-confidence")
    args = ap.parse_args()

    rows = _rows(args.include_medium)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8", newline="\n")
    langs = sorted({r["language"] for r in rows})
    print(f"wrote {len(rows)} rows -> {args.out}")
    print(f"  languages: {len(langs)} {langs}")
    print(f"  all high-confidence: {all(r['confidence'] == 'high' for r in rows)}")
    print("\nNEXT: python scripts/admin/training_corpus_manifest.py --record")
    print("      (must print CONTAMINATION=NO before this corpus is trained on)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
