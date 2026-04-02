// lib/self/updater.mjs
// Safe self-update engine — validate → backup → deploy → git commit → restart
// Every change is git-committed for full audit trail and easy rollback
// Restart mechanism: sets runs/.restart_pending flag, server acts on it post-sweep

import { readFileSync, writeFileSync, existsSync, mkdirSync, readdirSync, renameSync, unlinkSync } from 'fs';
import { join, basename } from 'path';
import { spawnSync, execSync } from 'child_process';
import { logSelfUpdate } from './learning_store.mjs';

const ROOT             = process.cwd();
const SOURCES_DIR      = join(ROOT, 'apis', 'sources');
const STAGING_DIR      = join(ROOT, 'runs', 'staged');
const BACKUP_DIR       = join(ROOT, 'runs', 'backups');
const AUTO_SOURCES_FILE = join(ROOT, 'apis', 'auto_sources.mjs');
const RESTART_FLAG     = join(ROOT, 'runs', '.restart_pending');

function ensureDirs() {
  for (const dir of [STAGING_DIR, BACKUP_DIR]) {
    if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
  }
}

// ── Syntax Validation ─────────────────────────────────────────────────────────

export function validateSyntax(code, tempName = 'validation_temp.mjs') {
  ensureDirs();
  const tempPath = join(STAGING_DIR, tempName);
  try {
    writeFileSync(tempPath, code, 'utf8');
    const result = spawnSync(process.execPath, ['--check', tempPath], {
      encoding: 'utf8',
      timeout: 15000,
    });
    return {
      valid: result.status === 0,
      error: result.stderr?.trim() || result.stdout?.trim() || '',
    };
  } finally {
    try { unlinkSync(tempPath); } catch {}
  }
}

// ── Backup ────────────────────────────────────────────────────────────────────

function backupFile(filePath) {
  if (!existsSync(filePath)) return null;
  ensureDirs();
  const ts = new Date().toISOString().replace(/[:.]/g, '-').substring(0, 19);
  const backupPath = join(BACKUP_DIR, `${basename(filePath)}.${ts}.bak`);
  writeFileSync(backupPath, readFileSync(filePath, 'utf8'), 'utf8');
  return backupPath;
}

// ── Git Integration ───────────────────────────────────────────────────────────

function gitCommit(message) {
  try {
    execSync('git add -A', { cwd: ROOT, stdio: 'pipe', timeout: 10000 });
    // Check if there's anything to commit
    const status = spawnSync('git', ['diff', '--cached', '--stat'], {
      cwd: ROOT, encoding: 'utf8', timeout: 5000,
    });
    if (!status.stdout?.trim()) {
      console.log('[Updater] Nothing to commit');
      return true;
    }
    execSync(`git commit -m "${message.replace(/"/g, "'").replace(/\n/g, ' ')}"`, {
      cwd: ROOT, stdio: 'pipe', timeout: 15000,
    });
    console.log('[Updater] Git commit OK:', message.substring(0, 60));
    return true;
  } catch (err) {
    console.error('[Updater] Git commit failed (non-fatal):', err.message?.substring(0, 100));
    return false;
  }
}

// ── auto_sources.mjs Management ───────────────────────────────────────────────
// This file is the only one we auto-modify — briefing.mjs is never touched

function readAutoSources() {
  if (!existsSync(AUTO_SOURCES_FILE)) {
    return { imports: [], registrations: [] };
  }
  const content = readFileSync(AUTO_SOURCES_FILE, 'utf8');

  // Parse existing imports
  const importRx = /^import\s*\{[^}]+\}\s*from\s*'[^']+';/gm;
  const imports = [];
  let m;
  while ((m = importRx.exec(content)) !== null) imports.push(m[0]);

  // Parse existing AUTO_SOURCES array entries
  const entryRx = /\{\s*name:\s*'([^']+)',\s*fn:\s*(\w+)\s*\}/g;
  const registrations = [];
  while ((m = entryRx.exec(content)) !== null) {
    registrations.push({ name: m[1], fn: m[2] });
  }

  return { imports, registrations, raw: content };
}

function writeAutoSources(imports, registrations) {
  backupFile(AUTO_SOURCES_FILE);

  const importLines = imports.join('\n');
  const entries = registrations
    .map(r => `  { name: '${r.name}', fn: ${r.fn} },`)
    .join('\n');

  const content = `// AUTO-MANAGED SOURCE REGISTRY
// Updated by Crucix self-update engine — do not edit manually
// Generated: ${new Date().toISOString()}

${importLines}

export const AUTO_SOURCES = [
${entries}
];
`;
  writeFileSync(AUTO_SOURCES_FILE, content, 'utf8');
}

function addToAutoSources(moduleName, fnName, sourceName) {
  const { imports, registrations } = readAutoSources();

  const importLine = `import { briefing as ${fnName} } from './sources/${moduleName}.mjs';`;
  if (!imports.includes(importLine)) imports.push(importLine);

  if (!registrations.find(r => r.fn === fnName)) {
    registrations.push({ name: sourceName, fn: fnName });
  }

  writeAutoSources(imports, registrations);
}

function removeFromAutoSources(moduleName, fnName) {
  const { imports, registrations } = readAutoSources();
  const filtered = {
    imports: imports.filter(i => !i.includes(`'./sources/${moduleName}.mjs'`)),
    registrations: registrations.filter(r => r.fn !== fnName),
  };
  writeAutoSources(filtered.imports, filtered.registrations);
}

// ── Deploy a staged module ────────────────────────────────────────────────────

export async function deployModule(moduleName) {
  ensureDirs();

  const stagePath = join(STAGING_DIR, `${moduleName}.mjs.staged`);
  if (!existsSync(stagePath)) {
    return { success: false, error: `No staged module: ${moduleName}` };
  }

  const code = readFileSync(stagePath, 'utf8');

  // 1. Syntax validation
  const syntaxCheck = validateSyntax(code, `${moduleName}_check.mjs`);
  if (!syntaxCheck.valid) {
    return { success: false, error: `Syntax error: ${syntaxCheck.error.substring(0, 200)}` };
  }

  const targetPath = join(SOURCES_DIR, `${moduleName}.mjs`);
  const isUpdate   = existsSync(targetPath);

  // 2. Backup existing if present
  const backupPath = isUpdate ? backupFile(targetPath) : null;

  // 3. Write module file
  writeFileSync(targetPath, code, 'utf8');

  // 4. Register in auto_sources.mjs (converts snake_case to camelCase fn name)
  const fnName     = _toCamelCase(moduleName);
  const sourceName = _toDisplayName(moduleName);
  addToAutoSources(moduleName, fnName, sourceName);

  // 5. Validate auto_sources.mjs syntax
  const autoCheck = validateSyntax(
    readFileSync(AUTO_SOURCES_FILE, 'utf8'),
    'auto_sources_check.mjs'
  );
  if (!autoCheck.valid) {
    // Rollback
    if (isUpdate && backupPath) writeFileSync(targetPath, readFileSync(backupPath, 'utf8'));
    else try { unlinkSync(targetPath); } catch {}
    removeFromAutoSources(moduleName, fnName);
    return { success: false, error: `auto_sources.mjs syntax error after update: ${autoCheck.error}` };
  }

  // 6. Git commit
  const action  = isUpdate ? 'fix' : 'feat';
  const message = `${action}(self): ${isUpdate ? 'fix' : 'add'} source module ${moduleName}\n\nAuto-deployed by Crucix self-update engine\nValidated: syntax OK · Timestamp: ${new Date().toISOString()}`;
  gitCommit(message);

  // 7. Log + cleanup
  logSelfUpdate('deploy_module', { moduleName, isUpdate, targetPath, backupPath });
  try { unlinkSync(stagePath); } catch {}
  try { unlinkSync(stagePath + '.meta.json'); } catch {}

  // 8. Set restart flag — server restarts after next sweep completion
  writeFileSync(RESTART_FLAG, new Date().toISOString(), 'utf8');

  return {
    success: true,
    moduleName,
    isUpdate,
    targetPath,
    backupPath,
    message: `✅ ${moduleName} deployed. Server restarts after next sweep to load it.`,
  };
}

// ── Rollback ──────────────────────────────────────────────────────────────────

export function rollbackModule(moduleName) {
  ensureDirs();

  const backups = readdirSync(BACKUP_DIR)
    .filter(f => f.startsWith(`${moduleName}.mjs.`) && f.endsWith('.bak'))
    .sort()
    .reverse();

  if (backups.length === 0) {
    return { success: false, error: `No backup found for ${moduleName}` };
  }

  const latestBackup = join(BACKUP_DIR, backups[0]);
  const targetPath   = join(SOURCES_DIR, `${moduleName}.mjs`);

  writeFileSync(targetPath, readFileSync(latestBackup, 'utf8'), 'utf8');
  gitCommit(`revert(self): rollback ${moduleName} to backup ${backups[0]}`);
  logSelfUpdate('rollback_module', { moduleName, backup: backups[0] });

  writeFileSync(RESTART_FLAG, new Date().toISOString(), 'utf8');

  return {
    success: true,
    moduleName,
    restoredFrom: backups[0],
    message: `⏪ ${moduleName} rolled back. Server restarts after next sweep.`,
  };
}

// ── Restart Flag Management ───────────────────────────────────────────────────

export function isRestartPending() {
  return existsSync(RESTART_FLAG);
}

export function clearRestartFlag() {
  try { unlinkSync(RESTART_FLAG); } catch {}
}

export function triggerGracefulRestart(delayMs = 4000) {
  console.log(`[Updater] Graceful restart in ${delayMs}ms...`);
  logSelfUpdate('restart', { reason: 'new module deployed', delayMs });
  setTimeout(() => {
    console.log('[Updater] Restarting to apply self-update');
    process.exit(0); // PM2 / Docker / Railway auto-restarts
  }, delayMs);
}

// ── Disable a degraded source (comment out from auto_sources) ─────────────────

export function disableSource(moduleName) {
  const fnName = _toCamelCase(moduleName);
  removeFromAutoSources(moduleName, fnName);
  gitCommit(`chore(self): disable degraded source ${moduleName}`);
  logSelfUpdate('disable_source', { moduleName, reason: 'auto-disabled due to low reliability' });
  writeFileSync(RESTART_FLAG, new Date().toISOString(), 'utf8');
  return { success: true, message: `⛔ ${moduleName} disabled. Will restart after next sweep.` };
}

// ── Get update history ────────────────────────────────────────────────────────

export function getDeployedModules() {
  if (!existsSync(SOURCES_DIR)) return [];
  try {
    return readdirSync(SOURCES_DIR)
      .filter(f => f.endsWith('.mjs'))
      .map(f => f.replace('.mjs', ''));
  } catch {
    return [];
  }
}

export function getAutoManagedModules() {
  const { registrations } = readAutoSources();
  return registrations.map(r => r.name);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function _toCamelCase(str) {
  return str.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

function _toDisplayName(str) {
  return str.split('_').map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(' ');
}
