// lib/alerts/email.mjs
// Email alert channel for compliance and intelligence notifications.
// Mirrors telegram.mjs / discord.mjs alert pattern — fires emails to the compliance team.

import { sendEmail } from '../aria/emailReader.mjs';

const COMPLIANCE_EMAILS = (process.env.COMPLIANCE_TEAM_EMAILS || process.env.ADMIN_EMAIL || '').split(',').filter(Boolean).map(e => e.trim());
const INTEL_EMAILS      = (process.env.ARIA_TEAM_EMAILS || process.env.ADMIN_EMAIL || '').split(',').filter(Boolean).map(e => e.trim());

/**
 * Send a compliance alert to all configured compliance team emails.
 * @param {string} subject - Alert subject (will be prefixed with [ARIA COMPLIANCE])
 * @param {string} body    - Plain text alert body
 */
export async function sendComplianceAlert(subject, body) {
  if (!COMPLIANCE_EMAILS.length) {
    console.warn('[EmailAlert] No COMPLIANCE_TEAM_EMAILS or ADMIN_EMAIL configured — skipping email alert');
    return { sent: false, reason: 'no recipients' };
  }

  const results = [];
  for (const email of COMPLIANCE_EMAILS) {
    try {
      const r = await sendEmail({
        to: email,
        subject: `[ARIA COMPLIANCE] ${subject}`,
        text: body,
      });
      results.push({ email, ...r });
    } catch (e) {
      console.warn(`[EmailAlert] Failed to send compliance alert to ${email}:`, e.message);
      results.push({ email, sent: false, reason: e.message });
    }
  }

  const sentCount = results.filter(r => r.sent).length;
  console.log(`[EmailAlert] Compliance alert "${subject}" sent to ${sentCount}/${COMPLIANCE_EMAILS.length} recipients`);
  return { sent: sentCount > 0, results };
}

/**
 * Send an intelligence alert to all configured intel team emails.
 * @param {string} subject - Alert subject (will be prefixed with [ARIA INTEL])
 * @param {string} body    - Plain text alert body
 */
export async function sendIntelAlert(subject, body) {
  if (!INTEL_EMAILS.length) {
    console.warn('[EmailAlert] No ARIA_TEAM_EMAILS or ADMIN_EMAIL configured — skipping intel email');
    return { sent: false, reason: 'no recipients' };
  }

  const results = [];
  for (const email of INTEL_EMAILS) {
    try {
      const r = await sendEmail({
        to: email,
        subject: `[ARIA INTEL] ${subject}`,
        text: body,
      });
      results.push({ email, ...r });
    } catch (e) {
      console.warn(`[EmailAlert] Failed to send intel alert to ${email}:`, e.message);
      results.push({ email, sent: false, reason: e.message });
    }
  }

  const sentCount = results.filter(r => r.sent).length;
  console.log(`[EmailAlert] Intel alert "${subject}" sent to ${sentCount}/${INTEL_EMAILS.length} recipients`);
  return { sent: sentCount > 0, results };
}
