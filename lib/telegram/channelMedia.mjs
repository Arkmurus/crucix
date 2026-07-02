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
export function generateInfographicCard(data) {
  const {
    title = 'ARIA Intelligence',
    subtitle = '',
    metrics = [],
    bullets = [],
    source = 'ARIA Intelligence',
    type = 'intel',
  } = data;

  const colours = _typeColours(type);
  const w = 1200;
  const h = 630;
  const titleSize = title.length > 60 ? 32 : title.length > 30 ? 38 : 44;
  const subtitleSize = 22;

  let svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
  <defs>
    <linearGradient id="bg" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" style="stop-color:${colours.bgStart};stop-opacity:1" />
      <stop offset="100%" style="stop-color:${colours.bgEnd};stop-opacity:1" />
    </linearGradient>
    <linearGradient id="header" x1="0%" y1="0%" x2="100%" y2="0%">
      <stop offset="0%" style="stop-color:${colours.accent};stop-opacity:1" />
      <stop offset="100%" style="stop-color:${colours.accentEnd || colours.accent};stop-opacity:1" />
    </linearGradient>
    <filter id="shadow" x="-5%" y="-5%" width="110%" height="110%">
      <feDropShadow dx="0" dy="2" stdDeviation="4" flood-opacity="0.15"/>
    </filter>
  </defs>

  <!-- Background -->
  <rect width="${w}" height="${h}" fill="url(#bg)" rx="16"/>

  <!-- Header bar -->
  <rect x="0" y="0" width="${w}" height="8" fill="url(#header)"/>

  <!-- Content type badge -->
  <rect x="40" y="30" width="${_textWidth(colours.label, 14) + 40}" height="32" rx="16" fill="${colours.accent}" opacity="0.2"/>
  <text x="60" y="51" font-family="system-ui, sans-serif" font-size="14" font-weight="600" fill="${colours.accent}">${_escapeXml(colours.label)}</text>

  <!-- Title -->
  <text x="40" y="110" font-family="system-ui, sans-serif" font-size="${titleSize}" font-weight="700" fill="#ffffff" filter="url(#shadow)">${_wrapText(_escapeXml(title), titleSize, 28)}</text>

  <!-- Subtitle -->
  ${subtitle ? `<text x="40" y="${150 + (title.length > 60 ? 40 : 0)}" font-family="system-ui, sans-serif" font-size="${subtitleSize}" fill="#b0b8c8">${_wrapText(_escapeXml(subtitle), subtitleSize, 24)}</text>` : ''}

  <!-- Metrics row -->
  ${metrics.map((m, i) => {
    const mx = 40 + (i * 280);
    return `
  <g>
    <rect x="${mx}" y="240" width="240" height="80" rx="12" fill="rgba(255,255,255,0.08)"/>
    <text x="${mx + 120}" y="278" font-family="system-ui, sans-serif" font-size="28" font-weight="700" fill="${colours.accent}" text-anchor="middle">${_escapeXml(m.value)}</text>
    <text x="${mx + 120}" y="302" font-family="system-ui, sans-serif" font-size="13" fill="#8892a0" text-anchor="middle">${_escapeXml(m.label)}</text>
  </g>`;
  }).join('')}

  <!-- Bullet points -->
  ${bullets.map((b, i) => {
    const by = metrics.length > 0 ? 360 : 240;
    return `
  <text x="40" y="${by + (i * 32)}" font-family="system-ui, sans-serif" font-size="16" fill="#c0c8d8">
    <tspan font-weight="700" fill="${colours.accent}">▸ </tspan>${_escapeXml(b.substring(0, 90))}
  </text>`;
  }).join('')}

  <!-- Footer -->
  <line x1="40" y1="${h - 70}" x2="${w - 40}" y2="${h - 70}" stroke="rgba(255,255,255,0.1)"/>
  <text x="40" y="${h - 40}" font-family="system-ui, sans-serif" font-size="13" fill="#6b7280">${_escapeXml(source)}</text>
  <text x="${w - 40}" y="${h - 40}" font-family="system-ui, sans-serif" font-size="13" fill="#6b7280" text-anchor="end">ARIA Intelligence</text>
</svg>`;

  return svg;
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
export async function uploadSvgAsPhoto(bot, svgContent, filename = 'card.svg') {
  if (!bot?.botToken) return { ok: false, error: 'No bot token' };

  try {
    // Convert SVG to a Buffer and upload as photo
    const boundary = `----${randomUUID().replace(/-/g, '')}`;
    const body = _buildMultipartBody(boundary, [
      { name: 'chat_id', value: String(bot.chatId || bot.channelId) },
      { name: 'photo', filename, contentType: 'image/svg+xml', data: Buffer.from(svgContent, 'utf-8') },
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
