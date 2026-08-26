// ARIA WhatsApp capability policy — WHO may ask for WHAT, and WHAT is being asked.
//
// Deliberately PURE, for the same reason waBinding.mjs and waGovernance.mjs are:
// aria-wa (which sees the messages) and aria-web (which owns accounts) must not
// drift into disagreeing. A rule that lives in two places is two rules.
//
// ── WHAT THIS ADDS, AND WHAT ALREADY EXISTED ─────────────────────────────────
//
// Already enforced, and NOT changed here: _waSenderAllowed() in the listener
// decides whether a sender may engage ARIA at all — a bound imaria.io account
// (R-F3587) or the bootstrap allow-list. That gate is binary and it is correct.
//
// What it cannot express is a TIER. Every registered user is currently equal, so
// there is no way to say "system internals are for admins" — which is the whole
// requirement here. This module adds the tier and the intent, and nothing else.
//
// ── TWO SEPARATE QUESTIONS, KEPT SEPARATE ────────────────────────────────────
//
// The legacy Twilio handler (lib/whatsapp/ariaWhatsApp.mjs) collapsed them:
//
//     if (COMMAND_RE.test(t))            return 'command';
//     if (MENTIONS.some(p => p.test(t))) return 'mention';
//     if (isDirectRequest(t))            return 'request';   // never reached
//
// Because it returns on the first match, "aria, run a DD on Acme" and "aria,
// good morning" both return 'mention'. The imperative signal is computed one
// line later and thrown away, so nothing downstream can tell a task from a
// greeting. That is not a naming problem: ADDRESSING (how she was reached) and
// INTENT (what is being asked) are orthogonal, and one field cannot hold both.
//
// Here they stay separate: classifyRequest() reports both, and intent is decided
// on the message with any address prefix REMOVED, so "aria," cannot mask it.

export const ROLE_ADMIN = 'admin';
export const ROLE_USER = 'user';

export const INTENT_COMMAND = 'command';
export const INTENT_TASK = 'task';
export const INTENT_CONVERSATION = 'conversation';

export const ADDRESSED_COMMAND = 'command';
export const ADDRESSED_MENTION = 'mention';
export const ADDRESSED_DIRECT = 'direct';
export const ADDRESSED_AMBIENT = 'ambient';

/** Capabilities that expose ARIA's own operational state or write to her
 *  permanent record. Named, not inferred: a capability that must be guessed at
 *  is a capability that will be guessed wrong. */
export const CAP_SYSTEM_INTERNALS = 'system_internals';
export const CAP_MEMORY_WRITE = 'memory_write';
export const CAP_ORDINARY = 'ordinary';

const _COMMAND_RE = /^\s*\/([a-z0-9_]+)/i;

/** Strip a leading address to ARIA so intent is judged on the REQUEST, not on
 *  the greeting that carried it. "aria, run a DD" must read as a task. */
const _ADDRESS_PREFIX_RE = /^\s*@?aria\b[\s,:;.!?-]*/i;
const _MENTION_RE = /(?:^|\s)@?aria\b/i;

// An imperative asking ARIA to DO something.
//
// VERB-LED BY CONSTRUCTION, not by wishful comment. Matching the bare word
// "research" anywhere reads "what is your research process?" as an instruction
// to research something — the question becomes the task it merely names. So a
// verb only counts when it stands where an imperative stands: opening a clause,
// or following an explicit request marker ("please …", "can you …").
const _TASK_VERB =
  '(?:investigate|research|look\\s+into|dig\\s+into|crawl|scrape|harvest|spider'
  + '|screen|profile|classify|summari[sz]e|read|fetch|ingest|analyse|analyze'
  + '|run|build|generate|produce|prepare|check|find|compile)';

const _REQUEST_MARKER =
  '(?:please\\s+|(?:can|could|would|will)\\s+you\\s+(?:please\\s+)?(?:go\\s+and\\s+)?'
  + '|i\\s+(?:need|want)\\s+you\\s+to\\s+|help\\s+me\\s+|go\\s+and\\s+)';

const _TASK_PATTERNS = [
  // Verb opening the message or a clause: "run a DD on Acme", "…; screen them".
  new RegExp(`(?:^|[.;!?]\\s*)${_TASK_VERB}\\b`, 'i'),
  // Verb after an explicit request marker: "please screen Acme".
  new RegExp(`${_REQUEST_MARKER}${_TASK_VERB}\\b`, 'i'),
  // Noun phrases that ARE requests whatever their position — you cannot say
  // "due diligence on X" or "background check on X" conversationally about ARIA.
  /\b(?:due\s+diligence|background\s+check|sanctions?\s+check|compliance\s+check|risk\s+assess(?:ment)?|deep[-\s]?dive)\s+(?:on|for|of|into)\b/i,
  /\b(?:run\s+a\s+)?\bdd\b\s+on\b/i,
  /\bfind\s+out\s+about\b/i,
];

// Asking ABOUT her state rather than asking her to act on the world.
const _SYSTEM_QUERY_PATTERNS = [
  /\b(status|health|diagnostics?|uptime|are\s+you\s+(?:ok|up|healthy|degraded))\b/i,
  /\b(which|what)\s+(?:llm|model|provider|engine)\b/i,
  /\b(cost|spend|budget|token\s+usage|cooldown|fallback\s+chain)\b/i,
  /\b(what\s+(?:are\s+you|were\s+you)\s+doing|what.?s\s+running|current\s+tasks?)\b/i,
];

const _URL_RE = /https?:\/\/\S+/i;

/**
 * How ARIA was reached, what is being asked, and whether it touches her internals.
 *
 * Returns { addressed, intent, command, systemQuery, capability, body }.
 * `addressed` and `intent` are INDEPENDENT — see the header. `body` is the text
 * with any address prefix removed, which is what should be sent onward.
 */
export function classifyRequest(text, { mentioned = false } = {}) {
  const raw = String(text || '');
  const head = raw.slice(0, 2000);

  const cmd = _COMMAND_RE.exec(head);
  const body = head.replace(_ADDRESS_PREFIX_RE, '').trim();
  const wasMentioned = mentioned || _MENTION_RE.test(head);

  const addressed = cmd
    ? ADDRESSED_COMMAND
    : wasMentioned
      ? ADDRESSED_MENTION
      : (_TASK_PATTERNS.some((p) => p.test(body)) || _URL_RE.test(body))
        ? ADDRESSED_DIRECT
        : ADDRESSED_AMBIENT;

  const systemQuery = !cmd && _SYSTEM_QUERY_PATTERNS.some((p) => p.test(body));

  // Intent is judged on `body`, so an address prefix cannot mask a task.
  let intent;
  if (cmd) intent = INTENT_COMMAND;
  else if (systemQuery) intent = INTENT_CONVERSATION;   // asking about her, not asking her to act
  else if (_TASK_PATTERNS.some((p) => p.test(body)) || _URL_RE.test(body)) intent = INTENT_TASK;
  else intent = INTENT_CONVERSATION;

  return {
    addressed,
    intent,
    command: cmd ? cmd[1].toLowerCase() : null,
    systemQuery,
    capability: systemQuery ? CAP_SYSTEM_INTERNALS : CAP_ORDINARY,
    body: body || head.trim(),
  };
}

/**
 * The role of a bound sender.
 *
 * Keyed on the bound ACCOUNT id, never on a phone number. waBinding.mjs states
 * the reason and it applies with more force to privilege than to access: "an
 * operator-maintained list of phone numbers proves nothing about who is holding
 * the phone". A handset can be lent, spoofed, or re-issued by a carrier; the
 * imaria.io account behind a pairing code cannot.
 *
 * Fails CLOSED: no binding, or an unrecognised account, is ROLE_USER. There is
 * no path where an unidentified sender becomes an admin by default.
 */
export function roleForBinding(binding, adminUserIds = []) {
  const uid = String(binding?.userId || '').trim();
  if (!uid) return ROLE_USER;
  const admins = new Set(
    (Array.isArray(adminUserIds) ? adminUserIds : String(adminUserIds || '').split(','))
      .map((v) => String(v || '').trim())
      .filter(Boolean),
  );
  return admins.has(uid) ? ROLE_ADMIN : ROLE_USER;
}

/**
 * May this role see real, live system internals?
 *
 * The operator requirement is that REAL system data reaches admins and
 * superusers only. Note what this does NOT license: an ordinary user must not
 * receive a fabricated or vague substitute either. Refusing is honest; inventing
 * a plausible status is the §1 failure this codebase keeps legislating against.
 * Callers should decline the capability, not soften the answer.
 */
export function maySeeSystemInternals(role) {
  return role === ROLE_ADMIN;
}

/** R-F4361 (C-307) — may `role` write ARIA's PERMANENT memory?
 *
 *  `/teach` and `/correct` write facts that CLAUDE.md §7 forbids ever evicting,
 *  so a wrong or malicious one is permanent. Until this existed, the only thing
 *  standing between a stranger and that write was `WA_ALLOWED_SENDERS` — the
 *  bootstrap allow-list, whose own docstring says it "exists only to bootstrap
 *  the first operator". Opening it for testers therefore also opened permanent
 *  memory, which is why the list could not simply be removed.
 *
 *  `CAP_MEMORY_WRITE` and `capabilityForCommand` already classified these two
 *  commands correctly. NOTHING CONSULTED THE CLASSIFICATION — a repo-wide search
 *  found no production caller, only a test asserting the mapping against itself.
 *  A policy with no consumer did not happen; this is its consumer.
 *
 *  ADMIN here means a BOUND imaria.io account named in ARIA_WA_ADMIN_USER_IDS —
 *  never a phone number, per `roleForBinding`'s reasoning that a handset "can be
 *  lent, spoofed, or re-issued by a carrier while the account behind a pairing
 *  code cannot".
 *
 *  Fails CLOSED on anything unrecognised: an unknown role must never buy the
 *  strongest capability in the system.
 */
export function mayWriteMemory(role) {
  return role === ROLE_ADMIN;
}

/** Capability a given slash command exercises. Unknown commands are ORDINARY —
 *  this module does not silently widen the gate for something it has not been
 *  taught about; that is the caller's existing allow-list to decide. */
export function capabilityForCommand(command) {
  const c = String(command || '').toLowerCase();
  if (c === 'teach' || c === 'correct') return CAP_MEMORY_WRITE;
  if (c === 'status' || c === 'diag' || c === 'health') return CAP_SYSTEM_INTERNALS;
  return CAP_ORDINARY;
}
