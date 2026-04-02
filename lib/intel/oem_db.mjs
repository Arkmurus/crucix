// lib/intel/oem_db.mjs
// European Artillery Ammunition & Components OEM Intelligence Directory
// Covers NATO + non-NATO European manufacturers
// Includes: complete rounds, projectiles, fuzes, propellants, cases, guidance, charges
//
// Scale tiers:
//   prime     — major defense prime, full system integration
//   tier1     — significant manufacturer, complete rounds capability
//   tier2     — component / sub-system manufacturer
//   specialist — niche / highly specialised single product focus

export const OEM_DATABASE = [

  // ─── GERMANY ────────────────────────────────────────────────────────────────

  {
    id: 'rheinmetall-waffe-munition',
    name: 'Rheinmetall Waffe Munition GmbH',
    country: 'Germany',
    nato: true,
    parentGroup: 'Rheinmetall AG',
    scale: 'prime',
    products: ['155mm complete rounds', '120mm mortar', '105mm', 'propellant charges', 'fuzes', 'cartridge cases', 'sub-munitions'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar', '76mm'],
    usp: 'Europe\'s largest artillery ammunition manufacturer. DM121/DM131 propellant charges and DM121A1 zone charges are NATO standard. Major supplier to Bundeswehr and NATO allies.',
    description: 'Full-spectrum artillery ammunition manufacturer covering everything from complete rounds to individual components. Key products include 155mm L/52 charges, DM702 base bleed projectiles, and mortar bombs. Major NATO supplier with qualified production lines for STANAG-compliant ammunition.',
    website: 'https://www.rheinmetall.com/en/rheinmetall_ag/divisions_and_companies/weapons_and_ammunition',
    contact: {
      general: '+49 211 473-01',
      address: 'Rheinmetall AG, Rheinmetall Platz 1, 40476 Düsseldorf, Germany',
      email: 'info@rheinmetall.com',
    },
    exportContact: 'export.control@rheinmetall.com',
    tags: ['155mm', 'propellant', 'charges', 'mortar', 'Germany', 'NATO', 'prime'],
  },

  {
    id: 'diehl-defence',
    name: 'Diehl Defence GmbH & Co. KG',
    country: 'Germany',
    nato: true,
    parentGroup: 'Diehl Group',
    scale: 'prime',
    products: ['fuzes', 'SMArt 155 sensor-fuzed munition', 'BONUS guided projectile', '155mm complete rounds', 'proximity fuzes', 'point-detonating fuzes'],
    calibres: ['155mm', '105mm', '120mm mortar'],
    usp: 'Developer of SMArt 155 — the world\'s first qualified sensor-fuzed artillery munition. Strong fuze portfolio from PD to proximity to time fuzes across all major calibres.',
    description: 'Specialist in smart munitions and fuze technology. SMArt 155 delivers top-attack capability against armoured vehicles. Also produces conventional fuzes (DM84, DM84A1) and collaborates with BAE Systems on BONUS programme. Key partner for Bundeswehr precision fires.',
    website: 'https://www.diehl.com/defence',
    contact: {
      general: '+49 911 957-0',
      address: 'Diehl Defence GmbH & Co. KG, Fischbachstraße 16, 90552 Röthenbach an der Pegnitz, Germany',
      email: 'info@diehl-defence.com',
    },
    exportContact: 'defence.exports@diehl-defence.com',
    tags: ['fuzes', 'smart munitions', 'SMArt 155', 'Germany', 'NATO', 'precision'],
  },

  {
    id: 'junghans-defence',
    name: 'Junghans Defence GmbH',
    country: 'Germany',
    nato: true,
    parentGroup: 'Junghans Group',
    scale: 'tier2',
    products: ['mechanical time fuzes', 'electronic time fuzes', 'point-detonating fuzes', 'multi-option fuzes'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'Highly specialised fuze manufacturer with over 150 years of precision engineering. M739A1 multi-option fuze widely fielded across NATO. Fuzes qualified for temperature extremes and shock environments.',
    description: 'One of Europe\'s leading fuze manufacturers. Product portfolio includes the M739A1 multi-option fuze, DM111/DM111A1 time fuzes, and electronic point-detonating fuzes. Supplies NATO armies and qualified production partner for US DoD programmes.',
    website: 'https://www.junghans.de/en/defence',
    contact: {
      general: '+49 7423 89-0',
      address: 'Junghans Defence GmbH, Geishaldenstraße 49, 78713 Schramberg, Germany',
      email: 'defence@junghans.de',
    },
    tags: ['fuzes', 'time fuzes', 'Germany', 'NATO', 'specialist'],
  },

  // ─── FRANCE ─────────────────────────────────────────────────────────────────

  {
    id: 'nexter-munitions',
    name: 'Nexter Munitions SAS',
    country: 'France',
    nato: true,
    parentGroup: 'KNDS Group',
    scale: 'prime',
    products: ['155mm complete rounds', '120mm mortar', '105mm', 'OFL APFSDS', 'propellant charges', 'projectiles', 'smoke rounds'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar'],
    usp: 'CAESAR howitzer-compatible ammunition developer. Full French Army qualification for 155mm charges and projectiles. ERFB-BB and VLAP extended-range projectiles. Dominant French supplier with legacy from Giat Industries.',
    description: 'France\'s primary artillery ammunition manufacturer and part of KNDS alongside KMW. Produces the complete 155mm family including OFM 155 F3 rocket-assisted projectile and BM-15 smoke. Mortar range covers 60mm to 120mm. Extensive customer base across Francophone Africa and Middle East.',
    website: 'https://www.knds.com/en/munitions',
    contact: {
      general: '+33 1 30 11 60 00',
      address: 'KNDS France SAS, Route de Guerry, 18023 Bourges Cedex, France',
      email: 'contact@knds.fr',
    },
    exportContact: 'export@knds.fr',
    tags: ['155mm', 'CAESAR', 'mortar', 'France', 'NATO', 'prime'],
  },

  {
    id: 'eurenco',
    name: 'Eurenco SA',
    country: 'France',
    nato: true,
    parentGroup: 'Eurenco Group (France/Sweden/Belgium)',
    scale: 'tier1',
    products: ['propellant powders', 'nitrocellulose', 'double-base propellants', 'triple-base propellants', 'modular charges', 'energetic materials'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm', 'small calibre'],
    usp: 'Europe\'s largest energetics group. Multi-site production across France (Bergerac, Sorgues), Sweden (Bofors), and Belgium (Clabecq). Sole European producer of certain nitramine compounds. Critical supply chain chokepoint for NATO propellant.',
    description: 'Eurenco produces propellant powders, nitrocellulose, and energetic materials used throughout European artillery ammunition. Key products include NCC (nitrocellulose powder), double-base propellant for BONUS/SMArt, and modular charge systems. Classified critical infrastructure for NATO ammunition supply.',
    website: 'https://www.eurenco.com',
    contact: {
      general: '+33 5 53 74 44 00',
      address: 'Eurenco SA, Quartier Ambès, 24100 Bergerac, France',
      email: 'contact@eurenco.com',
    },
    tags: ['propellants', 'energetics', 'nitrocellulose', 'France', 'Sweden', 'Belgium', 'NATO'],
  },

  {
    id: 'tda-armements',
    name: 'TDA Armements SAS',
    country: 'France',
    nato: true,
    parentGroup: 'Thales / Leonardo JV',
    scale: 'tier1',
    products: ['120mm rifled mortar', '81mm mortar', 'mortar bombs', 'mortar systems', 'charge systems'],
    calibres: ['120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'French Army\'s primary mortar system developer (2R2M twin 120mm self-propelled mortar). Full mortar ammunition family qualification. Long export track record to Middle East, Africa, and Asia.',
    description: 'Designs and produces mortar weapon systems and associated ammunition. The 2R2M self-propelled 120mm mortar is in service with French, Belgian, and Moroccan armies. Produces the complete M929/M930/M931/M934 120mm bomb family and 81mm Mk2 bombs.',
    website: 'https://www.tda-armements.com',
    contact: {
      general: '+33 4 72 52 65 00',
      address: 'TDA Armements SAS, 3 Avenue du Président Salvador Allende, 91035 Evry, France',
      email: 'tda@tda-armements.com',
    },
    tags: ['mortar', '120mm', '81mm', 'France', 'NATO'],
  },

  // ─── UNITED KINGDOM ─────────────────────────────────────────────────────────

  {
    id: 'chemring-munitions',
    name: 'Chemring Munitions Ltd',
    country: 'United Kingdom',
    nato: true,
    parentGroup: 'Chemring Group PLC',
    scale: 'tier1',
    products: ['fuzes', 'pyrotechnic charges', 'mortar bombs', 'illuminating rounds', 'smoke rounds', 'signal cartridges', 'decoy flares'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'UK\'s leading fuze and pyrotechnic manufacturer. L106A1 and L119 fuze families are UK/NATO standard. Wide export customer base. Chemring Group is global with US and Australian production capability too.',
    description: 'Produces fuzes, pyrotechnic components, mortar ammunition, and illuminating rounds. Key products include L106A1 direct action fuze, L119A1 multi-role fuze, and the full UK mortar bomb family (L15A3, L16A2). Core supplier to UK MoD and close ally export customers.',
    website: 'https://www.chemring.co.uk/munitions',
    contact: {
      general: '+44 1794 853000',
      address: 'Chemring Munitions Ltd, Gosport Road, Fareham, Hampshire PO14 1AH, United Kingdom',
      email: 'munitions@chemring.co.uk',
    },
    tags: ['fuzes', 'pyrotechnics', 'mortar', 'UK', 'NATO'],
  },

  {
    id: 'bae-systems-munitions',
    name: 'BAE Systems Global Combat Systems',
    country: 'United Kingdom',
    nato: true,
    parentGroup: 'BAE Systems PLC',
    scale: 'prime',
    products: ['155mm L/52 artillery systems', 'AS90 howitzer', 'M777 howitzer', '155mm complete rounds', 'BONUS guided projectile', 'Vulcano guided projectile'],
    calibres: ['155mm', '105mm'],
    usp: 'Developer of M777 ultralight howitzer (fielded by US, UK, Canada, AUS, India) and AS90. BONUS 155mm sensor-fuzed anti-armour projectile co-developed with Diehl. Vulcano 155mm GPS/IR guided munition.',
    description: 'Major global artillery systems and munitions prime. M777 is the most widely exported 155mm howitzer in NATO. BONUS 155 gives end-sensitive capability vs armoured vehicles. Vulcano 155 extends range to 70km+ with GPS guidance. Bofors production in Sweden feeds the 155mm programme.',
    website: 'https://www.baesystems.com/en/product/artillery',
    contact: {
      general: '+44 1252 373232',
      address: 'BAE Systems PLC, 6 Carlton Gardens, London SW1Y 5AD, United Kingdom',
      email: 'info@baesystems.com',
    },
    exportContact: 'global.sales@baesystems.com',
    tags: ['155mm', 'M777', 'AS90', 'BONUS', 'UK', 'NATO', 'prime'],
  },

  // ─── NORWAY / SCANDINAVIA ────────────────────────────────────────────────────

  {
    id: 'nammo',
    name: 'Nammo AS',
    country: 'Norway',
    nato: true,
    parentGroup: 'Nammo Group (Norway/Finland JV)',
    scale: 'prime',
    products: ['155mm complete rounds', '105mm complete rounds', 'mortar bombs', 'propellant charges', 'rocket motors', 'ERFB-BB projectiles', 'illuminating rounds', 'smoke rounds', 'M107/M795 projectiles'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'Scandinavia\'s largest ammunition manufacturer. Multi-country production (Norway, Finland, Sweden, US). 155mm ERFB-BB (Extended Range Full Bore Base Bleed) achieves 40km+ range. Supplies NATO and close allies. Key production surge partner for Ukraine support.',
    description: 'Nammo Group operates factories across Norway (Raufoss), Finland (Lapua), Sweden (Karlskoga), and the US. Product range spans small calibre through 155mm including the high-performance ERFB-BB projectile, modular propellant charges, and smart munitions. Major contractor for Norwegian, Finnish, and US military.',
    website: 'https://www.nammo.com',
    contact: {
      general: '+47 61 15 36 00',
      address: 'Nammo AS, Raufoss Technology Park, 2831 Raufoss, Norway',
      email: 'info@nammo.com',
    },
    exportContact: 'sales@nammo.com',
    tags: ['155mm', '105mm', 'mortar', 'propellant', 'Norway', 'Finland', 'NATO', 'prime'],
  },

  {
    id: 'bae-systems-bofors',
    name: 'BAE Systems Bofors AB',
    country: 'Sweden',
    nato: true,
    parentGroup: 'BAE Systems PLC',
    scale: 'tier1',
    products: ['155mm Archer self-propelled howitzer', 'BONUS 155mm guided projectile', 'Excalibur-compatible projectiles', 'Vulcano 155mm', 'propellant charges', 'fuzes'],
    calibres: ['155mm', '40mm Bofors'],
    usp: 'Legacy Bofors facility now producing BONUS 155 and supporting Archer howitzer. Archer achieves burst-fire of 8 rounds in under 30 seconds. BONUS top-attack sub-munition is qualified in multiple NATO armies.',
    description: 'Located in Karlskoga, Sweden — the historic home of Bofors. Now produces 155mm complete rounds, BONUS anti-armour projectiles, and components for the Archer self-propelled gun. Key supplier to Swedish, Norwegian, and international 155mm programmes.',
    website: 'https://www.baesystems.com/en/our-company/our-businesses/platforms--services/bofors',
    contact: {
      general: '+46 586 730 00',
      address: 'BAE Systems Bofors AB, Box 900, 691 80 Karlskoga, Sweden',
      email: 'info.bofors@baesystems.com',
    },
    tags: ['155mm', 'BONUS', 'Archer', 'Sweden', 'NATO'],
  },

  // ─── ITALY ───────────────────────────────────────────────────────────────────

  {
    id: 'rwm-italia',
    name: 'RWM Italia SpA',
    country: 'Italy',
    nato: true,
    parentGroup: 'Rheinmetall AG',
    scale: 'tier1',
    products: ['105mm complete rounds', '155mm projectiles', 'mortar bombs', 'aircraft bombs', 'guided bombs', 'warheads'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar'],
    usp: 'Italy\'s primary artillery and bomb manufacturer. Produces 105mm DM702/M1 family standard in Italian Army and export. Significant capability in air-delivered munitions alongside artillery rounds.',
    description: 'Part of Rheinmetall Group, RWM Italia produces conventional artillery rounds, mortar bombs, and air-delivered munitions. The 105mm artillery production line supports Italian military and extensive export customers in Africa, Middle East, and Asia.',
    website: 'https://www.rwm-italia.com',
    contact: {
      general: '+39 0781 9371',
      address: 'RWM Italia SpA, Loc. Donnigazza, 09010 Domusnovas (CI), Sardinia, Italy',
      email: 'info@rwm-italia.com',
    },
    tags: ['105mm', '155mm', 'mortar', 'Italy', 'NATO', 'Rheinmetall'],
  },

  {
    id: 'simmel-difesa',
    name: 'Simmel Difesa SpA',
    country: 'Italy',
    nato: true,
    parentGroup: 'Independent',
    scale: 'tier2',
    products: ['fuzes', 'electronic time fuzes', 'proximity fuzes', 'point-detonating fuzes', 'mortar fuzes'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar'],
    usp: 'Italian fuze specialist with full NATO fuze portfolio. DM84 fuze copy production and proprietary designs. Compact proximity fuze technology competitive with Junghans.',
    description: 'Manufactures a comprehensive range of artillery and mortar fuzes for the Italian armed forces and export customers. Products include mechanical time fuzes (MTF), point-detonating fuzes (PDF), and electronic multi-option fuzes. Qualified producer for Italian MoD fuze standardisation programme.',
    website: 'https://www.simmel-difesa.it',
    contact: {
      general: '+39 06 5003 2111',
      address: 'Simmel Difesa SpA, Via del Casale di San Nicola, 00123 Rome, Italy',
      email: 'info@simmel-difesa.it',
    },
    tags: ['fuzes', 'Italy', 'NATO', 'specialist'],
  },

  // ─── BELGIUM ─────────────────────────────────────────────────────────────────

  {
    id: 'mecar',
    name: 'Mecar SA',
    country: 'Belgium',
    nato: true,
    parentGroup: 'Aerojet Rocketdyne / General Dynamics (acquired)',
    scale: 'tier1',
    products: ['60mm mortar', '81mm mortar', '120mm mortar', 'projectiles', 'cartridges', 'anti-tank rounds', 'illuminating rounds'],
    calibres: ['120mm mortar', '81mm mortar', '60mm mortar', '90mm', '106mm recoilless'],
    usp: 'Specialist in mortar ammunition and non-standard calibre rounds. Strong legacy in African and Middle East export markets. One of Europe\'s few producers of 60mm commando mortar bombs.',
    description: 'Mecar has a long history producing conventional mortar and artillery rounds, particularly 81mm and 120mm mortar bombs. Extensive export catalogue including illuminating, smoke, HE, and practice rounds. Previously produced 90mm and 106mm recoilless rounds for legacy systems still in African service.',
    website: 'https://www.mecar.be',
    contact: {
      general: '+32 67 64 70 11',
      address: 'Mecar SA, Rue de Tres-Maisons, 7181 Feluy, Belgium',
      email: 'info@mecar.be',
    },
    tags: ['mortar', '81mm', '120mm', '60mm', 'Belgium', 'NATO'],
  },

  // ─── POLAND ──────────────────────────────────────────────────────────────────

  {
    id: 'zm-mesko',
    name: 'ZM Mesko SA',
    country: 'Poland',
    nato: true,
    parentGroup: 'Polska Grupa Zbrojeniowa (PGZ)',
    scale: 'tier1',
    products: ['122mm rockets', '128mm rockets', 'anti-tank missiles', 'propellant charges', 'mortar bombs', 'hand grenades', 'illuminating rounds'],
    calibres: ['122mm', '128mm', '120mm mortar', '81mm mortar'],
    usp: 'Poland\'s largest ammunition and rocket manufacturer. Key supplier for post-Soviet 122mm BM-21 Grad systems still widely operated in Eastern Europe. Rapidly expanding 155mm NATO-calibre production with PGZ group.',
    description: 'ZM Mesko produces rockets, propellant charges, anti-tank guided missiles (Spike licence), and mortar ammunition. Key products include the 122mm WP-8 rocket for BM-21 Grad and Polish DANA-howitzer propellant charges. PGZ is investing heavily to expand 155mm NATO standard production lines.',
    website: 'https://www.mesko.com.pl',
    contact: {
      general: '+48 48 612 00 00',
      address: 'ZM Mesko SA, ul. Legionów 122, 26-600 Radom, Poland',
      email: 'mesko@mesko.com.pl',
    },
    tags: ['122mm', 'rockets', 'propellant', 'Poland', 'NATO', 'PGZ'],
  },

  {
    id: 'nitro-chem',
    name: 'Nitro-Chem SA',
    country: 'Poland',
    nato: true,
    parentGroup: 'Polska Grupa Zbrojeniowa (PGZ)',
    scale: 'tier2',
    products: ['propellant powders', 'nitrocellulose', 'single-base propellants', 'double-base propellants', 'explosive fills'],
    calibres: ['155mm', '122mm', '152mm', 'mortar'],
    usp: 'Poland\'s sole propellant powder manufacturer. Critical for PGZ group ammunition independence. NATO-qualified propellant grades. Expanding capacity to support Polish and NATO allies\' surge requirements.',
    description: 'Produces nitrocellulose-based propellants for artillery, mortar, and small arms. Supplies the wider PGZ group as well as export customers. Holds STANAG qualification for 155mm modular charge propellant. Significant investment announced in 2023-2025 for capacity tripling.',
    website: 'https://www.nitro-chem.com.pl',
    contact: {
      general: '+48 52 323 14 00',
      address: 'Nitro-Chem SA, ul. Wojska Polskiego 65A, 85-825 Bydgoszcz, Poland',
      email: 'nitrochem@nitro-chem.com.pl',
    },
    tags: ['propellants', 'nitrocellulose', 'Poland', 'NATO', 'PGZ'],
  },

  {
    id: 'zm-dezamet',
    name: 'ZM Dezamet SA',
    country: 'Poland',
    nato: true,
    parentGroup: 'Polska Grupa Zbrojeniowa (PGZ)',
    scale: 'tier2',
    products: ['mortar bombs', '120mm mortar', '81mm mortar', '60mm mortar', 'grenade fuzes', 'pyrotechnic components'],
    calibres: ['120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'Poland\'s dedicated mortar ammunition plant. Produces all calibres for Polish armed forces. Growing export capacity. 120mm mortar bomb compatible with French Thomson-Brandt and Israeli Soltam systems.',
    description: 'Dezamet specialises exclusively in mortar ammunition across all calibres (60mm, 81mm, 120mm). Products include HE, illuminating, smoke, and practice variants. Sole qualified supplier for Polish Army mortar requirements with export to NATO partners.',
    website: 'https://www.dezamet.com.pl',
    contact: {
      general: '+48 15 843 40 00',
      address: 'ZM Dezamet SA, ul. Mokra 1, 37-403 Nowa Deba, Poland',
      email: 'dezamet@dezamet.com.pl',
    },
    tags: ['mortar', '120mm', '81mm', 'Poland', 'NATO', 'PGZ'],
  },

  // ─── CZECH REPUBLIC ─────────────────────────────────────────────────────────

  {
    id: 'msm-group',
    name: 'MSM Group sro',
    country: 'Czech Republic',
    nato: true,
    parentGroup: 'MSM Group',
    scale: 'tier1',
    products: ['152mm projectiles', '155mm projectiles', 'cartridge cases', 'components', 'fuze adaptors'],
    calibres: ['152mm', '155mm', '122mm'],
    usp: 'Czech Republic\'s primary artillery shell manufacturer. Significant conversion capability from Soviet 152mm to NATO 155mm dimensions. Key supplier for Ukraine via Czech government ammunition initiative.',
    description: 'MSM Group operates multiple production facilities across Czech Republic manufacturing projectile bodies, cartridge cases, and artillery components. Has received major contracts to produce 155mm shells for Ukrainian support. Conversion from 152mm Soviet tooling to 155mm NATO standard underway.',
    website: 'https://www.msmgroup.cz',
    contact: {
      general: '+420 566 501 111',
      address: 'MSM Group sro, Hrotovická 1697, 674 01 Třebíč, Czech Republic',
      email: 'info@msmgroup.cz',
    },
    tags: ['155mm', '152mm', 'projectiles', 'Czech Republic', 'NATO'],
  },

  {
    id: 'explosia',
    name: 'Explosia AS',
    country: 'Czech Republic',
    nato: true,
    parentGroup: 'Czech state (Synthesia Group)',
    scale: 'tier2',
    products: ['propellant powders', 'PETN', 'RDX', 'HMX', 'TNT', 'OCTOL', 'plastic explosives', 'propellant charges'],
    calibres: ['155mm', '122mm', '152mm', 'mortar'],
    usp: 'Central Europe\'s largest explosives and propellant manufacturer. Produces Semtex plastic explosive (global exports) alongside military-grade TNT, RDX, and propellant powders. STANAG-qualified for 155mm charges.',
    description: 'Located in Pardubice, Explosia operates the Semtex and military explosives production lines. Key NATO supplier for explosive fills, propellant powders, and bulk energetics. Critical infrastructure for Czech and Slovak ammunition programmes. Also produces Vz.70 propellant for 152mm Soviet-legacy systems.',
    website: 'https://www.explosia.cz',
    contact: {
      general: '+420 466 822 111',
      address: 'Explosia AS, Semtín, 532 17 Pardubice, Czech Republic',
      email: 'explosia@explosia.cz',
    },
    tags: ['propellants', 'explosives', 'RDX', 'TNT', 'Czech Republic', 'NATO'],
  },

  // ─── SLOVAKIA ────────────────────────────────────────────────────────────────

  {
    id: 'zvs-holding',
    name: 'ZVS Holding AS',
    country: 'Slovakia',
    nato: true,
    parentGroup: 'Slovak state / Konstrukta Defence',
    scale: 'tier1',
    products: ['propellant charges', '155mm complete rounds', '152mm complete rounds', '122mm ammunition', 'mortar charges', 'cartridge cases'],
    calibres: ['155mm', '152mm', '122mm', '120mm mortar'],
    usp: 'Slovakia\'s main artillery ammunition manufacturer. Unique dual capability: both NATO 155mm and Soviet-legacy 122mm/152mm. Critical supplier for Eastern Europe transition from Soviet to NATO calibres. 155mm production expanded 2022-2024 with EU funding.',
    description: 'ZVS operates from Dubnica nad Váhom, producing complete artillery rounds and propellant charges. Dual-calibre capability (NATO and Soviet-legacy) makes ZVS highly relevant for Eastern European armies transitioning standards. Significant 155mm surge capacity commissioned for NATO requirements.',
    website: 'https://www.zvs.sk',
    contact: {
      general: '+421 42 442 1111',
      address: 'ZVS Holding AS, Centrum 29/29, 018 41 Dubnica nad Váhom, Slovakia',
      email: 'info@zvs.sk',
    },
    tags: ['155mm', '152mm', '122mm', 'propellant', 'Slovakia', 'NATO'],
  },

  // ─── BULGARIA ────────────────────────────────────────────────────────────────

  {
    id: 'arsenal-jsc',
    name: 'Arsenal JSCo',
    country: 'Bulgaria',
    nato: true,
    parentGroup: 'Bulgarian state / Dunarit Group',
    scale: 'tier1',
    products: ['122mm complete rounds', '152mm complete rounds', '82mm mortar', '120mm mortar', 'anti-tank rounds', 'small arms ammunition', 'projectile bodies'],
    calibres: ['152mm', '122mm', '120mm mortar', '82mm mortar'],
    usp: 'Bulgaria\'s largest ammunition manufacturer. 122mm and 152mm production at former Soviet-standard scale. Major exporter across Africa, Middle East, and Asia. Significant surge capacity critical for post-Ukraine demand.',
    description: 'Arsenal operates large-scale production of Soviet-legacy calibres (122mm, 152mm) alongside 82mm and 120mm mortar bombs. Export network spans over 50 countries. Has been a key supplier of 122mm BM-21 Grad rockets and 152mm projectiles for Ukraine support via European channels.',
    website: 'https://www.arsenal-bg.com',
    contact: {
      general: '+359 64 889 460',
      address: 'Arsenal JSCo, 9 Tsar Boris III Str., 5800 Pleven, Bulgaria',
      email: 'arsenal@arsenal-bg.com',
    },
    exportContact: 'export@arsenal-bg.com',
    tags: ['122mm', '152mm', 'mortar', 'Bulgaria', 'NATO', 'Soviet calibre'],
  },

  {
    id: 'vmz-sopot',
    name: 'VMZ State Enterprise',
    country: 'Bulgaria',
    nato: true,
    parentGroup: 'Bulgarian Ministry of Economy',
    scale: 'tier1',
    products: ['122mm BM-21 rockets', 'artillery rockets', 'projectiles', 'mortar bombs', 'propellant charges'],
    calibres: ['122mm', '107mm', '120mm mortar'],
    usp: 'Specialist in artillery rockets (BM-21 Grad 122mm) and conventional projectiles. Large-scale 122mm rocket production makes VMZ critical for current Eastern European demand. Also produces 107mm rockets for legacy systems.',
    description: 'Located in Sopot, Bulgaria. VMZ is one of Europe\'s major producers of 122mm BM-21 Grad rockets, alongside Arsenal. The factory has seen significant demand increase post-2022. Also produces conventional projectile bodies and mortar charges.',
    website: 'https://www.vmz.bg',
    contact: {
      general: '+359 3133 2041',
      address: 'VMZ State Enterprise, Sopot 4330, Plovdiv Region, Bulgaria',
      email: 'vmz@vmz.bg',
    },
    tags: ['122mm', 'rockets', 'BM-21', 'Bulgaria', 'NATO'],
  },

  // ─── ROMANIA ─────────────────────────────────────────────────────────────────

  {
    id: 'romarm',
    name: 'Romarm SA',
    country: 'Romania',
    nato: true,
    parentGroup: 'Romanian state',
    scale: 'tier1',
    products: ['152mm complete rounds', '122mm rockets', '120mm mortar', '82mm mortar', 'propellant charges', 'cartridge cases', 'projectile bodies'],
    calibres: ['155mm', '152mm', '122mm', '120mm mortar', '82mm mortar'],
    usp: 'Romania\'s state defence industrial group covering full ammunition spectrum. Subsidiary Tohan specialises in rockets. Subsidiary CUG Reşiţa produces howitzers. Major Soviet-to-NATO transition investment ongoing for 155mm production.',
    description: 'Romarm oversees Romania\'s defence industry including Tohan (rockets, mortar bombs), CUG Reşiţa (howitzers, 152mm), Explozia (propellants), and Carfil (vehicles). Romania is investing in 155mm production capability to meet NATO commitments while maintaining legacy 152mm lines.',
    website: 'https://www.romarm.ro',
    contact: {
      general: '+40 21 314 7960',
      address: 'Romarm SA, Str. Ion Câmpineanu Nr. 11, 010031 Bucharest, Romania',
      email: 'office@romarm.ro',
    },
    tags: ['152mm', '122mm', 'mortar', 'Romania', 'NATO'],
  },

  // ─── GREECE ──────────────────────────────────────────────────────────────────

  {
    id: 'hellenic-defence-systems',
    name: 'Hellenic Defence Systems SA (EAS)',
    country: 'Greece',
    nato: true,
    parentGroup: 'Greek state / ELVO consortium',
    scale: 'tier1',
    products: ['105mm complete rounds', '155mm complete rounds', '120mm mortar', '81mm mortar', 'propellant charges', 'fuzes'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar'],
    usp: 'Greece\'s primary ammunition manufacturer. Full qualification for M107 and M795 155mm HE projectiles. Strong regional position supplying Balkan and Middle East customers. 105mm M1 family for legacy US and British howitzers.',
    description: 'EAS produces artillery and mortar ammunition for the Hellenic Army and export. Facilities in Elefsis produce the full NATO 155mm and 105mm round families. Mortar production covers 81mm and 120mm. Greece\'s geography and political relationships provide strong position for Middle East/North Africa export.',
    website: 'https://www.eas.gr',
    contact: {
      general: '+30 210 556 1000',
      address: 'Hellenic Defence Systems SA, 17th km Athinon-Lamias Avenue, 14452 Athens, Greece',
      email: 'info@eas.gr',
    },
    exportContact: 'exports@eas.gr',
    tags: ['155mm', '105mm', 'mortar', 'Greece', 'NATO'],
  },

  // ─── SPAIN ───────────────────────────────────────────────────────────────────

  {
    id: 'expal-systems',
    name: 'Expal Systems SA',
    country: 'Spain',
    nato: true,
    parentGroup: 'Hanwha Defense (Korea) — acquired 2022',
    scale: 'tier1',
    products: ['155mm complete rounds', '105mm complete rounds', 'fuzes', 'mortar bombs', 'aircraft bombs', 'energetic materials', 'propellant charges'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar'],
    usp: 'Spain\'s largest ammunition manufacturer, now part of Hanwha group. ECIMOS electronic fuze widely adopted in Southern Europe and Latin America. Strong export position via Spain\'s political relationships in Africa and Latin America.',
    description: 'Expal operates factories in Murcia and Palencia producing complete 155mm and 105mm rounds, fuzes, and energetic materials. ECIMOS fuze technology and Spanish Army qualification. Post-Hanwha acquisition gains Korean supply chain integration. Major exporter to Latin America, Middle East, and Africa.',
    website: 'https://www.expal.com',
    contact: {
      general: '+34 91 597 7400',
      address: 'Expal Systems SA, Vía de los Poblados 3, Edificio 8, 28033 Madrid, Spain',
      email: 'info@expal.com',
    },
    exportContact: 'defense.export@expal.com',
    tags: ['155mm', '105mm', 'fuzes', 'Spain', 'NATO'],
  },

  {
    id: 'gd-santa-barbara',
    name: 'General Dynamics Santa Bárbara Sistemas',
    country: 'Spain',
    nato: true,
    parentGroup: 'General Dynamics Corporation',
    scale: 'prime',
    products: ['155mm howitzers', 'M109 upgrade', '155mm complete rounds', 'propellant charges', 'armoured vehicles'],
    calibres: ['155mm'],
    usp: 'Spanish-based GD division producing 155mm ammunition and howitzer systems (SBT-155 Pegaso). M109A5E+ upgrade programme for Spanish Army. Integrates GD global supply chain with Spanish manufacturing.',
    description: 'GD Santa Bárbara produces 155mm artillery ammunition alongside the SBT-155 Pegaso self-propelled howitzer. The Pegaso uses the M109 chassis with enhanced 155mm/52 cal barrel. Significant 155mm round production for Spanish Army and NATO customers.',
    website: 'https://www.gdscbs.es',
    contact: {
      general: '+34 91 205 6300',
      address: 'General Dynamics Santa Bárbara Sistemas SL, C/ Orense 4, 28020 Madrid, Spain',
      email: 'info@gdscbs.es',
    },
    tags: ['155mm', 'howitzer', 'Spain', 'NATO', 'General Dynamics'],
  },

  // ─── AUSTRIA / SWITZERLAND ───────────────────────────────────────────────────

  {
    id: 'hirtenberger-defence',
    name: 'Hirtenberger Defence GmbH',
    country: 'Austria',
    nato: false,
    parentGroup: 'Hirtenberger Group',
    scale: 'tier2',
    products: ['mortar bombs', '120mm mortar', '81mm mortar', '60mm mortar', 'cartridge components', 'fuze body blanks', 'base plug components'],
    calibres: ['120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'Austria\'s primary mortar ammunition manufacturer. Strong export position — Austria\'s neutral status facilitates trade with countries NATO members cannot easily supply. Precision machining capability for fuze body and component manufacturing.',
    description: 'Hirtenberger Defence (formerly Hirtenberger Schieß- und Zündmittel) produces mortar ammunition and components. Austria\'s neutrality allows supply to a wider range of customers. 120mm and 81mm HE, illuminating, and smoke rounds. Fuze and component machining for other European manufacturers.',
    website: 'https://www.hirtenberger.com/defence',
    contact: {
      general: '+43 2252 900 0',
      address: 'Hirtenberger Defence GmbH, Josef Eissner-Straße 1, 2552 Hirtenberg, Austria',
      email: 'defence@hirtenberger.at',
    },
    tags: ['mortar', '120mm', '81mm', 'Austria', 'neutral', 'components'],
  },

  {
    id: 'nitrochemie',
    name: 'Nitrochemie AG',
    country: 'Switzerland',
    nato: false,
    parentGroup: 'Nitrochemie Group (RUAG / Chemring JV)',
    scale: 'tier2',
    products: ['propellant powders', 'single-base propellants', 'double-base propellants', 'triple-base propellants', 'modular charges', 'gun propellants'],
    calibres: ['155mm', '105mm', 'mortar', 'small arms'],
    usp: 'Premium propellant manufacturer with Swiss precision quality. SWISS P propellants widely used in NATO STANAG charges. Joint ownership by RUAG and Chemring Group gives access to NATO qualification pipeline. Switzerland\'s neutrality allows export flexibility.',
    description: 'Nitrochemie in Wimmis produces propellant powders for artillery, mortar, and small arms. SWISS P series propellants meet NATO STANAG 4170 requirements. Jointly owned by RUAG (50%) and Chemring (50%), enabling broad NATO customer access despite Swiss neutrality.',
    website: 'https://www.nitrochemie.com',
    contact: {
      general: '+41 33 228 11 11',
      address: 'Nitrochemie Wimmis AG, 3752 Wimmis, Switzerland',
      email: 'info@nitrochemie.ch',
    },
    tags: ['propellants', 'Switzerland', 'neutral', 'STANAG'],
  },

  // ─── TURKEY ──────────────────────────────────────────────────────────────────

  {
    id: 'mkek',
    name: 'Makina ve Kimya Endüstrisi AS (MKEK)',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Turkish state (SSB)',
    scale: 'prime',
    products: ['155mm complete rounds', '105mm complete rounds', '120mm mortar', '81mm mortar', 'propellant charges', 'fuzes', 'explosive fills', 'small arms ammunition'],
    calibres: ['155mm', '105mm', '120mm mortar', '81mm mortar', '60mm mortar'],
    usp: 'Turkey\'s largest ammunition manufacturer. Full spectrum from propellant powders to complete rounds. 155mm production qualifies for T-155 Fırtına howitzer. Rapidly expanding export via Turkey\'s assertive defence export programme.',
    description: 'MKEK operates across Turkey producing complete artillery and mortar rounds, fuzes, propellant powders, and explosives. Key programmes include T-155 Fırtına howitzer ammunition, 105mm for M101/M102 legacy systems, and 120mm for Israeli-Turkish mortar systems. Growing export to Africa and Central Asia.',
    website: 'https://www.mkek.gov.tr',
    contact: {
      general: '+90 312 395 4000',
      address: 'MKEK Genel Müdürlüğü, Tandoğan, 06330 Ankara, Turkey',
      email: 'mkek@mkek.gov.tr',
    },
    exportContact: 'ihracat@mkek.gov.tr',
    tags: ['155mm', '105mm', 'mortar', 'Turkey', 'NATO', 'prime'],
  },

  {
    id: 'roketsan',
    name: 'Roketsan AS',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Turkish state / ASELSAN JV',
    scale: 'tier1',
    products: ['122mm rockets', 'guided artillery rockets (TRLG-230)', 'smart micro munition', 'warheads', 'rocket motors', 'mortar bombs'],
    calibres: ['122mm', '107mm', '70mm', 'MLRS-class'],
    usp: 'Turkey\'s rocket and guided munition champion. TRLG-230 precision-guided rocket extends BM-21-compatible range to 70km with GPS/IMU guidance. Critical for Turkey\'s domestic and export ambitions across Middle East and Africa.',
    description: 'Roketsan designs and manufactures unguided and guided rockets, rocket motors, and warheads. TRLG-230 guided rocket for Turkish ÇNRA rocket artillery system. Produces 122mm rockets for BM-21 Grad compatible systems. Smart micro munition for extended mortar engagement ranges.',
    website: 'https://www.roketsan.com.tr',
    contact: {
      general: '+90 312 826 6000',
      address: 'Roketsan AS, Elmadag Tesisleri, Ankara, Turkey',
      email: 'info@roketsan.com.tr',
    },
    exportContact: 'export@roketsan.com.tr',
    tags: ['rockets', '122mm', 'guided', 'Turkey', 'NATO'],
  },

  {
    id: 'aselsan-fuzes',
    name: 'ASELSAN AS — Fuze Division',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Turkish Armed Forces Foundation (TSKGV)',
    scale: 'tier2',
    products: ['electronic fuzes', 'multi-option fuzes', 'proximity fuzes', 'mortar fuzes', 'electronic time fuzes'],
    calibres: ['155mm', '105mm', '120mm mortar'],
    usp: 'Turkey\'s electronics giant entering fuze market. ATOM electronic multi-option fuze locally developed to reduce dependency on Junghans/Diehl imports. Critical for Turkish ammunition sovereignty programme.',
    description: 'ASELSAN\'s fuze programme aims to replace imported fuzes with Turkish-developed ATOM (Advanced Technology Multi-Option) fuze. Electronic time, proximity, and PD modes in a single unit. Integration with MKEK 155mm rounds underway.',
    website: 'https://www.aselsan.com.tr',
    contact: {
      general: '+90 312 592 1000',
      address: 'ASELSAN AS, Mehmet Akif Ersoy Mahallesi 296. Cadde No:16, 06200 Yenimahalle, Ankara, Turkey',
      email: 'info@aselsan.com.tr',
    },
    tags: ['fuzes', 'Turkey', 'NATO', 'electronics'],
  },

  // ─── SERBIA (non-NATO) ───────────────────────────────────────────────────────

  {
    id: 'krusik',
    name: 'Krusik AD',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian state / Yugoimport',
    scale: 'tier1',
    products: ['122mm rockets', '128mm rockets', '155mm projectiles', '120mm mortar', '82mm mortar', 'anti-tank rounds', 'propellant charges'],
    calibres: ['155mm', '122mm', '128mm', '120mm mortar', '82mm mortar'],
    usp: 'One of Europe\'s largest artillery and rocket producers. Major 122mm BM-21 Grad rocket supplier globally. 155mm projectile production expanding. Serbia\'s non-NATO status enables supply to countries with restricted access to NATO manufacturers.',
    description: 'Located in Valjevo, Krusik produces rockets, artillery shells, mortar bombs, and propellant charges. 122mm rockets are a primary export product. 155mm shell production launched to meet European demand. Non-NATO status creates both opportunities (wider customer access) and constraints (some NATO end-user certificates restricted).',
    website: 'https://www.krusik.rs',
    contact: {
      general: '+381 14 220 100',
      address: 'Krusik AD, Vojvode Stepe 4, 14000 Valjevo, Serbia',
      email: 'export@krusik.rs',
    },
    exportContact: 'export@krusik.rs',
    tags: ['122mm', 'rockets', '155mm', 'mortar', 'Serbia', 'non-NATO'],
  },

  {
    id: 'sloboda-cacak',
    name: 'Sloboda AD Čačak',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian state / Yugoimport',
    scale: 'tier2',
    products: ['propellant charges', 'propellant powders', '122mm charges', '152mm charges', 'cartridge cases', 'explosive fills'],
    calibres: ['152mm', '122mm', '120mm mortar', '82mm mortar'],
    usp: 'Serbia\'s propellant and charge specialist. Key supplier for Krusik and broader Yugoslav-legacy ammunition system. Non-NATO position allows supply flexibility.',
    description: 'Sloboda produces propellant charges, cartridge cases, and explosive fills primarily for Soviet-legacy calibres. Works closely with Krusik for complete round production. Export customer base includes Middle East, Africa, and Asia.',
    website: 'https://www.sloboda.rs',
    contact: {
      general: '+381 32 370 100',
      address: 'Sloboda AD, Industrijska bb, 32000 Čačak, Serbia',
      email: 'sloboda@sloboda.rs',
    },
    tags: ['propellants', '122mm', '152mm', 'Serbia', 'non-NATO'],
  },

  {
    id: 'ppt-namenska',
    name: 'PPT Namenska AD',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian state / Yugoimport',
    scale: 'tier2',
    products: ['propellant powders', 'nitrocellulose', 'double-base propellants', 'small arms propellant', 'artillery propellant'],
    calibres: ['155mm', '122mm', '152mm', 'mortar', 'small arms'],
    usp: 'Serbia\'s primary nitrocellulose and propellant powder plant. Critical feedstock supplier for entire Yugoslav-successor ammunition industry. Competitive pricing vs. Western producers.',
    description: 'PPT Namenska in Lučani produces nitrocellulose and propellant powders for the Serbian defence industry and export. Supplies Krusik, Sloboda, and foreign customers. NC and double-base propellants for artillery, mortar, and small arms.',
    website: 'https://www.pptnamenska.rs',
    contact: {
      general: '+381 32 515 100',
      address: 'PPT Namenska AD, Lučani, 32240, Serbia',
      email: 'export@pptnamenska.rs',
    },
    tags: ['propellants', 'nitrocellulose', 'Serbia', 'non-NATO'],
  },

  // ─── BOSNIA & HERZEGOVINA (non-NATO) ────────────────────────────────────────

  {
    id: 'pobjeda-technology',
    name: 'Pobjeda Technology DD',
    country: 'Bosnia & Herzegovina',
    nato: false,
    parentGroup: 'BiH state / Grupa Pobjeda',
    scale: 'tier2',
    products: ['propellant powders', 'double-base propellants', 'gun propellants', 'rocket propellants', 'nitrocellulose'],
    calibres: ['155mm', '122mm', '120mm mortar', 'small arms'],
    usp: 'Former Yugoslav propellant production centre maintaining significant capacity. Competitive pricing and former Yugoslav-standard qualifications. Supplies both NATO and non-NATO customers.',
    description: 'Pobjeda in Goražde is one of the Western Balkans\' main propellant manufacturers. Production includes NC powder, double-base propellants, and rocket propellants. Qualified for NATO 155mm charge propellants. Customers include regional manufacturers and direct government exports.',
    website: 'https://www.pobjeda.ba',
    contact: {
      general: '+387 38 221 700',
      address: 'Pobjeda Technology DD, Vitkovići bb, 73000 Goražde, Bosnia & Herzegovina',
      email: 'export@pobjeda.ba',
    },
    tags: ['propellants', 'nitrocellulose', 'Bosnia', 'non-NATO'],
  },

  {
    id: 'bnt-tmih',
    name: 'BNT-TMiH DD',
    country: 'Bosnia & Herzegovina',
    nato: false,
    parentGroup: 'RS Ministry of Industry',
    scale: 'tier2',
    products: ['mortar bombs', '120mm mortar', '82mm mortar', '60mm mortar', 'projectile bodies', 'hand grenades'],
    calibres: ['120mm mortar', '82mm mortar', '60mm mortar'],
    usp: 'Republika Srpska entity manufacturer covering mortar and light ammunition. Competitive pricing. Former JNA production tooling maintains Soviet-standard calibre capability.',
    description: 'BNT operates from Novi Travnik producing mortar bombs and light weapons ammunition. Products include 82mm HE/smoke/illuminating and 120mm HE mortar bombs. Export customers in Africa and Middle East.',
    website: 'https://www.bnt.ba',
    contact: {
      general: '+387 30 795 100',
      address: 'BNT-TMiH DD, Braće Terzića bb, 70220 Novi Travnik, Bosnia & Herzegovina',
      email: 'bnt@bnt.ba',
    },
    tags: ['mortar', '82mm', '120mm', 'Bosnia', 'non-NATO'],
  },

  // ─── UKRAINE ─────────────────────────────────────────────────────────────────

  {
    id: 'shostka-chemical',
    name: 'Shostka State Chemical Plant',
    country: 'Ukraine',
    nato: false,
    parentGroup: 'Ukroboronprom',
    scale: 'tier2',
    products: ['propellant powders', 'nitrocellulose', 'rocket propellants', 'gun propellants'],
    calibres: ['152mm', '122mm', '120mm mortar', 'MLRS'],
    usp: 'Ukraine\'s primary NC and propellant producer. Critical supply chain node for Ukrainian domestic ammunition production. Major ongoing production commitment for active conflict requirements.',
    description: 'Shostka has historically been one of the largest propellant manufacturers in Eastern Europe. Produces nitrocellulose, double-base and triple-base powders for artillery and rocket systems. Currently operating at surge capacity for Ukrainian armed forces needs.',
    website: 'https://www.ukroboronprom.com.ua',
    contact: {
      general: '+380 44 490 6000',
      address: 'Ukroboronprom, vul. Povitroflotskyi 28, Kyiv, Ukraine',
      email: 'ukroboronprom@ukroboronprom.com.ua',
    },
    tags: ['propellants', 'nitrocellulose', 'Ukraine', 'non-NATO'],
  },

  {
    id: 'pavlograd-chemical',
    name: 'Pavlograd Chemical Plant',
    country: 'Ukraine',
    nato: false,
    parentGroup: 'Ukroboronprom',
    scale: 'tier1',
    products: ['solid rocket propellants', 'MLRS charges', 'ballistic missile propellants', 'artillery propellant charges'],
    calibres: ['122mm MLRS', '220mm', '300mm MLRS', 'ballistic'],
    usp: 'Ukraine\'s primary solid rocket motor producer. Critical for MLRS ammunition (Grad, Smerch, Uragan) production. Unique capability for large-diameter solid propellant casting.',
    description: 'Pavlograd produces solid propellant rocket motors and charges for Ukraine\'s MLRS systems. Currently one of the most critical Ukrainian defence industrial facilities. Produces propellant for all Soviet-legacy rocket artillery calibres and supports HIMARS-compatible rocket development.',
    website: 'https://www.pcz.com.ua',
    contact: {
      general: '+380 5632 22555',
      address: 'Pavlograd Chemical Plant, Pavlograd, Dnipropetrovsk Oblast, Ukraine',
      email: 'info@pcz.com.ua',
    },
    tags: ['propellants', 'rockets', 'MLRS', 'Ukraine', 'non-NATO'],
  },

  // ─── FINLAND ─────────────────────────────────────────────────────────────────

  {
    id: 'forcit',
    name: 'Forcit Defence Oy',
    country: 'Finland',
    nato: true,
    parentGroup: 'Forcit Group',
    scale: 'tier2',
    products: ['explosive fills', 'TNT', 'COMP-B', 'HE projectile fills', 'demolition charges', 'pyrotechnics'],
    calibres: ['155mm', '120mm mortar', '81mm mortar'],
    usp: 'Finland\'s sole explosive fill manufacturer. TNT and Comp-B production qualified for NATO calibres. Critical for Finnish Artillery readiness. Joined NATO supply chain 2023.',
    description: 'Forcit operates the Hanko facility in Finland producing TNT, Composition B, and other explosive fills for artillery projectiles, demolition charges, and mines. Joined NATO as Finland acceded. Now supporting NATO ammunition surge capacity as a STANAG-qualified supplier.',
    website: 'https://www.forcit.fi/defence',
    contact: {
      general: '+358 19 220 5500',
      address: 'Forcit Oy, Kantvik, 02430 Masala, Finland',
      email: 'defence@forcit.fi',
    },
    tags: ['explosives', 'TNT', 'fills', 'Finland', 'NATO'],
  },

  // ─── NETHERLANDS / PORTUGAL ──────────────────────────────────────────────────

  {
    id: 'knds-netherlands',
    name: 'KNDS Netherlands BV',
    country: 'Netherlands',
    nato: true,
    parentGroup: 'KNDS Group',
    scale: 'tier2',
    products: ['155mm projectile components', 'steel bodies', 'fuze assembly', 'propellant charge assembly'],
    calibres: ['155mm'],
    usp: 'Dutch KNDS facility for 155mm component manufacturing and charge assembly. Short supply chain for Benelux NATO customers. PzH 2000 ammunition focal point.',
    description: 'KNDS Netherlands assembles 155mm propellant charges and components for PzH 2000 and other 155mm howitzers in Netherlands and Belgian service. Feeds the PzH 2000 surge programme for Ukraine support.',
    website: 'https://www.knds.nl',
    contact: {
      general: '+31 88 662 0000',
      address: 'KNDS Netherlands BV, Amersfoort, Netherlands',
      email: 'info@knds.nl',
    },
    tags: ['155mm', 'Netherlands', 'NATO', 'KNDS'],
  },


  // ─── UNITED STATES ──────────────────────────────────────────────────────────

  {
    id: 'general-dynamics-ots',
    name: 'General Dynamics Ordnance and Tactical Systems',
    country: 'United States',
    nato: true,
    parentGroup: 'General Dynamics Corporation',
    scale: 'prime',
    products: ['155mm complete rounds', '105mm complete rounds', '120mm mortar', '81mm mortar', '60mm mortar', 'propellant charges', 'cartridge cases', 'projectiles', 'fuzes', 'grenades'],
    calibres: ['155mm', '105mm', '120mm', '81mm', '60mm', '30mm', '20mm'],
    usp: 'Largest US artillery ammunition manufacturer. Primary supplier to US Army for 155mm M795/M549A1 projectiles and M231/M232 propellant charges. Qualified for NATO STANAG and allied export programmes.',
    description: 'GD-OTS operates multiple US Army-owned Government-Owned Contractor-Operated (GOCO) facilities including Scranton Army Ammunition Plant (PA) and Marion Engineering Center (OH). Produces the full 155mm family — projectiles, charges, fuzes, and complete rounds — for US Army and FMS customers. Strong FMS track record across Africa, Asia, and Latin America.',
    website: 'https://www.gdots.com',
    contact: {
      general: '+1 727-578-8100',
      address: 'General Dynamics OTS, 11399 16th Court N, Suite 200, St. Petersburg, FL 33716, USA',
      email: 'ots@gd.com',
    },
    exportContact: 'gd-ots.exports@gd.com',
    tags: ['155mm', '105mm', 'mortar', 'propellant', 'USA', 'NATO', 'FMS', 'prime', 'GOCO'],
  },

  {
    id: 'bae-systems-us',
    name: 'BAE Systems Inc. — Ordnance Systems',
    country: 'United States',
    nato: true,
    parentGroup: 'BAE Systems plc',
    scale: 'prime',
    products: ['155mm M549A1 RAP', '155mm Excalibur guidance kit', 'M795 projectile', 'XM1113 rocket-assisted projectile', 'naval gun ammunition', 'precision guidance fuze PGK M1156'],
    calibres: ['155mm', '127mm', '76mm', '5"/54'],
    usp: 'Developer and producer of Excalibur GPS-guided 155mm projectile — the benchmark for precision artillery. M1156 Precision Guidance Kit (PGK) turns standard M795 projectiles into precision rounds at fraction of cost.',
    description: 'BAE Systems Inc. Ordnance Systems (Holston Army Ammunition Plant, TN; McAlester Army Ammunition Plant, OK) produces energetics, propellants, and precision munitions. Lead contractor for Excalibur 155mm GPS-guided projectile fielded in 10+ countries. PGK M1156 gives African/developing-nation customers an affordable precision option.',
    website: 'https://www.baesystems.com/en-us/our-company/inc-businesses/platforms-and-services/ordnance-systems',
    contact: {
      general: '+1 603-885-4321',
      address: 'BAE Systems Inc., 621 Northwest 53rd Street, Suite 100, Boca Raton, FL 33487, USA',
      email: 'usinfo@baesystems.com',
    },
    exportContact: 'international.sales@baesystems.com',
    tags: ['155mm', 'Excalibur', 'precision', 'PGK', 'USA', 'NATO', 'prime', 'GPS-guided'],
  },

  {
    id: 'olin-winchester-defence',
    name: 'Olin Corporation — Winchester Defence',
    country: 'United States',
    nato: true,
    parentGroup: 'Olin Corporation',
    scale: 'tier1',
    products: ['5.56mm ammunition', '7.62mm ammunition', '9mm ammunition', '.50 cal ammunition', '20mm ammunition', 'small arms cartridges', 'brass cartridge cases'],
    calibres: ['5.56mm', '7.62mm', '9mm', '.50 BMG', '12.7mm', '20mm'],
    usp: 'Dominant US small arms ammunition manufacturer. Primary supplier to US military for M855A1 EPR (Enhanced Performance Round) and M80A1 7.62mm. Lake City Army Ammunition Plant GOCO operator.',
    description: 'Winchester Defence operates the Lake City Army Ammunition Plant (Independence, MO) — the US military\'s primary small calibre ammunition facility producing billions of rounds annually. Qualified exporter to NATO allies and US FMS customers. Competitive pricing advantage versus European suppliers for high-volume contracts in Africa and Latin America.',
    website: 'https://www.winchester.com/winchester-defense',
    contact: {
      general: '+1 618-258-2000',
      address: 'Winchester, 600 Powder Mill Road, East Alton, IL 62024, USA',
      email: 'defence@winchester.com',
    },
    exportContact: 'defence.exports@winchester.com',
    tags: ['small arms', '5.56mm', '7.62mm', '.50 cal', 'USA', 'NATO', 'Lake City', 'GOCO'],
  },

  {
    id: 'aerojet-rocketdyne',
    name: 'Aerojet Rocketdyne — Defense Systems',
    country: 'United States',
    nato: true,
    parentGroup: 'L3Harris Technologies',
    scale: 'prime',
    products: ['rocket motors', 'propulsion systems', 'GRAD-compatible rockets', '2.75" rocket warheads', 'propellants', 'solid rocket propellant'],
    calibres: ['70mm (2.75")', '80mm', '122mm GRAD-compatible'],
    usp: 'Leading US producer of unguided and guided rocket propulsion. 2.75" (70mm) Hydra rocket motors widely used across Africa in helicopter and ground-launched configurations. Solid propellant production for multiple NATO programmes.',
    description: 'Aerojet Rocketdyne (now part of L3Harris) produces rocket motors for Hydra-70 system used by US Army, USMC, and allied nations. African militaries operating UH-1, Bell 412, or MD-500 platforms can use Hydra-70 without licence complexity. Competitive FMS pricing and strong DoD export track record.',
    website: 'https://www.l3harris.com',
    contact: {
      general: '+1 916-355-4000',
      address: 'Aerojet Rocketdyne, 2001 Aerojet Road, Rancho Cordova, CA 95742, USA',
      email: 'contactus@aerojet.com',
    },
    tags: ['rockets', '70mm', 'propulsion', 'Hydra-70', 'USA', 'NATO', 'prime'],
  },

  {
    id: 'northrop-grumman-armament',
    name: 'Northrop Grumman — Armament Systems',
    country: 'United States',
    nato: true,
    parentGroup: 'Northrop Grumman Corporation',
    scale: 'prime',
    products: ['M230 30mm cannon', 'M197 20mm gatling', 'MK44 Bushmaster II', '30mm ammunition', '20mm APDS-T', '40mm AGL ammunition', 'autocannon systems'],
    calibres: ['30mm', '20mm', '40mm', '25mm'],
    usp: 'Developer of M230 chain gun used on AH-64 Apache and AC-130 gunships. MK44 Bushmaster II (30mm) used in 30+ countries. Full ammunition production capability for all cannon calibres.',
    description: 'Northrop Grumman Armament Systems (formerly Alliant Techsystems) produces autocannon and ammunition systems for air and ground platforms. Strong African presence through Apache helicopter FMS deals. M230 30mm is the standard cannon for AH-64 operators globally. Competitive for countries procuring armed helicopters or IFVs.',
    website: 'https://www.northropgrumman.com/what-we-do/land/armament-systems',
    contact: {
      general: '+1 703-280-2900',
      address: 'Northrop Grumman, 2980 Fairview Park Drive, Falls Church, VA 22042, USA',
      email: 'bd@northropgrumman.com',
    },
    exportContact: 'internationalbd@northropgrumman.com',
    tags: ['30mm', '20mm', 'autocannon', 'Bushmaster', 'Apache', 'USA', 'NATO', 'prime'],
  },

  {
    id: 'alliant-techsystems-atk',
    name: 'Alliant Techsystems (ATK) / Orbital ATK — Ammunition',
    country: 'United States',
    nato: true,
    parentGroup: 'Northrop Grumman Corporation',
    scale: 'prime',
    products: ['120mm tank ammunition', 'M829 APFSDS', 'M830 HEAT-MP', '155mm guided projectiles', '40mm low-velocity grenades', '40mm HEDP', 'shoulder-launched rockets', 'SMAW', 'AT4'],
    calibres: ['120mm', '105mm', '155mm', '40mm', '84mm'],
    usp: 'Produces US Army\'s entire 120mm tank ammunition family for M1A1/A2 Abrams. M829A4 APFSDS-T is the current-generation anti-armour round. Key supplier for African nations procuring Abrams through FMS or seeking compatible ammunition.',
    description: 'Now operating as Northrop Grumman Innovation Systems, this facility (Plymouth, MN; Radford, VA) is the primary US source for 120mm kinetic energy penetrators and HEAT rounds. Also produces AT4 and SMAW shoulder-launched systems under licence from Saab and NAMMO. Competitive sourcing option for Lusophone African nations through FMS channels.',
    website: 'https://www.northropgrumman.com',
    contact: {
      general: '+1 703-280-2900',
      address: 'Northrop Grumman Innovation Systems, 4700 Nathan Lane N, Plymouth, MN 55442, USA',
      email: 'bd@northropgrumman.com',
    },
    tags: ['120mm', 'tank ammunition', 'APFSDS', 'AT4', 'SMAW', '40mm', 'USA', 'NATO'],
  },

  {
    id: 'elbit-systems-america',
    name: 'Elbit Systems of America',
    country: 'United States',
    nato: true,
    parentGroup: 'Elbit Systems Ltd.',
    scale: 'tier1',
    products: ['night vision devices', 'JPEQ AN/PVS-14', 'thermal sights', 'fire control systems', 'mortar fire control', 'precision guidance kits', 'EW systems'],
    calibres: ['universal — fire control / optics'],
    usp: 'US-manufactured Israeli-origin night vision and fire control technology. AN/PVS-14 monocular is NATO-standard ITAR-controlled night vision. Mortar fire control systems (MORFIRE) in service with 20+ countries.',
    description: 'Elbit Systems of America (Fort Worth, TX) manufactures US-origin ITAR-controlled variants of Elbit\'s core product lines. Particularly strong in night vision and fire control for infantry and indirect fire systems. FMS-eligible and direct commercial sales available for allied nations. Competitive against European NV suppliers on price.',
    website: 'https://www.elbitsystems-us.com',
    contact: {
      general: '+1 817-234-6799',
      address: 'Elbit Systems of America, 4700 Marine Creek Pkwy, Fort Worth, TX 76179, USA',
      email: 'usa@elbitsystems.com',
    },
    exportContact: 'international@elbitsystems-us.com',
    tags: ['night vision', 'fire control', 'optics', 'mortar', 'USA', 'ITAR', 'FMS'],
  },

  // ─── CANADA ──────────────────────────────────────────────────────────────────

  {
    id: 'smc-canada',
    name: 'SNC-Lavalin / General Dynamics Ordnance — Canada',
    country: 'Canada',
    nato: true,
    parentGroup: 'General Dynamics Corporation',
    scale: 'tier1',
    products: ['155mm projectiles', '105mm projectiles', 'propellant charges', 'mortar bombs', 'small arms ammunition', 'fuzes'],
    calibres: ['155mm', '105mm', '81mm mortar', '60mm mortar', '5.56mm', '7.62mm'],
    usp: 'Canadian Government-qualified NATO ammunition manufacturer. Valcartier facility (QC) produces 155mm and small arms ammunition for Canadian Armed Forces and allied export. Competitive for non-EU buyers avoiding European export restrictions.',
    description: 'General Dynamics OTS Canada (Quebec City) operates the Valcartier facility producing artillery and mortar ammunition for CAF and NATO FMS customers. Canadian production offers an alternative to European sourcing with shorter lead times for Atlantic-facing African markets. Qualified under Canadian Export and Import Permits Act.',
    website: 'https://www.gdots.com',
    contact: {
      general: '+1 418-844-4000',
      address: 'GD OTS Canada, 101 route de la Rive, Quebec, QC G0A 4V0, Canada',
      email: 'canada@gdots.com',
    },
    tags: ['155mm', '105mm', 'Canada', 'NATO', 'Valcartier', 'FMS', 'artillery'],
  },

  {
    id: 'colt-canada',
    name: 'Colt Canada Corporation',
    country: 'Canada',
    nato: true,
    parentGroup: 'Colt Defense LLC',
    scale: 'tier1',
    products: ['C7 assault rifle', 'C8 carbine', 'C9 LMG', '5.56mm rifles', 'upper receivers', 'barrels', 'rifle components'],
    calibres: ['5.56mm', '7.62mm'],
    usp: 'Manufacturer of Canada\'s service rifle (C7/C8) — NATO-qualified STANAG 4172 compatible. Widely exported to allied nations including Netherlands, Denmark, and Norway. Competitive alternative to HK or FN for smaller African procurement contracts.',
    description: 'Colt Canada (Kitchener, Ontario) is the Canadian Armed Forces\' primary rifle supplier producing the C7A2 and C8A3 families. Products are interoperable with all NATO STANAG 4172 magazines and accessories. Export track record includes Netherlands, Denmark, New Zealand, and UK SF. Logistics advantage for Atlantic-facing customers versus European or US suppliers.',
    website: 'https://www.coltcanada.com',
    contact: {
      general: '+1 519-578-6900',
      address: 'Colt Canada Corporation, 140 Fountain Street North, Kitchener, ON N2H 6N3, Canada',
      email: 'info@coltcanada.com',
    },
    exportContact: 'exports@coltcanada.com',
    tags: ['5.56mm', 'rifle', 'C7', 'C8', 'Canada', 'NATO', 'STANAG', 'small arms'],
  },

  {
    id: 'diemaco-smc',
    name: 'SNC-Lavalin Defence Products (Diemaco)',
    country: 'Canada',
    nato: true,
    parentGroup: 'SNC-Lavalin Group',
    scale: 'tier2',
    products: ['ammunition components', 'propellant manufacture', 'pyrotechnics', 'signal cartridges', 'flares', 'smoke grenades'],
    calibres: ['multi-calibre components', 'pyrotechnic devices'],
    usp: 'Canadian pyrotechnics and propellant specialist. Signal cartridges and flares qualified to NATO standards. Competitive pricing for African militaries versus European alternatives with favourable Canadian export policy.',
    description: 'SNC-Lavalin Defence Products produces pyrotechnic devices, signal cartridges, and propellant components under Canadian export licensing (relatively permissive for allied developing nations). Good logistics position for West Africa through Halifax and Montreal ports. Products include smoke screening, illumination rounds, and training ammunition.',
    website: 'https://www.snclavalin.com/en/defence',
    contact: {
      general: '+1 514-393-1000',
      address: 'SNC-Lavalin, 455 René-Lévesque Blvd W, Montreal, QC H2Z 1Z3, Canada',
      email: 'defence@snclavalin.com',
    },
    tags: ['pyrotechnics', 'flares', 'propellant', 'Canada', 'NATO', 'signal cartridges'],
  },

  // ─── BRAZIL ──────────────────────────────────────────────────────────────────

  {
    id: 'imbel-brazil',
    name: 'IMBEL — Indústria de Material Bélico do Brasil',
    country: 'Brazil',
    nato: false,
    parentGroup: 'Brazilian Army (state-owned)',
    scale: 'tier1',
    products: ['7.62mm rifle ammunition', '5.56mm ammunition', '9mm pistol ammunition', '.40 cal ammunition', '40mm grenade ammunition', 'IA2 assault rifle', 'MD97 rifle', 'MD-2 pistol', 'MD-6 submachine gun'],
    calibres: ['5.56mm', '7.62mm', '9mm', '.40 cal', '40mm'],
    usp: 'Brazil\'s state-owned defence manufacturer. Largest ammunition producer in South America. IA2 rifle in service with Brazilian Army and exported to Bolivia, Ecuador, and others. Competitive pricing — no ITAR restrictions. Portuguese-speaking Lusophone market advantage.',
    description: 'IMBEL (Fábrica de Itajubá, MG) is Brazil\'s primary state small arms and ammunition manufacturer under Army control. Produces the IA2 assault rifle (5.56mm and 7.62mm variants), pistols, and full small arms ammunition range. Key advantage for Lusophone African customers: shared Portuguese language, no US/EU export licensing friction, lower costs, and cultural familiarity. Active exporter to Bolivia, Ecuador, Paraguay, Peru, and increasingly African nations.',
    website: 'https://www.imbel.gov.br',
    contact: {
      general: '+55 35 3629-2000',
      address: 'IMBEL, Av. das Indústrias, 2000, Itajubá, MG 37504-000, Brazil',
      email: 'comercial@imbel.gov.br',
    },
    exportContact: 'exportacao@imbel.gov.br',
    tags: ['5.56mm', '7.62mm', 'rifle', 'IA2', 'Brazil', 'small arms', 'Lusophone', 'South America', 'no ITAR'],
  },

  {
    id: 'avibras-brazil',
    name: 'Avibras Indústria Aeroespacial S.A.',
    country: 'Brazil',
    nato: false,
    parentGroup: 'Avibras Group',
    scale: 'tier1',
    products: ['ASTROS II MLRS', '127mm rockets', '180mm rockets', '300mm rockets', 'rocket artillery', 'missile systems', 'AV-SS 40 surface-to-surface missiles'],
    calibres: ['127mm', '180mm', '300mm', '40mm'],
    usp: 'Developer of ASTROS II — Brazil\'s Multiple Launch Rocket System, exported to Saudi Arabia, Iraq, Qatar, and Malaysia. 127mm to 300mm range coverage. No ITAR restrictions. Competitive for African nations seeking MLRS capability outside NATO channels.',
    description: 'Avibras (São José dos Campos, SP) developed ASTROS II MLRS deployed in Gulf War and Saudi Arabia. System covers 9km to 300km range depending on rocket type. Major export successes demonstrate credibility. For Africa, ASTROS provides indirect fire capability without NATO export control complexity. Brazil\'s DEC (Despacho de Exportação Controlada) licensing is generally more permissive than US/EU for developing nations with stable governance.',
    website: 'https://www.avibras.com.br',
    contact: {
      general: '+55 12 3947-4000',
      address: 'Avibras Indústria Aeroespacial, Rod. Presidente Dutra km 138, São José dos Campos, SP 12210-760, Brazil',
      email: 'avibras@avibras.com.br',
    },
    exportContact: 'exportacao@avibras.com.br',
    tags: ['rockets', 'MLRS', 'ASTROS', '127mm', '300mm', 'Brazil', 'South America', 'no ITAR'],
  },

  {
    id: 'taurus-armas',
    name: 'Taurus Armas S.A.',
    country: 'Brazil',
    nato: false,
    parentGroup: 'Taurus Armas S.A.',
    scale: 'tier1',
    products: ['9mm pistols', '.40 cal pistols', '.45 ACP pistols', '.357 revolvers', 'CT30 submachine gun', 'TS9 service pistol', 'Carabina .40', 'shotguns'],
    calibres: ['9mm', '.40 cal', '.45 ACP', '.357', '12ga'],
    usp: 'World\'s second-largest handgun manufacturer. TS9 service pistol in Brazilian Police and military. No ITAR. Extremely competitive pricing — 40-60% below European/US equivalents. Exported to 80+ countries including several African police forces.',
    description: 'Taurus Armas (Porto Alegre, RS; Bainbridge, GA USA) produces pistols, revolvers, and carbines at very competitive price points. Already exported to Mozambique, Angola, and West African police/security forces. For Arkmurus, Taurus represents a cost-effective Lusophone-aligned option for police modernisation contracts where premium European brands are cost-prohibitive. US subsidiary means partial ITAR applicability on US-made components — clarify on case basis.',
    website: 'https://www.taurusarmas.com.br',
    contact: {
      general: '+55 51 3357-1000',
      address: 'Taurus Armas, Av. do Forte, 2357, Porto Alegre, RS 91360-001, Brazil',
      email: 'contato@taurus.com.br',
    },
    exportContact: 'international@taurus.com.br',
    tags: ['pistol', 'revolver', '9mm', '.40 cal', 'Brazil', 'Lusophone', 'South America', 'police', 'affordable'],
  },

  // ─── ARGENTINA ───────────────────────────────────────────────────────────────

  {
    id: 'fabricaciones-militares',
    name: 'Fabricaciones Militares (DGFM)',
    country: 'Argentina',
    nato: false,
    parentGroup: 'Argentine Ministry of Defence',
    scale: 'tier1',
    products: ['7.62mm ammunition', '5.56mm ammunition', '9mm ammunition', '.308 Winchester', 'mortar bombs 60mm', 'mortar bombs 81mm', '105mm artillery shells', 'FARA 83 assault rifle', 'explosives', 'propellants'],
    calibres: ['5.56mm', '7.62mm', '9mm', '60mm mortar', '81mm mortar', '105mm'],
    usp: 'Argentina\'s state defence manufacturer with full small arms ammunition and artillery ammunition capability. DGFM (Dirección General de Fabricaciones Militares) is one of South America\'s most complete defence industrial complexes. Competitive for African buyers: no ITAR, no EU restrictions, competitive pricing.',
    description: 'DGFM operates Villa María (Córdoba) ammunition plant producing small arms ammunition and artillery shells. Also produces explosives and propellants at Azul facility. Export history includes regional Latin American customers and some African contacts. Argentina\'s export licensing (CITAR equivalent, Ministerio de Defensa) is generally accessible for stable African governments. Logistics via Buenos Aires and Rosario ports.',
    website: 'https://www.fabricacionesmilitares.gob.ar',
    contact: {
      general: '+54 11 4814-4900',
      address: 'DGFM, Av. Córdoba 4440, C1414BAB Buenos Aires, Argentina',
      email: 'info@fabricacionesmilitares.gob.ar',
    },
    tags: ['5.56mm', '7.62mm', 'mortar', '105mm', 'Argentina', 'South America', 'state-owned', 'no ITAR'],
  },

  // ─── COLOMBIA ────────────────────────────────────────────────────────────────

  {
    id: 'indumil-colombia',
    name: 'INDUMIL — Industria Militar de Colombia',
    country: 'Colombia',
    nato: false,
    parentGroup: 'Colombian Ministry of National Defence',
    scale: 'tier2',
    products: ['5.56mm ammunition', '7.62mm ammunition', '9mm ammunition', '.50 BMG', '40mm grenades', '60mm mortar bombs', '81mm mortar bombs', 'IGA assault rifle', 'Galil 5.56mm (licence)'],
    calibres: ['5.56mm', '7.62mm', '9mm', '.50 BMG', '40mm', '60mm mortar', '81mm mortar'],
    usp: 'Colombia\'s state defence manufacturer with decades of US-backed modernisation. Produces Galil-type rifles under IMI licence. Small arms ammunition competitive for Spanish-speaking African markets (Equatorial Guinea). No ITAR — Colombian export licensing relatively accessible.',
    description: 'INDUMIL (El Palacio plant, Cajicá; La Salina plant) produces full small arms ammunition range and light weapons. Unique advantage: battle-tested products from Colombian Armed Forces counter-insurgency operations — extremely high reliability standards. Export interest in West Africa through Brazil/Colombia south-south cooperation frameworks. Galil-pattern rifles familiar to many African armies.',
    website: 'https://www.indumil.gov.co',
    contact: {
      general: '+57 601 650-7800',
      address: 'INDUMIL, Carrera 44 No. 26-21, Bogotá D.C., Colombia',
      email: 'comunicaciones@indumil.gov.co',
    },
    exportContact: 'exportaciones@indumil.gov.co',
    tags: ['5.56mm', '7.62mm', '.50 cal', 'mortar', 'Colombia', 'South America', 'Galil', 'no ITAR'],
  },

];

// ─── Search / Query Functions ─────────────────────────────────────────────────

/**
 * Search the OEM database by keyword(s).
 * Matches against: name, country, products, calibres, tags, usp, description.
 */
export function searchOEMs(query) {
  if (!query || !query.trim()) return OEM_DATABASE;
  const terms = query.toLowerCase().trim().split(/\s+/);
  return OEM_DATABASE.filter(oem => {
    const haystack = [
      oem.name, oem.country, oem.usp, oem.description,
      ...(oem.products || []),
      ...(oem.calibres || []),
      ...(oem.tags || []),
      oem.parentGroup || '',
      oem.nato ? 'nato' : 'non-nato',
    ].join(' ').toLowerCase();
    return terms.every(t => haystack.includes(t));
  });
}

/**
 * Get a single OEM by ID.
 */
export function getOEM(id) {
  return OEM_DATABASE.find(o => o.id === id);
}

// ─── Country flag map ─────────────────────────────────────────────────────────
const FLAG = {
  'Germany': '🇩🇪', 'France': '🇫🇷', 'United Kingdom': '🇬🇧',
  'Norway': '🇳🇴', 'Sweden': '🇸🇪', 'Italy': '🇮🇹',
  'Belgium': '🇧🇪', 'Poland': '🇵🇱', 'Czech Republic': '🇨🇿',
  'Slovakia': '🇸🇰', 'Bulgaria': '🇧🇬', 'Romania': '🇷🇴',
  'Greece': '🇬🇷', 'Spain': '🇪🇸', 'Austria': '🇦🇹',
  'Switzerland': '🇨🇭', 'Turkey': '🇹🇷', 'Serbia': '🇷🇸',
  'Bosnia & Herzegovina': '🇧🇦', 'Ukraine': '🇺🇦',
  'Finland': '🇫🇮', 'Netherlands': '🇳🇱',
  'United States': '🇺🇸', 'Canada': '🇨🇦',
  'Brazil': '🇧🇷', 'Argentina': '🇦🇷', 'Colombia': '🇨🇴',
};

const SCALE_EMOJI = { prime: '🏭', tier1: '🔩', tier2: '⚙️', specialist: '🎯' };
const SCALE_LABEL = { prime: 'Prime Contractor', tier1: 'Tier 1', tier2: 'Tier 2 Component', specialist: 'Specialist' };

/**
 * Group OEMs by country.
 */
export function groupByCountry(oems = OEM_DATABASE) {
  const groups = {};
  for (const oem of oems) {
    if (!groups[oem.country]) groups[oem.country] = [];
    groups[oem.country].push(oem);
  }
  return groups;
}

/**
 * Format a card for a single OEM (for Telegram).
 * compact=true → single directory row
 * compact=false → full profile card for browse/search view
 */
export function formatOEMCard(oem, compact = false) {
  const flag     = FLAG[oem.country] || '🌍';
  const nato     = oem.nato ? '🔵 NATO' : '🟡 Non-NATO';
  const scaleIco = SCALE_EMOJI[oem.scale] || '⚙️';
  const scaleLbl = SCALE_LABEL[oem.scale] || '';

  if (compact) {
    return `${scaleIco} ${oem.name}  ·  ${oem.products.slice(0, 2).join(' · ')}\n`;
  }

  // ── Full profile card ──────────────────────────────────────────────────────
  let msg = `━━━━━━━━━━━━━━━━━━━━━━━━\n`;
  msg += `${scaleIco} *${oem.name}*\n`;
  msg += `${flag} ${oem.country}  ·  ${nato}  ·  ${scaleLbl}`;
  if (oem.parentGroup && oem.parentGroup !== oem.name) msg += `\n_${oem.parentGroup}_`;
  msg += '\n\n';

  // Products & calibres
  msg += `🔩 ${oem.products.slice(0, 5).join('  ·  ')}`;
  if (oem.products.length > 5) msg += `  _(+${oem.products.length - 5})_`;
  msg += `\n🎯 ${oem.calibres.join('  ·  ')}\n\n`;

  // Key strength
  msg += `📌 _${oem.usp}_\n`;

  // Description
  if (oem.description) msg += `\n${oem.description}\n`;

  // Contact block
  msg += `\n🌐 ${oem.website}\n`;
  const contactLine = [
    oem.contact?.general ? `☎️ ${oem.contact.general}` : null,
    oem.contact?.email   ? `✉️ ${oem.contact.email}` : null,
  ].filter(Boolean).join('  ·  ');
  if (contactLine) msg += `${contactLine}\n`;
  if (oem.exportContact) msg += `📋 _Export: ${oem.exportContact}_\n`;
  if (oem.contact?.address) msg += `🏢 _${oem.contact.address}_`;

  return msg;
}

/**
 * Format the full directory grouped by country.
 */
export function formatOEMList(oems, title = 'OEM DIRECTORY') {
  const groups = groupByCountry(oems);
  const ts     = new Date().toISOString().slice(0, 10);

  let msg = `🏭 *ARKMURUS — ${title}*\n_${ts} · ${oems.length} manufacturers_\n`;
  msg += '━━━━━━━━━━━━━━━━━━━━━━━━\n\n';

  for (const [country, list] of Object.entries(groups).sort()) {
    const natoFlag = list[0].nato ? '🔵' : '🟡';
    msg += `*${natoFlag} ${country}* (${list.length})\n`;
    for (const oem of list) {
      const ico = SCALE_EMOJI[oem.scale] || '🔩';
      msg += `  ${ico} ${oem.name}\n`;
      msg += `     ${oem.products.slice(0, 2).join(' · ')}\n`;
    }
    msg += '\n';
  }

  msg += `_/oem [query] for details · /oem [country] · /oem [calibre]_`;
  return msg;
}
