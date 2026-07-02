// test/channel-publisher-rf2287.test.mjs
// Capability test for Telegram Broadcast Channel Publisher (R-F2287)
//
// Tests:
//   1. scoreSignal — signal scoring and curation
//   2. formatChannelPost — post formatting
//   3. formatDailyBrief — daily briefing formatting
//   4. canPostNow / recordPost — rate limiting
//   5. curateSignals — full curation pipeline
//   6. getSchedulerState — state introspection
//   7. _resetSchedulerState — state reset

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

// Import the module
const mod = await import('../lib/telegram/channelPublisher.mjs');
const {
  scoreSignal,
  formatChannelPost,
  formatDailyBrief,
  canPostNow,
  recordPost,
  curateSignals,
  getSchedulerState,
  _resetSchedulerState,
} = mod;

// ── Helpers ────────────────────────────────────────────────────────────────────

function makeSignal(overrides = {}) {
  return {
    title: 'Test Intel Signal',
    summary: 'A test intelligence signal for channel curation testing',
    source: 'reuters',
    timestamp: new Date().toISOString(),
    severity: 'medium',
    country: 'Angola',
    sector: 'Oil & Gas',
    ...overrides,
  };
}

// ── Tests ──────────────────────────────────────────────────────────────────────

describe('ChannelPublisher — scoreSignal', () => {
  before(() => _resetSchedulerState());
  after(() => _resetSchedulerState());

  it('rejects null/undefined signals', () => {
    assert.equal(scoreSignal(null).pass, false);
    assert.equal(scoreSignal(undefined).pass, false);
    assert.equal(scoreSignal({}).pass, false);
  });

  it('scores a valid signal above threshold', () => {
    const result = scoreSignal(makeSignal());
    assert.ok(result.pass, `Expected pass, got score=${result.score} reason=${result.reason}`);
    assert.ok(result.score >= 0.5);
  });

  it('boosts critical severity signals', () => {
    const normal = scoreSignal(makeSignal({ severity: 'low' }));
    const critical = scoreSignal(makeSignal({ severity: 'critical' }));
    assert.ok(critical.score > normal.score, `Critical (${critical.score}) should score higher than low (${normal.score})`);
  });

  it('boosts signals with relevance keywords', () => {
    const generic = scoreSignal(makeSignal({ title: 'Generic update', summary: 'Nothing specific here' }));
    const relevant = scoreSignal(makeSignal({ title: 'Sanctions update', summary: 'New sanctions on Angolan oil and gas procurement' }));
    assert.ok(relevant.score >= generic.score, `Relevant (${relevant.score}) should score >= generic (${generic.score})`);
  });

  it('deduplicates identical signals via curateSignals', () => {
    _resetSchedulerState();
    const signal = makeSignal({ title: 'Dedup Test Signal', summary: 'Unique dedup test signal for channel' });
    const curated = curateSignals([signal, signal]);
    assert.equal(curated.length, 1, 'Should dedup to 1 signal');
  });

  it('respects custom minScore', () => {
    const result = scoreSignal(makeSignal({ severity: 'low', title: 'x', summary: 'y' }), { minScore: 0.9 });
    // Low-severity generic signal won't hit 0.9
    assert.equal(result.pass, false);
  });
});

describe('ChannelPublisher — formatChannelPost', () => {
  it('formats a valid signal into a markdown post', () => {
    const post = formatChannelPost(makeSignal());
    assert.ok(typeof post === 'string');
    assert.ok(post.length > 50);
    assert.ok(post.includes('Test Intel Signal'));
    assert.ok(post.includes('reuters')); // source from signal
  });

  it('includes reply keyword hint by default', () => {
    const post = formatChannelPost(makeSignal());
    assert.ok(post.includes('Reply with'));
    assert.ok(post.includes('test_intel'));
  });

  it('omits reply hint when requested', () => {
    const post = formatChannelPost(makeSignal(), { includeReplyHint: false });
    assert.ok(!post.includes('Reply with'));
  });

  it('detects content type from signal text', () => {
    const sanctions = formatChannelPost(makeSignal({ title: 'OFAC Sanctions Update', summary: 'New sanctions on entities' }));
    assert.ok(sanctions.includes('Sanctions Update'));

    const procurement = formatChannelPost(makeSignal({ title: 'Tender Alert', summary: 'New procurement tender released' }));
    assert.ok(procurement.includes('Procurement Signal'));
  });

  it('truncates very long posts', () => {
    const long = formatChannelPost(makeSignal({ summary: 'x'.repeat(5000) }));
    assert.ok(long.length <= 4000);
  });
});

describe('ChannelPublisher — formatDailyBrief', () => {
  it('formats a daily brief with sections', () => {
    const brief = formatDailyBrief({
      sections: [
        { title: 'Sanctions', text: 'New sanctions on Angola' },
        { title: 'Opportunities', text: 'Tender in Mozambique' },
      ],
    });
    assert.ok(brief.includes('ARKMURUS DAILY BRIEF'));
    assert.ok(brief.includes('Sanctions'));
    assert.ok(brief.includes('Opportunities'));
    assert.ok(brief.includes('deepdive'));
  });

  it('handles empty brief data', () => {
    const brief = formatDailyBrief({});
    assert.ok(brief.includes('ARKMURUS DAILY BRIEF'));
  });
});

describe('ChannelPublisher — rate limiting', () => {
  before(() => _resetSchedulerState());
  after(() => _resetSchedulerState());

  it('allows posting initially', () => {
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, true);
    assert.equal(reason, 'ok');
  });

  it('blocks after daily limit', () => {
    _resetSchedulerState();
    // Record max daily posts
    for (let i = 0; i < 6; i++) recordPost();
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, false);
    assert.ok(reason.includes('daily limit'));
  });

  it('respects cooldown', () => {
    _resetSchedulerState();
    recordPost(); // sets lastPostAt to now
    const { canPost, reason } = canPostNow();
    assert.equal(canPost, false);
    assert.ok(reason.includes('cooldown'));
  });
});

describe('ChannelPublisher — curateSignals', () => {
  before(() => _resetSchedulerState());
  after(() => _resetSchedulerState());

  it('returns empty for empty input', () => {
    assert.deepEqual(curateSignals([]), []);
    assert.deepEqual(curateSignals(null), []);
    assert.deepEqual(curateSignals(undefined), []);
  });

  it('selects highest-scored signals', () => {
    const signals = [
      makeSignal({ title: 'Low priority', severity: 'low', summary: 'Minor update' }),
      makeSignal({ title: 'Critical alert', severity: 'critical', summary: 'Major sanctions development in Angola oil sector' }),
      makeSignal({ title: 'Medium update', severity: 'medium', summary: 'Procurement tender in Mozambique' }),
    ];
    const curated = curateSignals(signals, { maxPosts: 2 });
    assert.ok(curated.length <= 2);
    // Critical alert should be selected
    const titles = curated.map(s => s.title);
    assert.ok(titles.includes('Critical alert'), `Expected Critical alert in [${titles.join(', ')}]`);
  });

  it('respects maxPosts limit', () => {
    _resetSchedulerState();
    const signals = Array.from({ length: 10 }, (_, i) =>
      makeSignal({ title: `Signal ${i}`, severity: 'high', summary: `Important signal number ${i} about sanctions in Africa` })
    );
    const curated = curateSignals(signals, { maxPosts: 3 });
    assert.ok(curated.length <= 3);
  });
});

describe('ChannelPublisher — scheduler state', () => {
  before(() => _resetSchedulerState());
  after(() => _resetSchedulerState());

  it('returns state object with expected keys', () => {
    const state = getSchedulerState();
    assert.ok(typeof state.lastPostAt === 'number');
    assert.ok(typeof state.dailyPostCount === 'number');
    assert.ok(typeof state.dailyPostDate === 'string');
    assert.ok(typeof state.postedHashes === 'number');
  });

  it('tracks posted hashes after curation', () => {
    _resetSchedulerState();
    const before = getSchedulerState().postedHashes;
    curateSignals([makeSignal()]);
    const after = getSchedulerState().postedHashes;
    assert.ok(after > before);
  });
});
