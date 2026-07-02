// test/linkedin-publisher-rf2288.test.mjs
// Capability test for LinkedIn Publisher (R-F2288)

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../lib/linkedin/linkedinPublisher.mjs');
const {
  isConfigured,
  getConfig,
  canPostNow,
  recordPost,
  getState,
  formatForLinkedIn,
  _resetState,
} = mod;

describe('LinkedInPublisher — configuration', () => {
  it('reports not configured when env vars are missing', () => {
    assert.equal(isConfigured(), false);
  });

  it('getConfig returns status object', () => {
    const config = getConfig();
    assert.equal(config.configured, false);
    assert.ok(typeof config.hasToken === 'boolean');
    assert.ok(typeof config.hasOrg === 'boolean');
  });
});

describe('LinkedInPublisher — rate limiting', () => {
  before(() => _resetState());
  after(() => _resetState());

  it('allows posting initially', () => {
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, true);
    assert.equal(reason, 'ok');
  });

  it('blocks after daily limit', () => {
    _resetState();
    for (let i = 0; i < 3; i++) recordPost();
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, false);
    assert.ok(reason.includes('daily limit'));
  });

  it('respects cooldown', () => {
    _resetState();
    recordPost();
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, false);
    assert.ok(reason.includes('cooldown'));
  });
});

describe('LinkedInPublisher — getState', () => {
  before(() => _resetState());
  after(() => _resetState());

  it('returns state with expected keys', () => {
    const state = getState();
    assert.ok(typeof state.configured === 'boolean');
    assert.ok(typeof state.lastPostAt === 'number');
    assert.ok(typeof state.dailyPostCount === 'number');
  });
});

describe('LinkedInPublisher — formatForLinkedIn', () => {
  it('strips Telegram markdown', () => {
    const result = formatForLinkedIn('*Bold text* and _italic_ and `code`');
    assert.ok(!result.includes('*Bold*'));
    assert.ok(result.includes('Bold text'));
  });

  it('removes reply-keyword hints', () => {
    const result = formatForLinkedIn('Post content\n💬 Reply with `keyword` for deeper analysis');
    assert.ok(!result.includes('Reply with'));
  });

  it('adds default hashtags', () => {
    const result = formatForLinkedIn('Test post');
    assert.ok(result.includes('#Intelligence'));
    assert.ok(result.includes('#Defence'));
  });

  it('adds extra hashtags when provided', () => {
    // Extra tags replace some defaults (only 5 total)
    const result = formatForLinkedIn('Test post', { extraTags: ['#Angola'] });
    assert.ok(result.includes('#Angola'));
  });

  it('handles empty input', () => {
    assert.equal(formatForLinkedIn(''), '');
    assert.equal(formatForLinkedIn(null), '');
  });

  it('truncates long posts', () => {
    const long = 'x'.repeat(5000);
    const result = formatForLinkedIn(long);
    assert.ok(result.length <= 3100); // 3000 + hashtags
  });
});
