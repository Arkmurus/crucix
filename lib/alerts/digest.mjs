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
    const contracts        = currentData.defense?.updates || [];
    const defNewsSignals   = currentData.defenseNews?.signals || [];
    const procLusiSignals  = currentData.procurementTenders?.lusophone || [];
    const vix              = currentData.fred?.find(f => f.id === 'VIXCLS');
    const hy        = currentData.fred?.find(f => f.id === 'BAMLH0A0HYM2');
    const oil       = currentData.energy        || {};
    const delta     = currentData.delta?.summary;
    const trends    = analyzeTrends();
    const eventsData = currentData.defenseEvents;
    const nearEvents = (eventsData?.upcoming || []).filter(e => e.daysUntil >= 0 && e.daysUntil <= 90);

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

    // ── Lusophone & Africa defence signals ────────────────────────────────
    const lusiSigs = defNewsSignals.filter(s =>
      ['angola','mozambique','guinea-bissau','cape verde','são tomé','sao tome','lusophone']
        .some(kw => (s.text || '').toLowerCase().includes(kw))
    );
    if (lusiSigs.length > 0 || procLusiSignals.length > 0) {
      msg += `*Lusophone & Africa Defence*\n`;
      for (const s of lusiSigs.slice(0, 3)) {
        msg += `🇵🇹 ${(s.text || '').substring(0, 140)}\n`;
      }
      for (const t of procLusiSignals.slice(0, 2)) {
        msg += `📋 [Tender] ${(t.title || t.text || '').substring(0, 130)}\n`;
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

    // ── BD Intelligence ────────────────────────────────────────────────────────
    const bd = currentData.bdIntelligence;
    if (bd) {
      const bdBrain     = bd.brain?.weeklyPriority;
      const bdTenders   = (bd.tenders || []).filter(t => t.type === 'TENDER').slice(0, 3);
      const bdContracts = (bd.tenders || []).filter(t => t.type === 'CONTRACT').slice(0, 2);
      const bdIdeas     = (bd.ideas || []).slice(0, 2);
      if (bdBrain || bdTenders.length > 0 || bdIdeas.length > 0) {
        msg += `*BD & Strategy Intelligence*\n`;
        if (bdBrain?.action) {
          msg += `⚡ *Priority:* ${bdBrain.action.substring(0, 150)}\n`;
          if (bdBrain.whyNow) msg += `   _Why now: ${bdBrain.whyNow.substring(0, 100)}_\n`;
        }
        for (const t of bdTenders) {
          const prob = t.winProbability != null ? ` _(${t.winProbability}% win)_` : '';
          msg += `🎯 *[TENDER]* ${t.market}: ${t.title.substring(0, 100)}${prob}\n`;
          if (t.url) msg += `   ${t.url.substring(0, 80)}\n`;
        }
        for (const t of bdContracts) {
          msg += `✅ *[AWARD]* ${t.market}: ${t.title.substring(0, 100)}\n`;
        }
        for (const i of bdIdeas) {
          msg += `💡 *[IDEA]* ${i.market}: ${(i.actionStep || i.rationale || '').substring(0, 100)}\n`;
        }
        if (bd.counts?.pipelineDeals > 0) msg += `_Pipeline: ${bd.counts.pipelineDeals} deals tracked · /bd for full BD brief_\n`;
        msg += `\n`;
      }
    }

    // ── Upcoming defence events ────────────────────────────────────────────
    if (nearEvents.length > 0) {
      msg += `*Upcoming Defence Events*\n`;
      for (const e of nearEvents.slice(0, 4)) {
        const dot = e.daysUntil <= 14 ? '🔴' : e.daysUntil <= 30 ? '🟡' : '🟢';
        const star = e.priority === 'high' ? ' ⭐' : '';
        msg += `${dot}${star} *${e.name}* — ${e.daysUntil}d · ${e.location}\n`;
      }
      msg += `\n`;
    }

    // ── Source Integrity ───────────────────────────────────────────────────
    const srcOk    = currentData.meta?.sourcesOk      || 0;
    const srcTotal = currentData.meta?.sourcesQueried  || 0;
    const srcFail  = currentData.meta?.sourcesFailed   || 0;
    const CRITICAL_SRCS = new Set(['Defense News', 'ProcurementTenders', 'Lusophone', 'ACLED', 'OFAC', 'OpenSanctions', 'DefenseEvents', 'SIPRI Arms', 'ExportControlIntel']);
    const failedCritical = (currentData.health || []).filter(h => h.err && CRITICAL_SRCS.has(h.n)).map(h => h.n);
    const failedOtherCount = Math.max(0, srcFail - failedCritical.length);
    let srcLine = `_Sources: ${srcOk}/${srcTotal} OK`;
    if (failedCritical.length > 0) {
      srcLine += ` · ⚠️ degraded: *${failedCritical.join(', ')}*`;
      if (failedOtherCount > 0) srcLine += ` + ${failedOtherCount} others`;
    } else if (srcFail > 0) {
      srcLine += ` · ${srcFail} non-critical degraded`;
    }
    srcLine += `_`;
    msg += srcLine + `\n`;

    msg += `_/brief · /osint · /full · /trends · /sweep · /events_`;

    await telegramAlerter.sendMessage(msg);
    console.log('[Digest] Morning briefing sent');
  } catch (err) {
    console.error('[Digest] Error:', err.message);
  }
}
