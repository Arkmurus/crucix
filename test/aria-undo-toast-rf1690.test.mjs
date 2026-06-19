// test/aria-undo-toast-rf1690.test.mjs
// R-F1690 — verifies the SHIPPED showUndoToast() from public/aria.html so the
// delete-with-undo contract can't silently regress: timeout/dismiss => commit
// (the real delete), Undo => cancel the delete. Runs the real source against a
// minimal DOM stub + a controllable timer (no jsdom dependency).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const html = readFileSync(new URL('../public/aria.html', import.meta.url), 'utf8');
const start = html.indexOf('function showUndoToast(');
assert.ok(start !== -1, 'showUndoToast must exist in aria.html');
const end = html.indexOf('// ── Conversations panel', start);
const fnSrc = html.slice(start, end);

function makeEl() {
  const el = {
    className: '', _text: '', attrs: {}, _listeners: {}, children: [], parentNode: null,
    setAttribute(k, v) { this.attrs[k] = v; },
    appendChild(c) { this.children.push(c); c.parentNode = this; return c; },
    removeChild(c) { this.children = this.children.filter(x => x !== c); c.parentNode = null; },
    addEventListener(ev, fn) { this._listeners[ev] = fn; },
    fire(ev) { if (this._listeners[ev]) this._listeners[ev](); },
  };
  Object.defineProperty(el, 'textContent', { get() { return this._text; }, set(v) { this._text = v; } });
  return el;
}

function run() {
  const created = [];
  const body = makeEl();
  const document = {
    getElementById: () => null,
    createElement: () => { const e = makeEl(); created.push(e); return e; },
    body,
  };
  const timers = [];
  const setT = (fn) => { timers.push(fn); return timers.length; };
  const clearT = (id) => { if (id) timers[id - 1] = null; };
  const factory = new Function('document', 'setTimeout', 'clearTimeout', 'console',
    fnSrc + '\nreturn showUndoToast;');
  const showUndoToast = factory(document, setT, clearT, console);

  let committed = 0, undone = 0;
  showUndoToast('Conversation deleted', () => undone++, () => committed++, 6000);
  const undoBtn = created.find(e => e.className.includes('undo-toast-action'));
  const closeBtn = created.find(e => e.attrs['aria-label'] === 'Dismiss');
  const fireTimer = () => { const f = timers.find(Boolean); if (f) f(); };
  return { committed: () => committed, undone: () => undone, undoBtn, closeBtn, fireTimer };
}

test('R-F1690: timeout commits the delete (no undo)', () => {
  const h = run();
  h.fireTimer();
  assert.equal(h.committed(), 1, 'timeout must commit');
  assert.equal(h.undone(), 0);
});

test('R-F1690: Undo cancels the delete and blocks a later commit', () => {
  const h = run();
  h.undoBtn.fire('click');
  assert.equal(h.undone(), 1, 'Undo must restore');
  assert.equal(h.committed(), 0);
  h.fireTimer();                         // timer fires afterwards — must be a no-op
  assert.equal(h.committed(), 0, 'commit must not run after Undo');
});

test('R-F1690: dismiss (✕) accepts the deletion (commits now)', () => {
  const h = run();
  h.closeBtn.fire('click');
  assert.equal(h.committed(), 1, 'dismiss = accept deletion');
  assert.equal(h.undone(), 0);
});

test('R-F1690: the Undo button and dismiss control both exist', () => {
  const h = run();
  assert.ok(h.undoBtn, 'Undo button present');
  assert.ok(h.closeBtn, 'Dismiss control present');
});
