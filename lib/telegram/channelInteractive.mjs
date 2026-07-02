// lib/telegram/channelInteractive.mjs
//
// Telegram Channel Interactive Engine — R-F2288
// ==============================================
// Powers audience engagement on the broadcast channel:
//   1. Reply-keyword system — users reply with keywords for deeper analysis
//   2. Deep-dive responses — auto-generated intel deep-dives on demand
//   3. Poll engagement — scheduled polls with result tracking
//   4. Callback query handling — inline button interactions
//   5. Engagement analytics — track which content drives interaction
//
// Phase 3 of the Telegram channel strategy (2026-07-18).

import { createHash } from 'node:crypto';

// ── Constants ──────────────────────────────────────────────────────────────────

const TELEGRAM_API = 'https://api.telegram.org';

/** Max keywords stored per channel. */
const MAX_KEYWORDS = 200;

/** Cooldown per user for keyword replies (seconds). */
const USER_KEYWORD_COOLDOWN_S = 30;

/** Max deep-dive response length. */
const MAX_DEEP_DIVE_LENGTH = 3500;

// ── In-memory stores ───────────────────────────────────────────────────────────

/** Registered keywords → { signal, response, postedAt, messageId } */
const _keywordRegistry = new Map();

/** User cooldown tracker: `userId:keyword` → timestamp */
const _userCooldowns = new Map();

/** Engagement stats: keyword → { triggered, lastTriggeredAt } */
const _engagementStats = new Map();

/** Poll results: pollId → { question, options, votes, closed } */
const _pollResults = new Map();

// ── Keyword Registration ───────────────────────────────────────────────────────

/**
 * Register a keyword for a channel post.
 *
 * When a user replies with this keyword, the associated deep-dive is sent.
 *
 * @param {object} entry
 * @param {string} entry.keyword — The reply keyword (lowercase, alphanumeric).
 * @param {string} entry.response — The deep-dive response text.
 * @param {object} [entry.signal] — Original signal that triggered the post.
 * @param {number} [entry.messageId] — Telegram message ID this keyword is attached to.
 * @returns {{ ok: boolean, error?: string }}
 */
export function registerKeyword(entry) {
  const { keyword, response, signal, messageId } = entry;

  if (!keyword || !response) {
    return { ok: false, error: 'keyword and response are required' };
  }

  const clean = keyword.toLowerCase().replace(/\s+/g, '_').replace(/[^a-z0-9_]/g, '').substring(0, 40);
  if (!clean) return { ok: false, error: 'invalid keyword' };

  // Evict oldest if at capacity
  if (_keywordRegistry.size >= MAX_KEYWORDS) {
    const oldest = _keywordRegistry.entries().next().value;
    if (oldest) _keywordRegistry.delete(oldest[0]);
  }

  _keywordRegistry.set(clean, {
    response: response.substring(0, MAX_DEEP_DIVE_LENGTH),
    signal: signal || null,
    postedAt: Date.now(),
    messageId: messageId || null,
  });

  if (!_engagementStats.has(clean)) {
    _engagementStats.set(clean, { triggered: 0, lastTriggeredAt: null });
  }

  return { ok: true, keyword: clean };
}

/**
 * Generate a keyword from a title and register it with a deep-dive response.
 *
 * @param {string} title — Post title.
 * @param {string} deepDive — Deep-dive analysis text.
 * @param {object} [signal] — Original signal.
 * @param {number} [messageId] — Telegram message ID.
 * @returns {{ ok: boolean, keyword: string, error?: string }}
 */
export function registerPostKeyword(title, deepDive, signal, messageId) {
  const keyword = _generateKeyword(title);
  return registerKeyword({ keyword, response: deepDive, signal, messageId });
}

// ── Keyword Resolution ─────────────────────────────────────────────────────────

/**
 * Resolve a keyword reply from a user.
 *
 * Checks cooldown, retrieves the deep-dive, and returns the response.
 *
 * @param {string} keyword — The keyword the user replied with.
 * @param {string} userId — Telegram user ID for cooldown tracking.
 * @returns {{ ok: boolean, response?: string, error?: string, cooldownRemaining?: number }}
 */
export function resolveKeyword(keyword, userId) {
  const clean = keyword.toLowerCase().replace(/[^a-z0-9_]/g, '').substring(0, 40);

  if (!_keywordRegistry.has(clean)) {
    return { ok: false, error: `Unknown keyword "${keyword}". Try a different keyword from a recent post.` };
  }

  // Cooldown check
  const cooldownKey = `${userId}:${clean}`;
  const lastUsed = _userCooldowns.get(cooldownKey) || 0;
  const elapsed = (Date.now() - lastUsed) / 1000;
  if (elapsed < USER_KEYWORD_COOLDOWN_S) {
    const remaining = Math.ceil(USER_KEYWORD_COOLDOWN_S - elapsed);
    return { ok: false, error: `Please wait ${remaining}s before using this keyword again.`, cooldownRemaining: remaining };
  }

  // Update cooldown
  _userCooldowns.set(cooldownKey, Date.now());

  // Update engagement stats
  const stats = _engagementStats.get(clean) || { triggered: 0, lastTriggeredAt: null };
  stats.triggered++;
  stats.lastTriggeredAt = Date.now();
  _engagementStats.set(clean, stats);

  const entry = _keywordRegistry.get(clean);
  return { ok: true, response: entry.response };
}

/**
 * Check if a message text matches a registered keyword.
 *
 * @param {string} text — User's message text.
 * @returns {{ matched: boolean, keyword?: string }}
 */
export function matchKeyword(text) {
  if (!text) return { matched: false };

  const clean = text.toLowerCase().trim().replace(/[^a-z0-9_]/g, '');
  if (_keywordRegistry.has(clean)) {
    return { matched: true, keyword: clean };
  }

  // Also check if the text starts with a keyword
  for (const kw of _keywordRegistry.keys()) {
    if (clean.startsWith(kw) || clean.endsWith(kw)) {
      return { matched: true, keyword: kw };
    }
  }

  return { matched: false };
}

// ── Deep-Dive Builder ──────────────────────────────────────────────────────────

/**
 * Build a deep-dive response from a signal.
 *
 * Generates structured analysis: context, key players, implications, recommendations.
 *
 * @param {object} signal — The original intel signal.
 * @returns {string} Formatted deep-dive text.
 */
export function buildDeepDive(signal) {
  if (!signal) return 'No additional information available for this topic.';

  const title = signal.title || signal.summary || 'Intel Analysis';
  const summary = signal.summary || signal.text || '';
  const source = signal.source || 'ARIA Intelligence';
  const country = signal.country || '';
  const sector = signal.sector || '';
  const severity = signal.severity || 'medium';

  let dive = `🔍 *DEEP DIVE: ${_escapeMarkdown(title)}*\n`;
  dive += `━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  // Context
  dive += `*Context*\n`;
  dive += `${_escapeMarkdown(summary.substring(0, 500))}\n\n`;

  // Key details
  const details = [];
  if (country) details.push(`📍 *Country:* ${_escapeMarkdown(country)}`);
  if (sector) details.push(`🏭 *Sector:* ${_escapeMarkdown(sector)}`);
  if (severity) details.push(`⚠️ *Severity:* ${severity.toUpperCase()}`);
  if (source) details.push(`📡 *Source:* ${_escapeMarkdown(source)}`);
  if (signal.timestamp) {
    details.push(`🕐 *Reported:* ${new Date(signal.timestamp).toLocaleString('en-GB', { timeZone: 'Europe/London' })}`);
  }

  dive += `*Key Information*\n`;
  dive += details.map(d => `• ${d}`).join('\n');
  dive += '\n\n';

  // Implications (generated from signal context)
  dive += `*Implications*\n`;
  const implications = _generateImplications(signal);
  dive += implications.map(i => `• ${_escapeMarkdown(i)}`).join('\n');
  dive += '\n\n';

  // Related keywords
  const related = _findRelatedKeywords(signal);
  if (related.length > 0) {
    dive += `*Explore Further*\n`;
    dive += `Reply with: ${related.map(k => `\`${k}\``).join(', ')}\n`;
  }

  dive += `\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n`;
  dive += `🤖 *ARIA Intelligence* — Deep Dive`;

  return dive;
}

/**
 * Build a "no deep-dive available" fallback.
 *
 * @param {string} keyword — The keyword that was used.
 * @returns {string}
 */
export function buildFallbackDeepDive(keyword) {
  return `🔍 *Deep Dive: ${_escapeMarkdown(keyword)}*\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`
    + `I don't have a pre-built deep dive for this topic yet. `
    + `Try asking me directly with \`/aria ask: your question\` for a live analysis.\n\n`
    + `━━━━━━━━━━━━━━━━━━━━━━━━━━\n🤖 *ARIA Intelligence*`;
}

// ── Poll Management ────────────────────────────────────────────────────────────

/**
 * Register a poll for result tracking.
 *
 * @param {string} pollId — Telegram poll ID.
 * @param {string} question — Poll question.
 * @param {string[]} options — Poll options.
 */
export function registerPoll(pollId, question, options) {
  _pollResults.set(pollId, {
    question,
    options: options.map(o => ({ text: o, votes: 0 })),
    closed: false,
    createdAt: Date.now(),
  });
}

/**
 * Record poll votes (called via Telegram poll_answer webhook).
 *
 * @param {string} pollId — Telegram poll ID.
 * @param {number[]} optionIds — Selected option indices.
 */
export function recordPollVote(pollId, optionIds) {
  const poll = _pollResults.get(pollId);
  if (!poll || poll.closed) return;

  for (const optId of optionIds) {
    if (poll.options[optId]) {
      poll.options[optId].votes++;
    }
  }
}

/**
 * Close a poll and return results.
 *
 * @param {string} pollId — Telegram poll ID.
 * @returns {{ ok: boolean, results?: object, error?: string }}
 */
export function closePoll(pollId) {
  const poll = _pollResults.get(pollId);
  if (!poll) return { ok: false, error: 'Poll not found' };

  poll.closed = true;
  const totalVotes = poll.options.reduce((s, o) => s + o.votes, 0);
  const winner = poll.options.reduce((best, o) => o.votes > (best?.votes || 0) ? o : best, null);

  return {
    ok: true,
    results: {
      question: poll.question,
      totalVotes,
      options: poll.options,
      winner: winner?.text || null,
      winnerVotes: winner?.votes || 0,
    },
  };
}

/**
 * Get all active polls.
 *
 * @returns {Array<{pollId:string,question:string,optionsCount:number}>}
 */
export function getActivePolls() {
  const active = [];
  for (const [pollId, poll] of _pollResults) {
    if (!poll.closed) {
      active.push({ pollId, question: poll.question, optionsCount: poll.options.length });
    }
  }
  return active;
}

// ── Engagement Analytics ───────────────────────────────────────────────────────

/**
 * Get engagement analytics for the channel.
 *
 * @returns {object}
 */
export function getEngagementStats() {
  const totalKeywords = _keywordRegistry.size;
  let totalTriggers = 0;
  const topKeywords = [];

  for (const [kw, stats] of _engagementStats) {
    totalTriggers += stats.triggered;
    topKeywords.push({ keyword: kw, triggered: stats.triggered, lastTriggeredAt: stats.lastTriggeredAt });
  }

  topKeywords.sort((a, b) => b.triggered - a.triggered);

  return {
    totalKeywords,
    totalTriggers,
    topKeywords: topKeywords.slice(0, 10),
    activePolls: getActivePolls().length,
    userCooldownsActive: _userCooldowns.size,
  };
}

/**
 * Reset all interactive state (for testing).
 */
export function _resetInteractiveState() {
  _keywordRegistry.clear();
  _userCooldowns.clear();
  _engagementStats.clear();
  _pollResults.clear();
}

// ── Internal Helpers ───────────────────────────────────────────────────────────

/**
 * Generate a keyword from a title.
 */
function _generateKeyword(title) {
  const words = String(title)
    .toLowerCase()
    .replace(/[^a-z0-9\s]/g, '')
    .split(/\s+/)
    .filter(w => w.length > 2 && !['the', 'and', 'for', 'are', 'was', 'had', 'has', 'but', 'not', 'all', 'can'].includes(w));
  if (words.length === 0) return 'analyze';
  return words.slice(0, 2).join('_');
}

/**
 * Generate implications from a signal.
 */
function _generateImplications(signal) {
  const text = (signal.text || signal.title || signal.summary || '').toLowerCase();
  const implications = [];

  if (text.includes('sanction') || text.includes('ofac') || text.includes('embargo')) {
    implications.push('Increased compliance screening required for related entities');
    implications.push('Potential supply chain disruption for sanctioned parties');
    implications.push('Review existing contracts for sanction-linked counterparties');
  }
  if (text.includes('tender') || text.includes('procurement') || text.includes('rfp')) {
    implications.push('New business development opportunity — review requirements');
    implications.push('Competitor analysis recommended for tender landscape');
    implications.push('Consider partnership or JV for local content requirements');
  }
  if (text.includes('conflict') || text.includes('instability') || text.includes('coup')) {
    implications.push('Security assessment recommended for operations in region');
    implications.push('Monitor supply chain routes for disruption risk');
    implications.push('Review force majeure clauses in existing contracts');
  }
  if (text.includes('defence') || text.includes('military') || text.includes('armed')) {
    implications.push('Defence budget allocation may signal procurement priorities');
    implications.push('Monitor offset and industrial participation requirements');
  }
  if (text.includes('oil') || text.includes('gas') || text.includes('mining')) {
    implications.push('Commodity price exposure may affect partner country budgets');
    implications.push('Infrastructure investment opportunities in extractive sector');
  }

  if (implications.length === 0) {
    implications.push('Monitor for further developments in this area');
    implications.push('Cross-reference with existing intelligence for pattern detection');
  }

  return implications;
}

/**
 * Find keywords related to a signal.
 */
function _findRelatedKeywords(signal) {
  const text = (signal.text || signal.title || signal.summary || '').toLowerCase();
  const related = [];

  for (const [kw, entry] of _keywordRegistry) {
    const entryText = (entry.signal?.text || entry.signal?.title || entry.signal?.summary || '').toLowerCase();
    // Check for shared words
    const signalWords = new Set(text.split(/\s+/).filter(w => w.length > 3));
    const entryWords = entryText.split(/\s+/).filter(w => w.length > 3);
    const shared = entryWords.filter(w => signalWords.has(w));
    if (shared.length >= 2 && kw !== _generateKeyword(signal.title || '')) {
      related.push(kw);
    }
  }

  return related.slice(0, 3);
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
