// test/shared-js-executes-rf3872.test.mjs
//
// R-F3872 — EXECUTE the shared front-end modules. Every other guard in this
// series reads source; none of them runs it.
//
// ── THE OUTAGE THIS EXISTS FOR ───────────────────────────────────────────────
// R-F3866 added escaping to the sidebar nav renderer. `escapeText` was declared
// INSIDE `Sidebar.init()`, so it was invisible to `Sidebar.html()`, and every
// page threw during startup:
//
//   ReferenceError: escapeText is not defined
//       at link (js/sidebar.js:299)
//       at Object.html (js/sidebar.js:319)
//       at Object.init (js/sidebar.js:12)
//
// The dashboard rendered blank. Nothing caught it:
//   * the fixer REFUSED to touch a page whose escaper it could not find — but it
//     asked "is this name defined anywhere in the FILE", and file-level presence
//     is not lexical SCOPE;
//   * every guard is static, and a ReferenceError does not exist until the code
//     runs;
//   * `node --check` parses, and a scope error parses fine;
//   * the full Node suite stayed green at 1818/8, because nothing executed these
//     modules;
//   * the server returned HTTP 200 with the correct byte count.
//
// Green tests and a 200 both read as health. Only the browser console had it.
//
// These tests are deliberately shallow: they do not assert markup, they assert
// that the module body and its top-level renderers RUN. That is the class of
// defect the static guards structurally cannot see.
//
// Run: node --test test/shared-js-executes-rf3872.test.mjs

import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { describe, it } from 'node:test';

function repoRoot() {
  return path.resolve(
    path.dirname(new URL(import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1'),
    '..',
  );
}
const read = (f) => fs.readFileSync(path.join(repoRoot(), 'public', f), 'utf8');

/** A DOM/browser stub broad enough to let a page module initialise. */
function browserStub() {
  const el = () => ({
    style: {},
    dataset: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    setAttribute() {}, getAttribute: () => null, removeAttribute() {},
    appendChild() {}, append() {}, prepend() {}, remove() {},
    addEventListener() {}, removeEventListener() {},
    querySelector: () => null, querySelectorAll: () => [],
    closest: () => null, focus() {}, click() {},
    innerHTML: '', textContent: '', value: '',
  });
  const document = {
    addEventListener() {}, removeEventListener() {},
    getElementById: () => el(), querySelector: () => el(), querySelectorAll: () => [],
    createElement: () => el(), createTextNode: () => el(),
    body: el(), head: el(), documentElement: el(), readyState: 'complete',
    cookie: '',
  };
  const location = { pathname: '/dashboard.html', href: '', search: '', hash: '', origin: 'https://x' };
  const storage = { getItem: () => null, setItem() {}, removeItem() {}, clear() {} };
  const win = {
    document, location, localStorage: storage, sessionStorage: storage,
    addEventListener() {}, removeEventListener() {},
    matchMedia: () => ({ matches: false, addEventListener() {}, addListener() {} }),
    navigator: { userAgent: 'node', clipboard: { writeText: async () => {} } },
    setTimeout() {}, setInterval() {}, clearInterval() {}, clearTimeout() {},
    fetch: async () => ({ ok: false, status: 401, json: async () => ({}), text: async () => '' }),
    console,
  };
  win.window = win;
  win.self = win;
  return {
    window: win, document, location, localStorage: storage, sessionStorage: storage,
    navigator: win.navigator, fetch: win.fetch, console,
    setTimeout() {}, setInterval() {}, clearInterval() {}, clearTimeout() {},
    matchMedia: win.matchMedia, alert() {}, confirm: () => true,
    requestAnimationFrame() {}, IntersectionObserver: class { observe() {} disconnect() {} },
    MutationObserver: class { observe() {} disconnect() {} },
    io: () => ({ on() {}, emit() {}, disconnect() {} }),
  };
}

/** Run `src` with the stub in scope; `tail` may return a value to inspect. */
function runModule(src, tail = '') {
  const stub = browserStub();
  // eslint-disable-next-line no-new-func
  const fn = new Function(...Object.keys(stub), `${src}\n${tail}`);
  return fn(...Object.values(stub));
}

describe('R-F3872 the shared modules execute, not merely parse', () => {
  for (const file of ['js/app.js', 'js/sidebar.js']) {
    it(`${file} module body runs without throwing`, () => {
      assert.doesNotThrow(() => runModule(read(file)),
        `${file} throws at load — every page that includes it breaks on startup`);
    });
  }
});

describe('R-F3872 the sidebar renderer runs — the R-F3871 outage', () => {
  const SRC = () => read('js/sidebar.js');

  it('Sidebar.html() executes and emits the nav', () => {
    // This is the exact call that threw. It parses either way; only running it
    // distinguishes a correct scope from a broken one.
    const html = runModule(SRC(), 'return Sidebar.html("brief");');
    assert.equal(typeof html, 'string');
    assert.ok(html.includes('rail-link'), 'the nav rail must render');
    assert.ok(html.length > 1000, `nav suspiciously short (${html.length} chars)`);
  });

  it('every escaper the sidebar CALLS is reachable from where it is called', () => {
    // The precise defect: `function escapeText` sat inside Sidebar.init(), so
    // Sidebar.html() could not see it. Resolving the name from the renderer's own
    // scope is the only check that catches that.
    const names = runModule(SRC(), `
      const called = [...arguments.callee.toString().matchAll(/x/g)];
      return [
        typeof escapeText,
        typeof Sidebar,
      ];`.replace('arguments.callee.toString()', '""'));
    assert.equal(names[0], 'function', 'escapeText must be reachable at module scope');
    assert.equal(names[1], 'object');
  });

  it('renders for every page key the nav is asked for', () => {
    for (const page of ['brief', 'aria', 'dd-reports', 'vetting', 'watchlist', 'unknown-page']) {
      assert.doesNotThrow(() => runModule(SRC(), `return Sidebar.html(${JSON.stringify(page)});`),
        `Sidebar.html(${page}) throws`);
    }
  });
});

describe('R-F3872 app.js exports the helpers other pages depend on', () => {
  it('escHtml and safeHref are callable after the module runs', () => {
    const out = runModule(read('js/app.js'),
      'return [typeof escHtml, typeof safeHref, escHtml(`<b>&"\'`), safeHref("javascript:alert(1)")];');
    assert.equal(out[0], 'function', 'escHtml is the GLOBAL escaper most pages reach for');
    assert.equal(out[1], 'function');
    assert.equal(out[2], '&lt;b&gt;&amp;&quot;&#39;', 'escHtml must escape the full set at runtime');
    assert.equal(out[3], '#', 'safeHref must fail closed on a javascript: URL');
  });

  it('Toast.show builds a toast without innerHTML and without throwing', () => {
    // R-F3852 rewrote this from an innerHTML template to DOM nodes; if that
    // rewrite had a scope or API error it would break every page that toasts.
    assert.doesNotThrow(() => runModule(read('js/app.js'),
      'Toast.show("hello <script>", "error");'));
  });
});
