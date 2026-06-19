// test/pdf-report-rf1705.test.mjs
// R-F1705 — the audit-grade PDF was producing many near-empty pages (the
// watermark/footer auto-paginated) and mangled Unicode. This drives the real
// generator and asserts: valid PDF, NO blank-page inflation, and that the
// content that previously broke (arrows, emoji, dividers, long body) is handled.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { generateAuditGradeReport } from '../lib/reports/pdf_generator.mjs';

function pageCount(buf) {
  const s = buf.toString('latin1');
  const m = s.match(/\/Type\s*\/Page(?![s])/g);
  return m ? m.length : 0;
}

const META = {
  subject: 'NAF — Nigerian Airforce',
  userEmail: 'acorrea@arkmurus.com',
  sessionId: 'acorreaarkmuruscom_123_xy',
  messageIndex: 7,
  verifyUrl: '/api/reports/verify',
};

test('R-F1705: short content → no blank-page inflation', async () => {
  const body = 'A short answer with an arrow → and an em—dash and a bullet • here.';
  const buf = await generateAuditGradeReport(body, META, { classification: 'CONFIDENTIAL' });
  assert.ok(Buffer.isBuffer(buf) && buf.slice(0, 5).toString() === '%PDF-', 'valid PDF');
  const pages = pageCount(buf);
  // Before the fix this kind of report ballooned to ~12 pages (decorations on
  // their own pages). A short report must be at most ~2 pages now.
  assert.ok(pages >= 1 && pages <= 2, `expected 1–2 pages, got ${pages}`);
});

test('R-F1705: realistic multi-section body paginates sanely (no decoration pages)', async () => {
  const para = 'This month ARIA monitored many intelligence sources across target markets and produced a structured assessment. '.repeat(6);
  const body = [
    '## STATUS — current gaps',
    'The tool returned → nothing because the wrong thing was searched. 🛰 Self-introspection needed.',
    '%%%%%%%%%%%%%%%%%%%%',
    '- Any named-entity DD: entity → orchestrator → structured report. Works.',
    '- Any tender analysis: crawl, classify, write the brief.',
    '1. The Signal listener (3-5 dev days)',
    '2. Task CRUD from chat (2 dev days)',
    '---',
    para,
    para,
    para,
  ].join('\n');
  const buf = await generateAuditGradeReport(body, META, { classification: 'CONFIDENTIAL' });
  const pages = pageCount(buf);
  // It has real multi-paragraph content + the fixed appendix sections, so a few
  // pages are expected — but NOT the ~12 it produced before. Cap at 6.
  assert.ok(pages >= 1 && pages <= 6, `expected ≤6 pages, got ${pages}`);
  // Mojibake guard: the raw mangled markers must NOT appear as drawn text.
  const txt = buf.toString('latin1');
  assert.ok(!txt.includes('Ø=Ý'), 'no mangled-emoji bytes drawn');
});
