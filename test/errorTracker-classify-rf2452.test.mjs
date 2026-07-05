/**
 * R-F2452 — classifyError must treat client malformed-JSON as CLIENT_INPUT
 * (a 400 bad-request), not STRUCTURAL (a server-side code defect). Regression:
 * real code parse defects + AUTH/TRANSIENT stay unchanged.
 */
import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { classifyError, SEVERITY } from '../lib/observability/errorTracker.mjs';

describe('R-F2452 client malformed-JSON classification', () => {
  it('Express body-parser entity.parse.failed → client_input', () => {
    const err = Object.assign(new SyntaxError('Unexpected token } in JSON at position 12'),
      { type: 'entity.parse.failed', status: 400, statusCode: 400, expose: true });
    assert.equal(classifyError(err, 'express_route'), SEVERITY.CLIENT_INPUT);
  });

  it('a 400 with a JSON/parse message → client_input', () => {
    const err = Object.assign(new Error('Unexpected token in JSON'), { status: 400 });
    assert.equal(classifyError(err), SEVERITY.CLIENT_INPUT);
  });

  it('a real server-side parse/schema defect (not 400) stays STRUCTURAL', () => {
    assert.equal(classifyError(new Error('failed to parse config schema')), SEVERITY.STRUCTURAL);
    assert.equal(classifyError(new Error('unexpected token in XML response')), SEVERITY.STRUCTURAL);
  });

  it('AUTH and TRANSIENT classification unchanged (regression)', () => {
    assert.equal(classifyError({ status: 401, message: 'unauthorized' }), SEVERITY.AUTH);
    assert.equal(classifyError({ status: 503, message: 'gateway timeout' }), SEVERITY.TRANSIENT);
  });
});
