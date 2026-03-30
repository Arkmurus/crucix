// Telegram Alerter - Complete Intelligence Bot with Full Message Display
// Features: Full OSINT messages, no truncation, proper formatting, watchlist alerts
// FIXED: evaluateAndAlert and evaluateAndAlertWithHistory now functional

import { createHash } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join } from 'path';

const TELEGRAM_API = 'https://api.telegram.org';
const TELEGRAM_MAX_TEXT = 4096;

// Use the public Render URL for API calls
const API_BASE_URL = process.env.RENDER_EXTERNAL_URL || 'https://crucix-m4lt.onrender.com';

// Dashboard credentials for API authentication
const DASHBOARD_USER = process.env.DASHBOARD_USER || 'arkmurus';
const DASHBOARD_PASS = process.env.DASHBOARD_PASS || 'Crucix2026!';
const AUTH_HEADER = `Basic ${Buffer.from(`${DASHBOARD_USER}:${DASHBOARD_PASS}`).toString('base64')}`;

const COMMANDS = {
  '/status': 'System health and source status',
  '/brief': 'Executive intelligence summary',
  '/full': 'Complete intelligence report (full details)',
  '/osint': 'Send all urgent OSINT messages individually (full text)',
  '/search': 'Due diligence on company (e.g., /search Lockheed Martin)',
  '/contracts': 'All defense contracts (full list)',
  '/arms': 'SIPRI arms trade data (full list)',
  '/conflict': 'ACLED conflict zones (full list)',
  '/watchlist': 'Your tracked companies',
  '/add': 'Add company to watchlist',
  '/remove': 'Remove from watchlist',
  '/alerts': 'Show recent alerts',
  '/testalert': 'Send a test alert',
  '/sweep': 'Trigger manual sweep',
  '/debug': 'Show debug information',
  '/supply': 'Supply chain intelligence (shortages, materials, alerts)',
  '/supplyfull': 'Complete supply chain intelligence report',
  '/help': 'Show commands'
};

export class TelegramAlerter {
  constructor({ botToken, chatId, port = 3117 }) {
    this.botToken = botToken;
    this.chatId = chatId;
    this.port = port;
    this._alertHistory = [];
    this._lastUpdateId = 0;
    this._commandHandlers = {};
    this._pollingInterval = null;
    this._botUsername = null;
    this._watchlist = new Map();
    this._previousData = null;

    this._cache = {
      data: null,
      timestamp: 0,
      ttl: 30000
    };

    this._loadWatchlist();

    const allowedUsers = process.env.TELEGRAM_ALLOWED_USERS;
    this._allowedUsers = allowedUsers ? new Set(allowedUsers.split(',').map(id => id.trim())) : new Set([this.chatId]);
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
        headers: { 'Authorization': AUTH_HEADER }
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
      currentList.forEach((c, i) => message += `${i + 1}. ${c}\n`);
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
        headers: { 'Authorization': AUTH_HEADER }
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
      if (!data) return `Loading intelligence data...`;

      let message = `CRUCIX INTELLIGENCE BRIEF\n`;
      message += `${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC\n\n`;

      if (data.delta?.summary) {
        const ds = data.delta.summary;
        const dirEmoji = ds.direction === 'risk-off' ? 'DOWN' : ds.direction === 'risk-on' ? 'UP' : 'MIXED';
        message += `DIRECTION: ${dirEmoji}\n`;
        message += `Changes: ${ds.totalChanges} | Critical: ${ds.criticalChanges || 0}\n\n`;
      }

      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        message += `DEFENSE CONTRACTS (${contracts.length})\n`;
        message += `----------------------------------------\n`;
        contracts.slice(0, 3).forEach((c, i) => { message += `${i + 1}. ${c.title}\n`; });
        if (contracts.length > 3) message += `\n... and ${contracts.length - 3} more. Use /contracts for full list.\n`;
        message += `\n`;
      }

      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy;
      message += `MARKETS\n`;
      message += `VIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;

      const urgentCount = data.tg?.urgent?.length || 0;
      message += `URGENT OSINT: ${urgentCount} active signals\n\n`;
      message += `Commands:\n/full - Complete report\n/osint - All OSINT messages\n/search [company] - Due diligence\n/supply - Supply chain intelligence\n`;

      return message;
    } catch (error) {
      return `Brief failed: ${error.message}`;
    }
  }

  async _handleFullReport() {
    try {
      const data = await this._getCachedData();
      if (!data) return `Loading data...`;

      let message = `COMPLETE INTELLIGENCE REPORT\n`;
      message += `${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC\n\n`;

      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        message += `DEFENSE CONTRACTS (${contracts.length})\n`;
        message += `----------------------------------------\n`;
        contracts.slice(0, 10).forEach((c, i) => { message += `${i + 1}. ${c.title}\n`; });
        message += `\n`;
      }

      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy;
      message += `MARKETS\n`;
      message += `VIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;

      const urgent = data.tg?.urgent || [];
      if (urgent.length > 0) {
        message += `URGENT OSINT (${urgent.length})\n`;
        message += `----------------------------------------\n`;
        urgent.slice(0, 5).forEach((s, i) => {
          const text = s.text || s;
          const channel = s.channel ? `${s.channel}: ` : '';
          message += `${i + 1}. ${channel}${text.substring(0, 150)}...\n\n`;
        });
      }

      return message;
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  async _sendFullOSINT() {
    try {
      const data = await this._getCachedData();
      const urgent = data?.tg?.urgent || [];
      if (urgent.length === 0) return `No urgent OSINT messages.`;

      await this.sendMessage(`URGENT OSINT MESSAGES (${urgent.length})`);

      for (let i = 0; i < urgent.length; i++) {
        const s = urgent[i];
        const text = s.text || s;
        const channel = s.channel ? `${s.channel}` : 'Unknown';
        const views = s.views ? `Views: ${s.views.toLocaleString()}` : '';
        let msg = `${i + 1}. ${channel}\n`;
        if (views) msg += `${views}\n`;
        msg += `\n${text}\n\n----------------------------------------`;
        await this.sendMessage(msg);
        await new Promise(r => setTimeout(r, 300));
      }

      return `Sent ${urgent.length} full OSINT messages.`;
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
      return `DEBUG\n\nDefense: ${data?.defense?.updates?.length || 0}\nSIPRI: ${data?.sipri?.updates?.length || 0}\nOSINT: ${data?.tg?.urgent?.length || 0}\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0}\nSupply Chain: ${data?.supplyChain?.updates?.length || 0}`;
    } catch (error) {
      return `Debug error: ${error.message}`;
    }
  }

  async triggerManualSweep() {
    try {
      const response = await fetch(`${API_BASE_URL}/api/sweep`, {
        method: 'POST',
        headers: { 'Authorization': AUTH_HEADER }
      });
      const result = await response.json();
      return result.success ? 'Manual sweep triggered.' : 'Sweep failed.';
    } catch (error) {
      return `Failed: ${error.message}`;
    }
  }

  // ========== SUPPLY CHAIN INTELLIGENCE ==========

  async _handleSupplyChain() {
    try {
      const data = await this._getCachedData();
      const supply = data.supplyChain;
      if (!supply) return `Supply Chain Intelligence\n\nNo data available.`;

      let message = `SUPPLY CHAIN INTELLIGENCE\n`;
      message += `${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC\n\n`;

      const criticalAlerts = supply.metrics?.alerts?.filter(a => a.type === 'critical') || [];
      if (criticalAlerts.length > 0) {
        message += `CRITICAL ALERTS\n`;
        criticalAlerts.forEach(a => { message += `- ${a.message}\n`; });
        message += `\n`;
      }

      const shortages = supply.metrics?.shortages?.slice(0, 3) || [];
      if (shortages.length > 0) {
        message += `COMPONENT SHORTAGES\n`;
        shortages.forEach(s => { message += `- ${s.component}: ${s.status} (${s.leadTime})\n`; });
        message += `\n`;
      }

      const materials = supply.metrics?.rawMaterials?.slice(0, 3) || [];
      if (materials.length > 0) {
        message += `RAW MATERIALS\n`;
        materials.forEach(m => { message += `- ${m.name}: ${m.price} (${m.change})\n`; });
        message += `\n`;
      }

      message += `Commands:\n/supplyfull - Complete supply chain report\n`;
      return message;
    } catch (error) {
      return `Supply Chain Error: ${error.message}`;
    }
  }

  async _handleSupplyFull() {
    try {
      const data = await this._getCachedData();
      const supply = data.supplyChain;
      if (!supply) return `Supply Chain Intelligence\n\nNo data available.`;

      let message = `COMPLETE SUPPLY CHAIN REPORT\n`;
      message += `${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC\n\n`;

      const alerts = supply.metrics?.alerts || [];
      if (alerts.length > 0) {
        message += `ALERTS (${alerts.length})\n`;
        alerts.forEach(a => { message += `- ${a.message}\n`; });
        message += `\n`;
      }

      const shortages = supply.metrics?.shortages || [];
      if (shortages.length > 0) {
        message += `COMPONENT SHORTAGES (${shortages.length})\n`;
        shortages.forEach(s => {
          message += `- ${s.component}\n  Status: ${s.status} | Lead Time: ${s.leadTime}\n  Impact: ${s.impact}\n`;
        });
        message += `\n`;
      }

      const materials = supply.metrics?.rawMaterials || [];
      if (materials.length > 0) {
        message += `RAW MATERIALS (${materials.length})\n`;
        materials.forEach(m => {
          message += `- ${m.name}: ${m.price} (${m.change})\n  Impact: ${m.impact}\n`;
        });
        message += `\n`;
      }

      const manufacturing = supply.metrics?.manufacturing || [];
      if (manufacturing.length > 0) {
        message += `MANUFACTURING CAPACITY\n`;
        manufacturing.forEach(m => {
          message += `- ${m.region}: ${m.capacity}\n  ${m.notes}\n`;
        });
      }

      return message;
    } catch (error) {
      return `Supply Chain Error: ${error.message}`;
    }
  }

  // ========== FIXED: SWEEP COMPLETE & ALERT LOGIC ==========

  async onSweepComplete(currentData) {
    this._cache.data = currentData;
    this._cache.timestamp = Date.now();
    if (this._previousData) {
      await this.evaluateAndAlertWithHistory(this._previousData, currentData);
    }
    this._previousData = currentData;
  }

  // FIX: was empty stub — now compares previous vs current sweep and sends alert if anything changed
  async evaluateAndAlertWithHistory(previousData, currentData) {
    try {
      const prevUrgent = previousData?.tg?.urgent?.length || 0;
      const currUrgent = currentData?.tg?.urgent?.length || 0;
      const newSignals = currUrgent - prevUrgent;

      const prevNews = previousData?.newsFeed?.length || 0;
      const currNews = currentData?.newsFeed?.length || 0;
      const newNews = currNews - prevNews;

      // Only alert if there is something genuinely new
      if (newSignals <= 0 && newNews <= 0) {
        console.log('[Telegram] No new signals since last sweep — no alert sent');
        return false;
      }

      return this.evaluateAndAlert(null, currentData.delta, null, { newSignals, newNews, currentData });
    } catch (err) {
      console.error('[Telegram] evaluateAndAlertWithHistory error:', err.message);
      return false;
    }
  }

  // FIX: was empty stub — now builds and sends a real Telegram alert message
  async evaluateAndAlert(llmProvider, delta, memory, context = {}) {
    try {
      const data = context.currentData || await this._getCachedData();
      if (!data) return false;

      const urgent      = data.tg?.urgent    || [];
      const newsFeed    = data.newsFeed      || [];
      const vix         = data.fred?.find(f => f.id === 'VIXCLS');
      const oil         = data.energy        || {};
      const ideas       = data.ideas         || [];
      const correlations = data.correlations || [];
      const polymarkets  = data.polymarket?.markets || data.sources?.Polymarket?.markets || [];
      const ts = new Date().toISOString().slice(0,19).replace('T',' ');

      const SEP  = '━━━━━━━━━━━━━━━━━━━━━━━━━━━━━';
      const LINE = '─────────────────────────────';

      let msg = SEP + '\n';
      msg += '🛰 *ARKMURUS INTELLIGENCE*\n';
      msg += SEP + '\n';
      msg += '🕐 _' + ts + ' UTC_\n\n';

      // MARKET PULSE
      const vixVal = vix?.value;
      const vixIcon = vixVal > 30 ? '🔴' : vixVal > 20 ? '🟡' : '🟢';
      msg += '📊 *MARKET PULSE*\n';
      msg += LINE + '\n';
      if (vixVal) msg += vixIcon + ' VIX ' + vixVal + '  |  ';
      if (oil.brent) msg += 'Brent $' + oil.brent + '  |  ';
      if (oil.wti) msg += 'WTI $' + oil.wti;
      if (oil.natgas) msg += '\nNatGas $' + oil.natgas + '/MMBtu';
      msg += '\n\n';

      // DIRECTION
      if (delta?.summary) {
        const dirMap = {'risk-off':'📉 RISK-OFF','risk-on':'📈 RISK-ON','mixed':'↔️ MIXED'};
        const dir = dirMap[delta.summary.direction] || '↔️ MIXED';
        msg += '🧭 ' + dir;
        if (delta.summary.criticalChanges > 0) msg += '  🔴 ' + delta.summary.criticalChanges + ' critical';
        msg += '\n\n';
      }

      // REGIONAL CONVERGENCES
      if (correlations.length > 0) {
        msg += '🌍 *CONVERGENCES*\n';
        for (const c of correlations.slice(0,4)) {
          const icon = c.severity === 'critical' ? '🔴' : c.severity === 'high' ? '🟠' : '🟡';
          msg += icon + ' ' + c.region + '\n';
        }
        msg += '\n';
      }

      // ALL URGENT OSINT
      if (urgent.length > 0) {
        msg += SEP + '\n';
        msg += '📡 *OSINT — ' + urgent.length + ' SIGNALS*\n';
        msg += SEP + '\n';
        for (let i = 0; i < Math.min(urgent.length, 12); i++) {
          const s = urgent[i];
          const ch = s.channel ? '*[' + s.channel + ']*' : '';
          const text = (s.text || '').replace(/\n+/g,' ').trim();
          msg += '\n' + (i+1) + '. ' + ch + '\n' + text.substring(0, 260) + '\n';
        }
        if (urgent.length > 12) msg += '\n_+' + (urgent.length-12) + ' more · /osint for all_\n';
        msg += '\n';
      }

      // PREDICTION MARKETS
      const topMkts = polymarkets.filter(m => m.volume24h > 20000).slice(0,4);
      if (topMkts.length > 0) {
        msg += '🎯 *PREDICTION MARKETS*\n';
        msg += LINE + '\n';
        for (const m of topMkts) {
          const icon = m.yesProb >= 75 ? '🔴' : m.yesProb >= 50 ? '🟡' : '🟢';
          msg += icon + ' *' + m.yesProb + '%* · ' + m.question.substring(0,55) + '\n';
        }
        msg += '\n';
      }

      // INTELLIGENCE OPPORTUNITIES
      if (ideas.length > 0) {
        msg += '💡 *INTELLIGENCE OPPORTUNITIES*\n';
        msg += LINE + '\n';
        for (const idea of ideas.slice(0,3)) {
          const icon = idea.type === 'long' ? '📈' : idea.type === 'hedge' ? '🛡' : '👁';
          msg += icon + ' *' + idea.title + '*\n';
          if (idea.rationale) msg += '_' + idea.rationale.substring(0,110) + '_\n';
        }
        msg += '\n';
      }

      // NEWS FEED
      if (newsFeed.length > 0) {
        msg += '📰 *FEED*\n';
        for (const n of newsFeed.slice(0,4)) {
          msg += '· ' + n.headline + ' _(' + n.source + ')_\n';
        }
        msg += '\n';
      }

      msg += SEP + '\n';
      msg += '_/brief · /osint · /predict · /arms · /conflict · /sweep_';

      const result = await this.sendMessage(msg);
      if (result.ok) {
        this._lastAlertTime = Date.now();
        this._addAlert('sweep', 'Alert sent: ' + urgent.length + ' signals', 'high');
        console.log('[Telegram] Alert sent — ' + urgent.length + ' OSINT signals, ' + newsFeed.length + ' news');
      }
      return result.ok;
    } catch (err) {
      console.error('[Telegram] evaluateAndAlert error:', err.message);
      return false;
    }
  }

  onCommand(command, handler) {
    this._commandHandlers[command.toLowerCase()] = handler;
  }

  startPolling(intervalMs = 5000) {
    if (!this.isConfigured) return;
    if (this._pollingInterval) return;
    console.log('[Telegram] Bot polling started');
    this._pollingInterval = setInterval(() => this._pollUpdates(), intervalMs);
    this._pollUpdates();
  }

  stopPolling() {
    if (this._pollingInterval) {
      clearInterval(this._pollingInterval);
      this._pollingInterval = null;
    }
  }

  async _pollUpdates() {
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
  }

  async _handleMessage(msg) {
    const text      = msg.text.trim();
    const parts     = text.split(/\s+/);
    const rawCommand = parts[0].toLowerCase();
    const command   = rawCommand.startsWith('/') ? rawCommand.split('@')[0] : null;
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
    const botCommands = Object.entries(COMMANDS).map(([c, d]) => ({ command: c.replace('/', ''), description: d.substring(0, 256) }));
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
      signal: AbortSignal.timeout(10000)
    });
  }

  _buildConfiguredChatScope() {
    return { type: 'chat', chat_id: Number(this.chatId) };
  }
}
