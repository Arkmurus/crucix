// R-F3518 — the article list and the category breakdown answered different
// questions, and the page presented the disagreement as an empty category.
//
// public/news.html fetched /api/aria/news/recent?limit=100 and filtered THOSE
// hundred in the browser (:177), while "Coverage by Category" beside it rendered
// stats.by_category — computed server-side over the full retained corpus of
// _MAX_ARTICLES (1,000). Both numbers were true of different populations.
//
// So a category whose articles were older than the newest 100 rendered
// "No articles yet. Click Poll Now to fetch the latest news" directly beside a
// bar saying that same category had dozens. The instruction was also false:
// polling adds NEW articles and could never surface the older ones the filter
// was looking for.
//
// R-F3517 moved the filter server-side over the same population the stats
// aggregate. This test drives the REAL page functions in a vm — never source
// text — and asserts the two behaviours that matter: selecting a category
// RE-QUERIES rather than re-filtering the page in memory, and an empty result is
// described honestly instead of as an empty corpus.

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { test } from 'node:test';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const html = readFileSync(join(here, '..', 'public', 'news.html'), 'utf8');

const start = html.indexOf('let allArticles = []');
const end = html.indexOf('async function triggerPoll');
assert.ok(start > 0 && end > start, 'the news page functions must be extractable');
const source = html.slice(start, end);

/**
 * Run the page's own functions against fixture responses.
 * Returns the rendered list markup, the count pill, and every URL requested.
 */
function harness({ stats, recent }) {
  const els = {};
  const requested = [];
  const el = () => ({ innerHTML: '', textContent: '', style: {}, querySelectorAll: () => [] });
  for (const id of ['articles-list', 'article-count', 'category-breakdown',
                    'category-filter', 'kpi-total', 'kpi-total-label', 'kpi-sources',
                    'kpi-categories', 'kpi-last-poll', 'source-count', 'last-updated',
                    'fetch-failure-banner']) els[id] = el();

  const ctx = {
    document: { getElementById: (id) => els[id] || el() },
    newsApi: {
      get: async (url) => {
        requested.push(url);
        if (url.includes('/news/stats')) return stats;
        return recent(url);
      },
    },
    activeCategory: 'all',
    console,
    Number, Object, Math, Date, isNaN, encodeURIComponent, Promise, URL,
    safeHref: (u) => u,        // provided by app.js in the browser
  };
  vm.createContext(ctx);
  // The shim matters. Run against the PRE-FIX page and `loadArticles` does not
  // exist, so without it every test dies on a ReferenceError — proving only that
  // a function is missing, not that the page behaved wrongly. The shim supplies
  // the OLD behaviour (fetch the newest 100 unfiltered, then re-render), so the
  // failures are the real rendered outcome: an empty list beside a breakdown
  // that says otherwise. A guard must fail for the reason it claims.
  vm.runInContext(source + `
    globalThis.__load = loadData;
    globalThis.__loadArticles = (typeof loadArticles === 'function')
      ? loadArticles
      : async () => { renderArticles(allArticles); };
    globalThis.__setCat = (c) => { activeCategory = c; };
  `, ctx);
  return { ctx, els, requested };
}

const STATS = {
  total_sources: 40,
  recent_articles: 1000,
  retention_limit: 1000,
  categories: ['global_defence', 'cyber'],
  // The corpus holds 87 cyber articles — NONE of them in the newest 100.
  by_category: { global_defence: 913, cyber: 87 },
  poll_state: { last_poll_at: '2026-07-30T10:00:00Z', articles_new: 3 },
};

/** The newest 100 are all global_defence; cyber exists only further back. */
function recentFixture(url) {
  const u = new URL(url, 'https://x');
  const cat = u.searchParams.get('category') || '';
  if (cat === 'cyber') {
    return {
      articles: [{ url: 'https://e.com/1', title: 'Cyber incident at a port operator',
                   source: 'Janes', category: 'cyber', detected_at: '2026-07-20T09:00:00Z' }],
      count: 1, category: 'cyber', filtered_server_side: true,
    };
  }
  return {
    articles: Array.from({ length: 100 }, (_, i) => ({
      url: `https://e.com/d${i}`, title: `Defence item ${i}`, source: 'Janes',
      category: 'global_defence', detected_at: '2026-07-30T09:00:00Z',
    })),
    count: 100, category: '', filtered_server_side: false,
  };
}

test('selecting a category RE-QUERIES the server instead of filtering the page', async () => {
  const { ctx, els, requested } = harness({ stats: STATS, recent: recentFixture });
  await ctx.__load();

  ctx.__setCat('cyber');
  await ctx.__loadArticles();

  const asked = requested.filter(u => u.includes('/news/recent'));
  assert.ok(asked.some(u => u.includes('category=cyber')),
    `the category filter never reached the server — requests were:\n${asked.join('\n')}`);
  assert.match(els['articles-list'].innerHTML, /Cyber incident at a port operator/,
    'a cyber article outside the newest 100 was not listed — this is the exact ' +
    'defect: the breakdown claims 87 and the list shows none');
});

test('an empty category does not claim the corpus is empty', async () => {
  const stats = { ...STATS, by_category: { global_defence: 1000, cyber: 0 } };
  const recent = (url) => url.includes('category=cyber')
    ? { articles: [], count: 0, category: 'cyber', filtered_server_side: true }
    : recentFixture(url);

  const { ctx, els } = harness({ stats, recent });
  await ctx.__load();
  ctx.__setCat('cyber');
  await ctx.__loadArticles();

  const out = els['articles-list'].innerHTML;
  assert.doesNotMatch(out, /Poll Now/,
    'the page told the operator to poll for articles that polling cannot ' +
    'surface — polling adds NEW items, not older ones in this category');
  assert.match(out, /No articles in/,
    'an empty CATEGORY must be described as such, not as an empty corpus');
});

test('a server/list disagreement is reported, not rendered as "no articles"', async () => {
  // stats say 87, the list returns none: that is a server-side inconsistency and
  // the page must say so rather than quietly showing an empty state.
  const recent = (url) => url.includes('category=cyber')
    ? { articles: [], count: 0, category: 'cyber', filtered_server_side: true }
    : recentFixture(url);

  const { ctx, els } = harness({ stats: STATS, recent });
  await ctx.__load();
  ctx.__setCat('cyber');
  await ctx.__loadArticles();

  assert.match(els['articles-list'].innerHTML, /inconsistency/i,
    'the page hid a contradiction between the breakdown and the list');
});

test('the count states how much of the category is shown', async () => {
  // 187 in the corpus, 100 on the page: the pill must not imply 100 is the total.
  const stats = { ...STATS, by_category: { global_defence: 813, cyber: 187 } };
  const recent = (url) => url.includes('category=cyber')
    ? { articles: Array.from({ length: 100 }, (_, i) => ({
          url: `https://e.com/c${i}`, title: `Cyber ${i}`, source: 'Janes',
          category: 'cyber', detected_at: '2026-07-20T09:00:00Z' })),
        count: 100, category: 'cyber', filtered_server_side: true }
    : recentFixture(url);

  const { ctx, els } = harness({ stats, recent });
  await ctx.__load();
  ctx.__setCat('cyber');
  await ctx.__loadArticles();

  assert.equal(els['article-count'].textContent, '100 of 187',
    'showing a bare page-limited number invites reading it as the total');
});

test('an aria-intel without R-F3517 degrades to a stated browser filter', async () => {
  // If the flag is absent the page must NOT render an unfiltered list as if it
  // had been filtered — the fallback is used and only matching items show.
  const recent = () => ({
    articles: [
      { url: 'https://e.com/a', title: 'Defence item', source: 'J', category: 'global_defence' },
      { url: 'https://e.com/b', title: 'Cyber item', source: 'J', category: 'cyber' },
    ],
    count: 2,   // no filtered_server_side at all — an older backend
  });

  const { ctx, els } = harness({ stats: STATS, recent });
  await ctx.__load();
  ctx.__setCat('cyber');
  await ctx.__loadArticles();

  const out = els['articles-list'].innerHTML;
  assert.match(out, /Cyber item/);
  assert.doesNotMatch(out, /Defence item/,
    'an unfiltered response was rendered as though the server had filtered it');
});
