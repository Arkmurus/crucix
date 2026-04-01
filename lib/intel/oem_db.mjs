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
