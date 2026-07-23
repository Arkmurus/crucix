// lib/telegram/channelMedia.mjs
//
// Telegram Channel Media Engine — R-F2288
// =========================================
// Generates rich media content for the broadcast channel:
//   1. SVG infographics (cards, scoreboards, comparison charts)
//   2. Threaded posts (multi-message threads with visual continuity)
//   3. Rich cards (title + description + image + link previews)
//   4. Polls (interactive audience engagement)
//   5. Media attachments (photo, document upload via Telegram API)
//
// All media is generated server-side as SVG/HTML and rendered via
// Telegram's sendPhoto / sendDocument APIs. No external image service.

import { createHash, randomUUID } from 'node:crypto';
import fs from 'node:fs';   // R-F2903 — font-availability probe (see _fontsAvailable)

// ── Constants ──────────────────────────────────────────────────────────────────

const TELEGRAM_API = 'https://api.telegram.org';

// ── SVG Infographic Templates ──────────────────────────────────────────────────

/**
 * Generate an SVG infographic card for a channel post.
 *
 * Produces a 1200x630px card (Open Graph standard) with:
 *   - Gradient header bar with content type colour
 *   - Title text (auto-scaled)
 *   - Key metrics or bullet points
 *   - Source + timestamp footer
 *   - ARIA Intelligence branding
 *
 * @param {object} data — Card content.
 * @param {string} data.title — Card title.
 * @param {string} [data.subtitle] — Subtitle or summary.
 * @param {Array<{label:string,value:string}>} [data.metrics] — Key metrics.
 * @param {string[]} [data.bullets] — Bullet points.
 * @param {string} [data.source] — Source attribution.
 * @param {string} [data.type='intel'] — Content type for colour scheme.
 * @returns {string} SVG markup as string.
 */
export function buildIntelCardData(data = {}) {
  const title = String(data.title || data.decision_summary || data.summary || 'ARIA Intelligence').trim();
  const subtitle = String(data.subtitle || data.text || data.description || data.recommended_action || '').trim();
  const type = data.type || _inferType(title, subtitle, data);
  const source = data.source || data.publisher || data.channel || 'ARIA Intelligence';
  const metrics = Array.isArray(data.metrics) && data.metrics.length
    ? data.metrics.slice(0, 3).map(m => ({
        label: String(m.label || '').slice(0, 32),
        value: String(m.value || '').slice(0, 28),
      })).filter(m => m.label || m.value)
    : _deriveMetrics(data);
  const bullets = Array.isArray(data.bullets) && data.bullets.length
    ? data.bullets
    : _deriveBullets(data);

  return {
    title,
    subtitle,
    metrics,
    bullets: bullets.map(b => String(b || '').trim()).filter(Boolean).slice(0, 3),
    source,
    type,
    // R-F2903 — EVIDENCE fields. The card previously showed a headline, a summary and
    // some bullets: visually fine, and indistinguishable from any newsletter graphic.
    // ARIA's differentiator is not that it has an opinion, it is that every claim is
    // anchored to a primary source and carries an honest strength label. If the card
    // does not show the grade, the corroboration state and the source, it is selling
    // the wrong product.
    //
    // Each is rendered ONLY when present — nothing is defaulted, invented or rounded
    // up. A missing evidence URL renders no evidence line rather than a plausible one.
    grade: String(data.grade || data.intel_grade || '').toUpperCase().slice(0, 1),
    corroboration: String(data.corroboration || '').trim(),
    evidenceUrl: String(data.evidenceUrl || data.url || '').trim(),
    detectedAt: String(data.detectedAt || data.detected_at || data.published || '').trim(),
    target: String(data.target || '').trim(),
    action: String(data.action || data.recommended_action || '').trim(),
  };
}

/**
 * R-F2903 — the honest label for a publication grade. Never upgrades a grade and
 * never implies confirmation that was not established.
 */
function _gradeLabel(grade, corroboration) {
  const corroborated = /corroborat/i.test(corroboration);
  if (grade === 'A') {
    return corroborated
      ? 'GRADE A · INDEPENDENTLY CORROBORATED'
      : 'GRADE A · OFFICIAL PRIMARY SOURCE';
  }
  if (grade === 'B') return 'GRADE B · SINGLE SOURCE · CORROBORATION PENDING';
  return '';
}

/** R-F2903 — slice with a visible truncation marker. A clipped string that looks
 * complete is a small lie; "…" says the value continues. */
function _ellipsize(text, max) {
  const t = String(text || '');
  return t.length > max ? `${t.slice(0, Math.max(1, max - 1))}…` : t;
}

/** Trim a URL for display without misrepresenting it (host + truncated path). */
function _displayUrl(url) {
  try {
    const u = new URL(url);
    const path = u.pathname.length > 28 ? `${u.pathname.slice(0, 27)}…` : u.pathname;
    return `${u.host}${path === '/' ? '' : path}`;
  } catch {
    return String(url || '').slice(0, 52);
  }
}

export function generateInfographicCard(data) {
  const {
    title,
    subtitle,
    metrics,
    bullets,
    source,
    type,
    grade,
    corroboration,
    evidenceUrl,
    detectedAt,
    target,
    action,
  } = buildIntelCardData(data || {});
  // R-F2903 — the badge states the EVIDENCE grade, not a content category. A reader
  // must be able to tell, at a glance and without opening anything, how strong this
  // is. Falls back to the category label only when no grade was supplied.
  const gradeLabel = _gradeLabel(grade, corroboration);
  const evidenceHost = evidenceUrl ? _displayUrl(evidenceUrl) : '';
  const detectedShort = detectedAt ? String(detectedAt).slice(0, 16).replace('T', ' ') : '';

  const colours = _typeColours(type);
  const w = 1200;
  const h = 630;
  const titleSize = title.length > 76 ? 34 : title.length > 42 ? 40 : 48;
  const shortSubtitle = subtitle.replace(/\s+/g, ' ').slice(0, 190);
  // R-F2903 — titles may run to 4 lines. The TED notice titles are long by nature
  // (country – CPV category – native-language description) and losing the tail makes
  // the item harder to identify, which defeats the point of naming the source.
  const titleLines = _svgLines(title, 48, 96, titleSize + 8, _charsForWidth(612, titleSize), 4);
  // R-F2903 — WHY IT MATTERS was reading bullets[0], which the channel caller sets to
  // recommended_action, so both right-hand panels rendered the SAME sentence and the
  // actual "why" never appeared. The subtitle IS why_it_matters — use it first.
  const impact = shortSubtitle || bullets[0] || 'New intelligence item requires review.';
  // Do not repeat the why-text as a left-hand block when the panel already shows it.
  const subtitleLines = (shortSubtitle && shortSubtitle !== impact)
    ? _svgLines(shortSubtitle, 48, 240, 31, _charsForWidth(612, 23))
    : '';
  // R-F2903 — prefer the signal's OWN recommended_action; the bullet fallback was a
  // generic instruction that read as analysis but was written by the template.
  const nextCheck = action || bullets[1] || bullets[2] || 'Screen the named entity, address, directors and source trail before acting.';

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#111827;stop-opacity:1" />
      <stop offset="52%" style="stop-color:${colours.bgStart};stop-opacity:1" />
      <stop offset="100%" style="stop-color:#050505;stop-opacity:1" />
    </linearGradient>
    <linearGradient id="accent" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:${colours.accent};stop-opacity:1" />
      <stop offset="100%" style="stop-color:${colours.accentEnd || colours.accent};stop-opacity:1" />
    </linearGradient>
    <radialGradient id="glow" cx="18%" cy="12%" r="80%">
      <stop offset="0%" style="stop-color:${colours.accent};stop-opacity:0.22" />
      <stop offset="58%" style="stop-color:${colours.accent};stop-opacity:0.02" />
      <stop offset="100%" style="stop-color:#000000;stop-opacity:0" />
    </radialGradient>
    <filter id="shadow" x="-8%" y="-8%" width="116%" height="116%">
      <feDropShadow dx="0" dy="12" stdDeviation="16" flood-color="#000000" flood-opacity="0.28"/>
    </filter>
  </defs>

  <rect width="${w}" height="${h}" fill="url(#bg)" rx="18"/>
  <rect width="${w}" height="${h}" fill="url(#glow)" rx="18"/>
  <rect x="0" y="0" width="12" height="${h}" fill="url(#accent)"/>
  <path d="M860 0 L1200 0 L1200 630 L1000 630 C1080 470 1078 290 960 160 C925 122 892 70 860 0 Z" fill="${colours.accent}" opacity="0.09"/>

  <g filter="url(#shadow)">
    ${(() => {
      const badge = gradeLabel || colours.label;
      const bw = Math.min(560, _textWidth(badge, 14) + 58);
      return `<rect x="42" y="34" width="${bw}" height="38" rx="19" fill="#ffffff" opacity="0.08"/>
    <rect x="42" y="34" width="${bw}" height="38" rx="19" fill="${colours.accent}" opacity="0.14"/>
    <text x="66" y="58" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="14" font-weight="800" letter-spacing="0" fill="${colours.accent}">${_escapeXml(badge)}</text>`;
    })()}
  </g>
  ${target ? `<text x="48" y="342" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="700" letter-spacing="0" fill="#9ca3af">TARGET</text>
  <text x="48" y="374" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="19" font-weight="700" letter-spacing="0" fill="#f3f4f6">${_escapeXml(_ellipsize(target, _charsForWidth(612, 19)))}</text>` : ''}

  <text x="48" y="112" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="${titleSize}" font-weight="850" letter-spacing="0" fill="#ffffff">${titleLines}</text>
  ${subtitleLines ? `<text x="48" y="240" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="23" font-weight="500" letter-spacing="0" fill="#d1d5db">${subtitleLines}</text>` : ''}

  <g>
    ${metrics.slice(0, 3).map((m, i) => {
      const mx = 48 + (i * 220);
      return `
    <rect x="${mx}" y="334" width="196" height="88" rx="8" fill="#ffffff" opacity="0.08"/>
    <rect x="${mx}" y="334" width="196" height="2" fill="${colours.accent}" opacity="0.85"/>
    <text x="${mx + 18}" y="374" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="29" font-weight="850" letter-spacing="0" fill="#ffffff">${_escapeXml(m.value)}</text>
    <text x="${mx + 18}" y="400" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="12" font-weight="700" letter-spacing="0" fill="#9ca3af">${_escapeXml(m.label)}</text>`;
    }).join('')}
  </g>

  <g>
    <rect x="720" y="92" width="420" height="180" rx="8" fill="#ffffff" opacity="0.08"/>
    <text x="748" y="128" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="850" letter-spacing="0" fill="${colours.accent}">WHY IT MATTERS</text>
    <text x="748" y="164" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="20" font-weight="600" letter-spacing="0" fill="#f3f4f6">${_svgLines(impact, 748, 164, 27, _charsForWidth(372, 20), 4)}</text>

    <rect x="720" y="292" width="420" height="140" rx="8" fill="#ffffff" opacity="0.07"/>
    <text x="748" y="328" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="850" letter-spacing="0" fill="${colours.accent}">RECOMMENDED ACTION</text>
    <text x="748" y="364" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="20" font-weight="600" letter-spacing="0" fill="#f3f4f6">${_svgLines(nextCheck, 748, 364, 27, _charsForWidth(372, 20))}</text>
  </g>

  ${!target ? `<g>
    ${bullets.slice(0, 3).map((b, i) => `
    <circle cx="61" cy="${466 + (i * 31)}" r="4" fill="${colours.accent}"/>
    <text x="78" y="${472 + (i * 31)}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="17" font-weight="600" letter-spacing="0" fill="#d1d5db">${_escapeXml(String(b).replace(/\s+/g, ' ').slice(0, 86))}</text>`).join('')}
  </g>` : ''}

  ${evidenceHost ? `
  <g>
    <rect x="720" y="${h - 178}" width="432" height="86" rx="8" fill="${colours.accent}" opacity="0.10"/>
    <rect x="720" y="${h - 178}" width="4" height="86" fill="${colours.accent}" opacity="0.9"/>
    <text x="744" y="${h - 150}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="12" font-weight="850" letter-spacing="0" fill="${colours.accent}">EVIDENCE · PRIMARY SOURCE</text>
    <text x="744" y="${h - 126}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="15" font-weight="700" letter-spacing="0" fill="#f3f4f6">${_escapeXml(evidenceHost)}</text>
    ${detectedShort ? `<text x="744" y="${h - 104}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="600" letter-spacing="0" fill="#9ca3af">Detected ${_escapeXml(detectedShort)}${corroboration ? ` · ${_escapeXml(corroboration)}` : ''}</text>` : ''}
  </g>` : ''}

  <line x1="48" y1="${h - 76}" x2="${w - 48}" y2="${h - 76}" stroke="#ffffff" stroke-opacity="0.13"/>
  <text x="48" y="${h - 44}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="700" letter-spacing="0" fill="#9ca3af">${_escapeXml(source)}</text>
  <text x="${w - 48}" y="${h - 44}" font-family="DejaVu Sans, system-ui, Segoe UI, sans-serif" font-size="13" font-weight="850" letter-spacing="0" fill="#f3f4f6" text-anchor="end">ARIA Intelligence · imaria.io</text>
</svg>`;
}

/**
 * Generate a comparison infographic (before/after, side-by-side).
 *
 * @param {object} data
 * @param {string} data.title
 * @param {object} data.left — { label, value, colour }
 * @param {object} data.right — { label, value, colour }
 * @param {string} [data.context]
 * @returns {string} SVG markup.
 */
export function generateComparisonCard(data) {
  const { title, left, right, context = '' } = data;
  const w = 1200;
  const h = 630;

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0f172a;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#1e293b;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="${w}" height="${h}" fill="url(#bg)" rx="16"/>

  <text x="600" y="60" font-family="system-ui, sans-serif" font-size="36" font-weight="700" fill="#ffffff" text-anchor="middle">${_escapeXml(title)}</text>

  <!-- Left card -->
  <rect x="40" y="100" width="540" height="400" rx="16" fill="${left.colour || '#1e40af'}" opacity="0.15"/>
  <rect x="40" y="100" width="540" height="400" rx="16" fill="none" stroke="${left.colour || '#1e40af'}" stroke-width="2" opacity="0.4"/>
  <text x="310" y="180" font-family="system-ui, sans-serif" font-size="22" font-weight="600" fill="${left.colour || '#60a5fa'}" text-anchor="middle">${_escapeXml(left.label)}</text>
  <text x="310" y="320" font-family="system-ui, sans-serif" font-size="64" font-weight="800" fill="#ffffff" text-anchor="middle">${_escapeXml(left.value)}</text>

  <!-- VS divider -->
  <circle cx="600" cy="300" r="36" fill="#334155"/>
  <text x="600" y="310" font-family="system-ui, sans-serif" font-size="18" font-weight="700" fill="#94a3b8" text-anchor="middle">VS</text>

  <!-- Right card -->
  <rect x="620" y="100" width="540" height="400" rx="16" fill="${right.colour || '#059669'}" opacity="0.15"/>
  <rect x="620" y="100" width="540" height="400" rx="16" fill="none" stroke="${right.colour || '#059669'}" stroke-width="2" opacity="0.4"/>
  <text x="890" y="180" font-family="system-ui, sans-serif" font-size="22" font-weight="600" fill="${right.colour || '#34d399'}" text-anchor="middle">${_escapeXml(right.label)}</text>
  <text x="890" y="320" font-family="system-ui, sans-serif" font-size="64" font-weight="800" fill="#ffffff" text-anchor="middle">${_escapeXml(right.value)}</text>

  ${context ? `<text x="600" y="550" font-family="system-ui, sans-serif" font-size="16" fill="#8892a0" text-anchor="middle">${_escapeXml(context)}</text>` : ''}
</svg>`;
}

/**
 * Generate a timeline infographic (chronological events).
 *
 * @param {object} data
 * @param {string} data.title
 * @param {Array<{date:string,event:string,colour?:string}>} data.events
 * @returns {string} SVG markup.
 */
export function generateTimelineCard(data) {
  const { title, events = [] } = data;
  const w = 1200;
  const h = Math.max(400, 120 + events.length * 80);
  const colours = ['#3b82f6', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#06b6d4'];

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:#0f172a;stop-opacity:1" />
      <stop offset="100%" style="stop-color:#1e293b;stop-opacity:1" />
    </linearGradient>
  </defs>
  <rect width="${w}" height="${h}" fill="url(#bg)" rx="16"/>

  <text x="40" y="60" font-family="system-ui, sans-serif" font-size="32" font-weight="700" fill="#ffffff">${_escapeXml(title)}</text>

  <!-- Timeline line -->
  <line x1="120" y1="100" x2="120" y2="${h - 40}" stroke="#334155" stroke-width="3"/>

  ${events.map((ev, i) => {
    const ey = 120 + (i * 80);
    const c = ev.colour || colours[i % colours.length];
    return `
  <circle cx="120" cy="${ey + 8}" r="10" fill="${c}" stroke="#1e293b" stroke-width="3"/>
  <text x="150" y="${ey + 6}" font-family="system-ui, sans-serif" font-size="14" font-weight="600" fill="${c}">${_escapeXml(ev.date)}</text>
  <text x="150" y="${ey + 28}" font-family="system-ui, sans-serif" font-size="16" fill="#c0c8d8">${_escapeXml(ev.event.substring(0, 100))}</text>`;
  }).join('')}
</svg>`;
}

// ── Thread Builder ─────────────────────────────────────────────────────────────

/**
 * Build a threaded post sequence from a long-form analysis.
 *
 * Splits content into a visual header card + numbered continuation messages,
 * each with "🧵 X/Y" navigation footer.
 *
 * @param {object} data
 * @param {string} data.title — Thread title.
 * @param {string} data.content — Full content (will be split).
 * @param {string} [data.type='analysis'] — Content type.
 * @param {number} [data.maxLength=3500] — Max chars per message.
 * @returns {Array<{text:string,image?:string}>} Ordered thread messages.
 */
export function buildThread(data) {
  const { title, content, type = 'analysis', maxLength = 3500 } = data;
  if (!content) return [{ text: title }];

  const parts = [];
  const sentences = content.match(/[^.!?\n]+[.!?\n]*/g) || [content];
  let current = `🧵 *${_escapeMarkdown(title)}*\n━━━━━━━━━━━━━━━━━━\n\n`;

  for (const sentence of sentences) {
    if ((current + sentence).length > maxLength) {
      parts.push(current);
      current = `*${_escapeMarkdown(title)} (cont.)*\n━━━━━━━━━━━━━━━━━━\n\n`;
    }
    current += sentence;
  }
  if (current) parts.push(current);

  // Add navigation footer
  return parts.map((text, i) => {
    const footer = `\n\n━━━━━━━━━━━━━━━━━━\n🧵 ${i + 1}/${parts.length}`;
    const navText = text + footer;
    return { text: navText };
  });
}

// ── Poll Builder ───────────────────────────────────────────────────────────────

/**
 * Build a Telegram poll for audience engagement.
 *
 * @param {object} data
 * @param {string} data.question — Poll question.
 * @param {string[]} data.options — Poll options (2-10).
 * @param {boolean} [data.isQuiz=false] — Whether this is a quiz.
 * @param {number} [data.correctOptionId] — For quizzes, the correct answer index.
 * @param {string} [data.explanation] — For quizzes, explanation text.
 * @returns {object} Poll payload for Telegram API.
 */
export function buildPoll(data) {
  const { question, options, isQuiz = false, correctOptionId, explanation } = data;

  if (!question || !options || options.length < 2 || options.length > 10) {
    throw new Error('Poll requires question and 2-10 options');
  }

  return {
    question: question.substring(0, 300),
    options: options.map(o => o.substring(0, 100)),
    is_anonymous: true,
    type: isQuiz ? 'quiz' : 'regular',
    ...(isQuiz && correctOptionId !== undefined ? {
      correct_option_id: correctOptionId,
      explanation: explanation?.substring(0, 200) || '',
    } : {}),
    open_period: 300, // 5 minutes
  };
}

// ── Media Upload ───────────────────────────────────────────────────────────────

/**
 * Upload an SVG image to Telegram and return the file ID for reuse.
 *
 * @param {object} bot — Telegram bot config { botToken, chatId }.
 * @param {string} svgContent — SVG markup.
 * @param {string} [filename='card.svg'] — Filename for upload.
 * @returns {Promise<{ok:boolean,fileId?:string,error?:string}>}
 */
/**
 * R-F2903 — rasterise SVG to PNG.
 *
 * Telegram's sendPhoto accepts JPEG/PNG ONLY. This module uploaded the raw SVG with
 * contentType 'image/svg+xml', so every card upload failed with
 * `400 IMAGE_PROCESS_FAILED` — it could never have worked. It went unnoticed because
 * the channel had not successfully reached the upload step in months; the first real
 * post (2026-07-23, R-F2902) surfaced it immediately.
 *
 * Returns a PNG Buffer, or null if rasterisation is unavailable/fails — the caller
 * then posts text-only, exactly as before. The card is polish; the intel is the post.
 */
// R-F2903 — a fontless container renders every glyph as a tofu box. The PNG is
// still produced, still the right size, still a plausible byte count — so every
// programmatic check passes while the image is unreadable. Byte counts cannot see
// this; only a human looking at the render can. So make the CAUSE loud instead,
// once per process, and refuse to upload a card we know is unreadable.
let _fontsChecked = false;
let _fontsPresent = true;
function _fontsAvailable() {
  if (_fontsChecked) return _fontsPresent;
  _fontsChecked = true;
  try {
    _fontsPresent = fs.existsSync('/usr/share/fonts') || process.platform === 'win32';
  } catch {
    _fontsPresent = true;   // undetermined -> do not block (the send stays authoritative)
  }
  if (!_fontsPresent) {
    console.error('[channelMedia] BLOCKED: no fonts installed (/usr/share/fonts missing) — '
      + 'cards would rasterise as unreadable tofu boxes. Install fontconfig + fonts-dejavu-core '
      + 'in the runtime image. Posting text-only until fixed.');
  }
  return _fontsPresent;
}

async function _svgToPng(svgContent) {
  if (!_fontsAvailable()) return null;
  try {
    const { default: sharp } = await import('sharp');
    return await sharp(Buffer.from(svgContent, 'utf-8'), { density: 144 })
      .png({ compressionLevel: 9 })
      .toBuffer();
  } catch (e) {
    console.warn(`[channelMedia] SVG->PNG rasterise failed: ${String(e?.message || e).slice(0, 160)}`);
    return null;
  }
}

export async function uploadSvgAsPhoto(bot, svgContent, filename = 'card.png') {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    // R-F2903 — rasterise first; Telegram rejects SVG outright.
    const png = await _svgToPng(svgContent);
    if (!png || !png.length) {
      return { ok: false, error: 'svg_rasterise_failed' };
    }
    const pngName = String(filename).replace(/\.svg$/i, '.png');
    const boundary = `----${randomUUID().replace(/-/g, '')}`;
    const body = _buildMultipartBody(boundary, [
      { name: 'chat_id', value: String(bot.chatId || bot.channelId) },
      { name: 'photo', filename: pngName, contentType: 'image/png', data: png },
    ]);

    const res = await fetch(`${TELEGRAM_API}/bot${bot.botToken}/sendPhoto`, {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body,
      signal: AbortSignal.timeout(30000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 200)}` };
    }

    const data = await res.json();
    return { ok: true, fileId: data.result?.photo?.[0]?.file_id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Send a photo (by file_id or URL) to a Telegram chat.
 *
 * @param {object} bot — Bot config.
 * @param {string} photo — file_id or URL.
 * @param {object} [opts]
 * @param {string} [opts.caption] — Photo caption (markdown).
 * @param {number} [opts.replyToMessageId] — Reply context.
 * @returns {Promise<{ok:boolean,messageId?:number}>}
 */
export async function sendPhoto(bot, photo, opts = {}) {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    const body = {
      chat_id: bot.chatId || bot.channelId,
      photo,
      ...(opts.caption ? { caption: opts.caption.substring(0, 1024), parse_mode: 'Markdown' } : {}),
      ...(opts.replyToMessageId ? { reply_to_message_id: opts.replyToMessageId } : {}),
    };

    const res = await fetch(`${TELEGRAM_API}/bot${bot.botToken}/sendPhoto`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 200)}` };
    }

    const data = await res.json();
    return { ok: true, messageId: data.result?.message_id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Send a poll to a Telegram chat.
 *
 * @param {object} bot — Bot config.
 * @param {object} pollData — From buildPoll().
 * @returns {Promise<{ok:boolean,messageId?:number}>}
 */
export async function sendPoll(bot, pollData) {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    const body = {
      chat_id: bot.chatId || bot.channelId,
      ...pollData,
    };

    const res = await fetch(`${TELEGRAM_API}/bot${bot.botToken}/sendPoll`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 200)}` };
    }

    const data = await res.json();
    return { ok: true, messageId: data.result?.message_id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Send a document (PDF, CSV, etc.) to a Telegram chat.
 *
 * @param {object} bot — Bot config.
 * @param {Buffer|string} document — File buffer or URL.
 * @param {string} filename — Display filename.
 * @param {object} [opts]
 * @param {string} [opts.caption] — Document caption.
 * @returns {Promise<{ok:boolean,messageId?:number}>}
 */
export async function sendDocument(bot, document, filename, opts = {}) {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    const boundary = `----${randomUUID().replace(/-/g, '')}`;
    const parts = [
      { name: 'chat_id', value: String(bot.chatId || bot.channelId) },
      { name: 'document', filename, contentType: 'application/octet-stream', data: Buffer.isBuffer(document) ? document : Buffer.from(document, 'utf-8') },
    ];
    if (opts.caption) parts.push({ name: 'caption', value: opts.caption.substring(0, 1024) });

    const res = await fetch(`${TELEGRAM_API}/bot${bot.botToken}/sendDocument`, {
      method: 'POST',
      headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` },
      body: _buildMultipartBody(boundary, parts),
      signal: AbortSignal.timeout(30000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 200)}` };
    }

    const data = await res.json();
    return { ok: true, messageId: data.result?.message_id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

// ── Inline Keyboard Builder ────────────────────────────────────────────────────

/**
 * Build an inline keyboard markup for interactive buttons.
 *
 * @param {Array<Array<{text:string,callback_data?:string,url?:string}>>} rows
 * @returns {object} InlineKeyboardMarkup.
 */
export function buildInlineKeyboard(rows) {
  return {
    reply_markup: {
      inline_keyboard: rows.map(row =>
        row.map(btn => ({
          text: btn.text.substring(0, 64),
          ...(btn.url ? { url: btn.url } : { callback_data: btn.callback_data || btn.text.toLowerCase().replace(/\s+/g, '_') }),
        }))
      ),
    },
  };
}

/**
 * Send a message with inline keyboard buttons.
 *
 * @param {object} bot — Bot config.
 * @param {string} text — Message text.
 * @param {object} keyboard — From buildInlineKeyboard().
 * @returns {Promise<{ok:boolean,messageId?:number}>}
 */
export async function sendWithInlineKeyboard(bot, text, keyboard) {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    const body = {
      chat_id: bot.chatId || bot.channelId,
      text: text.substring(0, 4000),
      parse_mode: 'Markdown',
      ...keyboard,
    };

    const res = await fetch(`${TELEGRAM_API}/bot${bot.botToken}/sendMessage`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(15000),
    });

    if (!res.ok) {
      const err = await res.text().catch(() => 'unknown');
      return { ok: false, error: `HTTP ${res.status}: ${err.substring(0, 200)}` };
    }

    const data = await res.json();
    return { ok: true, messageId: data.result?.message_id };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

// ── Internal Helpers ───────────────────────────────────────────────────────────

function _inferType(title, subtitle, data = {}) {
  const text = `${title || ''} ${subtitle || ''} ${data.source || ''}`.toLowerCase();
  if (/\b(flash|breaking|urgent|critical)\b/.test(text)) return 'flash';
  if (/\b(sanction|ofac|ofsi|sdn|watchlist|export control)\b/.test(text)) return 'sanctions';
  if (/\b(procurement|tender|rfq|contract|award|bid)\b/.test(text)) return 'procurement';
  if (/\b(country|geopolitical|conflict|election|border|security)\b/.test(text)) return 'geopolitical';
  if (/\b(opportunity|market|pipeline|lead)\b/.test(text)) return 'opportunity';
  if (/\b(case file|fraud|supply chain)\b/.test(text)) return 'case_file';
  if (/\b(method|rights|checklist|screen)\b/.test(text)) return 'rights';
  return data.type || 'intel';
}

function _deriveMetrics(data = {}) {
  const metrics = [];
  if (data.confidence != null) metrics.push({ label: 'Confidence', value: `${Math.round(Number(data.confidence) * (Number(data.confidence) <= 1 ? 100 : 1))}%` });
  if (data.score != null) metrics.push({ label: 'Signal score', value: String(Math.round(Number(data.score))) });
  if (data.severity) metrics.push({ label: 'Severity', value: String(data.severity).toUpperCase().slice(0, 12) });
  if (data.country) metrics.push({ label: 'Country', value: String(data.country).slice(0, 18) });
  if (data.sector) metrics.push({ label: 'Sector', value: String(data.sector).slice(0, 18) });
  if (Array.isArray(data.entities) && data.entities.length) metrics.push({ label: 'Entities', value: String(data.entities.length) });
  return metrics.slice(0, 3);
}

function _deriveBullets(data = {}) {
  const fields = [
    data.why_it_matters,
    data.recommended_action,
    data.action,
    data.corroboration,
    data.risk,
    data.url ? `Source link available: ${data.url}` : '',
  ];
  const fromText = String(data.text || data.subtitle || data.summary || '')
    .split(/\n|\. /)
    .map(s => s.replace(/^[•*\-\s]+/, '').trim())
    .filter(s => s.length >= 18);
  return [...fields, ...fromText].filter(Boolean).slice(0, 3);
}

/**
 * Get colour scheme for a content type.
 */
function _typeColours(type) {
  const schemes = {
    flash:      { label: '🚨 FLASH ALERT',     accent: '#ef4444', accentEnd: '#dc2626', bgStart: '#1a0a0a', bgEnd: '#2d1a1a' },
    sanctions:  { label: '⚖️  SANCTIONS',       accent: '#f59e0b', accentEnd: '#d97706', bgStart: '#1a1505', bgEnd: '#2d2010' },
    procurement:{ label: '🔍 PROCUREMENT',      accent: '#3b82f6', accentEnd: '#2563eb', bgStart: '#0a1428', bgEnd: '#14203d' },
    geopolitical:{label: '🌍 GEOPOLITICAL',     accent: '#8b5cf6', accentEnd: '#7c3aed', bgStart: '#140a28', bgEnd: '#20143d' },
    opportunity:{ label: '💡 OPPORTUNITY',      accent: '#10b981', accentEnd: '#059669', bgStart: '#0a1a14', bgEnd: '#142d20' },
    case_file:  { label: '📁 CASE FILE',        accent: '#ec4899', accentEnd: '#db2777', bgStart: '#1a0a14', bgEnd: '#2d1420' },
    rights:     { label: '🛡️  KNOW YOUR RIGHTS',accent: '#06b6d4', accentEnd: '#0891b2', bgStart: '#0a1a1e', bgEnd: '#14282d' },
    country:    { label: '🌐 COUNTRY READ',     accent: '#84cc16', accentEnd: '#65a30d', bgStart: '#0a1a05', bgEnd: '#142d10' },
    sector:     { label: '📋 SECTOR DEEP DIVE', accent: '#f97316', accentEnd: '#ea580c', bgStart: '#1a0f05', bgEnd: '#2d1a10' },
    daily:      { label: '📰 DAILY BRIEF',      accent: '#6366f1', accentEnd: '#4f46e5', bgStart: '#0a0a1e', bgEnd: '#14142d' },
    intel:      { label: '📊 MARKET INTEL',     accent: '#64748b', accentEnd: '#475569', bgStart: '#0f172a', bgEnd: '#1e293b' },
  };
  return schemes[type] || schemes.intel;
}

/**
 * Estimate text width for badge sizing.
 */
// R-F2903 — how many characters of `fontSize` fit in `px`, using the same 0.6em
// average-advance estimate as _textWidth. Char-count limits were tuned for a narrow
// UI font; DejaVu Sans is wider, so the German TED notice overflowed the title into
// the panel and pushed both panels past the card edge. Deriving the budget from the
// available WIDTH means a font change cannot silently break the layout again.
function _charsForWidth(px, fontSize) {
  return Math.max(8, Math.floor(px / (fontSize * 0.6)));
}

function _textWidth(text, fontSize) {
  return text.length * fontSize * 0.6;
}

/**
 * Wrap text for SVG (insert line breaks).
 */
function _wrapText(text, fontSize, maxCharsPerLine) {
  const maxChars = maxCharsPerLine || Math.floor(80 / (fontSize / 14));
  if (text.length <= maxChars) return text;
  const lines = [];
  for (let i = 0; i < text.length; i += maxChars) {
    lines.push(text.substring(i, i + maxChars));
  }
  return lines.map((l, i) => `<tspan x="40" dy="${i === 0 ? 0 : fontSize + 6}">${l}</tspan>`).join('');
}

function _svgLines(text, x, y, lineHeight, maxCharsPerLine, maxLines = 3) {
  const words = String(text || '').replace(/\s+/g, ' ').trim().split(' ').filter(Boolean);
  if (!words.length) return '';
  const lines = [];
  let current = '';
  let truncated = false;
  for (const word of words) {
    const next = current ? `${current} ${word}` : word;
    if (next.length > maxCharsPerLine && current) {
      lines.push(current);
      current = word;
    } else {
      current = next;
    }
    if (lines.length >= maxLines) { truncated = true; break; }
  }
  if (current && lines.length < maxLines) lines.push(current);
  else if (current) truncated = true;
  // R-F2903 — the line cap was hardcoded at 3 and the overflow was dropped SILENTLY,
  // so a long title (e.g. the Hungary TED notice) lost its final word with nothing to
  // show it had been cut. maxLines is now a parameter, and an ellipsis marks any
  // truncation — a reader must never be shown a clipped string that looks complete.
  if (truncated && lines.length) lines[lines.length - 1] = `${lines[lines.length - 1]}…`;
  return lines.map((line, i) =>
    `<tspan x="${x}" y="${y + (i * lineHeight)}">${_escapeXml(line.slice(0, maxCharsPerLine + 8))}</tspan>`
  ).join('');
}

/**
 * Escape XML special characters for SVG safety.
 */
function _escapeXml(text) {
  if (!text) return '';
  return String(text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

/**
 * Escape markdown for Telegram.
 */
function _escapeMarkdown(text) {
  if (!text) return '';
  return String(text)
    .replace(/_/g, '\\_')
    .replace(/\*/g, '\\*')
    .replace(/`/g, '\\`')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]');
}

/**
 * Build a multipart/form-data body for file uploads.
 */
function _buildMultipartBody(boundary, parts) {
  const lines = [];
  for (const part of parts) {
    lines.push(`--${boundary}`);
    if (part.filename) {
      lines.push(`Content-Disposition: form-data; name="${part.name}"; filename="${part.filename}"`);
      lines.push(`Content-Type: ${part.contentType || 'application/octet-stream'}`);
    } else {
      lines.push(`Content-Disposition: form-data; name="${part.name}"`);
    }
    lines.push('');
    lines.push(part.value || (part.data ? part.data.toString('utf-8') : ''));
  }
  lines.push(`--${boundary}--`);
  return lines.join('\r\n');
}
