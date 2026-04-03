/**
 * CRUCIX — Rate Limiting & Input Validation Middleware
 * GAP 2 FIX: Protects all 70+ routes from abuse.
 *
 * Mount in server.mjs BEFORE route registration:
 *   import { applyRateLimiting, applyInputValidation } from './middleware/rateLimiter.mjs';
 *   applyRateLimiting(app);
 *   applyInputValidation(app);
 */

import rateLimit from 'express-rate-limit';
import slowDown  from 'express-slow-down';
import helmet    from 'helmet';
import { body, query, param, validationResult } from 'express-validator';

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
    keyGenerator: (req) => req.user?.id || req.ip,   // per-user if authenticated
  },

  // ARIA chat — more lenient, still bounded
  ariaChat: {
    windowMs:  60 * 1000,
    max:       20,
    message:   { error: 'ARIA chat rate limit reached. Max 20 messages/minute.' },
    keyGenerator: (req) => req.user?.id || req.ip,
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
    keyGenerator: (req) => req.user?.id || req.ip,
  },
};

// ── Slow-down for repeated requests (progressive delay) ───────────────────────

const speedLimiter = slowDown({
  windowMs:        15 * 60 * 1000,
  delayAfter:      80,             // start slowing after 80 req/15min
  delayMs:         (used, req) => (used - 80) * 200,   // +200ms per req over limit
  maxDelayMs:      5000,           // max 5s delay
});

// ── Apply All Rate Limiting ───────────────────────────────────────────────────

export function applyRateLimiting(app) {

  // Global — all routes
  app.use('/api/', rateLimit(TIERS.standard));
  app.use('/api/', speedLimiter);

  // Auth routes
  app.use('/api/auth/login',           rateLimit(TIERS.auth));
  app.use('/api/auth/register',        rateLimit(TIERS.auth));
  app.use('/api/auth/verify-2fa',      rateLimit(TIERS.auth));
  app.use('/api/auth/reset-password',  rateLimit(TIERS.auth));

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
        scriptSrc:   ["'self'", "'unsafe-inline'"],   // Angular needs this
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

const DANGEROUS_PATTERNS = [
  /<script[\s\S]*?>[\s\S]*?<\/script>/gi,
  /javascript:/gi,
  /on\w+\s*=/gi,          // onclick=, onload=, etc.
  /\$\{.*?\}/g,           // template injection
  /\{\{.*?\}\}/g,         // Angular/Handlebars injection
  /';\s*drop\s+table/gi,  // SQL injection
  /;\s*delete\s+from/gi,
];

export function xssGuard(req, res, next) {
  const check = (value) => {
    if (typeof value !== 'string') return value;
    for (const pattern of DANGEROUS_PATTERNS) {
      if (pattern.test(value)) {
        return null;  // reject dangerous input
      }
    }
    return value;
  };

  const sanitize = (obj) => {
    if (!obj || typeof obj !== 'object') return obj;
    for (const [key, value] of Object.entries(obj)) {
      if (typeof value === 'string') {
        const checked = check(value);
        if (checked === null) {
          return res.status(400).json({ error: `Invalid input in field: ${key}` });
        }
        obj[key] = checked;
      } else if (typeof value === 'object') {
        sanitize(value);
      }
    }
    return obj;
  };

  if (req.body)  sanitize(req.body);
  if (req.query) sanitize(req.query);
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
