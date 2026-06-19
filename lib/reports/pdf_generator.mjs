// lib/reports/pdf_generator.mjs
// PDF Report Generator — Monthly Brief + Approach Pack + Audit-grade Report
// Uses pdfkit (pure Node.js, no Python, no native deps)

import PDFDocument from 'pdfkit';
import { signingConfigured, contentHash, sign as signReport } from './sign.mjs';

// ── Brand colours ────────────────────────────────────────────────────────────
const PURPLE = '#7c3aed';
const PURPLE_LT = '#a875ff';
const DARK = '#15101f';
const BLUE = '#0066FF';
const WHITE = '#ffffff';
const GREY = '#aaaaaa';
const INK = '#1b1b1b';        // R-F1705 body text (print-dark)
const INK_SOFT = '#3c3c3c';
const MUTE = '#8a8a90';        // metadata labels
const LINE = '#e3e3e8';        // hairlines
const RED = '#dc2626';
const GREEN = '#16a34a';
const ORANGE = '#ea580c';

// ── R-F1705 layout constants (A4, pt) ──
const PAGE_W = 595.28, PAGE_H = 841.89;
const MARGIN = 48;
const CONTENT_W = PAGE_W - MARGIN * 2;

// ── R-F1705 Unicode → print-safe. PDFKit's built-in Helvetica is WinAnsi-only,
// so arrows/emoji/box-drawing render as garbage (→ became "!'", emoji "Ø=Ý5",
// separators "%%%%"). Map the common ones to ASCII and strip the rest. ──
function _pdfSafe(s) {
  if (s == null) return '';
  s = String(s);
  const ARROW = new Set([0x2192,0x2794,0x279C,0x27A1,0x2799,0x2B95,0x21D2,0x27A4]);
  const ARROWL = new Set([0x2190,0x21D0]);
  const DASHY = new Set([0x2194,0x2191,0x2193,0x2195,0x21C4,0x2013]);
  const SQUOTE = new Set([0x2018,0x2019,0x201A,0x2032,0x02BC]);
  const DQUOTE = new Set([0x201C,0x201D,0x201E,0x2033]);
  const BULLET = new Set([0x2022,0x00B7,0x25CF,0x25AA,0x25E6,0x2023,0x2043,0x2219,0x2027]);
  const bulletCh = String.fromCharCode(0x2022);
  let out = '';
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    if (ARROW.has(cp)) { out += '->'; continue; }
    if (ARROWL.has(cp)) { out += '<-'; continue; }
    if (DASHY.has(cp)) { out += '-'; continue; }
    if (SQUOTE.has(cp)) { out += String.fromCharCode(39); continue; }
    if (DQUOTE.has(cp)) { out += String.fromCharCode(34); continue; }
    if (cp === 0x2026) { out += '...'; continue; }
    if (cp === 0x00A0) { out += ' '; continue; }
    if (BULLET.has(cp)) { out += bulletCh; continue; }
    if (cp === 9 || cp === 10 || cp === 13) { out += ch; continue; }
    if (cp === 0x2014) { out += ch; continue; }
    if (cp >= 0x20 && cp <= 0xFF) { out += ch; continue; }
  }
  return out;
}

// ── R-F1705 helpers (sanitised text, consistent margins, no overflow paging) ──
function _ensureSpace(doc, needed) {
  if (doc.y > PAGE_H - MARGIN - 24 - (needed || 0)) { doc.addPage(); doc.x = MARGIN; doc.y = MARGIN; }
}

function addHeader(doc, title, subtitle, classification) {
  doc.rect(0, 0, PAGE_W, 96).fill(DARK);
  doc.rect(0, 96, PAGE_W, 3).fill(PURPLE);   // accent rule under the band
  doc.font('Helvetica-Bold').fontSize(19).fillColor(WHITE).text('ARKMURUS', MARGIN, 26, { continued: true });
  doc.font('Helvetica').fontSize(11).fillColor(PURPLE_LT).text('  INTELLIGENCE');
  // classification badge, top-right
  if (classification) {
    const c = String(classification).toUpperCase();
    const col = c === 'PUBLIC' ? GREEN : (c === 'INTERNAL' ? ORANGE : RED);
    const bw = doc.font('Helvetica-Bold').fontSize(7.5).widthOfString(c) + 16;
    doc.roundedRect(PAGE_W - MARGIN - bw, 28, bw, 16, 3).fill(col);
    doc.fillColor(WHITE).font('Helvetica-Bold').fontSize(7.5).text(c, PAGE_W - MARGIN - bw, 32, { width: bw, align: 'center' });
  }
  doc.font('Helvetica-Bold').fontSize(15).fillColor(WHITE).text(_pdfSafe(title), MARGIN, 52, { width: CONTENT_W - 10, ellipsis: true, height: 20, lineBreak: false });
  if (subtitle) doc.font('Helvetica').fontSize(8.5).fillColor('#b9b1cc').text(_pdfSafe(subtitle), MARGIN, 74, { width: CONTENT_W, lineBreak: false });
  doc.fillColor(INK);
  doc.x = MARGIN; doc.y = 120;
}

function addSection(doc, title) {
  _ensureSpace(doc, 64);
  doc.moveDown(0.7);
  doc.font('Helvetica-Bold').fontSize(10.5).fillColor(PURPLE)
    .text(_pdfSafe(title).toUpperCase(), MARGIN, doc.y, { width: CONTENT_W, characterSpacing: 0.4 });
  doc.moveDown(0.25);
  doc.moveTo(MARGIN, doc.y).lineTo(PAGE_W - MARGIN, doc.y).lineWidth(0.5).strokeColor(LINE).stroke();
  doc.moveDown(0.5);
  doc.font('Helvetica').fillColor(INK).fontSize(9.5);
}

function addDivider(doc) {
  _ensureSpace(doc, 24);
  doc.moveDown(0.35);
  doc.moveTo(MARGIN + 90, doc.y).lineTo(PAGE_W - MARGIN - 90, doc.y).lineWidth(0.5).strokeColor(LINE).stroke();
  doc.moveDown(0.55);
}

function addBullet(doc, text, marker) {
  const t = _pdfSafe(text);
  if (!t) return;
  _ensureSpace(doc, 30);
  const y = doc.y;
  doc.font('Helvetica').fontSize(9.5).fillColor(PURPLE).text(marker || '•', MARGIN, y, { width: 18, lineBreak: false });
  const x2 = MARGIN + (marker ? 22 : 14);
  doc.font('Helvetica').fontSize(9.5).fillColor(INK_SOFT).text(t, x2, y, { width: PAGE_W - MARGIN - x2, lineGap: 2.5 });
  doc.moveDown(0.32);
}

function addKeyValue(doc, key, value) {
  _ensureSpace(doc, 24);
  const y = doc.y;
  const labelW = 132;
  doc.font('Helvetica').fontSize(9).fillColor(MUTE).text(_pdfSafe(key), MARGIN, y, { width: labelW, lineBreak: false });
  doc.font('Helvetica').fontSize(9).fillColor(INK).text(_pdfSafe(value) || '-', MARGIN + labelW + 6, y, { width: CONTENT_W - labelW - 6 });
  doc.moveDown(0.4);
}

function addParagraph(doc, text) {
  const t = _pdfSafe(text);
  if (!t) return;
  _ensureSpace(doc, 36);
  doc.font('Helvetica').fontSize(9.5).fillColor(INK_SOFT).text(t, MARGIN, doc.y, { width: CONTENT_W, lineGap: 3.5, align: 'left' });
  doc.moveDown(0.55);
}

function addFooter(doc) {
  const range = doc.bufferedPageRange();
  for (let i = range.start; i < range.start + range.count; i++) {
    doc.switchToPage(i);
    // R-F1705: drawing in the bottom-margin zone (y > height - margin) makes
    // PDFKit's text-flow think it's out of room and ADD A PAGE (the empty-pages
    // bug). Zeroing the bottom margin for the stamp prevents that.
    const sb = doc.page.margins.bottom; doc.page.margins.bottom = 0;
    const y = PAGE_H - 32;
    doc.moveTo(MARGIN, y - 6).lineTo(PAGE_W - MARGIN, y - 6).lineWidth(0.5).strokeColor(LINE).stroke();
    doc.font('Helvetica').fontSize(7).fillColor(MUTE)
      .text('ARKMURUS INTELLIGENCE — CONFIDENTIAL', MARGIN, y, { width: 320, lineBreak: false });
    doc.font('Helvetica').fontSize(7).fillColor(MUTE)
      .text('Page ' + (i + 1) + ' of ' + range.count, PAGE_W - MARGIN - 110, y, { width: 110, align: 'right', lineBreak: false });
    doc.page.margins.bottom = sb;
  }
}

// R-F82 (2026-05-09): per-customer watermarking for audit-grade PDFs.
// A leaked report should trace back to the customer who issued it.
// Two layers:
//   1. Visible per-page footer: 'Issued to <email> | <reportId>' so a
//      printed/scanned report still carries identification.
//   2. Diagonal faint watermark on each page (light grey, 8% opacity)
//      with the same identification — survives photocopying.
// The reportId is the first 12 chars of the HMAC signature, so the
// trace is deterministic from the audit log even after rotation.
function addCustomerWatermark(doc, metadata, signature) {
  const range = doc.bufferedPageRange();
  const email = (metadata && (metadata.userEmail || metadata.userId)) || '';
  const reportId = (signature || '').slice(0, 12) || 'unsigned';
  if (!email && !signature) return; // nothing to watermark

  const ident = _pdfSafe(email ? `Issued to ${email}  |  Report ${reportId}` : `Report ${reportId}`);

  for (let i = range.start; i < range.start + range.count; i++) {
    doc.switchToPage(i);
    // R-F1705: zero the bottom margin so the near-bottom tracing line can't
    // trigger PDFKit's auto-pagination (the empty-pages bug).
    const sb = doc.page.margins.bottom; doc.page.margins.bottom = 0;

    // Per-page tracing line above the footer (single line, no wrap).
    doc.font('Helvetica').fontSize(7).fillColor('#b9b9bd')
      .text(ident, MARGIN, PAGE_H - 46, { width: CONTENT_W, lineBreak: false });

    // Faint diagonal watermark — single line (lineBreak:false), no auto-paging.
    doc.save();
    doc.rotate(-28, { origin: [PAGE_W / 2, PAGE_H / 2] });
    doc.font('Helvetica-Bold').fillColor('#000000', 0.045).fontSize(26)
      .text(ident, PAGE_W / 2 - 300, PAGE_H / 2 - 16, { width: 600, align: 'center', lineBreak: false });
    doc.restore();

    doc.page.margins.bottom = sb;
  }
}

// ── Monthly Intelligence Brief ───────────────────────────────────────────────
export function generateMonthlyBrief(data) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const now = new Date();
      const monthName = now.toLocaleString('en-GB', { month: 'long', year: 'numeric' });

      addHeader(doc, `Monthly Intelligence Brief — ${monthName}`,
        `Generated ${now.toISOString().slice(0, 16).replace('T', ' ')} UTC | ARIA v3.0`);

      // 1. Executive Summary
      addSection(doc, '1. Executive Summary');
      const bd = data.bdIntelligence;
      const opps = data.opportunities || [];
      const tenders = bd?.tenders || [];
      const ideas = bd?.ideas || [];
      const brain = bd?.brain;
      addParagraph(doc,
        `This month, ARIA monitored ${data.meta?.sourcesQueried || 48} intelligence sources across ${opps.length} target markets. ` +
        `${tenders.length} procurement tenders were identified and ${ideas.length} strategic ideas generated. ` +
        (brain?.weeklyPriority?.action ? `Top priority: ${brain.weeklyPriority.action}` : 'No brain priority set this cycle.'));

      // 2. Top Opportunities
      addSection(doc, '2. Top Procurement Opportunities');
      if (opps.length) {
        opps.slice(0, 8).forEach(o => {
          addBullet(doc, `${o.market} — Score ${o.score}/100 (${o.tier}) | ${(o.procurementNeeds || []).slice(0, 3).join(', ')} | ${o.complianceStatus}`);
          if (o.notes) addParagraph(doc, '   ' + o.notes);
        });
      } else {
        addParagraph(doc, 'No opportunities detected this cycle. Run a sweep to refresh.');
      }

      // 3. Brain Strategy
      if (brain) {
        addSection(doc, '3. Brain Strategy Assessment');
        if (brain.weeklyPriority) {
          addKeyValue(doc, 'TOP PRIORITY', brain.weeklyPriority.action);
          addKeyValue(doc, 'Market', brain.weeklyPriority.market);
          addKeyValue(doc, 'Why now', brain.weeklyPriority.whyNow);
          addKeyValue(doc, 'First step', brain.weeklyPriority.firstStep);
        }
        if (brain.salesLeads?.length) {
          doc.moveDown(0.5);
          doc.fontSize(10).fillColor(PURPLE).text('Sales Leads:');
          brain.salesLeads.forEach(l => {
            addBullet(doc, `[${l.urgency}] ${l.market}: ${(l.lead || '').slice(0, 100)} — ${l.estimatedValue || '?'}`);
          });
        }
        if (brain.selfLearning?.strategyAdjustment) {
          doc.moveDown(0.3);
          addKeyValue(doc, 'Strategy adjustment', brain.selfLearning.strategyAdjustment);
        }
      }

      // 4. Active Tenders
      addSection(doc, '4. Active Tenders');
      if (tenders.length) {
        tenders.slice(0, 10).forEach(t => {
          addBullet(doc, `[${t.leadQuality || '—'}] ${t.market} — ${(t.title || '').slice(0, 80)} (Score: ${t.score}, Win: ${t.winProbability || '?'}%)`);
        });
      } else {
        addParagraph(doc, 'No tenders identified this cycle.');
      }

      // 5. Pipeline
      addSection(doc, '5. Deal Pipeline');
      const pipeline = bd?.pipeline || [];
      if (pipeline.length) {
        pipeline.slice(0, 10).forEach(d => {
          addBullet(doc, `${d.id || '?'} | ${d.market} | ${d.stage} | ${(d.title || '').slice(0, 60)}`);
        });
      } else {
        addParagraph(doc, 'Pipeline empty.');
      }

      // 6. Correlations
      addSection(doc, '6. Regional Situation Awareness');
      const corrs = data.correlations || [];
      if (corrs.length) {
        corrs.slice(0, 8).forEach(c => {
          addBullet(doc, `${c.region} [${(c.severity || '').toUpperCase()}]: ${(c.topSignals?.[0]?.text || '').slice(0, 120)}`);
        });
      }

      // 7. Compliance
      addSection(doc, '7. Compliance Notes');
      addParagraph(doc, 'All opportunities pre-screened against OFAC SDN, OFSI, UN SC, EU consolidated sanctions lists. Export control flags (ITAR/EAR/EU dual-use) noted per tender. End-user certificate requirements apply to all transactions.');

      // 8. Next Actions
      addSection(doc, '8. Recommended Actions');
      addBullet(doc, 'Review top 3 opportunities and decide GO/NO-GO within 48 hours');
      addBullet(doc, 'Check active relationship windows (/windows on Telegram)');
      addBullet(doc, 'Follow up on stale pipeline deals');
      addBullet(doc, 'Run /sweep daily to maintain intelligence freshness');

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}

// ── Compliance Brief PDF ────────────────────────────────────────────────────

export function generateCompliancePDF(briefMarkdown, metadata = {}) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const now = new Date();
      const dateLabel = metadata.date || now.toISOString().split('T')[0];

      // Classification marking
      doc.rect(0, 0, doc.page.width, 18).fill(RED);
      doc.fontSize(8).fillColor(WHITE).text('CONFIDENTIAL — INTERNAL USE ONLY', 0, 4, { align: 'center' });

      addHeader(doc, `Compliance Intelligence Brief — ${dateLabel}`,
        `Generated ${now.toISOString().slice(0, 16).replace('T', ' ')} UTC | ARIA Compliance Module`);

      // Parse markdown sections and render
      const lines = (briefMarkdown || '').split('\n');
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed) { doc.moveDown(0.3); continue; }

        // H2 / H3 headings
        if (trimmed.startsWith('### ')) {
          addSection(doc, trimmed.replace(/^###\s*/, ''));
        } else if (trimmed.startsWith('## ')) {
          addSection(doc, trimmed.replace(/^##\s*/, ''));
        } else if (trimmed.startsWith('# ')) {
          addSection(doc, trimmed.replace(/^#\s*/, ''));
        }
        // Bullets
        else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) {
          addBullet(doc, trimmed.replace(/^[-*]\s*/, '').replace(/\*\*/g, ''));
        }
        // Bold key-value lines
        else if (trimmed.match(/^\*\*.+\*\*:/)) {
          const kv = trimmed.replace(/\*\*/g, '').split(':');
          addKeyValue(doc, kv[0].trim(), kv.slice(1).join(':').trim());
        }
        // Normal text
        else {
          addParagraph(doc, trimmed.replace(/\*\*/g, ''));
        }
      }

      // Footer classification
      doc.moveDown(1);
      doc.fontSize(8).fillColor(RED).text('CONFIDENTIAL — Arkmurus Ltd — Do not distribute externally', 40, doc.y, { align: 'center' });

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}


// ── Investigation Report PDF ────────────────────────────────────────────────

export function generateInvestigationPDF(report, entityName) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const now = new Date();

      // Classification marking
      doc.rect(0, 0, doc.page.width, 18).fill(RED);
      doc.fontSize(8).fillColor(WHITE).text('CONFIDENTIAL — INTERNAL USE ONLY', 0, 4, { align: 'center' });

      addHeader(doc, `Entity Investigation — ${entityName || 'Unknown'}`,
        `Generated ${now.toISOString().slice(0, 16).replace('T', ' ')} UTC | ARIA Deep Research`);

      if (typeof report === 'string') {
        // Markdown-style report — same parser as compliance
        const lines = report.split('\n');
        for (const line of lines) {
          const trimmed = line.trim();
          if (!trimmed) { doc.moveDown(0.3); continue; }
          if (trimmed.startsWith('### ')) addSection(doc, trimmed.replace(/^###\s*/, ''));
          else if (trimmed.startsWith('## ')) addSection(doc, trimmed.replace(/^##\s*/, ''));
          else if (trimmed.startsWith('# ')) addSection(doc, trimmed.replace(/^#\s*/, ''));
          else if (trimmed.startsWith('- ') || trimmed.startsWith('* ')) addBullet(doc, trimmed.replace(/^[-*]\s*/, '').replace(/\*\*/g, ''));
          else if (trimmed.match(/^\*\*.+\*\*:/)) { const kv = trimmed.replace(/\*\*/g, '').split(':'); addKeyValue(doc, kv[0].trim(), kv.slice(1).join(':').trim()); }
          else addParagraph(doc, trimmed.replace(/\*\*/g, ''));
        }
      } else if (report && typeof report === 'object') {
        // Structured report object
        if (report.summary) {
          addSection(doc, 'Executive Summary');
          addParagraph(doc, report.summary);
        }
        if (report.findings?.length) {
          addSection(doc, 'Key Findings');
          for (const f of report.findings) addBullet(doc, typeof f === 'string' ? f : JSON.stringify(f));
        }
        if (report.sanctions_status) {
          addSection(doc, 'Sanctions & Compliance Status');
          addParagraph(doc, typeof report.sanctions_status === 'string' ? report.sanctions_status : JSON.stringify(report.sanctions_status, null, 2));
        }
        if (report.risk_assessment) {
          addSection(doc, 'Risk Assessment');
          addParagraph(doc, typeof report.risk_assessment === 'string' ? report.risk_assessment : JSON.stringify(report.risk_assessment, null, 2));
        }
        if (report.recommendations?.length) {
          addSection(doc, 'Recommendations');
          for (const r of report.recommendations) addBullet(doc, typeof r === 'string' ? r : JSON.stringify(r));
        }
        if (report.sources?.length) {
          addSection(doc, 'Sources');
          for (const s of report.sources) addBullet(doc, typeof s === 'string' ? s : `${s.name || s.url || JSON.stringify(s)}`);
        }
      }

      // Footer
      doc.moveDown(1);
      doc.fontSize(8).fillColor(RED).text('CONFIDENTIAL — Arkmurus Ltd — Do not distribute externally', 40, doc.y, { align: 'center' });

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}


// ── Audit-grade Report ──────────────────────────────────────────────────────
//
// Generic "render any ARIA chat output as an audit-grade PDF" path.
//
// The output includes:
//   - Branded header (Arkmurus / ARIA)
//   - Subject line + generation timestamp + user identity + session id
//   - Body (markdown rendered with the same parser as the compliance brief)
//   - Citations section: every inline `[from <url>]`, `[snippet #N]`,
//     `[EXTRACT N]`, `[from ATTACHED DOCUMENT: ...]` extracted into a
//     numbered list at the end. Compliance officers want one place to
//     check sources rather than scanning for inline citations.
//   - Constitution clause references — the relevant sub-set of ARIA's
//     23-clause constitution that applies to this output (e.g. clause
//     14 "no fabricated verifiable facts" + clause 17 "multi-source
//     verification" for any DD report). Scanned heuristically from the
//     content; the operator can tighten the heuristic later.
//   - Audit-trail block: content SHA-256, signature, signing-key
//     fingerprint, "verify at <url>" instruction, ARIA-version + commit.
//
// `metadata` shape:
//   {
//     subject: string,            // primary heading on cover
//     userEmail: string,
//     userId: string,
//     sessionId: string,
//     messageIndex: number,
//     ariaVersion: string,        // e.g. "ARIA v3.0 / commit abc1234"
//     verifyUrl: string,          // public verify endpoint
//   }
//
// `opts.classification` — "CONFIDENTIAL" (default) | "INTERNAL" | "PUBLIC"
//
// Returns Buffer (PDF bytes).
const _CITATION_RE = /\[(?:from\s+([^\]]+)|snippet\s*#?(\d+)|EXTRACT\s+(\d+)|from\s+ATTACHED\s+DOCUMENT:\s*([^\]]+))\]/gi;

const _CONSTITUTION_INDEX = [
  // Heuristic match — keyword in content → which clauses are relevant.
  // Defence-DD outputs touch a narrow band of the 23 clauses; we list
  // those most likely to be load-bearing for compliance officer review.
  { id: 1, label: 'Epistemic honesty' },
  { id: 2, label: 'Source integrity' },
  { id: 3, label: 'Compliance first' },
  { id: 9, label: 'No profiling without data' },
  { id: 10, label: 'Officeholder discipline' },
  { id: 12, label: 'No document review without text' },
  { id: 13, label: 'No `[CONFIRMED]` on uncited current events / no propaganda elevation / no topic bleed' },
  { id: 14, label: 'No fabricated verifiable facts' },
  { id: 15, label: 'Inline citation on tool-derived facts' },
  { id: 17, label: 'Multi-source verification' },
  { id: 18, label: 'Source self-validation' },
  { id: 19, label: 'Search doctrine' },
  { id: 20, label: 'No fabricated commitments / status inflation' },
  { id: 23, label: 'No acceptance of user-asserted compliance premises' },
];

function _extractCitations(content) {
  if (!content) return [];
  const seen = new Map();
  let m;
  _CITATION_RE.lastIndex = 0;
  while ((m = _CITATION_RE.exec(content)) !== null) {
    const url = m[1]?.trim();
    const snippet = m[2] || m[3];
    const doc = m[4]?.trim();
    let key, label;
    if (url) { key = 'url:' + url; label = url; }
    else if (snippet) { key = 'snippet:' + snippet; label = `Snippet ${snippet}`; }
    else if (doc) { key = 'doc:' + doc; label = `Attached document: ${doc}`; }
    else continue;
    if (!seen.has(key)) seen.set(key, { type: url ? 'url' : (doc ? 'doc' : 'snippet'), label });
  }
  return Array.from(seen.values());
}

function _addClassificationStripe(doc, classification) {
  const c = (classification || 'CONFIDENTIAL').toUpperCase();
  const colour = c === 'PUBLIC' ? GREEN : (c === 'INTERNAL' ? ORANGE : RED);
  doc.rect(0, 0, doc.page.width, 18).fill(colour);
  doc.fontSize(8).fillColor(WHITE).text(c + ' — AUDIT-GRADE REPORT', 0, 4, { align: 'center' });
}

function _renderMarkdownBody(doc, body) {
  const lines = (body || '').split('\n');
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) { doc.moveDown(0.25); continue; }
    // R-F1705: a run of separator chars (---, ***, ___, ===, or repeated
    // symbols ARIA sometimes emits) renders as a thin divider, not literal text.
    if (/^([-*_=•·~%#]{3,}|[─━═]{2,})$/.test(trimmed)) { addDivider(doc); continue; }
    if (trimmed.startsWith('### ')) addSection(doc, trimmed.replace(/^###\s*/, ''));
    else if (trimmed.startsWith('## ')) addSection(doc, trimmed.replace(/^##\s*/, ''));
    else if (trimmed.startsWith('# ')) addSection(doc, trimmed.replace(/^#\s*/, ''));
    else if (trimmed.startsWith('- ') || trimmed.startsWith('* ') || trimmed.startsWith('• ')) {
      addBullet(doc, trimmed.replace(/^[-*•]\s*/, '').replace(/\*\*/g, ''));
    } else if (/^\d+[.)]\s+/.test(trimmed)) {
      // Numbered list — keep the number as the marker.
      const mm = trimmed.match(/^(\d+)[.)]\s+([\s\S]*)/);
      addBullet(doc, (mm[2] || '').replace(/\*\*/g, ''), mm[1] + '.');
    } else if (trimmed.match(/^\*\*.+\*\*:/)) {
      const kv = trimmed.replace(/\*\*/g, '').split(':');
      addKeyValue(doc, kv[0].trim(), kv.slice(1).join(':').trim());
    } else {
      addParagraph(doc, trimmed.replace(/\*\*/g, ''));
    }
  }
}

export function generateAuditGradeReport(content, metadata = {}, opts = {}) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const generatedAt = new Date().toISOString();
      const subject = (metadata.subject || 'ARIA Report').slice(0, 200);
      const classification = opts.classification || 'CONFIDENTIAL';

      // R-F1705: classification now renders as a badge inside the header band
      // (the old separate stripe was painted over by the band).
      addHeader(doc, subject,
        `Generated ${generatedAt.slice(0, 16).replace('T', ' ')} UTC  |  ${metadata.ariaVersion || 'ARIA'}`,
        classification);

      // Cover metadata
      addSection(doc, 'Report metadata');
      addKeyValue(doc, 'Subject', subject);
      addKeyValue(doc, 'Generated for', metadata.userEmail || metadata.userId || '—');
      addKeyValue(doc, 'Session ID', metadata.sessionId || '—');
      addKeyValue(doc, 'Message index', metadata.messageIndex !== undefined ? String(metadata.messageIndex) : '—');
      addKeyValue(doc, 'Generated at (UTC)', generatedAt);
      addKeyValue(doc, 'Classification', classification);

      // Body
      addSection(doc, 'Report body');
      _renderMarkdownBody(doc, content);

      // Citations
      const citations = _extractCitations(content);
      addSection(doc, 'Citations & sources');
      if (citations.length === 0) {
        addParagraph(doc,
          'No inline citations were detected in this output. Per ARIA constitution clause 15, ' +
          'tool-derived facts must carry inline citations; the absence of any citation here ' +
          'should be treated as a signal that this output is general-knowledge background rather ' +
          'than tool-derived findings, and should be independently verified before relying on it ' +
          'for compliance decisions.'
        );
      } else {
        citations.forEach((c, i) => addBullet(doc, `[${i + 1}] ${c.label}`));
      }

      // Constitution references
      addSection(doc, 'Constitution discipline');
      addParagraph(doc,
        'ARIA operates under a 23-clause constitution that constrains output. The following ' +
        'clauses are most relevant to interpreting this report:'
      );
      for (const c of _CONSTITUTION_INDEX) {
        addBullet(doc, `Clause ${c.id} — ${c.label}`);
      }
      addParagraph(doc,
        'Full constitution text is available at /api/aria/constitution. Each clause is ' +
        'incident-anchored: past failures that motivated the clause are documented inline ' +
        'in the constitution source. This report is bound by the live constitution active ' +
        'at the moment of generation (see Audit trail).'
      );

      // Audit trail
      const sha = contentHash(content);
      let signature = null;
      let signedNote = '';
      if (signingConfigured()) {
        try {
          signature = signReport({
            contentSha256: sha,
            userId: metadata.userId || '',
            sessionId: metadata.sessionId || '',
            messageIndex: metadata.messageIndex,
            generatedAt,
          });
        } catch (e) {
          signedNote = ` (signing failed: ${e.message})`;
        }
      } else {
        signedNote = ' — UNSIGNED. Set REPORT_SIGNING_KEY on the server to enable HMAC signing.';
      }

      addSection(doc, 'Audit trail');
      addKeyValue(doc, 'Content SHA-256', sha);
      addKeyValue(doc, 'HMAC signature (v1)', signature || ('not signed' + signedNote));
      if (metadata.verifyUrl) {
        addKeyValue(doc, 'Verify at', metadata.verifyUrl);
        addParagraph(doc,
          'To verify this report has not been altered: extract the body, compute SHA-256, ' +
          'submit (sha + userId + sessionId + messageIndex + generatedAt + signature) to the ' +
          'verify endpoint above. A non-matching signature indicates the PDF has been edited ' +
          'after generation OR the signing key has been rotated.'
        );
      }
      if (!signingConfigured()) {
        addParagraph(doc,
          'WARNING: this report is unsigned. The PDF integrity cannot be verified by a third party. ' +
          'Treat as informational only; do not present as audit evidence.'
        );
      }

      // Final classification footer
      doc.moveDown(1);
      const colour = classification === 'PUBLIC' ? GREEN : (classification === 'INTERNAL' ? ORANGE : RED);
      doc.fontSize(8).fillColor(colour)
        .text(`${classification} — Arkmurus Ltd — ` +
              (classification === 'PUBLIC' ? 'May be redistributed' : 'Do not distribute externally'),
              40, doc.y, { align: 'center' });

      // R-F82: per-customer watermark + per-page tracing line BEFORE the
      // standard footer so the watermark identification is the most
      // visible element near the bottom edge of every page.
      addCustomerWatermark(doc, metadata, signature);
      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}


// ── Approach Pack ────────────────────────────────────────────────────────────
export function generateApproachPack(market, product, approach, gtm, contacts) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: 40, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      addHeader(doc, `Approach Pack — ${market}`,
        `${product || 'General'} | Generated ${new Date().toISOString().slice(0, 16).replace('T', ' ')} UTC`);

      // 1. Market Profile
      addSection(doc, '1. Market Profile');
      if (approach?.profile) {
        addKeyValue(doc, 'Language', approach.profile.language);
        addKeyValue(doc, 'Formality', approach.profile.formality);
        addKeyValue(doc, 'Greeting', approach.profile.greeting);
        addKeyValue(doc, 'Timezone', approach.profile.timezone);
        addKeyValue(doc, 'Currency', approach.profile.currency);
        addKeyValue(doc, 'Approach', approach.profile.approach);
      }

      // 2. GTM Strategy
      if (gtm) {
        addSection(doc, '2. Go-To-Market Strategy');
        addKeyValue(doc, 'Relationship Tier', gtm.tier);
        addKeyValue(doc, 'Time to First Deal', gtm.timeToFirstDeal);
        addKeyValue(doc, 'Exhibition', gtm.exhibition);
        addKeyValue(doc, 'Best OEMs', gtm.bestOEM);
        addKeyValue(doc, 'Offset', gtm.offset);
        addKeyValue(doc, 'Partner Needed', String(gtm.playbook?.partnerNeeded || 'No'));
        addKeyValue(doc, 'Local Agent', String(gtm.playbook?.localAgentNeeded || 'No'));
        addKeyValue(doc, 'Key Risk', gtm.playbook?.keyRisk);
        doc.moveDown(0.3);
        doc.fontSize(10).fillColor(PURPLE).text('Steps:');
        (gtm.playbook?.steps || []).forEach((s, i) => addBullet(doc, `${i + 1}. ${s}`));
      }

      // 3. Recommended OEMs
      addSection(doc, '3. Ranked OEM Partners');
      (approach?.rankedOEMs || []).forEach((o, i) => {
        addBullet(doc, `${i + 1}. ${o.oem} (${o.country}) — Price: ${o.price} | Africa: ${o.africa} | ${o.itar ? 'ITAR-CONTROLLED' : 'Non-ITAR'}`);
        addParagraph(doc, `   Products: ${o.products}`);
      });

      // 4. Key Contacts
      addSection(doc, '4. Key Decision Makers');
      if (contacts?.length) {
        contacts.slice(0, 6).forEach(c => {
          addBullet(doc, `${c.name} — ${c.title || c.role}`);
          if (c.organisation) addParagraph(doc, `   ${c.organisation} | Influence: ${c.influence || '—'}`);
        });
      } else {
        addParagraph(doc, 'No contacts in database for this market. Recommend HUMINT development.');
      }

      // 5. Compliance Checklist
      addSection(doc, '5. Compliance Checklist');
      (approach?.compliance || []).forEach(c => addBullet(doc, c));

      // 6. Draft Opening Message
      if (approach?.draftMessage) {
        addSection(doc, '6. Draft Opening Message');
        addParagraph(doc, approach.draftMessage);
      }

      // 7. Estimated Timeline
      addSection(doc, '7. Deal Economics');
      addKeyValue(doc, 'Estimated Cycle', approach?.estimatedCycle || '3-12 months');
      addKeyValue(doc, 'Commission Range', '5-12% of total deal value');
      addParagraph(doc, 'Note: factor training package (5-15% of equipment cost), spares (10-20%), logistics (3-8%), and offset obligations into total deal value before calculating commission.');

      addFooter(doc);
      doc.end();
    } catch (e) { reject(e); }
  });
}
