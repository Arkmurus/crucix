/**
 * R-F3301..R-F3304 — the landing template must actually work, not merely exist.
 *
 * The defect that motivated this: the access-request form is
 * `class="subscribe-form lead-form"`, but NOTHING in the stylesheet defines
 * `.subscribe-form`, and every Pelican input/button rule is scoped under a
 * `.form` ancestor the element does not have. The form therefore rendered as
 * browser-default inputs (21px tall, no font, no padding) beside a grey OS
 * button, in the hero, on the public landing page. A test that grepped for the
 * class names would have passed the whole time.
 *
 * So the assertions below check PROPERTIES, not wording:
 *   * the declarations that actually reach each field, resolved through the real
 *     ancestor chain, rather than "a rule mentioning .lead-form exists"
 *   * every in-page anchor a link promises resolves to a real id
 *   * the model card's footer is the last thing in its content shell
 *   * the legal pages render on a light canvas
 *
 * The matcher itself is verified against the pre-fix CSS at the bottom of this
 * file. A style guard that cannot report an unstyled control is worse than no
 * guard, because it certifies a page nobody looked at.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const CSS = readFileSync(join('public', 'pelican', 'assets', 'css', 'style.css'), 'utf8');
const INDEX = readFileSync(join('public', 'index.html'), 'utf8');
const MODEL_CARD = readFileSync(join('public', 'model-card.html'), 'utf8');
const PRIVACY = readFileSync(join('public', 'about', 'privacy.html'), 'utf8');
const TERMS = readFileSync(join('public', 'about', 'terms.html'), 'utf8');

// ── a very small CSS matcher ────────────────────────────────────────────────
// Deliberately narrow: this stylesheet uses only descendant combinators and
// simple selectors, so that is all it handles. Anything it cannot parse is
// dropped rather than guessed at, which can only make the guard stricter.

/** Strip comments and every @media block, leaving the unconditional cascade. */
function baseRules(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf('@media', i);
    const brace = text.indexOf('{', i);
    if (brace === -1) break;
    if (at !== -1 && at < brace) {
      // skip the whole at-rule, matching braces
      let depth = 0;
      let j = text.indexOf('{', at);
      if (j === -1) break;
      for (; j < text.length; j += 1) {
        if (text[j] === '{') depth += 1;
        else if (text[j] === '}') { depth -= 1; if (depth === 0) { j += 1; break; } }
      }
      i = j;
      continue;
    }
    const close = text.indexOf('}', brace);
    if (close === -1) break;
    out.push({
      selectors: text.slice(i, brace).split(',').map((s) => s.trim()).filter(Boolean),
      body: text.slice(brace + 1, close),
    });
    i = close + 1;
  }
  return out;
}

/** Does one simple selector (`div.a#b`) describe this element? */
function matchesSimple(part, el) {
  if (part.includes(':') || part.includes('[') || part.includes('>')) return null; // unsupported → caller drops the rule
  const tag = (part.match(/^[a-zA-Z][\w-]*/) || [''])[0];
  if (tag && tag !== el.tag) return false;
  for (const cls of part.match(/\.[\w-]+/g) || []) {
    if (!el.classes.includes(cls.slice(1))) return false;
  }
  for (const id of part.match(/#[\w-]+/g) || []) {
    if (el.id !== id.slice(1)) return false;
  }
  return true;
}

/** el = the element; ancestors = outermost-first. */
function declarationsFor(rules, el, ancestors) {
  const props = new Map();
  for (const rule of rules) {
    let hit = false;
    for (const selector of rule.selectors) {
      const parts = selector.split(/\s+/).filter(Boolean);
      const own = matchesSimple(parts[parts.length - 1], el);
      if (own !== true) continue;
      // walk the ancestor requirements right-to-left
      let cursor = ancestors.length - 1;
      let ok = true;
      for (let p = parts.length - 2; p >= 0; p -= 1) {
        let found = false;
        while (cursor >= 0) {
          const m = matchesSimple(parts[p], ancestors[cursor]);
          cursor -= 1;
          if (m === true) { found = true; break; }
        }
        if (!found) { ok = false; break; }
      }
      if (ok) { hit = true; break; }
    }
    if (!hit) continue;
    for (const decl of rule.body.split(';')) {
      const idx = decl.indexOf(':');
      if (idx < 1) continue;
      props.set(decl.slice(0, idx).trim().toLowerCase(), decl.slice(idx + 1).trim());
    }
  }
  return props;
}

// The real chain the form sits in, read off public/index.html.
const FORM_ANCESTORS = [
  { tag: 'body', classes: [], id: null },
  { tag: 'div', classes: ['wrapper'], id: null },
  { tag: 'div', classes: ['main'], id: 'main' },
  { tag: 'div', classes: ['hero'], id: null },
  { tag: 'div', classes: ['container'], id: null },
  { tag: 'div', classes: ['row', 'align-center'], id: null },
  { tag: 'div', classes: ['col-md-12', 'col-lg-5'], id: null },
  { tag: 'div', classes: ['hero-content'], id: null },
  { tag: 'form', classes: ['subscribe-form', 'lead-form'], id: 'lead-form' },
];
const EMAIL_FIELD = { tag: 'input', classes: ['mail'], id: 'lead-email' };
const SUBMIT = { tag: 'button', classes: ['submit-button'], id: null };

test('R-F3305 the stylesheet closes every block it opens', () => {
  // The vendored sheet left `@media (min-width: 240px)` open at line 243, so
  // every rule after it was the at-rule's body. It rendered fine (240px is
  // below any real viewport), which is exactly why it survived: the only
  // symptom was that a parser walking the sheet found almost no top-level
  // rules. The two tests below depend on that, so state it separately.
  const text = CSS.replace(/\/\*[\s\S]*?\*\//g, '');
  let depth = 0;
  let underflow = false;
  for (const ch of text) {
    if (ch === '{') depth += 1;
    else if (ch === '}') { depth -= 1; if (depth < 0) underflow = true; }
  }
  assert.equal(depth, 0, `style.css ends inside ${depth} unclosed block(s)`);
  assert.equal(underflow, false, 'style.css closes a block it never opened');
});

test('R-F3301 the access-request fields are actually styled through their real ancestor chain', () => {
  const rules = baseRules(CSS);
  const field = declarationsFor(rules, EMAIL_FIELD, FORM_ANCESTORS);

  // Height is the tell. An unstyled text input is ~21px and looks broken next
  // to a 52px hero headline; the template's own control height is 45-52px.
  const height = parseInt(field.get('height') || '0', 10);
  assert.ok(height >= 40, `the email field inherits height="${field.get('height')}" — it is unstyled`);
  assert.ok(field.get('font-family'), 'the email field has no font-family, so it renders in the browser default');
  assert.ok(field.get('padding'), 'the email field has no padding');
  assert.ok(field.get('color'), 'the email field has no text colour');
});

test('R-F3301 the submit button is a branded control, not an OS default', () => {
  const rules = baseRules(CSS);
  const button = declarationsFor(rules, SUBMIT, FORM_ANCESTORS);

  const background = (button.get('background') || button.get('background-color') || '').toLowerCase();
  assert.ok(background, 'the submit button has no background, so it renders as the grey OS button');
  assert.ok(/#4285f4|rgb/.test(background), `the submit button background is "${background}", not the page accent`);
  assert.ok(parseInt(button.get('height') || '0', 10) >= 40, 'the submit button has no height');
  assert.equal((button.get('color') || '').toLowerCase(), '#ffffff', 'the submit button label needs a colour set against its fill');
  assert.equal(button.get('cursor'), 'pointer', 'the submit button should read as clickable');
});

test('R-F3301 the form still posts to the real endpoint it is styled for', () => {
  // Styling must not have moved the fields the capture path depends on.
  assert.match(INDEX, /action="\/api\/leads"/);
  assert.match(INDEX, /id="lead-name"[^>]*name="name"/);
  assert.match(INDEX, /id="lead-email"[^>]*name="email"/);
  assert.match(INDEX, /id="lead-response"/);
  // Labels stay present for screen readers even though they are visually hidden.
  assert.match(INDEX, /<label class="sr-only" for="lead-name">/);
  assert.match(INDEX, /<label class="sr-only" for="lead-email">/);
});

test('R-F3302 the unsupported free-account note is gone, not merely reworded', () => {
  assert.doesNotMatch(INDEX, /No credit card required/i);
  assert.doesNotMatch(INDEX, /class="form-note"/,
    'the note container is still in the markup, so it can be refilled by accident');
});

test('R-F3303 every anchor the landing links to resolves to a real id', () => {
  const targets = [...INDEX.matchAll(/href="([^"]*#[\w-]+)"/g)].map((m) => m[1]);
  assert.ok(targets.length >= 6, 'the landing should still be an anchored page');
  const pages = { '/model-card.html': MODEL_CARD, '': INDEX };
  for (const target of new Set(targets)) {
    const [path, anchor] = target.split('#');
    const page = pages[path];
    assert.ok(page !== undefined, `unchecked link target: ${target}`);
    assert.ok(
      new RegExp(`id="${anchor}"`).test(page),
      `${target} points at a section that does not exist`,
    );
  }
});

test('R-F3303 the model card footer closes the document instead of orphaning a section', () => {
  const footer = MODEL_CARD.indexOf('<div class="mc-footer">');
  assert.ok(footer > 0, 'the model card must keep its footer');
  const after = MODEL_CARD.slice(footer);
  assert.doesNotMatch(after, /<h2 class="mc-h2"/,
    'a numbered section renders after the footer, and outside the .mc-shell content box');
  // and the shell must still be closed
  assert.match(after, /<\/div>/);
});

test('R-F3304 the legal pages render on a light canvas, matching the landing they are linked from', () => {
  for (const [name, raw] of [['privacy', PRIVACY], ['terms', TERMS]]) {
    // Strip comments first: a hex quoted in a note about the OLD theme is not a
    // colour anyone sees, and counting it would make the guard report its own
    // documentation as the defect.
    const page = raw.replace(/\/\*[\s\S]*?\*\//g, '');
    const body = page.match(/\n\s*body\s*\{([^}]*)\}/);
    assert.ok(body, `${name}: no body rule found`);
    const background = (body[1].match(/background:\s*([^;]+)/) || [])[1];
    assert.ok(background, `${name}: body sets no background`);
    assert.match(background.trim(), /^(#fff(fff)?|white)$/i,
      `${name}: body background is "${background.trim()}", not white`);
    assert.doesNotMatch(page, /#0c0919/, `${name}: the retired dark canvas colour is still present`);
    assert.doesNotMatch(page, /color:\s*rgba\(255,\s*255,\s*255/,
      `${name}: light-on-dark text colours survive and will be invisible on white`);
  }
});

test('R-F3301 the style matcher can report an unstyled control', () => {
  // Verify the instrument against the CSS as it shipped in R-F3297. If this
  // does not fail, the two tests above prove nothing.
  const before = `
.lead-form {
	display: grid;
	grid-template-columns: minmax(110px, 0.72fr) minmax(190px, 1.28fr) auto;
	max-width: 650px;
}
.lead-form input,
.lead-form button { min-width: 0; }
.lead-form input:first-of-type { border-radius: 5px 0 0 5px; border-right: 1px solid #e6e9ee; }
.lead-form .mail { border-radius: 0; }
.lead-form .submit-button { border: 0; white-space: nowrap; cursor: pointer; }
.form input { height: 45px; background-color: #F3F3F3; font-family: 'Montserrat'; }
.form .submit-button { height: 45px; background: #4285f4; color: #FFFFFF; }
`;
  const rules = baseRules(before);
  const field = declarationsFor(rules, EMAIL_FIELD, FORM_ANCESTORS);
  const button = declarationsFor(rules, SUBMIT, FORM_ANCESTORS);
  assert.equal(field.get('height'), undefined,
    'the matcher applied .form input to a form that has no .form ancestor');
  assert.equal(button.get('background'), undefined,
    'the matcher applied .form .submit-button to a form that has no .form ancestor');
  // and it must still see the rules that DO match
  assert.equal(button.get('cursor'), 'pointer');
  assert.equal(field.get('min-width'), '0');
});
