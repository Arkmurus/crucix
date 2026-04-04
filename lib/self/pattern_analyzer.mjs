// lib/self/pattern_analyzer.mjs
// Detects recurring intelligence patterns from historical archive data
// Two layers: rules-based (always available) + LLM-enhanced (when LLM configured)
// Runs weekly via cron — results cached in runs/learning/patterns.json

import { readdirSync, readFileSync, existsSync } from 'fs';
import { join } from 'path';
import { savePatterns } from './learning_store.mjs';

const ARCHIVE_DIR = join(process.cwd(), 'runs', 'archive');

function loadArchiveRuns(maxRuns = 200) {
  if (!existsSync(ARCHIVE_DIR)) return [];
  try {
    const files = readdirSync(ARCHIVE_DIR)
      .filter(f => f.startsWith('run_') && f.endsWith('.json'))
      .sort()
      .reverse()
      .slice(0, maxRuns);

    return files.map(f => {
      try { return JSON.parse(readFileSync(join(ARCHIVE_DIR, f), 'utf8')); }
      catch { return null; }
    }).filter(Boolean);
  } catch {
    return [];
  }
}

// ── Rules-Based Pattern Detection ─────────────────────────────────────────────
// No LLM required — pure statistics on archive data

function detectRulesPatterns(runs) {
  const patterns = [];

  if (runs.length < 5) return patterns;

  // Pattern 1: VIX stress correlates with OSINT signal surge
  const withVixAndSignals = runs.filter(r => r.vix != null && r.signalCount != null);
  if (withVixAndSignals.length >= 10) {
    const high = withVixAndSignals.filter(r => r.vix > 25);
    const low  = withVixAndSignals.filter(r => r.vix <= 25);
    if (high.length >= 3 && low.length >= 3) {
      const avgH = high.reduce((s, r) => s + r.signalCount, 0) / high.length;
      const avgL = low.reduce((s, r) => s + r.signalCount, 0) / low.length;
      if (avgH > avgL * 1.4) {
        patterns.push({
          id: 'vix-osint-correlation',
          name: 'VIX Stress → OSINT Signal Surge',
          confidence: 'HIGH',
          source: 'rules',
          description: `VIX >25 runs average ${Math.round(avgH)} OSINT signals vs ${Math.round(avgL)} in calm markets (${high.length} observations)`,
          implication: 'Monitor OSINT channels closely during equity stress — geopolitical events cluster with financial volatility',
          forArkmurus: 'Arms procurement windows often follow geopolitical stress — elevate pipeline review when VIX spikes',
          detectedAt: new Date().toISOString(),
        });
      }
    }
  }

  // Pattern 2: Sustained directional regime
  const directions = runs.map(r => r.direction).filter(Boolean);
  if (directions.length >= 5) {
    let maxStreak = 1, curStreak = 1, streakDir = directions[0];
    for (let i = 1; i < directions.length; i++) {
      if (directions[i] === directions[i - 1]) {
        curStreak++;
        if (curStreak > maxStreak) { maxStreak = curStreak; streakDir = directions[i]; }
      } else {
        curStreak = 1;
      }
    }
    if (maxStreak >= 4) {
      patterns.push({
        id: 'direction-regime',
        name: `Sustained ${streakDir} Regime Detected`,
        confidence: maxStreak >= 8 ? 'HIGH' : 'MEDIUM',
        source: 'rules',
        description: `${maxStreak} consecutive sweeps showing "${streakDir}" direction`,
        implication: maxStreak >= 8
          ? 'Entrenched regime — adjust positions for sustained trend, watch for reversal triggers'
          : 'Developing trend — confirm direction with 2+ more sweeps before acting',
        forArkmurus: streakDir === 'risk-off'
          ? 'Risk-off regimes correlate with defense budget approvals — increase prospecting cadence'
          : 'Risk-on: focus on existing pipeline conversion vs new origination',
        detectedAt: new Date().toISOString(),
      });
    }
  }

  // Pattern 3: Accelerating critical change frequency
  if (runs.length >= 15) {
    const allCrit  = runs.map(r => r.critChanges || 0);
    const recent   = allCrit.slice(0, 10);
    const baseline = allCrit.slice(10);
    const avgRecent   = recent.reduce((a, b) => a + b, 0) / recent.length;
    const avgBaseline = baseline.reduce((a, b) => a + b, 0) / baseline.length;

    if (avgBaseline > 0 && avgRecent > avgBaseline * 1.5) {
      patterns.push({
        id: 'crit-acceleration',
        name: 'Critical Signal Acceleration',
        confidence: 'HIGH',
        source: 'rules',
        description: `Recent 10-sweep average: ${avgRecent.toFixed(1)} critical changes vs ${avgBaseline.toFixed(1)} baseline (+${Math.round((avgRecent / avgBaseline - 1) * 100)}%)`,
        implication: 'Systemic instability accelerating — review tail-risk exposures on all active contracts',
        forArkmurus: 'Escalating critical changes often precede emergency procurement — prioritise response capability',
        detectedAt: new Date().toISOString(),
      });
    }
  }

  // Pattern 4: Oil price / conflict correlation
  const withOil = runs.filter(r => r.wti != null && r.signalCount != null);
  if (withOil.length >= 15) {
    const highOil = withOil.filter(r => r.wti > 85);
    const lowOil  = withOil.filter(r => r.wti <= 85);
    if (highOil.length >= 3 && lowOil.length >= 3) {
      const avgHigh = highOil.reduce((s, r) => s + r.signalCount, 0) / highOil.length;
      const avgLow  = lowOil.reduce((s, r) => s + r.signalCount, 0) / lowOil.length;
      if (avgHigh > avgLow * 1.3) {
        patterns.push({
          id: 'oil-conflict-correlation',
          name: 'High Oil → Elevated Conflict Signals',
          confidence: 'MEDIUM',
          source: 'rules',
          description: `WTI >$85 correlates with ${Math.round(((avgHigh / avgLow) - 1) * 100)}% more OSINT signals (${highOil.length} high-oil observations)`,
          implication: 'Energy price spikes co-occur with geopolitical stress — energy and security signals reinforce each other',
          forArkmurus: 'High-oil environments signal active resource conflict — elevate Angola/Mozambique monitoring',
          detectedAt: new Date().toISOString(),
        });
      }
    }
  }

  return patterns;
}

// ── LLM-Enhanced Pattern Detection ────────────────────────────────────────────

async function detectLLMPatterns(llmProvider, runs) {
  if (!llmProvider?.isConfigured || runs.length < 10) return [];

  // Compact archive for LLM context (avoid token bloat)
  const compact = runs.slice(0, 60).map(r => ({
    d: r.timestamp?.substring(0, 10),
    dir: r.direction,
    sig: r.signalCount,
    crit: r.critChanges,
    vix: r.vix,
    wti: r.wti,
    top: (r.topSignals || []).slice(0, 2).map(s => (s.text || '').substring(0, 50)),
  }));

  const systemPrompt = `You are an intelligence pattern analyst for Arkmurus, a defense brokering firm focused on Lusophone Africa (Angola, Mozambique, Guinea-Bissau) and emerging markets.

Analyze this historical sweep data (each entry = one 15-min intelligence sweep) and identify 3-5 actionable patterns that:
1. Preceded arms procurement activity in target markets
2. Signal budget availability / procurement windows opening
3. Show correlation between economic signals and defense activity
4. Identify Lusophone Africa-specific escalation patterns

For each pattern output a JSON object:
{
  "id": "snake_case_id",
  "name": "Short descriptive name",
  "confidence": "HIGH|MEDIUM|LOW",
  "description": "What the data shows (cite numbers)",
  "implication": "What this means for intelligence positioning",
  "forArkmurus": "Direct sales/brokering implication"
}

Output ONLY a valid JSON array. No markdown.`;

  try {
    const result = await llmProvider.complete(
      systemPrompt,
      JSON.stringify(compact, null, 2),
      { maxTokens: 2000, timeout: 60000 }
    );

    if (!result || !result.text) return [];

    let text = result.text.trim();
    if (text.startsWith('```')) text = text.replace(/^```(?:json)?\n?/, '').replace(/\n?```$/, '');

    const parsed = JSON.parse(text);
    if (!Array.isArray(parsed)) return [];

    return parsed
      .filter(p => p.id && p.name && p.confidence)
      .map(p => ({ ...p, source: 'llm', detectedAt: new Date().toISOString() }));
  } catch (err) {
    console.error('[PatternAnalyzer] LLM analysis failed:', err.message);
    return [];
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

export async function analyzePatterns(llmProvider = null) {
  const runs = loadArchiveRuns();
  if (runs.length < 5) {
    console.log(`[PatternAnalyzer] Insufficient data (${runs.length} runs) — need 5+ for analysis`);
    return { patterns: [], runsAnalyzed: runs.length };
  }

  const rulePatterns = detectRulesPatterns(runs);
  const llmPatterns  = await detectLLMPatterns(llmProvider, runs);

  // Merge: LLM patterns first (richer context), then rules patterns not already covered
  const llmIds = new Set(llmPatterns.map(p => p.id));
  const merged = [
    ...llmPatterns,
    ...rulePatterns.filter(p => !llmIds.has(p.id)),
  ];

  savePatterns(merged);
  console.log(`[PatternAnalyzer] ${merged.length} patterns saved (${rulePatterns.length} rules, ${llmPatterns.length} LLM) from ${runs.length} runs`);

  return { patterns: merged, runsAnalyzed: runs.length };
}

export function formatPatternsForTelegram(patternStore) {
  const patterns = patternStore?.patterns || [];

  if (patterns.length === 0) {
    return '🔍 *INTELLIGENCE PATTERNS*\n\nNo patterns detected yet.\nPattern analysis requires 5+ archive runs — check back after a few hours.';
  }

  const updatedAt = patternStore?.updatedAt
    ? new Date(patternStore.updatedAt).toISOString().slice(0, 10)
    : 'unknown';

  let msg = `🔍 *INTELLIGENCE PATTERNS*\n_Last analyzed: ${updatedAt} · ${patterns.length} patterns_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

  const high   = patterns.filter(p => p.confidence === 'HIGH');
  const medium = patterns.filter(p => p.confidence !== 'HIGH');

  for (const p of [...high, ...medium].slice(0, 6)) {
    const badge = p.confidence === 'HIGH' ? '🔴' : '🟠';
    const src   = p.source === 'llm' ? '·AI' : '·rules';
    msg += `${badge} *${p.name}* ${src}\n`;
    if (p.description) msg += `${p.description.substring(0, 130)}\n`;
    if (p.forArkmurus) msg += `→ _${p.forArkmurus.substring(0, 120)}_\n`;
    msg += '\n';
  }

  msg += `_/learn status · Analysis auto-runs weekly_`;
  return msg;
}
