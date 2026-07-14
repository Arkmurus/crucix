// test/pdf-export-full-content-rf2618.test.mjs
// R-F2618 — the audit-grade PDF export (aria.html "Export PDF" -> /api/reports/pdf ->
// generateAuditGradeReport) was losing information two ways, both reproduced here by
// generating a real PDF and extracting its text:
//   1. markdown tables printed as raw "| a | b |" pipe-text (DD risk/UBO/financial
//      tables unreadable) — now rendered as an aligned table.
//   2. any character > U+00FF was silently dropped (WinAnsi core font) — so Cyrillic /
//      CJK entity names VANISHED. Now Cyrillic is romanised (matches OFAC/UN lists) and
//      other unsupported scripts get a visible '?' placeholder instead of disappearing.
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { generateAuditGradeReport } from '../lib/reports/pdf_generator.mjs';
const require = createRequire(import.meta.url);
const pdfParse = require('pdf-parse');

async function renderText(content) {
  const pdf = await generateAuditGradeReport(content,
    { subject: 'T', userId: 'u', sessionId: 'u_1', messageIndex: 1 }, { classification: 'CONFIDENTIAL' });
  const { text, numpages } = await pdfParse(pdf);
  return { text, numpages };
}

describe('R-F2618 — PDF export captures full content', () => {
  it('renders markdown tables as cells, not raw pipe-text', async () => {
    const { text } = await renderText([
      '| Entity | Country | Risk |',
      '| --- | --- | --- |',
      '| Acme Defence Ltd | UK | HIGH |',
      '| Globex SA | FR | MEDIUM |',
    ].join('\n'));
    assert.ok(text.includes('Acme Defence Ltd'), 'table cell text must survive');
    assert.ok(text.includes('Globex SA'), 'second data row must survive');
    assert.ok(!text.includes('| Acme'), 'raw markdown pipes must not leak into the PDF');
    assert.ok(!text.includes('| --- |'), 'the delimiter row must not render');
  });

  it('romanises Cyrillic entity names instead of dropping them', async () => {
    const { text } = await renderText('Counterparty: Роснефть; UBO Игорь Сечин — OFAC SDN.');
    assert.ok(text.includes('Rosneft'), 'Роснефть must romanise to Rosneft (was dropped entirely)');
    assert.ok(text.includes('Igor Sechin'), 'Игорь Сечин must romanise to Igor Sechin');
    assert.ok(!/[Ѐ-ӿ]/.test(text), 'no raw Cyrillic should remain (core font cannot draw it)');
  });

  it('placeholders unrenderable scripts (CJK/Arabic) rather than silently erasing', async () => {
    const { text } = await renderText('Vendor: Huawei (华为技术) flagged on the Entity List.');
    assert.ok(text.includes('Huawei'), 'Latin part survives');
    assert.ok(text.includes('?'), 'the CJK name must leave a visible placeholder, not vanish');
    assert.ok(!text.includes('华为'), 'core font cannot draw CJK (placeholdered)');
  });

  it('keeps full multi-page content (no clipping)', async () => {
    const body = Array.from({ length: 45 }, (_, i) =>
      `Finding ${i + 1}: substantive DD detail spanning several pages. ENDMARK_${i + 1}.`).join('\n\n');
    const { text, numpages } = await renderText('START_OF_BODY\n\n' + body + '\n\nEND_OF_BODY');
    assert.ok(numpages >= 2, 'long content should paginate');
    assert.ok(text.includes('START_OF_BODY'), 'first line present');
    assert.ok(text.includes('ENDMARK_45.'), 'last finding present (nothing dropped off the end)');
    assert.ok(text.includes('END_OF_BODY'), 'final line present');
  });
});
