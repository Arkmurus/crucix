/**
 * ARIA — Fuzzy Entity Matching for Sanctions Screening
 * =====================================================
 * Handles: transliteration, aliases, partial matches, common misspellings.
 *
 * Used by the compliance screening pipeline to match entity names against
 * OFAC, OFSI, UN SC, and OpenSanctions lists with tolerance for:
 *   - Cyrillic / Arabic transliteration variants
 *   - Diacritics and accented characters
 *   - Token reordering (e.g. "Surname, Firstname" vs "Firstname Surname")
 *   - Levenshtein typo tolerance
 *   - Acronym expansion (e.g. IRGC -> Islamic Revolutionary Guard Corps)
 */

// ── Acronym expansion table ────────────────────────────────────────────────────
const ACRONYM_MAP = {
  'irgc':   'islamic revolutionary guard corps',
  'irisl':  'islamic republic of iran shipping lines',
  'dprk':   'democratic peoples republic of korea',
  'prc':    'peoples republic of china',
  'fsa':    'free syrian army',
  'isis':   'islamic state of iraq and syria',
  'isil':   'islamic state of iraq and the levant',
  'hamas':  'harakat al muqawama al islamiyya',
  'pflp':   'popular front for the liberation of palestine',
  'isi':    'inter services intelligence',
  'fsb':    'federal security service',
  'svr':    'foreign intelligence service',
  'gru':    'main intelligence directorate',
  'mois':   'ministry of intelligence and security',
  'aeoi':   'atomic energy organization of iran',
  'iaea':   'international atomic energy agency',
  'opcw':   'organisation for the prohibition of chemical weapons',
  'fatf':   'financial action task force',
  'pla':    'peoples liberation army',
  'plaan':  'peoples liberation army navy',
  'plaaf':  'peoples liberation army air force',
  'plarf':  'peoples liberation army rocket force',
  'wagner': 'wagner group',
  'rsf':    'rapid support forces',
  'fdn':    'forces de defense nationale',
};

// ── Common Cyrillic -> Latin transliterations ──────────────────────────────────
const CYRILLIC_MAP = {
  '\u0430': 'a',  '\u0431': 'b',  '\u0432': 'v',  '\u0433': 'g',  '\u0434': 'd',
  '\u0435': 'e',  '\u0451': 'yo', '\u0436': 'zh', '\u0437': 'z',  '\u0438': 'i',
  '\u0439': 'y',  '\u043a': 'k',  '\u043b': 'l',  '\u043c': 'm',  '\u043d': 'n',
  '\u043e': 'o',  '\u043f': 'p',  '\u0440': 'r',  '\u0441': 's',  '\u0442': 't',
  '\u0443': 'u',  '\u0444': 'f',  '\u0445': 'kh', '\u0446': 'ts', '\u0447': 'ch',
  '\u0448': 'sh', '\u0449': 'shch', '\u044a': '',  '\u044b': 'y',  '\u044c': '',
  '\u044d': 'e',  '\u044e': 'yu', '\u044f': 'ya',
};

// ── Common Arabic romanisation patterns ────────────────────────────────────────
const ARABIC_PATTERNS = [
  [/\bal[\s-]?/gi, 'al '],        // al- prefix normalisation
  [/\bel[\s-]?/gi, 'al '],        // el- variant
  [/\bab[dou][\s-]?/gi, 'abd '],  // abdul/abdu/abdo
  [/\bmohammed?\b/gi, 'muhammad'],
  [/\bmohamad\b/gi, 'muhammad'],
  [/\bmohamed\b/gi, 'muhammad'],
  [/\bmuhamed\b/gi, 'muhammad'],
  [/\bmuhamad\b/gi, 'muhammad'],
  [/\bosama\b/gi, 'usama'],
  [/\bhussein\b/gi, 'husayn'],
  [/\bhussain\b/gi, 'husayn'],
  [/\bqaida\b/gi, 'qaida'],
  [/\bqaeda\b/gi, 'qaida'],
];

// ── Normalise entity name ──────────────────────────────────────────────────────

export function normalizeEntity(name) {
  if (!name || typeof name !== 'string') return '';

  let s = name.toLowerCase().trim();

  // Transliterate Cyrillic
  s = s.replace(/[\u0400-\u04ff]/g, ch => CYRILLIC_MAP[ch] || ch);

  // Strip diacritics (Latin extended)
  s = s.normalize('NFD').replace(/[\u0300-\u036f]/g, '');

  // Apply Arabic romanisation patterns
  for (const [pattern, replacement] of ARABIC_PATTERNS) {
    s = s.replace(pattern, replacement);
  }

  // Remove common legal suffixes
  s = s.replace(/\b(ltd|llc|inc|corp|co|limited|group|holdings|international|gmbh|ag|sa|srl|bv|nv|plc|jsc|ojsc|pjsc|ooo|zao|ao)\b/g, '');

  // Remove punctuation except spaces
  s = s.replace(/[^a-z0-9\s]/g, ' ');

  // Collapse whitespace
  s = s.replace(/\s+/g, ' ').trim();

  return s;
}

// ── Levenshtein distance ───────────────────────────────────────────────────────

function levenshtein(a, b) {
  if (a === b) return 0;
  if (!a.length) return b.length;
  if (!b.length) return a.length;

  // Optimisation: if length difference already exceeds reasonable threshold, skip
  if (Math.abs(a.length - b.length) > Math.max(a.length, b.length) * 0.5) {
    return Math.max(a.length, b.length);
  }

  const matrix = [];
  for (let i = 0; i <= b.length; i++) matrix[i] = [i];
  for (let j = 0; j <= a.length; j++) matrix[0][j] = j;

  for (let i = 1; i <= b.length; i++) {
    for (let j = 1; j <= a.length; j++) {
      const cost = b[i - 1] === a[j - 1] ? 0 : 1;
      matrix[i][j] = Math.min(
        matrix[i - 1][j] + 1,      // deletion
        matrix[i][j - 1] + 1,      // insertion
        matrix[i - 1][j - 1] + cost // substitution
      );
    }
  }
  return matrix[b.length][a.length];
}

// ── Token-based similarity (handles name reordering) ───────────────────────────

function tokenSimilarity(a, b) {
  const tokensA = a.split(/\s+/).filter(Boolean);
  const tokensB = b.split(/\s+/).filter(Boolean);

  if (tokensA.length === 0 || tokensB.length === 0) return 0;

  let matchedScore = 0;
  const usedB = new Set();

  for (const tA of tokensA) {
    let bestScore = 0;
    let bestIdx = -1;
    for (let i = 0; i < tokensB.length; i++) {
      if (usedB.has(i)) continue;
      const tB = tokensB[i];
      const maxLen = Math.max(tA.length, tB.length);
      if (maxLen === 0) continue;
      const dist = levenshtein(tA, tB);
      const sim = 1 - dist / maxLen;
      if (sim > bestScore) {
        bestScore = sim;
        bestIdx = i;
      }
    }
    if (bestIdx >= 0 && bestScore > 0.6) {
      usedB.add(bestIdx);
      matchedScore += bestScore;
    }
  }

  const maxTokens = Math.max(tokensA.length, tokensB.length);
  return matchedScore / maxTokens;
}

// ── Fuzzy match ────────────────────────────────────────────────────────────────

export function fuzzyMatch(query, candidates, threshold = 0.75) {
  const normQuery = normalizeEntity(query);
  if (!normQuery) return [];

  // Expand acronyms: if query looks like an acronym, add expanded form
  const queryVariants = [normQuery];
  const lowerQuery = normQuery.replace(/\s/g, '');
  if (ACRONYM_MAP[lowerQuery]) {
    queryVariants.push(normalizeEntity(ACRONYM_MAP[lowerQuery]));
  }

  const results = [];

  for (const candidate of candidates) {
    const name = typeof candidate === 'string' ? candidate : (candidate.name || candidate.caption || '');
    const normCandidate = normalizeEntity(name);
    if (!normCandidate) continue;

    let bestScore = 0;

    for (const qv of queryVariants) {
      // 1. Exact normalised match
      if (qv === normCandidate) {
        bestScore = 1.0;
        break;
      }

      // 2. Substring containment (scaled by length ratio)
      if (normCandidate.includes(qv) || qv.includes(normCandidate)) {
        const shorter = Math.min(qv.length, normCandidate.length);
        const longer = Math.max(qv.length, normCandidate.length);
        const containScore = shorter / longer;
        bestScore = Math.max(bestScore, Math.max(containScore, 0.85));
      }

      // 3. Token-based similarity (handles reordered names)
      const tokScore = tokenSimilarity(qv, normCandidate);
      bestScore = Math.max(bestScore, tokScore);

      // 4. Levenshtein on full string (for short names / typos)
      const maxLen = Math.max(qv.length, normCandidate.length);
      if (maxLen > 0 && maxLen < 80) {
        const dist = levenshtein(qv, normCandidate);
        const levScore = 1 - dist / maxLen;
        bestScore = Math.max(bestScore, levScore);
      }
    }

    // Also check if the candidate itself is an acronym that expands to match
    const candidateNoSpace = normCandidate.replace(/\s/g, '');
    if (ACRONYM_MAP[candidateNoSpace]) {
      const expanded = normalizeEntity(ACRONYM_MAP[candidateNoSpace]);
      for (const qv of queryVariants) {
        const tokScore = tokenSimilarity(qv, expanded);
        bestScore = Math.max(bestScore, tokScore);
      }
    }

    if (bestScore >= threshold) {
      results.push({
        name,
        normalized: normCandidate,
        score: Math.round(bestScore * 1000) / 1000,
        ...(typeof candidate === 'object' ? candidate : {}),
      });
    }
  }

  return results.sort((a, b) => b.score - a.score);
}

// ── Screen entity against sanctions lists ──────────────────────────────────────

export function screenEntity(entityName, sanctionsLists) {
  if (!entityName || !sanctionsLists) {
    return { matched: false, matches: [], risk_level: 'unknown' };
  }

  const allMatches = [];

  for (const list of sanctionsLists) {
    const listName = list.name || list.source || 'unknown';
    const entries = list.entries || list.updates || [];

    // Build candidate array with names flattened
    const candidates = [];
    for (const entry of entries) {
      const names = entry.names || [entry.name || entry.caption || ''];
      for (const n of names) {
        if (n) {
          candidates.push({
            name: n,
            uid: entry.uid || entry.id || '',
            type: entry.type || entry.schema || '',
            programs: entry.programs || entry.datasets || [],
            list: listName,
          });
        }
      }
    }

    const hits = fuzzyMatch(entityName, candidates, 0.75);
    for (const hit of hits) {
      allMatches.push({
        list: hit.list || listName,
        name: hit.name,
        score: hit.score,
        type: hit.type || '',
        uid: hit.uid || '',
        programs: hit.programs || [],
      });
    }
  }

  // Deduplicate by name+list
  const seen = new Set();
  const deduped = [];
  for (const m of allMatches) {
    const key = `${m.list}:${m.name}`;
    if (!seen.has(key)) {
      seen.add(key);
      deduped.push(m);
    }
  }

  // Determine risk level
  let risk_level = 'clear';
  if (deduped.length > 0) {
    const maxScore = Math.max(...deduped.map(m => m.score));
    if (maxScore >= 0.95) risk_level = 'critical';
    else if (maxScore >= 0.85) risk_level = 'high';
    else if (maxScore >= 0.75) risk_level = 'medium';
  }

  return {
    matched: deduped.length > 0,
    entity: entityName,
    normalized: normalizeEntity(entityName),
    matches: deduped.slice(0, 20), // cap at 20 results
    risk_level,
    screened_at: new Date().toISOString(),
  };
}
