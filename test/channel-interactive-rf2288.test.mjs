// test/channel-interactive-rf2288.test.mjs
// Capability test for Telegram Channel Interactive Engine (R-F2288)

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../lib/telegram/channelInteractive.mjs');
const {
  registerKeyword,
  registerPostKeyword,
  resolveKeyword,
  matchKeyword,
  buildDeepDive,
  buildFallbackDeepDive,
  registerPoll,
  recordPollVote,
  closePoll,
  getActivePolls,
  getEngagementStats,
  _resetInteractiveState,
} = mod;

describe('ChannelInteractive — registerKeyword', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('registers a keyword with response', () => {
    const result = registerKeyword({ keyword: 'test_kw', response: 'Deep dive response' });
    assert.equal(result.ok, true);
    assert.equal(result.keyword, 'test_kw');
  });

  it('rejects empty keyword', () => {
    const result = registerKeyword({ keyword: '', response: 'x' });
    assert.equal(result.ok, false);
  });

  it('rejects missing response', () => {
    const result = registerKeyword({ keyword: 'test' });
    assert.equal(result.ok, false);
  });

  it('cleans keyword to alphanumeric', () => {
    const result = registerKeyword({ keyword: 'Test Keyword!@#', response: 'Cleaned' });
    assert.equal(result.ok, true);
    assert.equal(result.keyword, 'test_keyword');
  });
});

describe('ChannelInteractive — registerPostKeyword', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('generates keyword from title and registers deep-dive', () => {
    const result = registerPostKeyword('Angola Sanctions Update', 'Deep analysis of Angola sanctions...');
    assert.equal(result.ok, true);
    assert.ok(result.keyword.length > 0);
    assert.ok(result.keyword.includes('angola'));
  });
});

describe('ChannelInteractive — resolveKeyword', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('returns deep-dive for registered keyword', () => {
    registerKeyword({ keyword: 'test_dive', response: 'Here is your deep dive analysis' });
    const result = resolveKeyword('test_dive', 'user123');
    assert.equal(result.ok, true);
    assert.ok(result.response.includes('deep dive'));
  });

  it('returns error for unknown keyword', () => {
    const result = resolveKeyword('nonexistent', 'user123');
    assert.equal(result.ok, false);
    assert.ok(result.error.includes('Unknown keyword'));
  });

  it('enforces user cooldown', () => {
    _resetInteractiveState();
    registerKeyword({ keyword: 'cooldown_test', response: 'Response' });
    // First call should succeed
    const first = resolveKeyword('cooldown_test', 'user456');
    assert.equal(first.ok, true);
    // Second call should be rate-limited
    const second = resolveKeyword('cooldown_test', 'user456');
    assert.equal(second.ok, false);
    assert.ok(second.cooldownRemaining > 0);
  });

  it('allows different users independently', () => {
    _resetInteractiveState();
    registerKeyword({ keyword: 'multi_user', response: 'Shared response' });
    const user1 = resolveKeyword('multi_user', 'user_a');
    assert.equal(user1.ok, true);
    const user2 = resolveKeyword('multi_user', 'user_b');
    assert.equal(user2.ok, true);
  });
});

describe('ChannelInteractive — matchKeyword', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('matches exact keyword', () => {
    registerKeyword({ keyword: 'angola_oil', response: 'Deep dive' });
    const result = matchKeyword('angola_oil');
    assert.equal(result.matched, true);
    assert.equal(result.keyword, 'angola_oil');
  });

  it('matches keyword at start of text', () => {
    registerKeyword({ keyword: 'sanctions', response: 'Deep dive' });
    const result = matchKeyword('sanctions update please');
    assert.equal(result.matched, true);
  });

  it('returns no match for unrelated text', () => {
    const result = matchKeyword('hello how are you');
    assert.equal(result.matched, false);
  });

  it('handles null/empty input', () => {
    assert.equal(matchKeyword(null).matched, false);
    assert.equal(matchKeyword('').matched, false);
  });
});

describe('ChannelInteractive — buildDeepDive', () => {
  it('generates structured deep-dive from signal', () => {
    const dive = buildDeepDive({
      title: 'Sanctions on Angola',
      summary: 'New sanctions imposed on Angolan oil sector entities',
      source: 'reuters',
      country: 'Angola',
      sector: 'Oil & Gas',
      severity: 'high',
      timestamp: new Date().toISOString(),
    });
    assert.ok(dive.includes('DEEP DIVE'));
    assert.ok(dive.includes('Sanctions on Angola'));
    assert.ok(dive.includes('Angola'));
    assert.ok(dive.includes('Oil & Gas'));
    assert.ok(dive.includes('Implications'));
  });

  it('handles null signal gracefully', () => {
    const dive = buildDeepDive(null);
    assert.ok(dive.includes('No additional information'));
  });

  it('generates relevant implications for sanctions', () => {
    const dive = buildDeepDive({
      title: 'OFAC Sanctions',
      summary: 'New OFAC sanctions on Russian entities',
      source: 'state_dept',
    });
    assert.ok(dive.includes('compliance'));
    assert.ok(dive.includes('sanctioned'));
  });

  it('generates relevant implications for tenders', () => {
    const dive = buildDeepDive({
      title: 'Tender Opportunity',
      summary: 'New procurement tender for defence equipment in Mozambique',
      source: 'world_bank',
    });
    assert.ok(dive.includes('business development'));
    assert.ok(dive.includes('tender'));
  });
});

describe('ChannelInteractive — buildFallbackDeepDive', () => {
  it('generates fallback message', () => {
    const fallback = buildFallbackDeepDive('test_topic');
    // The keyword is markdown-escaped (underscore becomes \_)
    assert.ok(fallback.includes('test\\_topic') || fallback.includes('test_topic'));
    assert.ok(fallback.includes('pre-built deep dive'));
  });
});

describe('ChannelInteractive — poll management', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('registers and tracks polls', () => {
    registerPoll('poll_1', 'Best sector?', ['Oil', 'Gas', 'Mining']);
    const active = getActivePolls();
    assert.equal(active.length, 1);
    assert.equal(active[0].question, 'Best sector?');
  });

  it('records votes', () => {
    _resetInteractiveState();
    registerPoll('poll_2', 'Favourite?', ['A', 'B', 'C']);
    recordPollVote('poll_2', [0]);
    recordPollVote('poll_2', [0]);
    recordPollVote('poll_2', [1]);

    const result = closePoll('poll_2');
    assert.equal(result.ok, true);
    assert.equal(result.results.totalVotes, 3);
    assert.equal(result.results.winner, 'A');
    assert.equal(result.results.winnerVotes, 2);
  });

  it('closes polls and returns results', () => {
    _resetInteractiveState();
    registerPoll('poll_3', 'Test?', ['Yes', 'No']);
    const result = closePoll('poll_3');
    assert.equal(result.ok, true);
    assert.ok(result.results.question);
    assert.ok(result.results.options);
  });

  it('returns error for unknown poll', () => {
    const result = closePoll('nonexistent');
    assert.equal(result.ok, false);
  });
});

describe('ChannelInteractive — getEngagementStats', () => {
  before(() => _resetInteractiveState());
  after(() => _resetInteractiveState());

  it('returns stats with expected structure', () => {
    const stats = getEngagementStats();
    assert.ok(typeof stats.totalKeywords === 'number');
    assert.ok(typeof stats.totalTriggers === 'number');
    assert.ok(Array.isArray(stats.topKeywords));
    assert.ok(typeof stats.activePolls === 'number');
  });

  it('tracks keyword triggers', () => {
    _resetInteractiveState();
    registerKeyword({ keyword: 'track_me', response: 'Tracked response' });
    resolveKeyword('track_me', 'user_track');
    const stats = getEngagementStats();
    assert.equal(stats.totalTriggers, 1);
    assert.equal(stats.topKeywords[0].keyword, 'track_me');
    assert.equal(stats.topKeywords[0].triggered, 1);
  });
});
