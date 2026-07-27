import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const page = readFileSync('public/aria.html', 'utf8');

// Only the rendered welcome paragraph — never the surrounding comment, so this
// guard cannot pass by matching its own explanation.
const welcome = (page.match(/<p>I'm ARIA[^<]*<\/p>/) || [''])[0];

test('the chat opens with a welcome line at all', () => {
  assert.ok(welcome, 'the ARIA welcome paragraph is missing from aria.html');
});

test('the opening line does not fence the product to one sector', () => {
  // R-F3257 — a user here to screen a supplier, vet a hire or ask an ordinary
  // question should not be told on arrival which industry the tool is for.
  assert.doesNotMatch(welcome, /defence|defense/i,
    'the welcome line names a single sector again — it reads as a door to '
    + 'anyone whose question is not about that sector');
});

test('it still says what ARIA actually does', () => {
  // Welcoming must not mean vague. The line has to name real, shipped work,
  // or it tells a new user nothing about why they are here.
  assert.match(welcome, /companies|people|sanctions|risk/i,
    'the welcome line no longer names any concrete capability');
});

test('it invites the questions it does not enumerate', () => {
  assert.match(welcome, /whatever else|anything else|something else/i,
    'the line lists capabilities without opening the floor, which is what '
    + 'made the previous copy read as a boundary');
});
