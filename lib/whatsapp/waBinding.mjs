// R-F3587 — phone ↔ account binding. Only a verified imaria.io user may engage
// ARIA on WhatsApp.
//
// This module is deliberately PURE — no I/O, no clock of its own, no storage.
// aria-web (which owns user accounts) and aria-wa (which sees the messages) both
// import this exact file, so the two tiers cannot drift into disagreeing about
// who is verified. Same reason lib/whatsapp/waGovernance.mjs is shared: a rule
// that lives in two places is two rules.
//
// ── WHY A PAIRING CODE, AND NOT AN ALLOW-LIST ────────────────────────────────
//
// An operator-maintained list of phone numbers proves nothing about who is
// holding the phone, and it does not scale past the operator. A pairing code
// proves BOTH directions at once:
//
//   1. the person is signed in to imaria.io  (only an authenticated session can
//      mint a code), and
//   2. the person controls the handset       (only that handset can send the
//      code to ARIA).
//
// ── AND IT SOLVES THE LID PROBLEM BY MEASUREMENT ─────────────────────────────
//
// R-F3582: Baileys 7 addresses the same person as `<phone>@s.whatsapp.net` OR
// `<lid>@lid`, and the alt-field names are not something we could verify from
// here. A pairing message sidesteps the whole question — it IS the identity
// evidence. Whatever identifiers WhatsApp attaches to the message that carries
// the code are exactly the identifiers that person will arrive with later, so we
// record all of them and never have to guess the field names.

export const PAIRING_TTL_MS = 10 * 60 * 1000;      // 10 minutes
export const PAIRING_CODE_LEN = 6;

/** A pairing code as it appears in a WhatsApp message. Accepts an optional
 *  "ARIA" prefix and stray punctuation so a user who types naturally still
 *  pairs, but the code itself must be exactly PAIRING_CODE_LEN digits — a looser
 *  pattern would let any 6-digit substring of ordinary chat start a pairing. */
const _CODE_RE = new RegExp(`(?:^|\\b)(?:aria[\\s,:-]*)?(?:link|pair|verify)?[\\s,:-]*(\\d{${PAIRING_CODE_LEN}})(?:\\b|$)`, 'i');

export function extractPairingCode(text) {
  const m = _CODE_RE.exec(String(text || '').trim());
  return m ? m[1] : null;
}

/**
 * Every identifier a message carries for its sender.
 *
 * R-F3586 established this shape; it lives here now so the listener's runtime
 * check and the binding written at pairing time use ONE definition. If they
 * diverged, a user could pair under one identifier and be refused under another
 * — silently, which is the failure mode this whole area keeps producing.
 *
 * The bare user part of each jid is included as well as the full jid, so a
 * binding survives a domain change (…@s.whatsapp.net -> …@lid) for the same id.
 */
export function identitiesFromMessage(senderJid, msg = null) {
  const out = new Set();
  const add = (v) => {
    const raw = String(v || '').trim();
    if (!raw) return;
    out.add(raw);
    const at = raw.lastIndexOf('@');
    const user = (at === -1 ? raw : raw.slice(0, at)).split(':')[0];
    if (user) out.add(user);
  };
  add(senderJid);
  const k = (msg && msg.key) || {};
  for (const f of ['participant', 'participantAlt', 'participantPn', 'senderPn',
                   'remoteJid', 'remoteJidAlt']) {
    if (k[f]) add(k[f]);
  }
  if (msg && msg.participant) add(msg.participant);
  out.delete('');
  return [...out];
}

/** Build the pending-pairing record aria-web hands to the listener. */
export function newPairing({ userId, code, now = Date.now() }) {
  if (!userId) return { ok: false, code: 'user_required' };
  if (!/^\d+$/.test(String(code || '')) || String(code).length !== PAIRING_CODE_LEN) {
    return { ok: false, code: 'bad_code_format' };
  }
  return {
    ok: true,
    pairing: {
      version: 1,
      userId: String(userId),
      code: String(code),
      issuedAt: new Date(now).toISOString(),
      expiresAt: new Date(now + PAIRING_TTL_MS).toISOString(),
      usedAt: null,
    },
  };
}

/**
 * Is this pending pairing usable right now?
 *
 * SINGLE USE and TIME BOUND, both enforced here rather than at the call site, so
 * a second caller cannot reuse a code by taking a different path to it.
 */
export function pairingState(pairing, now = Date.now()) {
  if (!pairing) return { valid: false, code: 'no_pairing' };
  if (pairing.usedAt) return { valid: false, code: 'already_used' };
  const exp = Date.parse(pairing.expiresAt || '');
  if (!Number.isFinite(exp) || exp <= now) return { valid: false, code: 'expired' };
  return { valid: true, code: 'valid' };
}

/**
 * Turn a verified pairing into a binding.
 *
 * `identities` is everything the pairing MESSAGE carried — see
 * identitiesFromMessage. Storing all of them is what makes the binding immune to
 * WhatsApp changing which identifier it puts on a message.
 */
export function newBinding({ userId, identities, now = Date.now() }) {
  const ids = [...new Set((identities || []).map((s) => String(s || '').trim()).filter(Boolean))];
  if (!userId) return { ok: false, code: 'user_required' };
  if (!ids.length) return { ok: false, code: 'no_identity' };
  return {
    ok: true,
    binding: {
      version: 1,
      userId: String(userId),
      identities: ids,
      boundAt: new Date(now).toISOString(),
      revokedAt: null,
    },
  };
}

/**
 * Which bound user, if any, this sender is — matching on ANY recorded identifier.
 *
 * Returns null for an unknown or revoked sender. Callers MUST treat null as
 * "refuse", never as "unknown, allow anyway": the entire point is that engaging
 * ARIA costs LLM budget and, via /teach and /correct, writes into a memory that
 * never evicts.
 */
export function resolveBoundUser(bindings, identities) {
  if (!Array.isArray(bindings) || !identities || !identities.length) return null;
  const want = new Set(identities.map((s) => String(s || '').trim()).filter(Boolean));
  for (const b of bindings) {
    if (!b || b.revokedAt) continue;
    for (const id of b.identities || []) {
      if (want.has(String(id))) return { userId: b.userId, binding: b };
    }
  }
  return null;
}

/** Redact a binding for display: never echo raw identifiers back to a surface. */
export function publicBindingView(binding) {
  if (!binding) return { bound: false };
  return {
    bound: !binding.revokedAt,
    userId: binding.userId,
    identityCount: (binding.identities || []).length,
    boundAt: binding.boundAt || null,
    revokedAt: binding.revokedAt || null,
  };
}
