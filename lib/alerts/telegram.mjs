// Telegram Alerter - Complete Intelligence Bot with Full Message Display
// Features: Full OSINT messages, no truncation, proper formatting, watchlist alerts
// BUILD: 815f3f6 — ARKMURUS 8-section brief

console.log('[Telegram] MODULE LOADED — build 815f3f6');

import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const TELEGRAM_API = 'https://api.telegram.org';
const TELEGRAM_MAX_TEXT = 4096;

// APP_URL takes priority, then Render external URL for backward compat,
// then localhost (works on any VPS/container where bot runs alongside server)
const API_BASE_URL = process.env.APP_URL
  || process.env.RENDER_EXTERNAL_URL
  || `http://localhost:${process.env.PORT || 3117}`;

const DASHBOARD_USER = process.env.DASHBOARD_USER || 'arkmurus';
const DASHBOARD_PASS = process.env.DASHBOARD_PASS || 'Crucix2026!';
const AUTH_HEADER = `Basic ${Buffer.from(`${DASHBOARD_USER}:${DASHBOARD_PASS}`).toString('base64')}`;

// Europe/London timestamp helper — ICU-free, honours BST/GMT automatically
// UK clock: BST (UTC+1) from last Sunday March 01:00 UTC to last Sunday October 01:00 UTC
function londonTs(date = new Date(), seconds = true) {
  function ukOffset(d) {
    const y = d.getUTCFullYear();
    const lastSunMar = new Date(Date.UTC(y, 2, 31, 1, 0, 0));
    while (lastSunMar.getUTCDay() !== 0) lastSunMar.setUTCDate(lastSunMar.getUTCDate() - 1);
    const lastSunOct = new Date(Date.UTC(y, 9, 31, 1, 0, 0));
    while (lastSunOct.getUTCDay() !== 0) lastSunOct.setUTCDate(lastSunOct.getUTCDate() - 1);
    return (d >= lastSunMar && d < lastSunOct) ? 1 : 0;
  }
  const p = n => String(n).padStart(2, '0');
  const local = new Date(date.getTime() + ukOffset(date) * 3600000);
  const base = `${local.getUTCFullYear()}-${p(local.getUTCMonth()+1)}-${p(local.getUTCDate())} ${p(local.getUTCHours())}:${p(local.getUTCMinutes())}`;
  return seconds ? `${base}:${p(local.getUTCSeconds())}` : base;
}

const COMMANDS = {
  '/status':     'System health and source status',
  '/brief':      'Executive intelligence summary',
  '/full':       'Complete intelligence report',
  '/osint':      'Send all urgent OSINT messages individually',
  '/ask':        'Query current intel (e.g., /ask Angola arms deal)',
  '/search':     'Web + social + intel search (e.g., /search Wagner Angola)',
  '/predict':    'Prediction market odds (e.g., /predict iran)',
  '/contracts':  'All defense contracts (full list)',
  '/arms':       'SIPRI arms trade data (full list)',
  '/conflict':   'ACLED conflict zones (full list)',
  '/watchlist':  'Your tracked companies',
  '/add':        'Add company to watchlist',
  '/remove':     'Remove from watchlist',
  '/alerts':     'Show recent alerts',
  '/testalert':  'Send a test alert',
  '/sweep':      'Trigger manual sweep',
  '/debug':      'Show debug information',
  '/supply':     'Supply chain intelligence',
  '/supplyfull': 'Complete supply chain intelligence report',
  '/risk':       'Counterparty risk score (e.g., /risk Rosoboronexport)',
  '/events':     'Upcoming defence exhibitions & conferences',
  '/oem':        'European artillery OEM directory (e.g., /oem germany · /oem fuzes · /oem 155mm)',
  '/help':       'Show commands',
};

function formatVol(v) {
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000)    return `${(v / 1000).toFixed(0)}K`;
  return String(v);
}

// ── Signal quality helpers ────────────────────────────────────────────────────

// Patterns that indicate a signal is a fundraising/subscription append, not intel
const JUNK_PATTERNS = [
  /if (our|my) translations? (help|allows?)/i,
  /consider (supporting|donating|subscribing)/i,
  /support (us|our work|the channel)/i,
  /\bpatreon\b/i,
  /\bsubscribe\b.*\bchannel\b/i,
  /join.*telegram|follow.*channel/i,
  /donate.*link|link.*donate/i,
];

// Matches DD.MM.YY or MM/DD/YY style dates where 2-digit year = 2020–2024 (stale)
// e.g. "02.28.22", "15.03.23", "11/04/24"
const STALE_DATE_RE = /\b\d{1,2}[./]\d{1,2}[./](2[0-4])\b/;

// Current year's 2-digit value — signals dated ≥2 years ago are considered stale
const CURRENT_YR2 = new Date().getFullYear() % 100; // e.g. 26 in 2026

/**
 * Return true if the signal text contains a clearly stale timestamp
 * (a date whose 2-digit year is more than 1 year behind now).
 */
function hasStaleDate(text) {
  const m = text.match(STALE_DATE_RE);
  if (!m) return false;
  const yr = parseInt(m[1], 10); // e.g. 22
  return (CURRENT_YR2 - yr) >= 2; // stale if ≥2 years behind
}

/**
 * Strip donation/subscription suffixes and clean up the text.
 * Returns null if the entire signal is junk.
 */
function cleanSignalText(raw) {
  if (!raw) return null;
  let text = raw.trim();

  // Cut at the first junk pattern occurrence
  for (const p of JUNK_PATTERNS) {
    const m = text.search(p);
    if (m > 0) text = text.substring(0, m).trim();
  }

  // Strip trailing ellipsis artifacts
  text = text.replace(/\s*\(?\.\.\.\)?$/, '').trim();

  // Usable length after removing emoji and whitespace
  const usable = text.replace(/[\p{Emoji}\s]/gu, '');
  if (usable.length < 20) return null;

  return text;
}

/**
 * Return true if the signal should be dropped entirely.
 */
function isJunkSignal(text) {
  if (!text) return true;
  // Entirely junk (donation etc as the primary content)
  if (JUNK_PATTERNS.some(p => p.test(text.substring(0, 60)))) return true;
  // Contains a clearly stale date (e.g. a 2022-era post being re-shared)
  if (hasStaleDate(text)) return true;
  // After cleaning, nothing useful remains
  const cleaned = cleanSignalText(text);
  return !cleaned;
}

/**
 * Truncate at the last complete sentence boundary before maxLen.
 * Falls back to the last word boundary, appending '…'.
 */
function smartTruncate(text, maxLen = 380) {
  if (!text || text.length <= maxLen) return text || '';
  const slice = text.substring(0, maxLen);
  // Prefer ending after a full stop, exclamation, or question mark
  const lastSentence = Math.max(
    slice.lastIndexOf('. '),
    slice.lastIndexOf('.\n'),
    slice.lastIndexOf('! '),
    slice.lastIndexOf('? '),
  );
  if (lastSentence > maxLen * 0.55) return text.substring(0, lastSentence + 1).trim();
  // Fall back to last word boundary
  const lastSpace = slice.lastIndexOf(' ');
  if (lastSpace > maxLen * 0.55) return slice.substring(0, lastSpace).trim() + '…';
  return slice.trim() + '…';
}

/**
 * Within-batch near-duplicate suppression.
 * If two signals share ≥4 significant words AND ≥50% keyword overlap,
 * keep only the longer (more complete) one.
 */
function deduplicateBatch(signals) {
  const STOPWORDS = new Set([
    'that', 'with', 'from', 'have', 'this', 'they', 'their', 'been', 'were',
    'will', 'more', 'also', 'into', 'about', 'after', 'which', 'would', 'there',
    'said', 'says', 'according',
  ]);

  function keyWords(text) {
    return new Set(
      (text || '').toLowerCase()
        .replace(/[^\w\s]/g, ' ')
        .split(/\s+/)
        .filter(w => w.length > 4 && !STOPWORDS.has(w))
    );
  }

  const kept = [];
  for (const sig of signals) {
    const sigWords = keyWords(sig.text);
    const dupIdx = kept.findIndex(k => {
      const kWords = keyWords(k.text);
      const overlap = [...sigWords].filter(w => kWords.has(w)).length;
      const minSize = Math.min(sigWords.size, kWords.size);
      return minSize >= 4 && overlap >= 4 && (overlap / minSize) >= 0.5;
    });

    if (dupIdx === -1) {
      kept.push(sig);
    } else {
      // Keep whichever has more usable text
      if ((sig.text || '').length > (kept[dupIdx].text || '').length) {
        kept[dupIdx] = sig;
      }
    }
  }
  return kept;
}

export class TelegramAlerter {
  constructor({ botToken, chatId, port = 3117 }) {
    this.botToken = botToken;
    const rawId = String(chatId || '').trim();
    // Seenode sometimes strips the leading minus from negative chat IDs.
    // Supergroup/channel IDs are always negative — restore it if missing.
    this.chatId = rawId.match(/^\d{10,}$/) ? `-${rawId}` : rawId;
    console.log(`[Telegram] chatId: "${this.chatId}" (raw="${rawId}")`);
    this.port = port;
    this._alertHistory = [];
    this._lastUpdateId = 0;
    this._commandHandlers = {};
    this._pollingInterval = null;
    this._pollBusy = false;
    this._botUsername = null;
    this._watchlist = new Map();
    this._previousData = null;
    this._alertIntervalMs = 3 * 60 * 60 * 1000; // 3 hours

    this._cache = { data: null, timestamp: 0, ttl: 30000 };

    // Flash alert rate-limiting: max 3 per hour for critical signals
    this._flashCount     = 0;
    this._flashHourStart = Date.now();
    this._flashSeenTexts = new Set(); // dedup flash alerts within the hour window

    // Persist lastAlertTime to disk + Redis so Render redeploys don't reset the 3h gap
    this._lastAlertTime = this._loadLastAlertTime();
    this._alertTimeInitialized = false; // Redis check deferred to first sweep (async)

    this._loadWatchlist();

    const allowedUsers = process.env.TELEGRAM_ALLOWED_USERS;
    this._allowedUsers = allowedUsers
      ? new Set(allowedUsers.split(',').map(id => id.trim()))
      : new Set([this.chatId]);
    console.log(`[Telegram] Allowed users: ${Array.from(this._allowedUsers).join(', ')}`);
  }

  get isConfigured() {
    return !!(this.botToken && this.chatId);
  }

  async _getCachedData() {
    const now = Date.now();
    if (this._cache.data && (now - this._cache.timestamp) < this._cache.ttl) {
      return this._cache.data;
    }
    try {
      const response = await fetch(`${API_BASE_URL}/api/data`, {
        headers: { 'Authorization': AUTH_HEADER },
      });
      if (!response.ok) {
        // Server not ready yet — return stale cache without updating it
        return this._cache.data || null;
      }
      const data = await response.json();
      this._cache.data = data;
      this._cache.timestamp = now;
      return data;
    } catch (error) {
      console.error('[Telegram] Cache fetch error:', error.message);
      return this._cache.data || null;
    }
  }

  _getWatchlistFilePath() {
    return join(process.cwd(), `watchlist_${this.chatId}.json`);
  }

  _loadWatchlist() {
    try {
      const filePath = this._getWatchlistFilePath();
      if (existsSync(filePath)) {
        const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
        this._watchlist.set(this.chatId, parsed.companies || []);
      } else {
        this._watchlist.set(this.chatId, []);
      }
    } catch (err) {
      this._watchlist.set(this.chatId, []);
    }
  }

  _saveWatchlist() {
    try {
      writeFileSync(
        this._getWatchlistFilePath(),
        JSON.stringify({ companies: this._watchlist.get(this.chatId) || [] }, null, 2),
        'utf8'
      );
    } catch (err) {}
  }

  _alertTimePath() {
    return join(process.cwd(), 'runs', 'last_alert_time.json');
  }

  _loadLastAlertTime() {
    try {
      const f = this._alertTimePath();
      if (existsSync(f)) {
        const { t } = JSON.parse(readFileSync(f, 'utf8'));
        if (typeof t === 'number') return t;
      }
    } catch {}
    // No saved time — treat as if a digest just fired so we don't blast on every restart/redeploy
    return Date.now();
  }

  _saveLastAlertTime(t) {
    try { writeFileSync(this._alertTimePath(), JSON.stringify({ t }), 'utf8'); } catch {}
    // Also persist to Redis so Render redeploys don't reset the 3h window
    this._saveAlertTimeRedis(t).catch(() => {});
  }

  // ── Redis helpers for alert timer (Upstash REST API) ─────────────────────────
  _redisConfigured() {
    return !!(process.env.UPSTASH_REDIS_URL && process.env.UPSTASH_REDIS_TOKEN);
  }

  async _loadAlertTimeRedis() {
    if (!this._redisConfigured()) return null;
    try {
      const res = await fetch(`${process.env.UPSTASH_REDIS_URL}/get/crucix:lastAlertTime`, {
        headers: { Authorization: `Bearer ${process.env.UPSTASH_REDIS_TOKEN}` },
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) return NaN;
      const data = await res.json();
      const val = data.result ? parseInt(data.result, 10) : NaN;
      return isNaN(val) ? null : val;
    } catch { return null; }
  }

  async _saveAlertTimeRedis(t) {
    if (!this._redisConfigured()) return;
    try {
      await fetch(`${process.env.UPSTASH_REDIS_URL}/set/crucix:lastAlertTime`, {
        method: 'POST',
        headers: {
          Authorization:  `Bearer ${process.env.UPSTASH_REDIS_TOKEN}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify([String(t), 'EX', 90 * 24 * 3600]),
        signal: AbortSignal.timeout(5000),
      });
    } catch {}
  }

  async _handleWatchlist(args) {
    const parts = args.trim().split(/\s+/);
    const action = parts[0]?.toLowerCase();
    const company = parts.slice(1).join(' ');
    const currentList = this._watchlist.get(this.chatId) || [];

    if (action === 'list' || (!action && !company)) {
      if (currentList.length === 0) return `Your watchlist is empty\n\nUse /add [company] to add companies.`;
      let message = `YOUR WATCHLIST\n\n`;
      currentList.forEach((c, i) => { message += `${i + 1}. ${c}\n`; });
      message += `\nUse /search [query] to search web, news, social + live intel`;
      return message;
    }

    if (action === 'add' && company) {
      if (!currentList.includes(company)) {
        currentList.push(company);
        this._watchlist.set(this.chatId, currentList);
        this._saveWatchlist();
        this._addAlert('watchlist', `Added ${company} to watchlist`, 'info');
        return `Added ${company} to watchlist.`;
      }
      return `${company} already in watchlist.`;
    }

    if (action === 'remove' && company) {
      const index = currentList.indexOf(company);
      if (index !== -1) {
        currentList.splice(index, 1);
        this._watchlist.set(this.chatId, currentList);
        this._saveWatchlist();
        this._addAlert('watchlist', `Removed ${company} from watchlist`, 'info');
        return `Removed ${company} from watchlist.`;
      }
      return `${company} not found.`;
    }

    return `Use /add [company], /remove [company], or /watchlist`;
  }

  _addAlert(type, message, severity = 'info') {
    this._alertHistory.unshift({ type, message, severity, timestamp: Date.now() });
    if (this._alertHistory.length > 50) this._alertHistory = this._alertHistory.slice(0, 50);
  }

  async sendMessage(message, opts = {}) {
    if (!this.isConfigured) return { ok: false };
    const chatId = opts.chatId ?? this.chatId;
    const MAX_LENGTH = 4000;

    if (message.length <= MAX_LENGTH) {
      try {
        const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: chatId,
            text: message,
            parse_mode: 'Markdown',
            disable_web_page_preview: true,
            ...(opts.replyToMessageId ? { reply_to_message_id: opts.replyToMessageId } : {}),
          }),
          signal: AbortSignal.timeout(15000),
        });
        if (!res.ok) {
          const errText = await res.text().catch(() => '<unreadable>');
          let errDesc = errText;
          try { errDesc = JSON.parse(errText)?.description || errText; } catch {}
          console.error(`[Telegram] Send failed (HTTP ${res.status}): ${errDesc} | chat_id=${chatId}`);
          // Retry once without parse_mode in case of Markdown formatting error
          if (res.status === 400) {
            try {
              const retryRes = await fetch(`${TELEGRAM_API}/bot${this.botToken}/sendMessage`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ chat_id: chatId, text: message.replace(/[*_`[\]]/g, ''), disable_web_page_preview: true }),
                signal: AbortSignal.timeout(15000),
              });
              if (retryRes.ok) {
                console.log('[Telegram] Sent successfully (plain text fallback)');
                const retryData = await retryRes.json();
                return { ok: true, messageId: retryData.result?.message_id };
              }
            } catch {}
          }
          return { ok: false };
        }
        const data = await res.json();
        return { ok: true, messageId: data.result?.message_id };
      } catch (err) {
        console.error('[Telegram] Send error:', err.message);
        return { ok: false };
      }
    }

    console.log('[Telegram] Message too long, splitting...');
    // Split only on section dividers (━) or major section emoji headers — NOT on ⚔️/🔫 which appear in message bodies
    const sections = message.split(/(?=━━|📋|📊|📡)/);
    const chunks = [];
    let currentChunk = '';

    for (const section of sections) {
      if ((currentChunk + section).length > MAX_LENGTH) {
        if (currentChunk) chunks.push(currentChunk);
        currentChunk = section;
      } else {
        currentChunk += section;
      }
    }
    if (currentChunk) chunks.push(currentChunk);

    for (let i = 0; i < chunks.length; i++) {
      try {
        await fetch(`${TELEGRAM_API}/bot${this.botToken}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            chat_id: chatId,
            text: chunks[i],
            parse_mode: 'Markdown',
            disable_web_page_preview: true,
            ...(opts.replyToMessageId && i === 0 ? { reply_to_message_id: opts.replyToMessageId } : {}),
          }),
          signal: AbortSignal.timeout(15000),
        });
      } catch (err) {
        console.error(`[Telegram] Chunk ${i + 1} error:`, err.message);
      }
      await new Promise(r => setTimeout(r, 500));
    }

    return { ok: true, chunks: chunks.length };
  }

  async sendAlert(message, type = 'general') {
    this._addAlert(type, message, 'high');
    return this.sendMessage(message);
  }

  async _handleCompanySearch(query) {
    if (!query?.trim()) return `Intel Search\n\nProvide a query.\nExample: /search Wagner Group Angola`;
    try {
      const { runSearch } = await import('../search/engine.mjs');
      const cachedData = await this._getCachedData();
      const result = await runSearch(query, cachedData);
      const { results, totals } = result;

      const total = Object.values(totals).reduce((a, b) => a + b, 0);
      if (total === 0) return `No results found for *"${query}"*.\n\nTry broader terms or use /ask for live intel only.`;

      const ts = londonTs(new Date(), false);
      let msg = `🔍 *SEARCH: ${query.toUpperCase()}*\n_${ts} London · ${total} results_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // Live intel first (highest relevance)
      if (results.intel?.length > 0) {
        msg += `*LIVE INTEL*\n`;
        for (const r of results.intel.slice(0, 3)) {
          const icon = r.priority === 'critical' ? '🔴' : r.priority === 'high' ? '🟠' : '🔵';
          msg += `${icon} [${r.source}] ${r.title.substring(0, 160)}\n`;
        }
        msg += `\n`;
      }

      // Top news
      if (results.news?.length > 0) {
        msg += `*NEWS*\n`;
        for (const r of results.news.slice(0, 3)) {
          msg += `📰 ${r.title.substring(0, 140)}\n`;
        }
        msg += `\n`;
      }

      // Company data
      if (results.companies?.length > 0) {
        msg += `*COMPANY REGISTRY*\n`;
        for (const c of results.companies.slice(0, 3)) {
          msg += `🏢 ${c.title} — ${c.snippet}\n`;
        }
        msg += `\n`;
      }

      // Wikipedia reference
      if (results.reference?.length > 0) {
        const wiki = results.reference[0];
        msg += `*REFERENCE*\n${wiki.snippet.substring(0, 220)}\n\n`;
      }

      // Social pulse
      if (results.social?.length > 0) {
        msg += `*SOCIAL* (Reddit)\n`;
        for (const r of results.social.slice(0, 2)) {
          msg += `💬 ${r.title.substring(0, 120)} · ${r.source}\n`;
        }
        msg += `\n`;
      }

      msg += `_Full results at /search.html?q=${encodeURIComponent(query)}_`;
      return msg;
    } catch (error) {
      return `Search failed: ${error.message}`;
    }
  }

  async _handleBrief() {
    console.log('[Telegram] _handleBrief() called — v18cbcf2 ARKMURUS 8-section format');
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Intelligence data is loading — please try again in 60 seconds.`;

      const ts  = londonTs();
      const ds  = data.delta?.summary || {};
      const dir = ds.direction;
      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy || {};
      const corrs = data.correlations || [];
      const critCorrs = corrs.filter(c => c.severity === 'critical' || c.severity === 'high');

      let msg = `*ARKMURUS INTELLIGENCE BRIEF*\n_${ts} London_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // ── 1. LEVERAGEABLE IDEAS ─────────────────────────────────────────────────
      const ideas = data.ideas || [];
      if (ideas.length > 0) {
        msg += `*1. LEVERAGEABLE IDEAS*\n`;
        for (const idea of ideas.slice(0, 3)) {
          const thesis    = idea.thesis || idea.title || idea.text || String(idea);
          const instrument = idea.instrument || idea.sector || '';
          const horizon   = idea.horizon || idea.timeHorizon || '';
          const conf      = idea.confidence || '';
          const catalyst  = idea.catalyst || idea.catalysts?.[0] || '';
          msg += `▸ *${thesis.substring(0, 120)}*\n`;
          if (instrument) msg += `  Instrument: ${instrument}`;
          if (horizon)    msg += ` · Horizon: ${horizon}`;
          if (conf)       msg += ` · Confidence: ${conf}`;
          msg += `\n`;
          if (catalyst)   msg += `  Catalyst: ${catalyst.toString().substring(0, 100)}\n`;
          msg += `\n`;
        }
        if (ideas.length > 3) msg += `_+ ${ideas.length - 3} more ideas in /full_\n\n`;
      } else {
        // Derive ideas from top correlation + supply chain when LLM not available
        const topCorr = critCorrs[0];
        const topAlert = (data.supplyChain?.metrics?.alerts || []).find(a => a.type === 'critical');
        if (topCorr || topAlert) {
          msg += `*1. LEVERAGEABLE IDEAS*\n`;
          if (topCorr) {
            msg += `▸ *${topCorr.region} — multi-source ${topCorr.severity} signal*\n`;
            msg += `  Monitor exposure to ${topCorr.region} counterparties and contracts.\n`;
            msg += `  Horizon: 24–72h · Catalyst: ${topCorr.topSignals?.[0]?.text?.substring(0, 80) || 'see /full'}\n\n`;
          }
          if (topAlert) {
            msg += `▸ *Supply chain stress: ${topAlert.message?.substring(0, 100)}*\n`;
            msg += `  Review procurement timelines and alternative sourcing.\n\n`;
          }
          msg += `_Enable LLM (ANTHROPIC_API_KEY) for full trade ideas with instruments and invalidation criteria._\n\n`;
        }
      }

      // ── 2. EXECUTIVE THESIS ───────────────────────────────────────────────────
      msg += `*2. EXECUTIVE THESIS*\n`;
      const dirLine = dir === 'risk-off' ? '📉 Risk-off — global stress indicators elevated'
                    : dir === 'risk-on'  ? '📈 Risk-on — conditions broadly constructive'
                    : '↔️ Mixed signals — no dominant regime forming yet';
      msg += `${dirLine}.\n`;
      if (critCorrs.length > 0) {
        const regions = critCorrs.slice(0, 3).map(c => c.region).join(', ');
        msg += `Concurrent stress across *${regions}* suggests coordinated pressure, not isolated events.\n`;
      }
      if (ds.criticalChanges > 0) {
        msg += `*${ds.criticalChanges}* indicators crossed critical thresholds this sweep.\n`;
      }
      if (vix?.value > 25) {
        msg += `VIX at *${vix.value}* confirms elevated market anxiety — reduce leverage on new positions.\n`;
      }
      msg += `\n`;

      // ── 3. SITUATION AWARENESS ────────────────────────────────────────────────
      if (critCorrs.length > 0) {
        msg += `*3. SITUATION AWARENESS*\n`;
        for (const c of critCorrs.slice(0, 4)) {
          const badge = c.severity === 'critical' ? '🔴' : '🟠';
          const top   = c.topSignals?.[0]?.text || '';
          msg += `${badge} *${c.region}* [${(c.sourceCount || c.sources?.length || 1)} sources]\n`;
          if (top) msg += `  └ ${top.substring(0, 140)}\n`;
        }
        msg += `\n`;
      }

      // OSINT top signals
      const urgent = data.tg?.urgent || [];
      if (urgent.length > 0) {
        msg += `📡 *OSINT (${urgent.length} signals — top 2)*\n`;
        for (const s of urgent.slice(0, 2)) {
          msg += `• *[${s.channel || 'OSINT'}]* ${(s.text || '').trim().replace(/\n+/g, ' ').substring(0, 160)}\n`;
        }
        msg += `\n`;
      }

      // ── 4. PATTERN RECOGNITION ────────────────────────────────────────────────
      const multiSourceCorrs = corrs.filter(c => (c.sourceCount || c.sources?.length || 0) >= 3);
      if (multiSourceCorrs.length > 0) {
        msg += `*4. PATTERN RECOGNITION*\n`;
        for (const c of multiSourceCorrs.slice(0, 2)) {
          msg += `🔗 *${c.region}* — ${c.sourceCount || c.sources?.length} independent sources converging`;
          const sig2 = c.topSignals?.[1]?.text;
          if (sig2) msg += `: "${sig2.substring(0, 100)}"`;
          msg += `. Pattern: ${c.severity === 'critical' ? 'strengthening' : 'stable'}.\n`;
        }
        msg += `\n`;
      }

      // ── 5. HISTORICAL PARALLELS ───────────────────────────────────────────────
      // Rule-based: map active signal patterns to known historical analogues
      {
        const parallels = [];
        const allText = [
          ...critCorrs.map(c => `${c.region} ${c.topSignals?.map(s => s.text).join(' ')}`),
          ...(data.defenseNews?.signals || []).map(s => s.text || ''),
          ...(data.procurementTenders?.signals || []).map(s => s.text || ''),
        ].join(' ').toLowerCase();

        if ((allText.includes('angola') || allText.includes('mozambique') || allText.includes('lusophone')) &&
            (allText.includes('arms') || allText.includes('military') || allText.includes('contract') || allText.includes('tender'))) {
          parallels.push({
            label: 'Lusophone Africa arms cycle',
            match: 'Weapons procurement activity in Portuguese-speaking Africa',
            parallel: 'Post-2002 Angola reconstruction arms import surge; 2013–16 Mozambique naval expansion before Cabo Delgado',
            diff: 'Export control environment stricter; Chinese competition for contracts now primary factor',
            position: 'Early procurement cycle — opportunities exist before contract award',
          });
        }
        if (allText.includes('sanction') && (allText.includes('africa') || allText.includes('sahel') || allText.includes('mali') || allText.includes('niger') || allText.includes('burkina'))) {
          parallels.push({
            label: 'Sahel arms embargo pattern',
            match: 'Sanctions + arms restrictions in Sahel',
            parallel: 'Post-2012 Mali coup: ECOWAS embargo → Wagner entry 2021 → French exit 2023',
            diff: 'Current junta governments more resistant; US AFRICOM footprint reduced',
            position: 'Embargo creates gray-market premium; legitimate suppliers squeezed out',
          });
        }
        if (critCorrs.length >= 2 && dir === 'risk-off') {
          parallels.push({
            label: 'Multi-region risk-off cluster',
            match: `${critCorrs.length} concurrent critical regions with risk-off macro direction`,
            parallel: 'Q4 2023 (Gaza + Ukraine + Red Sea) and Q1 2022 (Russia invasion + China lockdowns)',
            diff: dir === 'risk-off' ? 'Current regime shows similar breadth but magnitude unconfirmed' : '',
            position: 'Historical playbook: commodities + defense sector outperform in first 60 days',
          });
        }
        if (allText.includes('fms') || allText.includes('foreign military sale') || allText.includes('dsca')) {
          parallels.push({
            label: 'US FMS notification cycle',
            match: 'Active DSCA/FMS notifications detected',
            parallel: 'Pre-delivery FMS notifications precede contract finalisation by 30–120 days (historical average)',
            diff: 'Current US political environment may slow Congressional approval',
            position: 'Notification → approval → LOA signing window is the Arkmurus offset opportunity',
          });
        }

        if (parallels.length > 0) {
          msg += `*5. HISTORICAL PARALLELS*\n`;
          for (const p of parallels.slice(0, 2)) {
            msg += `📜 *${p.label}*\n`;
            msg += `  Match: ${p.match}\n`;
            msg += `  Rhymes with: ${p.parallel.substring(0, 130)}\n`;
            if (p.diff) msg += `  Differs: ${p.diff.substring(0, 100)}\n`;
            msg += `  Position: _${p.position.substring(0, 120)}_\n\n`;
          }
        }
      }

      // ── 5b. LUSOPHONE & AFRICA DEFENCE SIGNALS ────────────────────────────────
      {
        const lusiSigs = (data.defenseNews?.signals || []).filter(s => s.text?.toLowerCase().includes('lusoph') ||
          ['angola','mozambique','guinea-bissau','cape verde','são tomé','sao tome'].some(kw => s.text?.toLowerCase().includes(kw)));
        const procLusi  = (data.procurementTenders?.lusophone || []).slice(0, 3);
        const procAfr   = (data.procurementTenders?.africa || []).filter(i => !procLusi.includes(i)).slice(0, 2);

        if (lusiSigs.length > 0 || procLusi.length > 0 || procAfr.length > 0) {
          msg += `*LUSOPHONE & AFRICA DEFENCE*\n`;
          for (const s of lusiSigs.slice(0, 3)) {
            msg += `🇵🇹 ${(s.text || '').substring(0, 140)}\n`;
          }
          for (const t of procLusi) {
            msg += `📋 [TENDER] ${(t.title || t.text || '').substring(0, 130)}\n`;
            if (t.source) msg += `  _Source: ${t.source}_\n`;
          }
          for (const t of procAfr) {
            msg += `📋 [Africa] ${(t.title || t.text || '').substring(0, 130)}\n`;
          }
          msg += `\n`;
        }
      }

      // ── 6. MARKET & ASSET IMPLICATIONS ───────────────────────────────────────
      const hasMarketData = vix?.value || oil.brent;
      if (hasMarketData) {
        msg += `*6. MARKET & ASSET IMPLICATIONS*\n`;
        if (vix?.value) msg += `• Volatility (VIX): *${vix.value}* — ${vix.value > 30 ? '🔴 extreme stress' : vix.value > 20 ? '🟠 elevated' : '🟢 normal'}\n`;
        if (oil.brent)  msg += `• Brent crude: *$${oil.brent}* · WTI: *$${oil.wti || '--'}*\n`;
        const scMats = (data.supplyChain?.metrics?.rawMaterials || []).filter(m => m.risk === 'critical' || m.risk === 'high').slice(0, 3);
        for (const m of scMats) msg += `• ${m.name}: *${m.price}* (${m.change}) — ${m.impact}\n`;
        msg += `\n`;
      }

      // ── 7. DECISION BOARD ─────────────────────────────────────────────────────
      msg += `*7. DECISION BOARD*\n`;
      const topIdea = ideas[0];
      msg += `• Best long: ${topIdea ? topIdea.instrument || topIdea.thesis?.substring(0, 60) : 'await multi-source confirmation'}\n`;
      const sanctions = data.opensanctions?.preDesignation || [];
      msg += `• Best hedge: ${sanctions.length > 0 ? `Exposure review — ${sanctions.length} pre-designation signal(s)` : dir === 'risk-off' ? 'Gold / defensive assets' : 'Monitor VIX for entry'}\n`;
      const topWatch = critCorrs[0];
      msg += `• Watch: ${topWatch ? `${topWatch.region} — next 24–72h` : 'No critical zones currently'}\n`;
      if (ds.totalChanges > 0) msg += `• Monitor: ${ds.totalChanges} delta changes — confirm or reverse in next sweep\n`;
      msg += `\n`;

      // ── 8. SOURCE INTEGRITY ───────────────────────────────────────────────────
      const srcOk    = data.meta?.sourcesOk || 0;
      const srcTotal = data.meta?.sourcesQueried || 0;
      const srcFail  = data.meta?.sourcesFailed || 0;
      msg += `*8. SOURCE INTEGRITY*\n`;
      msg += `${srcOk}/${srcTotal} sources delivered data`;
      if (srcFail > 0) msg += ` · ${srcFail} degraded`;
      const hasLLM = ideas.length > 0 && data.ideasSource === 'llm';
      msg += `\nThesis basis: ${hasLLM ? 'LLM synthesis + hard data' : 'hard data only — LLM not active'}`;
      msg += `\n`;

      msg += `\n━━━━━━━━━━━━━━━━━━━━━━━━\n`;
      msg += `_/full · /osint · /supply · /arms · /predict · /ask [topic]_`;

      return msg;
    } catch (error) {
      return `Brief failed: ${error.message}`;
    }
  }

  async _handleFullReport() {
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Loading data — please retry in 60 seconds.`;

      const ts  = londonTs();
      const ds  = data.delta?.summary || {};

      let msg = `*COMPLETE INTELLIGENCE REPORT*\n`;
      msg += `_${ts} London | Arkmurus Crucix v2_\n`;
      msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // Markets
      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy || {};
      msg += `*📊 FINANCIAL MARKETS*\n`;
      msg += `VIX: *${vix?.value || '--'}* | Brent: *$${oil.brent || '--'}* | WTI: *$${oil.wti || '--'}*\n`;
      if (ds.direction) msg += `Market direction: *${ds.direction.toUpperCase()}* (${ds.totalChanges || 0} changes, ${ds.criticalChanges || 0} critical)\n`;
      msg += `\n`;

      // Regional correlations
      const corrs = data.correlations || [];
      if (corrs.length > 0) {
        msg += `*🌍 REGIONAL THREAT ASSESSMENT*\n`;
        for (const c of corrs.slice(0, 5)) {
          const badge = c.severity === 'critical' ? '🔴' : c.severity === 'high' ? '🟠' : '🔵';
          msg += `${badge} *${c.region}* [${c.severity.toUpperCase()}]\n`;
          const sigs = c.topSignals || [];
          if (sigs.length > 0) msg += `  └ ${sigs[0].text?.substring(0, 120)}\n`;
        }
        msg += `\n`;
      }

      // Full OSINT
      const urgent = data.tg?.urgent || [];
      if (urgent.length > 0) {
        msg += `*📡 OSINT SIGNALS (${urgent.length})*\n`;
        for (const s of urgent.slice(0, 5)) {
          const ch   = s.channel || 'OSINT';
          const text = (s.text || '').trim().replace(/\n+/g, ' ');
          const views = s.views ? ` _(${formatVol(s.views)} views)_` : '';
          msg += `\n*[${ch}]${views}*\n${text.substring(0, 300)}\n`;
        }
        msg += `\n`;
      }

      // Defense contracts
      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        msg += `*🔫 DEFENSE CONTRACTS (${contracts.length})*\n`;
        for (const c of contracts.slice(0, 5)) {
          msg += `• ${c.title}\n`;
          if (c.content) msg += `  _${c.content.substring(0, 100)}_\n`;
        }
        msg += `\n`;
      }

      // Supply chain
      const sc = data.supplyChain?.metrics || {};
      const scAlerts = (sc.alerts || []).filter(a => a.type === 'critical' || a.type === 'high');
      if (scAlerts.length > 0) {
        msg += `*⚙️ SUPPLY CHAIN ALERTS*\n`;
        for (const a of scAlerts.slice(0, 3)) msg += `• ${a.message}\n`;
        msg += `\n`;
      }

      // Sanctions
      const sanctions = data.opensanctions?.preDesignation || [];
      if (sanctions.length > 0) {
        msg += `*🚫 PRE-DESIGNATION SIGNALS (${sanctions.length})*\n`;
        for (const s of sanctions.slice(0, 3)) msg += `• ${s.name} — ${s.datasets?.join(', ')}\n`;
        msg += `\n`;
      }

      msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
      msg += `_Use /osint for full individual OSINT messages · /supply for materials detail_`;

      return msg;
    } catch (error) {
      return `Report failed: ${error.message}`;
    }
  }

  async _sendFullOSINT() {
    try {
      const data = await this._getCachedData();
      const urgent = data?.tg?.urgent || [];
      if (urgent.length === 0) return `No urgent OSINT messages in current sweep.`;

      // Send header then each item individually — no trailing return string
      // (returning a string causes _handleMessage to send it as an extra message)
      await this.sendMessage(`📡 *URGENT OSINT — ${urgent.length} messages*\nSending all now...`);

      for (let i = 0; i < urgent.length; i++) {
        const s = urgent[i];
        const text = (s.text || String(s)).trim();
        const channel = s.channel || 'Unknown';
        const views = s.views ? `👁 ${s.views.toLocaleString()} views\n` : '';
        const msg = `*${i + 1}/${urgent.length} · ${channel}*\n${views}\n${text}`;
        await this.sendMessage(msg);
        await new Promise(r => setTimeout(r, 500)); // 500ms gap avoids Telegram rate limit
      }

      await this.sendMessage(`✅ All ${urgent.length} OSINT messages sent.\n_Use /brief for synthesised analysis._`);
      return null; // prevent _handleMessage from sending an extra message
    } catch (error) {
      return `Error: ${error.message}`;
    }
  }

  async _handleContracts() {
    try {
      const data = await this._getCachedData();
      const contracts = data.defense?.updates || [];
      if (contracts.length === 0) return `DEFENSE CONTRACTS\n\nNo contracts.`;
      let message = `DEFENSE CONTRACTS (${contracts.length})\n`;
      contracts.forEach((c, i) => {
        message += `${i + 1}. ${c.title}\n`;
        if (c.content) message += `   ${c.content}\n\n`;
      });
      return message;
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _handleEvents(args) {
    try {
      const data = await this._getCachedData();
      const eventsData = data?.defenseEvents;
      if (!eventsData || !eventsData.upcoming?.length) {
        return `*DEFENCE EVENTS*\n\nNo upcoming events in calendar.`;
      }

      const filter = (args || '').toLowerCase().trim();
      const EUROPE = ['FR','GB','DE','PL','SE','BE','CZ','BG','IT','ES','GR','NL','NO','DK','FI','AT','HU','RO','PT'];
      let events = eventsData.upcoming;
      if (filter === 'high')   events = events.filter(e => e.priority === 'high');
      if (filter === 'europe') events = events.filter(e => EUROPE.includes(e.country));
      if (filter === 'africa') events = events.filter(e => e.region === 'Africa');

      let msg = `*UPCOMING DEFENCE EVENTS*`;
      if (filter) msg += ` _(${filter})_`;
      msg += `\n\n`;

      for (const e of events.slice(0, 12)) {
        const dot = e.daysUntil <= 14 ? '🔴' : e.daysUntil <= 60 ? '🟡' : '🟢';
        const star = e.priority === 'high' ? ' ⭐' : '';
        msg += `${dot}${star} *${e.name}*\n`;
        msg += `   📍 ${e.location}\n`;
        msg += `   📅 ${e.startDate} → ${e.endDate} _(${e.daysUntil}d)_\n`;
        if (e.notes) msg += `   _${e.notes.substring(0, 120)}_\n`;
        msg += `\n`;
      }

      msg += `_Filters: /events high · /events europe · /events africa_`;
      return msg;
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _handleArms() {
    try {
      const data = await this._getCachedData();
      const arms = data.sipri?.updates || [];
      if (arms.length === 0) return `ARMS TRADE\n\nNo data.`;
      let message = `ARMS TRADE (SIPRI)\n`;
      arms.forEach((a, i) => {
        message += `${i + 1}. ${a.title}\n`;
        if (a.content) message += `   ${a.content}\n\n`;
      });
      return message;
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _handleConflict() {
    try {
      const data = await this._getCachedData();
      const conflicts = data.acled?.deadliestEvents || [];
      if (conflicts.length === 0) return `CONFLICT ZONES\n\nNo data.`;
      let message = `CONFLICT ZONES (ACLED)\n`;
      conflicts.forEach((c, i) => {
        message += `${i + 1}. ${c.location || 'Unknown'}: ${c.fatalities || 0} fatalities\n`;
      });
      return message;
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _handleAlerts() {
    if (this._alertHistory.length === 0) return `No alerts yet. Try /testalert.`;
    let message = `ALERT HISTORY (${this._alertHistory.length})\n\n`;
    this._alertHistory.slice(0, 10).forEach((alert, i) => {
      const time = londonTs(new Date(alert.timestamp));
      message += `${i + 1}. ${time} - ${alert.message}\n\n`;
    });
    return message;
  }

  async _testAlert() {
    const testMessage = `TEST ALERT\n\nThis is a test alert from Crucix.\n\n${londonTs()} London`;
    await this.sendAlert(testMessage, 'test');
    return `Test alert sent!`;
  }

  async _debugData() {
    try {
      const data = await this._getCachedData();
      return `DEBUG\n\nDefense: ${data?.defense?.updates?.length || 0}\nSIPRI: ${data?.sipri?.updates?.length || 0}\nOSINT: ${data?.tg?.urgent?.length || 0}\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0}\nSupply Chain: ${data?.supplyChain?.updates?.length || 0}\nPolymarket: ${data?.polymarket?.markets?.length || 0} markets`;
    } catch (error) {
      return `Debug error: ${error.message}`;
    }
  }

  async triggerManualSweep() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/sweep`, {
        method: 'POST',
        headers: { 'Authorization': AUTH_HEADER },
      });
      const result = await response.json();
      return result.success ? 'Manual sweep triggered.' : 'Sweep failed.';
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _handleSupplyChain() {
    try {
      const data   = await this._getCachedData();
      const supply = data?.supplyChain || data?.sources?.['Supply Chain'];
      if (!supply) return `⏳ Supply chain data not yet available — awaiting next sweep.`;

      const ts  = londonTs(new Date(), false);
      let msg   = `*SUPPLY CHAIN INTELLIGENCE*\n_${ts} London_\n━━━━━━━━━━━━━━━\n\n`;

      // Commodity price movers
      const materials = supply.metrics?.rawMaterials || [];
      const alertMats = materials.filter(m => m.risk === 'critical' || m.risk === 'high');
      if (alertMats.length > 0) {
        msg += `*⚠️ COMMODITY ALERTS (${alertMats.length})*\n`;
        for (const m of alertMats.slice(0, 5)) {
          msg += `• *${m.name}:* ${m.price} *(${m.change})* — ${m.impact}\n`;
        }
        msg += `\n`;
      }

      // Chokepoint status
      const chokes = (supply.metrics?.logistics || []).filter(c => c.severity !== 'normal');
      if (chokes.length > 0) {
        msg += `*🚢 MARITIME CHOKEPOINTS*\n`;
        for (const c of chokes.slice(0, 4)) {
          const sev = c.severity === 'critical' ? '🔴' : c.severity === 'high' ? '🟠' : '🔵';
          msg += `${sev} *${c.name}* — ${c.severity.toUpperCase()}\n`;
          msg += `_${c.impact}_\n`;
          if (c.mentions?.length) msg += `Latest: ${c.mentions[0].substring(0, 100)}\n`;
          msg += `\n`;
        }
      }

      // Munitions/explosive news
      const munitions = supply.metrics?.munitions || {};
      if (munitions.explosiveNews?.length > 0) {
        msg += `*🔫 MUNITIONS & PROPELLANT SUPPLY*\n`;
        msg += `_${munitions.note}_\n`;
        for (const n of munitions.explosiveNews.slice(0, 3)) {
          msg += `• *${n.source}:* ${n.title.substring(0, 120)}\n`;
        }
        msg += `\n`;
      }

      msg += `_Use /supplyfull for the complete materials database_`;
      return msg;
    } catch (error) {
      return `Supply Chain Error: ${error.message}`;
    }
  }

  async _handleSupplyFull() {
    try {
      const data   = await this._getCachedData();
      const supply = data?.supplyChain || data?.sources?.['Supply Chain'];
      if (!supply) return `⏳ Supply chain data not yet available — awaiting next sweep.`;

      const ts  = londonTs(new Date(), false);
      let msg   = `*COMPLETE SUPPLY CHAIN REPORT*\n_${ts} London_\n━━━━━━━━━━━━━━━\n\n`;

      // All material prices
      const materials = supply.metrics?.rawMaterials || [];
      if (materials.length > 0) {
        msg += `*📦 ALL MATERIALS (${materials.length})*\n`;
        for (const m of materials) {
          const risk = m.risk === 'critical' ? '🔴' : m.risk === 'high' ? '🟠' : '⚪';
          const src  = m.source ? ` _(${m.source})_` : '';
          msg += `${risk} *${m.name}:* ${m.price} (${m.change})${src}\n`;
          msg += `  └ _${m.impact}_\n`;
        }
        msg += `\n`;
      }

      // All alerts
      const alerts = supply.metrics?.alerts || [];
      if (alerts.length > 0) {
        msg += `*🚨 ALL SUPPLY ALERTS (${alerts.length})*\n`;
        for (const a of alerts) {
          const icon = a.type === 'critical' ? '🔴' : a.type === 'high' ? '🟠' : '🔵';
          msg += `${icon} ${a.message}\n`;
        }
        msg += `\n`;
      }

      // Chokepoints — all
      const chokes = supply.metrics?.logistics || [];
      if (chokes.length > 0) {
        msg += `*🚢 ALL CHOKEPOINTS*\n`;
        for (const c of chokes) {
          const sev = c.severity === 'critical' ? '🔴' : c.severity === 'high' ? '🟠' : c.severity === 'elevated' ? '🔵' : '⚪';
          msg += `${sev} *${c.name}:* ${c.severity.toUpperCase()} — _${c.impact}_\n`;
        }
      }

      return msg;
    } catch (error) {
      return `Supply Chain Error: ${error.message}`;
    }
  }

  async _handlePredict(q) {
    try {
      const data = await this._getCachedData();
      const markets = data?.polymarket?.markets || [];
      const arb     = data?.arbitrage || [];

      if (!markets.length) return `⏳ Prediction market data not yet available — try after next sweep.`;

      const ts  = londonTs(new Date(), false);
      let msg   = `*PREDICTION MARKETS*\n_${ts} London · Polymarket_\n━━━━━━━━━━━━━━━\n\n`;

      // Filter by query if provided
      const filtered = q?.trim()
        ? markets.filter(m => m.question?.toLowerCase().includes(q.toLowerCase()))
        : markets;
      const display = filtered.length ? filtered : markets;

      for (const m of display.slice(0, 8)) {
        const prob  = m.yesProb || 0;
        const bar   = '█'.repeat(Math.round(prob / 10)) + '░'.repeat(10 - Math.round(prob / 10));
        const color = prob >= 70 ? '🔴' : prob >= 45 ? '🟠' : '🔵';
        msg += `${color} *${prob}%* — ${m.question}\n`;
        msg += `\`${bar}\`\n\n`;
      }

      // Include arbitrage signals if available
      if (arb.length > 0) {
        msg += `*ARBITRAGE SIGNALS*\n`;
        msg += `_Markets diverging from OSINT risk scores:_\n`;
        for (const a of arb.slice(0, 3)) {
          msg += `• *${a.market?.substring(0, 60)}*\n  Market: ${a.marketProb}% | OSINT risk: ${a.osintSeverity} | Gap: ${a.gap}\n\n`;
        }
      }

      msg += `_Use /predict [keyword] to filter by topic_`;
      return msg;
    } catch (e) {
      return `Prediction markets error: ${e.message}`;
    }
  }

  async _handleRisk(q){
    if(!q||!q.trim())return"Usage: /risk name";
    try{
      const m=await import("../apis/sources/counterparty_risk.mjs");
      return await m.handleRiskCommand(q);
    }catch(e){return"Error: "+e.message;}
  }

  // ── Inline keyboard helpers ──────────────────────────────────────────────────

  async _sendWithKeyboard(chatId, text, keyboard) {
    try {
      const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/sendMessage`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: chatId,
          text,
          parse_mode: 'Markdown',
          disable_web_page_preview: true,
          reply_markup: { inline_keyboard: keyboard },
        }),
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) {
        // Retry without Markdown in case of formatting error
        const plain = text.replace(/[*_`[\]]/g, '');
        await fetch(`${TELEGRAM_API}/bot${this.botToken}/sendMessage`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ chat_id: chatId, text: plain, disable_web_page_preview: true, reply_markup: { inline_keyboard: keyboard } }),
          signal: AbortSignal.timeout(15000),
        });
      }
    } catch (err) {
      console.error('[Telegram] sendWithKeyboard error:', err.message);
    }
  }

  async _answerCallback(callbackQueryId, text = '') {
    try {
      await fetch(`${TELEGRAM_API}/bot${this.botToken}/answerCallbackQuery`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ callback_query_id: callbackQueryId, text }),
        signal: AbortSignal.timeout(10000),
      });
    } catch {}
  }

  // ── OEM paginated card browser ────────────────────────────────────────────────

  async _sendOEMPage(chatId, results, query, offset) {
    const { formatOEMCard } = await import('../intel/oem_db.mjs');
    const PAGE  = 2;
    const total = results.length;
    const slice = results.slice(offset, offset + PAGE);
    const label = query ? `*${query.toUpperCase()}*` : '*ALL OEMs*';

    let text = `🏭 OEM BROWSE — ${label}\n`;
    text += `_Showing ${offset + 1}–${Math.min(offset + PAGE, total)} of ${total} manufacturers_\n`;

    for (const oem of slice) {
      text += '\n' + formatOEMCard(oem, false) + '\n';
    }

    const navRow = [];
    const remaining = total - offset - PAGE;
    if (offset > 0)            navRow.push({ text: '◀ Prev', callback_data: `oem:${offset - PAGE}:${query}` });
    if (offset + PAGE < total) navRow.push({ text: `Next ▶  (${remaining} left)`, callback_data: `oem:${offset + PAGE}:${query}` });

    await this._sendWithKeyboard(chatId, text, navRow.length ? [navRow] : []);
  }

  async _handleOEMCallback(data, callbackQueryId, chatId) {
    try {
      const { searchOEMs, OEM_DATABASE } = await import('../intel/oem_db.mjs');
      // Callback data format: oem:{offset}:{query}
      const m = data.match(/^oem:(\d+):(.*)$/);
      if (!m) {
        console.error(`[Telegram] OEM callback: unrecognised data="${data}"`);
        await this._answerCallback(callbackQueryId);
        return;
      }
      const offset  = parseInt(m[1]) || 0;
      const query   = m[2].trim();
      console.log(`[Telegram] OEM callback: offset=${offset} query="${query}"`);
      const results = query ? searchOEMs(query) : OEM_DATABASE;
      await this._answerCallback(callbackQueryId);
      await this._sendOEMPage(chatId, results, query, offset);
    } catch (err) {
      console.error(`[Telegram] OEM callback error: ${err.message}`);
      await this._answerCallback(callbackQueryId, 'Error loading OEM data');
    }
  }

  async _handleOEM(query, chatId) {
    try {
      const { searchOEMs, formatOEMCard, formatOEMList, OEM_DATABASE } = await import('../intel/oem_db.mjs');

      // No query → directory overview + Browse button
      if (!query?.trim()) {
        const text     = formatOEMList(OEM_DATABASE);
        const keyboard = [[{ text: '🗂 Browse All Cards →', callback_data: 'oem:0:' }]];
        await this._sendWithKeyboard(chatId, text, keyboard);
        return null;
      }

      const q       = query.trim();
      const results = searchOEMs(q);

      if (results.length === 0) {
        return `No OEMs found matching "${q}".\n\nTry: /oem germany · /oem fuzes · /oem 155mm · /oem propellant · /oem non-nato`;
      }

      // Single result → full card, no buttons needed
      if (results.length === 1) {
        return formatOEMCard(results[0], false);
      }

      // Multiple results → paginated cards (2 at a time)
      await this._sendOEMPage(chatId, results, q, 0);
      return null;
    } catch (err) {
      return `OEM directory error: ${err.message}`;
    }
  }

  async _handleAsk(query) {
    if (!query?.trim()) {
      return `Usage: /ask [topic]\nExample: /ask Angola arms\n\nSearches all current intelligence for matching signals.`;
    }
    const data = await this._getCachedData();
    if (!data) return `⏳ Intelligence data loading — try again in 60s.`;

    const q = query.toLowerCase().trim();
    const results = [];

    const collect = (text, source, priority = 'medium') => {
      if (!text) return;
      if (text.toLowerCase().includes(q)) {
        results.push({ text: String(text).substring(0, 220), source, priority });
      }
    };

    // OSINT signals
    for (const s of (data.tg?.urgent || [])) collect(s.text, s.channel || 'OSINT', 'high');
    // Defense
    for (const c of (data.defense?.updates || [])) collect((c.title || '') + ' ' + (c.content || ''), 'Defense', 'medium');
    // Lusophone
    for (const u of (data.lusophone?.updates || [])) collect(u.title || u.text, 'Lusophone', 'medium');
    // Export control
    for (const e of (data.exportControl?.updates || [])) collect(e.title || e.text, 'Export Control', 'high');
    // GDELT
    for (const g of (data.gdelt?.updates || [])) collect(g.title, 'GDELT', 'medium');
    // SIPRI arms
    for (const a of (data.sipri?.updates || [])) collect(a.title || a.content, 'SIPRI Arms', 'high');
    // ACLED conflict
    for (const c of (data.acled?.deadliestEvents || [])) collect(`${c.location}: ${c.fatalities} fatalities`, 'ACLED Conflict', 'critical');
    // Supply chain alerts
    for (const a of (data.supplyChain?.metrics?.alerts || [])) collect(a.message, 'Supply Chain', a.type || 'medium');
    // Sanctions
    for (const s of (data.opensanctions?.updates || [])) collect(s.name, 'Sanctions', 'high');
    // Regional correlations
    for (const c of (data.correlations || [])) {
      if (c.region?.toLowerCase().includes(q)) {
        results.push({
          text: `${c.region}: ${(c.topSignals?.[0]?.text || 'no detail').substring(0, 180)}`,
          source: 'Regional Analysis',
          priority: c.severity,
        });
      }
    }

    if (results.length === 0) {
      return `No current intelligence matching *"${query}"*.\n\nTry broader terms or use /full for the complete report.`;
    }

    const ts = londonTs(new Date(), false);
    let msg = `🔍 *INTEL QUERY: ${query.toUpperCase()}*\n`;
    msg += `_${ts} London · ${results.length} match${results.length !== 1 ? 'es' : ''}_\n`;
    msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

    const sorted = results.sort((a, b) => {
      const order = { critical: 0, high: 1, medium: 2, low: 3 };
      return (order[a.priority] ?? 2) - (order[b.priority] ?? 2);
    });

    for (const r of sorted.slice(0, 8)) {
      const icon = r.priority === 'critical' ? '🔴' : r.priority === 'high' ? '🟠' : '🔵';
      msg += `${icon} *[${r.source}]* ${r.text}\n\n`;
    }

    if (results.length > 8) {
      msg += `_... and ${results.length - 8} more. Use /full for complete report._`;
    }
    return msg;
  }

  onCommand(command, handler) {
    this._commandHandlers[command.toLowerCase()] = handler;
  }

  async startPolling(intervalMs = 5000) {
    if (!this.isConfigured) return;
    if (this._pollingInterval) return;
    // On startup, acknowledge all pending updates without processing them.
    // This prevents old commands from being replayed after Render restarts.
    await this._skipPendingUpdates();
    console.log('[Telegram] Bot polling started');
    this._pollingInterval = setInterval(() => this._pollUpdates(), intervalMs);
    this._pollUpdates();
  }

  async _skipPendingUpdates() {
    try {
      const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/getUpdates?limit=100&timeout=0`, {
        signal: AbortSignal.timeout(10000),
      });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok || !data.result?.length) return;
      const maxId = Math.max(...data.result.map(u => u.update_id));
      this._lastUpdateId = maxId;
      console.log(`[Telegram] Skipped ${data.result.length} pending updates on startup (offset → ${maxId})`);
    } catch {}
  }

  stopPolling() {
    if (this._pollingInterval) {
      clearInterval(this._pollingInterval);
      this._pollingInterval = null;
    }
  }

  async _pollUpdates() {
    if (this._pollBusy) return;
    this._pollBusy = true;
    try {
      const params = new URLSearchParams({ offset: String(this._lastUpdateId + 1), timeout: '0', limit: '10', allowed_updates: '["message","callback_query"]' });
      const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/getUpdates?${params}`, { signal: AbortSignal.timeout(10000) });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok) return;

      for (const update of data.result) {
        this._lastUpdateId = Math.max(this._lastUpdateId, update.update_id);

        // Inline keyboard button tap
        const cbq = update.callback_query;
        if (cbq?.data) {
          const cbChatId = String(cbq.message?.chat?.id || this.chatId);
          const cbUserId = String(cbq.from?.id);
          console.log(`[Telegram] callback_query: data="${cbq.data}" chat=${cbChatId} user=${cbUserId} (allowed: chat=${cbChatId === String(this.chatId)} user=${this._allowedUsers.has(cbUserId)})`);
          if (cbChatId === String(this.chatId) || this._allowedUsers.has(cbUserId)) {
            if (cbq.data.startsWith('oem:')) await this._handleOEMCallback(cbq.data, cbq.id, cbChatId);
            else await this._answerCallback(cbq.id); // dismiss spinner for unrecognised buttons
          }
          continue;
        }

        const msg = update.message;
        if (!msg?.text) continue;

        const chatId = String(msg.chat?.id);
        const userId = String(msg.from?.id);
        const isGroup           = chatId === String(this.chatId);
        const isWhitelistedUser = this._allowedUsers.has(userId);
        if (!isGroup && !isWhitelistedUser) continue;

        await this._handleMessage(msg);
      }
    } catch (err) {}
    finally {
      this._pollBusy = false;
    }
  }

  async _handleMessage(msg) {
    const text       = msg.text.trim();
    const parts      = text.split(/\s+/);
    const rawCommand = parts[0].toLowerCase();
    const command    = rawCommand.startsWith('/') ? rawCommand.split('@')[0] : null;
    if (!command) return;
    const args        = parts.slice(1).join(' ');
    const replyChatId = msg.chat?.id;

    const handlers = {
      '/ask':        () => this._handleAsk(args),
      '/search':     () => this._handleCompanySearch(args),
      '/brief':      () => this._handleBrief(),
      '/full':       () => this._handleFullReport(),
      '/osint':      () => this._sendFullOSINT(),
      '/contracts':  () => this._handleContracts(),
      '/arms':       () => this._handleArms(),
      '/conflict':   () => this._handleConflict(),
      '/watchlist':  () => this._handleWatchlist(args),
      '/add':        () => this._handleWatchlist(`add ${args}`),
      '/remove':     () => this._handleWatchlist(`remove ${args}`),
      '/sweep':      () => this.triggerManualSweep(),
      '/debug':      () => this._debugData(),
      '/supply':     () => this._handleSupplyChain(),
      '/supplyfull': () => this._handleSupplyFull(),
      '/events':     () => this._handleEvents(args),
      '/predict':    () => this._handlePredict(args),
      '/risk':       () => this._handleRisk(args),
      '/oem':        () => this._handleOEM(args, replyChatId),
      '/status':     () => `CRUCIX ONLINE\n\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0} companies\nSupply Chain: Active`,
      '/alerts':     () => this._handleAlerts(),
      '/testalert':  () => this._testAlert(),
      '/help':       () => Object.entries(COMMANDS).map(([c, d]) => `${c} - ${d}`).join('\n'),
    };

    const handler = handlers[command] || this._commandHandlers[command];
    if (handler) {
      // Built-in handlers are closures that already capture args.
      // Custom handlers registered via onCommand(cmd, fn) receive (args, chatId, userId).
      const isCustom = !!this._commandHandlers[command] && !handlers[command];
      const response = isCustom
        ? await handler(args, replyChatId, msg.from?.id?.toString())
        : await handler();
      if (response) await this.sendMessage(response, { chatId: replyChatId, replyToMessageId: msg.message_id });
    }
  }

  async _initializeBotCommands() {
    await this._loadBotIdentity();
    const botCommands = Object.entries(COMMANDS).map(([c, d]) => ({
      command: c.replace('/', ''),
      description: d.substring(0, 256),
    }));
    await this._setMyCommands(botCommands, this._buildConfiguredChatScope());
  }

  async _loadBotIdentity() {
    const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/getMe`, { signal: AbortSignal.timeout(10000) });
    if (!res.ok) throw new Error('getMe failed');
    const data = await res.json();
    if (!data.ok) throw new Error('Invalid bot');
    this._botUsername = String(data.result.username).toLowerCase();
  }

  async _setMyCommands(commands, scope = null) {
    const body = { commands };
    if (scope) body.scope = scope;
    await fetch(`${TELEGRAM_API}/bot${this.botToken}/setMyCommands`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
  }

  _buildConfiguredChatScope() {
    return { type: 'chat', chat_id: Number(this.chatId) };
  }

  // ── Flash alert: immediate send for truly critical events ─────────────────
  // Bypasses the 3-hour cadence. Rate-limited to 3 per hour.
  async _sendFlashAlert(signal) {
    if (!this.isConfigured) return;

    const now = Date.now();
    // Reset hourly counter + seen-text dedup window
    if (now - this._flashHourStart > 3600000) {
      this._flashCount     = 0;
      this._flashHourStart = now;
      this._flashSeenTexts = new Set();
    }
    if (this._flashCount >= 3) return; // max 3 flash alerts per hour

    this._flashCount++;
    const ts  = londonTs();
    // For correlation signals the text starts with "Region: body" — display the region
    // separately so the alert is clean rather than repeating "correlation / Region: body".
    let displaySource = signal.source;
    let displayText   = signal.text;
    if (signal.source === 'correlation') {
      const m = signal.text.match(/^([A-Za-z /]+):\s*([\s\S]+)$/);
      if (m) { displaySource = m[1]; displayText = m[2]; }
    }
    const msg = `🚨 *FLASH ALERT*\n_${ts} London_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n\`${displaySource}\`\n${displayText}\n\n_Crucix early warning — /brief for full context._`;
    console.log(`[Telegram] FLASH ALERT sent (${this._flashCount}/3 this hour): ${signal.text.substring(0, 60)}`);
    await this.sendMessage(msg);
  }

  // Called after every sweep cycle. Sends alerts on the 3-hour cadence,
  // only for signals that haven't been sent before (dedup).
  async onSweepComplete(data) {
    if (!this.isConfigured) return;

    const now = Date.now();

    // ── Restore 3h timer from Redis on first sweep after restart ─────────────
    // File-based timer is lost on Render redeploy; Redis survives restarts.
    if (!this._alertTimeInitialized) {
      this._alertTimeInitialized = true;
      const redisTime = await this._loadAlertTimeRedis();
      if (redisTime && redisTime > this._lastAlertTime) {
        this._lastAlertTime = redisTime;
        console.log(`[Telegram] Alert timer restored from Redis: ${new Date(redisTime).toISOString()}`);
      }
    }

    // ── Collect candidate signals ────────────────────────────────────────────
    const candidates = [];

    // Delta changes (critical/high only)
    for (const change of (data.delta?.changes || [])) {
      if (change.severity === 'critical' || change.severity === 'high') {
        candidates.push({
          text:     change.summary || `${change.label}: ${change.direction} (${change.pctChange?.toFixed(1) || '?'}%)`,
          source:   'delta',
          priority: change.severity,
        });
      }
    }

    // Regional correlations (critical/high)
    // Use the top-signal text as the dedupKey (without region prefix) so the same
    // underlying story doesn't produce separate candidates for "Middle East: X"
    // and "East Asia: X" when the correlation engine assigns it to multiple regions.
    const _corrSeenTexts = new Set();
    for (const c of (data.correlations || [])) {
      if (c.severity === 'critical' || c.severity === 'high') {
        const top = c.topSignals?.[0]?.text || '';
        const topKey = top.toLowerCase().replace(/\s+/g, ' ').trim().substring(0, 120);
        if (topKey && _corrSeenTexts.has(topKey)) continue; // same story, different region tag
        if (topKey) _corrSeenTexts.add(topKey);
        candidates.push({
          text:     `${c.region}: ${top}`,
          source:   'correlation',
          priority: c.severity,
          url:      c.topSignals?.[0]?.url || null,
          dedupKey: topKey || `corr:${c.region}`,
        });
      }
    }

    // Lusophone critical alerts
    for (const a of (data.lusophone?.alerts || [])) {
      candidates.push({ text: a.text, source: a.source || 'Lusophone', priority: 'critical' });
    }

    // Urgent OSINT — score by Arkmurus relevance (procurement/Africa = higher; pure Ukraine kinetics = lower)
    const ARKMURUS_BOOST_RE  = /contract|procurement|tender|acquisition|ucav|drone.*deal|arms deal|defence budget|modernisa|lusophone|angola|mozambique|nigeria|sahel|africa|export|fms|foreign military|elbit|rafael|mbda|saab|rheinmetall|bae|dassault|airbus.*defence/i;
    const UKRAINE_NOISE_RE   = /shahed.*shot|drone.*shot down|kharkiv|zelenskyy|frontline|bakhmut|zaporizhzhia|russian drone|ukrainian drone|interception rate/i;
    for (const s of (data.tg?.urgent || [])) {
      const txt = s.text || '';
      let prio = 'high';
      if (ARKMURUS_BOOST_RE.test(txt))  prio = 'critical';
      else if (UKRAINE_NOISE_RE.test(txt)) prio = 'normal';
      candidates.push({
        text:     txt,
        source:   s.channel || 'OSINT',
        priority: prio,
        url:      s.url || null,
      });
    }

    // Polymarket arbitrage signals
    for (const s of (data.arbitrage || []).slice(0, 3)) {
      candidates.push({ text: s.text, source: 'Polymarket Arb', priority: s.priority });
    }

    // Sanctions pre-designation signals (multi-list within 48h)
    for (const s of (data.opensanctions?.preDesignation || [])) {
      candidates.push({ text: s.text, source: 'OpenSanctions', priority: 'critical' });
    }

    // Supply chain — chokepoint disruptions only (commodity prices belong in digests/supply)
    const scChokes = (data.supplyChain?.metrics?.logistics || [])
      .filter(c => c.severity === 'critical' || c.severity === 'high');
    for (const c of scChokes) {
      candidates.push({
        text:     `${c.name}: ${c.severity.toUpperCase()} disruption — ${c.impact}${c.mentions?.[0] ? '. ' + c.mentions[0] : ''}`,
        source:   'Maritime Intelligence',
        priority: c.severity,
      });
    }

    // Defense News signals (live RSS — DefenseWeb, Breaking Defense, ISS Africa)
    for (const s of (data.defenseNews?.signals || [])) {
      candidates.push({ text: s.text || '', source: s.source || 'Defense News', priority: 'medium' });
    }

    // Procurement Tenders signals — Lusophone/Africa tenders and FMS notifications
    for (const s of (data.procurementTenders?.signals || [])) {
      candidates.push({ text: s.text || '', source: s.source || 'ProcurementTenders', priority: 'medium' });
    }

    // Cross-source confirmation: Lusophone signal in BOTH defenseNews AND procurementTenders → elevate to high
    const LUSOPHONE_KW = ['angola', 'mozambique', 'guinea-bissau', 'guinea bissau', 'cape verde', 'são tomé', 'sao tome', 'lusophone'];
    const defNewsLusi  = (data.defenseNews?.signals || []).filter(s => LUSOPHONE_KW.some(kw => (s.text || '').toLowerCase().includes(kw)));
    const procLusi     = (data.procurementTenders?.signals || []).filter(s => LUSOPHONE_KW.some(kw => (s.text || '').toLowerCase().includes(kw)));
    if (defNewsLusi.length > 0 && procLusi.length > 0) {
      // Both sources see Lusophone activity — cross-confirmed, elevate lead signal
      const lead = defNewsLusi[0];
      candidates.push({
        text:     `[Cross-source] Lusophone defence activity confirmed: ${(lead.text || '').substring(0, 160)}`,
        source:   'correlation',
        priority: 'high',
      });
    }

    // ── Quality filter: remove junk and clean text before dedup ─────────────
    for (let i = candidates.length - 1; i >= 0; i--) {
      const c = candidates[i];
      const cleaned = cleanSignalText(c.text);
      if (!cleaned || isJunkSignal(c.text)) {
        candidates.splice(i, 1);
      } else {
        c.text = cleaned;
      }
    }

    if (candidates.length === 0) return;

    // ── Cross-candidate dedup: if a raw OSINT signal's text already appears as
    // the body of a correlation candidate, drop the raw duplicate so we don't
    // send "CIG_telegram: X" immediately after "Middle East: X" (same content).
    const corrTextsNorm = new Set(
      candidates
        .filter(c => c.source === 'correlation')
        .map(c => c.text.replace(/^[A-Za-z /]+:\s*/, '').toLowerCase().replace(/\s+/g, ' ').trim().substring(0, 100))
    );
    for (let i = candidates.length - 1; i >= 0; i--) {
      if (candidates[i].source === 'correlation') continue;
      const norm = candidates[i].text.toLowerCase().replace(/\s+/g, ' ').trim().substring(0, 100);
      if (corrTextsNorm.has(norm)) candidates.splice(i, 1);
    }

    // ── Cross-source confirmation: single-source criticals → downgrade to high ─
    // Only correlation signals (already multi-source by definition) and
    // OpenSanctions pre-designation signals retain critical status.
    // This reduces false-positive alert fatigue from single-feed noise.
    const MULTI_SOURCE_TRUSTED = new Set(['correlation', 'OpenSanctions']);
    for (const c of candidates) {
      if (c.priority === 'critical' && !MULTI_SOURCE_TRUSTED.has(c.source)) {
        c.priority = 'high';
      }
    }

    // ── Flash path: fire IMMEDIATELY for ultra-critical signals ──────────────
    // Rate-limited to 3/hour. Deduped by normalised text within the hour window.
    // Uses word-boundary regex so "couple" doesn't match "coup", etc.
    const FLASH_KEYWORDS = [
      'coup', 'overthrow', 'nuclear', 'invasion', 'invaded', 'war declared',
      'assassination', 'exchange halted', 'circuit breaker', 'martial law',
      'chemical weapon', 'dirty bomb', 'imminent attack',
    ];
    const FLASH_REGEXES = FLASH_KEYWORDS.map(kw => new RegExp(`\\b${kw.replace(/ /g, '\\s+')}\\b`, 'i'));
    const flashSentSignals = [];
    for (const s of candidates) {
      const text = s.text || '';
      if (!FLASH_REGEXES.some(re => re.test(text))) continue;
      // Strip "Region: " prefix before dedup key so "Middle East: X" and "East Asia: X"
      // are treated as the same flash (the underlying story is identical).
      const stripped = text.replace(/^[A-Za-z /]+:\s*/, '');
      const textKey = stripped.toLowerCase().replace(/\s+/g, ' ').trim().substring(0, 120);
      if (this._flashSeenTexts.has(textKey)) continue;
      this._flashSeenTexts.add(textKey);
      await this._sendFlashAlert(s);
      flashSentSignals.push(s);
    }
    // Mark flash-sent signals in the main dedup store so the NEW INTEL path
    // doesn't fire a second alert for the same story in the same sweep.
    if (flashSentSignals.length > 0) {
      try {
        const { filterNewSignals: markSeen } = await import('../intel/dedup.mjs');
        await markSeen(flashSentSignals);
      } catch {}
    }

    // ── Within 3h window: push new CRITICAL/HIGH signals immediately ──────────
    // Peek (no mark) → mark + send only the new urgent ones.
    // The digest at the 3h mark will skip these (already seen) and cover the rest.
    // Quality gate: single-source 'critical' requires cross-source confirmation
    // (_crossSourceConfirmed >= 2) or high dedup score (>= 15) to pass immediately.
    // This prevents single-keyword OSINT posts from causing false-alarm interruptions.
    if (now - this._lastAlertTime < this._alertIntervalMs) {
      try {
        const { peekNewSignals, filterNewSignals } = await import('../intel/dedup.mjs');
        const urgentCandidates = candidates.filter(s => {
          if (s.priority !== 'critical' && s.priority !== 'high') return false;
          if (s.priority === 'critical') {
            // Require cross-source confirmation OR very high score for immediate push
            const confirmed = (s._crossSourceConfirmed || s._confirmedBy || 1);
            return confirmed >= 2 || (s.score || 0) >= 15;
          }
          return true; // high passes immediately without confirmation
        });
        const deduped = deduplicateBatch(urgentCandidates);
        const newUrgent = peekNewSignals(deduped);
        if (newUrgent.length > 0) {
          await filterNewSignals(newUrgent); // mark as seen now so digest doesn't resend
          const minsLeft = Math.ceil((this._alertIntervalMs - (now - this._lastAlertTime)) / 60000);
          const ts = londonTs();
          const header = `🚨 *NEW INTEL ALERT*\n_${ts} London · ${newUrgent.length} new signal${newUrgent.length !== 1 ? 's' : ''}_\n━━━━━━━━━━━━━━━━━━━━━━━━`;
          const footer = `\n_Next digest in ~${minsLeft}min · /brief for full context_`;
          // Build each entry independently — split messages at entry boundaries (never mid-entry)
          const entries = newUrgent.map((s, i) => {
            const icon = s.priority === 'critical' ? '🔴' : s.priority === 'normal' ? '🔵' : '🟠';
            // Strip Telegram Markdown v1 special chars from raw signal text —
            // OSINT posts often contain *, _, ` that would cause Telegram to
            // reject the message with a 400 parse error.
            const cleanText = s.text.trim().replace(/\n{3,}/g, '\n\n').replace(/[*_`]/g, '');
            const displayText = smartTruncate(cleanText, 600);
            const srcLink = s.url ? `\n🔗 ${s.url.substring(0, 100)}` : '';
            return `\n${icon} \`${s.source}\`\n${displayText}${srcLink}`;
          });
          const SEP = '\n─────────────────────────';
          // Pack entries into messages staying under 3800 chars each
          const MSG_LIMIT = 3800;
          let msgParts = [];
          let current = header;
          for (let i = 0; i < entries.length; i++) {
            const sep = i < entries.length - 1 ? SEP : '';
            const addition = entries[i] + sep;
            if (current.length + addition.length + footer.length > MSG_LIMIT && current !== header) {
              msgParts.push(current);
              current = `📡 *INTEL ALERT (cont.)*\n━━━━━━━━━━━━━━━━━━━━━━━━` + entries[i] + sep;
            } else {
              current += addition;
            }
          }
          current += footer;
          msgParts.push(current);
          for (const part of msgParts) await this.sendMessage(part);
          console.log(`[Telegram] Immediate push: ${newUrgent.length} new critical/high signals (~${minsLeft}min to digest)`);
        }
      } catch {}
      return; // Respect 3h gate for full digest
    }

    // ── 3-hour digest gate passed ─────────────────────────────────────────────
    this._lastAlertTime = now;
    this._saveLastAlertTime(now);

    // ── Dedup: only send signals not seen in the last 48h ────────────────────
    let newSignals;
    try {
      const { filterNewSignals } = await import('../intel/dedup.mjs');
      newSignals = await filterNewSignals(candidates);
    } catch {
      newSignals = candidates;
    }

    if (newSignals.length === 0) {
      console.log('[Telegram] All signals already seen — sending no-new-intel nudge');
      const ts = londonTs();
      const srcOk    = data.meta?.sourcesOk    || 0;
      const srcTotal = data.meta?.sourcesQueried || 0;
      await this.sendMessage(
        `✅ *No new intelligence*\n_${ts} London_\n\nAll ${srcOk}/${srcTotal} sources swept — no signals beyond what was already sent. Situation unchanged.\n\n_/brief for full context · /sweep to force a new run_`
      );
      return;
    }

    // ── Within-batch near-duplicate suppression ───────────────────────────────
    newSignals = deduplicateBatch(newSignals);

    // ── Format ───────────────────────────────────────────────────────────────
    const ds       = data.delta?.summary || {};
    const dir      = ds.direction;
    const dirEmoji = { 'risk-off': '📉', 'risk-on': '📈', 'mixed': '↔️' }[dir] || '↔️';
    const ts       = londonTs();

    const criticals = newSignals.filter(s => s.priority === 'critical');
    const highs     = newSignals.filter(s => s.priority === 'high');
    const rest      = newSignals.filter(s => s.priority !== 'critical' && s.priority !== 'high');

    // Opening context paragraph
    const vix = data.fred?.find(f => f.id === 'VIXCLS');
    const oil = data.energy || {};
    const dirText = dir === 'risk-off' ? 'markets are in a risk-off posture'
                  : dir === 'risk-on'  ? 'markets are broadly risk-on'
                  : 'market signals are mixed';

    let msg = `🔍 *ARKMURUS INTELLIGENCE UPDATE*\n`;
    msg += `_${ts} London · ${newSignals.length} new signal${newSignals.length !== 1 ? 's' : ''}_\n`;
    msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

    // Environment summary sentence
    msg += `${dirEmoji} *ENVIRONMENT:* Currently ${dirText}`;
    if (vix?.value) msg += `, with VIX at *${vix.value}*${vix.value > 25 ? ' (elevated — stress signal)' : ''}`;
    if (oil.brent)  msg += ` and Brent crude at *$${oil.brent}*`;
    msg += `. This sweep recorded *${ds.totalChanges || 0}* measurable changes`;
    if (ds.criticalChanges > 0) msg += `, *${ds.criticalChanges}* of which crossed critical thresholds`;
    msg += `.\n\n`;

    // Critical signals — show ALL of them (no false count mismatch)
    if (criticals.length > 0) {
      msg += `🚨 *CRITICAL INTELLIGENCE — ${criticals.length} signal${criticals.length !== 1 ? 's' : ''}*\n`;
      msg += `━━━━━━━━━━━━━━━━━\n`;
      for (let i = 0; i < criticals.length; i++) {
        const s = criticals[i];
        const text = smartTruncate(s.text.trim().replace(/\n{3,}/g, '\n\n').replace(/[*_`]/g, ''), 420);
        msg += `\n\`${s.source}\`\n${text}\n`;
        if (i < criticals.length - 1) msg += `─────────────────────────\n`;
      }
      msg += `\n`;
    }

    // High signals — show ALL of them
    if (highs.length > 0) {
      msg += `⚠️ *HIGH PRIORITY — ${highs.length} signal${highs.length !== 1 ? 's' : ''}*\n`;
      msg += `━━━━━━━━━━━━━━━━━\n`;
      for (let i = 0; i < highs.length; i++) {
        const s = highs[i];
        const text = smartTruncate(s.text.trim().replace(/\n{3,}/g, '\n\n').replace(/[*_`]/g, ''), 350);
        msg += `\n\`${s.source}\`\n${text}\n`;
        if (i < highs.length - 1) msg += `─────────────────────────\n`;
      }
      msg += `\n`;
    }

    // Additional signals — show all
    if (rest.length > 0) {
      msg += `📋 *ADDITIONAL SIGNALS — ${rest.length}*\n`;
      for (const s of rest) {
        msg += `• [${s.source}] ${s.text.substring(0, 120)}\n`;
      }
      msg += `\n`;
    }

    // Supply chain summary in digest (commodity movers + chokepoints)
    const scMaterials = (data.supplyChain?.metrics?.rawMaterials || []).filter(m => m.risk === 'critical' || m.risk === 'high');
    const scChokeSummary = (data.supplyChain?.metrics?.logistics || []).filter(c => c.severity === 'critical' || c.severity === 'high');
    if (scMaterials.length > 0 || scChokeSummary.length > 0) {
      msg += `📦 *SUPPLY CHAIN*\n`;
      for (const m of scMaterials.slice(0, 4)) {
        msg += `• *${m.name}:* ${m.price} *(${m.change})* — ${m.impact}\n`;
      }
      for (const c of scChokeSummary.slice(0, 3)) {
        const sev = c.severity === 'critical' ? '🔴' : '🟠';
        msg += `${sev} ${c.name}: ${c.severity.toUpperCase()} — ${c.impact}\n`;
      }
      msg += `_/supply for full report_\n\n`;
    }

    msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
    // Source integrity footer — name any failed critical sources so confidence is clear
    const srcOk    = data.meta?.sourcesOk    || 0;
    const srcTotal = data.meta?.sourcesQueried || 0;
    const srcFail  = data.meta?.sourcesFailed  || 0;

    const CRITICAL_SRCS = ['Defense News', 'ProcurementTenders', 'Lusophone', 'ACLED',
                           'OFAC', 'OpenSanctions', 'DefenseEvents', 'SIPRI Arms', 'FCDO'];
    const failedCritical = (data.health || [])
      .filter(h => h.err && CRITICAL_SRCS.includes(h.n))
      .map(h => h.n);
    const failedOtherCount = srcFail - failedCritical.length;

    let srcLine = `_Sources: ${srcOk}/${srcTotal} OK`;
    if (failedCritical.length > 0) {
      srcLine += ` · ⚠️ degraded: *${failedCritical.join(', ')}*`;
      if (failedOtherCount > 0) srcLine += ` + ${failedOtherCount} others`;
    } else if (srcFail > 0) {
      srcLine += ` · ${srcFail} degraded (non-critical)`;
    }
    srcLine += ` · /brief for full analysis_`;
    msg += srcLine;

    console.log(`[Telegram] Sending sweep digest: ${newSignals.length} signals (${criticals.length} critical, ${highs.length} high)`);
    const result = await this.sendMessage(msg);
    if (!result.ok) {
      // Markdown failed — strip all formatting and retry as plain text
      const plain = msg.replace(/[*_`]/g, '').replace(/━+/g, '---');
      await this.sendMessage(plain);
    }
  }
}
