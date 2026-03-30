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
  '/status':     'System health and source status',\\n  '/brief':      'Executive intelligence summary',\\n  '/full':       'Complete intelligence report',\\n  '/osint':      'Send all urgent OSINT messages individually',\\n  '/search':     'Due diligence on company (e.g., /search Lockheed Martin)',\\n  '/predict':    'Prediction market odds on geopolitical events',\\n  '/contracts':  'All defense contracts (full list)',\\n  '/arms':       'SIPRI arms trade data (full list)',\\n  '/conflict':   'ACLED conflict zones (full list)',\\n  '/watchlist':  'Your tracked companies',\\n  '/add':        'Add company to watchlist',\\n  '/remove':     'Remove from watchlist',\\n  '/alerts':     'Show recent alerts',\\n  '/testalert':  'Send a test alert',\\n  '/sweep':      'Trigger manual sweep',\\n  '/debug':      'Show debug information',\\n  '/supply':     'Supply chain intelligence',\\n  '/supplyfull': 'Complete supply chain intelligence report',\\n  '/help':       'Show commands',\\n  '/predict':    'Prediction market odds (e.g., /predict iran)',\\n  '/risk':       'Counterparty risk score (e.g., /risk Rosoboronexport)',
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
    this._botUsername = null;
    this._watchlist = new Map();
    this._previousData = null;
    this._lastAlertTime = 0;
    this._alertIntervalMs = 3 * 60 * 60 * 1000; // 3 hours

    this._cache = { data: null, timestamp: 0, ttl: 30000 };

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
      if (!data) return `Loading intelligence data...`;

      let message = `CRUCIX INTELLIGENCE BRIEF\n`;
      message += `${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC\n\n`;

      if (data.delta?.summary) {
        const ds = data.delta.summary;
        const dir = ds.direction === 'risk-off' ? 'DOWN' : ds.direction === 'risk-on' ? 'UP' : 'MIXED';
        message += `DIRECTION: ${dir}\nChanges: ${ds.totalChanges} | Critical: ${ds.criticalChanges || 0}\n\n`;
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
      message += `MARKETS\nVIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;

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
      message += `MARKETS\nVIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;

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

  async _handlePredict(q){
    try{
      const d=await this._getCachedData();
      const mk=(d&&d.polymarket&&d.polymarket.markets)||[];
      if(!mk.length)return"No data yet";
      let o="PREDICTION MARKETS\n\n";
      mk.slice(0,8).forEach(function(x){o+=x.yesProb+"% - "+x.question+"\n";});
      return o;
    }catch(e){return"Error: "+e.message;}
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
}
