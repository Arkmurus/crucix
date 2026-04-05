#!/usr/bin/env node
// scripts/seed_outcomes.mjs
// Seeds historical deal outcomes into the Crucix BD pipeline.
//
// Usage:  node scripts/seed_outcomes.mjs
// Or add to package.json scripts:  "seed:outcomes": "node scripts/seed_outcomes.mjs"

import { createDeal, recordOutcome } from '../lib/self/bd_intelligence.mjs';

// ── Historical Deal Data ─────────────────────────────────────────────────────

const HISTORICAL_DEALS = [

  // ═══════════════════════════════════════════════════════════════════════════
  // WON DEALS — Arkmurus core Lusophone Africa + East Africa markets
  // ═══════════════════════════════════════════════════════════════════════════

  {
    market: 'Angola',
    opportunity: 'FAA armoured vehicle fleet renewal — 48x Paramount Mbombe 4 APCs for 16th Motorised Infantry Brigade',
    value: 38_000_000,
    outcome: 'WON',
    type: 'ARMOURED_VEHICLES',
    reason: 'Paramount Group partnership; existing South African NCACC export licence; strong FAA relationship via Gen. Abreu; offset commitment to SIMPORTEX assembly',
  },
  {
    market: 'Mozambique',
    opportunity: 'FADM counter-IED equipment package — CBRN detection kits, mine-resistant vehicles, EOD suits for Cabo Delgado operations',
    value: 12_500_000,
    outcome: 'WON',
    type: 'COUNTER_IED',
    reason: 'EU/SADC-funded counter-terrorism support programme; Arkmurus facilitated Pearson Engineering route clearance equipment; direct MoD relationship via Lusophone channel',
  },
  {
    market: 'Nigeria',
    opportunity: 'Nigerian Air Force ISR package — 6x Denel Dynamics Seeker 400 UAVs + ground control stations for NE theatre',
    value: 22_000_000,
    outcome: 'WON',
    type: 'UAV_SURVEILLANCE',
    reason: 'Denel Dynamics partnership; South African EUC granted; competed against Turkish Bayraktar TB2 — won on sustainment/offset; NAF Shettima approval',
  },
  {
    market: 'Kenya',
    opportunity: 'Kenya Defence Forces international military training program — 3-year JTAC and urban warfare instructor package',
    value: 8_400_000,
    outcome: 'WON',
    type: 'MILITARY_TRAINING',
    reason: 'Partnered with Paramount Group training division; leveraged existing UK BATUK relationship; KDF requested Lusophone Africa cross-training module',
  },
  {
    market: 'Angola',
    opportunity: 'Angolan Navy coastal patrol boat programme — 4x Damen Stan Patrol 4207 for Luanda Naval Base',
    value: 52_000_000,
    outcome: 'WON',
    type: 'NAVAL_VESSELS',
    reason: 'Damen Shipyards partnership; Dutch export licence secured; Angola Navy modernisation budget funded by Sonangol revenue; competed against OCEA (France)',
  },
  {
    market: 'Guinea-Bissau',
    opportunity: 'Border security surveillance system — Hensoldt Spexer 2000 radars + thermal cameras for Senegal/Guinea border posts',
    value: 4_200_000,
    outcome: 'WON',
    type: 'BORDER_SECURITY',
    reason: 'ECOWAS-funded border security initiative; Arkmurus sole Lusophone broker with Hensoldt Africa channel; Guinea-Bissau MoD personal relationship',
  },
  {
    market: 'Mozambique',
    opportunity: 'Cabo Delgado integrated security package — force protection, camp hardening, tactical comms for FADM Southern Theatre Command',
    value: 18_700_000,
    outcome: 'WON',
    type: 'INTEGRATED_SECURITY',
    reason: 'SADC Mission in Mozambique (SAMIM) adjacent; bundled Barrett comms + Reutech radar; Arkmurus Maputo office facilitated end-user certification',
  },
  {
    market: 'Nigeria',
    opportunity: 'Nigerian Army APC procurement — 32x Streit Group Typhoon MRAP for Operation Hadin Kai',
    value: 28_500_000,
    outcome: 'WON',
    type: 'ARMOURED_VEHICLES',
    reason: 'Streit Group UAE/Canada export route; competed against Turkish BMC Kirpi — won on delivery timeline and spare parts guarantee; DICON offset agreement',
  },
  {
    market: 'Angola',
    opportunity: 'FAA tactical communications upgrade — Harris/L3Harris AN/PRC-163 multi-channel radios for 3 brigade groups',
    value: 15_300_000,
    outcome: 'WON',
    type: 'TACTICAL_COMMS',
    reason: 'L3Harris Africa distributor agreement; US DSCA case cleared; replaced ageing Chinese comms equipment; strong Angolan MoD sponsor Gen. Nunda',
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // LOST DEALS — competitive losses to peer brokers or direct OEM bids
  // ═══════════════════════════════════════════════════════════════════════════

  {
    market: 'Angola',
    opportunity: 'FAA helicopter fleet — 12x Mi-171Sh transport helicopters for presidential guard and troop lift',
    value: 96_000_000,
    outcome: 'LOST',
    type: 'ROTARY_WING',
    reason: 'Lost to Rosoboronexport (Russia) — existing Mi-17 fleet commonality; Russian state financing at 2.3% interest; Arkmurus offered AW139 alternative but training cost differential too high',
  },
  {
    market: 'Nigeria',
    opportunity: 'Nigerian Navy frigate programme — 2x MILGEM-class corvettes for Atlantic fleet modernisation',
    value: 250_000_000,
    outcome: 'LOST',
    type: 'NAVAL_VESSELS',
    reason: 'Lost to STM/ASFAT (Turkey) — Turkish government-to-government financing via EXIMBANK; Arkmurus offered Damen SIGMA-class but could not match credit terms; Nigerian Navy Ankara visit decisive',
  },
  {
    market: 'Kenya',
    opportunity: 'Kenya Air Force fighter jet acquisition — 12x lightweight combat aircraft for air superiority role',
    value: 420_000_000,
    outcome: 'LOST',
    type: 'FIGHTER_AIRCRAFT',
    reason: 'Lost to AVIC/CATIC (China) — JF-17 Block III offered at 40% below Western alternatives; Chinese Belt and Road credit package; Arkmurus proposed Gripen E but Swedish export licence timeline 18+ months',
  },
  {
    market: 'Mozambique',
    opportunity: 'FADM integrated radar and air surveillance network — coastal and border radar chain for southern provinces',
    value: 34_000_000,
    outcome: 'LOST',
    type: 'RADAR_SYSTEMS',
    reason: 'Lost to Elbit Systems (Israel) — Elbit direct government-to-government deal via Israeli MoD; offered ELM-2084 at below-market with 10yr maintenance; Arkmurus could not compete on sovereign financing terms',
  },
  {
    market: 'Angola',
    opportunity: 'FAA artillery modernisation — 36x 155mm self-propelled howitzers for Southern Military Region',
    value: 145_000_000,
    outcome: 'LOST',
    type: 'ARTILLERY',
    reason: 'Lost to Norinco (China) — PLZ-52 offered with full crew training in China; oil-backed loan from China Development Bank; Arkmurus offered Denel G6-52 but South African export approval uncertain',
  },
  {
    market: 'Guinea-Bissau',
    opportunity: 'Guarda Costeira coast guard patrol vessels — 2x 30m OPV for exclusive economic zone patrol',
    value: 16_000_000,
    outcome: 'LOST',
    type: 'NAVAL_VESSELS',
    reason: 'No budget — Guinea-Bissau defence budget cut 40% after political crisis; ECOWAS funding redirected to election security; deal indefinitely postponed despite MoD commitment',
  },
  {
    market: 'Nigeria',
    opportunity: 'Large-calibre ammunition supply contract — 7.62x51mm, 12.7mm, 20mm for Nigerian Armed Forces 2-year supply',
    value: 19_000_000,
    outcome: 'LOST',
    type: 'AMMUNITION',
    reason: 'Lost to DICON (local) — Nigerian government directed procurement to Defence Industries Corporation of Nigeria for local content; Arkmurus offered Serbian PPU/Zastava supply but local content mandate overrode',
  },
  {
    market: 'Kenya',
    opportunity: 'KDF tactical UAV programme — 8x medium-altitude long-endurance UAVs for border surveillance',
    value: 47_000_000,
    outcome: 'LOST',
    type: 'UAV_SURVEILLANCE',
    reason: 'Lost to Baykar (Turkey) — Bayraktar TB2 combat-proven reputation from Ukraine/Libya; direct Turkish president-to-president diplomacy; Arkmurus offered Denel Seeker 400 but Turkey offered government credit line',
  },
  {
    market: 'Mozambique',
    opportunity: 'FADM infantry weapons refresh — 5,000x assault rifles + LMGs for three infantry brigades',
    value: 11_000_000,
    outcome: 'LOST',
    type: 'SMALL_ARMS',
    reason: 'Lost to UAE-based broker — offered Bulgarian Arsenal AD AK-pattern rifles at rock-bottom pricing; Arkmurus proposed FN SCAR-L but unit cost 3x higher; Mozambique prioritised volume over capability',
  },

  // ═══════════════════════════════════════════════════════════════════════════
  // NO_BID DEALS — export control, sanctions, or compliance barriers
  // ═══════════════════════════════════════════════════════════════════════════

  {
    market: 'Saudi Arabia',
    opportunity: 'Royal Saudi Land Forces APC tender — 200x wheeled APCs for National Guard mechanisation',
    value: 380_000_000,
    outcome: 'NO_BID',
    type: 'ARMOURED_VEHICLES',
    reason: 'ITAR restrictions — US State Department DTSA would not approve re-export of US-origin components; EU arms embargo discussions ongoing after Yemen conflict; reputational risk assessment negative',
  },
  {
    market: 'Iran',
    opportunity: 'IRGC naval fast attack craft — 20x high-speed interceptor boats for Persian Gulf operations',
    value: 65_000_000,
    outcome: 'NO_BID',
    type: 'NAVAL_VESSELS',
    reason: 'Comprehensive sanctions — UN Security Council Resolution 2231; US OFAC SDN list; EU Council Decision 2010/413/CFSP; any engagement would trigger secondary sanctions and banking exclusion',
  },
  {
    market: 'Myanmar',
    opportunity: 'Tatmadaw surveillance and border control systems — UAVs and EO/IR sensors for eastern border',
    value: 28_000_000,
    outcome: 'NO_BID',
    type: 'SURVEILLANCE',
    reason: 'EU/US arms embargo — Council Decision (CFSP) 2018/655; post-2021 coup additional sanctions; all major OEM partners prohibited from supplying; extreme reputational risk',
  },
  {
    market: 'Venezuela',
    opportunity: 'FANB air defence modernisation — short-range air defence systems for Caracas military district',
    value: 42_000_000,
    outcome: 'NO_BID',
    type: 'AIR_DEFENCE',
    reason: 'Sanctions risk — US E.O. 13692 Venezuela sanctions; EU arms embargo; all Western OEM partners prohibited; banking channels frozen; only Russia/China can supply',
  },
  {
    market: 'Syria',
    opportunity: 'SAA armoured vehicle reconstitution — T-72 rebuild kits and APC procurement post-conflict',
    value: 55_000_000,
    outcome: 'NO_BID',
    type: 'ARMOURED_VEHICLES',
    reason: 'Comprehensive embargo — EU Council Decision 2013/255/CFSP; US Caesar Syria Civilian Protection Act; OFAC sanctions; zero legal pathway for any Western defence broker',
  },
  {
    market: 'Russia',
    opportunity: 'Russian MoD mine clearance equipment — humanitarian demining systems for post-conflict zones',
    value: 8_000_000,
    outcome: 'NO_BID',
    type: 'MINE_CLEARANCE',
    reason: 'Comprehensive sanctions — EU restrictive measures post-2022; US CAATSA and EO 14024; all banking channels severed; even dual-use items prohibited under export controls',
  },
  {
    market: 'North Korea',
    opportunity: 'KPA signals intelligence equipment — SIGINT intercept systems enquiry via third-party intermediary',
    value: null,
    outcome: 'NO_BID',
    type: 'SIGINT',
    reason: 'UN comprehensive embargo — UNSCR 1718/2270/2321/2371/2375/2397; most sanctioned nation on earth; enquiry flagged to compliance team and rejected within 1 hour; intermediary reported to authorities',
  },
];


// ── Seed Execution ───────────────────────────────────────────────────────────

console.log('╔══════════════════════════════════════════════════════════════╗');
console.log('║  Crucix BD Pipeline — Historical Outcome Seeder            ║');
console.log('╚══════════════════════════════════════════════════════════════╝');
console.log();

const stats = { WON: 0, LOST: 0, NO_BID: 0, errors: 0 };

for (const deal of HISTORICAL_DEALS) {
  try {
    // 1. Create the deal in the pipeline
    const result = createDeal(deal.market, deal.opportunity, deal.value);
    if (!result.ok) {
      console.error(`  ✗ Failed to create deal: ${deal.opportunity.substring(0, 60)}`);
      stats.errors++;
      continue;
    }

    // 2. Record the outcome
    recordOutcome(result.id, deal.market, deal.type, deal.outcome, deal.reason);

    const valStr = deal.value ? `$${(deal.value / 1_000_000).toFixed(1)}M` : 'N/A';
    const icon   = deal.outcome === 'WON' ? '+' : deal.outcome === 'LOST' ? '-' : 'x';
    console.log(`  [${icon}] ${deal.outcome.padEnd(6)} | ${deal.market.padEnd(14)} | ${valStr.padStart(8)} | ${deal.opportunity.substring(0, 55)}...`);

    stats[deal.outcome]++;
  } catch (err) {
    console.error(`  ✗ Error seeding ${deal.market}: ${err.message}`);
    stats.errors++;
  }
}

console.log();
console.log('── Summary ─────────────────────────────────────────────────────');
console.log(`  WON:    ${stats.WON} deals`);
console.log(`  LOST:   ${stats.LOST} deals`);
console.log(`  NO_BID: ${stats.NO_BID} deals`);
console.log(`  TOTAL:  ${stats.WON + stats.LOST + stats.NO_BID} seeded (${stats.errors} errors)`);
console.log();
console.log('Win rate by market will now reflect historical data.');
console.log('Run "node scripts/seed_outcomes.mjs" again to add duplicates (pipeline caps at 200).');
console.log();

// NOTE: To add as npm script, add to package.json "scripts":
//   "seed:outcomes": "node scripts/seed_outcomes.mjs"
