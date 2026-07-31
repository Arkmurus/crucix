// R-F3578 — enforceable ARIA WA channel governance.
// This module is deliberately pure: HTTP routes and the listener both consume
// the same validation rules, so UI wording can never become the security gate.

export const WA_MODES = Object.freeze({ OFFICIAL: 'official', LINKED: 'linked' });
export const LINKED_TTL_MS = 30 * 24 * 60 * 60 * 1000;

export const LINKED_SCOPES = Object.freeze([
  'forwarded_or_tagged',
  'selected_transaction_chats',
  'approved_chat_messages',
  'approved_chat_attachments',
  'approved_chat_voice_notes',
  'individually_approved_groups',
]);

export const REQUIRED_RISK_ACCEPTANCES = Object.freeze([
  'optional_mode',
  'official_channel_available',
  'unsupported_integration',
  'account_enforcement_risk',
  'continuity_not_guaranteed',
  'authorised_account',
  'no_unlawful_monitoring',
  'privacy_retention_reviewed',
]);

const SCOPE_SET = new Set(LINKED_SCOPES);
const ACCEPTANCE_SET = new Set(REQUIRED_RISK_ACCEPTANCES);

export function normaliseLinkedSelection(input = {}) {
  const scopes = [...new Set(Array.isArray(input.scopes) ? input.scopes : [])]
    .filter((scope) => SCOPE_SET.has(scope));
  const accepted = [...new Set(Array.isArray(input.accepted) ? input.accepted : [])]
    .filter((item) => ACCEPTANCE_SET.has(item));
  const approvedChats = [...new Set(Array.isArray(input.approvedChats) ? input.approvedChats : [])]
    .map((value) => String(value).trim()).filter(Boolean).slice(0, 100);
  return { scopes, accepted, approvedChats };
}

export function validateLinkedSelection(input = {}) {
  const selection = normaliseLinkedSelection(input);
  const missingAcceptances = REQUIRED_RISK_ACCEPTANCES.filter(
    (item) => !selection.accepted.includes(item),
  );
  if (missingAcceptances.length) {
    return { ok: false, code: 'risk_acceptance_incomplete', missingAcceptances, selection };
  }
  if (!selection.scopes.length) {
    return { ok: false, code: 'scope_required', missingAcceptances: [], selection };
  }
  return { ok: true, code: 'valid', missingAcceptances: [], selection };
}

export function issueLinkedGrant(input, now = Date.now()) {
  const checked = validateLinkedSelection(input);
  if (!checked.ok) return checked;
  return {
    ok: true,
    grant: {
      version: 1,
      mode: WA_MODES.LINKED,
      status: 'active',
      scopes: checked.selection.scopes,
      approvedChats: checked.selection.approvedChats,
      accepted: checked.selection.accepted,
      acceptedAt: new Date(now).toISOString(),
      expiresAt: new Date(now + LINKED_TTL_MS).toISOString(),
      pausedAt: null,
      revokedAt: null,
    },
  };
}

export function linkedGrantState(grant, now = Date.now()) {
  if (!grant || grant.mode !== WA_MODES.LINKED) return { active: false, code: 'consent_required' };
  if (grant.status === 'paused') return { active: false, code: 'connection_paused' };
  if (grant.status === 'revoked') return { active: false, code: 'consent_revoked' };
  const expiry = Date.parse(grant.expiresAt || '');
  if (!Number.isFinite(expiry) || expiry <= now) return { active: false, code: 'consent_expired' };
  const checked = validateLinkedSelection(grant);
  if (!checked.ok) return { active: false, code: checked.code };
  return { active: true, code: 'active', expiresAt: grant.expiresAt };
}

export function linkedMessageAllowed(grant, { chatId = '', isGroup = false, kind = 'message' } = {}, now = Date.now()) {
  const state = linkedGrantState(grant, now);
  if (!state.active) return state;
  const scopes = new Set(grant.scopes || []);
  if (isGroup && !scopes.has('individually_approved_groups')) return { active: false, code: 'group_scope_denied' };
  if (kind === 'attachment' && !scopes.has('approved_chat_attachments')) return { active: false, code: 'attachment_scope_denied' };
  if (kind === 'voice_note' && !scopes.has('approved_chat_voice_notes')) return { active: false, code: 'voice_scope_denied' };
  if (scopes.has('forwarded_or_tagged')) return { active: true, code: 'tagged_only' };
  const approved = new Set(grant.approvedChats || []);
  if (!approved.has(chatId)) return { active: false, code: 'chat_not_approved' };
  return { active: true, code: 'approved' };
}

export function publicGovernanceState(grant, officialNumber = '') {
  const state = linkedGrantState(grant);
  return {
    recommendedMode: WA_MODES.OFFICIAL,
    official: {
      enabled: Boolean(officialNumber),
      number: officialNumber || null,
      seesOnlySubmittedContent: true,
    },
    linked: {
      experimental: true,
      active: state.active,
      status: grant?.status || 'not_configured',
      state: state.code,
      scopes: Array.isArray(grant?.scopes) ? grant.scopes : [],
      approvedChats: Array.isArray(grant?.approvedChats) ? grant.approvedChats : [],
      acceptedAt: grant?.acceptedAt || null,
      expiresAt: grant?.expiresAt || null,
    },
  };
}

export function buildOperationalEvent({ eventId, chatId = '', timestamp, byteCount = 0, outcome = 'accepted' } = {}) {
  return {
    eventId: String(eventId || ''),
    chatType: String(chatId).endsWith('@g.us') ? 'group' : 'direct',
    timestamp: Number(timestamp) || Date.now(),
    byteCount: Math.max(0, Number(byteCount) || 0),
    outcome: String(outcome || 'accepted').slice(0, 40),
  };
}
