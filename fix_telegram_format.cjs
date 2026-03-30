const fs = require('fs');
const f = 'lib/alerts/telegram.mjs';
let c = fs.readFileSync(f, 'utf8');

// Replace the entire evaluateAndAlert method with the new formatted version
const oldStart = '  async evaluateAndAlert(llmProvider, delta, memory, context = {}) {';
const oldEnd = '    } catch (err) {\n      console.error(\'[Telegram] evaluateAndAlert error:\', err.message);\n      return false;\n    }\n  }';

const newMethod = `  async evaluateAndAlert(llmProvider, delta, memory, context = {}) {
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

      let msg = SEP + '\\n';
      msg += '🛰 *ARKMURUS INTELLIGENCE*\\n';
      msg += SEP + '\\n';
      msg += '🕐 _' + ts + ' UTC_\\n\\n';

      // MARKET PULSE
      const vixVal = vix?.value;
      const vixIcon = vixVal > 30 ? '🔴' : vixVal > 20 ? '🟡' : '🟢';
      msg += '📊 *MARKET PULSE*\\n';
      msg += LINE + '\\n';
      if (vixVal) msg += vixIcon + ' VIX ' + vixVal + '  |  ';
      if (oil.brent) msg += 'Brent $' + oil.brent + '  |  ';
      if (oil.wti) msg += 'WTI $' + oil.wti;
      if (oil.natgas) msg += '\\nNatGas $' + oil.natgas + '/MMBtu';
      msg += '\\n\\n';

      // DIRECTION
      if (delta?.summary) {
        const dirMap = {'risk-off':'📉 RISK-OFF','risk-on':'📈 RISK-ON','mixed':'↔️ MIXED'};
        const dir = dirMap[delta.summary.direction] || '↔️ MIXED';
        msg += '🧭 ' + dir;
        if (delta.summary.criticalChanges > 0) msg += '  🔴 ' + delta.summary.criticalChanges + ' critical';
        msg += '\\n\\n';
      }

      // REGIONAL CONVERGENCES
      if (correlations.length > 0) {
        msg += '🌍 *CONVERGENCES*\\n';
        for (const c of correlations.slice(0,4)) {
          const icon = c.severity === 'critical' ? '🔴' : c.severity === 'high' ? '🟠' : '🟡';
          msg += icon + ' ' + c.region + '\\n';
        }
        msg += '\\n';
      }

      // ALL URGENT OSINT
      if (urgent.length > 0) {
        msg += SEP + '\\n';
        msg += '📡 *OSINT — ' + urgent.length + ' SIGNALS*\\n';
        msg += SEP + '\\n';
        for (let i = 0; i < Math.min(urgent.length, 12); i++) {
          const s = urgent[i];
          const ch = s.channel ? '*[' + s.channel + ']*' : '';
          const text = (s.text || '').replace(/\\n+/g,' ').trim();
          msg += '\\n' + (i+1) + '. ' + ch + '\\n' + text.substring(0, 260) + '\\n';
        }
        if (urgent.length > 12) msg += '\\n_+' + (urgent.length-12) + ' more · /osint for all_\\n';
        msg += '\\n';
      }

      // PREDICTION MARKETS
      const topMkts = polymarkets.filter(m => m.volume24h > 20000).slice(0,4);
      if (topMkts.length > 0) {
        msg += '🎯 *PREDICTION MARKETS*\\n';
        msg += LINE + '\\n';
        for (const m of topMkts) {
          const icon = m.yesProb >= 75 ? '🔴' : m.yesProb >= 50 ? '🟡' : '🟢';
          msg += icon + ' *' + m.yesProb + '%* · ' + m.question.substring(0,55) + '\\n';
        }
        msg += '\\n';
      }

      // INTELLIGENCE OPPORTUNITIES
      if (ideas.length > 0) {
        msg += '💡 *INTELLIGENCE OPPORTUNITIES*\\n';
        msg += LINE + '\\n';
        for (const idea of ideas.slice(0,3)) {
          const icon = idea.type === 'long' ? '📈' : idea.type === 'hedge' ? '🛡' : '👁';
          msg += icon + ' *' + idea.title + '*\\n';
          if (idea.rationale) msg += '_' + idea.rationale.substring(0,110) + '_\\n';
        }
        msg += '\\n';
      }

      // NEWS FEED
      if (newsFeed.length > 0) {
        msg += '📰 *FEED*\\n';
        for (const n of newsFeed.slice(0,4)) {
          msg += '· ' + n.headline + ' _(' + n.source + ')_\\n';
        }
        msg += '\\n';
      }

      msg += SEP + '\\n';
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
  }`;

const startIdx = c.indexOf(oldStart);
const endIdx = c.indexOf(oldEnd, startIdx);

if (startIdx > -1 && endIdx > -1) {
  c = c.substring(0, startIdx) + newMethod + c.substring(endIdx + oldEnd.length);
  fs.writeFileSync(f, c);
  console.log('Telegram format updated successfully');
} else {
  console.log('Method boundaries not found');
  console.log('startIdx:', startIdx, 'endIdx:', endIdx);
}
