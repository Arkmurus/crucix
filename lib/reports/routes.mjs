// lib/reports/routes.mjs
// Express router for /api/reports/*.
//
// Routes:
//   POST /api/reports/pdf     — auth; { sessionId, messageIndex, classification?,
//                                       subject? } → PDF stream
//   POST /api/reports/verify  — public; { contentSha256, userId, sessionId,
//                                         messageIndex, generatedAt, signature } →
//                              { valid, reason }
//
// /pdf works by:
//   1. authenticating the requesting user (JWT)
//   2. fetching the canonical conversation from the Python brain
//      (fly.io) using the internal token
//   3. picking message[messageIndex]
//   4. confirming the requesting user owns that conversation
//      (conversation.userId === request user.id)
//   5. rendering with generateAuditGradeReport
//
// Step 4 is the trust gate: even if a user passes someone else's
// sessionId, the ownership check rejects it. The fly side enforces the
// same check via conversation_store; this layer is defence-in-depth.

import express from 'express';
import { generateAuditGradeReport } from './pdf_generator.mjs';
import { signingConfigured, contentHash, sign as signReport, verify as verifyReport } from './sign.mjs';

export function createReportsRouter({ requireAuth, findUserById, brainBaseUrl, brainInternalToken }) {
  if (!requireAuth || !findUserById) {
    throw new Error('createReportsRouter: missing requireAuth/findUserById');
  }
  const router = express.Router();

  // R-F2857 — the PDF's constitution clause count must be the LIVE value or
  // ABSENT. The generator no longer hardcodes it (it read "23-clause" while the
  // live constitution was v37/37), so this is where the real number comes from.
  //
  // Three properties this must hold, in priority order:
  //   1. It can NEVER fail or slow a render — any error/timeout yields null and
  //      the PDF simply omits the count.
  //   2. Only SUCCESSES are cached. Caching a transient failure would pin the
  //      count absent for the whole TTL — the "never cache the transient"
  //      lesson from the non-strict-read clobber class.
  //   3. A wrong number is worse than no number, so anything non-finite or
  //      non-positive is treated as unknown.
  let _clauseCache = { value: null, at: 0 };
  const _CLAUSE_TTL_MS = 5 * 60 * 1000;
  async function liveClauseCount() {
    const now = Date.now();
    if (_clauseCache.value !== null && now - _clauseCache.at < _CLAUSE_TTL_MS) {
      return _clauseCache.value;
    }
    const ac = new AbortController();
    const timer = setTimeout(() => ac.abort(), 2000);
    try {
      const headers = {};
      if (brainInternalToken) headers['Authorization'] = 'Bearer ' + brainInternalToken;
      const resp = await fetch(
        `${brainBaseUrl.replace(/\/$/, '')}/api/aria/constitution/version`,
        { headers, method: 'GET', signal: ac.signal },
      );
      if (!resp.ok) return null;
      const body = await resp.json();
      const n = Number(body?.clause_count);
      if (!Number.isFinite(n) || n <= 0) return null;
      _clauseCache = { value: n, at: now };   // cache successes only
      return n;
    } catch {
      return null;                            // never propagate into the render
    } finally {
      clearTimeout(timer);
    }
  }
  router.use(express.json({ limit: '500kb' }));

  router.post('/pdf', requireAuth, async (req, res) => {
    const userId = req.user?.userId;
    if (!userId) return res.status(401).json({ error: 'auth required' });
    const user = findUserById(userId);
    if (!user) return res.status(404).json({ error: 'user not found' });

    const sessionId = (req.body?.sessionId || '').toString().trim();
    const messageIndex = req.body?.messageIndex;
    if (!sessionId) return res.status(400).json({ error: 'sessionId required' });
    if (!Number.isInteger(messageIndex) || messageIndex < 0) {
      return res.status(400).json({ error: 'messageIndex (non-negative integer) required' });
    }

    const subject = (req.body?.subject || '').toString().slice(0, 200) || null;
    const classification = (req.body?.classification || 'CONFIDENTIAL').toString().toUpperCase();
    if (!['PUBLIC', 'INTERNAL', 'CONFIDENTIAL'].includes(classification)) {
      return res.status(400).json({ error: 'classification must be PUBLIC | INTERNAL | CONFIDENTIAL' });
    }

    // Derive the user slug up-front — needed both for the brain /detail
    // ownership query (R-F606) and the local defence-in-depth check below.
    // R-F742 (2026-05-20) derivation: prefer EMAIL > USERNAME > id so the
    // slug survives seenode redeploys that regenerate user.id but keep
    // email stable. Must match aria.html:652 exactly.
    const userSlug = (user.email || user.username || user.id || '').replace(/[^A-Za-z0-9]/g, '');

    // Fetch the conversation from the Python brain. Use the internal token
    // so we always succeed regardless of the user's own bearer state at
    // the upstream service.
    // R-F769 (2026-05-21) — pass ?user_id=<slug> so the R-F606 ownership
    // check on the brain side passes. Pre-R-F769 this caller omitted
    // user_id entirely and every PDF export 400'd at the brain → 502 to
    // the user ("failed to fetch conversation"). The aria.html sibling
    // was fixed in R-F739; this caller was missed.
    let convo;
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (brainInternalToken) headers['Authorization'] = 'Bearer ' + brainInternalToken;
      const resp = await fetch(
        `${brainBaseUrl.replace(/\/$/, '')}/api/aria/conversations/${encodeURIComponent(sessionId)}/detail?user_id=${encodeURIComponent(userSlug)}`,
        { headers, method: 'GET' },
      );
      if (resp.status === 404) return res.status(404).json({ error: 'conversation not found' });
      if (!resp.ok) {
        const text = await resp.text().catch(() => '');
        console.warn('[reports] brain fetch failed:', resp.status, text.slice(0, 200));
        return res.status(502).json({ error: 'failed to fetch conversation', status: resp.status });
      }
      convo = await resp.json();
    } catch (err) {
      console.error('[reports] brain fetch error:', err);
      return res.status(502).json({ error: 'failed to reach brain', detail: err.message });
    }

    // Local ownership guard — defence-in-depth on top of the brain-side
    // R-F606 check. Session-id format is `<userIdSlug>_<ts>_<rand>` for
    // web users (R-F38). Admins bypass for support cases.
    const sidPrefix = sessionId.split('_', 1)[0];
    if (user.role !== 'admin' && userSlug && sidPrefix && userSlug !== sidPrefix) {
      return res.status(403).json({ error: 'session does not belong to this user' });
    }

    const messages = convo.messages || [];
    if (messageIndex >= messages.length) {
      return res.status(404).json({ error: `messageIndex ${messageIndex} out of range (have ${messages.length})` });
    }
    const target = messages[messageIndex] || {};
    const content = (target.content || '').toString();
    if (!content.trim()) {
      return res.status(400).json({ error: 'target message has no content' });
    }

    // Default subject to conversation title or first 60 chars of content.
    const finalSubject = subject || (convo.title || content.split('\n', 1)[0].slice(0, 80));

    let pdf;
    try {
      pdf = await generateAuditGradeReport(content, {
        subject: finalSubject,
        userEmail: user.email,
        userId: user.id,
        sessionId,
        messageIndex,
        ariaVersion: process.env.ARIA_VERSION_LABEL || 'ARIA',
        constitutionClauseCount: await liveClauseCount(),   // R-F2857 — live or absent
        verifyUrl: (process.env.APP_URL || '').replace(/\/$/, '')
          + '/api/reports/verify',
      }, { classification });
    } catch (err) {
      console.error('[reports] render error:', err);
      return res.status(500).json({ error: 'pdf render failed', detail: err.message });
    }

    const filename = _safeFilename(finalSubject) + '.pdf';
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.setHeader('X-Report-Signed', signingConfigured() ? '1' : '0');
    res.setHeader('X-Report-Content-Sha256', contentHash(content));
    res.setHeader('Cache-Control', 'private, no-cache');
    res.send(pdf);
  });

  // Public verification — anyone holding a PDF can extract its audit-trail
  // fields and POST them here to confirm the signature is intact. We
  // don't require auth because the verifier may be a third party (a
  // counterparty's compliance officer) without an account.
  router.post('/verify', (req, res) => {
    const {
      contentSha256, userId, sessionId, messageIndex, generatedAt, signature,
    } = req.body || {};
    if (!contentSha256 || !signature) {
      return res.status(400).json({ valid: false, reason: 'contentSha256 + signature required' });
    }
    const result = verifyReport({
      contentSha256, userId, sessionId, messageIndex, generatedAt,
    }, signature);
    res.json({
      valid: !!result.valid,
      reason: result.reason || '',
      // Include the canonical we tested against so the verifier can
      // audit independently.
      v: 1,
    });
  });

  return router;
}

function _safeFilename(s) {
  return (s || 'aria-report')
    .replace(/[^A-Za-z0-9._-]+/g, '_')
    .replace(/_+/g, '_')
    .slice(0, 80) || 'aria-report';
}
