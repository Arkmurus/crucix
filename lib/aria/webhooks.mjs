// lib/aria/webhooks.mjs
// Outbound webhook system — push ARIA intelligence to external systems.
// Supports: generic webhook, Salesforce, HubSpot, Slack, custom endpoints.
//
// Webhook URLs are comma-separated in ARIA_WEBHOOK_URLS env var.
// Each receives a POST with JSON body: { type, data, timestamp, source }
//
// Event types:
//   NEW_LEAD          — brain or sweep generated a sales lead
//   COMPLIANCE_ALERT  — sanctions match, export control flag
//   SANCTIONS_MATCH   — entity matched a sanctions list
//   DEAL_UPDATE       — pipeline deal stage changed
//   INTEL_SIGNAL      — high-severity intelligence signal

const WEBHOOK_URLS = () => (process.env.ARIA_WEBHOOK_URLS || '').split(',').filter(Boolean);
const SLACK_WEBHOOK = () => process.env.ARIA_SLACK_WEBHOOK || '';
const WEBHOOK_TIMEOUT = 10000;

/**
 * Push a webhook event to all configured endpoints.
 * @param {{ type: string, data: object, timestamp?: string, source?: string }} event
 */
export async function pushWebhook(event) {
  const urls = WEBHOOK_URLS();
  if (!urls.length) return { sent: 0, failed: 0 };

  const payload = {
    type: event.type || 'UNKNOWN',
    data: event.data || {},
    timestamp: event.timestamp || new Date().toISOString(),
    source: event.source || 'aria',
  };

  let sent = 0;
  let failed = 0;

  const promises = urls.map(async (url) => {
    try {
      const res = await fetch(url.trim(), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: AbortSignal.timeout(WEBHOOK_TIMEOUT),
      });
      if (res.ok) {
        sent++;
      } else {
        console.warn(`[Webhooks] ${url} returned HTTP ${res.status}`);
        failed++;
      }
    } catch (e) {
      console.warn(`[Webhooks] ${url} failed: ${e.message}`);
      failed++;
    }
  });

  await Promise.allSettled(promises);
  if (sent > 0) console.log(`[Webhooks] Pushed ${event.type} to ${sent}/${urls.length} endpoints`);
  return { sent, failed };
}

/**
 * Push a formatted message to Slack via incoming webhook.
 * @param {string} message — Slack mrkdwn text
 * @param {string} [channel] — optional channel override
 */
export async function pushToSlack(message, channel) {
  const url = SLACK_WEBHOOK();
  if (!url) return false;

  const body = { text: message };
  if (channel) body.channel = channel;

  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(WEBHOOK_TIMEOUT),
    });
    if (!res.ok) {
      console.warn(`[Webhooks] Slack webhook returned HTTP ${res.status}`);
      return false;
    }
    return true;
  } catch (e) {
    console.warn(`[Webhooks] Slack push failed: ${e.message}`);
    return false;
  }
}
