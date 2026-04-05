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

  // ─── ISRAEL ──────────────────────────────────────────────────────────────────

  {
    id: 'elbit-systems',
    name: 'Elbit Systems Ltd',
    country: 'Israel',
    nato: false,
    parentGroup: null,
    scale: 'prime',
    products: ['armoured vehicles', 'UAVs', 'C4ISR systems', 'fire control', 'EW systems', 'night vision', 'ammunition', 'artillery systems', 'border surveillance', 'naval systems', 'small arms optics'],
    usp: 'Israel\'s largest private defence company. Active across sub-Saharan Africa (Kenya, Ghana, Nigeria, Rwanda, Cameroon). Hermes 900/450 UAVs deployed in 10+ African countries. Strong after-sales support networks. Not ITAR-controlled (Israeli export licensing).',
    description: 'Full-spectrum defence prime with deep Africa footprint. Products include Hermes series UAVs, LAND TORCH fire control, TORCH-X C2, Lynx APC, ATMOS 155mm howitzer, and Elbit Helmet Mounted Display systems. Has active agent and JV relationships in multiple African markets. Export approvals typically faster than US or European counterparts for African buyers.',
    website: 'https://www.elbitsystems.com',
    contact: { general: '+972-77-294-6662', address: 'Advanced Technology Center, Haifa 3100401, Israel', email: 'info@elbitsystems.com' },
    exportContact: 'international.business@elbitsystems.com',
    tags: ['UAV', 'Hermes', 'C4ISR', 'armoured vehicles', 'Israel', 'Africa', 'prime', 'non-ITAR'],
  },

  {
    id: 'rafael-advanced-defense',
    name: 'Rafael Advanced Defense Systems Ltd',
    country: 'Israel',
    nato: false,
    parentGroup: 'State of Israel (majority)',
    scale: 'prime',
    products: ['Spike ATGM', 'Trophy APS', 'Iron Dome (export)', 'Litening targeting pods', 'Spice precision guidance', 'David\'s Sling components', 'naval systems', 'Typhoon naval gun', 'ammunition'],
    usp: 'Developer of Spike ATGM — the world\'s most widely exported ATGM, in service with 36+ countries. Trophy APS is only combat-proven active protection system. Strong Africa track record (Nigeria, Ghana, Cameroon for Spike). Government-to-government sale facilitation.',
    description: 'State-majority defence R&D authority turned prime. Spike NLOS/LR/MR/SR family covers all ranges. Typhoon remote weapon station widely used on patrol vessels — relevant for coast guard applications in Angola, Cape Verde, Mozambique. Trophy APS fitted to US M1A2 and Polish Abrams. African office in Johannesburg.',
    website: 'https://www.rafael.co.il',
    contact: { general: '+972-4-879-4444', address: 'POB 2250, Haifa 3102102, Israel', email: 'media@rafael.co.il' },
    exportContact: 'intl.sales@rafael.co.il',
    tags: ['Spike', 'ATGM', 'Trophy', 'APS', 'naval', 'Israel', 'prime', 'non-ITAR', 'Africa'],
  },

  {
    id: 'iai-israel-aerospace',
    name: 'Israel Aerospace Industries (IAI)',
    country: 'Israel',
    nato: false,
    parentGroup: 'State of Israel',
    scale: 'prime',
    products: ['Heron UAV', 'Heron TP', 'Harop loitering munition', 'Barak air defence', 'maritime patrol aircraft', 'satellite systems', 'ELTA radar', 'naval C2', 'Searcher UAV', 'Green Dragon'],
    usp: 'Heron series UAVs are the most widely operated MALE UAVs outside the US. Active in Kenya, Ethiopia, Nigeria, Morocco, Angola. ELTA radar systems in 50+ countries. Strong Portuguese-language market presence in Brazil (G-550 CAEW, IAI aircraft modifications).',
    description: 'State-owned aerospace and defence prime. Heron 1/TP widely used in ISR roles across Africa and Asia. ELTA Systems division produces ground, naval and airborne radars. Unique Lusophone angle: long-standing relationship with Brazilian Air Force (KC-390 mission systems, G-550 CAEW). This creates natural bridge to Angolan and Mozambican aviation procurement.',
    website: 'https://www.iai.co.il',
    contact: { general: '+972-3-935-1111', address: 'Ben Gurion International Airport, Israel 7010000', email: 'iai@iai.co.il' },
    exportContact: 'marketing@iai.co.il',
    tags: ['UAV', 'Heron', 'radar', 'ELTA', 'maritime patrol', 'Israel', 'prime', 'Brazil', 'Africa'],
  },

  {
    id: 'plasan',
    name: 'Plasan Sasa Ltd',
    country: 'Israel',
    nato: false,
    parentGroup: null,
    scale: 'tier1',
    products: ['composite armour', 'ballistic protection kits', 'vehicle armour', 'blast protection', 'personal armour', 'MRAP armour', 'armour upgrades'],
    usp: 'World\'s leading composite armour specialist. Armour on MRAP vehicles used by US military (MATV, Caiman), Israeli Merkava Mk IV. Upgrade kits for legacy vehicles — critical for African armies with Soviet-era fleets. No ITAR on own-design solutions.',
    description: 'Specialist armour engineering company providing modular protection systems for vehicles and individual soldiers. Can upgrade existing Soviet-era APCs (BMP, BTR) with modern ballistic protection — directly relevant for Angola (FAA has BTR-80/BMP-2 fleet), Mozambique. Provides composite armour, blast-attenuating seats, and RPG protection screens. Active retrofit programmes across sub-Saharan Africa.',
    website: 'https://www.plasan.com',
    contact: { general: '+972-4-699-0000', address: 'D.N. Upper Galilee, Sasa 1392000, Israel' },
    exportContact: 'armour.exports@plasan.com',
    tags: ['armour', 'composite', 'vehicle protection', 'upgrade', 'Israel', 'MRAP', 'Africa'],
  },

  {
    id: 'aeronautics-defense',
    name: 'Aeronautics Defense Systems Ltd',
    country: 'Israel',
    nato: false,
    parentGroup: 'Aeronautics Ltd',
    scale: 'tier1',
    products: ['Orbiter 3 loitering munition', 'Orbiter 1K VTOL loitering munition', 'Dominator UAV', 'Aerostar UAV', 'mini UAV systems'],
    usp: 'Pioneer in affordable loitering munition systems. Orbiter series sold to 15+ countries at price points accessible to African defence budgets. No US ITAR restrictions. Strong training and maintenance support packages for developing-world operators.',
    description: 'Mid-tier Israeli UAV manufacturer specialising in tactical UAVs and loitering munitions. Orbiter 3 VTOL loitering munition can be launched from vehicles or naval platforms — ideal for coast guard and border security applications. Has existing customer base in Africa. Offers attractive financing and training packages. Good fit for Angola\'s patrol vessel requirements.',
    website: 'https://www.aeronautics-sys.com',
    contact: { general: '+972-8-861-4444', address: 'Yavne Industrial Zone, Israel' },
    tags: ['UAV', 'loitering munition', 'Orbiter', 'VTOL', 'Israel', 'affordable', 'Africa'],
  },

  // ─── TURKEY (SYSTEMS) ────────────────────────────────────────────────────────

  {
    id: 'baykar-technology',
    name: 'Baykar Makina Sanayi ve Ticaret A.Ş.',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Baykar Holding',
    scale: 'prime',
    products: ['Bayraktar TB2 MALE UAV', 'Bayraktar TB3 MALE UAV', 'Bayraktar Akıncı UCAV', 'Kızılelma stealth UAV', 'Bayraktar Mini UAV'],
    usp: 'Bayraktar TB2 is the most combat-proven and commercially successful MALE UAV in the world. In service with 25+ countries including Morocco, Niger, Togo, Rwanda, Ethiopia, Somalia, Djibouti, Senegal. Dramatically lower unit cost than US/Israeli equivalents (~$2M per system vs $30M+ for Predator). Turkish government actively subsidises exports as foreign policy tool.',
    description: 'Rapidly grown from niche manufacturer to global UAV prime. TB2 proven in Libya, Ukraine, Azerbaijan, Ethiopia. Multiple African nations already operate TB2. Relevant for Angola and Mozambique ISR/strike requirements. Turkey actively pursuing defence partnerships across Africa as part of neo-Ottoman foreign policy. Baykar offers generous training, simulator and maintenance packages. Note: exports require Turkish government approval; politically driven.',
    website: 'https://www.baykartech.com',
    contact: { general: '+90-212-867-4100', address: 'Teknopark Istanbul, Sanayi Mah. Teknopark Blv, Pendik, Istanbul, Turkey' },
    exportContact: 'international@baykartech.com',
    tags: ['UAV', 'TB2', 'MALE', 'UCAV', 'Turkey', 'Africa', 'ISR', 'strike', 'affordable'],
  },

  {
    id: 'fnss-defense-systems',
    name: 'FNSS Savunma Sistemleri A.Ş.',
    country: 'Turkey',
    nato: true,
    parentGroup: 'BAE Systems / Nurol Holding (JV)',
    scale: 'tier1',
    products: ['ACV-15 APC', 'Kaplan MT medium tank', 'Kaplan-20 IFV', 'PARS wheeled APC', 'Yıldırım MRAP', 'armoured engineering vehicles'],
    usp: 'BAE Systems JV gives FNSS UK export compliance standards with Turkish production cost advantages. PARS 6x6 and 8x8 widely exported to African and Asian markets. Significantly cheaper than European equivalents with comparable protection.',
    description: 'Turkish-British JV producing tracked and wheeled armoured vehicles. ACV-15 APC in service with Saudi Arabia, Bahrain, and others. PARS family (6x6, 8x8) is the key African-market product — moderate cost, reliable, good off-road performance. Relevant for Angola FAA modernisation programme (replacing aging BMP fleet). Malaysia operates Kaplan. Active in Middle East and Asia markets.',
    website: 'https://www.fnss.com.tr',
    contact: { general: '+90-312-641-1919', address: 'Organize Sanayi Bölgesi, 6. Cadde No.21, Sincan, Ankara, Turkey' },
    exportContact: 'export@fnss.com.tr',
    tags: ['armoured vehicles', 'APC', 'IFV', 'wheeled', 'Turkey', 'Africa', 'affordable'],
  },

  {
    id: 'otokar',
    name: 'Otokar Otomotiv ve Savunma Sanayi A.Ş.',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Koç Holding',
    scale: 'tier1',
    products: ['Cobra light armoured vehicle', 'Cobra II', 'Arma 8x8 APC', 'Sultan APC', 'Tulpar tracked IFV', 'Altay MBT (development)'],
    usp: 'Cobra 4x4 is the highest-volume exported Turkish military vehicle — 30+ countries including 12+ African nations. Simple, reliable, maintainable with minimal specialist tools. Ideal for African security forces with limited maintenance infrastructure.',
    description: 'Turkey\'s largest military land vehicle manufacturer. Cobra series exported to Azerbaijan, Kazakhstan, Saudi Arabia, UAE, Bahrain, Jordan, Maldives, and multiple African nations. Key selling point: simple maintenance, spare parts availability, training. Arma 8x8 APC is larger alternative to Cobra for higher-threat environments. Koç Holding ownership means strong commercial backing and after-sales infrastructure.',
    website: 'https://www.otokar.com',
    contact: { general: '+90-265-291-7000', address: 'Hendek OSB, Hendek, Sakarya, Turkey' },
    exportContact: 'defence.export@otokar.com',
    tags: ['armoured vehicles', 'Cobra', 'light armour', 'Turkey', 'Africa', 'maintenance-friendly'],
  },

  {
    id: 'aselsan',
    name: 'ASELSAN A.Ş.',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Turkish Armed Forces Foundation (TSKGV)',
    scale: 'prime',
    products: ['C4ISR systems', 'military radios', 'SATCOM terminals', 'IFF systems', 'radar systems', 'EW systems', 'border surveillance', 'thermal cameras', 'remote weapon stations', 'electronic warfare'],
    usp: 'Turkey\'s largest defence electronics company. ASELSAN communications systems (MUTES, EDACS) widely deployed. Border surveillance systems (including TUBITAK-SAGE collaboration) active in 15+ countries. Cost-effective alternative to Thales/Harris for African communications requirements.',
    description: 'Dominant Turkish electronics/communications prime. Products include vehicle/man-portable radios, naval fire control systems, RWS (remote weapon stations used on Turkish vehicles worldwide), and border surveillance sensor systems. Relevant for Angolan and Mozambican communications modernisation programmes. Often sold as integrated package with FNSS/Otokar/BMC vehicles.',
    website: 'https://www.aselsan.com.tr',
    contact: { general: '+90-312-592-1000', address: 'Mehmet Akif Ersoy Mah. 16. Cadde No:16, Yenimahalle, Ankara, Turkey' },
    exportContact: 'export@aselsan.com.tr',
    tags: ['C4ISR', 'communications', 'radios', 'radar', 'border surveillance', 'Turkey', 'electronics'],
  },

  {
    id: 'bmc-turkey',
    name: 'BMC Otomotiv Sanayi ve Ticaret A.Ş.',
    country: 'Turkey',
    nato: true,
    parentGroup: 'Qatar Armed Forces / Etihad International Investment (joint)',
    scale: 'tier1',
    products: ['Kirpi MRAP', 'Vuran MRAP', 'Hippo APC', 'military trucks', 'tactical wheeled vehicles', 'protected patrol vehicles'],
    usp: 'Kirpi MRAP is one of the most exported MRAPs globally — 4,000+ vehicles delivered to 25+ countries including multiple African nations. Qatar sovereign backing ensures financial stability. Extremely competitive pricing versus Western equivalents.',
    description: 'Turkish military truck and MRAP manufacturer with Qatar sovereign fund ownership giving access to Gulf financing for buyer nations. Kirpi MRAP (3 variants) in service with Niger, Somalia, Nigeria, Bahrain, UAE, Qatar, and others. Direct relevance for Mozambique counter-insurgency requirements (Cabo Delgado). Offers offset manufacturing and local assembly agreements.',
    website: 'https://www.bmc.com.tr',
    contact: { general: '+90-232-490-5050', address: 'Kemalpasa OSB, Kemalpasa, Izmir, Turkey' },
    exportContact: 'defence@bmc.com.tr',
    tags: ['MRAP', 'Kirpi', 'military trucks', 'Turkey', 'Africa', 'counter-insurgency', 'Gulf-backed'],
  },

  // ─── SOUTH AFRICA ────────────────────────────────────────────────────────────

  {
    id: 'paramount-group',
    name: 'Paramount Group',
    country: 'South Africa',
    nato: false,
    parentGroup: null,
    scale: 'prime',
    products: ['Mbombe 4 APC', 'Mbombe 6 IFV', 'Maverick MRAP', 'Marauder MRAP', 'Matador APC', 'armoured vehicles', 'ISR systems', 'border surveillance', 'aircraft modification', 'weaponisation services'],
    usp: 'Africa\'s largest indigenous private defence group. Deep relationships with SADC, ECOWAS, and East African armed forces. Manufacturing in South Africa, Kazakhstan, UAE and UK. Specifically designed vehicles for African conditions — sand, laterite, tropical heat. Critical advantage for Arkmurus: Paramount\'s African networks complement Lusophone access perfectly.',
    description: 'Johannesburg-based prime that is unquestionably the most important defence company for sub-Saharan African procurement. Mbombe 4/6 APC/IFV family designed specifically for African terrain and maintenance conditions. Marauder MRAP proven in combat operations. Strong relationships with SANDF, FADM (Mozambique), Kenyan military, Nigerian Army, Rwandan Defence Forces. Has engaged in Angola (FAA). Key partnership target for Arkmurus — complementary geographic networks, strong Portuguese-speaking market interest.',
    website: 'https://www.paramountgroup.com',
    contact: { general: '+27-11-458-7200', address: 'Paramount Group HQ, Johannesburg, South Africa', email: 'info@paramountgroup.com' },
    exportContact: 'defence.exports@paramountgroup.com',
    tags: ['armoured vehicles', 'MRAP', 'APC', 'IFV', 'South Africa', 'Africa', 'prime', 'Mbombe', 'Marauder'],
  },

  {
    id: 'rheinmetall-denel-munition',
    name: 'Rheinmetall Denel Munition (Pty) Ltd (RDM)',
    country: 'South Africa',
    nato: false,
    parentGroup: 'Rheinmetall AG (51%) / Denel (49%)',
    scale: 'tier1',
    products: ['155mm ammunition', '105mm ammunition', '60mm mortar', '81mm mortar', '120mm mortar', 'artillery propellants', '20mm-40mm cannon rounds', 'airburst fuzes', 'naval ammunition'],
    calibres: ['155mm', '105mm', '60mm mortar', '81mm mortar', '120mm mortar', '20mm', '23mm', '35mm', '40mm'],
    usp: 'Africa\'s only large-calibre artillery ammunition manufacturer. Unique position: no ITAR (South African export licensing), closer to African markets, African pricing. RDM 155mm ammunition used by SANDF and exported to multiple African countries. NATO-standard STANAG-qualified products.',
    description: 'Joint venture combining Rheinmetall\'s product range with South African manufacture. Critical for African procurement: no US ITAR restrictions, competitive pricing vs European equivalents, shorter logistics chain to African buyers, experience with African maintenance environments. Products include V-LAP (velocity enhanced long-range artillery projectile), HE and smoke rounds, propellant charges. Angola\'s 155mm ATMOS howitzers (Elbit) require compatible ammunition — RDM is a natural fit.',
    website: 'https://www.rheinmetall-denel.co.za',
    contact: { general: '+27-21-905-5000', address: 'Somerset West, Cape Town, South Africa', email: 'info@rdm.co.za' },
    exportContact: 'exports@rdm.co.za',
    tags: ['155mm', '105mm', 'mortar', 'propellant', 'South Africa', 'Africa', 'no ITAR', 'STANAG'],
  },

  {
    id: 'denel-land-systems',
    name: 'Denel Land Systems (Pty) Ltd',
    country: 'South Africa',
    nato: false,
    parentGroup: 'Denel SOC Ltd (South African Government)',
    scale: 'tier1',
    products: ['G5 155mm towed howitzer', 'G6 155mm self-propelled howitzer', 'T5-52 howitzer', 'Rooikat wheeled AFV', 'Badger IFV (development)', 'ZT-35 ATGM', 'Mechem CASSPIR MRAP'],
    usp: 'Denel G5/G6 howitzers are globally recognised long-range artillery systems. CASSPIR is the original mine-protected vehicle concept. Despite financial difficulties, products remain in high demand. G6 exported to UAE; G5 to numerous African nations. No ITAR.',
    description: 'South Africa\'s state-owned armoured vehicle and artillery manufacturer. G6 SPH is one of the few wheeled 155mm/52cal systems globally — suitable for African road networks. CASSPIR mine-protected vehicle has proven lineage from Rhodesia/Namibia Bush War — directly relevant for mine-affected Mozambique and Angola. Denel has serious financial challenges but remains operational and export-capable with government support.',
    website: 'https://www.denel.co.za',
    contact: { general: '+27-12-428-0911', address: 'Irene, Centurion, South Africa', email: 'info@denel.co.za' },
    exportContact: 'international.sales@denel.co.za',
    tags: ['howitzer', 'G6', 'G5', 'MRAP', 'CASSPIR', 'South Africa', 'artillery', 'no ITAR'],
  },

  // ─── INDIA ───────────────────────────────────────────────────────────────────

  {
    id: 'tata-advanced-systems',
    name: 'Tata Advanced Systems Ltd (TASL)',
    country: 'India',
    nato: false,
    parentGroup: 'Tata Group',
    scale: 'tier1',
    products: ['armoured vehicles', 'light specialist vehicles', 'aerospace structures', 'C2 systems', 'UAV systems', 'electronics', 'WHAP 8x8 APC'],
    usp: 'Tata Group backing provides exceptional financial stability and supply chain. WHAP 8x8 APC is India\'s new indigenous APC — competitive pricing vs Western equivalents. India\'s "Make in India" defence drive creates transfer-of-technology opportunities attractive to developing nations.',
    description: 'Tata Group\'s defence manufacturing subsidiary with broad portfolio from vehicles to aerospace. Active in Southeast Asia and Africa. India\'s diplomatic push into Africa creates procurement channels. Key advantage: offset manufacturing agreements — can offer local assembly/jobs in buyer nation (attractive to politically sensitive African buyers). Has signed MoUs with multiple African defence ministries.',
    website: 'https://www.tataadvancedsystems.com',
    contact: { general: '+91-40-3041-5000', address: 'Gachibowli, Hyderabad 500032, India' },
    exportContact: 'defence.exports@tataadvancedsystems.com',
    tags: ['armoured vehicles', 'APC', 'UAV', 'India', 'Africa', 'offset manufacturing', 'affordable'],
  },

  {
    id: 'bharat-electronics',
    name: 'Bharat Electronics Limited (BEL)',
    country: 'India',
    nato: false,
    parentGroup: 'Government of India (Ministry of Defence)',
    scale: 'tier1',
    products: ['radar systems', 'C4I systems', 'electronic warfare', 'communications', 'fire control systems', 'night vision', 'coastal surveillance', 'naval electronics'],
    usp: 'India\'s state defence electronics prime with 60+ years manufacturing. Cheaper than Western equivalents by 30-60%. Indian government actively marketing BEL products across Africa through Lines of Credit ($1B+ credit lines to African nations). No ITAR.',
    description: 'India\'s primary defence electronics manufacturer under Ministry of Defence. Products cover radar, C2, EW, communications, and naval systems. India has extended significant Lines of Credit to African nations including Mozambique, Tanzania, Kenya which can be used to procure BEL products. Growing African customer base. Relevant for border surveillance and coastal monitoring requirements in Lusophone Africa.',
    website: 'https://www.bel-india.in',
    contact: { general: '+91-80-2272-5050', address: 'Outer Ring Road, Doddanakundi, Bengaluru 560048, India' },
    exportContact: 'export@bel.co.in',
    tags: ['radar', 'C4I', 'EW', 'communications', 'coastal surveillance', 'India', 'Africa', 'no ITAR', 'credit lines'],
  },

  // ─── SMALL ARMS / POLICE / SECURITY ──────────────────────────────────────────

  {
    id: 'fn-herstal',
    name: 'FN Herstal SA',
    country: 'Belgium',
    nato: true,
    parentGroup: 'Herstal Group / Walloon Regional Investment Company',
    scale: 'prime',
    products: ['FN SCAR', 'FN FAL', 'FN F2000', 'MINIMI machine gun', 'FN MAG machine gun', 'FN M2 .50cal', 'pistols (FNX, FN 509)', 'FN P90', 'remote weapon stations (deFNder)'],
    usp: 'Manufacturer of the FN FAL — the most widely distributed assault rifle in Africa (hundreds of thousands in service across sub-Saharan Africa). FN MAG machine gun standard NATO. Very strong Africa service/spares infrastructure due to historical FAL distribution.',
    description: 'Belgium\'s premier small arms manufacturer with global distribution. FN FAL in service with 90+ countries, with massive installed base across Africa creating continued ammunition, spares and upgrade demand. FN SCAR replacing FAL in many armies (SCAR-L/H both available). deFNder RWS relevant for vehicle armament. Strong relationship with many African armies through historical supply channels. Excellent for Arkmurus: FAL spares and modernisation packages are accessible entry points.',
    website: 'https://www.fnherstal.com',
    contact: { general: '+32-4-240-8111', address: 'Voie de Liège 33, 4040 Herstal, Belgium' },
    exportContact: 'export@fnherstal.com',
    tags: ['small arms', 'FN FAL', 'SCAR', 'machine gun', 'Belgium', 'Africa', 'police', 'military'],
  },

  {
    id: 'heckler-koch',
    name: 'Heckler & Koch GmbH',
    country: 'Germany',
    nato: true,
    parentGroup: null,
    scale: 'tier1',
    products: ['G36 assault rifle', 'HK416 assault rifle', 'HK433', 'MP5 submachine gun', 'MP7', 'G28 DMR', 'HK21 machine gun', 'HK269 grenade launcher', 'USP/P30/SFP9 pistols'],
    usp: 'HK416 standard rifle of French Army, German KSK, US SOCOM (CQBR). MP5 is the definitive special forces submachine gun — in service with 40+ African police and security forces. G36 widely distributed. Strong reputation for reliability in adverse conditions.',
    description: 'Germany\'s premier small arms manufacturer. MP5 particularly relevant — in service with virtually every African special police unit. HK416 increasingly replacing G36 in elite units. Importantly: HK has German export licensing requirements which can be restrictive but provides legitimacy. Key for Arkmurus: MP5 spares, magazines, and accessories are constant requirement for African interior ministries.',
    website: 'https://www.heckler-koch.com',
    contact: { general: '+49-7424-921-0', address: 'Heckler & Koch GmbH, 78727 Oberndorf am Neckar, Germany' },
    exportContact: 'governmental.sales@heckler-koch.com',
    tags: ['small arms', 'HK416', 'MP5', 'G36', 'Germany', 'special forces', 'police', 'submachine gun'],
  },

  {
    id: 'beretta',
    name: 'Fabbrica d\'Armi Pietro Beretta SpA',
    country: 'Italy',
    nato: true,
    parentGroup: 'Beretta Holding SpA',
    scale: 'tier1',
    products: ['ARX160 assault rifle', 'M9/M92 pistol family', 'PX4 Storm', 'APX pistol', 'CX4 Storm carbine', 'Cx4 Storm', 'shotguns (police/military)'],
    usp: 'Beretta M92 is the sidearm of 40+ African armed forces and police — installed base means constant spare parts and accessories demand. ARX160 winning contracts in Africa and Asia. Italian export licensing generally accessible for African markets.',
    description: 'Italy\'s most important small arms manufacturer with exceptional Africa penetration. M92 pistol is the dominant sidearm across Francophone and Lusophone Africa — direct relevance for Angola (FAA standard sidearm), Cape Verde, Guinea-Bissau. ARX160 designed for developing-world operators: modular, low maintenance, works in sand/dust/water. Beretta\'s Italian EU/NATO credentials facilitate export approvals to African markets.',
    website: 'https://www.beretta.com',
    contact: { general: '+39-030-839-2111', address: 'Via Pietro Beretta 18, 25063 Gardone Val Trompia, Brescia, Italy' },
    exportContact: 'defence.export@beretta.it',
    tags: ['small arms', 'pistol', 'M92', 'ARX160', 'Italy', 'Africa', 'police', 'Lusophone'],
  },

  {
    id: 'cz-group',
    name: 'Česká zbrojovka Group SE (CZ Group)',
    country: 'Czech Republic',
    nato: true,
    parentGroup: null,
    scale: 'tier1',
    products: ['CZ 75 pistol', 'CZ 75 SP-01', 'P-10 pistol series', 'BREN 2 assault rifle', 'CZ Scorpion EVO 3', 'CZ 805 BREN', 'CZ TS 2', 'surplus weapons (historical)'],
    usp: 'CZ BREN 2 is NATO\'s most affordable high-quality assault rifle — winning multiple African army tenders. CZ 75 is in service with 100+ countries. Czech export licensing is accessible for African buyers. Czech-Africa relations historically strong (including Lusophone Africa via Cold War supply channels).',
    description: 'Czech Republic\'s dominant small arms manufacturer (also owns Colt and Accurate Ordnance since 2021). BREN 2 assault rifle winning contracts due to price-performance ratio. CZ 75 pistol family is benchmark for police procurement. Historical Czech-African connection through COMECON era means familiarity with Czech products in many African military stores. Good option for Angola (FAA) which operated Czech weapons in the 1970s-90s.',
    website: 'https://www.czgroup.com',
    contact: { general: '+420-572-751-111', address: 'Svat. Čecha 1283, 688 01 Uherský Brod, Czech Republic' },
    exportContact: 'government.sales@czgroup.com',
    tags: ['small arms', 'BREN 2', 'CZ 75', 'pistol', 'Czech Republic', 'NATO', 'Africa', 'affordable'],
  },

  // ─── NAVAL / MARITIME / COAST GUARD ──────────────────────────────────────────

  {
    id: 'damen-shipyards',
    name: 'Damen Shipyards Group',
    country: 'Netherlands',
    nato: true,
    parentGroup: null,
    scale: 'prime',
    products: ['OPV 1800 offshore patrol vessel', 'FCS 5009 fast crew supplier', 'Stan Patrol 4207', 'Stan Patrol 2606', 'Stan Tug', 'landing craft', 'CB90 fast assault craft', 'naval support vessels'],
    usp: 'World\'s largest builder of patrol vessels under 100m. OPV 1800 is the most purchased patrol vessel by developing-world navies. Operational in Angola (Navy), Mozambique (Navy), Cape Verde (coast guard), Guinea-Bissau (navy). Lusophone Africa direct existing customer base — Arkmurus\'s strongest OEM ally for maritime.',
    description: 'Dutch shipbuilder with facilities in 35 countries and existing Lusophone Africa portfolio. The Angola Navy already operates Damen vessels — this creates an existing customer relationship. Mozambique Navy patrols Indian Ocean with Damen patrol boats. Cape Verde Coast Guard uses Damen Stan Patrol. Damen\'s standard OPV and patrol vessel designs are specifically optimised for developing-world navies: simple operation, minimal maintenance, Dutch export licensing accessible. Key partnership for Arkmurus maritime leads.',
    website: 'https://www.damen.com',
    contact: { general: '+31-183-639-911', address: 'Avelingen West 20, 4202 MS Gorinchem, Netherlands' },
    exportContact: 'naval.sales@damen.com',
    tags: ['naval', 'patrol vessel', 'OPV', 'coast guard', 'Netherlands', 'Lusophone', 'Angola', 'Mozambique', 'Cape Verde'],
  },

  {
    id: 'ocea-france',
    name: 'OCEA SA',
    country: 'France',
    nato: true,
    parentGroup: null,
    scale: 'tier1',
    products: ['FPB 98 MKI OPV', 'FPB 72 OPV', 'FPB 54 patrol boat', 'aluminium fast patrol vessels', 'coast guard vessels', 'landing craft'],
    usp: 'OCEA aluminium patrol vessels are the primary coast guard solution for Francophone and Lusophone West Africa. Guinea-Bissau, Senegal, Côte d\'Ivoire, Mozambique all operate OCEA vessels. French government co-financing often available. Lusophone Africa direct existing customer base.',
    description: 'French shipbuilder specialising in aluminium fast patrol vessels. OCEA FPB series is the dominant patrol vessel for West and Central Africa. French DCNS cooperation makes OCEA a natural fit for French-funded maritime security programmes. Guinea-Bissau naval interdiction requirement (narco-trafficking) directly served by OCEA\'s FPB series. Excellent OEM for Lusophone Africa maritime leads.',
    website: 'https://www.ocea.fr',
    contact: { general: '+33-2-51-26-60-00', address: '1 Rue de la Lambarde, 85600 Montaigu, France' },
    exportContact: 'naval@ocea.fr',
    tags: ['naval', 'patrol vessel', 'coast guard', 'aluminium', 'France', 'West Africa', 'Guinea-Bissau'],
  },

  {
    id: 'fassmer',
    name: 'Fr. Fassmer GmbH & Co. KG',
    country: 'Germany',
    nato: true,
    parentGroup: null,
    scale: 'tier2',
    products: ['patrol vessels 20-100m', 'coast guard cutters', 'OPV 80', 'OPV 100', 'landing craft', 'multi-role vessels', 'lifeboats'],
    usp: 'German quality OPV manufacturer with strong developing-world customer base. Philippines Coast Guard, multiple African navies. German export licensing is reliable and consistent. Vessels optimised for low-maintenance operations.',
    description: 'German mid-sized shipbuilder producing patrol and multi-role vessels. Strong reputation for build quality and after-sales support. Has supplied vessels to Philippines, Brazil, and various African navies. Relevant for Angola and Mozambique offshore oil-zone patrol requirements. German engineering standards at competitive European pricing.',
    website: 'https://www.fassmer.de',
    contact: { general: '+49-4401-9800', address: 'Am Dobben 15, 27804 Berne (Weser), Germany' },
    exportContact: 'naval.export@fassmer.de',
    tags: ['naval', 'OPV', 'patrol vessel', 'coast guard', 'Germany', 'Africa', 'Philippines'],
  },

  // ─── SURVEILLANCE / BORDER SECURITY / C4ISR ──────────────────────────────────

  {
    id: 'thales-group',
    name: 'Thales Group',
    country: 'France',
    nato: true,
    parentGroup: null,
    scale: 'prime',
    products: ['ground surveillance radar', 'border control systems', 'GNSS military', 'tactical communications (PR4G)', 'C2 systems', 'airborne radar', 'naval combat management', 'missile guidance', 'IFF systems', 'optronics'],
    usp: 'Thales is the dominant supplier of border surveillance and C4ISR to Francophone Africa under French military cooperation frameworks (Recamp, Sabre). Has existing relationships in Cameroon, Senegal, Côte d\'Ivoire, Djibouti. Ground Master radar widely deployed. French government DGA approval process well-established.',
    description: 'French electronics prime with presence in 68 countries. Ground Master radar series provides tactical air picture at affordable price for African militaries. SYNAPS border surveillance system being deployed across Sahel. PR4G tactical radios standard in Francophone Africa. Thales specifically seeking to expand into Lusophone Africa where they lack footprint — creating partnership opportunity. Has Portuguese-language capability through Thales Portugal (naval systems).',
    website: 'https://www.thalesgroup.com',
    contact: { general: '+33-1-57-77-86-26', address: '1 Place des Martyrs de la Résistance, 92405 Courbevoie, France' },
    exportContact: 'defence.international@thalesgroup.com',
    tags: ['C4ISR', 'radar', 'border surveillance', 'communications', 'France', 'Africa', 'prime'],
  },

  {
    id: 'teledyne-flir',
    name: 'Teledyne FLIR LLC (FLIR Systems)',
    country: 'United States',
    nato: true,
    parentGroup: 'Teledyne Technologies',
    scale: 'tier1',
    products: ['thermal imaging cameras', 'Border Hawk surveillance system', 'unmanned ground systems', 'UAS thermal sensors', 'handheld thermal', 'vehicle-mounted thermal', 'maritime surveillance sensors', 'R&S cameras'],
    usp: 'FLIR thermal cameras are the standard in 90+ world armies for individual soldier and vehicle use. FLIR sensors fitted to most major platform OEMs (Paramount, FNSS, Otokar). Border surveillance FLIR systems in service across Africa. US ITAR-controlled but FLIR actively promotes African FMF/FMS-funded procurement.',
    description: 'World\'s largest thermal/infrared sensor company. Products span from $800 soldier-worn monoculars to $2M+ vehicle-mounted panoramic thermal sensors. Key role for Arkmurus: FLIR sensors are often a required add-on to armoured vehicle or patrol vessel contracts. FLIR has Africa regional office (Nairobi). Angola and Mozambique FAA/FADM both lack modern thermal imaging — significant opportunity. Note: US ITAR licensing required.',
    website: 'https://www.teledyneflir.com',
    contact: { general: '+1-503-498-3547', address: '27700A SW Parkway Ave., Wilsonville, OR 97070, USA' },
    exportContact: 'govtsales@flir.com',
    tags: ['thermal', 'surveillance', 'border security', 'sensors', 'USA', 'ITAR', 'Africa', 'ISR'],
  },

  {
    id: 'indra-sistemas',
    name: 'Indra Sistemas SA',
    country: 'Spain',
    nato: true,
    parentGroup: null,
    scale: 'prime',
    products: ['air traffic management', 'radar systems (air/ground)', 'C2 systems', 'border surveillance', 'air defence systems', 'training simulators', 'electronic warfare', 'satellite systems'],
    usp: 'Spain\'s largest defence electronics company with active Lusophone Africa relationships (Portugal strategic partnership gives access to Portuguese-speaking networks). Radar systems in service with multiple African nations. Has local office in Mozambique.',
    description: 'Spanish defence/IT prime with strong Ibero-American and African presence. Lanza radar family widely exported to Latin America and Africa. Has existing presence in Mozambique (IT systems) and Angola (radar). Iberian connection creates cultural/linguistic bridge for Lusophone markets. Simulators used by multiple African air forces. Indra actively targeting Lusophone Africa through Spanish-Portuguese cooperation frameworks.',
    website: 'https://www.indra.es',
    contact: { general: '+34-91-480-5000', address: 'Avda. de Bruselas, 35, 28108 Alcobendas, Madrid, Spain' },
    exportContact: 'defence.export@indra.es',
    tags: ['radar', 'C2', 'border surveillance', 'Spain', 'Lusophone', 'Africa', 'Portugal', 'simulator'],
  },

  {
    id: 'kongsberg-defence',
    name: 'Kongsberg Defence & Aerospace AS',
    country: 'Norway',
    nato: true,
    parentGroup: 'Kongsberg Gruppen ASA',
    scale: 'prime',
    products: ['NSM anti-ship missile', 'NASAMS air defence', 'Protector RWS', 'JDAM-ER', 'Penguin missile', 'naval fire control', 'Space systems'],
    usp: 'Protector Remote Weapon Station (RWS) is the world\'s most widely used RWS — fitted to vehicles in 20+ countries. NASAMS air defence system used by US National Guard to protect Washington DC. NSM anti-ship missile with no-ITAR status (Norwegian export licensing applies).',
    description: 'Norwegian prime with global reach. The Protector RWS is the key product for African vehicle integration — can be fitted to virtually any vehicle platform, operated remotely, multiple weapon options. No US ITAR on Norwegian products. Active in Middle East and expanding African reach. Good complement to any armoured vehicle deal.',
    website: 'https://www.kongsberg.com/kda',
    contact: { general: '+47-32-28-82-00', address: 'Kirkegårdsveien 45, N-3616 Kongsberg, Norway' },
    exportContact: 'defence.export@kongsberg.com',
    tags: ['RWS', 'Protector', 'anti-ship missile', 'air defence', 'Norway', 'NATO', 'no ITAR'],
  },

  {
    id: 'l3harris-technologies',
    name: 'L3Harris Technologies Inc.',
    country: 'United States',
    nato: true,
    parentGroup: null,
    scale: 'prime',
    products: ['tactical radio (Harris Falcon)', 'AN/PRC-117 multiband radio', 'SATCOM systems', 'EW systems', 'night vision', 'ISR systems', 'C2 software', 'airborne surveillance systems'],
    usp: 'Harris Falcon III tactical radio is the US military standard and exported to 150+ countries. Widely distributed across African militaries through FMF/FMS programmes. Strong installed base creates upgrade/spares opportunities. US ITAR but FMF/FMS pathway available for most target markets.',
    description: 'US prime formed from L3 and Harris merger. Falcon III radios in service with Nigeria, Kenya, Uganda, Ethiopia, and other African armies through FMF grants. Radio spares, accessories and interoperability systems are constant procurement requirements. AN/PRC-117 SATCOM bridge also relevant for remote operations (Cabo Delgado Mozambique). Note: ITAR controlled, requires ELN from US government for export.',
    website: 'https://www.l3harris.com',
    contact: { general: '+1-321-727-9100', address: '1025 West NASA Blvd., Melbourne, FL 32919, USA' },
    exportContact: 'international.sales@l3harris.com',
    tags: ['communications', 'radio', 'SATCOM', 'EW', 'USA', 'ITAR', 'Africa', 'FMF', 'FMS'],
  },

  {
    id: 'saab-ab',
    name: 'Saab AB',
    country: 'Sweden',
    nato: true,
    parentGroup: null,
    scale: 'prime',
    products: ['GlobalEye AEW&C', 'Gripen fighter (E/F)', 'Carl-Gustaf M4 MAAWS', 'AT4 anti-tank', 'NLAW', 'Giraffe radar', 'Arthur counter-battery radar', 'airborne surveillance', 'naval systems'],
    usp: 'Carl-Gustaf MAAWS is the most widely used man-portable anti-armour and multi-role weapon in Western militaries — sold to 40+ African and Asian armies. AT4 is ubiquitous in African peacekeeping forces. Swedish export licensing accessible.',
    description: 'Swedish prime with broad portfolio. The Carl-Gustaf M4 is particularly relevant for Arkmurus markets — in service with South Africa (SANDF), Kenya (KDF), Rwanda (RDF), Nigeria (NA) and others. M3 MAAWS in service with Uganda, Tanzania. AT4 rocket launcher even more widespread. Ammunition supply for these weapons is a constant procurement requirement — excellent entry point. Giraffe radar deployed across Middle East and Africa.',
    website: 'https://www.saab.com',
    contact: { general: '+46-13-18-00-00', address: 'Bröderna Ugglas Gata, Linköping 581 88, Sweden' },
    exportContact: 'defence.export@saab.se',
    tags: ['Carl-Gustaf', 'AT4', 'NLAW', 'radar', 'Sweden', 'NATO', 'Africa', 'anti-armour'],
  },

  // ─── UAV / UAS ────────────────────────────────────────────────────────────────

  {
    id: 'schiebel-camcopter',
    name: 'Schiebel Aircraft GmbH',
    country: 'Austria',
    nato: true,
    parentGroup: null,
    scale: 'specialist',
    products: ['Camcopter S-100 VTOL UAV', 'naval version S-100', 'ISR payload integration', 'maritime patrol UAV'],
    usp: 'Camcopter S-100 is the leading VTOL naval UAV — in service with UAE, Germany, France, Libya, Pakistan navies. Key advantage: can operate from patrol vessels without modification — directly relevant for Angola and Cape Verde naval OPV requirements.',
    description: 'Austrian specialist producing the S-100 unmanned helicopter. Key differentiator: truly ship-deployable from small platforms (corvette-size and up), automated take-off/landing. No ITAR (Austrian export licensing). In service with navies as diverse as UAE, Germany, Libya. Angola Navy OPV upgrade programme (Damen OPVs) could integrate S-100 for enhanced maritime surveillance. Cape Verde Coast Guard uses Italian OPVs where S-100 could add ISR capability.',
    website: 'https://www.schiebel.net',
    contact: { general: '+43-1-370-9000', address: 'Meisenweg 56, A-1220 Vienna, Austria' },
    exportContact: 'international@schiebel.net',
    tags: ['UAV', 'VTOL', 'naval UAV', 'maritime ISR', 'Austria', 'no ITAR', 'coast guard', 'Lusophone'],
  },

  // ─── BRAZIL (additional) ─────────────────────────────────────────────────────

  {
    id: 'cbc-brazil',
    name: 'CBC — Companhia Brasileira de Cartuchos SA',
    country: 'Brazil',
    nato: false,
    parentGroup: 'CBC / Companhia Brasileira de Cartuchos',
    scale: 'tier1',
    products: ['5.56x45mm NATO', '7.62x51mm NATO', '9mm Luger', '.40 S&W', '12 gauge', '.38 Special', '.357 Magnum', '7.62x39mm', '40mm grenades', 'less-lethal ammunition'],
    usp: 'Latin America\'s largest ammunition manufacturer. Portuguese-speaking country — natural partner for Lusophone Africa (Angola, Mozambique, Guinea-Bissau, Cape Verde). CPLP defence cooperation framework creates procurement channel. No ITAR. Highly competitive pricing.',
    description: 'Brazil\'s dominant ammunition manufacturer (also owns MEN in Germany, Sellier & Bellot in Czech Republic). Full small and medium calibre range. Critical Arkmurus advantage: Brazil-Angola, Brazil-Mozambique bilateral defence cooperation agreements make CBC ammunition procurement a preferred route for Lusophone African buyers — politically simple, linguistically accessible, competitively priced. CBC has active export to 70+ countries including multiple African nations.',
    website: 'https://www.cbc.com.br',
    contact: { general: '+55-11-4547-9200', address: 'Av. dos Esportes, 1771, Guarulhos, SP, Brazil' },
    exportContact: 'export@cbc.com.br',
    tags: ['ammunition', 'small arms', '5.56mm', '7.62mm', '9mm', 'Brazil', 'Lusophone', 'no ITAR', 'CPLP'],
  },

  {
    id: 'embraer-defence',
    name: 'Embraer Defense & Security',
    country: 'Brazil',
    nato: false,
    parentGroup: 'Embraer SA',
    scale: 'prime',
    products: ['KC-390 military transport/tanker', 'A-29 Super Tucano', 'Phenom 100/300 ISR variants', 'maritime patrol aircraft', 'C2 systems', 'simulators'],
    usp: 'KC-390 military transport is the world\'s most modern medium transport — already sold to Portugal, Hungary, Netherlands, Austria. Super Tucano is the world\'s best-selling light attack aircraft — 14+ African and Asian air forces operate it. Portuguese-speaking company with strong PALOP relationships.',
    description: 'Brazilian aerospace prime producing military transport and light attack aircraft. Super Tucano already in service with Nigeria (Alpha Jets replacing with A-29), Senegal, Mauritania, Angola (evaluation underway). KC-390 is Portugal\'s new military transport — creates Portugal-CPLP procurement pathway for Angola and Mozambique. Lusophone advantage is extremely strong here. Brazil\'s defence diplomacy actively promotes Embraer in Lusophone Africa.',
    website: 'https://www.embraer.com/global/en/defense-security',
    contact: { general: '+55-12-3927-1000', address: 'Av. Brigadeiro Faria Lima 2170, São José dos Campos, SP, Brazil' },
    exportContact: 'defence.exports@embraer.com.br',
    tags: ['aircraft', 'transport', 'light attack', 'A-29', 'KC-390', 'Brazil', 'Lusophone', 'Africa', 'CPLP'],
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

  // ─── POLAND ──────────────────────────────────────────────────────────────────

  {
    id: 'pgz-polska',
    name: 'Polska Grupa Zbrojeniowa (PGZ)',
    country: 'Poland',
    nato: true,
    parentGroup: 'PGZ S.A. (State Treasury)',
    scale: 'prime',
    products: ['155mm Krab SPH', 'Rosomak IFV', 'ammunition', 'radar systems', 'naval systems', 'air defence', 'MSBS Grot rifle'],
    calibres: ['155mm', '120mm', '5.56mm', '7.62mm', '12.7mm', '23mm', '30mm', '35mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · MSPO trade show · direct',
    usp: 'Poland\'s state defence champion consolidating 60+ companies. Krab 155mm SPH is combat-proven in Ukraine. Massive NATO-funded modernisation programme (€50bn+). Aggressive export push to NATO partners and emerging markets.',
    description: 'PGZ is the umbrella holding company for Poland\'s defence industry, encompassing manufacturers across land, sea, air, and electronics domains. The Krab 155mm self-propelled howitzer has been battle-validated in Ukraine, dramatically boosting export credibility. PGZ subsidiaries include HSW (artillery), WZM (armoured vehicles), and PCO (optics). Poland\'s defence budget surge (4%+ GDP) is fuelling capacity expansion.',
    website: 'https://www.pgzsa.pl',
    contact: {
      general: '+48 22 31 00 100',
      address: 'PGZ S.A., ul. Nowowiejska 26A, 00-911 Warszawa, Poland',
      email: 'info@pgzsa.pl',
    },
    exportContact: 'export@pgzsa.pl',
    tags: ['Krab', '155mm', 'IFV', 'Rosomak', 'Poland', 'NATO', 'prime', 'state-owned'],
  },

  {
    id: 'wb-electronics',
    name: 'WB Electronics S.A.',
    country: 'Poland',
    nato: true,
    parentGroup: 'WB Group',
    scale: 'tier1',
    products: ['Warmate loitering munition', 'FlyEye UAV', 'FT-5 reconnaissance UAV', 'Topaz APS', 'C4ISR systems', 'electronic warfare'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'MSPO trade show · direct · defence attaché',
    usp: 'Europe\'s leading loitering munition manufacturer. Warmate is combat-proven in Ukraine and exported to 15+ countries. Polish private-sector success story — nimble, cost-competitive, and rapidly iterating based on battlefield feedback.',
    description: 'WB Electronics (part of WB Group) is Poland\'s foremost drone and C4ISR company. Warmate loitering munition has been extensively used in Ukraine and is exported across NATO and partner nations. FlyEye mini-UAV family provides tactical ISR. The company also produces electronic warfare systems and advanced command & control software. Competitive pricing vs Israeli/Turkish alternatives.',
    website: 'https://www.wbgroup.pl',
    contact: {
      general: '+48 22 549 95 00',
      address: 'WB Electronics S.A., ul. Poznańska 129/133, 05-850 Ożarów Mazowiecki, Poland',
      email: 'info@wbgroup.pl',
    },
    exportContact: 'export@wbgroup.pl',
    tags: ['UAV', 'drone', 'loitering munition', 'Warmate', 'FlyEye', 'C4ISR', 'Poland', 'NATO'],
  },

  {
    id: 'mesko-ammunition',
    name: 'MESKO S.A.',
    country: 'Poland',
    nato: true,
    parentGroup: 'PGZ S.A.',
    scale: 'tier1',
    products: ['Piorun MANPADS', 'Grom MANPADS', '5.56mm ammunition', '7.62mm ammunition', '12.7mm ammunition', '23mm ammunition', 'RPG rockets', 'mortar bombs', 'propellant charges'],
    calibres: ['5.56mm', '7.62mm', '12.7mm', '23mm', '60mm mortar', '98mm RPG'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'PGZ export office · MSPO trade show · defence attaché',
    usp: 'Manufacturer of the Piorun MANPADS — the most effective man-portable air defence system fielded in Ukraine. Also produces full small-arms ammunition range to NATO STANAG standards. Battle-proven product portfolio at competitive pricing.',
    description: 'MESKO is Poland\'s primary ammunition and rocket manufacturer, located in Skarżysko-Kamienna. The Piorun (Thunderbolt) MANPADS has gained worldwide recognition from Ukraine battlefield performance. Also produces Grom MANPADS, full range of NATO-standard small arms ammunition, mortar bombs, and RPG rockets. Capacity expansion underway to meet surging demand.',
    website: 'https://www.mesko.com.pl',
    contact: {
      general: '+48 41 253 30 00',
      address: 'MESKO S.A., ul. Legionów 122, 26-111 Skarżysko-Kamienna, Poland',
      email: 'mesko@mesko.com.pl',
    },
    exportContact: 'export@mesko.com.pl',
    tags: ['MANPADS', 'Piorun', 'ammunition', 'small arms', 'mortar', 'Poland', 'NATO', 'PGZ'],
  },

  {
    id: 'bumar-labedy',
    name: 'Bumar-Łabędy S.A.',
    country: 'Poland',
    nato: true,
    parentGroup: 'PGZ S.A.',
    scale: 'tier1',
    products: ['PT-91 Twardy MBT', 'tank modernisation', 'armoured recovery vehicles', 'bridgelaying vehicles', 'armoured engineering vehicles'],
    calibres: ['125mm', '12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'PGZ export office · MSPO trade show',
    usp: 'Poland\'s main battle tank manufacturer — producer of PT-91 Twardy (T-72 derivative). Extensive experience modernising Soviet-era armour. Relevant for African armies operating T-72/T-55 fleets seeking NATO-standard upgrades without full replacement costs.',
    description: 'Bumar-Łabędy in Gliwice is Poland\'s armoured vehicle hub. The PT-91 Twardy is an extensively upgraded T-72 with Polish fire control, ERA, and powertrain improvements. Exported to Malaysia. Core competency in Soviet-era tank modernisation — highly relevant for Sub-Saharan African nations operating legacy T-55/T-72 fleets. Also produces ARVs and combat engineering vehicles.',
    website: 'https://www.bumar.gliwice.pl',
    contact: {
      general: '+48 32 234 70 00',
      address: 'Bumar-Łabędy S.A., ul. Mechaników 9, 44-109 Gliwice, Poland',
      email: 'bumar@bumar.gliwice.pl',
    },
    tags: ['MBT', 'tank', 'PT-91', 'armoured vehicle', 'modernisation', 'Poland', 'NATO', 'PGZ'],
  },

  {
    id: 'hsw-stalowa-wola',
    name: 'Huta Stalowa Wola S.A. (HSW)',
    country: 'Poland',
    nato: true,
    parentGroup: 'PGZ S.A.',
    scale: 'tier1',
    products: ['Krab 155mm SPH', 'Rak 120mm self-propelled mortar', 'Langusta MLRS', '155mm howitzer turrets', 'artillery systems'],
    calibres: ['155mm', '120mm mortar', '122mm MLRS'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'PGZ export office · MSPO trade show · direct',
    usp: 'Manufacturer of the Krab 155mm SPH and Rak 120mm mortar carrier — both combat-validated in Ukraine. Full artillery system integrator with proven export capacity. Rak is particularly attractive for African armies wanting self-propelled mortar capability at lower cost than Western alternatives.',
    description: 'HSW is the artillery centre of excellence within PGZ. The Krab 155mm SPH uses a Korean K9 chassis with British AS90 turret — now fully Polonised. The Rak 120mm self-propelled mortar on Rosomak hull provides rapid indirect fire capability. Both systems have been extensively used by Ukraine, providing invaluable combat validation data.',
    website: 'https://www.hsw.pl',
    contact: {
      general: '+48 15 813 50 01',
      address: 'HSW S.A., ul. Kwiatkowskiego 1, 37-450 Stalowa Wola, Poland',
      email: 'hsw@hsw.pl',
    },
    tags: ['Krab', '155mm', 'Rak', 'mortar', 'artillery', 'SPH', 'Poland', 'NATO', 'PGZ'],
  },

  // ─── CZECH REPUBLIC ──────────────────────────────────────────────────────────

  {
    id: 'csg-group',
    name: 'Czechoslovak Group (CSG)',
    country: 'Czech Republic',
    nato: true,
    parentGroup: 'CSG a.s.',
    scale: 'prime',
    products: ['DANA 152mm SPH', 'TATRA trucks', 'Excalibur Army vehicles', 'ammunition', 'aircraft overhaul', 'radar systems'],
    calibres: ['152mm', '155mm', '122mm', '120mm mortar', '7.62mm', '12.7mm'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'aware',
    contact_route: 'IDET trade show · direct · defence attaché',
    usp: 'Central Europe\'s largest private defence group. Owns Excalibur Army, Tatra Trucks, and multiple ammunition plants. Extensive export footprint in Africa, Middle East, and Asia. DANA self-propelled howitzer is in service across 15+ countries. Aggressively competitive on price.',
    description: 'CSG is a Czech private conglomerate that has acquired major defence assets including Tatra Trucks, Excalibur Army, and Slovak ammunition plants. DANA 152mm wheeled SPH has been exported extensively (Libya, Angola, others). CSG is known for pragmatic export policy and willingness to work in challenging markets. Recent acquisitions include Israeli and Spanish companies, expanding their portfolio significantly.',
    website: 'https://www.czechoslovakgroup.cz',
    contact: {
      general: '+420 530 500 500',
      address: 'CSG a.s., Lazaretní 4298/11a, 615 00 Brno, Czech Republic',
      email: 'info@czechoslovakgroup.cz',
    },
    exportContact: 'export@czechoslovakgroup.cz',
    tags: ['DANA', 'TATRA', 'SPH', 'trucks', 'Czech Republic', 'NATO', 'prime', 'Africa export'],
  },

  {
    id: 'excalibur-army',
    name: 'Excalibur Army s.r.o.',
    country: 'Czech Republic',
    nato: true,
    parentGroup: 'CSG a.s.',
    scale: 'tier1',
    products: ['armoured vehicle modernisation', 'BMP upgrades', 'T-72 overhaul', 'Pandur II IFV', 'DITA 155mm SPH', 'Morana 155mm SPH'],
    calibres: ['155mm', '125mm', '30mm', '12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'IDET trade show · CSG export office · direct',
    usp: 'Europe\'s go-to company for Soviet-era vehicle modernisation and overhaul. New DITA 155mm wheeled SPH is a game-changer — truck-mounted howitzer at fraction of Western SPH cost. Perfect for African armies transitioning from Soviet to NATO calibres.',
    description: 'Excalibur Army specialises in armoured vehicle overhaul, modernisation, and new vehicle production. Their core business is upgrading Soviet-era equipment (T-72, BMP-1/2, DANA) with modern fire control, optics, and powertrains. The new DITA 155mm truck-mounted howitzer offers NATO-calibre artillery at very competitive pricing. Active in African markets where Soviet-legacy fleets dominate.',
    website: 'https://www.excaliburarmy.cz',
    contact: {
      general: '+420 571 765 111',
      address: 'Excalibur Army s.r.o., Šternberk, Czech Republic',
      email: 'info@excaliburarmy.cz',
    },
    tags: ['modernisation', 'T-72', 'BMP', 'DITA', '155mm', 'Czech Republic', 'NATO', 'Soviet upgrade'],
  },

  {
    id: 'tatra-defence-vehicle',
    name: 'Tatra Defence Vehicle a.s.',
    country: 'Czech Republic',
    nato: true,
    parentGroup: 'CSG a.s.',
    scale: 'tier1',
    products: ['Pandur II 8x8 IFV', 'TITUS 6x6 MRAP', 'Tatra military trucks', 'special-purpose vehicles', 'logistics platforms'],
    calibres: ['30mm', '12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'IDET trade show · CSG export office · Tatra dealer network',
    usp: 'Tatra air-cooled diesel trucks are legendary for off-road performance in extreme conditions — already operating across Africa. Pandur II IFV is in Czech Army service. TITUS MRAP offers mine protection for peacekeeping operations.',
    description: 'TDV combines the Pandur II wheeled IFV with Tatra\'s world-renowned military truck platform. Tatra trucks are already in service across African nations for logistics and weapon mounting. The Pandur II 8x8 offers a wheeled IFV option competitive with Patria AMV and Piranha. TITUS MRAP designed for French Army (Scorpion programme) adds force protection capability.',
    website: 'https://www.tatradefence.cz',
    contact: {
      general: '+420 571 821 111',
      address: 'Tatra Defence Vehicle a.s., Kopřivnice, Czech Republic',
      email: 'info@tatradefence.cz',
    },
    tags: ['IFV', 'Pandur', 'TITUS', 'trucks', 'MRAP', 'Czech Republic', 'NATO', 'Tatra'],
  },

  // ─── ROMANIA ─────────────────────────────────────────────────────────────────

  {
    id: 'romarm-sa',
    name: 'Romarm S.A.',
    country: 'Romania',
    nato: true,
    parentGroup: 'Romanian Ministry of National Defence',
    scale: 'prime',
    products: ['AK-pattern rifles', 'small arms ammunition', '7.62mm ammunition', '5.56mm transition', 'mortar bombs', 'artillery rounds', 'RPG rockets', 'hand grenades'],
    calibres: ['5.56mm', '7.62x39mm', '7.62x54mm', '12.7mm', '14.5mm', '82mm mortar', '120mm mortar', '122mm', '152mm'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · BSDA trade show · direct',
    usp: 'Romania is a major European producer of AK-pattern weapons and Soviet-calibre ammunition — critical for African customers operating Soviet/Russian equipment. NATO member but retains full Soviet-calibre production capability. Competitive pricing with proven reliability.',
    description: 'Romarm is Romania\'s state defence holding company encompassing the Cugir Arms Factory (small arms), Mechanical Plant Moreni (vehicles), and multiple ammunition plants. Romania uniquely bridges NATO and Soviet-era equipment worlds — producing both 5.56mm NATO and 7.62x39mm Warsaw Pact ammunition. Extensive African export history including Angola, Mozambique, and multiple West African nations.',
    website: 'https://www.romarm.ro',
    contact: {
      general: '+40 21 319 58 80',
      address: 'Romarm S.A., Bd. Timișoara 5B, Sector 6, Bucharest, Romania',
      email: 'office@romarm.ro',
    },
    exportContact: 'export@romarm.ro',
    tags: ['AK', 'ammunition', 'Soviet calibre', '7.62x39', 'Romania', 'NATO', 'Africa export', 'state-owned'],
  },

  {
    id: 'pro-optica',
    name: 'Pro Optica S.A.',
    country: 'Romania',
    nato: true,
    parentGroup: 'Romarm S.A.',
    scale: 'tier2',
    products: ['night vision devices', 'thermal imaging sights', 'weapon sights', 'periscopes', 'laser rangefinders', 'surveillance systems'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'Romarm export office · BSDA trade show',
    usp: 'Romanian optics specialist producing night vision and thermal sights at Eastern European pricing. Products NATO-compatible and fielded by Romanian Army. Competitive alternative to Western optics for budget-conscious African procurement.',
    description: 'Pro Optica manufactures electro-optical systems for military applications, including Gen 2+/Gen 3 night vision goggles, thermal weapon sights, and vehicle-mounted surveillance systems. Part of the Romarm group. Products are tested against NATO standards and offer a strong price/performance ratio for African military modernisation programmes.',
    website: 'https://www.prooptica.ro',
    contact: {
      general: '+40 21 410 55 70',
      address: 'Pro Optica S.A., Str. Biharia 67-77, Sector 1, Bucharest, Romania',
      email: 'office@prooptica.ro',
    },
    tags: ['optics', 'night vision', 'thermal', 'sights', 'Romania', 'NATO', 'Romarm'],
  },

  {
    id: 'romaero-sa',
    name: 'Romaero S.A.',
    country: 'Romania',
    nato: true,
    parentGroup: 'Romanian Ministry of Economy',
    scale: 'tier2',
    products: ['aircraft MRO', 'C-130 maintenance', 'An-26 overhaul', 'IAR-99 trainer', 'aerostructures', 'aircraft component manufacture'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · BSDA trade show · direct',
    usp: 'Romanian aerospace MRO facility with capability on both Western (C-130) and Soviet-era (An-26) aircraft. Relevant for African air forces operating mixed fleets needing affordable maintenance solutions outside of OEM channels.',
    description: 'Romaero is a Bucharest-based aerospace company providing MRO services for military and civilian aircraft. Key capabilities include C-130 Hercules heavy maintenance and Soviet-era transport aircraft overhaul. Also manufactures aerostructures and components as a subcontractor to Airbus and other OEMs. For African air forces, Romaero offers a NATO-member MRO alternative at competitive pricing.',
    website: 'https://www.romaero.ro',
    contact: {
      general: '+40 21 232 04 40',
      address: 'Romaero S.A., Bd. Ficusului 44, Sector 1, Bucharest, Romania',
      email: 'office@romaero.ro',
    },
    tags: ['aerospace', 'MRO', 'C-130', 'aircraft', 'Romania', 'NATO'],
  },

  // ─── BULGARIA ────────────────────────────────────────────────────────────────

  {
    id: 'arsenal-jsco',
    name: 'Arsenal JSCo',
    country: 'Bulgaria',
    nato: true,
    parentGroup: 'Arsenal JSCo (State-owned)',
    scale: 'tier1',
    products: ['AK-pattern rifles', 'AR-M1 rifle', 'machine guns', 'RPG-7 launchers', 'grenade launchers', 'ammunition', 'mortars', 'anti-tank missiles'],
    calibres: ['5.56mm', '7.62x39mm', '7.62x54mm', '12.7mm', '14.5mm', '40mm', '60mm mortar', '82mm mortar'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'direct · defence attaché · Hemus trade show',
    usp: 'Bulgaria\'s flagship arms manufacturer and one of Europe\'s largest producers of Soviet-pattern small arms and ammunition. Massive production capacity, extremely competitive pricing. Extensive African export history — weapons found across the continent. AR-M1 is a modernised AK with NATO-compatible features.',
    description: 'Arsenal JSCo in Kazanlak is Bulgaria\'s premier small arms and light weapons manufacturer with roots dating to 1878. Produces the full AK family, RPG-7, mortars, and vast quantities of ammunition. Known for aggressive pricing and willingness to supply challenging markets. Export footprint spans Africa, Middle East, and Asia. AR-M1 series offers modernised Kalashnikov variants with Picatinny rails and improved ergonomics.',
    website: 'https://www.arsenal-bg.com',
    contact: {
      general: '+359 431 650 50',
      address: 'Arsenal JSCo, 3 Rozova Dolina Str., 6100 Kazanlak, Bulgaria',
      email: 'arsenal@arsenal-bg.com',
    },
    exportContact: 'export@arsenal-bg.com',
    tags: ['AK', 'small arms', 'ammunition', 'RPG', 'Bulgaria', 'NATO', 'Africa export', 'Soviet calibre'],
  },

  {
    id: 'vmz-sopot',
    name: 'VMZ — Vazovski Mashinostroitelni Zavodi EAD',
    country: 'Bulgaria',
    nato: true,
    parentGroup: 'VMZ EAD (State-owned)',
    scale: 'tier1',
    products: ['artillery ammunition', '122mm rounds', '152mm rounds', 'mortar bombs', 'rocket warheads', 'aviation bombs', 'hand grenades', 'demolition charges'],
    calibres: ['82mm mortar', '120mm mortar', '122mm', '152mm', '23mm', '57mm'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'direct · Hemus trade show · defence attaché',
    usp: 'Major European producer of Soviet-calibre artillery and mortar ammunition. 122mm and 152mm rounds at fraction of Western pricing. Bulk capacity for large orders. Bulgarian ammunition is battle-tested and widely fielded across Africa and Middle East.',
    description: 'VMZ Sopot is Bulgaria\'s primary heavy ammunition manufacturer, producing the full range of Soviet-calibre artillery rounds (122mm, 152mm), mortar bombs (82mm, 120mm), aircraft bombs, and rocket warheads. Historically one of the Soviet bloc\'s main ammunition suppliers, VMZ retains massive production capacity. Extensively exported to African and Middle Eastern customers. Quality is reliable and pricing is among the most competitive globally.',
    website: 'https://www.vmz.bg',
    contact: {
      general: '+359 3134 2270',
      address: 'VMZ EAD, 4330 Sopot, Bulgaria',
      email: 'office@vmz.bg',
    },
    exportContact: 'export@vmz.bg',
    tags: ['artillery ammunition', '122mm', '152mm', 'mortar', 'Bulgaria', 'NATO', 'Soviet calibre', 'Africa export'],
  },

  {
    id: 'emco-bulgaria',
    name: 'Emco Ltd',
    country: 'Bulgaria',
    nato: true,
    parentGroup: 'Emco Ltd (Private)',
    scale: 'tier2',
    products: ['small arms ammunition', '5.56mm', '7.62mm', '9mm', '.50 BMG', 'sporting ammunition', 'pyrotechnics'],
    calibres: ['5.56mm', '7.62x39mm', '7.62x51mm', '9mm', '12.7mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'direct · Hemus trade show · trade intermediary',
    usp: 'Private Bulgarian ammunition manufacturer offering NATO and Soviet calibre small arms ammunition. Fast production cycles, competitive pricing. Growing export presence in Africa and Middle East.',
    description: 'Emco is a private Bulgarian ammunition company producing small arms ammunition across NATO and Soviet calibres. Offers flexibility in order sizes and fast turnaround compared to state-owned competitors. Products include military-specification 5.56mm, 7.62mm, and 9mm ammunition. Expanding export footprint with focus on cost-sensitive markets.',
    website: 'https://www.emco-bg.com',
    contact: {
      general: '+359 2 963 3780',
      address: 'Emco Ltd, Sofia, Bulgaria',
      email: 'info@emco-bg.com',
    },
    tags: ['ammunition', 'small arms', 'NATO calibre', 'Soviet calibre', 'Bulgaria', 'NATO', 'private'],
  },

  // ─── SERBIA ──────────────────────────────────────────────────────────────────

  {
    id: 'yugoimport-sdpr',
    name: 'Yugoimport SDPR J.P.',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian Ministry of Defence',
    scale: 'prime',
    products: ['NORA B-52 155mm SPH', 'Lazar IFV', 'Miloš MRAP', 'ammunition', 'missiles', 'rockets', 'armoured vehicles', 'anti-tank systems'],
    calibres: ['155mm', '122mm', '128mm MLRS', '120mm mortar', '82mm mortar', '7.62mm', '12.7mm', '14.5mm', '23mm', '30mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'Partner trade show · direct · defence attaché',
    usp: 'Serbia\'s main defence exporter. NORA B-52 155mm truck-mounted howitzer is a serious competitor — exported to Myanmar, Bangladesh, Kenya, and others. Non-NATO status means fewer export restrictions. Competitive pricing with combat-tested quality from Yugoslav military legacy.',
    description: 'Yugoimport SDPR is Serbia\'s state defence export agency and manufacturer. The NORA B-52 155mm/52 cal wheeled SPH is their flagship product — a truck-mounted gun competing directly with CAESAR and DITA. Lazar 8x8 IFV and Miloš MRAP expand the vehicle portfolio. Serbian defence industry inherits Yugoslavia\'s extensive military-industrial complex and has maintained competitiveness through aggressive pricing and pragmatic export policy.',
    website: 'https://www.yugoimport.com',
    contact: {
      general: '+381 11 244 62 92',
      address: 'Yugoimport SDPR J.P., Bulevar umetnosti 2, 11070 Novi Beograd, Serbia',
      email: 'yugoimport@yugoimport.com',
    },
    exportContact: 'export@yugoimport.com',
    tags: ['NORA', '155mm', 'SPH', 'IFV', 'Lazar', 'MRAP', 'Serbia', 'non-NATO', 'Africa export'],
  },

  {
    id: 'zastava-arms',
    name: 'Zastava Arms',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian Ministry of Defence',
    scale: 'tier1',
    products: ['M70 assault rifle (AK-pattern)', 'M21 rifle', 'M84 machine gun', 'M93 sniper rifle', 'pistols', 'sporting firearms', 'grenade launchers'],
    calibres: ['5.56mm', '7.62x39mm', '7.62x54mm', '12.7mm', '9mm', '.338 Lapua'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'direct · Partner trade show · trade intermediary',
    usp: 'Serbian Zastava M70 is one of the most widely distributed AK-pattern rifles globally — highly regarded for durability. Strong brand recognition in Africa. Also produces the excellent M93 .50 cal sniper rifle. Very competitive pricing.',
    description: 'Zastava Arms in Kragujevac is one of Serbia\'s oldest arms manufacturers (est. 1853). The M70 AK-pattern rifle is their signature product — exported to over 50 countries and widely found across Africa. The M21 5.56mm rifle modernises the platform for NATO calibre. Zastava pistols (M57, CZ-99) are also widely exported. Quality is well-established and pricing undercuts most competitors.',
    website: 'https://www.zastava-arms.rs',
    contact: {
      general: '+381 34 336 890',
      address: 'Zastava Arms, Trg Topolivaca 4, 34000 Kragujevac, Serbia',
      email: 'office@zastava-arms.rs',
    },
    exportContact: 'export@zastava-arms.rs',
    tags: ['M70', 'AK', 'small arms', 'rifles', 'pistols', 'Serbia', 'non-NATO', 'Africa'],
  },

  {
    id: 'krusik-valjevo',
    name: 'Krušik Holding Corporation',
    country: 'Serbia',
    nato: false,
    parentGroup: 'Serbian Ministry of Defence',
    scale: 'tier1',
    products: ['mortar bombs', 'artillery rounds', 'rocket warheads', 'hand grenades', 'rifle grenades', 'anti-tank rockets', 'illuminating rounds', 'smoke rounds'],
    calibres: ['60mm mortar', '82mm mortar', '120mm mortar', '122mm', '128mm MLRS', '155mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'Yugoimport · direct · Partner trade show',
    usp: 'Major Balkan ammunition manufacturer covering full mortar and artillery ammunition families. Competitive pricing and large production capacity inherited from Yugoslav military-industrial complex. Available in both Soviet and NATO calibres.',
    description: 'Krušik in Valjevo produces mortar bombs, artillery projectiles, hand grenades, rifle grenades, and rocket warheads. Inherited substantial production capacity from Yugoslavia and maintains quality standards. Products exported across Africa, Asia, and Middle East through Yugoimport or direct channels. Capable of producing both Soviet-calibre (82mm, 122mm) and NATO-calibre (120mm, 155mm) ammunition.',
    website: 'https://www.krusik.rs',
    contact: {
      general: '+381 14 221 121',
      address: 'Krušik, Vladike Nikolaja 59, 14000 Valjevo, Serbia',
      email: 'office@krusik.rs',
    },
    tags: ['mortar', 'ammunition', 'artillery', 'grenades', 'Serbia', 'non-NATO', 'Soviet calibre'],
  },

  // ─── UKRAINE ─────────────────────────────────────────────────────────────────

  {
    id: 'ukroboronprom',
    name: 'Ukroboronprom (Ukrainian Defense Industry JSC)',
    country: 'Ukraine',
    nato: false,
    parentGroup: 'Ukrainian State (restructured 2023)',
    scale: 'prime',
    products: ['BTR-4 IFV', 'Oplot MBT', 'An-178 transport aircraft', 'Neptun anti-ship missile', 'radar systems', 'armoured vehicles', 'aircraft MRO', 'ammunition'],
    calibres: ['125mm', '30mm', '12.7mm', '7.62mm', '23mm', '122mm', '152mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · Ukrainian MoD · direct (post-conflict)',
    usp: 'Ukraine\'s state defence conglomerate — combat-hardened and battle-tested since 2014. BTR-4 IFV exported to Iraq, Thailand, Indonesia. Oplot MBT exported to Thailand. Neptun anti-ship missile sank the Moskva cruiser. Post-war, Ukrainian industry will be a major export force with unparalleled combat validation.',
    description: 'Ukroboronprom (restructured as Ukrainian Defense Industry JSC) is Ukraine\'s defence industrial champion encompassing 130+ companies. Pre-2022, exported to 60+ countries. Combat experience since 2014 has driven rapid innovation in drones, EW, missiles, and armoured vehicles. Post-conflict, Ukrainian defence companies will have the most combat-validated product portfolio globally. Current focus is domestic supply, but export programmes will resume aggressively.',
    website: 'https://www.ukroboronprom.com.ua',
    contact: {
      general: '+380 44 521 65 49',
      address: 'UDI JSC, 6 Hrushevskoho St., Kyiv, 01008, Ukraine',
      email: 'info@ukroboronprom.com.ua',
    },
    tags: ['BTR-4', 'Oplot', 'Neptun', 'IFV', 'MBT', 'Ukraine', 'non-NATO', 'combat-proven'],
  },

  {
    id: 'ukrainian-armor',
    name: 'Ukrainian Armor LLC',
    country: 'Ukraine',
    nato: false,
    parentGroup: 'Ukrainian Armor (Private)',
    scale: 'tier2',
    products: ['Varta APC', 'Novator IFV', 'Triton APC', 'armoured vehicle modernisation', 'add-on armour kits', 'mine protection upgrades'],
    calibres: ['12.7mm', '7.62mm', '30mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'direct · defence attaché · Arms & Security Kyiv',
    usp: 'Nimble Ukrainian private company producing affordable armoured vehicles. Varta 4x4 APC is battle-tested in Donbas. Post-war, these combat-validated light armoured vehicles will be extremely attractive for African peacekeeping and internal security roles.',
    description: 'Ukrainian Armor is a private Ukrainian company producing light armoured vehicles designed for Ukrainian conditions. The Varta 4x4 APC has been extensively combat-tested since 2015. Also produces add-on armour kits and vehicle modernisation packages. The company represents the new wave of Ukrainian private defence companies born from wartime necessity.',
    website: 'https://www.ukrainianarmor.com',
    contact: {
      general: '+380 44 227 00 70',
      address: 'Ukrainian Armor LLC, Kyiv, Ukraine',
      email: 'info@ukrainianarmor.com',
    },
    tags: ['APC', 'Varta', 'armoured vehicle', 'Ukraine', 'non-NATO', 'private', 'combat-proven'],
  },

  // ─── SLOVAKIA ────────────────────────────────────────────────────────────────

  {
    id: 'konstrukta-defence',
    name: 'Konštrukta-Defence a.s.',
    country: 'Slovakia',
    nato: true,
    parentGroup: 'DMD Group a.s.',
    scale: 'tier1',
    products: ['Zuzana 2 155mm SPH', 'EVA 155mm SPH', 'howitzer modernisation', 'artillery turrets', 'weapon stations'],
    calibres: ['155mm', '152mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'IDEB trade show · direct · CSG group',
    usp: 'Manufacturer of the Zuzana 2 155mm wheeled SPH — one of the most cost-effective NATO-calibre self-propelled guns available. Zuzana 2 sent to Ukraine with excellent combat reports. EVA 155mm truck-mounted howitzer is even more affordable. Ideal for African artillery modernisation.',
    description: 'Konštrukta-Defence is Slovakia\'s premier artillery manufacturer, producing the Zuzana 2 155mm/52 cal wheeled SPH. The Zuzana 2 has been supplied to Ukraine with strong battlefield performance. The newer EVA 155mm is a simpler truck-mounted design offering NATO-calibre artillery at the lowest cost point. Slovakia\'s defence industry maintains strong links to Czech CSG group and Polish PGZ.',
    website: 'https://www.konstrukta-defence.sk',
    contact: {
      general: '+421 33 557 14 11',
      address: 'Konštrukta-Defence a.s., Lieskovec 575/25, 018 41 Dubnica nad Váhom, Slovakia',
      email: 'info@konstrukta-defence.sk',
    },
    tags: ['Zuzana', '155mm', 'SPH', 'artillery', 'Slovakia', 'NATO', 'combat-proven'],
  },

  {
    id: 'zts-special',
    name: 'ZTS-Special a.s.',
    country: 'Slovakia',
    nato: true,
    parentGroup: 'ZTS Group',
    scale: 'tier2',
    products: ['ammunition', 'mortar bombs', 'artillery components', 'fuzes', 'pyrotechnics', 'demolition charges', 'hand grenades'],
    calibres: ['60mm mortar', '82mm mortar', '120mm mortar', '122mm', '155mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'IDEB trade show · trade intermediary · CSG group',
    usp: 'Slovak ammunition specialist with dual Soviet/NATO calibre capability. Competitive pricing from Central European production base. Components feed into larger assembly programmes across the region.',
    description: 'ZTS-Special produces ammunition, mortar bombs, fuzes, and pyrotechnic devices in both Soviet and NATO calibres. Part of the broader Central European ammunition supply chain, often producing components that are assembled by CSG or other regional integrators. Competitive pricing and capacity for surge production.',
    website: 'https://www.zts-special.sk',
    contact: {
      general: '+421 48 414 0111',
      address: 'ZTS-Special a.s., Partizánska cesta 75, 974 01 Banská Bystrica, Slovakia',
      email: 'info@zts-special.sk',
    },
    tags: ['ammunition', 'mortar', 'fuzes', 'Slovakia', 'NATO', 'components'],
  },

  // ─── CROATIA ─────────────────────────────────────────────────────────────────

  {
    id: 'hs-produkt',
    name: 'HS Produkt d.o.o.',
    country: 'Croatia',
    nato: true,
    parentGroup: 'HS Produkt (Private)',
    scale: 'tier1',
    products: ['VHS-2 assault rifle', 'HS2000/Springfield XD pistol', 'HS Striker pistol', 'VHS-K2 carbine', 'weapon accessories'],
    calibres: ['5.56mm', '9mm', '.40 S&W', '.45 ACP'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'direct · defence attaché · ASDA trade show',
    usp: 'Croatian small arms manufacturer whose Springfield XD pistol (HS2000) is one of the world\'s best-selling handguns. VHS-2 bullpup rifle is the Croatian Army standard and entering export markets. NATO-standard quality at Eastern European pricing. Known for innovation and reliability.',
    description: 'HS Produkt is a Croatian private arms manufacturer based in Karlovac. Their HS2000 pistol, marketed as Springfield XD in the US, has sold millions worldwide. The VHS-2 bullpup assault rifle is the Croatian Armed Forces standard weapon and has been adopted by several export customers. Quality is among the highest in Eastern Europe, with extensive NATO testing and qualification.',
    website: 'https://www.hs-produkt.hr',
    contact: {
      general: '+385 47 666 666',
      address: 'HS Produkt d.o.o., Mirka Bogovića 7, 47000 Karlovac, Croatia',
      email: 'info@hs-produkt.hr',
    },
    exportContact: 'export@hs-produkt.hr',
    tags: ['VHS-2', 'pistol', 'rifle', 'small arms', 'Croatia', 'NATO', 'Springfield XD'],
  },

  {
    id: 'djuro-djakovic',
    name: 'Đuro Đaković Specijalna Vozila d.d.',
    country: 'Croatia',
    nato: true,
    parentGroup: 'Đuro Đaković Group',
    scale: 'tier2',
    products: ['M-84 MBT overhaul', 'armoured vehicle modernisation', 'bridge laying equipment', 'railway vehicles', 'special-purpose vehicles'],
    calibres: ['125mm', '12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · ASDA trade show · direct',
    usp: 'Croatian manufacturer experienced with M-84 (T-72 derivative) MBT production and overhaul. Relevant for African armies operating T-72/M-84 fleets seeking affordable maintenance and modernisation from a NATO-member supplier.',
    description: 'Đuro Đaković in Slavonski Brod historically produced the M-84 MBT (Yugoslav T-72 variant) and retains armoured vehicle maintenance and modernisation capability. The company can overhaul T-72 family vehicles and produce armoured special-purpose vehicles. For African T-72 operators, this offers a NATO-country alternative to Russian/Ukrainian/Czech overhaul options.',
    website: 'https://www.dfrv.hr',
    contact: {
      general: '+385 35 206 333',
      address: 'Đuro Đaković Specijalna Vozila d.d., Dr. Mile Budaka 1, 35000 Slavonski Brod, Croatia',
      email: 'info@dfrv.hr',
    },
    tags: ['M-84', 'T-72', 'MBT', 'armoured vehicle', 'modernisation', 'Croatia', 'NATO'],
  },

  // ─── HUNGARY ─────────────────────────────────────────────────────────────────

  {
    id: 'hm-electronics-hungary',
    name: 'HM Electronics Zrt.',
    country: 'Hungary',
    nato: true,
    parentGroup: 'Hungarian Ministry of Defence',
    scale: 'tier2',
    products: ['military communications', 'software-defined radios', 'electronic warfare systems', 'C4ISR', 'cyber defence systems', 'military IT'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'defence attaché · direct · Hungarian MoD',
    usp: 'Hungary\'s military electronics champion developing indigenous C4ISR and communication systems. NATO interoperable. Hungary\'s defence industrial revival (Lynx IFV production from 2024) is creating new opportunities. Affordable electronic systems for modernisation programmes.',
    description: 'HM Electronics is part of Hungary\'s renewed defence industrial push, developing military communications, SDR systems, and C4ISR solutions. Hungary\'s decision to produce the KF41 Lynx IFV domestically (Rheinmetall partnership) is revitalising the entire Hungarian defence sector. HM Electronics provides the electronic backbone for Hungarian Armed Forces modernisation and offers exportable C4ISR packages.',
    website: 'https://www.hmei.hu',
    contact: {
      general: '+36 1 336 3700',
      address: 'HM Electronics Zrt., Budapest, Hungary',
      email: 'info@hmei.hu',
    },
    tags: ['C4ISR', 'communications', 'electronics', 'SDR', 'Hungary', 'NATO', 'cyber'],
  },

  // ─── INDIA ───────────────────────────────────────────────────────────────────

  {
    id: 'hal-india',
    name: 'Hindustan Aeronautics Limited (HAL)',
    country: 'India',
    nato: false,
    parentGroup: 'Indian Ministry of Defence (DPSU)',
    scale: 'prime',
    products: ['Tejas LCA', 'Dhruv helicopter', 'LCH Prachand', 'Dornier Do-228', 'Su-30MKI (licence)', 'aircraft MRO', 'aero engines', 'avionics'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Indian defence attaché · government-to-government',
    usp: 'India\'s aerospace prime. Tejas LCA is being actively marketed to African and Asian nations as an affordable 4th-gen fighter. Dhruv/ALH is the most exported Indian defence platform (Mauritius, Nepal, Maldives, Ecuador). Do-228 light transport ideal for African maritime patrol. India\'s "Make in India" defence diplomacy offers attractive offset/ToT packages.',
    description: 'HAL is India\'s largest defence company and the primary aerospace manufacturer. The Tejas Light Combat Aircraft is positioned as an affordable fighter for nations priced out of F-16/Gripen markets. The Dhruv advanced light helicopter has been exported with mixed results but offers strong value. Do-228 maritime patrol variant is relevant for coastal African nations. HAL\'s Su-30MKI production line demonstrates large-scale aircraft manufacturing capability. India actively uses defence exports as diplomatic tool in Africa.',
    website: 'https://www.hal-india.co.in',
    contact: {
      general: '+91 80 2232 0231',
      address: 'HAL, 15/1 Cubbon Road, Bangalore 560 001, India',
      email: 'cmd@hal-india.co.in',
    },
    exportContact: 'marketing@hal-india.co.in',
    tags: ['Tejas', 'helicopter', 'Dhruv', 'aircraft', 'India', 'non-NATO', 'Africa', 'Make in India'],
  },

  {
    id: 'bel-india',
    name: 'Bharat Electronics Limited (BEL)',
    country: 'India',
    nato: false,
    parentGroup: 'Indian Ministry of Defence (DPSU)',
    scale: 'prime',
    products: ['radar systems', 'electronic warfare', 'electro-optics', 'communication systems', 'missile systems', 'naval systems', 'homeland security', 'night vision'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Indian defence attaché · direct',
    usp: 'India\'s defence electronics powerhouse. Produces radars, EW suites, and C4ISR at a fraction of Western pricing. Active export programme to 30+ countries. Coastal surveillance radars already deployed in Mauritius, Seychelles, and Maldives — pathway to Lusophone coastal nations.',
    description: 'BEL is India\'s largest defence electronics company with nine manufacturing units across India. Key products include battlefield surveillance radars, electronic warfare suites, tactical communication systems, and electro-optical fire control systems. BEL has supplied coastal surveillance systems to island nations and is actively marketing to African countries. Pricing is 40-60% below Western equivalents with solid reliability.',
    website: 'https://www.bel-india.in',
    contact: {
      general: '+91 80 2503 9300',
      address: 'BEL, Outer Ring Road, Nagavara, Bangalore 560 045, India',
      email: 'marketing@bel-india.in',
    },
    exportContact: 'international@bel-india.in',
    tags: ['radar', 'electronics', 'EW', 'C4ISR', 'India', 'non-NATO', 'coastal surveillance', 'Make in India'],
  },

  {
    id: 'bdl-india',
    name: 'Bharat Dynamics Limited (BDL)',
    country: 'India',
    nato: false,
    parentGroup: 'Indian Ministry of Defence (DPSU)',
    scale: 'tier1',
    products: ['Akash SAM', 'Konkurs ATGM (licence)', 'Milan ATGM (licence)', 'Nag ATGM', 'Astra BVR missile', 'torpedo systems', 'counter-measure systems'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Indian defence attaché · government-to-government',
    usp: 'India\'s sole missile manufacturer. Akash surface-to-air missile is being offered for export at highly competitive pricing — a fraction of Patriot/NASAMS cost. Anti-tank missile portfolio covers all ranges. India\'s missile diplomacy is a growing export vector.',
    description: 'BDL in Hyderabad manufactures guided missiles, torpedoes, and allied defence equipment. The Akash medium-range SAM is the flagship export product, offering area air defence at pricing accessible to developing nations. Also produces licence-built Konkurs and Milan ATGMs. The Nag fire-and-forget ATGM and Astra BVR air-to-air missile represent indigenous capabilities. Export pushes are active across Southeast Asia, Middle East, and Africa.',
    website: 'https://www.bdl-india.in',
    contact: {
      general: '+91 40 2434 0550',
      address: 'BDL, Kanchanbagh, Hyderabad 500 058, India',
      email: 'info@bdl-india.in',
    },
    exportContact: 'export@bdl-india.in',
    tags: ['missiles', 'Akash', 'SAM', 'ATGM', 'India', 'non-NATO', 'Make in India'],
  },

  {
    id: 'tata-advanced-systems',
    name: 'Tata Advanced Systems Limited (TASL)',
    country: 'India',
    nato: false,
    parentGroup: 'Tata Group',
    scale: 'tier1',
    products: ['C-130J aerostructures', 'AH-64 fuselages', 'UAVs', 'armoured vehicles', 'missile systems', 'defence electronics', 'artillery systems'],
    calibres: ['155mm', '12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Tata corporate · direct',
    usp: 'Tata Group\'s defence arm — India\'s most trusted private conglomerate entering defence with massive resources. Partnership with Lockheed Martin (C-130J), Boeing (AH-64), and Airbus. Tata brand carries weight globally. 155mm artillery programme in development.',
    description: 'TASL is the defence subsidiary of Tata Sons, leveraging the Tata Group\'s massive industrial base for defence manufacturing. Key partnerships include C-130J aerostructure production with Lockheed Martin and AH-64 Apache fuselage production with Boeing. Indigenous programmes include tactical UAVs and armoured vehicles. The Tata brand is well-known across Africa through Tata Motors and other group companies, providing built-in market access.',
    website: 'https://www.tataadvancedsystems.com',
    contact: {
      general: '+91 40 6716 2000',
      address: 'TASL, Plot No. 25, Financial District, Nanakramguda, Hyderabad 500 032, India',
      email: 'info@tasl.co.in',
    },
    tags: ['aerospace', 'UAV', 'armoured vehicle', '155mm', 'India', 'non-NATO', 'Tata', 'private'],
  },

  {
    id: 'lt-defence',
    name: 'Larsen & Toubro Defence',
    country: 'India',
    nato: false,
    parentGroup: 'Larsen & Toubro Ltd',
    scale: 'tier1',
    products: ['K9 Vajra 155mm SPH', 'ATAGS howitzer', 'Pinaka MLRS', 'missile systems', 'naval platforms', 'armoured systems', 'air defence systems'],
    calibres: ['155mm', '120mm mortar', '214mm MLRS'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · L&T corporate · direct',
    usp: 'India\'s premier private-sector defence integrator. Produces K9 Vajra 155mm SPH under Samsung Techwin licence — high-volume production capability. Pinaka MLRS is combat-proven on India-Pakistan border. L&T\'s engineering excellence translates well to defence manufacturing.',
    description: 'L&T Defence is the military arm of India\'s largest engineering conglomerate. Key products include the K9 Vajra 155mm SPH (produced under Korean licence with increasing indigenous content), Pinaka MLRS, and naval weapon systems. L&T also manufactures the ATAGS (Advanced Towed Artillery Gun System) 155mm howitzer. The company\'s world-class engineering capability and project management makes it a credible defence partner. L&T already operates across Africa in infrastructure projects.',
    website: 'https://www.larsentoubro.com/defence',
    contact: {
      general: '+91 22 6752 5656',
      address: 'L&T Defence, L&T House, Ballard Estate, Mumbai 400 001, India',
      email: 'defence@larsentoubro.com',
    },
    tags: ['K9 Vajra', '155mm', 'SPH', 'MLRS', 'Pinaka', 'India', 'non-NATO', 'private', 'artillery'],
  },

  {
    id: 'mahindra-defence',
    name: 'Mahindra Defence Systems Ltd',
    country: 'India',
    nato: false,
    parentGroup: 'Mahindra Group',
    scale: 'tier2',
    products: ['Armado APC', 'ALSV light strike vehicle', 'armoured vehicles', 'mine-protected vehicles', 'naval vessels', 'airborne systems'],
    calibres: ['12.7mm', '7.62mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Mahindra corporate · direct',
    usp: 'Mahindra Group\'s defence arm — brand is ubiquitous across Africa through Mahindra automotive and tractors. Armado APC and ALSV light strike vehicles offer affordable tactical mobility. Leverages Mahindra\'s existing African dealer and service network.',
    description: 'Mahindra Defence produces light armoured vehicles, mine-protected vehicles, and tactical platforms leveraging the group\'s automotive engineering. The Armado APC offers basic armoured personnel transport. ALSV (Armoured Light Specialist Vehicle) provides rapid-reaction capability. The massive advantage is Mahindra\'s existing presence across Africa through its automotive and tractor divisions — providing ready-made logistics, spare parts, and service infrastructure.',
    website: 'https://www.mahindra.com/defence',
    contact: {
      general: '+91 20 2601 8888',
      address: 'Mahindra Defence, Mahindra Towers, Worli, Mumbai 400 018, India',
      email: 'defence@mahindra.com',
    },
    tags: ['APC', 'armoured vehicle', 'light strike', 'India', 'non-NATO', 'Mahindra', 'Africa presence'],
  },

  {
    id: 'kalyani-group-defence',
    name: 'Kalyani Group (Bharat Forge Defence)',
    country: 'India',
    nato: false,
    parentGroup: 'Kalyani Group',
    scale: 'tier1',
    products: ['ATAGS 155mm howitzer', 'Kalyani M4 armoured vehicle', 'artillery barrels', 'armoured vehicle hulls', 'defence forgings', 'ammunition components'],
    calibres: ['155mm', '105mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · direct · Bharat Forge corporate',
    usp: 'India\'s forge king — Bharat Forge produces artillery gun barrels and armoured vehicle components for Indian and export programmes. ATAGS 155mm howitzer (co-developed with DRDO) achieved world-record range of 48km. Kalyani M4 is an affordable 4x4 armoured vehicle for African markets.',
    description: 'Kalyani Group through Bharat Forge is India\'s premier metallurgical and forging powerhouse, now a major defence manufacturer. Key product is the ATAGS 155mm/52 cal towed howitzer which set a world range record. Kalyani M4 armoured vehicle is specifically designed for export to price-sensitive markets. Bharat Forge also produces artillery barrels, tank components, and ammunition bodies. World-class manufacturing capability at Indian cost structure.',
    website: 'https://www.bharatforge.com/defence',
    contact: {
      general: '+91 20 6704 2777',
      address: 'Bharat Forge Ltd, Mundhwa, Pune 411 036, India',
      email: 'defence@bharatforge.com',
    },
    tags: ['ATAGS', '155mm', 'howitzer', 'forging', 'artillery', 'armoured vehicle', 'India', 'non-NATO'],
  },

  {
    id: 'ofb-india',
    name: 'Ordnance Factory Board (now AVNL/MIL/AWEIL/YIL/TCL/GIL/NIPL)',
    country: 'India',
    nato: false,
    parentGroup: 'Indian Ministry of Defence (7 new DPSUs)',
    scale: 'prime',
    products: ['small arms', 'ammunition (all calibres)', 'armoured vehicles', 'artillery', 'explosives', 'propellants', 'military clothing', 'parachutes'],
    calibres: ['5.56mm', '7.62mm', '9mm', '12.7mm', '30mm', '105mm', '130mm', '155mm', '82mm mortar', '120mm mortar'],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Indian defence attaché · government-to-government',
    usp: 'India\'s massive state ordnance network (41 factories, restructured into 7 corporations in 2021). Produces everything from bullets to tanks. Scale enables very competitive bulk ammunition pricing. OFB ammunition has been exported to 30+ countries including African nations.',
    description: 'The former Ordnance Factory Board has been corporatised into 7 defence public sector undertakings. Together they form one of the world\'s largest ammunition and defence equipment manufacturing networks. Key exports include small arms ammunition, mortar bombs, and artillery rounds. The sheer scale allows competitive pricing for bulk orders. Indian government actively promotes these companies for African defence cooperation agreements.',
    website: 'https://www.ddpmod.gov.in',
    contact: {
      general: '+91 33 2248 1830',
      address: 'Various locations across India (HQ: Kolkata)',
      email: 'dgof@ofb.gov.in',
    },
    tags: ['ammunition', 'ordnance', 'small arms', 'artillery', 'India', 'non-NATO', 'state-owned', 'bulk'],
  },

  {
    id: 'ashok-leyland-defence',
    name: 'Ashok Leyland Defence Systems',
    country: 'India',
    nato: false,
    parentGroup: 'Hinduja Group (Ashok Leyland)',
    scale: 'tier2',
    products: ['Stallion military truck', 'Super Stallion logistics vehicle', 'mine-protected vehicles', 'field artillery tractors', 'bridge systems', 'specialist military vehicles'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Ashok Leyland dealer network · direct',
    usp: 'India\'s premier military vehicle manufacturer — Stallion truck family is the backbone of Indian Army logistics (70,000+ in service). Already present in multiple African countries through commercial truck sales. Military vehicles leverage proven commercial platforms for reliability and spares availability.',
    description: 'Ashok Leyland Defence produces military logistics vehicles, mine-protected vehicles, and specialist platforms. The Stallion 4x4 and Super Stallion 6x6 trucks form the backbone of Indian military logistics. For African militaries, the key advantage is Ashok Leyland\'s existing commercial presence across the continent — dealers, service centres, and spare parts networks are already established in many African countries.',
    website: 'https://www.ashokleyland.com/defence',
    contact: {
      general: '+91 44 2220 6000',
      address: 'Ashok Leyland Ltd, No. 1 Sardar Patel Road, Guindy, Chennai 600 032, India',
      email: 'defence@ashokleyland.com',
    },
    tags: ['trucks', 'logistics', 'military vehicle', 'Stallion', 'India', 'non-NATO', 'Africa presence'],
  },

  {
    id: 'adani-defence',
    name: 'Adani Defence & Aerospace',
    country: 'India',
    nato: false,
    parentGroup: 'Adani Group',
    scale: 'tier2',
    products: ['small arms (IWI licence)', 'UAVs', 'ammunition', 'Hermes 900 UAV (Elbit licence)', 'aerostructures', 'naval platforms'],
    calibres: ['5.56mm', '7.62mm', '9mm'],
    itar_controlled: false,
    lusophone_experience: false,
    arkmurus_relationship: 'none',
    contact_route: 'DefExpo trade show · Adani corporate · direct',
    usp: 'India\'s newest and most aggressive private defence entrant. Israeli technology partnerships (IWI small arms, Elbit UAVs) manufactured in India at lower cost. Adani Group\'s infrastructure empire (ports, logistics, power) provides unique dual-use capabilities across Africa.',
    description: 'Adani Defence is the rapidly growing defence arm of the Adani conglomerate. Key partnerships include IWI small arms production (Negev LMG, Tavor, Galil ACE) and Elbit Hermes 900 UAV production. Adani leverages its massive infrastructure presence for defence applications. The group already operates in multiple African countries through its ports and energy businesses, providing a unique market entry pathway for defence products.',
    website: 'https://www.adanidefence.com',
    contact: {
      general: '+91 79 2656 5555',
      address: 'Adani Defence, Shantigram, S.G. Highway, Ahmedabad 382 421, India',
      email: 'defence@adani.com',
    },
    tags: ['small arms', 'UAV', 'ammunition', 'India', 'non-NATO', 'Adani', 'Israeli tech', 'private'],
  },

  {
    id: 'mku-india',
    name: 'MKU Limited',
    country: 'India',
    nato: false,
    parentGroup: 'MKU Limited (Private)',
    scale: 'specialist',
    products: ['body armour', 'ballistic helmets', 'armoured vehicle protection kits', 'bulletproof vests', 'vehicle armouring', 'bomb suits'],
    calibres: [],
    itar_controlled: false,
    lusophone_experience: true,
    arkmurus_relationship: 'none',
    contact_route: 'direct · DefExpo trade show · Indian defence attaché',
    usp: 'India\'s leading ballistic protection company. Exported to 230+ military and police forces in 100+ countries including multiple African nations. NIJ and STANAG certified. Pricing is 50-70% below Western competitors with equivalent protection levels. Ideal for African army personal protection programmes.',
    description: 'MKU is an Indian private company specialising in ballistic protection — body armour, helmets, and vehicle armouring kits. Already one of the world\'s largest exporters of personal ballistic protection, with products in service across Africa, Asia, and Latin America. Products meet NIJ and STANAG standards. MKU offers an unbeatable price-performance ratio for personal protection equipment, making them a natural partner for African military modernisation programmes requiring large-scale body armour procurement.',
    website: 'https://www.mkugroup.com',
    contact: {
      general: '+91 120 657 3200',
      address: 'MKU Limited, MKU House, 14 Patparganj Industrial Area, Delhi 110 092, India',
      email: 'info@mkugroup.com',
    },
    exportContact: 'exports@mkugroup.com',
    tags: ['body armour', 'ballistic protection', 'helmets', 'India', 'non-NATO', 'specialist', 'Africa export'],
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
  'Croatia': '🇭🇷', 'Hungary': '🇭🇺', 'India': '🇮🇳',
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
