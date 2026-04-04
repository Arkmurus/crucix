// lib/compliance/screen.mjs
// Arkmurus deal compliance pre-screener
//
// Given a potential deal (seller country, buyer country, product category),
// returns a structured compliance assessment covering:
//   - UK ECJU export licensing (OGEL, SIEL, prohibited)
//   - US ITAR/EAR jurisdiction risk
//   - EU dual-use classification
//   - UN/EU/US arms embargo status
//   - End-use certificate requirements
//   - Recommended next steps for Arkmurus
//
// All logic is rules-based (no external API calls needed for pre-screen).
// Definitive determinations require formal legal advice — this is a first-pass filter.

// ── Embargo database ──────────────────────────────────────────────────────────
// UN Security Council arms embargoes (active as of 2026)
const UN_EMBARGOES = new Set([
  'CF', // Central African Republic
  'CD', // Democratic Republic of Congo
  'ER', // Eritrea
  'IQ', // Iraq (partial)
  'LY', // Libya
  'ML', // Mali
  'SO', // Somalia
  'SS', // South Sudan
  'SD', // Sudan
  'YE', // Yemen
  'KP', // North Korea (comprehensive)
  'IR', // Iran (arms)
  'AF', // Afghanistan (Taliban)
  'HT', // Haiti (gangs)
]);

// EU arms embargoes (in addition to UN)
const EU_EMBARGOES = new Set([
  ...UN_EMBARGOES,
  'BY', // Belarus
  'CN', // China (Tiananmen legacy — arms only)
  'MM', // Myanmar/Burma
  'NI', // Nicaragua
  'RU', // Russia (post-Feb 2022)
  'SY', // Syria
  'VE', // Venezuela
  'ZW', // Zimbabwe
]);

// UK arms embargoes (post-Brexit — generally follows EU + additional)
const UK_EMBARGOES = new Set([
  ...EU_EMBARGOES,
  'HK', // Hong Kong (specific restrictions)
]);

// US arms embargoes / Section 655 restriction (ITAR 126.1)
const US_EMBARGOES_ITAR = new Set([
  'BY', 'CN', 'CU', 'CY', 'ET', 'HK', 'IR', 'IQ', 'KP', 'LB', 'LY', 'MM',
  'RU', 'SO', 'SS', 'SD', 'SY', 'VE', 'YE', 'ZW', 'AF',
]);

// Countries with significant ITAR/EAR re-export risk (not embargoed but flagged)
const ITAR_CONCERN = new Set([
  'AE', // UAE — re-export hub; enhanced due diligence required
  'TR', // Turkey — Section 232 history; scrutinised for Russian diversion
  'IN', // India — ITAR-free procurement strategy but many US platforms
  'SA', // Saudi Arabia — FMS preferred; ITAR on most advanced systems
  'PK', // Pakistan — dual-use concerns
  'EG', // Egypt — sanctions history
  'NG', // Nigeria — end-use monitoring
  'ID', // Indonesia — ITAR scrutinised
]);

// ── UK OGEL coverage ──────────────────────────────────────────────────────────
// Open General Export Licences that likely cover the transaction
// (simplified — actual OGEL eligibility depends on exact goods/end-user/destination)
const OGEL_ELIGIBLE = new Set([
  'US', 'CA', 'AU', 'NZ', 'JP', 'KR', 'NO', 'IS', 'CH', // Five Eyes + close allies
  'AL', 'BA', 'GE', 'MD', 'ME', 'MK', 'RS', 'UA', 'XK', // NATO partners/aspirants
  // NATO members
  'BE', 'CZ', 'DK', 'FR', 'DE', 'GR', 'HU', 'IT', 'LU', 'NL', 'PL',
  'PT', 'RO', 'SK', 'SI', 'ES', 'TR', 'BG', 'EE', 'LV', 'LT', 'HR',
  'MN', 'FI', 'SE',
  // Specific licences: MOD-to-MOD (UK Government general licence covers most government-end-user deals to non-embargoed states)
]);

// ── Product category mapping ──────────────────────────────────────────────────
const PRODUCT_CATEGORIES = {
  'small_arms':        { ml: 'ML1',  eccn: '0A501', itarCategory: 'I',   euCategory: '1A', desc: 'Small arms & light weapons' },
  'ammunition':        { ml: 'ML3',  eccn: '0A505', itarCategory: 'III', euCategory: '1A', desc: 'Ammunition & fuzes' },
  'armoured_vehicles': { ml: 'ML6',  eccn: '0A606', itarCategory: 'VII', euCategory: '1A', desc: 'Armoured fighting vehicles' },
  'patrol_vessels':    { ml: 'ML9',  eccn: '8A620', itarCategory: 'VI',  euCategory: '1A', desc: 'Naval vessels & equipment' },
  'military_aircraft': { ml: 'ML10', eccn: '9A610', itarCategory: 'VIII',euCategory: '1A', desc: 'Military aircraft & UAVs' },
  'surveillance_uav':  { ml: 'ML10', eccn: '9A610', itarCategory: 'VIII',euCategory: '1A', desc: 'Surveillance UAVs' },
  'missiles_rockets':  { ml: 'ML4',  eccn: '0A501', itarCategory: 'IV', euCategory: '1A', desc: 'Missiles, rockets, torpedoes' },
  'radar_sensors':     { ml: 'ML15', eccn: '6A998', itarCategory: 'XI', euCategory: '6A', desc: 'Radar & EO/IR sensors' },
  'communications':    { ml: 'ML11', eccn: '5E002', itarCategory: 'XI', euCategory: '5A', desc: 'Military communications & crypto' },
  'c4isr':             { ml: 'ML11', eccn: '0A521', itarCategory: 'XI', euCategory: '5A', desc: 'C4ISR & command systems' },
  'personal_armour':   { ml: 'ML13', eccn: '1A005', itarCategory: 'X',  euCategory: '1A', desc: 'Protective & military equipment' },
  'explosives':        { ml: 'ML8',  eccn: '1C010', itarCategory: 'V',  euCategory: '1C', desc: 'Energetic materials & explosives' },
  'training_services': { ml: 'ML22', eccn: 'EAR99', itarCategory: 'IX', euCategory: null, desc: 'Military training & services' },
  'dual_use_tech':     { ml: null,   eccn: 'varies', itarCategory: null, euCategory: '5A/6A', desc: 'Dual-use technology' },
};

// ── Seller country ITAR contamination risk ───────────────────────────────────
// US-origin goods / goods with >10% US content are ITAR/EAR controlled globally
const US_CONTENT_RISK = new Set([
  'US', 'CA', 'AU', 'GB', // High ITAR content in defence exports
  'IL', // Israeli systems often contain US components
  'TR', // Some Turkish UAVs (Bayraktar TB2 — claims no ITAR; verify)
  'IN', // Tata/BEL may have US-licensed tech
]);

// Non-ITAR, non-EAR sellers (EU/other exports avoid US re-export restrictions)
const NO_ITAR_SELLERS = new Set([
  'DE', 'FR', 'IT', 'ES', 'PT', 'BE', 'NL', 'SE', 'FI', 'NO', 'CH',
  'PL', 'CZ', 'SK', 'RO', 'BG', 'HR',
  'ZA', // Paramount, RDM, Denel — explicitly no ITAR
  'BR', // CBC ammunition — no ITAR
  'TR', // Baykar TB2 — claimed ITAR-free (verify case by case)
]);

// ── Country risk levels ───────────────────────────────────────────────────────
const COUNTRY_RISK = {
  // Low risk — stable democracies, good end-use track record
  LOW:    new Set(['PT', 'ES', 'FR', 'DE', 'GB', 'US', 'CA', 'AU', 'NZ', 'NO', 'SE', 'FI',
                   'CV', 'BR', // Cape Verde, Brazil — Lusophone, stable
                   'PH', 'KE', 'GH', 'RW', 'TZ', 'BD']),
  // Medium risk — active conflicts but no embargo; enhanced due diligence
  MEDIUM: new Set(['AO', 'MZ', 'NG', 'SN', 'CI', 'UG', 'ET', 'ID', 'VN', 'CO', 'PE',
                   'SA', 'AE', 'JO', 'PL', 'RO', 'GR', 'BG']),
  // High risk — fragile states, diversion risk, elevated scrutiny
  HIGH:   new Set(['GW', 'CM', 'ML', 'NE', 'BF', 'CF', 'TD', 'PK', 'EG', 'TR', 'IN']),
};

function getRiskLevel(iso2) {
  if (COUNTRY_RISK.LOW.has(iso2))    return 'low';
  if (COUNTRY_RISK.MEDIUM.has(iso2)) return 'medium';
  if (COUNTRY_RISK.HIGH.has(iso2))   return 'high';
  return 'medium'; // default
}

// ── Main screening function ───────────────────────────────────────────────────
export function screenDeal({
  sellerCountry,  // ISO2: country of OEM / origin of goods
  buyerCountry,   // ISO2: end-user country
  productCategory, // key from PRODUCT_CATEGORIES
  brokerCountry = 'GB', // Arkmurus registration country (default UK)
  dealValueUSD = null,
  notes = '',
}) {
  const product = PRODUCT_CATEGORIES[productCategory] || PRODUCT_CATEGORIES['dual_use_tech'];
  const issues  = [];
  const warnings = [];
  const actions  = [];

  // ── 1. Embargo checks ───────────────────────────────────────────────────────
  const ukEmbargoed  = UK_EMBARGOES.has(buyerCountry);
  const euEmbargoed  = EU_EMBARGOES.has(buyerCountry);
  const unEmbargoed  = UN_EMBARGOES.has(buyerCountry);
  const usEmbargoed  = US_EMBARGOES_ITAR.has(buyerCountry);

  if (unEmbargoed) {
    issues.push({ severity: 'BLOCKED', code: 'UN-EMBARGO', text: `UN Security Council arms embargo on buyer country (${buyerCountry}) — deal prohibited under international law` });
  }
  if (ukEmbargoed && !unEmbargoed) {
    issues.push({ severity: 'BLOCKED', code: 'UK-EMBARGO', text: `UK arms embargo on ${buyerCountry} — no UK export licence possible; deal requires non-UK supply chain` });
  }
  if (euEmbargoed && !ukEmbargoed && !unEmbargoed) {
    warnings.push({ severity: 'HIGH', code: 'EU-EMBARGO', text: `EU arms embargo on ${buyerCountry} — restricts EU-origin goods and EU-based intermediaries` });
  }

  // ── 2. UK ECJU licensing requirement ────────────────────────────────────────
  let ukLicence = 'SIEL_REQUIRED'; // Standard Individual Export Licence — default

  if (ukEmbargoed) {
    ukLicence = 'PROHIBITED';
  } else if (sellerCountry === 'GB') {
    // OGEL (MOD to government / NATO allies for non-sensitive items)
    if (OGEL_ELIGIBLE.has(buyerCountry) && ['ML13', 'ML22', null].includes(product.ml)) {
      ukLicence = 'OGEL_LIKELY';
      actions.push('Verify OGEL eligibility: check ECO OGEL list for specific product ML code');
    } else if (OGEL_ELIGIBLE.has(buyerCountry)) {
      ukLicence = 'OGEL_OR_SIEL';
      actions.push('Apply for SIEL via SPIRE system (ecju.gov.uk) — allow 20 working days; OGEL may apply');
    } else {
      ukLicence = 'SIEL_REQUIRED';
      actions.push('Apply for SIEL via SPIRE system (ecju.gov.uk) — allow 20 working days minimum');
    }
  } else if (brokerCountry === 'GB') {
    // Brokering controls apply under Export Control Order 2008 s.4 even if not UK origin
    warnings.push({ severity: 'MEDIUM', code: 'UK-BROKERING', text: 'UK brokering controls apply (Export Control Order 2008, s.4) — brokering licence may be needed even for non-UK goods' });
    actions.push('Check if brokering registration/licence required under UK ECO 2008 s.4');
    ukLicence = 'BROKERING_CHECK';
  }

  // ── 3. ITAR / EAR (US) assessment ───────────────────────────────────────────
  const itarOrigin  = US_CONTENT_RISK.has(sellerCountry);
  const noItarOrigin = NO_ITAR_SELLERS.has(sellerCountry);
  let itarStatus = 'NOT_APPLICABLE';

  if (usEmbargoed && itarOrigin) {
    issues.push({ severity: 'BLOCKED', code: 'ITAR-126.1', text: `ITAR §126.1 prohibits transfer of US-origin defence articles to ${buyerCountry}` });
    itarStatus = 'PROHIBITED';
  } else if (itarOrigin && product.itarCategory) {
    itarStatus = 'ITAR_CONTROLLED';
    warnings.push({ severity: 'HIGH', code: 'ITAR', text: `Goods may be ITAR-controlled (Category ${product.itarCategory} USML) due to US origin — US State Dept DSP-83/TAL required` });
    actions.push('Obtain ITAR re-export approval from US State Dept (Directorate of Defense Trade Controls) before proceeding');
    if (ITAR_CONCERN.has(buyerCountry)) {
      warnings.push({ severity: 'HIGH', code: 'ITAR-ENDUSE', text: `${buyerCountry} is a heightened ITAR end-use scrutiny destination — extra DSP-83 scrutiny expected` });
    }
  } else if (noItarOrigin) {
    itarStatus = 'NO_ITAR';
    // If no ITAR, still check EAR
    if (product.eccn && product.eccn !== 'EAR99') {
      warnings.push({ severity: 'LOW', code: 'EAR', text: `Product ECCN ${product.eccn} is EAR-controlled — US re-export restrictions still apply to any US-origin components` });
    }
  } else {
    itarStatus = 'CHECK_CONTENT';
    warnings.push({ severity: 'MEDIUM', code: 'ITAR-CONTENT', text: 'Verify US content percentage — if >10% US-origin content, EAR de minimis rule applies; if defence article, ITAR controls regardless of percentage' });
    actions.push('Request US content declaration from OEM before contracting');
  }

  // ── 4. EU dual-use assessment ────────────────────────────────────────────────
  let euDualUse = 'NOT_APPLICABLE';
  if (euEmbargoed && (sellerCountry === 'DE' || sellerCountry === 'FR' || sellerCountry === 'IT' || sellerCountry === 'ES' || sellerCountry === 'NL')) {
    issues.push({ severity: 'BLOCKED', code: 'EU-DUAL-USE', text: `EU arms embargo on ${buyerCountry} — EU Regulation 2021/821 prohibits dual-use transfers` });
    euDualUse = 'PROHIBITED';
  } else if (product.euCategory) {
    euDualUse = 'LICENCE_REQUIRED';
    if (!euEmbargoed && OGEL_ELIGIBLE.has(buyerCountry)) {
      euDualUse = 'EU_GENERAL_LICENCE';
    }
  }

  // ── 5. End-User Certificate ─────────────────────────────────────────────────
  const eucRequired = ['small_arms', 'ammunition', 'armoured_vehicles', 'patrol_vessels',
                       'military_aircraft', 'surveillance_uav', 'missiles_rockets', 'explosives']
    .includes(productCategory);

  if (eucRequired) {
    actions.push('Obtain signed End-User Certificate (EUC/EUEC) from official armed forces / ministry before licence application');
    if (getRiskLevel(buyerCountry) === 'high') {
      actions.push('Consider requesting International Import Certificate (IIC) and Delivery Verification Certificate (DVC) given elevated risk country');
    }
  }

  // ── 6. Country-level risk ────────────────────────────────────────────────────
  const buyerRisk = getRiskLevel(buyerCountry);
  if (buyerRisk === 'high') {
    warnings.push({ severity: 'MEDIUM', code: 'COUNTRY-RISK', text: `${buyerCountry} is a high-risk destination — enhanced due diligence, political risk insurance recommended` });
  }

  // ── 7. Lusophone advantage ───────────────────────────────────────────────────
  const LUSOPHONE = new Set(['AO', 'MZ', 'GW', 'CV', 'ST', 'BR', 'TL', 'PT']);
  if (LUSOPHONE.has(buyerCountry) && !ukEmbargoed) {
    actions.push('Lusophone market — Arkmurus linguistic/cultural advantage; leverage CPLP framework for relationship access');
  }

  // ── 8. Arkmurus-specific broker recommendations ──────────────────────────────
  const blocked  = issues.filter(i => i.severity === 'BLOCKED').length > 0;
  const highRisk = issues.filter(i => i.severity === 'HIGH').length > 0 || warnings.filter(w => w.severity === 'HIGH').length > 0;

  let overallStatus;
  if (blocked)        overallStatus = 'PROHIBITED';
  else if (highRisk)  overallStatus = 'REQUIRES_APPROVAL';
  else                overallStatus = 'PROCEED_WITH_LICENCES';

  // Suggested OEM strategy based on ITAR status
  let oemStrategy = null;
  if (itarStatus === 'PROHIBITED' || itarStatus === 'ITAR_CONTROLLED') {
    oemStrategy = 'Consider ITAR-free alternatives: South African (Paramount, RDM), Brazilian (CBC, Embraer), Turkish (Baykar — verify ITAR-free claim), European (Rheinmetall, Nexter, KNDS) to avoid US State Dept involvement';
  } else if (itarStatus === 'NO_ITAR') {
    oemStrategy = 'Non-ITAR seller — no US State Dept authorisation needed; proceed with UK ECJU licensing track';
  }

  return {
    status:   overallStatus,
    summary:  buildSummary(overallStatus, buyerCountry, sellerCountry, product, ukLicence, itarStatus),
    deal: {
      sellerCountry,
      buyerCountry,
      productCategory,
      product:     product.desc,
      mlCode:      product.ml,
      eccn:        product.eccn,
      dealValueUSD,
    },
    licensing: {
      uk: {
        status:    ukLicence,
        embargoed: ukEmbargoed,
        label:     UK_LICENCE_LABELS[ukLicence] || ukLicence,
      },
      us: {
        status:    itarStatus,
        embargoed: usEmbargoed,
        label:     ITAR_LABELS[itarStatus] || itarStatus,
      },
      eu: {
        status:    euDualUse,
        embargoed: euEmbargoed,
        label:     EU_LABELS[euDualUse] || euDualUse,
      },
      un: {
        embargoed: unEmbargoed,
      },
    },
    eucRequired,
    buyerRisk,
    issues,
    warnings,
    actions: [...new Set(actions)], // deduplicate
    oemStrategy,
    disclaimer: 'This is a first-pass rules-based pre-screen only. Definitive export licensing determinations require formal advice from a qualified export control consultant and/or ECJU direct enquiry.',
    generatedAt: new Date().toISOString(),
  };
}

const UK_LICENCE_LABELS = {
  PROHIBITED:     'Prohibited — UK arms embargo applies',
  BROKERING_CHECK:'Brokering licence check required',
  OGEL_LIKELY:    'OGEL probably applicable',
  OGEL_OR_SIEL:   'OGEL or SIEL required',
  SIEL_REQUIRED:  'SIEL required (Standard Individual Export Licence)',
  NOT_APPLICABLE: 'N/A — no UK jurisdiction',
};

const ITAR_LABELS = {
  PROHIBITED:      'ITAR §126.1 — prohibited',
  ITAR_CONTROLLED: 'ITAR controlled — US State Dept approval needed',
  CHECK_CONTENT:   'Check US content percentage',
  NO_ITAR:         'No ITAR — non-US origin; EAR may apply',
  NOT_APPLICABLE:  'N/A',
};

const EU_LABELS = {
  PROHIBITED:          'Prohibited — EU embargo',
  LICENCE_REQUIRED:    'EU dual-use export authorisation required',
  EU_GENERAL_LICENCE:  'EU General Export Authorisation likely applicable',
  NOT_APPLICABLE:      'N/A',
};

function buildSummary(status, buyer, seller, product, ukLicence, itarStatus) {
  if (status === 'PROHIBITED') {
    return `DEAL BLOCKED: Arms embargo or ITAR §126.1 prohibition applies to ${buyer}. Do not proceed without specialist legal advice.`;
  }
  if (status === 'REQUIRES_APPROVAL') {
    return `REQUIRES APPROVAL: ${product.desc} (${product.ml || product.eccn}) to ${buyer} from ${seller}. UK ${UK_LICENCE_LABELS[ukLicence]}. ${itarStatus === 'ITAR_CONTROLLED' ? 'ITAR controlled — US authorisation needed.' : ''} Enhanced due diligence required.`;
  }
  return `PROCEED WITH LICENCES: Apply for ${UK_LICENCE_LABELS[ukLicence]} for ${product.desc} to ${buyer}. ${itarStatus === 'NO_ITAR' ? 'No ITAR complications.' : 'Verify US content.'} Standard licensing track.`;
}

// ── Batch screen multiple scenarios ──────────────────────────────────────────
export function screenMultiple(deals) {
  return deals.map(d => ({ ...screenDeal(d), dealRef: d.dealRef || null }));
}

// ── Get available product categories ─────────────────────────────────────────
export function getProductCategories() {
  return Object.entries(PRODUCT_CATEGORIES).map(([key, val]) => ({
    key,
    desc:      val.desc,
    mlCode:    val.ml,
    eccn:      val.eccn,
    itarCat:   val.itarCategory,
    euCategory: val.euCategory,
  }));
}
