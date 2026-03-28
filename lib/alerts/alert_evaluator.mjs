// Telegram Alerter v2 — Complete Intelligence Bot with Enhanced Alerts

import { createHash } from 'crypto';
import { AlertEvaluator } from './alert_evaluator.mjs';  // ADD THIS

const TELEGRAM_API = 'https://api.telegram.org';
const TELEGRAM_MAX_TEXT = 4096;

const TIER_CONFIG = { ... }; // Keep existing

const COMMANDS = { ... }; // Updated with /alerts and /testalert

export class TelegramAlerter {
  constructor({ botToken, chatId, port = 3117 }) {
    // ... existing code ...
    
    // Initialize alert evaluator (ADD THIS)
    this.alertEvaluator = new AlertEvaluator(this);
  }

  // ... all existing methods ...

  // ADD THIS METHOD
  async evaluateAndAlert(llmProvider, delta, memory) {
    try {
      const response = await fetch(`http://localhost:${this.port}/api/data`);
      const data = await response.json();
      const alertCount = await this.alertEvaluator.evaluateAll(data, delta);
      if (alertCount > 0) console.log(`[Telegram] Sent ${alertCount} alerts`);
      return alertCount > 0;
    } catch (error) {
      console.error('[Telegram] Alert evaluation failed:', error.message);
      return false;
    }
  }

  // ADD THESE HANDLERS
  async _handleAlertHistory() {
    const recentAlerts = this.alertEvaluator?.lastAlerts || {};
    return `📋 *RECENT ALERTS*\n\nConflict: ${recentAlerts.conflict ? new Date(recentAlerts.conflict).toLocaleTimeString() : 'Never'}\nContracts: ${recentAlerts.contract ? new Date(recentAlerts.contract).toLocaleTimeString() : 'Never'}\nSanctions: ${recentAlerts.sanctions ? new Date(recentAlerts.sanctions).toLocaleTimeString() : 'Never'}\nOSINT: ${recentAlerts.osint ? new Date(recentAlerts.osint).toLocaleTimeString() : 'Never'}`;
  }

  async _testAlert() {
    const testAlert = {
      type: 'contract',
      severity: 'HIGH',
      title: '🧪 TEST ALERT: Weapons Contract Detected',
      message: 'This is a test alert to verify the notification system is working.',
      actionable: 'No action required. Test successful.'
    };
    await this.alertEvaluator.sendAlert(testAlert);
    return '✅ Test alert sent! Check your Telegram.';
  }

  // ... rest of existing code ...
}