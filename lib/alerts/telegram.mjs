// Telegram Alerter - Complete Intelligence Bot with Full Message Display
// Features: Full OSINT messages, no truncation, proper formatting, watchlist alerts

import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const TELEGRAM_API = 'https://api.telegram.org';
const TELEGRAM_MAX_TEXT = 4096;

const API_BASE_URL = process.env.RENDER_EXTERNAL_URL || 'https://crucix-m4lt.onrender.com';

const DASHBOARD_USER = process.env.DASHBOARD_USER || 'arkmurus';
const DASHBOARD_PASS = process.env.DASHBOARD_PASS || 'Crucix2026!';
const AUTH_HEADER = `Basic ${Buffer.from(`${DASHBOARD_USER}:${DASHBOARD_PASS}`).toString('base64')}`;

const COMMANDS = {
  '/status':     'System health and source status',
  '/brief':      'Executive intelligence summary',
  '/full':       'Complete intelligence report',
  '/osint':      'Send all urgent OSINT messages individually',
  '/search':     'Due diligence on company (e.g., /search Lockheed Martin)',
  '/predict':    'Prediction market odds on geopolitical events',
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
  '/help':       'Show commands',
  '/predict':    'Prediction market odds (e.g., /predict iran)',
  '/risk':       'Counterparty risk score (e.g., /risk Rosoboronexport)',
};

function formatVol(v) {
  if (v >= 1000000) return `${(v / 1000000).toFixed(1)}M`;
  if (v >= 1000)    return `${(v / 1000).toFixed(0)}K`;
  return String(v);
}

export class TelegramAlerter {
  constructor({ botToken, chatId, port = 3117 }) {
    this.botToken = botToken;
    this.chatId = chatId;
    this.port = port;
    this._alertHistory = [];
    this._lastUpdateId = 0;
    this._commandHandlers = {};
    this._pollingInterval = null;
    this._pollBusy = false;
    this._botUsername = null;
    this._watchlist = new Map();
    this._previousData = null;
    this._lastAlertTime = 0;
    this._alertIntervalMs = 3 * 60 * 60 * 1000; // 3 hours

    this._cache = { data: null, timestamp: 0, ttl: 30000 };

    // Flash alert rate-limiting: max 3 per hour for critical signals
    this._flashCount = 0;
    this._flashHourStart = Date.now();

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

  async _handleWatchlist(args) {
    const parts = args.trim().split(/\s+/);
    const action = parts[0]?.toLowerCase();
    const company = parts.slice(1).join(' ');
    const currentList = this._watchlist.get(this.chatId) || [];

    if (action === 'list' || (!action && !company)) {
      if (currentList.length === 0) return `Your watchlist is empty\n\nUse /add [company] to add companies.`;
      let message = `YOUR WATCHLIST\n\n`;
      currentList.forEach((c, i) => { message += `${i + 1}. ${c}\n`; });
      message += `\nUse /search [company] for due diligence`;
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
          console.error('[Telegram] Send failed:', await res.text());
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
    const sections = message.split(/(?=━|📋|🔫|⚔️|📊|📡)/);
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

  async _handleCompanySearch(companyName) {
    if (!companyName?.trim()) return `Company Search\n\nProvide a company name.\nExample: /search Lockheed Martin`;
    try {
      const response = await fetch(`${API_BASE_URL}/api/search?q=${encodeURIComponent(companyName)}`, {
        headers: { 'Authorization': AUTH_HEADER },
      });
      const data = await response.json();
      if (!data.success) return `No information found for "${companyName}".`;

      let message = `DUE DILIGENCE: ${companyName.toUpperCase()}\n\n`;
      if (data.wikipedia?.extract) message += `Wikipedia:\n${data.wikipedia.extract}\n\n`;
      if (data.duckduckgo?.abstract) message += `DuckDuckGo:\n${data.duckduckgo.abstract}\n\n`;
      if (data.verificationLinks) {
        message += `Due Diligence Resources:\n`;
        message += `OpenCorporates: ${data.verificationLinks.openCorporates}\n`;
        message += `OFAC Sanctions: ${data.verificationLinks.ofacSanctions}\n`;
        message += `Defense News: ${data.verificationLinks.defenseNews}\n`;
        message += `SEC EDGAR: ${data.verificationLinks.secEdgar}\n`;
      }
      return message;
    } catch (error) {
      return `Search failed: ${error.message}`;
    }
  }

  async _handleBrief() {
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Intelligence data is loading — please try again in 60 seconds.`;

      const ts  = new Date().toISOString().slice(0, 19).replace('T', ' ');
      const ds  = data.delta?.summary || {};
      const dir = ds.direction;
      const dirLine = dir === 'risk-off' ? '📉 Risk-off environment — defensive posture advised'
                    : dir === 'risk-on'  ? '📈 Risk-on environment — conditions broadly constructive'
                    : '↔️ Mixed signals — situation fluid, monitor closely';

      let msg = `*ARKMURUS INTELLIGENCE BRIEF*\n`;
      msg += `_${ts} UTC_\n`;
      msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

      // Direction + market context
      msg += `*MARKET ENVIRONMENT*\n`;
      msg += `${dirLine}. `;
      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy || {};
      if (vix?.value) msg += `Volatility index (VIX) stands at *${vix.value}*${vix.value > 25 ? ' — elevated, indicating market stress' : ' — within normal range'}. `;
      if (oil.brent)  msg += `Brent crude is at *$${oil.brent}/bbl*`;
      if (oil.wti)    msg += `, WTI at *$${oil.wti}/bbl*.`;
      msg += `\n\n`;

      // Regional convergences
      const corrs = data.correlations || [];
      const critCorrs = corrs.filter(c => c.severity === 'critical' || c.severity === 'high');
      if (critCorrs.length > 0) {
        msg += `*ACTIVE HOTSPOTS*\n`;
        for (const c of critCorrs.slice(0, 4)) {
          const badge = c.severity === 'critical' ? '🔴' : '🟠';
          const signalCount = c.signalCount || c.sources?.length || 0;
          msg += `${badge} *${c.region}* — ${c.severity.toUpperCase()}. ${signalCount} independent sources reporting concurrently`;
          const top = c.topSignals?.[0]?.text;
          if (top) msg += `: "${top.substring(0, 100)}"`;
          msg += `.\n`;
        }
        msg += `\n`;
      }

      // OSINT signals
      const urgent = data.tg?.urgent || [];
      if (urgent.length > 0) {
        msg += `*URGENT OSINT (${urgent.length} signals)*\n`;
        for (const s of urgent.slice(0, 3)) {
          const ch   = s.channel || 'OSINT';
          const text = (s.text || '').trim().replace(/\n+/g, ' ');
          msg += `📡 *[${ch}]* ${text.substring(0, 180)}\n\n`;
        }
      }

      // Defense contracts
      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        msg += `*DEFENSE & PROCUREMENT (${contracts.length} items)*\n`;
        for (const c of contracts.slice(0, 2)) {
          msg += `🔫 ${c.title}\n`;
          if (c.content) msg += `_${c.content.substring(0, 120)}_\n`;
          msg += `\n`;
        }
        if (contracts.length > 2) msg += `_Use /contracts for the full list._\n\n`;
      }

      // Delta changes summary
      if (ds.totalChanges > 0) {
        msg += `*DELTA CHANGES*\n`;
        msg += `This sweep detected *${ds.totalChanges}* measurable changes across tracked indicators`;
        if (ds.criticalChanges > 0) msg += `, of which *${ds.criticalChanges}* are classified critical`;
        msg += `.\n\n`;
      }

      msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
      msg += `_Commands: /full · /osint · /supply · /arms · /predict · /trends_`;

      return msg;
    } catch (error) {
      return `Brief failed: ${error.message}`;
    }
  }

  async _handleFullReport() {
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Loading data — please retry in 60 seconds.`;

      const ts  = new Date().toISOString().slice(0, 19).replace('T', ' ');
      const ds  = data.delta?.summary || {};

      let msg = `*COMPLETE INTELLIGENCE REPORT*\n`;
      msg += `_${ts} UTC | Arkmurus Crucix v2_\n`;
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
      const time = new Date(alert.timestamp).toLocaleTimeString();
      message += `${i + 1}. ${time} - ${alert.message}\n\n`;
    });
    return message;
  }

  async _testAlert() {
    const testMessage = `TEST ALERT\n\nThis is a test alert from Crucix.\n\n${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC`;
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

      const ts  = new Date().toISOString().slice(0, 16).replace('T', ' ');
      let msg   = `*SUPPLY CHAIN INTELLIGENCE*\n_${ts} UTC_\n━━━━━━━━━━━━━━━\n\n`;

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

      const ts  = new Date().toISOString().slice(0, 16).replace('T', ' ');
      let msg   = `*COMPLETE SUPPLY CHAIN REPORT*\n_${ts} UTC_\n━━━━━━━━━━━━━━━\n\n`;

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

      const ts  = new Date().toISOString().slice(0, 16).replace('T', ' ');
      let msg   = `*PREDICTION MARKETS*\n_${ts} UTC · Polymarket_\n━━━━━━━━━━━━━━━\n\n`;

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
      const params = new URLSearchParams({ offset: String(this._lastUpdateId + 1), timeout: '0', limit: '10' });
      const res = await fetch(`${TELEGRAM_API}/bot${this.botToken}/getUpdates?${params}`, { signal: AbortSignal.timeout(10000) });
      if (!res.ok) return;
      const data = await res.json();
      if (!data.ok) return;

      for (const update of data.result) {
        this._lastUpdateId = Math.max(this._lastUpdateId, update.update_id);
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
      '/predict':    () => this._handlePredict(args),
      '/status':     () => `CRUCIX ONLINE\n\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0} companies\nSupply Chain: Active`,
      '/alerts':     () => this._handleAlerts(),
      '/testalert':  () => this._testAlert(),
      '/help':       () => Object.entries(COMMANDS).map(([c, d]) => `${c} - ${d}`).join('\n'),
    };

    const handler = handlers[command] || this._commandHandlers[command];
    if (handler) {
      const response = await handler();
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
    // Reset hourly counter
    if (now - this._flashHourStart > 3600000) {
      this._flashCount    = 0;
      this._flashHourStart = now;
    }
    if (this._flashCount >= 3) return; // max 3 flash alerts per hour

    this._flashCount++;
    const ts  = new Date().toISOString().slice(0, 19).replace('T', ' ');
    const msg = `🚨 *FLASH ALERT*\n_${ts} UTC_\n\n*[${signal.source}]* ${signal.text}\n\n_Crucix early warning — /brief for full context._`;
    console.log(`[Telegram] FLASH ALERT sent (${this._flashCount}/3 this hour): ${signal.text.substring(0, 60)}`);
    await this.sendMessage(msg);
  }

  // Called after every sweep cycle. Sends alerts on the 3-hour cadence,
  // only for signals that haven't been sent before (dedup).
  async onSweepComplete(data) {
    if (!this.isConfigured) return;

    const now = Date.now();

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
    for (const c of (data.correlations || [])) {
      if (c.severity === 'critical' || c.severity === 'high') {
        const top = c.topSignals?.[0]?.text || '';
        candidates.push({
          text:     `${c.region}: ${c.signalCount} signals across ${c.sourceCount} sources. ${top}`,
          source:   'correlation',
          priority: c.severity,
        });
      }
    }

    // Lusophone critical alerts
    for (const a of (data.lusophone?.alerts || [])) {
      candidates.push({ text: a.text, source: a.source || 'Lusophone', priority: 'critical' });
    }

    // Urgent OSINT (top 6 by views/score)
    for (const s of (data.tg?.urgent || []).slice(0, 6)) {
      candidates.push({
        text:     (s.text || '').substring(0, 200),
        source:   s.channel || 'OSINT',
        priority: 'high',
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

    if (candidates.length === 0) return;

    // ── Dedup: only send signals not seen in the last 48 h ──────────────────
    let newSignals;
    try {
      const { filterNewSignals } = await import('../intel/dedup.mjs');
      newSignals = filterNewSignals(candidates);
    } catch {
      newSignals = candidates;
    }

    if (newSignals.length === 0) {
      console.log('[Telegram] All signals already seen — no alert sent');
      return;
    }

    // ── Flash path: send immediately for ultra-critical signals ──────────────
    const FLASH_KEYWORDS = [
      'coup', 'overthrow', 'nuclear', 'invasion', 'invaded', 'war declared',
      'assassination', 'exchange halted', 'circuit breaker', 'martial law',
      'chemical weapon', 'dirty bomb', 'imminent attack',
    ];
    for (const s of newSignals) {
      const lower = s.text.toLowerCase();
      if (FLASH_KEYWORDS.some(kw => lower.includes(kw))) {
        await this._sendFlashAlert(s);
      }
    }

    // ── 3-hour cadence check for digest alert ────────────────────────────────
    if (now - this._lastAlertTime < this._alertIntervalMs) return;
    this._lastAlertTime = now;

    // ── Format ───────────────────────────────────────────────────────────────
    const ds       = data.delta?.summary || {};
    const dir      = ds.direction;
    const dirEmoji = { 'risk-off': '📉', 'risk-on': '📈', 'mixed': '↔️' }[dir] || '↔️';
    const ts       = new Date().toISOString().slice(0, 19).replace('T', ' ');

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
    msg += `_${ts} UTC · ${newSignals.length} new signal${newSignals.length !== 1 ? 's' : ''}_\n`;
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
      for (const s of criticals) {
        const text = s.text.trim().replace(/\n+/g, ' ');
        msg += `\n*[${s.source}]* ${text.substring(0, 280)}\n`;
      }
      msg += `\n`;
    }

    // High signals — show ALL of them
    if (highs.length > 0) {
      msg += `⚠️ *HIGH PRIORITY — ${highs.length} signal${highs.length !== 1 ? 's' : ''}*\n`;
      msg += `━━━━━━━━━━━━━━━━━\n`;
      for (const s of highs) {
        const text = s.text.trim().replace(/\n+/g, ' ');
        msg += `\n*[${s.source}]* ${text.substring(0, 220)}\n`;
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

    msg += `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
    msg += `_For full analysis: /brief · /full · /osint · /predict_`;

    console.log(`[Telegram] Sending sweep digest: ${newSignals.length} signals (${criticals.length} critical, ${highs.length} high)`);
    await this.sendMessage(msg);
  }
}
