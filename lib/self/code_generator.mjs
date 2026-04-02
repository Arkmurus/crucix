// lib/self/code_generator.mjs
// LLM-powered source module generator
// Writes conformant briefing() modules, stages them for human approval via /update apply

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, unlinkSync } from 'fs';
import { join, basename } from 'path';
import { logSelfUpdate } from './learning_store.mjs';

const ROOT        = process.cwd();
const SOURCES_DIR = join(ROOT, 'apis', 'sources');
const STAGING_DIR = join(ROOT, 'runs', 'staged');

function ensureStaging() {
  if (!existsSync(STAGING_DIR)) mkdirSync(STAGING_DIR, { recursive: true });
}

// Load a reference source module for the LLM to learn the pattern
function loadExampleSource() {
  const candidates = ['reliefweb.mjs', 'who.mjs', 'acled.mjs'];
  for (const name of candidates) {
    try {
      return readFileSync(join(SOURCES_DIR, name), 'utf8').substring(0, 2800);
    } catch {}
  }
  return '';
}

// ── Generate a new source module ─────────────────────────────────────────────

export async function generateSourceModule(llmProvider, description, moduleName) {
  if (!llmProvider?.isConfigured) {
    return { success: false, error: 'LLM provider not configured — set ANTHROPIC_API_KEY or similar' };
  }

  const example = loadExampleSource();

  const systemPrompt = `You are an expert JavaScript ESM developer creating an intelligence source module for Crucix, a defense OSINT platform focused on Lusophone Africa defense brokering.

REQUIREMENTS (non-negotiable):
1. Output ONLY raw JavaScript code — no markdown fences, no explanation text
2. First line must be: // [moduleName].mjs - [brief description]
3. Must export exactly: export async function briefing() { ... }
4. Use safeFetch from '../../apis/utils/fetch.mjs' for HTTP: import { safeFetch } from '../../apis/utils/fetch.mjs';
5. Return format must be:
   {
     updates: [{ title, text, url, date, priority }],   // array, may be empty
     signals: [{ text, severity, region, source }],      // array, may be empty
     markers: [{ lat, lng, text, type }],                // array, may be empty
     alerts:  [{ title, tier, text }],                   // array, may be empty
     ...domainSpecificFields                              // any other domain data
   }
6. Wrap EVERYTHING in try/catch — on any error return the empty fallback structure above
7. Include AbortSignal.timeout(25000) on every fetch call
8. Add brief inline comments explaining each data fetch

REFERENCE (working module pattern to follow):
${example}`;

  const userMsg = `Create a Crucix source module:
Module name: ${moduleName}
What it fetches: ${description}`;

  try {
    const result = await llmProvider.complete(systemPrompt, userMsg, {
      maxTokens: 3500,
      timeout: 90000,
    });

    let code = (result.text || '').trim();
    // Strip markdown fences if LLM added them despite instructions
    if (code.startsWith('```')) {
      code = code.replace(/^```(?:javascript|js|mjs)?\n?/, '').replace(/\n?```$/, '').trim();
    }

    // Validate required structure
    const errors = [];
    if (!code.includes('export async function briefing')) errors.push('Missing: export async function briefing()');
    if (!code.includes('try') || !code.includes('catch'))  errors.push('Missing error handling (try/catch)');
    if (!code.includes('safeFetch') && !code.includes('fetch')) errors.push('Missing HTTP fetch call');

    if (errors.length > 0) {
      return { success: false, error: `Generated code failed validation: ${errors.join('; ')}` };
    }

    return { success: true, code, moduleName, description };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

// ── Generate a fix for a broken source ────────────────────────────────────────

export async function generateSourceFix(llmProvider, moduleName, errorMessage) {
  if (!llmProvider?.isConfigured) {
    return { success: false, error: 'LLM provider not configured' };
  }

  const sourcePath = join(SOURCES_DIR, `${moduleName}.mjs`);
  if (!existsSync(sourcePath)) {
    return { success: false, error: `Source file not found: ${moduleName}.mjs` };
  }

  let existingCode;
  try {
    existingCode = readFileSync(sourcePath, 'utf8');
  } catch (err) {
    return { success: false, error: `Cannot read source: ${err.message}` };
  }

  const systemPrompt = `You are debugging a Crucix intelligence source module. The module is failing.

Error: ${errorMessage}

Fix the module so it:
1. Handles the error condition gracefully
2. Never throws — always returns the expected data structure
3. Returns empty arrays/nulls on failure, not undefined
4. Maintains the existing export async function briefing() interface

Output ONLY the corrected full module code, no markdown, no explanation.`;

  try {
    const result = await llmProvider.complete(systemPrompt, existingCode, {
      maxTokens: 3500,
      timeout: 60000,
    });

    let code = (result.text || '').trim();
    if (code.startsWith('```')) {
      code = code.replace(/^```(?:javascript|js|mjs)?\n?/, '').replace(/\n?```$/, '').trim();
    }

    if (!code.includes('export async function briefing')) {
      return { success: false, error: 'Generated fix missing required briefing() export' };
    }

    return { success: true, code, moduleName, type: 'fix' };
  } catch (err) {
    return { success: false, error: err.message };
  }
}

// ── Staging: queue for human approval ────────────────────────────────────────

export function stageModule(moduleName, code, metadata = {}) {
  ensureStaging();
  const stagePath = join(STAGING_DIR, `${moduleName}.mjs.staged`);
  const meta = {
    moduleName,
    stagedAt: new Date().toISOString(),
    type: metadata.type || 'new',
    description: metadata.description || '',
    originalError: metadata.originalError || null,
  };

  // Write code file
  writeFileSync(stagePath, code, 'utf8');
  // Write metadata sidecar
  writeFileSync(stagePath + '.meta.json', JSON.stringify(meta, null, 2), 'utf8');

  logSelfUpdate('stage_module', { moduleName, type: meta.type, description: meta.description });
  console.log(`[CodeGenerator] Staged: ${moduleName} → ${stagePath}`);
  return stagePath;
}

export function getStagedModules() {
  if (!existsSync(STAGING_DIR)) return [];
  try {
    return readdirSync(STAGING_DIR)
      .filter(f => f.endsWith('.mjs.staged'))
      .map(f => {
        const metaPath = join(STAGING_DIR, f + '.meta.json');
        let meta = {};
        try { meta = JSON.parse(readFileSync(metaPath, 'utf8')); } catch {}
        const code = readFileSync(join(STAGING_DIR, f), 'utf8');
        return {
          name: f.replace('.mjs.staged', ''),
          path: join(STAGING_DIR, f),
          lines: code.split('\n').length,
          ...meta,
        };
      });
  } catch {
    return [];
  }
}

export function getStagedCode(moduleName) {
  const stagePath = join(STAGING_DIR, `${moduleName}.mjs.staged`);
  if (!existsSync(stagePath)) return null;
  return readFileSync(stagePath, 'utf8');
}

export function removeStagedModule(moduleName) {
  const stagePath = join(STAGING_DIR, `${moduleName}.mjs.staged`);
  const metaPath  = stagePath + '.meta.json';
  try { if (existsSync(stagePath)) unlinkSync(stagePath); } catch {}
  try { if (existsSync(metaPath))  unlinkSync(metaPath); } catch {}
}

// Format staged modules list for Telegram
export function formatStagedForTelegram(staged) {
  if (!staged || staged.length === 0) {
    return '📦 *STAGED UPDATES*\n\nNo modules staged for deployment.\nUse /update add <description> to generate a new source.';
  }

  let msg = `📦 *STAGED UPDATES (${staged.length})*\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;
  for (const s of staged) {
    const badge = s.type === 'fix' ? '🔧' : '✨';
    msg += `${badge} *${s.name}* (${s.lines} lines)\n`;
    if (s.description) msg += `  ${s.description.substring(0, 100)}\n`;
    msg += `  Staged: ${s.stagedAt?.substring(0, 16).replace('T', ' ')} UTC\n\n`;
  }

  msg += `To deploy: /update apply <name>\nTo discard: /update discard <name>\nTo preview: /update preview <name>`;
  return msg;
}
