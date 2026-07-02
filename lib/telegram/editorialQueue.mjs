// lib/telegram/editorialQueue.mjs
//
// R-F2309 — Channel editorial queue.
// ==================================
// Operator directive: ONE relevant post/day, curated, not repetitive. This queue
// holds hand-authored, deep-research editorial posts (Case Files, DD-method,
// Signals) that the daily channel cron drains ONE per day, in order, BEFORE
// falling back to the live Morning Signal. When the queue is exhausted, the daily
// post reverts to live sanctions/procurement signals.
//
// Durability: the posts themselves live in code (EDITORIAL_POSTS — version
// controlled, survive a volume wipe). Only the "which ids have been posted"
// cursor persists on the Fly volume (/data), so a restart/redeploy never reposts
// or skips. If the volume is unavailable (dev), it falls back to ./data.

import fs from 'node:fs';
import path from 'node:path';

// ── Persistence path (Fly volume /data, else ./data) ────────────────────────────
function _stateDir() {
  const envDir = process.env.CHANNEL_DATA_DIR;
  if (envDir) return envDir;
  try { if (fs.existsSync('/data')) return '/data'; } catch { /* ignore */ }
  return path.join(process.cwd(), 'data');
}
const _statePath = () =>
  process.env.CHANNEL_EDITORIAL_STATE_PATH || path.join(_stateDir(), 'channel_editorial_posted.json');

/** @returns {Set<string>} ids already posted. */
function _loadPosted() {
  try {
    const raw = fs.readFileSync(_statePath(), 'utf8');
    const arr = JSON.parse(raw);
    return new Set(Array.isArray(arr?.posted) ? arr.posted : []);
  } catch { return new Set(); }
}

function _savePosted(set) {
  try {
    const dir = path.dirname(_statePath());
    fs.mkdirSync(dir, { recursive: true });
    fs.writeFileSync(_statePath(), JSON.stringify({ posted: [...set] }, null, 2), 'utf8');
    return true;
  } catch (e) {
    console.warn('[EditorialQueue] could not persist posted-state:', e.message);
    return false;
  }
}

// ── The editorial posts (curated, deep-research, sourced, honest) ────────────────
// Order = publish order. Add more here anytime; they queue automatically.
export const EDITORIAL_POSTS = [
  {
    id: 'ef-2026-07-case-file-beh-joule-pars',
    type: 'case_file',
    text:
`🕵️ *CASE FILE — Hidden in the supply chain*
━━━━━━━━━━━━━━━━━━━━━━━━━━

This year OFAC dismantled a single procurement web: *21 companies + 17 people*, built to feed Iran's missile and military-aircraft programs.

What did they actually buy? Not weapons — *accelerometers, gyroscopes and MEMS chips*. Civilian-looking dual-use parts. One node even sourced a *US-made helicopter*.

🔍 *The DD lesson:* a clean-looking components order is the #1 disguise. The red flag isn't the item — it's the *chain*: an unfamiliar intermediary, a vague end-use, an end-user who can't explain why they need inertial-navigation sensors.

Before you quote a counterparty, ask: _who is the real end-user, and does the end-use make sense for the goods?_

💬 Reply \`SCREEN [company]\` to check any counterparty against OFAC · UK OFSI · EU · UN · OpenSanctions — free.
_Source: US Treasury/OFAC designations._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-dd-method-screen-the-address',
    type: 'dd_method',
    text:
`💡 *DD METHOD — the trick most screens miss*
━━━━━━━━━━━━━━━━━━━━━━━━━━

In 2024 US regulators did something new: they sanctioned *8 Hong Kong addresses* (then more in Türkiye) — not company names. Why? *Shells change names overnight, but they reuse the same registered address.*

The scale: a New York Times investigation traced *~\\$4bn* of restricted chips into Russia through *6,000+ companies*, many clustered in a handful of Hong Kong shells.

🔍 *Run this in 5 minutes on any supplier:*
1. *Registered address* — how many other companies share it? (corporate-services clustering = flag)
2. *Incorporation date* — created right after a related entity was designated? (re-birth flag)
3. *Directors/owners* — overlap with a restricted party, even a minority stake? (control flag)
4. *Sanctions + watchlist* — name _and_ known aliases.

Name-only screening is a 2015 tool. Address- and ownership-aware screening is the standard now.

💬 Reply \`SCREEN [company]\` — ARIA screens aliases + flags what a single-name check misses.
_Sources: US BIS Entity List actions (2024); NYT investigation._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-signal-318-of-450',
    type: 'signal',
    text:
`📰 *SIGNAL — the number every exporter should sit with*
━━━━━━━━━━━━━━━━━━━━━━━━━━

RUSI stripped down recovered Russian weapons and found *318 of 450 components were made by Western companies* — roughly *18% under export-control regimes*.

None of those manufacturers _sold to Russia_. Their parts got there anyway — through resellers, transshipment hubs and shell layers.

🔍 *The DD lesson:* *end-use ≠ end-user.* A valid End-User Certificate tells you what they _say_; it doesn't tell you where the goods _land_. Verify the chain, not just the paperwork.

💬 New here? Tap *🔍 Consult ARIA* (pinned) or reply \`HELP\` to see what you can check free.
_Source: Royal United Services Institute (RUSI) component analysis._
🤖 *ARIA Intelligence* · imaria.io`,
  },
];

/**
 * The next editorial post that hasn't been published yet (FIFO), or null if the
 * queue is drained.
 * @returns {{id:string,type:string,text:string}|null}
 */
export function peekNextEditorial() {
  const posted = _loadPosted();
  for (const post of EDITORIAL_POSTS) {
    if (!posted.has(post.id)) return post;
  }
  return null;
}

/**
 * Mark an editorial post as published (persists to the volume so it's never
 * reposted across restarts/redeploys).
 * @param {string} id
 * @returns {boolean}
 */
export function markEditorialPosted(id) {
  const posted = _loadPosted();
  posted.add(id);
  return _savePosted(posted);
}

/** @returns {{total:number, posted:number, remaining:number, nextId:string|null}} */
export function editorialStatus() {
  const posted = _loadPosted();
  const next = peekNextEditorial();
  return {
    total: EDITORIAL_POSTS.length,
    posted: EDITORIAL_POSTS.filter(p => posted.has(p.id)).length,
    remaining: EDITORIAL_POSTS.filter(p => !posted.has(p.id)).length,
    nextId: next?.id ?? null,
  };
}
