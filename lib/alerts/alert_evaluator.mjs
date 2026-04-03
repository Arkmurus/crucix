/**
 * CRUCIX — Alert Evaluator
 * Evaluates incoming intel data against alert rules and fires notifications.
 */

import { ALERT_RULES } from './alert_rules.mjs';
import { createHash } from 'crypto';

export class AlertEvaluator {
  constructor(notifier) {
    this.notifier = notifier;   // TelegramAlerter or DiscordAlerter instance
    this.lastAlerts = {};       // type → timestamp of last sent alert
    this._sentHashes = new Set();
  }

  // ── Dedup ──────────────────────────────────────────────────────────────────
  _hash(alert) {
    return createHash('md5').update(`${alert.type}:${alert.title}`).digest('hex');
  }

  _isOnCooldown(type) {
    const cooldown = ALERT_RULES.cooldown?.[type] ?? 15 * 60 * 1000;
    const last = this.lastAlerts[type] ?? 0;
    return Date.now() - last < cooldown;
  }

  // ── Send ───────────────────────────────────────────────────────────────────
  async sendAlert(alert) {
    const hash = this._hash(alert);
    if (this._sentHashes.has(hash)) return false;
    if (this._isOnCooldown(alert.type)) return false;

    this._sentHashes.add(hash);
    this.lastAlerts[alert.type] = Date.now();

    const text =
      `🚨 *${alert.severity || 'ALERT'}: ${alert.title}*\n\n` +
      `${alert.message || ''}` +
      (alert.actionable ? `\n\n→ *Action:* ${alert.actionable}` : '');

    try {
      if (typeof this.notifier?.notify === 'function') {
        await this.notifier.notify(text);
      } else if (typeof this.notifier === 'function') {
        await this.notifier(text);
      }
      return true;
    } catch (e) {
      console.error('[AlertEvaluator] Send failed:', e.message);
      return false;
    }
  }

  // ── Evaluate all signals ───────────────────────────────────────────────────
  async evaluateAll(data, delta = {}) {
    let count = 0;

    // Conflict escalation
    if (ALERT_RULES.conflict?.enabled) {
      const conflicts = data?.acled?.filter?.(e =>
        (e.fatalities ?? 0) >= ALERT_RULES.conflict.thresholds.fatalities
      ) || [];
      for (const ev of conflicts.slice(0, 3)) {
        const sent = await this.sendAlert({
          type:      'conflict',
          severity:  'FLASH',
          title:     `Conflict Escalation — ${ev.country || 'Unknown'}`,
          message:   `${ev.event_type}: ${ev.fatalities} fatalities. Location: ${ev.location || '—'}`,
          actionable: 'Review procurement implications for this region.',
        });
        if (sent) count++;
      }
    }

    // Defense contracts
    if (ALERT_RULES.contracts?.enabled) {
      const contracts = data?.contracts?.filter?.(c =>
        (c.value ?? 0) >= ALERT_RULES.contracts.thresholds.minValue
      ) || [];
      for (const c of contracts.slice(0, 2)) {
        const sent = await this.sendAlert({
          type:      'contract',
          severity:  'PRIORITY',
          title:     `Defense Contract — ${c.awardee || 'Unknown'}`,
          message:   `$${((c.value ?? 0) / 1e6).toFixed(0)}M | ${c.description?.slice(0, 120) || '—'}`,
          actionable: 'Assess supply chain and competitor positioning.',
        });
        if (sent) count++;
      }
    }

    // Sanctions changes
    if (ALERT_RULES.sanctions?.enabled && delta?.newSanctions?.length > 0) {
      const sent = await this.sendAlert({
        type:      'sanctions',
        severity:  'PRIORITY',
        title:     `New Sanctions — ${delta.newSanctions.length} entities`,
        message:   delta.newSanctions.slice(0, 5).join(', '),
        actionable: 'Screen active deals against updated list immediately.',
      });
      if (sent) count++;
    }

    return count;
  }
}
