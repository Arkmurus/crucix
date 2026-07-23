// R-F2903 — Telegram card: rasterise to PNG, and make the card carry the USP.
//
// Two defects, both live on 2026-07-23:
//  1. sendPhoto received raw SVG as image/svg+xml. Telegram accepts JPEG/PNG only, so
//     every card upload failed with 400 IMAGE_PROCESS_FAILED. It had never worked; it
//     only surfaced when the channel finally posted successfully (R-F2902).
//  2. The card showed title + summary + bullets — visually fine and indistinguishable
//     from any newsletter graphic. It showed no grade, no corroboration state and no
//     source, i.e. none of the things that make ARIA's output different from an
//     opinion. "WHY IT MATTERS" additionally rendered the ACTION text, so both panels
//     said the same sentence.

import { test } from 'node:test';
import assert from 'node:assert/strict';

const { generateInfographicCard, uploadSvgAsPhoto } = await import('../lib/telegram/channelMedia.mjs');

const TENDER = {
  title: 'Hungary - Surveillance and security systems and devices - K2637 videomegfigyelo rendsz. amortizacios csereje',
  subtitle: 'BKM Budapesti Kozmuvek Nonprofit Zrt. (Hungary) - value undisclosed, deadline 2026-08-11. Matched products: surveillance_systems.',
  source: 'Procurement: TED',
  type: 'daily',
  grade: 'A',
  corroboration: 'single-source',
  evidenceUrl: 'https://ted.europa.eu/en/notice/-/detail/473202-2026',
  detectedAt: '2026-07-23T06:02:55Z',
  target: 'BKM Budapesti Kozmuvek Nonprofit Zrt.',
  action: 'Assess bid/no-bid - review scope, eligibility and deadline.',
};

// ── The card carries the evidence ──────────────────────────────────────────

test('R-F2903: the badge states the EVIDENCE grade, not a content category', () => {
  const svg = generateInfographicCard(TENDER);
  assert.match(svg, /GRADE A · OFFICIAL PRIMARY SOURCE/);
});

test('R-F2903: a corroborated Grade A says so; a single-source one does not claim it', () => {
  assert.match(generateInfographicCard({ ...TENDER, corroboration: 'corroborated' }),
    /GRADE A · INDEPENDENTLY CORROBORATED/);
  const single = generateInfographicCard(TENDER);
  assert.doesNotMatch(single, /CORROBORATED/,
    'a single-source item must never be labelled corroborated');
});

test('R-F2903: Grade B is labelled as single-source, corroboration pending', () => {
  const svg = generateInfographicCard({ ...TENDER, grade: 'B' });
  assert.match(svg, /GRADE B · SINGLE SOURCE · CORROBORATION PENDING/);
});

test('R-F2903: the primary-source evidence block is rendered', () => {
  const svg = generateInfographicCard(TENDER);
  assert.match(svg, /EVIDENCE · PRIMARY SOURCE/);
  assert.match(svg, /ted\.europa\.eu/);
  assert.match(svg, /Detected 2026-07-23 06:02/);
  assert.match(svg, /single-source/);
});

test('R-F2903: with NO evidence URL the block is omitted, never invented', () => {
  const { evidenceUrl, ...noUrl } = TENDER;
  const svg = generateInfographicCard(noUrl);
  assert.doesNotMatch(svg, /EVIDENCE · PRIMARY SOURCE/);
  assert.doesNotMatch(svg, /ted\.europa\.eu/);
});

// ── The panels say different things ────────────────────────────────────────

test('R-F2903: WHY IT MATTERS shows the why, not a duplicate of the action', () => {
  const svg = generateInfographicCard(TENDER);
  assert.match(svg, /WHY IT MATTERS/);
  assert.match(svg, /RECOMMENDED ACTION/);
  // The why-panel must contain the buyer/deadline detail, i.e. the actual "why".
  assert.match(svg, /value undisclosed/);
  // "Assess bid/no-bid" is the ACTION — it must appear once, not in both panels.
  const actionHits = (svg.match(/Assess bid\/no-bid/g) || []).length;
  assert.equal(actionHits, 1, 'the action text was duplicated into the why panel');
});

test('R-F2903: a long title is not silently clipped — truncation is marked', () => {
  const svg = generateInfographicCard(TENDER);
  assert.match(svg, /csereje/, 'the title tail was dropped');
});

test('R-F2903: text that DOES overflow is marked with an ellipsis', () => {
  const svg = generateInfographicCard({
    ...TENDER,
    subtitle: 'word '.repeat(200),   // far beyond any panel
  });
  assert.match(svg, /…/, 'overflowing text was clipped with no truncation marker');
});

// ── Rasterisation ──────────────────────────────────────────────────────────

test('R-F2903: the upload sends PNG, never image/svg+xml', async () => {
  const original = globalThis.fetch;
  let seenBody = '';
  globalThis.fetch = async (_url, opts) => {
    seenBody = Buffer.isBuffer(opts?.body) ? opts.body.toString('latin1') : String(opts?.body || '');
    return { ok: true, status: 200, json: async () => ({ ok: true, result: { photo: [{ file_id: 'F1' }] } }) };
  };
  try {
    const svg = generateInfographicCard(TENDER);
    const res = await uploadSvgAsPhoto({ botToken: 'T', chatId: '-100' }, svg, 'card.svg');
    assert.equal(res.ok, true, `upload failed: ${res.error}`);
    assert.match(seenBody, /Content-Type: image\/png/,
      'Telegram rejects SVG — the payload must be rasterised PNG');
    assert.doesNotMatch(seenBody, /image\/svg\+xml/);
    assert.match(seenBody, /filename="card\.png"/);
    // PNG magic bytes must actually be present in the multipart payload.
    assert.ok(seenBody.includes('PNG'), 'payload does not contain PNG data');
  } finally { globalThis.fetch = original; }
});

test('R-F2903: an unrasterisable card fails cleanly so the post still goes text-only', async () => {
  const original = globalThis.fetch;
  globalThis.fetch = async () => { throw new Error('should not reach Telegram'); };
  try {
    const res = await uploadSvgAsPhoto({ botToken: 'T', chatId: '-100' }, 'not an svg at all');
    assert.equal(res.ok, false);
    assert.equal(res.error, 'svg_rasterise_failed');
  } finally { globalThis.fetch = original; }
});
