"""build_export_control_curriculum — Claude-authored multilingual export-control
curriculum for ARIA-LLM (R-F4334 / C-280).

WHY THIS EXISTS. The 500-Q eval scores 110 questions (22%) in eleven languages
on national defence export-control regimes; v0.7 scored 6/110. The cause was
measured, not guessed: THE TEACHER DOES NOT KNOW THE MATERIAL. Asked directly,
DeepSeek — which generates the grounded corpus and judges the eval — answers:

    CIEEMG UNKNOWN   SBDU UNKNOWN   JIMDDU UNKNOWN
    ANCEX  UNKNOWN   SSB  UNKNOWN   BAFA   KNOWS

5 of 6. A curriculum distilled from a teacher cannot contain what the teacher
lacks, which is why v0.5/v0.6/v0.7 all plateaued at ~0.308. Nor is it a rubric
artifact: re-judging with the strict grounding rubric LIFTED (R-F4332/R-F4333)
still gave 36/175 = 0.206 on that bucket.

OPERATOR DIRECTIVE 2026-08-26: "we dont want deepseek to train aria anymore we
want you to train aria." Claude authors this in-session. It does NOT point
aria-intel at the Anthropic API — that would breach RULE ONE (CLAUDE.md §17,
anthropic is DD-only), which took DD down when broken. Her runtime stays
Anthropic-free; only the training DATA is Claude-authored.

BUILT ON THE NORTH STAR, NOT JUST THE BENCHMARK
(docs/golden_intel_north_star_2026_07_14.md). The USP is a DECISION SIGNAL
layer, and the named gap is VALUE DENSITY:

    "The gap is not the guard. The gap is value density. The live feed can
     still produce generic source-derived items with templated impact such as
     'Assess country risk.' That is not enough to be ARIA's USP."

and the success test:

    "I know what changed, why it matters to my decision, what to do next, and
     why ARIA found more value than the raw source headline."

Customer job #1 is literally export and sanctions protection, whose actions are
"screen, block, escalate, freeze, obtain licence, or avoid". So every answer
here carries TWO halves:

    1. THE SPECIFICS — authority, acronym, governing instrument. This is what
       the eval grades and what a 7B cannot retrieve, because the evidence is
       not in the RAG store (measured: 0 of 4 keywords for the French question,
       and an English rephrasing scores the same 0/4).
    2. THE OPERATOR ACTION — what the exporter must actually DO. Without it the
       answer is an encyclopedia entry, which the north star explicitly says is
       not enough.

Training only half of that would teach recall and leave the USP untouched.

CONTAMINATION DISCIPLINE — read before editing.
  * Facts are authored from general domain knowledge of national export-control
    regimes. They are NOT lifted from the eval's `expected_answer` fields, and
    no eval question is reproduced.
  * `training_corpus_manifest.py` checks PROMPT overlap only. It cannot see
    answer-level contamination, so that discipline is human, not mechanical.
  * Teaching the DOMAIN is legitimate; reproducing the TEST is not.

NO CODE-SWITCHING. An earlier version templated a per-language lead sentence
and left the body in English:
    "Yetkili kurum Savunma Sanayii Baskanligi (SSB), export licences are
     issued with the Ministry of National Defence..."
Training on that teaches code-switching — worse than not training, since the
eval asks in-language and grades an in-language answer. Caught by READING the
generated rows. Every answer below is written whole in its own language, and a
test pins it.

CONFIDENCE. Only `high` rows ship. These are regulatory facts in a
due-diligence product: a wrong acronym trained in is a falsehood ARIA repeats
with confidence. Section 24 requires a real dataset-quality review before any
paid cycle, and `confidence` is on every row so that review is possible.

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
    "acronym and the governing instrument, and then say what the exporter must "
    "actually DO. If you do not know, say so — never invent an agency, a "
    "statute or a treaty status."
)

PAIRS: list[dict] = [
    # ================= FRENCH =================
    {
        "id": "fr_france", "lang": "fr", "confidence": "high",
        "q": "En français : quelle autorité contrôle les exportations de matériel de guerre en France, et que doit faire l'exportateur ?",
        "a": (
            "L'autorité compétente est la CIEEMG (Commission Interministérielle pour l'Étude des "
            "Exportations de Matériels de Guerre), dont les travaux sont coordonnés par le SGDSN "
            "(Secrétariat général de la défense et de la sécurité nationale).\n\n"
            "La licence est une AEMG (Autorisation d'Exportation de Matériels de Guerre), régie par "
            "le Code de la défense. Les biens à double usage relèvent d'un circuit distinct, le "
            "SBDU (Service des Biens à Double Usage), au titre du règlement (UE) 2021/821.\n\n"
            "Ce que l'exportateur doit faire : classer le bien (matériel de guerre ou double "
            "usage), déposer la demande d'AEMG avant tout engagement contractuel, obtenir un "
            "certificat d'utilisation finale du destinataire, et vérifier les embargos ONU et UE "
            "applicables au pays de destination. Sans licence préalable, l'expédition est un délit "
            "— il ne s'agit pas d'une formalité régularisable après coup."
        ),
    },
    {
        "id": "fr_us", "lang": "fr", "confidence": "high",
        "q": "En français : comment les États-Unis contrôlent-ils leurs exportations de défense, et quelles obligations pour un partenaire européen ?",
        "a": (
            "Le matériel inscrit à l'USML relève de l'ITAR (International Traffic in Arms "
            "Regulations), administré par la DDTC (Directorate of Defense Trade Controls) du "
            "Département d'État. Les biens à double usage relèvent de l'EAR (Export Administration "
            "Regulations), administré par le BIS (Bureau of Industry and Security) du Département "
            "du Commerce, au titre de la CCL. Les sanctions et la liste SDN relèvent de l'OFAC "
            "(Office of Foreign Assets Control) du Trésor.\n\n"
            "Ce que le partenaire européen doit faire : l'ITAR suit le composant, pas la frontière. "
            "Un sous-ensemble d'origine américaine contamine le produit fini, ce qui impose une "
            "ré-exportation autorisée par la DDTC même entre deux pays européens. Il faut donc "
            "cartographier l'origine des composants, obtenir l'accord de ré-exportation avant "
            "livraison, et cribler le destinataire contre la liste SDN.\n\n"
            "Les États-Unis ont signé le TCA en 2013 puis retiré leur signature en 2019 : ils n'en "
            "sont pas État Partie."
        ),
    },
    {
        "id": "fr_att", "lang": "fr", "confidence": "high",
        "q": "En français : qu'est-ce que le Traité sur le commerce des armes et qu'implique-t-il concrètement pour un exportateur ?",
        "a": (
            "Le TCA (Traité sur le commerce des armes) encadre le commerce international des armes "
            "classiques ; il est entré en vigueur en décembre 2014 et son secrétariat siège à "
            "Genève. Il impose aux États Parties d'évaluer le risque à l'exportation et de remettre "
            "un rapport annuel.\n\n"
            "Ce que cela change pour l'exportateur : le TCA ne délivre aucune licence — "
            "l'autorisation reste nationale. Son effet pratique est l'évaluation de risque imposée "
            "à l'État : détournement, violations graves du droit humanitaire, violences fondées sur "
            "le genre. Un dossier qui ne documente pas l'utilisateur final et l'usage final sera "
            "refusé sur ce fondement.\n\n"
            "Un État exportateur non Partie demeure soumis à son propre contrôle national et à tout "
            "embargo du Conseil de sécurité des Nations unies : ne pas être Partie n'allège rien."
        ),
    },
    # ================= SPANISH =================
    {
        "id": "es_spain", "lang": "es", "confidence": "high",
        "q": "En español: ¿qué autoridad controla las exportaciones de material de defensa de España y qué debe hacer el exportador?",
        "a": (
            "La autoridad competente es la JIMDDU (Junta Interministerial Reguladora del Comercio "
            "Exterior de Material de Defensa y de Doble Uso), adscrita a la Secretaría de Estado de "
            "Comercio.\n\n"
            "El régimen se rige por la Ley 53/2007, de control del comercio exterior de material de "
            "defensa y de doble uso, desarrollada por el Real Decreto 679/2014. Los productos de "
            "doble uso se rigen además por el Reglamento (UE) 2021/821.\n\n"
            "Lo que debe hacer el exportador: clasificar el producto, solicitar la autorización a "
            "la JIMDDU antes de firmar el contrato, aportar certificado de último destino, y "
            "comprobar los embargos de la ONU y de la UE sobre el país de destino. Exportar sin "
            "autorización previa es una infracción, no un trámite subsanable después.\n\n"
            "España es Estado Parte del Tratado sobre el Comercio de Armas (TCA)."
        ),
    },
    {
        "id": "es_eu", "lang": "es", "confidence": "high",
        "q": "En español: ¿cómo regula la Unión Europea las exportaciones de armamento y qué significa para una empresa?",
        "a": (
            "La Unión Europea no tiene una autoridad única de licencias: cada Estado miembro "
            "concede las suyas. El marco común es la Posición Común 2008/944/PESC, que fija los "
            "ocho criterios para las exportaciones militares, y el Reglamento (UE) 2021/821 para "
            "los productos de doble uso.\n\n"
            "Lo que significa para la empresa: la licencia se pide en el Estado miembro donde se "
            "encuentra el exportador, no en Bruselas, y una denegación notificada por otro Estado "
            "miembro debe consultarse antes de conceder una operación esencialmente idéntica. "
            "Conviene por tanto comprobar si existe una denegación previa sobre el mismo "
            "destinatario.\n\n"
            "La UE mantiene además una lista consolidada de sanciones aplicada por todos los "
            "Estados miembros; el cribado del destinatario contra esa lista es obligatorio."
        ),
    },
    # ================= GERMAN =================
    {
        "id": "de_germany", "lang": "de", "confidence": "high",
        "q": "Auf Deutsch: Welche Behörde kontrolliert die deutschen Rüstungsexporte und was muss der Exporteur tun?",
        "a": (
            "Zuständig ist das BAFA (Bundesamt für Wirtschaft und Ausfuhrkontrolle) im "
            "Geschäftsbereich des Bundesministeriums für Wirtschaft.\n\n"
            "Rechtsgrundlage sind das Außenwirtschaftsgesetz (AWG) und die "
            "Außenwirtschaftsverordnung (AWV). Kriegswaffen unterliegen zusätzlich dem "
            "Kriegswaffenkontrollgesetz (KrWaffKontrG). Dual-Use-Güter fallen unter die "
            "EU-Verordnung 2021/821.\n\n"
            "Was der Exporteur tun muss: das Gut einstufen (Kriegswaffe oder Dual-Use), die "
            "Genehmigung beim BAFA vor Vertragsschluss beantragen, eine Endverbleibserklärung des "
            "Empfängers einholen und Embargos der UN und der EU für das Bestimmungsland prüfen. "
            "Für Kriegswaffen ist zusätzlich eine Genehmigung nach dem KrWaffKontrG erforderlich — "
            "eine BAFA-Ausfuhrgenehmigung allein genügt dort nicht.\n\n"
            "Deutschland ist Vertragsstaat des Vertrags über den Waffenhandel (ATT)."
        ),
    },
    {
        "id": "de_multilateral", "lang": "de", "confidence": "high",
        "q": "Auf Deutsch: Was sind das Wassenaar-Abkommen, das MTCR, die NSG und die Australische Gruppe, und binden sie einen Exporteur?",
        "a": (
            "Das sind multilaterale Exportkontrollregime — politische Absprachen, keine "
            "völkerrechtlichen Verträge.\n\n"
            "Das Wassenaar-Abkommen erfasst konventionelle Waffen und Dual-Use-Güter. Das MTCR "
            "(Missile Technology Control Regime) betrifft Trägersysteme. Die NSG (Nuclear Suppliers "
            "Group) betrifft nukleare Güter. Die Australische Gruppe betrifft chemische und "
            "biologische Güter.\n\n"
            "Was das für den Exporteur bedeutet: die Regime selbst binden ihn nicht. Bindend wird "
            "ihre Kontrollliste erst durch nationales Recht — in Deutschland über die Ausfuhrliste "
            "der AWV. Die Mitgliedschaft eines Staates ersetzt daher keine Genehmigung, und ein "
            "Geschäft mit einem Nichtmitglied ist nicht automatisch verboten. Maßgeblich ist immer "
            "die nationale Güterliste."
        ),
    },
    # ================= ROMANIAN =================
    {
        "id": "ro_romania", "lang": "ro", "confidence": "high",
        "q": "În română: ce autoritate controlează exporturile de produse militare din România și ce trebuie să facă exportatorul?",
        "a": (
            "Autoritatea competentă este ANCEX (Agenția Națională de Control al Exporturilor), "
            "aflată în subordinea Ministerului Afacerilor Externe.\n\n"
            "ANCEX emite licențele de export pentru produsele militare și pentru cele cu dublă "
            "utilizare, aplicând Poziția comună 2008/944/PESC. Produsele cu dublă utilizare intră "
            "sub incidența Regulamentului (UE) 2021/821.\n\n"
            "Ce trebuie să facă exportatorul: să obțină mai întâi înregistrarea la ANCEX, să "
            "clasifice produsul, să solicite licența înainte de livrare, să prezinte certificatul "
            "de utilizator final și să verifice embargourile ONU și UE pentru țara de destinație. "
            "Livrarea fără licență prealabilă constituie infracțiune.\n\n"
            "România este stat parte la Tratatul privind comerțul cu arme (TCA)."
        ),
    },
    # ================= TURKISH =================
    {
        "id": "tr_turkiye", "lang": "tr", "confidence": "high",
        "q": "Türkçe: Türkiye'nin savunma sanayii ihracatını hangi kurum yönetir ve ihracatçının ne yapması gerekir?",
        "a": (
            "Türkiye'de savunma sanayii ihracatı SSB (Savunma Sanayii Başkanlığı) tarafından "
            "yürütülür; ihracat lisansları MSB (Millî Savunma Bakanlığı) ile birlikte düzenlenir.\n\n"
            "Hukuki dayanak, harp araç ve gereçleri üreten özel sanayi kuruluşlarının denetimine "
            "ilişkin 5201 sayılı Kanun'dur. Çift kullanımlı ürünler Ticaret Bakanlığı tarafından "
            "idare edilir.\n\n"
            "İhracatçının yapması gerekenler: ürünü sınıflandırmak, sevkiyattan önce SSB/MSB "
            "lisansını almak, nihai kullanıcı sertifikası temin etmek ve hedef ülke için BM ve AB "
            "ambargolarını kontrol etmek. Ayrıca ABD menşeli bileşen varsa ITAR yeniden ihracat "
            "izni gerekir — bu, Türkiye lisansından ayrı ve ek bir yükümlülüktür.\n\n"
            "Türkiye Silah Ticareti Antlaşması'nı (ATT) 2013'te imzalamış ancak onaylamamıştır; "
            "bu nedenle taraf devlet değil, imzacı devlettir."
        ),
    },
    # ================= PORTUGUESE =================
    {
        "id": "pt_att", "lang": "pt", "confidence": "high",
        "q": "Em português: o que é o Tratado sobre o Comércio de Armas (ATT) e o que muda para um exportador?",
        "a": (
            "O ATT (Tratado sobre o Comércio de Armas) regula o comércio internacional de armas "
            "convencionais; entrou em vigor em dezembro de 2014 e o seu Secretariado tem sede em "
            "Genebra. Exige que os Estados Partes avaliem o risco da exportação e apresentem "
            "relatório anual.\n\n"
            "O que muda para o exportador: o ATT não emite licenças — a autorização continua a ser "
            "nacional. O efeito prático é a avaliação de risco imposta ao Estado: desvio, violações "
            "graves do direito humanitário e violência baseada no género. Um processo que não "
            "documente o utilizador final e a utilização final será recusado com esse fundamento.\n\n"
            "Um Estado exportador que não seja Parte continua sujeito ao seu próprio controlo "
            "nacional e a qualquer embargo do Conselho de Segurança das Nações Unidas — não ser "
            "Parte não dispensa nada."
        ),
    },
    {
        "id": "pt_us", "lang": "pt", "confidence": "high",
        "q": "Em português: como os Estados Unidos controlam as exportações de defesa e o que isso exige de um fornecedor estrangeiro?",
        "a": (
            "O material inscrito na USML é regido pelo ITAR (International Traffic in Arms "
            "Regulations), administrado pela DDTC (Directorate of Defense Trade Controls) do "
            "Departamento de Estado. Os bens de dupla utilização regem-se pelo EAR (Export "
            "Administration Regulations), administrado pelo BIS (Bureau of Industry and Security) "
            "do Departamento do Comércio. As sanções e a lista SDN cabem à OFAC (Office of Foreign "
            "Assets Control) do Tesouro.\n\n"
            "O que isso exige de um fornecedor estrangeiro: o ITAR acompanha o componente, não a "
            "fronteira. Um subconjunto de origem norte-americana contamina o produto final e obriga "
            "a uma reexportação autorizada pela DDTC, mesmo entre dois países terceiros. É preciso "
            "mapear a origem dos componentes, obter autorização antes da entrega e rastrear o "
            "destinatário na lista SDN.\n\n"
            "Os Estados Unidos assinaram o ATT em 2013 e retiraram a assinatura em 2019; não são "
            "Estado Parte."
        ),
    },
    # ================= POLISH =================
    {
        "id": "pl_eu", "lang": "pl", "confidence": "high",
        "q": "Po polsku: jak Unia Europejska reguluje eksport uzbrojenia i co to oznacza dla przedsiębiorcy?",
        "a": (
            "Unia Europejska nie ma jednego organu wydającego zezwolenia — każde państwo "
            "członkowskie wydaje własne. Wspólne ramy to Wspólne stanowisko 2008/944/WPZiB, które "
            "ustala osiem kryteriów dla eksportu wojskowego, oraz rozporządzenie (UE) 2021/821 "
            "dotyczące produktów podwójnego zastosowania.\n\n"
            "Co to oznacza dla przedsiębiorcy: zezwolenie uzyskuje się w państwie członkowskim "
            "siedziby eksportera, a nie w Brukseli. Jeżeli inne państwo członkowskie odmówiło "
            "zezwolenia na transakcję zasadniczo identyczną, należy to skonsultować przed "
            "wydaniem zgody — dlatego trzeba sprawdzić, czy wobec tego samego odbiorcy nie ma "
            "wcześniejszej odmowy.\n\n"
            "UE prowadzi ponadto skonsolidowaną listę sankcyjną stosowaną przez wszystkie państwa "
            "członkowskie; sprawdzenie odbiorcy na tej liście jest obowiązkowe."
        ),
    },
    {
        "id": "pl_att", "lang": "pl", "confidence": "high",
        "q": "Po polsku: czym jest Traktat o handlu bronią (ATT) i co z niego wynika dla eksportera?",
        "a": (
            "ATT (Traktat o handlu bronią) reguluje międzynarodowy obrót bronią konwencjonalną; "
            "wszedł w życie w grudniu 2014 roku, a jego Sekretariat mieści się w Genewie. Wymaga od "
            "państw-stron oceny ryzyka eksportu oraz corocznej sprawozdawczości.\n\n"
            "Co z niego wynika dla eksportera: ATT nie wydaje zezwoleń — pozwolenie pozostaje "
            "krajowe. Jego praktycznym skutkiem jest obowiązkowa ocena ryzyka po stronie państwa: "
            "przekierowanie towaru, poważne naruszenia prawa humanitarnego, przemoc ze względu na "
            "płeć. Wniosek, który nie dokumentuje użytkownika końcowego i zastosowania końcowego, "
            "zostanie z tego powodu odrzucony.\n\n"
            "Państwo, które nie jest stroną traktatu, nadal podlega własnym przepisom krajowym "
            "oraz każdemu embargu Rady Bezpieczeństwa ONZ."
        ),
    },
    # ================= RUSSIAN =================
    {
        "id": "ru_att", "lang": "ru", "confidence": "high",
        "q": "По-русски: что такое Договор о торговле оружием (ДТО) и что он означает для экспортёра?",
        "a": (
            "ДТО (Договор о торговле оружием, ATT) регулирует международную торговлю обычными "
            "вооружениями; он вступил в силу в декабре 2014 года, секретариат находится в Женеве. "
            "Договор обязывает государства-участники проводить оценку риска экспорта и ежегодно "
            "отчитываться.\n\n"
            "Что это означает для экспортёра: сам ДТО лицензий не выдаёт — разрешение остаётся "
            "национальным. Практическое следствие — обязательная оценка риска: перенаправление "
            "товара, серьёзные нарушения гуманитарного права, насилие по гендерному признаку. "
            "Заявка, в которой не подтверждён конечный пользователь и конечное использование, будет "
            "отклонена именно по этому основанию.\n\n"
            "Государство, не являющееся участником договора, всё равно связано собственным "
            "национальным контролем и любым эмбарго Совета Безопасности ООН."
        ),
    },
    {
        "id": "ru_us", "lang": "ru", "confidence": "high",
        "q": "По-русски: как США контролируют экспорт продукции военного назначения и что это значит для иностранного поставщика?",
        "a": (
            "Продукция, включённая в USML, регулируется ITAR (International Traffic in Arms "
            "Regulations); администрирует его DDTC (Directorate of Defense Trade Controls) "
            "Государственного департамента. Товары двойного назначения подпадают под EAR (Export "
            "Administration Regulations), которым занимается BIS (Bureau of Industry and Security) "
            "Министерства торговли. Санкции и список SDN ведёт OFAC (Office of Foreign Assets "
            "Control) Министерства финансов.\n\n"
            "Что это значит для иностранного поставщика: ITAR следует за компонентом, а не за "
            "границей. Узел американского происхождения «заражает» готовое изделие и требует "
            "разрешения DDTC на реэкспорт даже между двумя третьими странами. Необходимо "
            "отслеживать происхождение компонентов, получать разрешение до поставки и проверять "
            "получателя по списку SDN.\n\n"
            "США подписали ДТО в 2013 году и отозвали подпись в 2019 году; участником договора они "
            "не являются."
        ),
    },
    # ================= CHINESE =================
    {
        "id": "zh_us", "lang": "zh", "confidence": "high",
        "q": "用中文回答：美国如何管理国防物项出口？对外国供应商有什么要求？",
        "a": (
            "列入 USML 的国防物项适用 ITAR（国际武器贸易条例），由美国国务院的 DDTC"
            "（国防贸易管制局）主管。两用物项适用 EAR（出口管理条例），由商务部的 BIS"
            "（工业与安全局）依据 CCL 管理。制裁与 SDN 名单由财政部的 OFAC"
            "（外国资产控制办公室）负责。\n\n"
            "对外国供应商的要求：ITAR 跟随零部件而非国界。含有美国原产分系统的成品同样受管辖，"
            "即使在两个第三国之间转移，也需要事先取得 DDTC 的再出口许可。因此必须梳理零部件来源、"
            "在交付前取得许可，并将收货方与 SDN 名单进行筛查。\n\n"
            "美国于 2013 年签署《武器贸易条约》（ATT），并于 2019 年撤回签署，不是缔约国。"
        ),
    },
    {
        "id": "zh_att", "lang": "zh", "confidence": "high",
        "q": "用中文回答：《武器贸易条约》（ATT）是什么？对出口商有什么实际影响？",
        "a": (
            "《武器贸易条约》（ATT）规范常规武器的国际贸易，于 2014 年 12 月生效，秘书处设在日内瓦。"
            "条约要求缔约国进行出口风险评估并每年提交报告。\n\n"
            "对出口商的实际影响：ATT 本身不签发许可证，许可仍由各国主管机关签发。其实际作用是国家层面"
            "的强制风险评估，包括物项转移风险、严重违反国际人道法的风险，以及基于性别的暴力风险。"
            "未能证明最终用户与最终用途的申请，将据此被拒绝。\n\n"
            "非缔约国的出口国仍然受本国管制法律以及联合国安理会任何禁运措施的约束——不是缔约国"
            "并不意味着义务减少。"
        ),
    },
    # ================= ARABIC =================
    {
        "id": "ar_att", "lang": "ar", "confidence": "high",
        "q": "بالعربية: ما هي معاهدة تجارة الأسلحة (ATT) وما أثرها العملي على المُصدِّر؟",
        "a": (
            "معاهدة تجارة الأسلحة (ATT) تنظّم التجارة الدولية في الأسلحة التقليدية، وقد دخلت حيّز "
            "النفاذ في كانون الأول/ديسمبر 2014، ويقع مقر أمانتها في جنيف. وتُلزم الدول الأطراف "
            "بإجراء تقييم لمخاطر التصدير وبتقديم تقرير سنوي.\n\n"
            "الأثر العملي على المُصدِّر: المعاهدة نفسها لا تصدر تراخيص، إذ يبقى الترخيص وطنياً. "
            "أثرها الفعلي هو تقييم المخاطر المفروض على الدولة: خطر تحويل وجهة الشحنة، والانتهاكات "
            "الجسيمة للقانون الإنساني، والعنف القائم على النوع الاجتماعي. والطلب الذي لا يوثّق "
            "المستخدم النهائي والاستخدام النهائي يُرفض على هذا الأساس.\n\n"
            "والدولة المصدِّرة غير الطرف تظل خاضعة لرقابتها الوطنية ولأي حظر يفرضه مجلس الأمن "
            "التابع للأمم المتحدة."
        ),
    },
    {
        "id": "ar_us", "lang": "ar", "confidence": "high",
        "q": "بالعربية: كيف تنظّم الولايات المتحدة صادرات الدفاع، وما الذي يترتب على المورّد الأجنبي؟",
        "a": (
            "تخضع الأصناف المدرجة في قائمة USML للائحة ITAR (لوائح الاتجار الدولي بالأسلحة)، "
            "وتديرها DDTC (مديرية ضوابط التجارة الدفاعية) في وزارة الخارجية. أما الأصناف مزدوجة "
            "الاستخدام فتخضع للائحة EAR (لوائح إدارة الصادرات) التي يديرها BIS (مكتب الصناعة "
            "والأمن) في وزارة التجارة. والعقوبات وقائمة SDN من اختصاص OFAC (مكتب مراقبة الأصول "
            "الأجنبية) في وزارة الخزانة.\n\n"
            "ما الذي يترتب على المورّد الأجنبي: تتبع لائحة ITAR المكوّن نفسه لا الحدود. فالمكوّن "
            "أمريكي المنشأ يجعل المنتج النهائي خاضعاً للائحة، ويستلزم إذن إعادة تصدير من DDTC حتى "
            "بين دولتين ثالثتين. لذا يجب حصر منشأ المكوّنات، والحصول على الإذن قبل التسليم، وفحص "
            "المرسل إليه على قائمة SDN.\n\n"
            "وقّعت الولايات المتحدة معاهدة ATT عام 2013 ثم سحبت توقيعها عام 2019، وهي ليست دولة طرفاً."
        ),
    },
    # ================= SWAHILI =================
    {
        "id": "sw_att", "lang": "sw", "confidence": "high",
        "q": "Kwa Kiswahili: Mkataba wa Biashara ya Silaha (ATT) ni nini, na una maana gani kwa msafirishaji?",
        "a": (
            "Mkataba wa Biashara ya Silaha (ATT) unadhibiti biashara ya kimataifa ya silaha za "
            "kawaida. Ulianza kutumika mwezi Desemba 2014, na sekretarieti yake iko Geneva. "
            "Unazitaka nchi wanachama kufanya tathmini ya hatari ya usafirishaji na kutoa ripoti "
            "ya kila mwaka.\n\n"
            "Maana yake kwa msafirishaji: ATT yenyewe haitoi leseni — leseni bado hutolewa na "
            "mamlaka ya taifa husika. Athari yake halisi ni tathmini ya hatari inayolazimu serikali "
            "kuchunguza hatari ya mzigo kuelekezwa kwingine, ukiukaji mkubwa wa sheria za "
            "kibinadamu, na ukatili wa kijinsia. Ombi lisiloonyesha mtumiaji wa mwisho na matumizi "
            "ya mwisho litakataliwa kwa sababu hiyo.\n\n"
            "Nchi ambayo si mwanachama bado inafungwa na sheria zake za ndani na marufuku yoyote ya "
            "Baraza la Usalama la Umoja wa Mataifa."
        ),
    },
    {
        "id": "sw_un", "lang": "sw", "confidence": "high",
        "q": "Kwa Kiswahili: marufuku ya silaha ya Umoja wa Mataifa inafanya kazi vipi, na msafirishaji anapaswa kufanya nini?",
        "a": (
            "Marufuku ya silaha hutolewa na Baraza la Usalama la Umoja wa Mataifa chini ya Sura ya "
            "VII ya Mkataba wa Umoja wa Mataifa. Maazimio hayo yanawafunga wanachama wote, na kila "
            "nchi huyatekeleza kupitia sheria zake za ndani.\n\n"
            "Msafirishaji anapaswa kufanya nini: kwanza kuthibitisha kama nchi lengwa ina marufuku "
            "inayotumika, kisha kuchunguza mpokeaji dhidi ya orodha za vikwazo, na kupata cheti cha "
            "mtumiaji wa mwisho. Leseni ya taifa haitoshi peke yake — kama marufuku ipo, muamala "
            "hauruhusiwi hata kama mamlaka ya ndani imetoa kibali.\n\n"
            "Kumbuka pia kwamba marufuku nyingi hujumuisha mafunzo, matengenezo na msaada wa "
            "kiufundi, si silaha pekee."
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
