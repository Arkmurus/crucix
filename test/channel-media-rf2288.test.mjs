// test/channel-media-rf2288.test.mjs
// Capability test for Telegram Channel Media Engine (R-F2288)

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';

const mod = await import('../lib/telegram/channelMedia.mjs');
const {
  buildIntelCardData,
  generateInfographicCard,
  generateComparisonCard,
  generateTimelineCard,
  buildThread,
  buildPoll,
  buildInlineKeyboard,
} = mod;

describe('ChannelMedia — generateInfographicCard', () => {
  it('generates SVG with title', () => {
    const svg = generateInfographicCard({ title: 'Test Card' });
    assert.ok(svg.includes('<svg'));
    assert.ok(svg.includes('Test Card'));
    assert.ok(svg.includes('ARIA Intelligence'));
  });

  it('includes metrics when provided', () => {
    const svg = generateInfographicCard({
      title: 'Metrics Test',
      metrics: [{ label: 'Confidence', value: '85%' }, { label: 'Entities', value: '12' }],
    });
    assert.ok(svg.includes('85%'));
    assert.ok(svg.includes('Confidence'));
    assert.ok(svg.includes('12'));
  });

  it('includes bullet points when provided', () => {
    const svg = generateInfographicCard({
      title: 'Bullets Test',
      bullets: ['Key development in Angola', 'Sanctions impact assessment'],
    });
    assert.ok(svg.includes('Key development in Angola'));
    assert.ok(svg.includes('Sanctions impact assessment'));
  });

  it('uses correct colour scheme for content type', () => {
    const flash = generateInfographicCard({ title: 'Flash', type: 'flash' });
    assert.ok(flash.includes('FLASH ALERT'));

    const sanctions = generateInfographicCard({ title: 'Sanctions', type: 'sanctions' });
    assert.ok(sanctions.includes('SANCTIONS'));
  });

  it('renders editorial intelligence panels for channel cards', () => {
    const svg = generateInfographicCard({
      title: 'Procurement signal: avionics tender',
      subtitle: 'Verified tender with dual-use relevance and an unusually short bid window.',
      bullets: ['Dual-use avionics route needs screening', 'Check buyer, consignee and freight forwarder'],
      type: 'procurement',
    });
    assert.ok(svg.includes('WHY IT MATTERS'));
    // R-F2903 — panel renamed 'NEXT CHECK' -> 'RECOMMENDED ACTION'. The field it
    // renders IS the signal's recommended_action; "next check" understated a
    // decision-grade instruction and read as a suggestion to look again later.
    assert.ok(svg.includes('RECOMMENDED ACTION'));
    // R-F2903 — the why-panel now renders the SUBTITLE (the actual why_it_matters).
    // It previously rendered bullets[0], which the channel caller sets to the ACTION,
    // so both panels printed the same sentence.
    assert.ok(svg.includes('dual-use relevance'), 'why panel must show the why, not the action');
    assert.ok(svg.includes('Dual-use avionics route needs screening'));
  });

  it('normalizes raw signal data into reusable card fields', () => {
    const card = buildIntelCardData({
      title: 'OFAC exposure: Example Trading',
      text: 'Example Trading surfaced in a sanctions update. Screen the address cluster before quoting.',
      confidence: 0.87,
      source: 'OFAC',
    });
    assert.equal(card.type, 'sanctions');
    assert.equal(card.metrics[0].label, 'Confidence');
    assert.equal(card.metrics[0].value, '87%');
    assert.ok(card.bullets[0].includes('Example Trading surfaced'));
  });
});

describe('ChannelMedia — generateComparisonCard', () => {
  it('generates side-by-side comparison SVG', () => {
    const svg = generateComparisonCard({
      title: 'Before vs After',
      left: { label: 'Before', value: '$1.2M', colour: '#ef4444' },
      right: { label: 'After', value: '$3.8M', colour: '#10b981' },
    });
    assert.ok(svg.includes('Before vs After'));
    assert.ok(svg.includes('$1.2M'));
    assert.ok(svg.includes('$3.8M'));
    assert.ok(svg.includes('VS'));
  });
});

describe('ChannelMedia — generateTimelineCard', () => {
  it('generates timeline SVG with events', () => {
    const svg = generateTimelineCard({
      title: 'Event Timeline',
      events: [
        { date: '2026-01', event: 'Initial sanctions imposed' },
        { date: '2026-03', event: 'Entity designation expanded' },
      ],
    });
    assert.ok(svg.includes('Event Timeline'));
    assert.ok(svg.includes('2026-01'));
    assert.ok(svg.includes('Initial sanctions imposed'));
  });
});

describe('ChannelMedia — buildThread', () => {
  it('splits long content into multiple messages', () => {
    const thread = buildThread({
      title: 'Deep Analysis',
      content: 'A. '.repeat(2000),
      maxLength: 500,
    });
    assert.ok(thread.length > 1);
    assert.ok(thread[0].text.includes('1/'));
  });

  it('returns single message for short content', () => {
    const thread = buildThread({
      title: 'Quick Note',
      content: 'Short analysis.',
    });
    assert.equal(thread.length, 1);
  });

  it('includes navigation footer', () => {
    const thread = buildThread({
      title: 'Multi Part',
      content: 'X. '.repeat(2000),
      maxLength: 500,
    });
    assert.ok(thread[0].text.includes('🧵'));
  });
});

describe('ChannelMedia — buildPoll', () => {
  it('builds a valid poll payload', () => {
    const poll = buildPoll({
      question: 'Which sector is most at risk?',
      options: ['Oil & Gas', 'Mining', 'Defence', 'Finance'],
    });
    assert.equal(poll.question, 'Which sector is most at risk?');
    assert.equal(poll.options.length, 4);
    assert.equal(poll.is_anonymous, true);
    assert.equal(poll.type, 'regular');
  });

  it('builds a quiz with correct answer', () => {
    const quiz = buildPoll({
      question: 'Test quiz',
      options: ['A', 'B', 'C', 'D'],
      isQuiz: true,
      correctOptionId: 2,
      explanation: 'C is correct because...',
    });
    assert.equal(quiz.type, 'quiz');
    assert.equal(quiz.correct_option_id, 2);
    assert.ok(quiz.explanation.includes('C is correct'));
  });

  it('throws for invalid options', () => {
    assert.throws(() => buildPoll({ question: 'Q', options: ['only'] }));
    assert.throws(() => buildPoll({ question: 'Q', options: [] }));
  });
});

describe('ChannelMedia — buildInlineKeyboard', () => {
  it('builds inline keyboard markup', () => {
    const kb = buildInlineKeyboard([
      [{ text: 'Deep Dive', callback_data: 'deepdive' }],
      [{ text: 'View Source', url: 'https://example.com' }],
    ]);
    assert.ok(kb.reply_markup.inline_keyboard);
    assert.equal(kb.reply_markup.inline_keyboard.length, 2);
    assert.equal(kb.reply_markup.inline_keyboard[0][0].text, 'Deep Dive');
    assert.equal(kb.reply_markup.inline_keyboard[1][0].url, 'https://example.com');
  });
});
