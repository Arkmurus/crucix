/**
 * A very small CSS matcher, shared by the landing/auth style guards.
 *
 * Why this exists: grepping a stylesheet for a class name proves the text is
 * present, not that any declaration reaches the element. R-F3301 shipped a form
 * whose every class was spelled correctly and which inherited nothing, because
 * the rules that styled it were scoped under an ancestor the element did not
 * have. These helpers resolve the declarations that ACTUALLY apply, through the
 * real ancestor chain, so a guard can assert a rendered property.
 *
 * Deliberately narrow: the stylesheets it reads use only descendant combinators
 * and simple selectors, so that is all it handles. Anything it cannot parse is
 * dropped rather than guessed at, which can only make a guard stricter.
 *
 * Verified in test/landing-ux-polish-rf3301-3304.test.mjs, which replays a
 * known-broken stylesheet through it and asserts it reports the control as
 * unstyled. Do not change these without re-running that check.
 */

/** Strip comments and every @media block, leaving the unconditional cascade. */
export function baseRules(css) {
  const text = css.replace(/\/\*[\s\S]*?\*\//g, '');
  const out = [];
  let i = 0;
  while (i < text.length) {
    const at = text.indexOf('@media', i);
    const brace = text.indexOf('{', i);
    if (brace === -1) break;
    if (at !== -1 && at < brace) {
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

/** Does one simple selector (`div.a#b`) describe this element? null = unsupported. */
export function matchesSimple(part, el) {
  if (part.includes(':') || part.includes('[') || part.includes('>')) return null;
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

/** el = the element; ancestors = outermost-first. Returns Map<prop, value>. */
export function declarationsFor(rules, el, ancestors) {
  const props = new Map();
  for (const rule of rules) {
    let hit = false;
    for (const selector of rule.selectors) {
      const parts = selector.split(/\s+/).filter(Boolean);
      if (matchesSimple(parts[parts.length - 1], el) !== true) continue;
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
