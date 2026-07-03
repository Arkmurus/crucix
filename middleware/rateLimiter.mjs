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

// R-F35 (2026-05-03): IPv6-safe IP fallback. express-rate-limit v8+ emits
// ERR_ERL_KEY_GEN_IPV6 if you mix req.ip into a custom keyGenerator
// without normalising — IPv6 addresses can otherwise bypass the limiter
// because the same prefix maps to many distinct addresses. ipKeyGenerator
// applies a /64 prefix mask to v6 and leaves v4 alone. Live evidence
// 2026-05-03 11:09:55: 3× ValidationError stack traces at boot from
// rateLimiter.mjs:112/113/119 (ariaThin/ariaChat/admin tiers).
const _ipFallback = (req, res) => ipKeyGenerator(req, res);

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
    max:       150,
    message:   { error: 'Too many requests. Please wait 15 minutes.' },
    standardHeaders: true,
    legacyHeaders:   false,
    skip:      _internalTokenBypass,   // R-F390: WA listener / sweep cross-calls
  },

  // Auth endpoints — strict to prevent brute force
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
    keyGenerator: (req, res) => req.user?.userId || req.user?.id || _ipFallback(req, res),  // R-F2383: JWT payload is {userId,...}; `.id` was always undefined → per-IP
    skip:      _internalTokenBypass,   // R-F390
  },

  // ARIA chat — more lenient, still bounded
  ariaChat: {
    windowMs:  60 * 1000,
    max:       20,
    message:   { error: 'ARIA chat rate limit reached. Max 20 messages/minute.' },
    keyGenerator: (req, res) => req.user?.userId || req.user?.id || _ipFallback(req, res),  // R-F2383: JWT payload is {userId,...}; `.id` was always undefined → per-IP
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
    keyGenerator: (req, res) => req.user?.userId || req.user?.id || _ipFallback(req, res),  // R-F2383: JWT payload is {userId,...}; `.id` was always undefined → per-IP
    skip:      _internalTokenBypass,   // R-F390
  },
};

// ── Slow-down for repeated requests (progressive delay) ───────────────────────

const speedLimiter = slowDown({
  windowMs:        15 * 60 * 1000,
  delayAfter:      80,             // start slowing after 80 req/15min
  delayMs:         (used, req) => (used - 80) * 200,   // +200ms per req over limit
  maxDelayMs:      5000,           // max 5s delay
  skip:            _internalTokenBypass,   // R-F390
});

// ── Apply All Rate Limiting ───────────────────────────────────────────────────

export function applyRateLimiting(app) {

  // Global — all routes
  app.use('/api/', rateLimit(TIERS.standard));
  app.use('/api/', speedLimiter);

  // Auth routes
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
        scriptSrc:     ["'self'", "'unsafe-inline'"],  // inline page-app <script> blocks
        scriptSrcAttr: ["'none'"],                     // R-F1919: no inline event handlers
        styleSrc:    ["'self'", "'unsafe-inline'", 'fonts.googleapis.com'],
        fontSrc:     ["'self'", 'fonts.gstatic.com'],
        imgSrc:      ["'self'", 'data:', 'blob:'],
        connectSrc:  ["'self'", 'wss:', 'https:'],
        frameSrc:    ["'none'"],
        objectSrc:   ["'none'"],
      },
    },
    crossOriginEmbedderPolicy: false,   // allow iframe for share brief
  }));

  // Request size limits — prevents large payload attacks
  // Override with specific limits on brain routes (need bigger payloads)
  app.use('/api/aria',       (req, res, next) => {
    req.headers['content-length-limit'] = '500kb';
    next();
  });
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
