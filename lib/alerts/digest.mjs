// lib/alerts/digest.mjs
// Daily 07:00 UTC structured morning briefing
// Sends regardless of delta threshold — reliable daily read

import { analyzeTrends, formatTrendsForTelegram } from '../intel/archive.mjs';

export async function sendMorningDigest(telegramAlerter, currentData) {
  if (!telegramAlerter?.isConfigured) return;
  if (!currentData) return;

  try {
    const now     = new Date();
    const dateStr = now.toLocaleDateString('en-GB', {
      weekday: 'long', day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC'
    });

    const urgent    = currentData.tg?.urgent    || [];
    const newsFeed  = currentData.newsFeed      || [];
    const ideas     = currentData.ideas         || [];
    const contracts = currentData.defense?.updates || [];
    const vix       = currentData.fred?.find(f => f.id === 'VIXCLS');
    const hy        = currentData.fred?.find(f => f.id === 'BAMLH0A0HYM2');
    const oil       = currentData.energy        || {};
    const delta     = currentData.delta?.summary;
    const trends    = analyzeTrends();

    let msg = `*CRUCIX MORNING BRIEFING*\n`;
    msg += `_${dateStr} · 07:00 UTC_\n\n`;

    // ── Direction ──────────────────────────────────────────────────────────
    if (delta) {
      const dir = { 'risk-off': '📉 RISK-OFF', 'risk-on': '📈 RISK-ON', 'mixed': '↔️ MIXED' }[delta.direction] || '↔️ MIXED';
      msg += `*${dir}*`;
      if (delta.criticalChanges > 0) msg += ` | ⚠️ ${delta.criticalChanges} critical`;
      msg += `\n\n`;
    }

    // ── Markets snapshot ───────────────────────────────────────────────────
    msg += `*Markets*\n`;
    msg += `VIX: ${vix?.value || '--'}`;
    if (hy) msg += ` | HY Spread: ${hy.value}`;
    msg += `\nWTI: $${oil.wti || '--'} | Brent: $${oil.brent || '--'}`;
    if (oil.natgas) msg += ` | NatGas: $${oil.natgas}`;
    msg += `\n\n`;

    // ── 7-day trend summary ────────────────────────────────────────────────
    if (trends) {
      const sig = trends.signals;
      const sigArrow = sig.trend.direction === 'rising' ? '📈' : sig.trend.direction === 'falling' ? '📉' : '➡️';
      msg += `*7-day signal trend:* ${sigArrow} ${sig.trend.direction} ${sig.trend.pct}%\n\n`;
    }

    // ── Top OSINT signals ─────────────────────────────────────────────────
    if (urgent.length > 0) {
      msg += `*Top OSINT (${urgent.length} active signals)*\n`;
      for (const s of urgent.slice(0, 4)) {
        const ch = s.channel ? `[${s.channel}] ` : '';
        msg += `• ${ch}${(s.text || '').substring(0, 160)}\n\n`;
      }
    }

    // ── Top news ───────────────────────────────────────────────────────────
    if (newsFeed.length > 0) {
      msg += `*Top Headlines*\n`;
      for (const n of newsFeed.slice(0, 4)) {
        msg += `• ${n.headline} _(${n.source})_\n`;
      }
      msg += `\n`;
    }

    // ── LLM trade ideas ────────────────────────────────────────────────────
    if (ideas.length > 0) {
      msg += `*Intelligence Insights*\n`;
      for (const idea of ideas.slice(0, 3)) {
        const icon = idea.type === 'long' ? '📈' : idea.type === 'hedge' ? '🛡️' : '👁️';
        msg += `${icon} ${idea.title}\n`;
        if (idea.rationale) msg += `   _${idea.rationale.substring(0, 100)}_\n`;
      }
      msg += `\n`;
    }

    // ── Defense contracts ──────────────────────────────────────────────────
    if (contracts.length > 0) {
      msg += `*Defense Contracts (${contracts.length})*\n`;
      for (const c of contracts.slice(0, 3)) {
        msg += `• ${c.title}\n`;
      }
      msg += `\n`;
    }

    msg += `_/brief · /osint · /full · /trends · /sweep_`;

    await telegramAlerter.sendMessage(msg);
    console.log('[Digest] Morning briefing sent');
  } catch (err) {
    console.error('[Digest] Error:', err.message);
  }
}
