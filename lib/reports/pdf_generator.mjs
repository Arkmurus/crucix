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

// R-F2618 — Cyrillic → Latin (BGN/PCGN-style). The core WinAnsi font cannot render
// Cyrillic, and _pdfSafe used to DROP it, so Russian/ex-Soviet entity names (the #1
// non-Latin case in sanctions/defence DD) vanished from the PDF entirely. Sanctions
// lists (OFAC/UN/OFSI) romanise these names anyway, so transliterating preserves the
// information faithfully. Value maps intentionally include multi-char digraphs.
const _CYRILLIC = {
  а:'a',б:'b',в:'v',г:'g',д:'d',е:'e',ё:'e',ж:'zh',з:'z',и:'i',й:'y',к:'k',л:'l',м:'m',
  н:'n',о:'o',п:'p',р:'r',с:'s',т:'t',у:'u',ф:'f',х:'kh',ц:'ts',ч:'ch',ш:'sh',щ:'shch',
  ъ:'',ы:'y',ь:'',э:'e',ю:'yu',я:'ya',і:'i',ї:'yi',є:'ye',ґ:'g',
  А:'A',Б:'B',В:'V',Г:'G',Д:'D',Е:'E',Ё:'E',Ж:'Zh',З:'Z',И:'I',Й:'Y',К:'K',Л:'L',М:'M',
  Н:'N',О:'O',П:'P',Р:'R',С:'S',Т:'T',У:'U',Ф:'F',Х:'Kh',Ц:'Ts',Ч:'Ch',Ш:'Sh',Щ:'Shch',
  Ъ:'',Ы:'Y',Ь:'',Э:'E',Ю:'Yu',Я:'Ya',І:'I',Ї:'Yi',Є:'Ye',Ґ:'G',
};

// R-F2618 — letter scripts the WinAnsi core font cannot draw (CJK, Arabic, Hebrew,
// Hangul, Kana, Thai, Devanagari, Greek). We can't render them without an embedded
// Unicode font, but we must NOT silently erase them — emit a visible '?' placeholder
// so a foreign entity name shows as e.g. "Huawei (????)" rather than "Huawei ()".
function _isUnsupportedLetter(cp) {
  return (
    (cp >= 0x0370 && cp <= 0x03FF) ||  // Greek
    (cp >= 0x0590 && cp <= 0x05FF) ||  // Hebrew
    (cp >= 0x0600 && cp <= 0x06FF) ||  // Arabic
    (cp >= 0x0900 && cp <= 0x097F) ||  // Devanagari
    (cp >= 0x0E00 && cp <= 0x0E7F) ||  // Thai
    (cp >= 0x3040 && cp <= 0x30FF) ||  // Hiragana + Katakana
    (cp >= 0x3400 && cp <= 0x9FFF) ||  // CJK ideographs
    (cp >= 0xAC00 && cp <= 0xD7AF) ||  // Hangul
    (cp >= 0xF900 && cp <= 0xFAFF) ||  // CJK compatibility
    (cp >= 0x20000 && cp <= 0x2FA1F)   // CJK extension B+
  );
}

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
    if (_CYRILLIC[ch] !== undefined) { out += _CYRILLIC[ch]; continue; }  // R-F2618 — romanise, don't drop
    if (_isUnsupportedLetter(cp)) { out += '?'; continue; }               // R-F2618 — visible placeholder, never silent erasure
    // else: decorative symbol / emoji / box-drawing char we can't render → drop (as before).
  }
  return out;
}

// ── R-F1705 helpers (sanitised text, consistent margins, no overflow paging) ──
function _ensureSpace(doc, needed) {
  if (doc.y > PAGE_H - MARGIN - 24 - (needed || 0)) { doc.addPage(); doc.x = MARGIN; doc.y = MARGIN; }
}

function addHeader(doc, title, subtitle, classification) {
  // R-F2982 — clean corporate letterhead. No dark band: a white header with the
  // Aria Intelligence wordmark, a thin brand accent rule, and a dark-ink title.
  const topY = 32;
  doc.font('Helvetica-Bold').fontSize(18).fillColor(PURPLE)
    .text('ARIA', MARGIN, topY, { continued: true });
  doc.font('Helvetica').fontSize(11).fillColor(MUTE).text('  INTELLIGENCE');
  // classification badge, top-right — subtle pill
  if (classification) {
    const c = String(classification).toUpperCase();
    const col = c === 'PUBLIC' ? GREEN : (c === 'INTERNAL' ? ORANGE : RED);
    const bw = doc.font('Helvetica-Bold').fontSize(7.5).widthOfString(c) + 18;
    doc.roundedRect(PAGE_W - MARGIN - bw, topY + 1, bw, 15, 7.5).fill(col);
    doc.fillColor(WHITE).font('Helvetica-Bold').fontSize(7.5)
      .text(c, PAGE_W - MARGIN - bw, topY + 5, { width: bw, align: 'center' });
  }
  const ruleY = topY + 26;
  doc.moveTo(MARGIN, ruleY).lineTo(PAGE_W - MARGIN, ruleY).lineWidth(1).strokeColor(PURPLE).stroke();
  doc.font('Helvetica-Bold').fontSize(15).fillColor(INK)
    .text(_pdfSafe(title), MARGIN, ruleY + 12, { width: CONTENT_W - 10, ellipsis: true, height: 20, lineBreak: false });
  if (subtitle) doc.font('Helvetica').fontSize(8.5).fillColor(MUTE)
    .text(_pdfSafe(subtitle), MARGIN, ruleY + 32, { width: CONTENT_W, lineBreak: false });
  doc.fillColor(INK);
  doc.x = MARGIN; doc.y = ruleY + 52;
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
      .text('ARIA INTELLIGENCE — CONFIDENTIAL', MARGIN, y, { width: 320, lineBreak: false });
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
      doc.fontSize(8).fillColor(RED).text('CONFIDENTIAL — Aria Intelligence — Do not distribute externally', 40, doc.y, { align: 'center' });

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
      doc.fontSize(8).fillColor(RED).text('CONFIDENTIAL — Aria Intelligence — Do not distribute externally', 40, doc.y, { align: 'center' });

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
//   - Branded header (Aria Intelligence)
//   - Subject line + generation timestamp + user identity + session id
//   - Body (markdown rendered with the same parser as the compliance brief)
//   - Citations section: every inline `[from <url>]`, `[snippet #N]`,
//     `[EXTRACT N]`, `[from ATTACHED DOCUMENT: ...]` extracted into a
//     numbered list at the end. Compliance officers want one place to
//     check sources rather than scanning for inline citations.
//   - Constitution clause references — the relevant sub-set of ARIA's
//     behavioural constitution that applies to this output (the clause
//     COUNT is supplied live via metadata.constitutionClauseCount per
//     R-F2857; never hardcode it here) (e.g. clause
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
  // Defence-DD outputs touch a narrow band of the clauses; we list
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

// R-F2618 — a GFM table delimiter row, e.g. "|---|:--:|---|" or "--- | ---".
function _isDelimRow(t) { return /^\|?[\s:|-]+\|?$/.test(t) && t.includes('-'); }

// R-F2618 — render a parsed markdown table (array of cell-arrays; row 0 = header) as an
// actual aligned table. Previously each "| a | b |" line fell through to addParagraph and
// printed raw pipes, so the tables that carry the substance of a DD report (risk matrix,
// UBO chain, financials, sanctions hits) were unreadable. Equal columns, shaded header,
// page-break safe (each row calls _ensureSpace).
function _renderTable(doc, rows) {
  rows = (rows || []).filter(r => r && r.length);
  if (!rows.length) return;
  const ncols = Math.max(...rows.map(r => r.length));
  if (ncols < 1) return;
  const colW = CONTENT_W / ncols;
  const padX = 4, padY = 3, fs = 8.5;
  doc.moveDown(0.35);
  rows.forEach((row, ri) => {
    const header = ri === 0;
    doc.font(header ? 'Helvetica-Bold' : 'Helvetica').fontSize(fs);
    let rowH = 0;
    for (let c = 0; c < ncols; c++) {
      const h = doc.heightOfString(_pdfSafe(row[c] || '') || ' ', { width: colW - 2 * padX });
      if (h > rowH) rowH = h;
    }
    rowH += 2 * padY;
    _ensureSpace(doc, rowH + 4);
    const y = doc.y;
    if (header) doc.rect(MARGIN, y, CONTENT_W, rowH).fill('#f1eef8');
    doc.font(header ? 'Helvetica-Bold' : 'Helvetica').fontSize(fs).fillColor(header ? PURPLE : INK_SOFT);
    for (let c = 0; c < ncols; c++) {
      doc.text(_pdfSafe(row[c] || ''), MARGIN + c * colW + padX, y + padY, { width: colW - 2 * padX, lineGap: 1 });
    }
    doc.moveTo(MARGIN, y + rowH).lineTo(MARGIN + CONTENT_W, y + rowH).lineWidth(0.4).strokeColor(LINE).stroke();
    doc.y = y + rowH;
  });
  doc.moveDown(0.4);
  doc.font('Helvetica').fillColor(INK).fontSize(9.5);
}

function _renderMarkdownBody(doc, body) {
  const lines = (body || '').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const trimmed = lines[i].trim();
    if (!trimmed) { doc.moveDown(0.25); continue; }
    // R-F2618 — GFM table block: a row with pipes immediately followed by a |---| delimiter.
    if (trimmed.includes('|') && i + 1 < lines.length && _isDelimRow(lines[i + 1].trim())) {
      const block = [];
      let j = i;
      while (j < lines.length && lines[j].trim().includes('|')) { block.push(lines[j].trim()); j++; }
      const rows = block
        .filter(l => !_isDelimRow(l))
        .map(l => l.replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim().replace(/\*\*/g, '')));
      _renderTable(doc, rows);
      i = j - 1;
      continue;
    }
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
      //
      // R-F2857 — this count is DERIVED, never hardcoded. It read "23-clause"
      // while the live behavioural constitution was v37 / 37 clauses
      // (GET /api/aria/constitution/version), so every DD PDF understated
      // governance by 14 clauses in the exact section a compliance officer
      // reads. Swapping the literal 23 -> 37 would drift again at the next
      // amendment (CLAUDE.md §1: root cause, not symptom) — the same lesson
      // R-F221/R-F2617 already applied to public/model-card.html.
      //
      // The caller passes its live reading. When that is UNAVAILABLE we say
      // nothing about a count rather than assert an unverified one: stating a
      // number we cannot verify is precisely the failure ARIA's USP forbids.
      addSection(doc, 'Constitution discipline');
      const _clauseCount = Number.isFinite(Number(metadata.constitutionClauseCount))
        ? Number(metadata.constitutionClauseCount)
        : null;
      addParagraph(doc,
        (_clauseCount
          ? `ARIA operates under a ${_clauseCount}-clause constitution that constrains output. `
          : 'ARIA operates under a numbered behavioural constitution that constrains output. ') +
        'The following clauses are most relevant to interpreting this report:'
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
        .text(`${classification} — Aria Intelligence — ` +
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

// ── R-F2837 — Due-diligence report PDF ───────────────────────────────────────
//
// DD reports had NO export path: dd-reports.html rendered to screen only (its
// four "export" hits were the unrelated VLS public-key button), while this
// module's proven toolkit sat one import away — the same built-but-unwired
// pattern found repeatedly this month.
//
// STRUCTURE borrows from a competitor reference (NorthRow) where it is genuinely
// better: a persistent header on every page, a scannable summary, clean tabular
// facts. It DIVERGES on the one thing that matters — NorthRow prints
// "Contego Risk Score: 0" with no statement of what was NOT checked. A clean
// score without a coverage statement reads as "clear" when it may only mean
// "unexamined".
//
// ★ THE RULE THIS FILE ENFORCES: whatever ARIA found is what gets printed —
// GREEN, AMBER or RED, unaltered. This renderer NEVER upgrades, softens or omits
// a verdict, and NEVER prints a verdict without the decision-readiness state
// beside it. A GREEN classification paired with NOT_CLEARED is the honest output
// and both halves must appear together, or the document becomes a false clean in
// the most consequential place we produce one: the artefact a client acts on.
//
// Nothing here computes or re-derives a score. It renders what the report says.

const _VERDICT_COLOURS = {
  GREEN: GREEN, AMBER: ORANGE, RED: RED,
  HARD_STOP: RED, INSUFFICIENT: MUTE, UNKNOWN: MUTE,
};

// R-F3285 — the DISPLAYED name of a verdict, matching public/js/app.js
// riskLabel() exactly. A report a customer downloads and the page they read it
// on must not disagree about what the verdict is called: the same file was
// showing AMBER-LIGHT in the library, AMBERLIGHT on the PDF pill and AMBER on a
// finding pill. The STORED value is untouched — AMBER-LIGHT and AMBER-DARK are
// what the engine computed and what the archived report holds; this only
// decides what a human reads. A test asserts the two implementations agree,
// because this file runs in Node and cannot import a browser global.
function riskLabel(v) {
  const k = String(v || '').trim().toUpperCase().replace(/[\s-]+/g, '_');
  if (!k) return '';
  if (k.includes('AMBER')) return 'AMBER';
  if (k.includes('HARD_STOP')) return 'HARD STOP';
  return k.replace(/_/g, ' ');
}

function _verdictColour(v) {
  const s = String(v || '').toUpperCase();
  if (_VERDICT_COLOURS[s]) return _VERDICT_COLOURS[s];
  // R-F2982 — traffic-light verdicts carry a "LIGHT" suffix (AMBERLIGHT /
  // GREENLIGHT / REDLIGHT) and must render in their colour, not the gray
  // fallback (a Silverbrook AMBERLIGHT was showing as a gray pill).
  if (s.includes('RED')) return RED;
  if (s.includes('AMBER') || s.includes('ORANGE')) return ORANGE;
  if (s.includes('GREEN')) return GREEN;
  return MUTE;
}

function _fmtVal(v) {
  if (v == null) return '-';
  if (Array.isArray(v)) return v.length ? v.map(String).join(', ') : '-';
  if (typeof v === 'object') return '-';
  return String(v);
}

// ── R-F3049 — PDF / online PARITY ───────────────────────────────────────────
//
// The online view (dd_schema.structured_view) pulls the MEANINGFUL SCALAR out of
// each nested layer object — `country_risk.headline_risk`, `export_control
// .recommendation`, `financial_health.health_verdict`, the named controllers. The
// PDF flattened the same objects to "(present)" and lists to "N items", so two
// renderings of one report disagreed on every nested field. This table is the
// PDF's half of that contract: for each fact key, how to say it in words.
//
// Keys are the layer field names used in _DD_LAYER_PLAN.
const _FACT_SCALARS = {
  country_risk: ['headline_risk', 'risk_level', 'tier'],
  export_control: ['recommendation'],
  financial_health: ['health_verdict'],
  ghost_score: ['__ghost'],
  commercial_coherence: ['tier', 'coherence_score'],
};

// R-F3049 — labels that must match the online view word-for-word. Anything absent
// here falls back to a title-cased field name, which is already identical on both
// surfaces (e.g. "Registration Status").
const _FACT_LABELS = {
  ubo_chain: 'UBO chain nodes traversed',
  controlled_by: 'Controllers (registry-anchored)',
  controlled_by_unanchored: 'Controllers NOT traversed',
  ghost_score: 'Ghost score',
  country_risk: 'Country risk',
  export_control: 'Export control',
  financial_health: 'Financial health',
  grounded_rate: 'Grounded rate',
  source_tier_breakdown: 'Source tiers',
  press_coverage: 'Press coverage',
  declared_activity: 'Declared activity (SIC)',
};

// List fields rendered by NAMING their members rather than counting them.
const _FACT_NAMED_LISTS = {
  controlled_by: (x) => x.controller_name,
  controlled_by_unanchored: (x) => x.controller_name,
  ubo_chain: (x) => x.name,
  pep_connections: (x) => x.name,
  sanctions_network: (x) => x.name,
  cross_linked_entities: (x) => x.name || x.company_name,
  sanctions_regimes: (x) => (typeof x === 'string' ? x : x && x.regime),
};

function _fmtFactValue(key, v) {
  if (v == null || v === '') return null;
  if (Array.isArray(v)) {
    if (!v.length) return null;
    const picker = _FACT_NAMED_LISTS[key];
    if (picker) {
      const names = v.map((x) => (typeof x === 'string' ? x : picker(x || {})))
        .filter(Boolean).map(String);
      if (names.length) {
        // Name them; a long chain is truncated with an explicit remainder so the
        // count is never silently substituted for the content.
        const shown = names.slice(0, 8);
        return shown.join('; ') + (names.length > shown.length
          ? ` (+${names.length - shown.length} more)` : '');
      }
    }
    return v.length + ' item' + (v.length === 1 ? '' : 's');
  }
  if (typeof v === 'object') {
    // ghost_score has its own shape: {total, max_total, classification}
    if (v.total != null && (v.max_total != null || v.classification)) {
      return `${v.total}/${v.max_total ?? '?'}${v.classification ? ' ' + v.classification : ''}`;
    }
    // R-F3049 — a pure count map (e.g. source_tier_breakdown {T1:1, UNVERIFIED:2})
    // renders as the breakdown the online view shows, not as one arbitrary number.
    const entries = Object.entries(v);
    if (entries.length && entries.every(([, n]) => typeof n === 'number')) {
      return entries.map(([k2, n]) => `${k2}:${n}`).join(', ');
    }
    for (const f of (_FACT_SCALARS[key] || [])) {
      if (v[f] != null && v[f] !== '') return String(v[f]);
    }
    // Last resort: any short scalar the object carries, so a nested block is
    // never rendered as the content-free "(present)".
    for (const [, val] of Object.entries(v)) {
      if ((typeof val === 'string' || typeof val === 'number') && String(val).length <= 80) {
        return String(val);
      }
    }
    return null;
  }
  // R-F3049 — the online view renders grounded_rate as a percentage; match it.
  if (typeof v === 'number' && key === 'grounded_rate') return Math.round(v * 100) + '%';
  return String(v);
}

// Verdict and readiness render as ONE block. They are never shown apart.
function addVerdictBlock(doc, risk, readiness) {
  // Colour is decided from the RAW value (AMBERLIGHT still has to find its
  // colour); the pill PRINTS the normalised label.
  const raw = String(risk || 'UNKNOWN').toUpperCase();
  const col = _verdictColour(raw);
  const v = riskLabel(raw) || 'UNKNOWN';
  const status = String(readiness?.status || 'UNKNOWN').toUpperCase();
  const cleared = readiness?.clearance_ready === true;

  _ensureSpace(doc, 100);
  const y = doc.y;
  const boxH = 62;
  doc.roundedRect(MARGIN, y, CONTENT_W, boxH, 4).lineWidth(0.6).strokeColor(LINE).stroke();
  // R-F2982 — pill auto-fits the verdict text so long labels ("AMBERLIGHT") no
  // longer wrap/clip. Vertically centred in the 34px pill.
  const pillFs = 13;
  const pillW = Math.max(96, doc.font('Helvetica-Bold').fontSize(pillFs).widthOfString(v) + 22);

  // R-F3544 — LABEL BOTH. On the delivered Bidvest Noonan report (dd_75d996233394) this
  // block printed a large GREEN pill beside the words "NOT CLEARED", and a client who
  // skims reads the colour as the verdict. Both facts were already here; neither said
  // WHICH QUESTION it answers. Risk aggregates adverse SIGNALS ("did anything bad
  // surface?"); clearance asks "is there enough evidence to clear this?" — and an
  // unlabelled colour silently claims to answer both.
  //
  // Captions, not a recolour: R-F2786 separates the two BY DESIGN, and capping the
  // colour at the clearance status (tried as R-F3537) overrode that design and lost
  // information. Naming each keeps both facts and makes neither mistakable.
  doc.font('Helvetica-Bold').fontSize(6.5).fillColor(INK_SOFT)
    .text('RISK SIGNAL', MARGIN + 12, y + 5, { width: pillW, align: 'center', lineBreak: false });

  doc.roundedRect(MARGIN + 12, y + 14, pillW, 34, 4).fill(col);
  doc.font('Helvetica-Bold').fontSize(pillFs).fillColor(WHITE)
    .text(v, MARGIN + 12, y + 25, { width: pillW, align: 'center', lineBreak: false });

  const rx = MARGIN + 12 + pillW + 16;
  const rw = PAGE_W - MARGIN - rx;
  doc.font('Helvetica-Bold').fontSize(6.5).fillColor(INK_SOFT)
    .text('CLEARANCE', rx, y + 5, { width: rw, lineBreak: false });
  doc.font('Helvetica-Bold').fontSize(11).fillColor(cleared ? GREEN : ORANGE)
    .text(status.replace(/_/g, ' '), rx, y + 16, { width: rw, lineBreak: false });

  const bits = [];
  if (readiness?.answered != null && readiness?.required != null) {
    bits.push(readiness.answered + ' of ' + readiness.required + ' decision-critical questions answered');
  }
  if (readiness?.evidence_grade) bits.push('evidence grade ' + readiness.evidence_grade);
  doc.font('Helvetica').fontSize(8.5).fillColor(INK_SOFT)
    .text(_pdfSafe(bits.join('  |  ')) || '-', rx, y + 34, { width: rw, lineBreak: false });

  doc.y = y + boxH + 12;
  doc.x = MARGIN;

  if (!cleared) {
    // The disclosure is not a footnote. It sits directly beneath the verdict.
    addParagraph(doc,
      'This classification reflects only the checks that COMPLETED. It is not a clearance '
      + 'decision and must not be read as one. See the decision-readiness scorecard and the '
      + 'data gaps below for what remains unverified.');
  }
}

// R-F3091 — WHICH ENTITY is this report about?
//
// LIVE DEFECT (Mitie). The registry layer described MITIE FACILITIES MANAGEMENT
// LIMITED (02938041), a subsidiary; the press, financials and procurement awards all
// described the listed parent. The report never said which entity any layer covered,
// so group evidence and subsidiary evidence read as one company's file.
//
// Renders ONLY for a subsidiary — a standalone company has nothing to disambiguate —
// and reads the same persisted `entity_scope` object the online view renders, so the
// two surfaces cannot drift apart.
// Pure, so the CONTENT is directly assertable (a PDF buffer is compressed and
// cannot be grepped — the same reason ddReportSections exists).
export function ddEntityScopeLines(scope) {
  if (!scope || typeof scope !== 'object' || !scope.is_subsidiary) return [];
  const out = [['Registry subject',
    _fmtVal(scope.subject_name)
    + (scope.subject_registration ? ' (' + scope.subject_registration + ')' : '')]];
  const parent = scope.immediate_parent || {};
  if (parent.name) {
    out.push(['Controlled by', parent.name + (parent.registration_number
      ? ' (' + parent.registration_number + ')'
      : ' - no registration number; chain not walked')]);
  }
  // R-F3220 — PDF/online parity: arrows are reserved for the registry-anchored
  // ownership descent. The walk's node list is a set of parties reached, not a
  // chain, and joining siblings with arrows asserts control that was never found.
  const chain = Array.isArray(scope.ownership_chain_traced) ? scope.ownership_chain_traced : [];
  if (chain.length > 1) out.push(['Ownership chain (registry-anchored)', chain.join(' > ')]);
  const parties = Array.isArray(scope.parties_traversed) ? scope.parties_traversed : [];
  for (const p of parties) {
    if (!p || !Array.isArray(p.names) || !p.names.length || p.hop === 0) continue;
    out.push(['Parties traversed - ' + (p.relation || ('hop ' + p.hop)), p.names.join('; ')]);
  }
  return out;
}

function addEntityScope(doc, scope) {
  const rows = ddEntityScopeLines(scope);
  if (!rows.length) return;
  addSection(doc, 'Entity scope');
  for (const [k, v] of rows) addKeyValue(doc, k, v);
  for (const w of (Array.isArray(scope.warnings) ? scope.warnings : [])) {
    if (w) addParagraph(doc, w);
  }
}

// The five decision-critical questions, verbatim, pass or fail.
function addReadinessScorecard(doc, readiness) {
  const qs = readiness?.questions;
  if (!qs || typeof qs !== 'object') return;
  addSection(doc, 'Decision-readiness scorecard');
  for (const key of Object.keys(qs)) {
    const q = qs[key] || {};
    const ok = q.answered === true;
    _ensureSpace(doc, 32);
    const y = doc.y;
    doc.font('Helvetica-Bold').fontSize(9).fillColor(ok ? GREEN : ORANGE)
      .text(ok ? 'YES' : 'NO', MARGIN, y, { width: 28, lineBreak: false });
    doc.font('Helvetica-Bold').fontSize(9.5).fillColor(INK)
      .text(_pdfSafe(q.label || key), MARGIN + 32, y, { width: CONTENT_W - 32 });
    const detail = ok ? (q.evidence || '') : (q.blocker || '');
    if (detail) {
      doc.font('Helvetica').fontSize(8.5).fillColor(MUTE)
        .text(_pdfSafe(detail), MARGIN + 32, doc.y, { width: CONTENT_W - 32, lineGap: 2 });
    }
    doc.moveDown(0.45);
  }
}

// Data gaps, verbatim and prominent — never summarised away.
function addDataGaps(doc, gaps) {
  const list = Array.isArray(gaps) ? gaps.filter(Boolean) : [];
  addSection(doc, 'Data gaps and limitations');
  if (!list.length) {
    addParagraph(doc, 'No data gaps were recorded for this run.');
    return;
  }
  addParagraph(doc,
    'The following could not be established. Each is a limit on what this report '
    + 'evidences, not a finding of good standing.');
  for (const g of list) addBullet(doc, typeof g === 'string' ? g : _fmtVal(g), '!');
}

/**
 * Due-diligence report PDF (R-F2837).
 *
 * @param {object} report    stored DD body, as GET /api/aria/dd/report/{run_id}
 * @param {object} metadata  { docRef } — provenance only
 * Renders what the report contains. Computes no score, alters no verdict.
 */

// ── R-F2848 — full findings sections in the DD PDF ───────────────────────────
//
// R-F2837's PDF was a decision SUMMARY: verdict, scorecard, gaps. It omitted the
// per-layer findings (identity, network, verification, compliance, digital,
// commercial coherence, intelligence sweep) that the UI renders — the substance
// a reader needs. This adds them, rendered from the STRUCTURED report, not by
// re-parsing markdown.
//
// It renders what is present and nothing else: a layer with no findings prints
// its status and moves on; an empty layer is skipped. No field is invented, no
// severity is inferred, no verdict is re-derived (the R-F2837 rule holds). A
// finding with a source URL prints it, so every claim in the PDF is traceable
// exactly as far as the report itself traces it.

const _SEV_RANK = { red: 0, high: 0, critical: 0, amber: 1, medium: 1, orange: 1,
                    yellow: 1, info: 2, low: 2, green: 3 };
const _SEV_COLOUR = { red: RED, high: RED, critical: RED, amber: ORANGE, medium: ORANGE,
                      orange: ORANGE, yellow: ORANGE, info: MUTE, low: MUTE, green: GREEN };

function _sevColour(sev) {
  return _SEV_COLOUR[String(sev || '').toLowerCase()] || MUTE;
}

// One finding: a severity dot, the title, then detail / source / confidence.
function addFinding(doc, f) {
  if (!f || typeof f !== 'object') return;
  const title = f.title || f.claim || f.name || '';
  if (!title) return;
  const sev = String(f.severity || f.tier || '').toLowerCase();

  _ensureSpace(doc, 34);
  const y = doc.y;
  doc.circle(MARGIN + 3, y + 5, 2.5).fill(_sevColour(sev));
  doc.font('Helvetica-Bold').fontSize(9).fillColor(INK)
    .text(_pdfSafe(title), MARGIN + 12, y, { width: CONTENT_W - 12 });

  const detail = f.detail || f.description || f.summary || '';
  if (detail) {
    doc.font('Helvetica').fontSize(8.5).fillColor(INK_SOFT)
      .text(_pdfSafe(detail), MARGIN + 12, doc.y, { width: CONTENT_W - 12, lineGap: 2 });
  }
  // Provenance line — only what the finding actually carries.
  const prov = [];
  if (sev) prov.push(sev.toUpperCase());
  if (f.confidence) prov.push(String(f.confidence));
  const src = f.source || (Array.isArray(f.sources) ? f.sources.join(', ') : '');
  if (src) prov.push('source: ' + src);
  if (f.url) prov.push(String(f.url));
  if (prov.length) {
    doc.font('Helvetica').fontSize(7.5).fillColor(MUTE)
      .text(_pdfSafe(prov.join('  ·  ')), MARGIN + 12, doc.y, { width: CONTENT_W - 12 });
  }
  doc.moveDown(0.35);
}

function _sevRank(sev) {
  return _SEV_RANK[String(sev || '').toLowerCase()] ?? 2;
}

// ── R-F3024 / R-F3026 / R-F3027 — people + name-history formatters ──────────
// Pure string builders (no drawing), so what the PDF says about a director is
// unit-testable without rendering a PDF. Each renders ONLY the fields present —
// a missing appointment date must never become an invented one.
function _fmtOfficer(o) {
  if (!o || typeof o !== 'object') return String(o || '').trim();
  const name = String(o.name || '').trim();
  if (!name) return '';
  const bits = [];
  const role = String(o.officer_role || o.role || '').replace(/-/g, ' ').trim();
  if (role) bits.push(role);
  if (o.appointed_on) bits.push('appointed ' + o.appointed_on);
  if (o.resigned_on) bits.push('RESIGNED ' + o.resigned_on);
  if (o.nationality) bits.push(String(o.nationality));
  if (o.occupation) bits.push(String(o.occupation));
  return name + (bits.length ? ' — ' + bits.join(', ') : '');
}

function _fmtPsc(p) {
  if (!p || typeof p !== 'object') return String(p || '').trim();
  const name = String(p.name || '').trim();
  if (!name) return '';
  const bits = [];
  const kind = String(p.kind || '').replace('-person-with-significant-control', '')
    .replace(/-/g, ' ').trim();
  if (kind) bits.push(kind);
  const nat = Array.isArray(p.natures_of_control) ? p.natures_of_control : [];
  if (nat.length) bits.push(nat.slice(0, 4).map((n) => String(n).replace(/-/g, ' ')).join('; '));
  const ident = (p.identification && typeof p.identification === 'object') ? p.identification : {};
  if (ident.registration_number) bits.push('reg ' + ident.registration_number);
  if (p.ceased_on) bits.push('CEASED ' + p.ceased_on);
  return name + (bits.length ? ' — ' + bits.join(', ') : '');
}

function _fmtPrevName(p) {
  if (!p || typeof p !== 'object') return String(p || '').trim();
  const name = String(p.name || '').trim();
  if (!name) return '';
  return name + ' (until ' + (p.ceased_on || '?')
    + (p.effective_from ? ', from ' + p.effective_from : '') + ')';
}

// R-F3055 — mirror of dd_schema._render_adverse_media. Kept as a pure line-builder
// so the PDF's wording is unit-testable and can be diffed against the Python side.
const _ADVERSE_STATUS_WORDS = {
  completed: 'COMPLETED', complete: 'COMPLETED',
  // R-F3067 — a self-bounded sweep that returned findings is not a failure.
  partial: 'COMPLETED (bounded — stopped early)',
  in_progress: 'STILL RUNNING', running: 'STILL RUNNING',
  incomplete: 'DID NOT COMPLETE', failed: 'FAILED', error: 'FAILED',
};

// R-F3056 — draw one CLICKABLE line. ARIA's USP is that every claim is checkable at
// its primary source; a URL a reader has to retype is not checkable in practice.
// PDFKit turns `link` into a real annotation, so the source opens from the file a
// client has filed. Falls back to plain text for anything that is not http(s), so a
// `memory://` self-reference can never masquerade as an openable source.
const _LINK_BLUE = '#1a4d8f';

function _linkLine(doc, text, url, opts = {}) {
  const safe = _pdfSafe(String(text || ''));
  const bullet = opts.indent ? '    - ' : '  • ';
  const isWeb = /^https?:\/\//i.test(String(url || ''));
  if (!isWeb) {
    doc.font('Helvetica').fontSize(8).fillColor(INK)
      .text(bullet + safe, MARGIN, doc.y, { width: CONTENT_W });
    return;
  }
  doc.font('Helvetica').fontSize(8).fillColor(_LINK_BLUE)
    .text(bullet + safe, MARGIN, doc.y, {
      width: CONTENT_W, link: String(url), underline: true,
    });
  doc.fillColor(INK);
}

// R-F3060 — the decision-ready SUMMARY that leads the adverse-media block: what the
// concern is, and what to do about it. Mirrors dd_schema._adverse_media_summary so
// the PDF and the online view give the same advice. Derived only from what the blob
// records — never invents a concern, never offers reassurance the evidence lacks.
function _adverseMediaSummary(am, digital, entityType) {
  am = (am && typeof am === 'object') ? am : {};
  digital = (digital && typeof digital === 'object') ? digital : {};
  let status = String(am.status || '').trim().toLowerCase();
  if (!status) status = am.ok ? 'completed' : '';
  const findings = Array.isArray(am.findings) ? am.findings : [];
  const mat = (am.materiality && typeof am.materiality === 'object') ? am.materiality : {};
  const credible = Number(mat.credible_count || 0);
  const reviewFindings = Array.isArray(am.findings_for_review) ? am.findings_for_review : [];
  const layerBroken = ['error', 'partial'].includes(
    String((digital.meta || {}).status || '').toLowerCase());

  if (!Object.keys(am).length) {
    if (String(entityType || '').trim().toLowerCase() === 'person') {
      return { severity: 'unknown',
        headline: 'Adverse media: media sweep not run for an individual',
        concern: 'The sanctions/PEP screen ran and is reported under Compliance, but no '
          + 'press, court or regulatory media search was performed on this individual.',
        advice: 'Commission a dedicated media search on this person before relying on the '
          + 'file. Do not read the sanctions/PEP result as adverse-media coverage.' };
    }
    return { severity: 'unknown', headline: 'Adverse media: NOT SCREENED',
      concern: 'No adverse-media screening is recorded on this report, so nothing is known either way.',
      advice: 'Do not treat this as clean. Re-run the DD, or commission a manual adverse-media search, before relying on the file.' };
  }
  if (status === 'in_progress' || status === 'running') {
    return { severity: 'unknown', headline: 'Adverse media: SCREENING UNFINISHED',
      concern: 'The screening had not completed when this report was rendered, so the absence of findings below is not evidence of absence.',
      advice: 'Re-open the report to pick up the completed sweep before making a decision. Until then treat adverse media as UNCHECKED.' };
  }
  if (['incomplete', 'failed', 'error'].includes(status) || am.error) {
    return { severity: 'unknown', headline: 'Adverse media: SCREENING DID NOT COMPLETE',
      concern: 'The sweep failed or was cut short, so coverage is unknown.',
      advice: 'Re-run the DD. Do not record this entity as adverse-media clear on the strength of this run.' };
  }
  if (credible || reviewFindings.length) {
    const n = credible || reviewFindings.length;
    return { severity: credible ? 'amber' : 'info',
      headline: `Adverse media: ${n} item(s) require review`,
      concern: `${n} subject-named item(s) survived de-duplication and filtering. They are listed with their sources below.`,
      advice: 'Open each cited source and judge it on its merits before relying on this file — the count alone is not a finding, and ARIA does not decide materiality on your behalf.' };
  }
  if (findings.length && !Object.keys(mat).length) {
    // R-F3084 — legacy raw output has not passed the materiality/relevance gate.
    // Calling it subject-named is an attribution claim the evidence does not earn.
    return { severity: 'unknown',
      headline: 'Adverse media: RAW SEARCH RESULTS REQUIRE FILTERING',
      concern: `${findings.length} raw search result(s) were stored, but subject attribution has not been verified and no persisted materiality decision shows which, if any, are adverse.`,
      advice: 'Do not treat the raw count as a finding or a clean result. Re-run the screening or review and classify every cited source.' };
  }
  return { severity: layerBroken ? 'unknown' : 'info',
    headline: layerBroken
      ? 'Adverse media: nothing found, but the digital layer did not complete'
      : 'Adverse media: nothing found in the sources searched',
    concern: 'No adverse coverage was returned by the sources that answered. This is an '
      + 'absence of COVERAGE, not proof of good standing.'
      + (layerBroken ? ' The digital layer did not complete, so coverage is narrower than intended.' : ''),
    advice: 'Check the data gaps for sources that did not answer. For a high-value counterparty, '
      + 'commission a native-language and offline media check — ARIA searches what is indexed and '
      + 'reachable, which is not everything.' };
}

function _adverseMediaLines(am, entityType) {
  if (!am || typeof am !== 'object' || !Object.keys(am).length) {
    // R-F3068 — for an INDIVIDUAL the deep media sweep is gated off by design, but a
    // sanctions/PEP screen DID run. "NOT RUN … UNCHECKED" overstates the gap.
    if (String(entityType || '').trim().toLowerCase() === 'person') {
      return [{ text: 'Adverse media: MEDIA SWEEP NOT RUN for an individual subject — the '
        + 'sanctions/PEP screen (which covers adverse-media watchlists) DID run and is '
        + 'reported under Compliance. A dedicated press/court media search on this '
        + 'individual has NOT been performed; commission one before relying on this file.' }];
    }
    return [{ text: 'Adverse media: NOT RUN — no adverse-media screening is recorded on '
      + 'this report. Treat as UNCHECKED, not as clean.' }];
  }
  let raw = String(am.status || '').trim().toLowerCase();
  if (!raw) raw = am.ok ? 'completed' : '';
  const label = _ADVERSE_STATUS_WORDS[raw] || (raw ? raw.toUpperCase() : 'UNKNOWN');
  const out = [{ text: `Adverse media screening: ${label}`, bold: true }];

  if (am.templates_searched != null) {
    out.push({ text: `Query templates actually searched: ${am.templates_searched}`
      + (am.templates_total_in_set ? ` of ${am.templates_total_in_set}` : '') });
  }
  if (am.search_backends_answered != null) {
    out.push({ text: 'Search backends answered: '
      + (am.search_backends_answered ? 'yes' : 'NO — the sweep could not observe the web') });
  }

  // R-F3516 — WHICH named sources stayed silent. Mirror of the Python block in
  // dd_schema._render_adverse_media; the PDF and the online view must not disagree
  // about how much of the source set was actually reached.
  const silent = Array.isArray(am.classes_silent) ? am.classes_silent : null;
  const answered = (am.classes_answered && typeof am.classes_answered === 'object')
    ? am.classes_answered : null;
  if (silent && silent.length) {
    out.push({ text: `Sources SEARCHED but SILENT (${silent.length}): `
      + silent.slice(0, 12).map(String).join(', ')
      + (silent.length > 12 ? ` … +${silent.length - 12} more` : '') });
    out.push({ text: 'These sources returned nothing attributable to them. That is '
      + 'negative evidence, NOT a clean screen of those sources — and any result '
      + 'listed against them below came from somewhere else.', indent: true });
  } else if (answered && !Object.keys(answered).length
             && am.classes_asked && Object.keys(am.classes_asked).length) {
    out.push({ text: 'NO named source class returned a result attributable to it — '
      + 'the results below came from other domains than the ones searched. '
      + 'Treat source coverage as UNESTABLISHED.' });
  }

  const rawFindings = Array.isArray(am.findings) ? am.findings : [];
  const mat = (am.materiality && typeof am.materiality === 'object') ? am.materiality : null;
  const reviewFindings = Array.isArray(am.findings_for_review) ? am.findings_for_review : [];
  let findingsToRender;
  if (mat) {
    out.push({ text: `Raw search results returned: ${rawFindings.length}` });
    out.push({ text: `After de-duplication and filtering: ${mat.credible_count || 0} credible `
      + `adverse item(s) from ${mat.raw_count || 0} raw hit(s) `
      + `(${mat.duplicates_dropped || 0} duplicate, ${mat.self_references_dropped || 0} `
      + `self-referential, ${mat.non_adverse_dropped || 0} non-adverse)` });
    out.push({ text: `${reviewFindings.length} item(s) require human review after filtering` });
    findingsToRender = reviewFindings;
  } else {
    out.push({ text: `Raw, unfiltered search results returned: ${rawFindings.length}` });
    findingsToRender = rawFindings;
  }
  for (const f of findingsToRender.slice(0, 8)) {
    if (!f || typeof f !== 'object') continue;
    const url = String(f.source_url || f.url || '').trim();
    const title = String(f.title || '').trim();
    // R-F3056 — the URL travels as a real link so a reviewer can open the source.
    const prefix = mat ? '' : '[RAW/UNFILTERED] ';
    out.push({ text: prefix + (title.slice(0, 100) || '(untitled item)'),
      url: url || '', indent: true });
  }
  if (findingsToRender.length > 8) {
    out.push({ text: `… and ${findingsToRender.length - 8} more`, indent: true });
  }

  if (raw === 'in_progress' || raw === 'running') {
    out.push({ text: 'This screening had NOT finished when the report was rendered — it is '
      + 'UNCHECKED, not clean. Re-open the report to pick up the result.', warn: true });
  } else if (!rawFindings.length) {
    // R-F3055 — keep these phrases CONTIGUOUS: the Python parity test greps this file
    // for the exact shared wording, so a split literal would let the two surfaces
    // drift apart while still "passing" by eye.
    out.push({ text: 'No adverse coverage found in the sources searched. That is an '
      + 'absence of COVERAGE, not proof of good standing — the sources that did not answer are listed in the data gaps.' });
  }
  return out;
}

function _fmtController(c, anchored) {
  if (!c || typeof c !== 'object') return '';
  const name = String(c.controller_name || '').trim();
  if (!name) return '';
  const nat = Array.isArray(c.natures_of_control) ? c.natures_of_control : [];
  const bits = [];
  if (nat.length) bits.push(nat.slice(0, 3).map((n) => String(n).replace(/-/g, ' ')).join('; '));
  if (anchored && c.controller_registration_number) bits.push('reg ' + c.controller_registration_number);
  if (!anchored) bits.push('NO registration number at Companies House — chain above it NOT walked');
  return name + (bits.length ? ' — ' + bits.join(', ') : '');
}

// Priority-ordered layer plan: highest-signal first. Each entry names the layer
// and the scalar fields worth surfacing (nested structures are summarised as
// counts, never dumped). This is DATA, so the ordering and field choices are
// testable without rendering a PDF.
const _DD_LAYER_PLAN = [
  // R-F3049 — every field the ONLINE view surfaces must be reachable here too,
  // or the two renderings of one report disagree about what the report contains.
  // Added: ghost_score (online shows "0/28 GREEN"), financial_health (online shows
  // "Financial health: STRONG"), previous_names + lei_registration (R-F3024/3021),
  // controlled_by_unanchored (R-F3027).
  ['identity', 'Identity',
    ['registration_status', 'incorporation_date', 'registered_address', 'declared_activity',
     'ghost_score']],
  ['compliance', 'Compliance and sanctions',
    ['country_risk', 'export_control', 'financial_health', 'sanctions_regimes', 'licence_path']],
  ['network', 'Ownership and control network',
    ['controlled_by', 'controlled_by_unanchored', 'ubo_chain', 'pep_connections',
     'sanctions_network', 'cross_linked_entities']],
  ['verification', 'Verification',
    ['grounded_rate', 'unverified_claim_count', 'independent_corroboration_rate', 'conflicts']],
  ['digital', 'Digital footprint',
    ['press_coverage', 'source_tier_breakdown', 'procurement_history', 'people',
     'exhibition_presence']],
  ['commercial_coherence', 'Commercial coherence',
    ['coherence_score', 'tier', 'anomalies', 'jurisdiction_flags', 'licence_chain_gaps']],
  ['sweep_data', 'Intelligence sweep',
    ['relevant_news', 'sanctions_updates', 'procurement_alerts', 'trade_signals']],
];

/**
 * PURE selection of what the DD PDF renders, in priority order (R-F2848).
 *
 * Exported and side-effect-free so the SELECTION and HONESTY rules can be tested
 * directly, without parsing a rendered PDF (pdfkit compresses its streams, so
 * asserting on output bytes is a proxy, not the property). The renderer below
 * just draws what this returns.
 *
 * Rules, all enforced here and asserted by test:
 *   - a layer with no status, facts or findings is OMITTED (nothing invented);
 *   - a layer's `meta.error` is carried through, so an errored layer can never
 *     render as a clean result;
 *   - findings are severity-sorted (red → amber → info) but none are dropped;
 *   - no verdict is computed and no severity is inferred.
 */
export function ddReportSections(report = {}) {
  const out = [];

  for (const [key, title, facts] of _DD_LAYER_PLAN) {
    const layer = report[key];
    if (!layer || typeof layer !== 'object') continue;
    const meta = layer.meta || {};
    const status = String(meta.status || '').toLowerCase();
    const allFindings = Array.isArray(layer.findings) ? layer.findings.filter(Boolean) : [];
    // R-F3098 — split ENVIRONMENT-level items out of the decision-driving list.
    // On the live Mitie report "Sovereign macro context: central-govt debt 130.7% of
    // GDP" — whose own detail ends "not a finding against this entity" — and the
    // USAspending award total printed inline under "Compliance and sanctions", where
    // POSITION asserted what the wording denied. Nothing is dropped: they render
    // under their own heading, after the findings that are about the subject.
    const findings = allFindings.filter(f => !f.context_only);
    const contextFindings = allFindings.filter(f => f.context_only);
    // R-F3049 — format HERE, in the pure selection function, so what the PDF will
    // SAY about each fact is unit-testable without rendering a PDF (the R-F2848
    // principle: the renderer computes nothing). Previously the pairs stayed raw
    // and were flattened at draw time, which is why "(present)" and "N items"
    // survived unnoticed while the online view showed real values.
    // A SKIPPED layer's scalar fields are dataclass DEFAULTS, not measurements —
    // and every default is the reassuring value. dd_schema.CommercialCoherenceSection
    // declares `coherence_score: float = 1.0` and `tier: str = "GREEN"`, so a layer
    // that never ran is byte-identical to one that ran and found nothing wrong.
    //
    // Measured on delivered run dd_29368fbb8b3d (2026-08-03), which printed:
    //     COMMERCIAL COHERENCE
    //     SKIPPED
    //     Coherence Score 1
    //     Tier GREEN
    // A check that did not execute, rendered as a perfect score. That is the
    // absence-laundered-into-evidence failure this report is careful about
    // everywhere else — cert-transparency says "UNCHECKED, not a clean 0/100";
    // adverse media says "absence of COVERAGE, not proof of good standing". The
    // prose was right and the scalars contradicted it.
    //
    // Withhold ONLY the invented scalars. Status and findings still render, so a
    // skipped layer is still visible as skipped — the reader loses a fabricated
    // number, not the fact that the layer exists.
    const _skipped = ['skipped', 'not_run', 'not_started'].includes(status);
    const factPairs = _skipped ? [] : facts
      .map((k) => [k, _fmtFactValue(k, layer[k])])
      .filter(([, v]) => v != null && v !== '');
    // R-F2998 — name the actual sanctions/watchlists screened + a screening date in
    // the Compliance section. The per-list HIT/CLEAN/UNAVAILABLE breakdown already
    // exists on the screen (identity.sanctions_screen.verified_sources) but was never
    // rendered — a "Compliance and Sanctions" section that lists no lists and no date
    // is exactly the gap the DD reviewer flagged.
    //
    // R-F3019 — THIS NEVER RENDERED. The gate was `Array.isArray(verified_sources)`,
    // but `_sanctions_classify.derive_verified_sources()` returns a DICT keyed by
    // list name (`{"OFAC SDN": {label, status, ...}, ...}`) — the exact shape trap
    // already documented at dd_orchestrator.py:3373 (R-F2590). `Array.isArray({})`
    // is false, so the branch never ran and every PDF shipped with the lists
    // silently absent while the code claimed to print them. Now both shapes are
    // accepted, and the dict key is the fallback label so a list can never render
    // as '?'.
    let sanctionsSources = [];
    let sanctionsDate = '';
    if (key === 'compliance') {
      const screen = (report.identity && report.identity.sanctions_screen) || {};
      const vs = screen.verified_sources;
      if (Array.isArray(vs)) {
        sanctionsSources = vs.filter(Boolean);
      } else if (vs && typeof vs === 'object') {
        sanctionsSources = Object.entries(vs)
          .filter(([, v]) => v && typeof v === 'object')
          .map(([name, v]) => ({ name, label: v.label || name, status: v.status }));
      }
      if (sanctionsSources.length) {
        // R-F3019 — `screened_at` is stamped by sanctions.py at screen time.
        // generated_at is a REPORT timestamp, not a screening one; keep it only
        // as a last resort so the date is never silently absent.
        sanctionsDate = screen.screened_at || report.generated_at || '';
      }
    }
    // ── R-F3026 — NAME the people. `identity.directors` and `identity.shareholders`
    // are fully populated (officer_id, person_number, appointment dates, natures of
    // control) and this generator had NO code path for either — a grep for
    // directors|officers matched one unrelated comment. Meanwhile the readiness
    // scorecard printed on page 1 of this same PDF claims its identity evidence is
    // "live registry status plus number and DIRECTORS/incorporation". The PDF
    // asserted directors as evidence and never showed one.
    let people = [];
    if (key === 'identity') {
      const ident = (report.identity && typeof report.identity === 'object') ? report.identity : {};
      const dirs = Array.isArray(ident.directors) ? ident.directors : [];
      const pscs = Array.isArray(ident.shareholders) ? ident.shareholders : [];
      const prev = Array.isArray(ident.previous_names) ? ident.previous_names : [];
      if (prev.length) {
        people.push({ heading: 'Former names (Companies House)', items: prev.slice(0, 5).map(_fmtPrevName) });
      }
      if (dirs.length) {
        people.push({ heading: `Directors / officers (${dirs.length})`, items: dirs.slice(0, 25).map(_fmtOfficer) });
      }
      if (pscs.length) {
        people.push({ heading: `Persons with significant control (${pscs.length})`, items: pscs.slice(0, 25).map(_fmtPsc) });
      }
      people = people.filter((g) => g.items.some(Boolean));
    }
    if (key === 'network') {
      const net = (report.network && typeof report.network === 'object') ? report.network : {};
      const un = Array.isArray(net.controlled_by_unanchored) ? net.controlled_by_unanchored : [];
      const an = Array.isArray(net.controlled_by) ? net.controlled_by : [];
      if (an.length) {
        people.push({ heading: 'Controllers (registry-anchored)',
          items: an.slice(0, 10).map((c) => _fmtController(c, true)) });
      }
      if (un.length) {
        // R-F3027 — a 75-100% controller that no surface renders is the same
        // failure as one that was never found.
        people.push({ heading: 'Controllers disclosed but NOT traversed',
          items: un.slice(0, 10).map((c) => _fmtController(c, false)) });
      }
      people = people.filter((g) => g.items.some(Boolean));
    }
    // R-F3049 — the online view lists the cited press URLs as evidence; the PDF
    // printed only "Press Coverage 8 items". A URL a reader can open is the whole
    // difference between a citation and a claim, and R-F1592 already established
    // that dropping them makes downstream grounding read 0.
    let evidence = [];
    let adverseMedia = [];
    if (key === 'digital') {
      const press = Array.isArray(layer.press_coverage) ? layer.press_coverage : [];
      evidence = press
        .filter((p) => p && typeof p === 'object' && p.url)
        .slice(0, 15)
        .map((p) => ({
          url: String(p.url),
          source: String(p.source || ''),
          tier: String(p.source_tier || ''),
        }));
      // R-F3055 — ADVERSE MEDIA. `adverse_media` is a TOP-LEVEL report key, not a
      // layer, so _DD_LAYER_PLAN never reached it and the downloaded PDF showed no
      // adverse-media section at all (operator-reported). Mirrors the Python
      // `_render_adverse_media` contract so the PDF and the online view say the
      // same thing — including when the sweep has NOT finished, which is the state
      // every completed report was actually in. An absent section reads as "nothing
      // adverse found"; that is the false clean this product exists to prevent.
      // R-F3060 — lead with the decision-ready summary (concern + advice), then
      // the detail. A reader should not have to assemble the so-what from a status,
      // a count and a filter arithmetic.
      const _entType = ((report.identity || {}).entity_type) || '';   // R-F3068
      const _amSum = _adverseMediaSummary(report.adverse_media, layer, _entType);
      adverseMedia = [
        { text: _amSum.headline, bold: true, warn: _amSum.severity !== 'info' },
        { text: 'Concern: ' + _amSum.concern },
        { text: 'Advice: ' + _amSum.advice },
        ..._adverseMediaLines(report.adverse_media, _entType),
      ];
    }
    if (!factPairs.length && !findings.length && !contextFindings.length && !status
        && !sanctionsSources.length && !people.length && !evidence.length
        && !adverseMedia.length) continue;
    out.push({
      title, status, error: meta.error ? String(meta.error) : '',
      facts: factPairs,
      findings: [...findings].sort((a, b) => _sevRank(a.severity) - _sevRank(b.severity)),
      // R-F3098 — grouped by kind, after the subject findings. Never severity-sorted
      // with them: mixing a country statistic into a severity ranking is precisely
      // the conflation this split exists to end.
      contextFindings: [...contextFindings].sort((a, b) =>
        String(a.context_kind || '').localeCompare(String(b.context_kind || ''))
        || String(a.title || '').localeCompare(String(b.title || ''))),
      sanctionsSources, sanctionsDate, people, evidence, adverseMedia,
    });
  }

  // ── R-F4223 / C-203 — the summary must not reprint the layer ──────────────
  //
  // `synthesis.key_findings` is a SUMMARY VIEW of findings that also live in
  // their layer. dd_orchestrator._rollup_key_findings says so outright: "this
  // re-orders a 10-item view of a list that stays complete in its own section".
  // Pushing the view as a section AND every layer's full list printed each key
  // finding's body twice — nine of them, verbatim, across pages 2 and 3 of the
  // delivered Penfold report, adding a page to a customer-facing document.
  //
  // The layer stays canonical and COMPLETE (that is the contract above). Only
  // the duplicated BODY is suppressed from the summary, and only when the
  // finding genuinely appears in a layer — a key finding with no layer home
  // keeps its detail, because suppressing it would delete the only copy.
  // Pure: the projection copies, it never edits the caller's objects.
  const kf = Array.isArray((report.synthesis || {}).key_findings)
    ? report.synthesis.key_findings.filter(Boolean) : [];
  if (kf.length) {
    const rendered = new Set();
    for (const sec of out) {
      for (const f of [...(sec.findings || []), ...(sec.contextFindings || [])]) {
        rendered.add(_findingKey(f));
      }
    }
    out.unshift({
      title: 'Key findings', status: '', error: '', facts: [], summary: true,
      findings: [...kf]
        .sort((a, b) => _sevRank(a.severity) - _sevRank(b.severity))
        .map((f) => (rendered.has(_findingKey(f))
          ? { ...f, detail: '', description: '', summary: '', detail_suppressed: true }
          : { ...f })),
    });
  }
  return out;
}

// R-F4223 — identity of a finding for duplicate detection. Title plus source,
// because two checks can legitimately reach the same conclusion from different
// registers and both deserve to be shown; the same check saying the same thing
// twice is the duplication.
function _findingKey(f) {
  if (!f || typeof f !== 'object') return '';
  const src = f.source || (Array.isArray(f.sources) ? f.sources.join(',') : '');
  // JSON, not a delimiter: a separator character can appear inside a title,
  // and an escape written through a code generator is one mangling away from
  // a literal control byte in source (it was, once).
  return JSON.stringify([String(f.title || f.claim || f.name || '').trim(),
                         String(src).trim()]);
}

// Draw one section produced by ddReportSections().
function addReportSection(doc, sec) {
  addSection(doc, sec.title);
  if (sec.status) {
    // R-F3005 — 'OK' read as a quality judgement while the evidence grade was D.
    // 'COMPLETED' in neutral ink states only that the layer ran to completion; an
    // errored layer still renders red, a partial/other status in orange.
    const label = sec.status === 'ok' ? 'COMPLETED' : sec.status.toUpperCase();
    const col = sec.status === 'error' ? RED : (sec.status === 'ok' ? INK : ORANGE);
    doc.font('Helvetica-Bold').fontSize(8).fillColor(col)
      .text(label, MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.2);
    if (sec.error) {
      doc.font('Helvetica').fontSize(8.5).fillColor(RED)
        .text(_pdfSafe('Layer error: ' + sec.error), MARGIN, doc.y, { width: CONTENT_W });
      doc.moveDown(0.2);
    }
    doc.fillColor(INK);
  }
  for (const [k, v] of sec.facts) {
    // R-F3049 — use the ONLINE view's wording where it differs from a naive
    // title-case of the field name, so the two renderings read identically.
    // "Ubo Chain" also mis-sold what that list is: on a GB company it is the
    // walker's traversed NODES, which include officers - naming them (as the PDF
    // now does) makes an honest label mandatory rather than cosmetic.
    const label = _FACT_LABELS[k]
      || k.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
    // R-F3049 — v is ALREADY the display string (formatted in ddReportSections);
    // this function draws, it does not decide.
    addKeyValue(doc, label, String(v));
  }
  // R-F2998 — name the actual lists screened + the screening date (the DD reviewer's
  // "a Compliance and Sanctions section needs to name the lists screened and the date").
  if (Array.isArray(sec.sanctionsSources) && sec.sanctionsSources.length) {
    doc.moveDown(0.2);
    const dateStr = sec.sanctionsDate
      ? ' (screened as of ' + String(sec.sanctionsDate).slice(0, 10) + ')' : '';
    doc.font('Helvetica-Bold').fontSize(8.5).fillColor(INK)
      .text(_pdfSafe('Sanctions & watchlists screened' + dateStr + ':'), MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.15);
    for (const s of sec.sanctionsSources) {
      const lbl = _pdfSafe(String(s.label || s.name || '?'));
      const st = String(s.status || 'UNKNOWN').toUpperCase();
      const scol = st === 'HIT' ? RED : (st === 'CLEAN' ? GREEN : ORANGE);
      doc.font('Helvetica').fontSize(8.5).fillColor(scol)
        .text('  • ' + lbl + ' — ' + st, MARGIN, doc.y, { width: CONTENT_W });
      doc.moveDown(0.06);
    }
    doc.fillColor(INK);
  }
  // R-F3026/R-F3027 — named people and controllers, drawn from ddReportSections().
  for (const group of (Array.isArray(sec.people) ? sec.people : [])) {
    doc.moveDown(0.2);
    doc.font('Helvetica-Bold').fontSize(8.5).fillColor(INK)
      .text(_pdfSafe(String(group.heading || '')), MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.15);
    for (const item of (group.items || [])) {
      if (!item) continue;
      doc.font('Helvetica').fontSize(8.5).fillColor(INK)
        .text(_pdfSafe('  • ' + item), MARGIN, doc.y, { width: CONTENT_W });
      doc.moveDown(0.06);
    }
  }
  // R-F3055 — the adverse-media screening block (was absent from the PDF entirely).
  if (Array.isArray(sec.adverseMedia) && sec.adverseMedia.length) {
    doc.moveDown(0.25);
    for (const line of sec.adverseMedia) {
      const col = line.warn ? ORANGE : INK;
      if (line.url) {
        _linkLine(doc, line.text, line.url, { indent: true });
      } else {
        doc.font(line.bold ? 'Helvetica-Bold' : 'Helvetica').fontSize(8.5).fillColor(col)
          .text(_pdfSafe((line.indent ? '    - ' : line.bold ? '' : '  • ') + line.text),
            MARGIN, doc.y, { width: CONTENT_W });
      }
      doc.moveDown(0.08);
    }
    doc.fillColor(INK);
  }
  // R-F3049 — cited sources, with their tier, exactly as the online view lists them.
  // R-F3056 — rendered as CLICKABLE hyperlinks: ARIA's USP is that every claim is
  // checkable at its primary source, and a URL a reader has to retype is not.
  if (Array.isArray(sec.evidence) && sec.evidence.length) {
    doc.moveDown(0.2);
    doc.font('Helvetica-Bold').fontSize(8.5).fillColor(INK)
      .text(_pdfSafe(`Cited sources (${sec.evidence.length})`), MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.15);
    for (const e of sec.evidence) {
      const prefix = [e.source, e.tier ? `[${e.tier}]` : ''].filter(Boolean).join(' ');
      _linkLine(doc, (prefix ? prefix + ' — ' : '') + e.url, e.url);
      doc.moveDown(0.06);
    }
  }
  if (sec.findings.length) {
    doc.moveDown(0.2);
    for (const f of sec.findings) addFinding(doc, f);
  }
  // R-F3098 — environment-level items, LAST and under their own heading, so the
  // decision-driving list above contains only material about the subject.
  const ctx = Array.isArray(sec.contextFindings) ? sec.contextFindings : [];
  if (ctx.length) {
    const kinds = [...new Set(ctx.map(f => f.context_kind).filter(Boolean))];
    doc.moveDown(0.35);
    _ensureSpace(doc, 30);
    doc.font('Helvetica-Bold').fontSize(8).fillColor(MUTE)
      .text(_pdfSafe((kinds.join(' / ') || 'Context').toUpperCase()
        + ' - about the environment, not about this entity'),
        MARGIN, doc.y, { width: CONTENT_W });
    doc.moveDown(0.2);
    doc.fillColor(INK);
    for (const f of ctx) addFinding(doc, f);
  }
}

export function generateDueDiligencePDF(report = {}, metadata = {}) {
  return new Promise((resolve, reject) => {
    try {
      const doc = new PDFDocument({ size: 'A4', margin: MARGIN, bufferPages: true });
      const chunks = [];
      doc.on('data', c => chunks.push(c));
      doc.on('end', () => resolve(Buffer.concat(chunks)));

      const target = report.target || {};
      const identity = report.identity || {};
      const name = target.name || target.entity || identity.entity_name || 'Unknown entity';
      const runId = report.run_id || '-';
      const generatedAt = report.generated_at || new Date().toISOString();
      const readiness = report.decision_readiness || {};

      addHeader(doc, 'Due Diligence - ' + name,
        'Run ' + runId + '  |  Generated ' + String(generatedAt).slice(0, 16).replace('T', ' ') + ' UTC',
        'CONFIDENTIAL');

      addVerdictBlock(doc, report.risk_classification, readiness);

      addSection(doc, 'Subject');
      addKeyValue(doc, 'Entity', name);
      addKeyValue(doc, 'Type', _fmtVal(target.type));
      addKeyValue(doc, 'Jurisdiction', _fmtVal(target.jurisdiction || target.jurisdiction_iso2));
      addKeyValue(doc, 'Registration number',
        _fmtVal(identity.registration_number || target.registration_number));
      addKeyValue(doc, 'Canonical ID', _fmtVal(report.canonical_entity_id));
      addKeyValue(doc, 'Report reference', _fmtVal(metadata.docRef || runId));

      if (report.bottom_line || report.recommendation) {
        addSection(doc, 'Assessment');
        if (report.bottom_line) addParagraph(doc, report.bottom_line);
        if (report.recommendation) {
          doc.font('Helvetica-Bold').fontSize(9.5).fillColor(INK)
            .text('Recommendation', MARGIN, doc.y, { width: CONTENT_W });
          doc.moveDown(0.2);
          addParagraph(doc, report.recommendation);
        }
      }

      // R-F3091 — entity scope, mirrored from the online view. Read from the ONE
      // persisted object (`entity_scope`, computed by dd_schema._dd_entity_scope) so
      // the two surfaces cannot disagree — the R-F3055 lesson. Absent on reports
      // written before R-F3091, in which case nothing is printed.
      addEntityScope(doc, report.entity_scope);

      addReadinessScorecard(doc, readiness);

      // R-F3029 — the PDF printed only the readiness blockers, one of which is
      // "evidence grade D does not meet the Grade A reliance threshold" — a
      // tautology: it restates the grade instead of saying what is missing. The
      // real, actionable reasons live in `quality_assessment.blocking_reasons`
      // (e.g. "only 5 cited sources, need 8"; "citation grounding 0%") and the PDF
      // never read that object at all. Both are printed now, the specific ones
      // nested under the grade statement they explain.
      const blocking = Array.isArray(readiness.blocking_reasons) ? readiness.blocking_reasons : [];
      const qa = (report.quality_assessment && typeof report.quality_assessment === 'object')
        ? report.quality_assessment : {};
      const qaBlocking = Array.isArray(qa.blocking_reasons) ? qa.blocking_reasons.filter(Boolean) : [];
      if (blocking.length || qaBlocking.length) {
        addSection(doc, 'Outstanding before reliance');
        for (const b of blocking) {
          addBullet(doc, b);
          // expand the circular grade blocker with the evidence-depth reasons
          if (qaBlocking.length && /evidence grade/i.test(String(b))) {
            for (const q of qaBlocking) addBullet(doc, '   – ' + q);
          }
        }
        if (qaBlocking.length && !blocking.some((b) => /evidence grade/i.test(String(b)))) {
          for (const q of qaBlocking) addBullet(doc, q);
        }
      }

      // R-F2848 — full findings, priority-ordered. Selection is a pure function
      // (ddReportSections) so its ordering + honesty rules are unit-tested; here
      // we only draw what it returns.
      for (const sec of ddReportSections(report)) addReportSection(doc, sec);

      // Coverage — the absence of a layer is itself information.
      addSection(doc, 'Coverage');
      addKeyValue(doc, 'Layers run', _fmtVal(report.layers_run));
      addKeyValue(doc, 'Layers skipped', _fmtVal(report.layers_skipped));
      addKeyValue(doc, 'Confidence', _fmtVal(report.confidence_tag));
      if (report.confidence_gate_triggered) {
        addKeyValue(doc, 'Confidence gate', _fmtVal(report.confidence_gate_reasons));
      }

      addDataGaps(doc, report.data_gaps_summary);

      const next = Array.isArray(report.next_actions) ? report.next_actions.filter(Boolean) : [];
      if (next.length) {
        addSection(doc, 'Recommended next steps');
        for (const n of next) {
          addBullet(doc, typeof n === 'string' ? n : _fmtVal(n && (n.action || n.title)));
        }
      }

      addDivider(doc);
      addParagraph(doc,
        'Produced by ARIA. This document reports only what was evidenced at the time of the '
        + 'run. Where a check did not complete, that is stated explicitly rather than '
        + 'presented as a clear result.');

      addFooter(doc);
      doc.end();
    } catch (e) {
      reject(e);
    }
  });
}
