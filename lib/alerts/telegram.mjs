// Telegram Alerter - Complete Intelligence Bot with Full Message Display
// Features: Full OSINT messages, no truncation, proper formatting, watchlist alerts

import { createHash } from 'crypto';

const TELEGRAM_API = 'https://api.telegram.org';
const TELEGRAM_MAX_TEXT = 4096;

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
      const response = await fetch(`http://localhost:${this.port}/api/data`);
      const data = await response.json();
      this._cache.data = data;
      this._cache.timestamp = now;
      return data;
    } catch (error) {
      return this._cache.data || null;
    }
  }

  _getWatchlistFilePath() {
    return `watchlist_${this.chatId}.json`;
  }

  _loadWatchlist() {
    try {
      const fs = require('fs');
      const path = require('path');
      const filePath = path.join(process.cwd(), this._getWatchlistFilePath());
      if (fs.existsSync(filePath)) {
        const data = fs.readFileSync(filePath, 'utf8');
        const parsed = JSON.parse(data);
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
      const fs = require('fs');
      const path = require('path');
      const filePath = path.join(process.cwd(), this._getWatchlistFilePath());
      const data = JSON.stringify({ companies: this._watchlist.get(this.chatId) || [] }, null, 2);
      fs.writeFileSync(filePath, data, 'utf8');
    } catch (err) {}
  }

  async _handleWatchlist(args) {
    const parts = args.trim().split(/\s+/);
    const action = parts[0]?.toLowerCase();
    const company = parts.slice(1).join(' ');
    const currentList = this._watchlist.get(this.chatId) || [];

    if (action === 'list' || (!action && !company)) {
      if (currentList.length === 0) return `📋 *Your watchlist is empty*\n\nUse /add [company] to add companies.`;
      let message = `📋 *YOUR WATCHLIST*\n\n`;
      currentList.forEach((c, i) => message += `${i+1}. ${c}\n`);
      message += `\n💡 Use /search [company] for due diligence`;
      return message;
    }

    if (action === 'add' && company) {
      if (!currentList.includes(company)) {
        currentList.push(company);
        this._watchlist.set(this.chatId, currentList);
        this._saveWatchlist();
        this._addAlert('watchlist', `Added ${company} to watchlist`, 'info');
        return `✅ Added *${company}* to watchlist.`;
      }
      return `⚠️ *${company}* already in watchlist.`;
    }

    if (action === 'remove' && company) {
      const index = currentList.indexOf(company);
      if (index !== -1) {
        currentList.splice(index, 1);
        this._watchlist.set(this.chatId, currentList);
        this._saveWatchlist();
        this._addAlert('watchlist', `Removed ${company} from watchlist`, 'info');
        return `✅ Removed *${company}* from watchlist.`;
      }
      return `❌ *${company}* not found.`;
    }

    return `Use /add [company], /remove [company], or /watchlist`;
  }

  _addAlert(type, message, severity = 'info') {
    this._alertHistory.unshift({
      type,
      message,
      severity,
      timestamp: Date.now()
    });
    if (this._alertHistory.length > 50) {
      this._alertHistory = this._alertHistory.slice(0, 50);
    }
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
    const sections = message.split(/(?=━━━|📋|🔫|⚔️|📊|📡)/);
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
        console.error(`[Telegram] Chunk ${i+1} error:`, err.message);
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
    if (!companyName?.trim()) return `🔍 *Company Search*\n\nProvide a company name.\nExample: /search Lockheed Martin`;

    try {
      const response = await fetch(`http://localhost:${this.port}/api/search?q=${encodeURIComponent(companyName)}`);
      const data = await response.json();

      if (!data.success) return `❌ No information found for "${companyName}".`;

      let message = `🔍 *DUE DILIGENCE: ${companyName.toUpperCase()}*\n\n`;
      
      if (data.wikipedia?.extract) {
        message += `📖 *Wikipedia:*\n${data.wikipedia.extract}\n\n`;
      }
      
      if (data.duckduckgo?.abstract) {
        message += `🔍 *DuckDuckGo:*\n${data.duckduckgo.abstract}\n\n`;
      }
      
      if (data.verificationLinks) {
        message += `🛡️ *Due Diligence Resources:*\n`;
        message += `📋 OpenCorporates: ${data.verificationLinks.openCorporates}\n`;
        message += `⚖️ OFAC Sanctions: ${data.verificationLinks.ofacSanctions}\n`;
        message += `📰 Defense News: ${data.verificationLinks.defenseNews}\n`;
        message += `💰 SEC EDGAR: ${data.verificationLinks.secEdgar}\n`;
      }
      
      return message;
    } catch (error) {
      return `❌ Search failed: ${error.message}`;
    }
  }

  async _handleBrief() {
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Loading intelligence data...`;
      
      let message = `📋 *CRUCIX INTELLIGENCE BRIEF*\n`;
      message += `_${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC_\n\n`;
      
      if (data.delta?.summary) {
        const ds = data.delta.summary;
        const dirEmoji = ds.direction === 'risk-off' ? '📉' : ds.direction === 'risk-on' ? '📈' : '↔️';
        message += `*${dirEmoji} DIRECTION: ${ds.direction.toUpperCase()}*\n`;
        message += `Changes: ${ds.totalChanges} | Critical: ${ds.criticalChanges || 0}\n\n`;
      }
      
      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        message += `📋 *DEFENSE CONTRACTS (${contracts.length})*\n`;
        message += `━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;
        contracts.slice(0, 5).forEach((c, i) => {
          message += `${i+1}. ${c.title}\n`;
        });
        if (contracts.length > 5) {
          message += `\n... and ${contracts.length - 5} more. Use /contracts for full list.\n`;
        }
        message += `\n`;
      } else {
        message += `📋 *DEFENSE CONTRACTS*\nNo contracts in current sweep\n\n`;
      }
      
      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy;
      message += `📊 *MARKETS*\n`;
      message += `VIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;
      
      const urgentCount = data.tg?.urgent?.length || 0;
      message += `📡 *URGENT OSINT*: ${urgentCount} active signals\n\n`;
      
      message += `💡 *Commands:*\n`;
      message += `/full - Complete report\n`;
      message += `/osint - All OSINT messages\n`;
      message += `/search [company] - Due diligence\n`;
      message += `/supply - Supply chain intelligence\n`;
      
      return message;
    } catch (error) {
      return `❌ Brief failed: ${error.message}`;
    }
  }

  async _handleFullReport() {
    try {
      const data = await this._getCachedData();
      if (!data) return `⏳ Loading data...`;
      
      let message = `📊 *COMPLETE INTELLIGENCE REPORT*\n`;
      message += `_${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC_\n\n`;
      
      const contracts = data.defense?.updates || [];
      if (contracts.length > 0) {
        message += `📋 *DEFENSE CONTRACTS (${contracts.length})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;
        contracts.forEach((c, i) => {
          message += `${i+1}. ${c.title}\n`;
          if (c.content) message += `   ${c.content}\n\n`;
        });
      }
      
      const vix = data.fred?.find(f => f.id === 'VIXCLS');
      const oil = data.energy;
      message += `📊 *MARKETS*\n`;
      message += `VIX: ${vix?.value || '--'} | WTI: $${oil?.wti || '--'} | Brent: $${oil?.brent || '--'}\n\n`;
      
      const urgent = data.tg?.urgent || [];
      if (urgent.length > 0) {
        message += `📡 *URGENT OSINT (${urgent.length})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;
        urgent.forEach((s, i) => {
          const text = s.text || s;
          const channel = s.channel ? `📢 *${s.channel}*\n` : '';
          message += `${i+1}. ${channel}${text}\n\n─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─\n\n`;
        });
      }
      
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async _sendFullOSINT() {
    try {
      const data = await this._getCachedData();
      const urgent = data?.tg?.urgent || [];
      
      if (urgent.length === 0) return `📡 No urgent OSINT messages.`;
      
      await this.sendMessage(`📡 *URGENT OSINT MESSAGES (${urgent.length})*\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━`);
      
      for (let i = 0; i < urgent.length; i++) {
        const s = urgent[i];
        const text = s.text || s;
        const channel = s.channel ? `📢 *${s.channel}*` : '📢 Unknown';
        const views = s.views ? `👁️ ${s.views.toLocaleString()} views` : '';
        
        let msg = `${i+1}. ${channel}\n`;
        if (views) msg += `${views}\n`;
        msg += `\n${text}\n\n─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─ ─`;
        
        await this.sendMessage(msg);
        await new Promise(r => setTimeout(r, 300));
      }
      
      return `✅ Sent ${urgent.length} full OSINT messages.`;
    } catch (error) {
      return `❌ Error: ${error.message}`;
    }
  }

  async _handleContracts() {
    try {
      const data = await this._getCachedData();
      const contracts = data.defense?.updates || [];
      if (contracts.length === 0) return `📋 *DEFENSE CONTRACTS*\n\nNo contracts.`;
      
      let message = `📋 *DEFENSE CONTRACTS (${contracts.length})*\n`;
      contracts.forEach((c, i) => {
        message += `${i+1}. ${c.title}\n`;
        if (c.content) message += `   ${c.content}\n\n`;
      });
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async _handleArms() {
    try {
      const data = await this._getCachedData();
      const arms = data.sipri?.updates || [];
      if (arms.length === 0) return `🔫 *ARMS TRADE*\n\nNo data.`;
      
      let message = `🔫 *ARMS TRADE (SIPRI)*\n`;
      arms.forEach((a, i) => {
        message += `${i+1}. ${a.title}\n`;
        if (a.content) message += `   ${a.content}\n\n`;
      });
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async _handleConflict() {
    try {
      const data = await this._getCachedData();
      const conflicts = data.acled?.deadliestEvents || [];
      if (conflicts.length === 0) return `⚔️ *CONFLICT ZONES*\n\nNo data.`;
      
      let message = `⚔️ *CONFLICT ZONES (ACLED)*\n`;
      conflicts.forEach((c, i) => {
        message += `${i+1}. ${c.location || 'Unknown'}: ${c.fatalities || 0} fatalities\n`;
      });
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async _handleAlerts() {
    if (this._alertHistory.length === 0) return `📋 No alerts yet. Try /testalert.`;
    
    let message = `📋 *ALERT HISTORY (${this._alertHistory.length})*\n\n`;
    this._alertHistory.slice(0, 10).forEach((alert, i) => {
      const time = new Date(alert.timestamp).toLocaleTimeString();
      message += `${i+1}. 🔴 ${time} - ${alert.message}\n\n`;
    });
    return message;
  }

  async _testAlert() {
    const testMessage = `🧪 *TEST ALERT*\n\nThis is a test alert from Crucix.\n\n_${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC_`;
    await this.sendAlert(testMessage, 'test');
    return `✅ Test alert sent!`;
  }

  async _debugData() {
    try {
      const data = await this._getCachedData();
      return `🔍 *DEBUG*\n\nDefense: ${data?.defense?.updates?.length || 0}\nSIPRI: ${data?.sipri?.updates?.length || 0}\nOSINT: ${data?.tg?.urgent?.length || 0}\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0}\nSupply Chain: ${data?.supplyChain?.updates?.length || 0}`;
    } catch (error) {
      return `Debug error: ${error.message}`;
    }
  }

  async triggerManualSweep() {
    try {
      const response = await fetch(`http://localhost:${this.port}/api/sweep`, { method: 'POST' });
      const result = await response.json();
      return result.success ? '🚀 Manual sweep triggered.' : '❌ Sweep failed.';
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  // ========== SUPPLY CHAIN INTELLIGENCE ==========

  async _handleSupplyChain() {
    try {
      const response = await fetch(`http://localhost:${this.port}/api/data`);
      const data = await response.json();
      const supply = data.supplyChain;
      
      if (!supply) return `📦 *SUPPLY CHAIN INTELLIGENCE*\n\nNo data available.`;
      
      let message = `📦 *SUPPLY CHAIN INTELLIGENCE*\n`;
      message += `_${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC_\n\n`;
      
      // Critical Alerts
      const criticalAlerts = supply.metrics?.alerts?.filter(a => a.type === 'critical') || [];
      if (criticalAlerts.length > 0) {
        message += `🚨 *CRITICAL ALERTS*\n`;
        criticalAlerts.forEach(a => {
          message += `• ${a.message}\n`;
        });
        message += `\n`;
      }
      
      // Shortages
      const shortages = supply.metrics?.shortages?.slice(0, 5) || [];
      if (shortages.length > 0) {
        message += `⚠️ *COMPONENT SHORTAGES*\n`;
        shortages.forEach(s => {
          message += `• ${s.component}: ${s.status} (${s.leadTime})\n`;
        });
        message += `\n`;
      }
      
      // Raw Materials
      const materials = supply.metrics?.rawMaterials?.slice(0, 5) || [];
      if (materials.length > 0) {
        message += `📊 *RAW MATERIALS*\n`;
        materials.forEach(m => {
          const trend = m.trend === 'up' ? '📈' : m.trend === 'down' ? '📉' : '➡️';
          message += `• ${m.name}: ${m.price} ${trend} ${m.change}\n`;
        });
        message += `\n`;
      }
      
      // Logistics
      const logistics = supply.metrics?.logistics?.filter(l => l.severity === 'critical' || l.severity === 'high') || [];
      if (logistics.length > 0) {
        message += `🚢 *LOGISTICS ALERTS*\n`;
        logistics.forEach(l => {
          message += `• ${l.location}: ${l.disruption}\n`;
        });
      }
      
      message += `\n💡 *Commands*\n`;
      message += `/supplyfull - Complete supply chain report\n`;
      
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async _handleSupplyFull() {
    try {
      const response = await fetch(`http://localhost:${this.port}/api/data`);
      const data = await response.json();
      const supply = data.supplyChain;
      
      if (!supply) return `📦 *SUPPLY CHAIN INTELLIGENCE*\n\nNo data available.`;
      
      let message = `📦 *COMPLETE SUPPLY CHAIN REPORT*\n`;
      message += `_${new Date().toISOString().slice(0, 19).replace('T', ' ')} UTC_\n\n`;
      
      // All Alerts
      const alerts = supply.metrics?.alerts || [];
      if (alerts.length > 0) {
        message += `🚨 *ALERTS (${alerts.length})*\n`;
        alerts.forEach(a => {
          const emoji = a.type === 'critical' ? '🔴' : a.type === 'high' ? '🟠' : '🟡';
          message += `${emoji} ${a.message}\n`;
        });
        message += `\n`;
      }
      
      // All Shortages
      const shortages = supply.metrics?.shortages || [];
      if (shortages.length > 0) {
        message += `⚠️ *COMPONENT SHORTAGES (${shortages.length})*\n`;
        shortages.forEach(s => {
          message += `• ${s.component}\n`;
          message += `  Status: ${s.status} | Lead Time: ${s.leadTime}\n`;
          message += `  Impact: ${s.impact}\n`;
          message += `  Mitigation: ${s.mitigation}\n\n`;
        });
      }
      
      // All Raw Materials
      const materials = supply.metrics?.rawMaterials || [];
      if (materials.length > 0) {
        message += `📊 *RAW MATERIALS (${materials.length})*\n`;
        materials.forEach(m => {
          const trend = m.trend === 'up' ? '📈' : m.trend === 'down' ? '📉' : '➡️';
          message += `• ${m.name}: ${m.price} ${trend} ${m.change}\n`;
          message += `  Impact: ${m.impact}\n`;
        });
        message += `\n`;
      }
      
      // Manufacturing Capacity
      const manufacturing = supply.metrics?.manufacturing || [];
      if (manufacturing.length > 0) {
        message += `🏭 *MANUFACTURING CAPACITY*\n`;
        manufacturing.forEach(m => {
          const outlook = m.outlook === 'positive' ? '✅' : m.outlook === 'negative' ? '⚠️' : '➡️';
          message += `• ${m.region}: ${m.capacity} ${outlook}\n`;
          message += `  ${m.notes}\n`;
        });
        message += `\n`;
      }
      
      // Logistics
      const logistics = supply.metrics?.logistics || [];
      if (logistics.length > 0) {
        message += `🚢 *LOGISTICS CHOKEPOINTS*\n`;
        logistics.forEach(l => {
          const severity = l.severity === 'critical' ? '🔴' : l.severity === 'high' ? '🟠' : '🟡';
          message += `• ${severity} ${l.location}: ${l.disruption}\n`;
          message += `  Impact: ${l.impact}\n`;
        });
      }
      
      return message;
    } catch (error) {
      return `❌ Failed: ${error.message}`;
    }
  }

  async onSweepComplete(currentData) {
    this._cache.data = currentData;
    this._cache.timestamp = Date.now();
    if (this._previousData) {
      await this.evaluateAndAlertWithHistory(this._previousData, currentData);
    }
    this._previousData = currentData;
  }

  async evaluateAndAlertWithHistory(previousData, currentData) { return 0; }

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
        
        const isGroup = chatId === String(this.chatId);
        const isWhitelistedUser = this._allowedUsers.has(userId);
        
        if (!isGroup && !isWhitelistedUser) continue;
        
        await this._handleMessage(msg);
      }
    } catch (err) {}
  }

  async _handleMessage(msg) {
    const text = msg.text.trim();
    const parts = text.split(/\s+/);
    const rawCommand = parts[0].toLowerCase();
    const command = rawCommand.startsWith('/') ? rawCommand.split('@')[0] : null;
    if (!command) return;
    const args = parts.slice(1).join(' ');
    const replyChatId = msg.chat?.id;

    const handlers = {
      '/search': () => this._handleCompanySearch(args),
      '/brief': () => this._handleBrief(),
      '/full': () => this._handleFullReport(),
      '/osint': () => this._sendFullOSINT(),
      '/contracts': () => this._handleContracts(),
      '/arms': () => this._handleArms(),
      '/conflict': () => this._handleConflict(),
      '/watchlist': () => this._handleWatchlist(args),
      '/add': () => this._handleWatchlist(`add ${args}`),
      '/remove': () => this._handleWatchlist(`remove ${args}`),
      '/sweep': () => this.triggerManualSweep(),
      '/debug': () => this._debugData(),
      '/supply': () => this._handleSupplyChain(),
      '/supplyfull': () => this._handleSupplyFull(),
      '/status': () => `✅ *CRUCIX ONLINE*\n\nWatchlist: ${this._watchlist.get(this.chatId)?.length || 0} companies\nSupply Chain: Active`,
      '/alerts': () => this._handleAlerts(),
      '/testalert': () => this._testAlert(),
      '/help': () => Object.entries(COMMANDS).map(([c, d]) => `${c} — ${d}`).join('\n'),
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
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal: AbortSignal.timeout(10000)
    });
  }

  _buildConfiguredChatScope() {
    return { type: 'chat', chat_id: Number(this.chatId) };
  }

  async evaluateAndAlert() { return false; }
}