/**
 * ARIA — Rate Limiting & Input Validation Middleware
 * GAP 2 FIX: Protects all 70+ routes from abuse.
 *
 * Mount in server.mjs BEFORE route registration:
 *   import { applyRateLimiting, applyInputValidation } from './middleware/rateLimiter.mjs';
 *   applyRateLimiting(app);
 *   applyInputValidation(app);
 */

import rateLimit, { ipKeyGenerator } from 'express-rate-limit';
import slowDown  from 'express-slow-down';
import helmet    from 'helmet';
import { body, query, param, validationResult } from 'express-validator';
import { verifyToken } from '../lib/auth/users.mjs';   // R-F3072 — identity-keyed buckets
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { computeInlineScriptHashes } from '../lib/http/cspHashes.mjs';   // R-F3840

// R-F35 (2026-05-03): IPv6-safe IP fallback. express-rate-limit v8+ emits
// ERR_ERL_KEY_GEN_IPV6 if you mix req.ip into a custom keyGenerator
// without normalising — IPv6 addresses can otherwise bypass the limiter
// because the same prefix maps to many distinct addresses. ipKeyGenerator
// applies a /64 prefix mask to v6 and leaves v4 alone. Live evidence
// 2026-05-03 11:09:55: 3× ValidationError stack traces at boot from
// rateLimiter.mjs:112/113/119 (ariaThin/ariaChat/admin tiers).
//
// R-F3076 (2026-07-25) — THIS WAS SILENTLY COUNTING NOTHING. express-rate-limit
// v8 changed the helper's signature to `ipKeyGenerator(ip: string, ipv6Subnet?)`
// (v6/v7, which R-F35 was written against, took `(req, res)`). Passing `req` as
// `ip` means `isIPv6(req)` is false and the helper returns **the Request object
// itself** as the bucket key. Every request is a distinct object, so every
// request minted a fresh bucket and the counter never incremented past 1.
//
// Blast radius: every tier that reached this fallback — and since the limiters
// are mounted BEFORE any auth middleware (server.mjs:1462, see R-F3072 below),
// `req.user` was always undefined, so ariaThin (think, 5/min), ariaChat (chat,
// 20/min) and admin (30/min) reached it on EVERY request. ARIA's per-user LLM
// rate limits have therefore been enforcing nothing; the $300/mo cap (§17) and
// the brain-side per-user quota were the only live brakes. Verified 2026-07-25:
// 175 consecutive anonymous requests through a keyGenerator using this helper
// returned `RateLimit-Remaining: 149` — i.e. one hit, not 175.
const _ipFallback = (req, res) => ipKeyGenerator(req.ip);

// ── R-F3072: identity-keyed buckets ──────────────────────────────────────────
// The limiters ran BEFORE any auth middleware, so `req.user` was ALWAYS
// undefined here — applyRateLimiting(app) is mounted at server.mjs:1461 and
// every requireAuth is registered later, per route. So the tiers that read
// `req.user?.userId` (ariaThin / ariaChat / admin, R-F2383) silently degraded
// to per-IP, and the standard tier had no key at all. Decode the bearer
// ourselves — one HMAC, no store hit — so a signed-in human gets their OWN
// bucket instead of sharing one with everyone behind the same NAT.
//
// This also fixes what the per-IP bucket did to the app's OWN traffic: the
// standard tier is an ANTI-ABUSE control, but the dominant caller on /api/ is
// first-party polling. Measured steady state (2026-07-25): the shared sidebar
// alerts badge is 1 req/60s = 15 per 15-min window on EVERY app page; the
// dashboard auto-refresh is 6 requests/90s = 60 per window; vault.html runs two
// 60s pollers = 30; wa-connections refreshes every 15s = 60. One dashboard tab
// left open idles at ~75 of the 150 budget, so a second tab (dashboard + chat is
// the normal working posture) exhausted it — the user hit the express-slow-down
// ramp at 80 (+200ms per request, up to 5s) and then a hard 15-minute
// "Too many requests" across the WHOLE app, chat included, without doing
// anything unusual. Sizing the authenticated bucket from that measured profile
// leaves ~3x headroom for a power user with several tabs; the expensive and
// abusable routes are NOT covered by this number — they keep their own much
// tighter per-route tiers below (chat 20/min, think 5/min, compliance 10/min,
// export 3/min, admin 30/min, auth 10 per 15min), which are what actually
// bound cost and brute force. Anonymous traffic keeps the old 150/15min.
function _bearerUserId(req) {
  if (req._rlUserId !== undefined) return req._rlUserId;
  let uid = null;
  const m = /^Bearer\s+(.+)$/i.exec(req.headers?.authorization || '');
  if (m) {
    try {
      const p = verifyToken(m[1].trim());
      if (p && p.userId) uid = String(p.userId);
    } catch { /* expired / forged / internal-token → anonymous bucket */ }
  }
  req._rlUserId = uid;
  return uid;
}
const _identityKey = (req, res) => {
  const uid = _bearerUserId(req);
  return uid ? `u:${uid}` : _ipFallback(req, res);
};
const _authedMax = (authed, anon) => (req) => (_bearerUserId(req) ? authed : anon);

// R-F390 (2026-05-12): bypass user-facing rate limiters for requests that
// carry the internal-service bearer. The WA listener calls /api/aria/chat
// from inside the same Node process (waListener.mjs:2568) — Express sees
// 127.0.0.1, which shared the IP bucket with every sweep-side cross-call
// from the same machine. Live evidence 2026-05-12 22:11:15/22:11:48: two
// operator queries (Lukoil DD + status) got 429'd on both streaming and
// /chat fallback while sweeps were active. Bypass keyed on Authorization:
// Bearer == process.env.ARIA_INTERNAL_TOKEN; empty env = no bypass so a
// misconfigured deploy can't accidentally pass requests through.
function _internalTokenBypass(req) {
  const expected = (process.env.ARIA_INTERNAL_TOKEN || '').trim();
  if (!expected) return false;
  const auth = req.headers?.authorization || '';
  const m = auth.match(/^Bearer\s+(.+)$/i);
  if (!m) return false;
  return m[1].trim() === expected;
}

// ── Redis store for distributed rate limiting (uses your existing Upstash) ────
// If you want Redis-backed counters (survives restarts), install:
//   npm i rate-limit-redis @upstash/redis
// Otherwise the in-memory store below works for single-instance Render deploys.

// ── Tier Definitions ──────────────────────────────────────────────────────────

const TIERS = {
  // Standard API — generous but not unlimited
  standard: {
    windowMs:  15 * 60 * 1000,  // 15 min
    // R-F3072 — sized from the app's own measured polling profile (see
    // _bearerUserId above). Anonymous callers keep the original 150.
    max:       _authedMax(600, 150),
    keyGenerator: _identityKey,
    message:   { error: 'Too many requests. Please wait 15 minutes.' },
    standardHeaders: true,
    legacyHeaders:   false,
    skip:      _internalTokenBypass,   // R-F390: WA listener / sweep cross-calls
  },

  // Auth endpoints — strict to prevent brute force
  // R-F3180 — the vetting portal is UNAUTHENTICATED and token-guessable in
  // principle, so it gets its own tier rather than sitting on the anonymous
  // 150/15min. Tokens are 32 random bytes (guessing is infeasible), but a
  // tight cap turns any attempt into an obvious signal instead of background
  // noise. Deliberately NO custom keyGenerator: under express-rate-limit v8 a
  // hand-rolled one returned the Request object as the key and let every
  // request through (R-F3070). The default IP key is correct here.
  vettingPortal: {
    windowMs:  15 * 60 * 1000,
    max:       30,
    message:   { error: 'Too many attempts. Please wait and try again.' },
  },

  auth: {
    windowMs:  15 * 60 * 1000,
    max:       10,
    message:   { error: 'Too many auth attempts. Please wait 15 minutes.' },
    skipSuccessfulRequests: true,
  },

  // Sweep trigger — 1 per minute max (expensive operation)
  sweep: {
    windowMs:  60 * 1000,
    max:       1,
    message:   { error: 'Sweep already running. Try again in 60 seconds.' },
  },

  // ARIA think — expensive LLM call, 5/min per user
  ariaThin: {
    windowMs:  60 * 1000,
    max:       5,
    message:   { error: 'ARIA think rate limit reached. Max 5 requests/minute.' },
    keyGenerator: _identityKey,   // R-F3072: req.user is not populated at limiter time (mounted pre-auth) — decode the bearer instead
    skip:      _internalTokenBypass,   // R-F390
  },

  // ARIA chat — more lenient, still bounded
  ariaChat: {
    windowMs:  60 * 1000,
    max:       20,
    message:   { error: 'ARIA chat rate limit reached. Max 20 messages/minute.' },
    keyGenerator: _identityKey,   // R-F3072: req.user is not populated at limiter time (mounted pre-auth) — decode the bearer instead
    skip:      _internalTokenBypass,   // R-F390
  },

  // Compliance screening — moderate cost
  compliance: {
    windowMs:  60 * 1000,
    max:       10,
    message:   { error: 'Compliance screening limit reached. Max 10/minute.' },
  },

  // Export/PDF — server-intensive
  export: {
    windowMs:  60 * 1000,
    max:       3,
    message:   { error: 'Export limit reached. Max 3 exports/minute.' },
  },

  // Admin — trusted but still rate-limited
  admin: {
    windowMs:  60 * 1000,
    max:       30,
    message:   { error: 'Admin rate limit reached.' },
    keyGenerator: _identityKey,   // R-F3072: req.user is not populated at limiter time (mounted pre-auth) — decode the bearer instead
    skip:      _internalTokenBypass,   // R-F390
  },
};

// ── Slow-down for repeated requests (progressive delay) ───────────────────────

// R-F3072 — same split as the standard tier. At delayAfter:80 the ramp fired on
// a single dashboard tab left open ~12 minutes (~85 first-party requests) and
// added up to 5s to every subsequent call — the "the app got really slow and
// then stopped working" symptom, caused entirely by our own auto-refresh.
const _slowAfter = _authedMax(400, 80);
const speedLimiter = slowDown({
  windowMs:        15 * 60 * 1000,
  delayAfter:      _slowAfter,
  delayMs:         (used, req) => Math.max(0, used - _slowAfter(req)) * 200,
  maxDelayMs:      5000,           // max 5s delay
  keyGenerator:    _identityKey,
  skip:            _internalTokenBypass,   // R-F390
});

// ── Apply All Rate Limiting ───────────────────────────────────────────────────

export function applyRateLimiting(app) {

  // Global — all routes
  app.use('/api/', rateLimit(TIERS.standard));
  app.use('/api/', speedLimiter);

  // Auth routes
  // R-F3180 — before the generic tiers so the tighter cap wins.
  app.use('/api/vetting-portal',            rateLimit(TIERS.vettingPortal));

  app.use('/api/auth/login',                rateLimit(TIERS.auth));
  app.use('/api/auth/register',             rateLimit(TIERS.auth));
  app.use('/api/auth/verify-2fa',           rateLimit(TIERS.auth));
  app.use('/api/auth/reset-password',       rateLimit(TIERS.auth));
  app.use('/api/auth/recovery-reset',       rateLimit(TIERS.auth));
  // R-F609 (2026-05-16) — add forgot-password and the actual 2FA route.
  // Pre-R-F609 only /verify-2fa was rate-limited but the live route is
  // /2fa/authenticate (server.mjs:3497) — the limiter mounted nothing,
  // so the TOTP code (~ 1M keys with the otplib default window) was
  // unbounded against the global TIERS.standard 150 req / 15 min only.
  // forgot-password was likewise unbounded — attackers could spam reset
  // emails (cost + nuisance) or enumerate users via timing differences.
  app.use('/api/auth/forgot-password',      rateLimit(TIERS.auth));
  app.use('/api/auth/2fa/authenticate',     rateLimit(TIERS.auth));

  // Expensive compute routes
  app.use('/api/sweep',                rateLimit(TIERS.sweep));
  app.use('/api/aria/think',           rateLimit(TIERS.ariaThin));
  app.use('/api/aria/chat',            rateLimit(TIERS.ariaChat));
  app.use('/api/brain/sweep',          rateLimit(TIERS.sweep));
  app.use('/api/compliance',           rateLimit(TIERS.compliance));
  app.use('/api/export',               rateLimit(TIERS.export));

  // Admin panel
  app.use('/api/admin',                rateLimit(TIERS.admin));

  console.log('[rateLimiter] Rate limiting active on all /api/* routes');
}

// ── Security Headers ──────────────────────────────────────────────────────────

export function applySecurityHeaders(app) {
  // ── R-F3840: drop 'unsafe-inline' from script-src ──────────────────────────
  // Hash every inline <script> this server serves and name the hashes in the
  // policy instead. Rationale, and why hashing beats a nonce or externalising
  // 29 files, is in lib/http/cspHashes.mjs.
  //
  // Computed at BOOT from the files about to be served: the browser hashes exact
  // bytes, and these HTML files are CRLF on a Windows checkout but LF in the
  // Linux image, so any checked-in hash list would be wrong in production.
  //
  // FAIL-OPEN, deliberately. Hashes and 'unsafe-inline' are mutually exclusive —
  // once one hash is present the browser ignores 'unsafe-inline' — so a scan
  // that returned nothing (unreadable directory, changed layout) would blank
  // every page. If the scan finds no blocks we keep 'unsafe-inline' and say so,
  // because a broken UI is a worse outcome than the hygiene gap this closes.
  // ARIA_CSP_ALLOW_INLINE_SCRIPT=1 forces the old behaviour back without a code
  // change, as an operator escape hatch.
  const _publicDir = join(dirname(fileURLToPath(import.meta.url)), '..', 'public');
  const _forceInline = (process.env.ARIA_CSP_ALLOW_INLINE_SCRIPT || '').toLowerCase() === '1';
  let _scriptSrc = ["'self'", "'unsafe-inline'"];
  if (!_forceInline) {
    let scan = { hashes: [], files: 0, blocks: 0 };
    try {
      scan = computeInlineScriptHashes(_publicDir);
    } catch (e) {
      console.warn(`[CSP] R-F3840 inline-script scan FAILED (${e.message}) — keeping 'unsafe-inline'`);
    }
    if (scan.hashes.length > 0) {
      _scriptSrc = ["'self'", ...scan.hashes];
      console.log(
        `[CSP] R-F3840 script-src 'unsafe-inline' REMOVED — ${scan.hashes.length} inline-script `
        + `hashes across ${scan.blocks} blocks in ${scan.files} HTML files`,
      );
    } else {
      console.warn(
        `[CSP] R-F3840 no inline scripts found under ${_publicDir} — keeping 'unsafe-inline'. `
        + 'This is the fail-open branch: a wrong hash set blanks every page.',
      );
    }
  } else {
    console.warn("[CSP] R-F3840 ARIA_CSP_ALLOW_INLINE_SCRIPT=1 — script-src 'unsafe-inline' RESTORED by operator");
  }

  app.use(helmet({
    contentSecurityPolicy: {
      directives: {
        defaultSrc:  ["'self'"],
        // R-F1919 (G6b): scriptSrcAttr 'none' BLOCKS all inline on*= event handlers
        // (the #3 DOM-XSS class) — every served page's handlers were migrated to
        // delegated addEventListener first, so nothing breaks. `'unsafe-inline'`
        // stays on scriptSrc for now because each page still has a large inline
        // <script> app block; fully dropping it needs those externalised (tracked
        // follow-up). The stale "Angular needs this" note was wrong — Angular is
        // dead/undeployed; the real reason is the inline page-app scripts.
        // R-F3840 — 'self' + a sha256 per inline block (see above). Falls back to
        // 'unsafe-inline' only if the boot scan found nothing.
        scriptSrc:     _scriptSrc,
        scriptSrcAttr: ["'none'"],                     // R-F1919: no inline event handlers
        styleSrc:    ["'self'", "'unsafe-inline'", 'fonts.googleapis.com'],
        fontSrc:     ["'self'", 'fonts.gstatic.com'],
        imgSrc:      ["'self'", 'data:', 'blob:'],
        connectSrc:  ["'self'", 'wss:', 'https:'],
        // R-F3559 — the public model card embeds the same-origin, authenticated
        // WhatsApp manager. Keep third-party frames blocked while permitting that
        // owner-scoped surface to render.
        frameSrc:    ["'self'"],
        objectSrc:   ["'none'"],
        // R-F2604: base-uri stops a <base> tag hijacking relative script/href URLs;
        // frame-ancestors 'self' is the CSP-native clickjacking guard; form-action
        // 'self' stops an injected <form> from exfiltrating to a foreign endpoint.
        baseUri:        ["'self'"],
        frameAncestors: ["'self'"],
        formAction:     ["'self'"],
      },
    },
    crossOriginEmbedderPolicy: false,   // allow iframe for share brief
  }));

  // R-F2604: removed a dead "content-length-limit" middleware here — it set a
  // fabricated request header (`req.headers['content-length-limit']='500kb'`) that
  // Express/undici never honour, so it enforced nothing while reading as protection.
  // Real body-size enforcement is per-route via express.json({limit})/multer limits;
  // the one unbounded streaming route (/api/aria/extract-document) is capped in R-F2606.
}

// ── Input Validation Middleware ───────────────────────────────────────────────

/**
 * Returns a middleware chain for validating and sanitising input.
 * Usage:  router.post('/route', validate(rulesArray), handler)
 */
export function validate(rules) {
  return [
    ...rules,
    (req, res, next) => {
      const errors = validationResult(req);
      if (!errors.isEmpty()) {
        return res.status(400).json({
          error:  'Validation failed',
          fields: errors.array().map(e => ({ field: e.path, message: e.msg })),
        });
      }
      next();
    },
  ];
}

// ── Validation Rule Sets ──────────────────────────────────────────────────────

export const rules = {

  // Auth
  login: [
    body('email').isEmail().normalizeEmail().withMessage('Valid email required'),
    body('password').isLength({ min: 8, max: 128 }).withMessage('Password 8-128 chars'),
  ],

  register: [
    body('email').isEmail().normalizeEmail(),
    body('password').isLength({ min: 12 }).withMessage('Min 12 characters')
      .matches(/[A-Z]/).withMessage('Needs uppercase')
      .matches(/[0-9]/).withMessage('Needs number')
      .matches(/[^A-Za-z0-9]/).withMessage('Needs special character'),
    body('name').trim().isLength({ min: 2, max: 100 }).escape(),
  ],

  // ARIA / Brain
  ariaThink: [
    body('question').trim().isLength({ min: 5, max: 2000 })
      .withMessage('Question must be 5-2000 characters').escape(),
    body('context').optional().isObject(),
    body('fast').optional().isBoolean(),
  ],

  ariaChat: [
    body('message').trim().isLength({ min: 1, max: 4000 })
      .withMessage('Message must be 1-4000 characters'),
    body('session_id').optional().trim().isAlphanumeric().isLength({ max: 64 }),
  ],

  // Compliance screening
  complianceScreen: [
    body('entity_name').trim().isLength({ min: 2, max: 500 }).escape(),
    body('entity_country').optional().trim().isLength({ max: 100 }).escape(),
    body('product_category').optional().trim().isLength({ max: 200 }).escape(),
    body('document_text').optional().isLength({ max: 100000 }),
  ],

  // BD Pipeline
  pipelineDeal: [
    body('title').trim().isLength({ min: 2, max: 300 }).escape(),
    body('market').trim().isLength({ min: 2, max: 100 }).escape(),
    body('value').optional().isFloat({ min: 0, max: 1e12 }),
    body('stage').isIn(['IDENTIFIED', 'QUALIFIED', 'PROPOSED', 'NEGOTIATING', 'WON', 'LOST', 'NO_BID']),
    body('win_probability').optional().isFloat({ min: 0, max: 1 }),
  ],

  // Signal / opportunity search
  search: [
    query('q').optional().trim().isLength({ max: 500 }).escape(),
    query('market').optional().trim().isLength({ max: 100 }).escape(),
    query('limit').optional().isInt({ min: 1, max: 200 }),
    query('offset').optional().isInt({ min: 0 }),
  ],

  // Lead outcome
  leadOutcome: [
    param('lead_id').trim().isUUID(),
    body('outcome').isIn(['WON', 'LOST', 'NO_BID']),
    body('market').optional().trim().isLength({ max: 100 }).escape(),
    body('notes').optional().trim().isLength({ max: 2000 }).escape(),
  ],

  // Lead rating
  leadRating: [
    param('lead_id').trim().isUUID(),
    body('rating').isInt({ min: 1, max: 5 }),
    body('is_false_alarm').optional().isBoolean(),
  ],

  // Admin
  adminUserAction: [
    param('user_id').trim().isUUID(),
    body('action').isIn(['suspend', 'activate', 'force-logout', 'reset-2fa']),
  ],
};

// ── XSS / Injection Guard (additional layer) ──────────────────────────────────

// Patterns that are a real XSS threat IF reflected unsanitised into
// HTML output. These rules make sense for form fields on a page that
// renders user input (login, deal entry, etc). They do NOT make sense
// for LLM chat messages or extracted document content, where the text
// is consumed by Claude/DeepSeek and never rendered as HTML.
//
// Previous list (2026-04-17 23:08 incident) included `\$\{.*?\}` and
// `\{\{.*?\}\}` which match standard JavaScript template literals and
// Angular/Handlebars/Jinja syntax — present in EVERY code-containing
// document. A user uploading a PDF of a quantum-computing security
// whitepaper got blocked because the paper contained `${algorithm}`
// style code snippets. Tightened + scoped below.
const DANGEROUS_PATTERNS = [
  /<script[\s\S]*?>[\s\S]*?<\/script>/gi,
  /javascript:\s*[a-z0-9]/gi,          // "javascript:doBadThing" — require a real call after
  /\son(?:click|load|error|mouseover|submit|focus|blur|change|keydown|keyup|mousedown|mouseup)\s*=\s*["']/gi,  // actual event-handler attributes in HTML
  /';\s*drop\s+table\s+/gi,             // require a table name after
  /;\s*delete\s+from\s+/gi,
];

// Paths that legitimately accept free-form text, LLM messages, or
// document content. XSS guard is SKIPPED for these — the content is
// consumed by an LLM or written to a structured store, never reflected
// into a browser-rendered page. Exempting them fixes the 2026-04-17
// PDF-upload incident where `${}` / `{{}}` in a security-research PDF
// tripped the template-injection pattern.
const XSS_GUARD_EXEMPT_PREFIXES = [
  '/api/aria/chat',           // chat + streaming chat
  '/api/aria/think',
  '/api/aria/read-document',
  '/api/aria/corpus',         // corpus ingest (PDF/DOCX bulk text)
  '/api/aria/document',       // document extraction pipeline
  '/api/aria/investigate',    // research topics — free-form query text
  '/api/aria/crawl',          // web crawl — URLs + context text
  '/api/aria/deep-research',
  '/api/aria/teach',          // /teach topic: content — user corpus additions
  '/api/aria/writers/produce',  // writer inputs include template-looking content
];

export function xssGuard(req, res, next) {
  // Path-level exemption for LLM / document / research endpoints.
  // These paths accept content that is consumed by downstream LLMs
  // or ingested into structured stores — not rendered to a browser.
  const reqPath = (req.path || req.url || '').toLowerCase();
  for (const prefix of XSS_GUARD_EXEMPT_PREFIXES) {
    if (reqPath.startsWith(prefix)) {
      return next();
    }
  }

  const check = (value) => {
    if (typeof value !== 'string') return value;
    for (const pattern of DANGEROUS_PATTERNS) {
      // Reset lastIndex — /g flags carry state across .test() calls
      pattern.lastIndex = 0;
      if (pattern.test(value)) {
        return null;  // reject dangerous input
      }
    }
    return value;
  };

  // Returns {rejected, field} — first offending field stops the scan.
  // Caller is responsible for NOT calling next() when rejected=true.
  const sanitize = (obj) => {
    if (!obj || typeof obj !== 'object') return {rejected: false};
    for (const [key, value] of Object.entries(obj)) {
      if (typeof value === 'string') {
        const checked = check(value);
        if (checked === null) {
          return {rejected: true, field: key};
        }
        obj[key] = checked;
      } else if (typeof value === 'object') {
        const inner = sanitize(value);
        if (inner.rejected) return inner;
      }
    }
    return {rejected: false};
  };

  if (req.body) {
    const r = sanitize(req.body);
    if (r.rejected) {
      return res.status(400).json({ error: `Invalid input in field: ${r.field}` });
    }
  }
  if (req.query) {
    const r = sanitize(req.query);
    if (r.rejected) {
      return res.status(400).json({ error: `Invalid input in field: ${r.field}` });
    }
  }
  next();
}

// ── Apply Input Validation ────────────────────────────────────────────────────

export function applyInputValidation(app) {
  // Parse JSON with size limits
  // Note: These must come BEFORE route registration
  // In your server.mjs, replace express.json() with:
  //   app.use('/api/aria',  express.json({ limit: '500kb' }));
  //   app.use('/api/',      express.json({ limit: '100kb' }));
  //   app.use('/api/',      express.urlencoded({ extended: true, limit: '50kb' }));

  app.use('/api/', xssGuard);
  console.log('[inputValidation] XSS guard and input sanitization active');
}

// ── Usage Example in your route files ─────────────────────────────────────────
/*
import { validate, rules } from '../middleware/rateLimiter.mjs';

// In your auth routes:
router.post('/login', validate(rules.login), async (req, res) => { ... });

// In your ARIA routes:
router.post('/think', validate(rules.ariaThink), async (req, res) => { ... });

// In your BD pipeline routes:
router.post('/deals', validate(rules.pipelineDeal), async (req, res) => { ... });
*/
