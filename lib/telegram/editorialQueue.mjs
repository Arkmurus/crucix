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
  {
    id: 'ef-2026-07-case-file-transshipment-hub',
    type: 'case_file',
    text:
`🕵️ *CASE FILE — The invoice looked ordinary*
━━━━━━━━━━━━━━━━━━━━━━━━━━

A European electronics order routes through a small trading company in the Gulf. The invoice says "industrial controls." The buyer says the goods are for maintenance. Nothing screams defence.

The risk sits one layer deeper: the product family overlaps with export-controlled navigation and guidance systems, the consignee is newly incorporated, and the shipping route adds an unnecessary transshipment stop.

🔍 *The DD lesson:* do not screen only the buyer. Screen the consignee, freight forwarder, address, product family and final-use story. If any part of the chain is vague, pause before shipment.

💬 Reply \`SCREEN [company]\` before you quote a counterparty.
_Sources: BIS export-enforcement advisories; EU dual-use compliance guidance._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-dd-method-director-overlap',
    type: 'dd_method',
    text:
`💡 *DD METHOD — Follow the director, not the logo*
━━━━━━━━━━━━━━━━━━━━━━━━━━

Shell companies are cheap. Directors are harder to rotate cleanly. A supplier can change its name, website and domain in a day; the same directors, phone numbers, accountants and registered agents often remain.

🔍 *Run this before onboarding:*
1. Pull current and historical directors.
2. Search each name with the company address.
3. Look for recently dissolved entities with the same people.
4. Screen directors individually, not only the company.

The best early warning is not a sanctions hit. It is a pattern that explains why a hit may arrive later.

💬 Reply \`SCREEN [company]\` for the first pass, then escalate director overlap into full DD.
_Sources: UK Companies House investigation guidance; FATF beneficial-ownership recommendations._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-signal-red-sea-logistics',
    type: 'signal',
    text:
`🚢 *SIGNAL — Logistics risk is now a compliance signal*
━━━━━━━━━━━━━━━━━━━━━━━━━━

When a shipping lane becomes unstable, counterparties change routes fast. That creates new intermediaries, new ports, new freight agents and new paperwork. Some are legitimate. Some are camouflage.

For defence, dual-use and critical-mineral shipments, a reroute is not just an operational delay. It is a new due-diligence event.

🔍 *What to check:* port of loading, port of discharge, freight forwarder, insurance provider, vessel history and whether the reroute makes commercial sense.

💬 Reply \`SCREEN [company]\` before accepting a new logistics intermediary.
_Sources: IMO maritime-security circulars; OFAC maritime sanctions compliance guidance._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-case-file-end-user-certificate',
    type: 'case_file',
    text:
`📁 *CASE FILE — The certificate was not the control*
━━━━━━━━━━━━━━━━━━━━━━━━━━

An End-User Certificate can be real and still fail as evidence. It proves what was declared. It does not prove the buyer has the capability, budget, facility or operational need to use the goods.

The strongest fraud pattern is a technically valid document attached to an implausible story.

🔍 *The DD lesson:* compare the goods to the buyer's actual operating profile. A small distributor asking for specialist avionics, inertial sensors or secure radios needs a stronger explanation than "government client."

💬 Reply \`SCREEN [company]\` and treat paperwork as the start of DD, not the end.
_Sources: UK Export Control Joint Unit compliance guidance; Wassenaar Arrangement control-list practice._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-dd-method-address-density',
    type: 'dd_method',
    text:
`🏢 *DD METHOD — Count the neighbours*
━━━━━━━━━━━━━━━━━━━━━━━━━━

One company at an address is normal. Hundreds at the same suite can be normal too — if it is clearly a corporate-services provider. The risk starts when the address cluster contains exporters, shell traders and recently renamed entities in sensitive sectors.

🔍 *Five-minute check:*
1. Search the exact address in quotes.
2. Count how many entities share it.
3. Sort by incorporation date.
4. Look for repeated directors or domains.
5. Screen the address cluster, not just the supplier.

💬 Reply \`SCREEN [company]\` for the first entity, then run address-cluster DD before contracting.
_Sources: US Treasury shell-company typologies; FATF misuse-of-legal-persons guidance._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-signal-drone-supply-chain',
    type: 'signal',
    text:
`🛩️ *SIGNAL — Drones made procurement smaller, not safer*
━━━━━━━━━━━━━━━━━━━━━━━━━━

The high-risk item is no longer always a complete aircraft. It may be a camera gimbal, flight controller, radio module, battery pack, propeller batch or navigation board.

That makes counterparty DD harder: small orders can matter, and civilian catalogues can feed military systems.

🔍 *What to ask:* what platform is this for, who integrates it, who operates it, and why does the buyer need this specification rather than a normal commercial alternative?

💬 Reply \`SCREEN [company]\` before supplying UAV-adjacent components.
_Sources: UN Panel of Experts reporting; BIS unmanned-systems export-control advisories._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-case-file-bank-refusal',
    type: 'case_file',
    text:
`🏦 *CASE FILE — The bank saw the risk first*
━━━━━━━━━━━━━━━━━━━━━━━━━━

A payment delay is sometimes just compliance bureaucracy. Sometimes it is the first external signal that your deal has a hidden sanctions, ownership or end-use problem.

If a bank asks for unusually detailed goods descriptions, ownership charts or end-user evidence, do not treat it as admin. Treat it as free risk intelligence.

🔍 *The DD lesson:* bank questions show where your file is weak. Fix the evidence trail before shipment, not after funds freeze.

💬 Reply \`SCREEN [company]\` before resubmitting a questioned transaction.
_Sources: Wolfsberg Group trade-finance principles; FATF trade-based money-laundering typologies._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-dd-method-negative-news',
    type: 'dd_method',
    text:
`📰 *DD METHOD — Search the words people avoid*
━━━━━━━━━━━━━━━━━━━━━━━━━━

Counterparty searches fail when they only use the legal name. Serious adverse media often sits under local-language aliases, former names, director names, vessel names or a project nickname.

🔍 *Search pack:*
1. Legal name + local script variant.
2. Director name + "fraud" / "sanctions" / "procurement".
3. Address + sector.
4. Company former name + country.
5. Key project name + "investigation".

One clean English-language search is not an adverse-media review.

💬 Reply \`SCREEN [company]\` first, then use the alias pack for full DD.
_Sources: OECD due-diligence guidance; FATF adverse-media screening practice._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-signal-critical-minerals',
    type: 'signal',
    text:
`⛏️ *SIGNAL — Critical minerals are now strategic goods*
━━━━━━━━━━━━━━━━━━━━━━━━━━

Lithium, cobalt, graphite, rare earths and titanium are no longer just commodity stories. They sit inside batteries, sensors, aerospace parts, magnets and guided systems.

That changes the DD question. It is not only "who buys the mineral?" It is "what capability does this feed downstream?"

🔍 *Check the chain:* mine operator, trader, processor, logistics route, buyer, and whether the material can support defence or dual-use production.

💬 Reply \`SCREEN [company]\` before entering a critical-mineral supply chain.
_Sources: IEA critical-minerals market review; EU Critical Raw Materials Act materials list._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-case-file-fake-tender',
    type: 'case_file',
    text:
`📋 *CASE FILE — The tender that wanted a fee*
━━━━━━━━━━━━━━━━━━━━━━━━━━

A real-looking defence tender arrives by email. The buyer name is familiar, the logo is official, and the deadline is urgent. Then comes the tell: pay a registration, translation or "bid security" fee to unlock documents.

Legitimate procurement portals may charge formal fees, but unsolicited urgency plus off-portal payment is a red flag.

🔍 *The DD lesson:* verify the tender on the official portal, confirm the procurement reference, and call the listed authority using a number from the official website — not the email.

💬 Reply \`SCREEN [company]\` before responding to a tender intermediary.
_Sources: World Bank procurement fraud guidance; national procurement-portal anti-fraud notices._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-dd-method-country-risk',
    type: 'dd_method',
    text:
`🌍 *DD METHOD — Country risk is not a verdict*
━━━━━━━━━━━━━━━━━━━━━━━━━━

High-risk jurisdictions still contain clean buyers. Low-risk jurisdictions still host shell companies. The country tells you how much evidence to collect; it does not decide the answer.

🔍 *Better question:* does this counterparty's behaviour make sense for its jurisdiction, sector, age, ownership and product request?

If the story is mismatched, raise the standard: ownership proof, end-use evidence, site verification, payment-source checks and adverse media in local language.

💬 Reply \`SCREEN [company]\` to start with the entity, then layer country-specific DD.
_Sources: FATF risk-based approach guidance; UK sanctions compliance guidance._
🤖 *ARIA Intelligence* · imaria.io`,
  },
  {
    id: 'ef-2026-07-signal-ownership-thresholds',
    type: 'signal',
    text:
`⚖️ *SIGNAL — Ownership thresholds are not comfort blankets*
━━━━━━━━━━━━━━━━━━━━━━━━━━

The OFAC 50 Percent Rule is famous, but control risk does not stop at 49%. A minority owner can still control management, financing, board votes, supply access or the real commercial decision.

Sanctions screening answers "is this legally blocked?" Due diligence asks the next question: "who actually controls the deal?"

🔍 *Check:* direct ownership, indirect ownership, voting rights, nominee directors, financing, related-party transactions and operational dependence.

💬 Reply \`SCREEN [company]\` for sanctions exposure, then escalate control questions into full DD.
_Sources: OFAC 50 Percent Rule guidance; EU best practices for sanctions implementation._
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
