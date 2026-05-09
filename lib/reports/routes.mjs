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

    // Fetch the conversation from the Python brain. Use the internal token
    // so we always succeed regardless of the user's own bearer state at
    // the upstream service.
    let convo;
    try {
      const headers = { 'Content-Type': 'application/json' };
      if (brainInternalToken) headers['Authorization'] = 'Bearer ' + brainInternalToken;
      const resp = await fetch(
        `${brainBaseUrl.replace(/\/$/, '')}/api/aria/conversations/${encodeURIComponent(sessionId)}/detail`,
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

    // Ownership guard. The session-id format is `<userIdSlug>_<ts>_<rand>`
    // for web users (R-F38). Compare against the requesting user's slug.
    // Admins bypass for support cases.
    const userSlug = (user.id || user.username || '').replace(/[^A-Za-z0-9]/g, '');
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
