// lib/telegram/channelPublisher.mjs
//
// Telegram Broadcast Channel Publisher — R-F2287
// ===============================================
// Three engines in one module:
//   1. CurationEngine  — selects intel signals for the public channel
//   2. FormattingEngine — formats posts for Telegram channel (markdown, cards, threads)
//   3. ChannelScheduler — autonomous daily posting cadence
//
// The public channel is separate from the private admin bot chat. It pushes
// curated intelligence to subscribers with a reply-keyword system for drill-down.
//
// Strategy (2026-07-18):
//   Phase 1: Broadcast channel (public intelligence dissemination)
//   Phase 2: Curation pipeline (selecting/filtering what goes to the channel)
//   Phase 3: Reply-keyword system (interactivity — users reply with keywords)
//   Phase 4: WhatsApp mirror (after Telegram proves the model)

import { createHash } from 'node:crypto';

// ── Constants ──────────────────────────────────────────────────────────────────

/** Max Telegram message length (Telegram's limit is 4096, we stay safe at 4000). */
const MAX_MSG_LENGTH = 4000;

/** Max posts per day to avoid flooding subscribers. */
const MAX_DAILY_POSTS = 6;

/** Cooldown between posts in minutes. */
const POST_COOLDOWN_MIN = 90;

/** Minimum signal quality score (0-1) to be considered channel-worthy. */
const MIN_QUALITY_SCORE = 0.55;

/** Content type labels for channel posts. */
const CONTENT_TYPES = {
  FLASH_ALERT:     '🚨 Flash Alert',
  MARKET_INTEL:    '📊 Market Intel',
  PROCUREMENT:     '🔍 Procurement Signal',
  SANCTIONS:       '⚖️  Sanctions Update',
  GEOPOLITICAL:    '🌍 Geopolitical',
  SECTOR_DEEP:     '📋 Sector Deep Dive',
  CASE_FILE:       '📁 Case File',
  KNOW_YOUR_RIGHTS:'🛡️  Know Your Rights',
  COUNTRY_READ:    '🌐 Country Read',
  OPPORTUNITY:     '💡 Opportunity Signal',
  DAILY_BRIEF:     '📰 Daily Brief',
};

// ── In-memory state ────────────────────────────────────────────────────────────

/** Dedup set of posted content hashes (session-only; survives restarts via persist). */
const _postedHashes = new Set();

/** Post schedule queue — timestamps of when to post next content type. */
const _scheduleQueue = [];

/** Last post timestamp (ms since epoch). */
let _lastPostAt = 0;

/** Daily post counter (resets at midnight UTC). */
let _dailyPostCount = 0;
let _dailyPostDate = '';

// ── CurationEngine ─────────────────────────────────────────────────────────────

/**
 * Score an intel signal for channel-worthiness.
 *
 * Factors considered:
 *   - Signal quality (junk-filtered, deduped)
 *   - Relevance to Arkmurus audience (procurement, sanctions, Africa/LATAM intel)
 *   - Urgency (flash alerts score higher)
 *   - Freshness (within last 24h)
 *   - Source reliability
 *
 * @param {object} signal — An intel signal from the sweep pipeline.
 * @param {object} [opts] — Optional overrides.
 * @param {number} [opts.minScore=MIN_QUALITY_SCORE] — Minimum score to pass.
 * @returns {{ pass: boolean, score: number, reason: string }}
 */
export function scoreSignal(signal, opts = {}) {
  const minScore = opts.minScore ?? MIN_QUALITY_SCORE;
  if (!signal || typeof signal !== 'object') {
    return { pass: false, score: 0, reason: 'invalid signal' };
  }

  let score = 0.5; // base
  const reasons = [];

  // ── Urgency boost ──────────────────────────────────────────────────────────
  if (signal.severity === 'critical' || signal.severity === 'high') {
    score += 0.2;
    reasons.push('high-severity');
  }

  // ── Relevance signals ──────────────────────────────────────────────────────
  const text = (signal.text || signal.title || signal.summary || '').toLowerCase();
  const relevanceKeywords = [
    'sanction', 'procurement', 'tender', 'defence', 'military',
    'africa', 'angola', 'mozambique', 'nigeria', 'kenya', 'ethiopia',
    'brazil', 'portugal', 'lusophone', 'cplp',
    'oil', 'gas', 'mining', 'critical mineral', 'rare earth',
    'export control', 'trade restriction', 'tariff',
    'conflict', 'instability', 'coup', 'election',
    'infrastructure', 'energy', 'logistics', 'port',
  ];
  const matched = relevanceKeywords.filter(kw => text.includes(kw));
  if (matched.length >= 3) {
    score += 0.15;
    reasons.push(`relevant (${matched.length} keywords)`);
  } else if (matched.length >= 1) {
    score += 0.05;
  }

  // ── Freshness ──────────────────────────────────────────────────────────────
  if (signal.timestamp) {
    const ageHours = (Date.now() - new Date(signal.timestamp).getTime()) / 3_600_000;
    if (ageHours < 6) {
      score += 0.1;
      reasons.push('fresh');
    } else if (ageHours > 48) {
      score -= 0.1;
      reasons.push('stale');
    }
  }

  // ── Source reliability ─────────────────────────────────────────────────────
  const reliableSources = ['opensanctions', 'un_sc', 'fcdo', 'state_dept', 'europol',
    'interpol', 'world_bank', 'imf', 'afdb', 'african_union',
    'reuters', 'bloomberg', 'ft', 'bbc', 'economist',
  ];
  if (signal.source && reliableSources.includes(signal.source.toLowerCase())) {
    score += 0.1;
    reasons.push('reliable-source');
  }

  // ── Dedup check ────────────────────────────────────────────────────────────
  const contentHash = _contentHash(signal);
  if (_postedHashes.has(contentHash)) {
    return { pass: false, score, reason: 'already posted (dedup)' };
  }

  // Clamp
  score = Math.max(0, Math.min(1, score));

  const pass = score >= minScore;
  if (pass) {
    _postedHashes.add(contentHash);
  }

  return {
    pass,
    score,
    reason: reasons.length > 0 ? reasons.join(', ') : 'below threshold',
  };
}

/**
 * Curate a batch of signals into a channel-ready queue.
 *
 * @param {object[]} signals — Array of intel signals.
 * @param {object} [opts]
 * @param {number} [opts.maxPosts=MAX_DAILY_POSTS] — Max posts to select.
 * @returns {object[]} Sorted, scored, deduped signals ready for formatting.
 */
export function curateSignals(signals, opts = {}) {
  if (!Array.isArray(signals) || signals.length === 0) return [];

  const maxPosts = opts.maxPosts ?? MAX_DAILY_POSTS;

  const scored = signals
    .map(s => ({ signal: s, ...scoreSignal(s) }))
    .filter(r => r.pass)
    .sort((a, b) => b.score - a.score)
    .slice(0, maxPosts);

  return scored.map(r => r.signal);
}

// ── FormattingEngine ───────────────────────────────────────────────────────────

/**
 * Format a signal into a Telegram channel post.
 *
 * Produces markdown-formatted posts with:
 *   - Emoji header + content type label
 *   - Title/summary
 *   - Key details (bullet points)
 *   - Source attribution
 *   - Reply-keyword hint (Phase 3)
 *
 * @param {object} signal — Curated intel signal.
 * @param {object} [opts]
 * @param {boolean} [opts.includeReplyHint=true] — Append reply-keyword hint.
 * @returns {string} Formatted markdown message.
 */
export function formatChannelPost(signal, opts = {}) {
  const includeReplyHint = opts.includeReplyHint !== false;

  const typeLabel = _detectContentType(signal);
  const title = signal.title || signal.summary || 'Intel Update';
  const summary = signal.summary || signal.text || '';
  const source = signal.source || signal.channel || 'ARIA Intelligence';
  const timestamp = signal.timestamp
    ? new Date(signal.timestamp).toLocaleString('en-GB', { timeZone: 'Europe/London' })
    : new Date().toLocaleString('en-GB', { timeZone: 'Europe/London' });

  let msg = `${typeLabel}\n━━━━━━━━━━━━━━━━━━\n\n`;
  msg += `*${_escapeMarkdown(title)}*\n\n`;

  // Summary (truncated)
  const cleanSummary = _escapeMarkdown(summary.substring(0, 800));
  msg += `${cleanSummary}\n\n`;

  // Key details
  const details = _extractDetails(signal);
  if (details.length > 0) {
    msg += `*Key Details:*\n${details.map(d => `• ${_escapeMarkdown(d)}`).join('\n')}\n\n`;
  }

  // Source + timestamp
  msg += `━━━━━━━━━━━━━━━━━━\n`;
  msg += `📡 *Source:* ${_escapeMarkdown(source)}\n`;
  msg += `🕐 ${timestamp}\n`;

  // Reply-keyword hint (Phase 3)
  if (includeReplyHint) {
    const keyword = _generateKeyword(title);
    msg += `\n💬 *Reply with* \`${keyword}\` *for deeper analysis*`;
  }

  // Enforce length limit
  if (msg.length > MAX_MSG_LENGTH) {
    msg = msg.substring(0, MAX_MSG_LENGTH - 100) + '\n\n…*truncated*';
  }

  return msg;
}

/**
 * Format a daily briefing post for the channel.
 *
 * @param {object} briefData — Aggregated daily intelligence.
 * @param {object} [opts]
 * @returns {string} Formatted daily briefing.
 */
export function formatDailyBrief(briefData, opts = {}) {
  const date = new Date().toLocaleDateString('en-GB', {
    timeZone: 'Europe/London',
    day: 'numeric', month: 'long', year: 'numeric',
  });

  let msg = `📰 *ARKMURUS DAILY BRIEF*\n`;
  msg += `${date} · London\n`;
  msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  const sections = briefData?.sections || [];
  for (const section of sections) {
    msg += `*${_escapeMarkdown(section.title || 'Update')}*\n`;
    msg += `${_escapeMarkdown((section.text || '').substring(0, 500))}\n\n`;
  }

  msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
  msg += `🤖 *Curated by ARIA Intelligence*`;
  msg += `\n💬 *Reply with* \`deepdive\` *for full analysis*`;

  return msg;
}

// ── ChannelScheduler ───────────────────────────────────────────────────────────

/**
 * Check whether we can post now (rate-limit check).
 *
 * @returns {{ canPost: boolean, reason: string, nextPostAt: number|null }}
 */
export function canPostNow() {
  const now = Date.now();

  // Daily limit
  const today = new Date().toISOString().split('T')[0];
  if (_dailyPostDate !== today) {
    _dailyPostDate = today;
    _dailyPostCount = 0;
  }
  if (_dailyPostCount >= MAX_DAILY_POSTS) {
    const nextMidnight = new Date();
    nextMidnight.setUTCHours(24, 0, 0, 0);
    return { canPost: false, reason: 'daily limit reached', nextPostAt: nextMidnight.getTime() };
  }

  // Cooldown
  const elapsed = now - _lastPostAt;
  if (elapsed < POST_COOLDOWN_MIN * 60_000) {
    const nextPostAt = _lastPostAt + POST_COOLDOWN_MIN * 60_000;
    return { canPost: false, reason: 'cooldown active', nextPostAt };
  }

  return { canPost: true, reason: 'ok', nextPostAt: null };
}

/**
 * Record that a post was made (updates rate-limit state).
 */
export function recordPost() {
  _lastPostAt = Date.now();
  const today = new Date().toISOString().split('T')[0];
  if (_dailyPostDate !== today) {
    _dailyPostDate = today;
    _dailyPostCount = 0;
  }
  _dailyPostCount++;
}

/**
 * Get scheduler state (for monitoring/debugging).
 *
 * @returns {object}
 */
export function getSchedulerState() {
  return {
    lastPostAt: _lastPostAt,
    dailyPostCount: _dailyPostCount,
    dailyPostDate: _dailyPostDate,
    postedHashes: _postedHashes.size,
    scheduleQueueLength: _scheduleQueue.length,
  };
}

/**
 * Reset scheduler state (for testing).
 */
export function _resetSchedulerState() {
  _postedHashes.clear();
  _scheduleQueue.length = 0;
  _lastPostAt = 0;
  _dailyPostCount = 0;
  _dailyPostDate = '';
}

// ── Internal helpers ───────────────────────────────────────────────────────────

/**
 * Generate a content hash for dedup.
 * @param {object} signal
 * @returns {string}
 */
function _contentHash(signal) {
  const raw = JSON.stringify({
    t: signal.title || signal.summary || signal.text || '',
    s: signal.source || signal.channel || '',
    ts: signal.timestamp || '',
  });
  return createHash('sha256').update(raw).digest('hex').substring(0, 16);
}

/**
 * Detect the content type label for a signal.
 * @param {object} signal
 * @returns {string}
 */
function _detectContentType(signal) {
  const text = (signal.text || signal.title || signal.summary || '').toLowerCase();
  const sev = (signal.severity || '').toLowerCase();

  if (sev === 'critical' || sev === 'high') return CONTENT_TYPES.FLASH_ALERT;
  if (text.includes('sanction') || text.includes('ofac') || text.includes('embargo')) return CONTENT_TYPES.SANCTIONS;
  if (text.includes('tender') || text.includes('procurement') || text.includes('rfp')) return CONTENT_TYPES.PROCUREMENT;
  if (text.includes('defence') || text.includes('military') || text.includes('armed')) return CONTENT_TYPES.GEOPOLITICAL;
  if (text.includes('opportunity') || text.includes('arbitrage') || text.includes('market gap')) return CONTENT_TYPES.OPPORTUNITY;
  if (text.includes('case file') || text.includes('investigation') || text.includes('exposed')) return CONTENT_TYPES.CASE_FILE;
  if (text.includes('rights') || text.includes('legal') || text.includes('compliance')) return CONTENT_TYPES.KNOW_YOUR_RIGHTS;
  if (text.includes('country') || text.includes('profile') || text.includes('overview')) return CONTENT_TYPES.COUNTRY_READ;
  if (text.includes('sector') || text.includes('industry') || text.includes('market')) return CONTENT_TYPES.SECTOR_DEEP;
  return CONTENT_TYPES.MARKET_INTEL;
}

/**
 * Extract key detail bullets from a signal.
 * @param {object} signal
 * @returns {string[]}
 */
function _extractDetails(signal) {
  const details = [];

  if (signal.country) details.push(`Country: ${signal.country}`);
  if (signal.sector) details.push(`Sector: ${signal.sector}`);
  if (signal.impact) details.push(`Impact: ${signal.impact}`);
  if (signal.confidence) details.push(`Confidence: ${(signal.confidence * 100).toFixed(0)}%`);
  if (signal.entities && Array.isArray(signal.entities)) {
    details.push(`Related entities: ${signal.entities.slice(0, 3).join(', ')}`);
  }
  if (signal.value) details.push(`Estimated value: ${signal.value}`);

  return details;
}

/**
 * Generate a reply keyword from a title.
 * @param {string} title
 * @returns {string}
 */
function _generateKeyword(title) {
  const words = title
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter(w => w.length > 2 && !['the', 'and', 'for', 'are', 'was', 'had', 'has', 'but', 'not'].includes(w));
  if (words.length === 0) return 'analyze';
  // Take first 2 meaningful words
  return words.slice(0, 2).join('_');
}

/**
 * Escape markdown special characters for Telegram.
 * Telegram uses a subset of markdown: _ * ` [ ] ( ) ~ > # + - = | { } . !
 * @param {string} text
 * @returns {string}
 */
function _escapeMarkdown(text) {
  if (!text) return '';
  return text
    .replace(/_/g, '\\_')
    .replace(/\*/g, '\\*')
    .replace(/`/g, '\\`')
    .replace(/\[/g, '\\[')
    .replace(/\]/g, '\\]')
    .replace(/\(/g, '\\(')
    .replace(/\)/g, '\\)')
    .replace(/~/g, '\\~')
    .replace(/>/g, '\\>')
    .replace(/#/g, '\\#')
    .replace(/\+/g, '\\+')
    .replace(/-/g, '\\-')
    .replace(/=/g, '\\=')
    .replace(/\|/g, '\\|')
    .replace(/\{/g, '\\{')
    .replace(/\}/g, '\\}')
    .replace(/\./g, '\\.')
    .replace(/!/g, '\\!');
}

// ── Full Pipeline Orchestration ────────────────────────────────────────────────

/**
 * Run the full publish pipeline for a single signal:
 *   score → format → generate infographic → register keyword → post → LinkedIn cross-post
 *
 * This is the main entry point called from server.mjs sweep cycle.
 *
 * @param {object} signal — Intel signal to publish.
 * @param {object} bot — Telegram bot config { botToken, chatId, channelId }.
 * @param {object} [opts]
 * @param {boolean} [opts.generateImage=true] — Generate SVG infographic.
 * @param {boolean} [opts.registerKeyword=true] — Register reply keyword.
 * @param {boolean} [opts.crossPostLinkedIn=false] — Cross-post to LinkedIn.
 * @param {number} [opts.minScore] — Override minimum quality score.
 * @returns {Promise<{ok:boolean,postId?:number,keyword?:string,linkedIn?:object,error?:string}>}
 */
export async function publishSignal(signal, bot, opts = {}) {
  const {
    generateImage = true,
    registerKeyword: shouldRegisterKeyword = true,
    crossPostLinkedIn = false,
    minScore,
  } = opts;

  // 1. Score
  const scored = scoreSignal(signal, { minScore });
  if (!scored.pass) {
    return { ok: false, error: `Signal did not pass curation: ${scored.reason} (score=${scored.score.toFixed(2)})` };
  }

  if (!bot?.botToken) {
    return { ok: false, error: 'No bot token provided' };
  }

  try {
    // 2. Format the post
    const post = formatChannelPost(signal);

    // 3. Generate infographic image (async, best-effort)
    let imageResult = null;
    if (generateImage) {
      try {
        const { generateInfographicCard, uploadSvgAsPhoto } = await import('./channelMedia.mjs');
        const type = _detectContentType(signal).toLowerCase().replace(/[^a-z]/g, '') || 'intel';
        const svg = generateInfographicCard({
          title: signal.title || signal.summary || 'Intel Update',
          subtitle: (signal.summary || '').substring(0, 120),
          metrics: _extractMetrics(signal),
          bullets: _extractBullets(signal),
          source: signal.source || 'ARIA Intelligence',
          type,
        });
        imageResult = await uploadSvgAsPhoto(bot, svg, `intel_${Date.now()}.svg`);
      } catch (imgErr) {
        console.warn('[ChannelPublisher] Image generation skipped:', imgErr.message);
      }
    }

    // 4. Send the post (with image if available, otherwise text)
    let sendResult;
    if (imageResult?.ok && imageResult.fileId) {
      const { sendPhoto } = await import('./channelMedia.mjs');
      sendResult = await sendPhoto(bot, imageResult.fileId, { caption: post });
    } else {
      // Use the existing sendMessage via the bot
      const chatId = bot.channelId || bot.chatId;
      const res = await fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: chatId,
          text: post,
          parse_mode: 'Markdown',
          disable_web_page_preview: true,
        }),
        signal: AbortSignal.timeout(15000),
      });
      const data = await res.json();
      sendResult = { ok: res.ok, messageId: data.result?.message_id };
    }

    if (!sendResult.ok) {
      return { ok: false, error: 'Failed to send message to channel' };
    }

    recordPost();

    // 5. Register keyword for interactive deep-dive
    let keyword = null;
    if (shouldRegisterKeyword) {
      try {
        const { registerPostKeyword, buildDeepDive } = await import('./channelInteractive.mjs');
        const deepDive = buildDeepDive(signal);
        const reg = registerPostKeyword(signal.title || signal.summary || 'intel', deepDive, signal, sendResult.messageId);
        if (reg.ok) keyword = reg.keyword;
      } catch (kwErr) {
        console.warn('[ChannelPublisher] Keyword registration skipped:', kwErr.message);
      }
    }

    // 6. Cross-post to LinkedIn
    let linkedInResult = null;
    if (crossPostLinkedIn) {
      try {
        const { postTextUpdate, formatForLinkedIn, canPostNow: liCanPost } = await import('../linkedin/linkedinPublisher.mjs');
        if (liCanPost().canPost) {
          const liPost = formatForLinkedIn(post);
          linkedInResult = await postTextUpdate(liPost);
        }
      } catch (liErr) {
        console.warn('[ChannelPublisher] LinkedIn cross-post skipped:', liErr.message);
      }
    }

    return {
      ok: true,
      postId: sendResult.messageId,
      keyword,
      linkedIn: linkedInResult,
    };
  } catch (err) {
    return { ok: false, error: err.message };
  }
}

/**
 * Extract metrics from a signal for infographic display.
 * @param {object} signal
 * @returns {Array<{label:string,value:string}>}
 */
function _extractMetrics(signal) {
  const metrics = [];
  if (signal.confidence) metrics.push({ label: 'Confidence', value: `${(signal.confidence * 100).toFixed(0)}%` });
  if (signal.value) metrics.push({ label: 'Value', value: signal.value });
  if (signal.entities?.length) metrics.push({ label: 'Entities', value: String(signal.entities.length) });
  if (signal.country) metrics.push({ label: 'Country', value: signal.country });
  return metrics;
}

/**
 * Extract bullet points from a signal for infographic display.
 * @param {object} signal
 * @returns {string[]}
 */
function _extractBullets(signal) {
  const bullets = [];
  if (signal.impact) bullets.push(signal.impact);
  if (signal.summary && signal.summary.length > 60) bullets.push(signal.summary.substring(0, 200));
  return bullets;
}
