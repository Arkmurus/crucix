const fs = require('fs');

// ── FIX 1: lusophone.mjs — fix ReliefWeb 406 and replace dead feeds ──────────
let l = fs.readFileSync('apis/sources/lusophone.mjs', 'utf8');

// ReliefWeb 406 — needs proper Accept header and different URL format
l = l.replace(
  `  {
    name:   'ReliefWeb Guinea-Bissau',
    url:    'https://reliefweb.int/updates/rss.xml?source=countries&primary_country=86',
    type:   'rss',
    region: 'Guinea-Bissau',
    weight: 'critical',
  },
  {
    name:   'ReliefWeb Angola',
    url:    'https://reliefweb.int/updates/rss.xml?source=countries&primary_country=4',
    type:   'rss',
    region: 'Angola',
    weight: 'high',
  },
  {
    name:   'ReliefWeb Mozambique',
    url:    'https://reliefweb.int/updates/rss.xml?source=countries&primary_country=148',
    type:   'rss',
    region: 'Mozambique',
    weight: 'high',
  },`,
  `  {
    name:   'ReliefWeb Guinea-Bissau',
    url:    'https://reliefweb.int/country/gnb/updates.rss',
    type:   'rss',
    region: 'Guinea-Bissau',
    weight: 'critical',
  },
  {
    name:   'ReliefWeb Angola',
    url:    'https://reliefweb.int/country/ago/updates.rss',
    type:   'rss',
    region: 'Angola',
    weight: 'high',
  },
  {
    name:   'ReliefWeb Mozambique',
    url:    'https://reliefweb.int/country/moz/updates.rss',
    type:   'rss',
    region: 'Mozambique',
    weight: 'high',
  },`
);

// Replace dead VOA Africa with Al Jazeera Africa RSS
l = l.replace(
  `  {
    name:   'VOA Africa',
    url:    'https://www.voanews.com/api/zmpq_iqvt_r',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },`,
  `  {
    name:   'Al Jazeera Africa',
    url:    'https://www.aljazeera.com/xml/rss/all.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },`
);

// Replace dead AfDB with BBC Africa RSS
l = l.replace(
  `  {
    name:   'AfDB Operations',
    url:    'https://www.afdb.org/en/rss/news',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },`,
  `  {
    name:   'BBC Africa',
    url:    'https://feeds.bbci.co.uk/news/world/africa/rss.xml',
    type:   'rss',
    region: 'Africa',
    weight: 'medium',
  },`
);

// Replace DW Portuguese (blocked) with RFI Portuguese
l = l.replace(
  `  {
    name:   'DW Africa Português',
    url:    'https://rss.dw.com/rdf/rss-por-af',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },`,
  `  {
    name:   'RFI Portuguese Africa',
    url:    'https://www.rfi.fr/pt/rss',
    type:   'rss',
    region: 'Lusophone Africa',
    weight: 'high',
  },`
);

fs.writeFileSync('apis/sources/lusophone.mjs', l);
console.log('lusophone.mjs fixed');

// ── FIX 2: export_control_intel.mjs — replace blocked gov feeds ──────────────
let e = fs.readFileSync('apis/sources/export_control_intel.mjs', 'utf8');

// Replace UK ECJU GOV (blocked) with GOV.UK search RSS
e = e.replace(
  `  {
    name:   'UK ECJU GOV',
    url:    'https://www.gov.uk/search/news-and-communications.atom?keywords=export+controls&organisations%5B%5D=export-control-joint-unit',
    type:   'atom',
    weight: 'critical',
  },`,
  `  {
    name:   'UK Export Controls GOV',
    url:    'https://www.gov.uk/government/organisations/export-control-joint-unit.atom',
    type:   'atom',
    weight: 'critical',
  },`
);

// Replace UK Sanctions GOV (blocked) with FCDO news
e = e.replace(
  `  {
    name:   'UK Sanctions GOV',
    url:    'https://www.gov.uk/search/news-and-communications.atom?keywords=sanctions&organisations%5B%5D=foreign-commonwealth-development-office',
    type:   'atom',
    weight: 'critical',
  },`,
  `  {
    name:   'UK FCDO Sanctions',
    url:    'https://www.gov.uk/government/organisations/foreign-commonwealth-development-office.atom',
    type:   'atom',
    weight: 'critical',
  },`
);

// Replace OFAC Recent Actions (404) with Treasury press releases
e = e.replace(
  `  {
    name:   'OFAC Recent Actions',
    url:    'https://ofac.treasury.gov/recent-actions/rss',
    type:   'rss',
    weight: 'critical',
  },`,
  `  {
    name:   'Treasury Press Releases',
    url:    'https://home.treasury.gov/news/press-releases.xml',
    type:   'rss',
    weight: 'critical',
  },`
);

// Replace BIS (blocked) with Federal Register export controls RSS
e = e.replace(
  `  {
    name:   'BIS Export Controls',
    url:    'https://www.bis.doc.gov/index.php/rss-feeds?format=feed&type=rss',
    type:   'rss',
    weight: 'high',
  },`,
  `  {
    name:   'Federal Register Export',
    url:    'https://www.federalregister.gov/api/v1/articles.rss?conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau',
    type:   'rss',
    weight: 'high',
  },`
);

fs.writeFileSync('apis/sources/export_control_intel.mjs', e);
console.log('export_control_intel.mjs fixed');

console.log('All source fixes applied');
