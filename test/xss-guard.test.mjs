// XSS guard middleware — regression tests for the 2026-04-17 23:08
// incident where a PDF upload to /api/aria/chat was rejected with
// "Invalid input in field: message" because the PDF's extracted text
// contained `${algorithm}` code snippets matching the
// template-literal injection pattern.
//
// Rules after the fix:
//   1. Chat / document / research / corpus endpoints are EXEMPT —
//      their content goes to an LLM, not rendered in a browser.
//   2. Remaining patterns tightened so they only match actual HTML
//      script tags and event-handler attributes.
//   3. Template literals `${...}` and `{{...}}` are NO LONGER
//      blocked — they're normal in code and legal text.

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { xssGuard } from '../middleware/rateLimiter.mjs';

function mockReq(path, body = {}, query = {}) {
  return { path, url: path, body, query };
}

function mockRes() {
  const res = {
    _status: 200,
    _json: null,
    status(code) { this._status = code; return this; },
    json(obj) { this._json = obj; return this; },
  };
  return res;
}

function mockNext() {
  const fn = () => { fn.called = true; };
  fn.called = false;
  return fn;
}

// ═══════════════════════════════════════════════════════════════════
// 1. Exempt chat/document endpoints pass through untouched
// ═══════════════════════════════════════════════════════════════════

describe('xssGuard path exemptions', () => {
  it('allows template-literal content through /api/aria/chat', () => {
    const req = mockReq('/api/aria/chat', {
      message: 'Here is a code snippet: `const x = `${algorithm}`;` — analyse it.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
    assert.equal(res._status, 200);
  });

  it('allows Handlebars-looking content through /api/aria/chat/stream', () => {
    const req = mockReq('/api/aria/chat/stream', {
      message: 'The template renders {{user.name}} on each row.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });

  it('allows PDF text with event-handler-shaped substrings through /api/aria/read-document', () => {
    const req = mockReq('/api/aria/read-document', {
      content: 'The paper mentions onclick handlers as an attack surface.',
      encoding: 'utf-8',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });

  it('allows corpus ingest with code examples', () => {
    const req = mockReq('/api/aria/corpus/ingest', {
      text: 'Template: `onload="alert(${payload})"` — example of a past incident.',
      tier: 'B',
      source_class: 'security_research',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });
});

// ═══════════════════════════════════════════════════════════════════
// 2. Non-exempt endpoints still block obvious XSS
// ═══════════════════════════════════════════════════════════════════

describe('xssGuard remaining protection', () => {
  it('blocks a literal <script> tag on /api/user/profile', () => {
    const req = mockReq('/api/user/profile', {
      displayName: '<script>alert(1)</script>',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(res._status, 400);
    assert.ok(res._json.error.includes('Invalid input'));
    assert.equal(next.called, false);
  });

  it('blocks an event-handler attribute on a non-exempt path', () => {
    const req = mockReq('/api/user/profile', {
      bio: '<img src=x onclick="evil()">',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(res._status, 400);
    assert.equal(next.called, false);
  });

  it('blocks a literal javascript: URL on a non-exempt path', () => {
    const req = mockReq('/api/user/profile', {
      website: 'javascript:alert(1)',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(res._status, 400);
    assert.equal(next.called, false);
  });
});

// ═══════════════════════════════════════════════════════════════════
// 3. Tightened patterns — false positives from earlier version gone
// ═══════════════════════════════════════════════════════════════════

describe('xssGuard tightened patterns (no false positives)', () => {
  it('no longer blocks ${} template literals on non-exempt paths either', () => {
    // `${x}` alone is no longer in the pattern list — it's valid in
    // many legitimate contexts (shell variables, Ruby interpolation,
    // version strings).
    const req = mockReq('/api/user/profile', {
      bio: 'Running ${VERSION} in production.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
    assert.equal(res._status, 200);
  });

  it('no longer blocks {{handlebars}} syntax', () => {
    const req = mockReq('/api/user/profile', {
      bio: 'Template uses {{name}} binding.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });

  it('does NOT block the word "javascript" when not followed by a call', () => {
    const req = mockReq('/api/user/profile', {
      bio: 'I mostly program in javascript.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });

  it('does NOT block mention of "onclick" as a word', () => {
    const req = mockReq('/api/user/profile', {
      bio: 'Avoid inline onclick handlers — they are an XSS risk.',
    });
    const res = mockRes();
    const next = mockNext();
    xssGuard(req, res, next);
    assert.equal(next.called, true);
  });
});
