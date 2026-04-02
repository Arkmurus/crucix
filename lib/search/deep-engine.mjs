// lib/search/deep-engine.mjs
// Deep entity intelligence engine — multi-phase OSINT research
// Sources: Companies House UK · OpenCorporates · GLEIF · OpenSanctions
//          ICIJ Leaks · OpenOwnership · Wikidata · News · Web · Platform Intel
// AI layer: LLM-driven adaptive querying, network analysis, risk synthesis
// Self-learning: entity graph persists across sessions, source stats improve over time

import {
  upsertEntity, entityId, addRelationship, getRelated, isStale,
  recordSourceStat, recordSearch, saveStore,
} from './entity-store.mjs';

// ── HTTP helpers ──────────────────────────────────────────────────────────────

async function _fetch(url, opts = {}, timeoutMs = 12000) {
  const ctrl = new AbortController();
  const t    = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, {
      signal: ctrl.signal,
      headers: { 'User-Agent': 'Crucix-Intelligence/2.0 (compliance research)', ...opts.headers },
      ...opts,
    });
    clearTimeout(t);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r;
  } catch (e) { clearTimeout(t); throw e; }
}

async function getJSON(url, opts = {}) {
  const r = await _fetch(url, opts);
  return r.json();
}

async function getText(url, opts = {}) {
  const r = await _fetch(url, opts);
  return r.text();
}

// ── Timed source wrapper ─────────────────────────────────────────────────────

async function timed(sourceName, fn) {
  const t = Date.now();
  try {
    const result = await fn();
    recordSourceStat(sourceName, !!(Array.isArray(result) ? result.length : result), Date.now() - t);
    return result;
  } catch (e) {
    recordSourceStat(sourceName, false, Date.now() - t);
    console.warn(`[DeepSearch:${sourceName}] ${e.message}`);
    return null;
  }
}

// ── Companies House UK ────────────────────────────────────────────────────────
// Free API — register at developer.company-information.service.gov.uk
// Env: COMPANIES_HOUSE_API_KEY

function chAuth() {
  const key = process.env.COMPANIES_HOUSE_API_KEY;
  if (!key) return null;
  return 'Basic ' + Buffer.from(key + ':').toString('base64');
}

async function chGet(path) {
  const auth = chAuth();
  if (!auth) return null;
  return getJSON(`https://api.company-information.service.gov.uk${path}`, {
    headers: { Authorization: auth },
  });
}

async function searchCompaniesHouse(query) {
  return timed('CompaniesHouse', async () => {
    const data = await chGet(`/search/companies?q=${encodeURIComponent(query)}&items_per_page=5`);
    if (!data?.items?.length) return [];
    return data.items.map(c => ({
      type:       'company',
      source:     'Companies House UK',
      regNumber:  c.company_number,
      name:       c.title,
      status:     c.company_status,
      kind:       c.company_type,
      address:    c.address_snippet,
      created:    c.date_of_creation,
      url:        `https://find-and-update.company-information.service.gov.uk/company/${c.company_number}`,
      confidence: 0.96,
    }));
  });
}

async function getCompanyDetails(regNumber) {
  return timed('CompaniesHouse/details', async () => {
    const [profile, officers, psc, charges] = await Promise.allSettled([
      chGet(`/company/${regNumber}`),
      chGet(`/company/${regNumber}/officers?items_per_page=50`),
      chGet(`/company/${regNumber}/persons-with-significant-control?items_per_page=25`),
      chGet(`/company/${regNumber}/charges`),
    ]);

    const p  = profile.status  === 'fulfilled' ? profile.value  : null;
    const os = officers.status === 'fulfilled' ? officers.value?.items || [] : [];
    const ps = psc.status      === 'fulfilled' ? psc.value?.items      || [] : [];
    const ch = charges.status  === 'fulfilled' ? charges.value?.items  || [] : [];

    const companyEid = entityId('company', regNumber);
    if (p) {
      upsertEntity(companyEid, {
        type:        'company',
        name:        p.company_name,
        status:      p.company_status,
        kind:        p.type,
        jurisdiction:'gb',
        regNumber,
        address:     p.registered_office_address ? Object.values(p.registered_office_address).filter(Boolean).join(', ') : null,
        created:     p.date_of_creation,
        sic:         (p.sic_codes || []).join(', '),
        accounts:    p.accounts?.last_accounts?.made_up_to || null,
        url:         `https://find-and-update.company-information.service.gov.uk/company/${regNumber}`,
        source:      'Companies House UK',
        confidence:  0.96,
      });
    }

    const officerList = os.filter(o => o.resigned_on == null).map(o => {
      const oid = entityId('person', o.name + (o.date_of_birth?.year || ''));
      upsertEntity(oid, {
        type:       'person',
        name:       o.name,
        role:       o.officer_role,
        appointed:  o.appointed_on,
        nationality:o.nationality,
        dob:        o.date_of_birth ? `${o.date_of_birth.month}/${o.date_of_birth.year}` : null,
        address:    o.address ? Object.values(o.address).filter(Boolean).join(', ') : null,
        officerId:  o.links?.officer?.appointments?.split('/')[2] || null,
        source:     'Companies House UK',
        confidence: 0.96,
      });
      addRelationship(oid, companyEid, o.officer_role || 'director', 0.96, 'Companies House UK');
      return { id: oid, name: o.name, role: o.officer_role, appointed: o.appointed_on, officerId: o.links?.officer?.appointments?.split('/')[2] };
    });

    const pscList = ps.map(p => {
      const pid = entityId('person', p.name || p.identification?.registration_number || Math.random());
      upsertEntity(pid, {
        type:       p.kind?.includes('corporate') ? 'company' : 'person',
        name:       p.name,
        kind:       p.kind,
        natures:    p.natures_of_control || [],
        nationality:p.nationality,
        country:    p.country_of_residence,
        dob:        p.date_of_birth ? `${p.date_of_birth.month}/${p.date_of_birth.year}` : null,
        source:     'Companies House UK (PSC)',
        confidence: 0.95,
      });
      addRelationship(pid, companyEid, 'person_with_significant_control', 0.95, 'Companies House UK');
      return { id: pid, name: p.name, natures: p.natures_of_control, kind: p.kind };
    });

    const chargeList = ch.slice(0, 5).map(c => ({
      description: c.classification?.description || c.charge_code,
      status:      c.status,
      created:     c.created_on,
      satisfied:   c.satisfied_on || null,
      persons:     (c.persons_entitled || []).map(p => p.name),
    }));

    return { profile: p, officers: officerList, psc: pscList, charges: chargeList };
  });
}

async function getOfficerAppointments(officerId) {
  if (!officerId) return [];
  return timed('CompaniesHouse/appointments', async () => {
    const data = await chGet(`/officers/${officerId}/appointments`);
    if (!data?.items?.length) return [];
    return data.items.map(a => ({
      company:   a.appointed_to?.company_name,
      regNumber: a.appointed_to?.company_number,
      status:    a.appointed_to?.company_status,
      role:      a.officer_role,
      appointed: a.appointed_on,
      resigned:  a.resigned_on || null,
    }));
  });
}

// ── OpenCorporates officers ───────────────────────────────────────────────────

async function searchOCOfficers(name) {
  return timed('OpenCorporates/officers', async () => {
    const params = new URLSearchParams({ q: name });
    if (process.env.OPENCORPORATES_API_KEY) params.set('api_token', process.env.OPENCORPORATES_API_KEY);
    const data = await getJSON(`https://api.opencorporates.com/v0.4/officers/search?${params}`);
    const items = data?.results?.officers || [];
    return items.slice(0, 8).map(i => i.officer).map(o => ({
      name:      o.name,
      role:      o.position,
      company:   o.company?.name,
      regNumber: o.company?.company_number,
      jurisdiction: o.company?.jurisdiction_code,
      start:     o.start_date,
      end:       o.end_date || null,
      url:       o.opencorporates_url,
      source:    'OpenCorporates',
    }));
  });
}

async function searchOCCompanies(query) {
  return timed('OpenCorporates', async () => {
    const params = new URLSearchParams({ q: query, limit: '6' });
    if (process.env.OPENCORPORATES_API_KEY) params.set('api_token', process.env.OPENCORPORATES_API_KEY);
    const data = await getJSON(`https://api.opencorporates.com/v0.4/companies/search?${params}`);
    const items = data?.results?.companies || [];
    return items.map(i => i.company).map(c => ({
      type:        'company',
      source:      'OpenCorporates',
      name:        c.name,
      regNumber:   c.company_number,
      jurisdiction:c.jurisdiction_code,
      status:      c.current_status,
      created:     c.incorporation_date,
      url:         c.opencorporates_url,
      confidence:  0.88,
    }));
  });
}

// ── OpenSanctions ─────────────────────────────────────────────────────────────

async function sanctionsCheck(query) {
  return timed('OpenSanctions', async () => {
    const data = await getJSON(
      `https://api.opensanctions.org/search/default?q=${encodeURIComponent(query)}&limit=8`,
      { headers: process.env.OPENSANCTIONS_API_KEY
          ? { Authorization: `ApiKey ${process.env.OPENSANCTIONS_API_KEY}` }
          : {} }
    );
    if (!data?.results?.length) return { sanctioned: false, entities: [], lists: [] };
    const entities = data.results.map(e => ({
      name:       e.caption,
      sanctioned: (e.datasets || []).some(d => ['us_ofac_sdn','eu_fsf','un_sc_sanctions','gb_hmt_sanctions'].includes(d)),
      datasets:   e.datasets || [],
      topics:     e.properties?.topics || [],
      countries:  e.properties?.country || [],
      score:      e.score || 0,
    }));
    const sanctioned = entities.some(e => e.sanctioned);
    const lists = [...new Set(entities.filter(e => e.sanctioned).flatMap(e => e.datasets))];
    return { sanctioned, entities, lists, confidence: 0.95 };
  });
}

// ── GLEIF ─────────────────────────────────────────────────────────────────────

async function gleifSearch(query) {
  return timed('GLEIF', async () => {
    const completions = await getJSON(
      `https://api.gleif.org/api/v1/fuzzycompletions?field=entity.legalName&q=${encodeURIComponent(query)}`
    );
    const lei = completions?.data?.[0]?.relationships?.lei?.data?.id;
    if (!lei) return null;
    const detail = await getJSON(`https://api.gleif.org/api/v1/lei-records/${lei}`);
    const ent    = detail?.data?.attributes;
    if (!ent) return null;
    return {
      lei,
      name:          ent.entity?.legalName?.name,
      legalForm:     ent.entity?.legalForm?.id,
      jurisdiction:  ent.entity?.jurisdiction,
      status:        ent.entity?.status,
      address:       [
        ...(ent.entity?.legalAddress?.addressLines || []),
        ent.entity?.legalAddress?.city,
        ent.entity?.legalAddress?.country,
      ].filter(Boolean).join(', '),
      parent:        ent.entity?.directParent || null,
      ultimate:      ent.entity?.ultimateParent || null,
      registered:    ent.registration?.initialRegistrationDate,
      url:           `https://www.gleif.org/en/lei-data/global-lei-index/lei-record-detail-view/data/${lei}`,
      source:        'GLEIF (Official LEI)',
      confidence:    0.93,
    };
  });
}

// ── ICIJ Offshore Leaks ───────────────────────────────────────────────────────
// Panama Papers, Pandora Papers, Paradise Papers — public database

async function icijSearch(query) {
  return timed('ICIJ Offshore Leaks', async () => {
    // ICIJ exposes a JSON API via their search
    const data = await getJSON(
      `https://offshoreleaks.icij.org/search?q=${encodeURIComponent(query)}&c=&j=&e=3&cat=1`,
      { headers: { Accept: 'application/json' } }
    );
    // ICIJ returns HTML normally; try their graph API
    const graphData = await getJSON(
      `https://offshoreleaks.icij.org/graph-api/v1/search?q=${encodeURIComponent(query)}&cat=1&limit=10`
    ).catch(() => null);

    if (!graphData?.nodes?.length) return [];

    return graphData.nodes.map(n => ({
      name:     n.name,
      type:     n.labels?.[0]?.toLowerCase() || 'entity',
      country:  n.country || n.countryCode,
      database: n.sourceID,
      status:   n.status,
      address:  n.address,
      url:      `https://offshoreleaks.icij.org/nodes/${n.id}`,
      source:   'ICIJ Offshore Leaks',
      note:     'Leaked database — verify independently',
      confidence: 0.75,
    }));
  });
}

// ── OpenOwnership / BODS ──────────────────────────────────────────────────────

async function openOwnershipSearch(query) {
  return timed('OpenOwnership', async () => {
    const data = await getJSON(
      `https://register.openownership.org/entities.json?q=${encodeURIComponent(query)}&country=`
    );
    if (!data?.data?.length) return [];
    return data.data.slice(0, 5).map(e => ({
      name:        e.name,
      jurisdiction:e.jurisdiction_code,
      type:        e.type,
      identifier:  e.identifier_string,
      url:         `https://register.openownership.org/entities/${e.id}`,
      source:      'OpenOwnership',
      confidence:  0.85,
    }));
  });
}

// ── Wikidata structured search ────────────────────────────────────────────────

async function wikidataSearch(query) {
  return timed('Wikidata', async () => {
    const search = await getJSON(
      `https://www.wikidata.org/w/api.php?action=wbsearchentities&search=${encodeURIComponent(query)}&language=en&limit=3&format=json&type=item`
    );
    const entities = search?.search || [];
    if (!entities.length) return [];

    // For first hit, fetch detailed properties
    const id   = entities[0].id;
    const data = await getJSON(`https://www.wikidata.org/wiki/Special:EntityData/${id}.json`);
    const item = data?.entities?.[id];
    if (!item) return entities.map(e => ({ ...e, source: 'Wikidata', confidence: 0.78 }));

    const claim = (prop) => item.claims?.[prop]?.[0]?.mainsnak?.datavalue?.value;
    const claimStr = (prop) => {
      const v = claim(prop);
      return typeof v === 'string' ? v : v?.text || v?.id || null;
    };

    return [{
      id,
      label:       item.labels?.en?.value || entities[0].label,
      description: item.descriptions?.en?.value || entities[0].description,
      url:         `https://www.wikidata.org/wiki/${id}`,
      founded:     claimStr('P571'),
      country:     claimStr('P17'),
      ceo:         claimStr('P169'),
      employees:   claimStr('P1128'),
      revenue:     claimStr('P2139'),
      industry:    claimStr('P452'),
      isin:        claimStr('P946'),
      stock:       claimStr('P414'),
      source:      'Wikidata',
      confidence:  0.82,
    }];
  });
}

// ── Adverse media ─────────────────────────────────────────────────────────────

async function adverseMediaSearch(names) {
  const results = [];
  for (const name of names.slice(0, 5)) {
    try {
      // Google News for adverse terms
      const query = `"${name}" fraud OR investigation OR sanction OR criminal OR lawsuit OR scandal OR corruption`;
      const xml = await getText(
        `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en-US&gl=US&ceid=US:en`
      );
      const items = parseRSS(xml);
      results.push(...items.slice(0, 4).map(i => ({
        ...i, entity: name, type: 'adverse_media', source: 'Google News',
      })));
    } catch {}
  }
  return results;
}

function parseRSS(xml) {
  const items = [];
  const re = /<item[^>]*>([\s\S]*?)<\/item>/gi;
  let m;
  while ((m = re.exec(xml)) !== null) {
    const b   = m[1];
    const get = tag => {
      const r = b.match(new RegExp(`<${tag}[^>]*><!\\[CDATA\\[([\\s\\S]*?)\\]\\]><\\/${tag}>`, 'i'))
             || b.match(new RegExp(`<${tag}[^>]*>([\\s\\S]*?)<\\/${tag}>`, 'i'));
      return r ? r[1].trim().replace(/<[^>]+>/g,'').replace(/&amp;/g,'&').replace(/&quot;/g,'"') : '';
    };
    const title = get('title');
    if (title) items.push({
      title, url: get('link') || get('guid'),
      snippet: get('description').substring(0, 220),
      pubDate: get('pubDate') ? new Date(get('pubDate')).toISOString() : null,
    });
  }
  return items;
}

async function newsSearch(query, limit = 8) {
  try {
    const xml = await getText(
      `https://news.google.com/rss/search?q=${encodeURIComponent(query)}&hl=en-US&gl=US&ceid=US:en`
    );
    return parseRSS(xml).slice(0, limit).map(i => ({ ...i, source: 'Google News' }));
  } catch { return []; }
}

async function webSearch(query, limit = 6) {
  if (process.env.BRAVE_API_KEY) {
    try {
      const data = await getJSON(
        `https://api.search.brave.com/res/v1/web/search?q=${encodeURIComponent(query)}&count=${limit}`,
        { headers: { Accept: 'application/json', 'X-Subscription-Token': process.env.BRAVE_API_KEY } }
      );
      return (data?.web?.results || []).map(r => ({ title: r.title, url: r.url, snippet: r.description, source: 'Brave Search' }));
    } catch {}
  }
  return [];
}

// ── Platform intel cache ──────────────────────────────────────────────────────

function searchIntelCache(query, cachedData) {
  if (!cachedData) return [];
  const q   = query.toLowerCase();
  const hits = [];
  const check = (text, src, url, priority = 'medium') => {
    if (String(text || '').toLowerCase().includes(q)) {
      hits.push({ text: String(text).substring(0, 200), source: src, url, priority });
    }
  };
  for (const s of (cachedData.tg?.urgent || []))            check(s.text, 'OSINT Signal', null, 'high');
  for (const e of (cachedData.exportControl?.updates || [])) check(e.title, 'Export Control', e.url, 'high');
  for (const s of (cachedData.opensanctions?.updates || [])) check(s.name, 'Sanctions DB', null, 'critical');
  for (const n of (cachedData.defenseNews?.updates || []))   check(n.title, 'Defence News', n.url);
  for (const t of (cachedData.thinkTanks?.updates || []))    check(t.title, t.source || 'Think Tank', t.url);
  return hits.slice(0, 8);
}

// ── LLM adaptive layer ────────────────────────────────────────────────────────

async function llmAnalyze(query, collectedData, llmProvider) {
  if (!llmProvider) return null;
  try {
    const prompt = `You are an intelligence analyst performing due diligence on entity "${query}".

Analyze the following data and respond in JSON:

Data collected:
${JSON.stringify({
  companies:   (collectedData.companies || []).slice(0, 3).map(c => ({ name: c.name, jurisdiction: c.jurisdiction || c.regNumber, status: c.status })),
  officers:    (collectedData.officers || []).slice(0, 6).map(o => ({ name: o.name, role: o.role })),
  psc:         (collectedData.psc || []).slice(0, 4).map(p => ({ name: p.name, control: p.natures })),
  gleif:       collectedData.gleif ? { name: collectedData.gleif.name, status: collectedData.gleif.status, parent: collectedData.gleif.parent } : null,
  sanctions:   { sanctioned: collectedData.sanctions?.sanctioned, lists: collectedData.sanctions?.lists?.slice(0,3) },
  icij:        (collectedData.icij || []).slice(0, 3).map(i => ({ name: i.name, database: i.database })),
  news:        (collectedData.news || []).slice(0, 5).map(n => n.title),
  adverseMedia:(collectedData.adverseMedia || []).slice(0, 4).map(n => n.title),
}, null, 2)}

Respond with:
{
  "entityType": "company|person|group|unknown",
  "summary": "2-3 sentence intelligence assessment",
  "keyFacts": ["verified fact 1", "fact 2", "fact 3"],
  "riskLevel": "low|medium|high|critical",
  "riskFactors": ["risk 1"] or [],
  "peopleOfInterest": ["Name — Role/Context"] or [],
  "linkedEntities": ["Entity name"] or [],
  "followUpQueries": ["query 1", "query 2"] or [],
  "sector": "industry sector",
  "redFlags": ["specific concern"] or [],
  "confidence": 0.0-1.0
}`;

    const raw = await llmProvider.complete(prompt, { maxTokens: 900, temperature: 0.1 });
    const m   = raw.match(/\{[\s\S]*\}/);
    return m ? JSON.parse(m[0]) : null;
  } catch (e) {
    console.warn('[DeepSearch] LLM analysis error:', e.message);
    return null;
  }
}

// ── Network confidence scoring ────────────────────────────────────────────────

const SRC_WEIGHTS = {
  'Companies House UK': 0.96, 'GLEIF (Official LEI)': 0.93, 'Companies House UK (PSC)': 0.95,
  'OpenCorporates': 0.88, 'OpenSanctions': 0.95, 'OpenOwnership': 0.85,
  'Wikidata': 0.82, 'Wikipedia': 0.78, 'ICIJ Offshore Leaks': 0.75,
  'Google News': 0.70, 'Brave Search': 0.65,
};

function calcConfidence(sources) {
  if (!sources.length) return 0;
  const w = sources.map(s => SRC_WEIGHTS[s] || 0.60);
  return Math.min(0.97, Math.max(...w) + Math.min(0.15, (w.length - 1) * 0.04));
}

// ── Main deep search orchestrator ─────────────────────────────────────────────

export async function runDeepSearch(query, opts = {}) {
  const { cachedData, llmProvider, onEvent } = opts;
  const emit = (type, data) => { try { onEvent?.({ type, ...data }); } catch {} };
  const start = Date.now();

  emit('phase', { name: 'Initialising', detail: `Deep intelligence search: "${query}"`, icon: '🔍' });

  const result = {
    query, timestamp: new Date().toISOString(),
    companies: [], officers: [], psc: [], appointments: [],
    gleif: null, sanctions: null, icij: [], openOwnership: [],
    wikidata: [], news: [], adverseMedia: [], web: [], intel: [],
    synthesis: null, networkGraph: { nodes: [], edges: [] },
    confidence: 0, activeSources: [], durationMs: 0,
  };

  // ── Phase 1: Seed — fast parallel sweep ─────────────────────────────────────
  emit('phase', { name: 'Phase 1 — Seed Query', detail: 'Querying registries, sanctions & reference databases', icon: '🌐' });

  const [chRes, ocRes, gleifRes, sanctRes, wikRes, ocOfficers] = await Promise.allSettled([
    searchCompaniesHouse(query),
    searchOCCompanies(query),
    gleifSearch(query),
    sanctionsCheck(query),
    wikidataSearch(query),
    searchOCOfficers(query),
  ]);

  const chCompanies = chRes.status === 'fulfilled' && chRes.value ? chRes.value : [];
  const ocCompanies = ocRes.status === 'fulfilled' && ocRes.value ? ocRes.value : [];

  result.companies  = [...chCompanies, ...ocCompanies];
  result.gleif      = gleifRes.status   === 'fulfilled' ? gleifRes.value   : null;
  result.sanctions  = sanctRes.status   === 'fulfilled' ? sanctRes.value   : null;
  result.wikidata   = wikRes.status     === 'fulfilled' ? wikRes.value     : [];
  const ocOfficersList = ocOfficers.status === 'fulfilled' ? ocOfficers.value || [] : [];

  if (result.companies.length)   emit('finding', { category: 'companies',  count: result.companies.length, items: result.companies.slice(0, 3) });
  if (result.gleif)              emit('finding', { category: 'gleif',      data: result.gleif });
  if (result.sanctions?.sanctioned) emit('alert', { severity: 'critical', message: `SANCTIONS MATCH — ${result.sanctions.lists.join(', ')}` });
  if (result.wikidata.length)    emit('finding', { category: 'wikidata',   data: result.wikidata[0] });

  // ── Phase 2: Companies House deep dive ───────────────────────────────────────
  const chKey = !!process.env.COMPANIES_HOUSE_API_KEY;
  if (chKey && chCompanies.length > 0) {
    emit('phase', { name: 'Phase 2 — Corporate Deep Dive', detail: `Pulling officers, PSC & charges for ${Math.min(chCompanies.length, 3)} companies`, icon: '🏢' });

    for (const co of chCompanies.slice(0, 3)) {
      const details = await getCompanyDetails(co.regNumber);
      if (!details) continue;

      result.officers.push(...(details.officers || []));
      result.psc.push(...(details.psc || []));

      if (details.officers?.length) emit('finding', { category: 'officers', company: co.name, items: details.officers.slice(0, 5) });
      if (details.psc?.length)      emit('finding', { category: 'psc',      company: co.name, items: details.psc });
      if (details.charges?.length)  emit('finding', { category: 'charges',  company: co.name, items: details.charges });
    }
  }

  // ── Phase 3: Officer network expansion ──────────────────────────────────────
  if (chKey && result.officers.length > 0) {
    emit('phase', { name: 'Phase 3 — Network Expansion', detail: 'Tracing officer appointments across all registered companies', icon: '🕸️' });

    const appts = [];
    for (const officer of result.officers.slice(0, 6)) {
      if (!officer.officerId) continue;
      const apps = await getOfficerAppointments(officer.officerId);
      if (apps?.length) {
        appts.push({ officer: officer.name, appointments: apps.filter(a => !a.resigned) });
        emit('finding', { category: 'appointments', person: officer.name, count: apps.length, active: apps.filter(a => !a.resigned).length });
      }
    }
    result.appointments = appts;

    // Sanctions check all related company names
    const relatedNames = appts.flatMap(a => a.appointments.map(x => x.company)).filter(Boolean).slice(0, 10);
    for (const name of relatedNames) {
      const s = await sanctionsCheck(name);
      if (s?.sanctioned) {
        emit('alert', { severity: 'high', message: `Related entity "${name}" matches sanctions list: ${s.lists.join(', ')}` });
      }
    }
  }

  // ── Phase 4: OpenOwnership + ICIJ ───────────────────────────────────────────
  emit('phase', { name: 'Phase 4 — Leaked & Ownership Databases', detail: 'Checking ICIJ Offshore Leaks (Panama/Pandora Papers) & OpenOwnership', icon: '📂' });

  const [icijRes, ooRes] = await Promise.allSettled([
    icijSearch(query),
    openOwnershipSearch(query),
  ]);

  result.icij         = icijRes.status === 'fulfilled' ? icijRes.value || [] : [];
  result.openOwnership= ooRes.status   === 'fulfilled' ? ooRes.value   || [] : [];

  if (result.icij.length)         emit('finding', { category: 'icij',         items: result.icij });
  if (result.openOwnership.length) emit('finding', { category: 'openOwnership', items: result.openOwnership });

  // ── Phase 5: News & adverse media sweep ─────────────────────────────────────
  emit('phase', { name: 'Phase 5 — Intelligence Sweep', detail: 'Sweeping news feeds, adverse media & platform intel', icon: '📰' });

  const allNames = [
    query,
    ...result.companies.slice(0, 3).map(c => c.name),
    ...result.officers.slice(0, 4).map(o => o.name),
  ].filter(Boolean);

  const [newsRes, adverseRes, webRes] = await Promise.allSettled([
    newsSearch(query, 10),
    adverseMediaSearch(allNames.slice(0, 4)),
    webSearch(`"${query}" company intelligence`, 6),
  ]);

  result.news        = newsRes.status    === 'fulfilled' ? newsRes.value    || [] : [];
  result.adverseMedia= adverseRes.status === 'fulfilled' ? adverseRes.value || [] : [];
  result.web         = webRes.status     === 'fulfilled' ? webRes.value     || [] : [];
  result.intel       = searchIntelCache(query, cachedData);

  if (result.adverseMedia.length) emit('finding', { category: 'adverseMedia', items: result.adverseMedia.slice(0, 4) });
  if (result.news.length)         emit('finding', { category: 'news',          items: result.news.slice(0, 5) });
  if (result.intel.length)        emit('finding', { category: 'intel',          items: result.intel });

  // ── Phase 6: AI synthesis ────────────────────────────────────────────────────
  emit('phase', { name: 'Phase 6 — AI Synthesis', detail: 'Analysing network, assessing risk, generating intelligence report', icon: '🧠' });

  result.synthesis = await llmAnalyze(query, result, llmProvider);
  if (result.synthesis) emit('synthesis', { data: result.synthesis });

  // ── Phase 7: LLM follow-up queries ───────────────────────────────────────────
  if (result.synthesis?.followUpQueries?.length && llmProvider) {
    emit('phase', { name: 'Phase 7 — Adaptive Follow-up', detail: 'AI-generated follow-up queries based on findings', icon: '🔄' });
    for (const fq of result.synthesis.followUpQueries.slice(0, 3)) {
      const fqNews = await newsSearch(fq, 3);
      if (fqNews.length) {
        result.news.push(...fqNews);
        emit('finding', { category: 'followUp', query: fq, items: fqNews });
      }
    }
  }

  // ── Build network graph ───────────────────────────────────────────────────────
  const nodes = new Map();
  const addNode = (id, label, type, risk = 'normal') => {
    if (!nodes.has(id)) nodes.set(id, { id, label, type, risk });
  };
  const edges = [];

  const rootId = entityId('query', query);
  addNode(rootId, query, 'query', 'root');

  for (const c of result.companies.slice(0, 8)) {
    const cid = entityId('company', c.name);
    addNode(cid, c.name, 'company', c.status === 'dissolved' ? 'dissolved' : 'normal');
    edges.push({ from: rootId, to: cid, label: 'matched' });
  }
  for (const o of result.officers.slice(0, 10)) {
    const oid = entityId('person', o.name);
    addNode(oid, o.name, 'person');
    for (const c of result.companies.slice(0, 3)) {
      edges.push({ from: oid, to: entityId('company', c.name), label: o.role });
    }
  }
  for (const p of result.psc.slice(0, 6)) {
    const pid = entityId('person', p.name);
    addNode(pid, p.name, 'psc', 'highlight');
    for (const c of result.companies.slice(0, 2)) {
      edges.push({ from: pid, to: entityId('company', c.name), label: 'controls' });
    }
  }

  result.networkGraph = { nodes: [...nodes.values()], edges };

  // ── Finalise ──────────────────────────────────────────────────────────────────
  const activeSources = [
    ...(chCompanies.length      ? ['Companies House UK']       : []),
    ...(ocCompanies.length      ? ['OpenCorporates']           : []),
    ...(result.gleif            ? ['GLEIF (Official LEI)']     : []),
    ...(result.sanctions?.entities?.length ? ['OpenSanctions'] : []),
    ...(result.wikidata.length  ? ['Wikidata']                 : []),
    ...(result.icij.length      ? ['ICIJ Offshore Leaks']      : []),
    ...(result.openOwnership.length ? ['OpenOwnership']        : []),
    ...(result.news.length      ? ['Google News']              : []),
    ...(result.adverseMedia.length  ? ['Adverse Media']        : []),
    ...(result.web.length       ? ['Web Intelligence']         : []),
    ...(result.intel.length     ? ['Platform Intel']           : []),
  ];

  result.activeSources   = activeSources;
  result.confidence      = calcConfidence(activeSources.map(s => s));
  result.meetsThreshold  = result.confidence >= 0.80;
  result.durationMs      = Date.now() - start;

  // Store to entity graph
  const entityIds = result.companies.map(c => entityId('company', c.name));
  recordSearch(query, entityIds, result.durationMs);
  saveStore();

  emit('complete', {
    confidence:   result.confidence,
    sources:      activeSources.length,
    durationMs:   result.durationMs,
    companies:    result.companies.length,
    officers:     result.officers.length,
    psc:          result.psc.length,
  });

  console.log(`[DeepSearch] "${query}" complete — ${activeSources.length} sources · conf:${(result.confidence*100).toFixed(0)}% · ${result.durationMs}ms`);

  return result;
}
