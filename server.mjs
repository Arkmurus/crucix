#!/usr/bin/env node
// Crucix Intelligence Engine — Dev Server
// Serves the Jarvis dashboard, runs sweep cycle, pushes live updates via SSE

import express from 'express';
import { readFileSync, writeFileSync, mkdirSync, existsSync, unlinkSync } from 'fs';
import { dirname, join } from 'path';
import { fileURLToPath } from 'url';
import { exec, execSync } from 'child_process';
import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import cron from 'node-cron';
import config from './crucix.config.mjs';
import { getLocale, currentLanguage, getSupportedLocales } from './lib/i18n.mjs';
import { fullBriefing, pushSignalsToBrain, registerSourceHooks } from './apis/briefing.mjs';
import { synthesize, generateIdeas } from './dashboard/inject.mjs';
import { MemoryManager } from './lib/delta/index.mjs';
import { createLLMProvider } from './lib/llm/index.mjs';
import { generateLLMIdeas } from './lib/llm/ideas.mjs';
import { TelegramAlerter, degradedReply } from './lib/alerts/telegram.mjs';
import { DiscordAlerter } from './lib/alerts/discord.mjs';
import { filterNewSignals, initDedup } from './lib/intel/dedup.mjs';
import { correlate, formatCorrelationsForTelegram } from './lib/intel/correlate.mjs';
import { detectArbitrage } from './lib/intel/arbitrage.mjs';
import { archiveRun, archiveRunWithEntities, analyzeTrends, formatTrendsForTelegram, analyzeEntityTrajectory, formatEntityTrajectoryForTelegram } from './lib/intel/archive.mjs';
import { sendMorningDigest } from './lib/alerts/digest.mjs';
import { fetchUNSecurityCouncil, fetchCentralBanks, fetchThinkTanks, fetchTradeFLows } from './apis/sources/intel-feeds.mjs';
import { fetchOpenSanctions } from './apis/sources/opensanctions.mjs';

// === Self-Learning & Self-Update System ===
import { getLearningStats, getOutcomes, recordAlertOutcome, getSourceHistory, getSourcesToReview, getPatterns, getOpportunities, getExplorerFindings, getUpdateLog, recordSourceSweep, initLearningStore, getBrainAbsorbStats, runAndCacheBridgeVerdict, getBrainBridgeVerdict } from './lib/self/learning_store.mjs';
import { detectOpportunities, formatOpportunitiesForTelegram } from './lib/self/opportunity_engine.mjs';
import { analyzePatterns, formatPatternsForTelegram } from './lib/self/pattern_analyzer.mjs';
import { runExploration, exploreQuery, formatExplorerFindingsForTelegram, formatExplorerFindingsForTelegramIfTop, recordExplorerTelegramPost } from './lib/self/web_explorer.mjs';
import { generateSourceModule, generateSourceFix, stageModule, getStagedModules, getStagedCode, formatStagedForTelegram } from './lib/self/code_generator.mjs';
import { deployModule, rollbackModule, validateSyntax, isRestartPending, clearRestartFlag, triggerGracefulRestart, getAutoManagedModules } from './lib/self/updater.mjs';
import { runBDIntelligence, getBDIntelligence, getDealPipeline, updateDealStage, createDeal, recordOutcome, formatBDSummaryForTelegram, initBDStore } from './lib/self/bd_intelligence.mjs';
import { screenDeal, getProductCategories } from './lib/compliance/screen.mjs';
import { PersistStore } from './lib/persist/store.mjs';
import { createUser, findUserByEmail, findUserByUsername, findUserById, updateUser, deleteUser, revokeTokens, listUsers, verifyPassword, hashPassword, createToken, verifyToken, generateCode, initAdminUser, initUsersStore, getAdminIdentitySnapshot, getBootstrapTrace } from './lib/auth/users.mjs';
import { pinNonAdminUserId, isPrivileged } from './lib/auth/proxyPin.mjs';   // R-F2211 — central IDOR guard
import { issueSseTicket, redeemSseTicket } from './lib/auth/sseTickets.mjs'; // R-F1793
import { conversationKeyForUser, slugifyIdentity } from './lib/auth/conversationKey.mjs';  // R-F1687
import { ROLES, roleSatisfies } from './lib/auth/roles.mjs';  // R-F2170
import { requiredRoleForAriaPath, isDoubleEncodedPath } from './lib/auth/infraRoutes.mjs';  // R-F2775 + R-F2802
import { probeFlyHealth, combineCrossOk } from './lib/health/crossHealth.mjs';  // R-F2776
import { buildHealthSourceBuckets } from './lib/health/sourceBuckets.mjs';      // R-F2867
import { createLivenessObserver } from './lib/observability/livenessObserver.mjs';  // R-F2860
import { operatorPageFor, navPagesForRole } from './lib/auth/operatorPages.mjs';  // R-F2785 table + R-F2818 lookup + R-F2822 nav entitlement
import { classifyDeliveryOutcome, degradedDetail } from './lib/aria/deliveryOutcome.mjs';  // R-F1965
import { containsAnswerChunk } from './lib/aria_sse_delivery.mjs';  // R-F3075
import { classifySourceHealth } from './lib/source/healthBuckets.mjs';  // R-F2719
import { createBillingRouter } from './lib/billing/routes.mjs';
import { enforceQuota } from './lib/billing/enforce.mjs';  // R-F2765 — per-tier quota enforcement on the web path
import { uploadTooLarge, uploadTooLargeMessage, createUploadMeter, maxRequestBytesFor } from './lib/billing/uploadLimit.mjs';  // R-F3988 tier-aware cap + R-F3997 byte meter
import { createReportsRouter } from './lib/reports/routes.mjs';
import { createStatusRouter } from './lib/status/routes.mjs';
// R-F42 (2026-05-09): public API surface — env-gated on ENABLE_PUBLIC_API.
// When unset, both routers return 503 from byte 1, so this import is safe
// to leave permanently — no behaviour change until the operator opts in.
import {
  createKeysRouter, createV1Router, publicApiEnabled, consumeApiKeyBudget,
} from './lib/api_keys/routes.mjs';
import { createMcpRouter } from './lib/mcp/routes.mjs';           // R-F3140
// R-F3140 — authenticateKey/scopesFor for the MCP auth shim; tierAllows +
// DEFAULT_TIER for its tier gate. Called only inside _mcpAuthenticate, so a
// missing import would not fail at boot — it would ReferenceError on the
// first MCP request, in production, silently. (§3b: verify before calling.)
import {
  initApiKeysStore, authenticateKey, scopesFor,
} from './lib/api_keys/store.mjs';
import { tierAllows } from './lib/billing/quotas.mjs';
import { DEFAULT_TIER } from './lib/billing/tiers.mjs';
import { initIncidentsStore } from './lib/status/store.mjs';
import { sendVerificationEmail, sendVerificationSuccessEmail, sendPasswordResetEmail, sendPasswordChangedNotification, sendWelcomeEmail, sendAdminNotification, sendRejectionEmail, sendSuspensionEmail, sendReactivationEmail, sendPendingApprovalEmail, sendLeadVerificationEmail, isConfigured as smtpIsConfigured } from './lib/auth/email.mjs';
import { logAudit, getAuditLog } from './lib/auth/audit.mjs';
// R-F3328 — approving a design partner issues them a real login (see the module
// header: before this, approval wrote a status label and nothing else).
import { provisionDesignPartnerAccess, ACCESS_GRANTING_STATUSES } from './lib/auth/designPartnerAccess.mjs';
// R-F3332 — an issued temporary credential must be rotated before the account works.
import { rotationBlocked, rotationClearedFields, ROTATION_REQUIRED_CODE } from './lib/auth/passwordRotation.mjs';
import { isDisposableEmail, evaluateAutoApproval, MAX_VERIFY_ATTEMPTS } from './lib/auth/onboarding.mjs';
import { leadHoneypotTripped, leadDestinationBlocked } from './lib/auth/leadGuard.mjs';  // R-F3999 — anonymous lead-form abuse bounds
import { shouldQueryUpstream, nextCacheEntry } from './lib/metrics/publicMetricsCache.mjs';  // R-F4013 — bound public-metrics upstream calls
import { initComplianceAudit, getAuditLog as getComplianceAuditLog, exportAuditLog } from './lib/aria/complianceAudit.mjs';
import { initVapid, getVapidPublicKey, saveSubscription, removeSubscription, pushFlash, pushDigest } from './lib/push/push.mjs';
import { createServer } from 'http';
import { Server as SocketIOServer } from 'socket.io';
import {
  storeMessage, getConversation, markRead, getConversationSummaries, unreadCount,
  createGroup, getConversationById, storeConversationMessage, markConversationRead,
} from './lib/messages.mjs';
import { ariaChat as ariaLocalChat, ariaThink as ariaLocalThink } from './lib/aria/aria.mjs';
import { applyRateLimiting, applyInputValidation, applySecurityHeaders } from './middleware/rateLimiter.mjs';
import { initTokenDenylist, revokeToken, isTokenRevoked } from './lib/auth/tokenDenylist.mjs';   // R-F3074
import { handleTelegramWebhook, setLLMProvider as setTelegramLLM, handleAriaCommand, buildArkmursBrief } from './lib/telegram/telegramCommands.mjs';
import * as channelHooks from './lib/telegram/channelServerHooks.mjs';
import { startComplianceRefreshScheduler, screenEntity, getComplianceVersions } from './lib/compliance/listRefresher.mjs';
import { errorTracker, configureTelemetry, SweepMonitor } from './lib/observability/errorTracker.mjs';
// R-F3682 — allowlist for the UNAUTHENTICATED vetting-portal proxy suffix.
// Shared module (not an inline regex) so the capability test drives the shipped
// validator over a real socket rather than a copy of it.
import { isValidVettingPortalSuffix } from './lib/vetting/portalPath.mjs';
// R-F3831/R-F3832 — the same defect class as R-F3682, on NAMED route params.
// Rationale, charsets and the measured exploit live in the module.
import { isValidSessionId, isValidWaAccountId } from './lib/http/upstreamSegment.mjs';
// R-F3833 — ONE localhost-bypass decision, keyed off the REAL TCP peer. Five
// gates previously derived it from the forgeable req.ip; see the module header.
import { localhostBypassAllowed } from './lib/auth/localhostBypass.mjs';
// R-F3838 — scheme allowlist for URLs rendered as hrefs. escHtml stops attribute
// breakout; it does NOT stop `javascript:`, which needs no quote to fire.
import { safeExternalUrl } from './lib/util/safeUrl.mjs';
// R-F3860 — ONE bound for the three unauthenticated per-email attempt maps.
import { pruneAttemptMap } from './lib/util/attemptThrottle.mjs';
import { ProcurementDedup, SourcePruner } from './lib/sources/sourceMaintenance.mjs';
import { startExplorerScheduler } from './lib/self/explorerScheduler.mjs';
import { redisAdapter } from './lib/persist/redisAdapter.mjs';
import { reliableRun } from './lib/orchestrator/retry.mjs';
import ariaWhatsApp from './lib/whatsapp/ariaWhatsApp.mjs';
import { mountWAListener } from './lib/whatsapp/waListener.mjs';
import {
  issueLinkedGrant, linkedGrantState, publicGovernanceState,
} from './lib/whatsapp/waGovernance.mjs';
import { mountEmailReader } from './lib/aria/emailReader.mjs';
import { mountLinkedInRoutes, initLinkedInIntel } from './lib/aria/linkedinIntel.mjs';
import { mountProactive } from './lib/aria/proactive.mjs';
import { initPipeline, mountPipelineRoutes } from './lib/aria/pipeline.mjs';
import { mountBackupRoutes } from './lib/aria/backup.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = __dirname;
const RUNS_DIR = join(ROOT, 'runs');
const MEMORY_DIR = join(RUNS_DIR, 'memory');

// R-F542/F547/F570/F571 (2026-05-15/16): build_rev resolution chain.
//
// Truth-source order at startup:
//   1. build_rev.txt — written by sync.mjs at seenode build time (cheap)
//   2. `git rev-parse HEAD` from local checkout at server.mjs startup
//      (R-F571 fallback — covers the case where seenode skipped sync.mjs
//      or wrote to a different cwd; build_rev then becomes immune to
//      build-step fragility)
//   3. Date-stamp sentinel — last resort
//
// History:
//   - R-F542 introduced build_rev.txt + sync.mjs fetching from
//     GitHub API.
//   - R-F547 added date-stamp fallback inside sync.mjs.
//   - R-F570 removed GitHub-API dep from sync.mjs (local git only).
//   - R-F571 (2026-05-16): seenode shipped R-F570 but reported
//     UNKNOWN-BUILD anyway — seenode caching or cwd-drift meant the
//     build-time write didn't survive to runtime. This makes the
//     runtime the authoritative computation; sync.mjs becomes
//     advisory cache.
function _resolveBuildRev() {
  // 0. R-F846 (2026-05-23) — env var from Dockerfile.web ARG. Mirrors
  // aria-intel's pattern. ARIA_BUILD_GIT_SHA is baked into the image by
  // .github/workflows/deploy-fly.yml passing --build-arg. Manual deploys
  // skip the arg → value stays "unknown" → fall through to file/git.
  const envSha = (process.env.ARIA_BUILD_GIT_SHA || '').trim();
  if (envSha && envSha !== 'unknown') {
    const envTag = (process.env.ARIA_BUILD_R_TAG || '').trim();
    const tag = envTag && envTag !== 'no-r-tag' ? ` · ${envTag}` : '';
    return `${envSha.slice(0, 12)}${tag} (R-F846 build-arg)`;
  }

  // 1. Build-time write (cheap path).
  try {
    const fromFile = readFileSync(join(ROOT, 'build_rev.txt'), 'utf8').trim();
    if (fromFile && !fromFile.startsWith('UNKNOWN-BUILD')) {
      return fromFile;
    }
  } catch (_) { /* fall through */ }

  // 2. R-F571 runtime git fallback. seenode's container has the repo
  // checked out via its own deploy mechanism, so .git is present at
  // ROOT in normal operation.
  try {
    const opts = { cwd: ROOT, encoding: 'utf8', timeout: 5000, stdio: ['ignore', 'pipe', 'pipe'] };
    const sha = execSync('git rev-parse --short HEAD', opts).trim();
    if (!sha) throw new Error('empty sha');
    const date = execSync('git log -1 --pretty=%cs', opts).trim();
    const subject = execSync('git log -1 --pretty=%s', opts).trim().slice(0, 200);
    return `${sha} · ${date} · ${subject} (R-F571 runtime resolve)`;
  } catch (_) { /* fall through */ }

  // 3. Final sentinel — never bare 'UNKNOWN-BUILD' alone; always carry
  // a date stamp so the operator can correlate the deploy.
  return `UNKNOWN-BUILD · ${new Date().toISOString().slice(0, 10)} · build_rev.txt missing AND git unavailable`;
}

const CRUCIX_BUILD_REV = _resolveBuildRev();

// ── Trivial-question short-circuit ──────────────────────────────────────────
// Greetings, liveness probes ('are you online?'), identity questions, and
// 'test'/'ping'/'thanks' should never go through the LLM stack. They get a
// fixed reply with zero LLM cost. Mirrors the Python-side helper in
// aria_service/intel/reasoning_library.trivial_reply() — keep both in sync.
// Returns the reply string or null if the question isn't trivial.
function trivialReply(q) {
  if (!q || typeof q !== 'string') return null;
  // strip optional leading "aria" or "aria,", trailing punctuation, lowercase
  let s = q.trim().toLowerCase().replace(/[?.! ]+$/g, '');
  s = s.replace(/^aria[,!\s]*/, '').trim();
  // R-F1869 (audit DD-20): collapse internal whitespace runs to a single space
  // so the \s+/\s* groups in the probe regexes below can never backtrack on
  // long whitespace (ReDoS). Combined with the length guard, worst-case match
  // work is bounded.
  s = s.replace(/\s+/g, ' ');
  if (!s) return null;

  if (/^(are\s+you\s+(online|there|alive|awake|working|up|ready|here)|you\s+(online|there|alive|awake))$/.test(s)) {
    return "✅ Yes, I'm online and ready. Send me a question, drop a document or image, or use /help for commands.";
  }
  if (/^(hello|hi|hey|good\s+(morning|afternoon|evening|night))$/.test(s)) {
    return "Hi — ARIA here. Ask me anything about compliance, defence procurement, or market intel. /help shows the full command list.";
  }
  if (/^(who\s+are\s+you|what\s+are\s+you|what(?:'s| is)\s+your\s+name)$/.test(s)) {
    return "I'm ARIA — Arkmurus Research Intelligence Agent. I do compliance screening (sanctions, export controls, country risk), defence procurement intel, and market/competitor research. Run /help for the full menu.";
  }
  if (/^(test|ping|status)$/.test(s)) {
    return "✅ Pong. Service is up. /help for commands.";
  }
  if (/^(ok|yes|no)$/.test(s)) {
    return "👍";
  }
  if (/^(thanks?|thank\s+you)$/.test(s)) {
    return "You're welcome.";
  }
  // Background/status meta probes — keep in sync with waListener
  // _waTrivialReply and lib/aria/aria.mjs _ariaTrivialReply.
  // R-F1869 (audit DD-20): these probes only ever match short status questions;
  // gate them behind a length cap so a long crafted near-match can't drive the
  // nested-optional regexes into catastrophic backtracking.
  if (s.length <= 80 && (/^(can\s+you\s+(confirm|tell\s+me|verify)\s+)?(you('?re|\s+are)?\s+|are\s+you\s+)?(still|actually|really)?\s*(working|processing|running|on\s+it|there|alive)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/.test(s)
   || /^(still|actually)\s+(working|on\s+it|there)(\s+on\s+(it|that|this))?(\s+in\s+the\s+background)?$/.test(s)
   || /^did\s+you\s+(get|hear|see)\s+(that|me|it|my\s+(message|question))$/.test(s))) {
    return "✅ I'm here. I don't persist long-running tasks across messages — if your last question is still pending after ~60s, please re-send it and I'll work on it now.";
  }
  return null;
}

// ── Timezone helper — ICU-free, honours BST/GMT (Europe/London) ─────────────
// UK clock: BST (UTC+1) last Sun March 01:00 UTC → last Sun October 01:00 UTC
function londonTs(date = new Date(), seconds = true) {
  function ukOffset(d) {
    const y = d.getUTCFullYear();
    const lastSunMar = new Date(Date.UTC(y, 2, 31, 1, 0, 0));
    while (lastSunMar.getUTCDay() !== 0) lastSunMar.setUTCDate(lastSunMar.getUTCDate() - 1);
    const lastSunOct = new Date(Date.UTC(y, 9, 31, 1, 0, 0));
    while (lastSunOct.getUTCDay() !== 0) lastSunOct.setUTCDate(lastSunOct.getUTCDate() - 1);
    return (d >= lastSunMar && d < lastSunOct) ? 1 : 0;
  }
  const p = n => String(n).padStart(2, '0');
  const local = new Date(date.getTime() + ukOffset(date) * 3600000);
  const base = `${local.getUTCFullYear()}-${p(local.getUTCMonth()+1)}-${p(local.getUTCDate())} ${p(local.getUTCHours())}:${p(local.getUTCMinutes())}`;
  return seconds ? `${base}:${p(local.getUTCSeconds())}` : base;
}
function logTime(date = new Date()) { return londonTs(date); }
function logTimeShort(date = new Date()) { return londonTs(date, false); }

// Ensure directories exist (including logs for PM2)
// R-F2349 — profile photos live on the DURABLE volume (same as users.json),
// so they survive deploys. Keyed by user id, one file per user.
const AVATAR_DIR = join(process.env.PERSIST_DIR || RUNS_DIR, 'avatars');
for (const dir of [RUNS_DIR, MEMORY_DIR, join(MEMORY_DIR, 'cold'), join(RUNS_DIR, 'logs'), AVATAR_DIR]) {
  if (!existsSync(dir)) mkdirSync(dir, { recursive: true });
}

// === State ===
let currentData = null;
let lastSweepTime = null;
let sweepStartedAt = null;
let sweepInProgress = false;
const startTime = Date.now();
const sseClients = new Set();

// === Source Health Tracker ===
// Tracks success/fail counts per source across sweeps for reliability scoring
const sourceHealth = {}; // { sourceName: { ok: N, fail: N, disabled: N, lastStatus: string, lastMs: N } }

function updateSourceHealth(timingMap) {
  for (const [name, info] of Object.entries(timingMap || {})) {
    if (!sourceHealth[name]) sourceHealth[name] = { ok: 0, fail: 0, disabled: 0, lastStatus: null, lastMs: 0, recent: [] };
    const status = String(info.status || '');
    const notConfigured = ['not_configured', 'disabled_no_key', 'disabled_no_watchlist', 'activation_required'].includes(status);
    if (status === 'ok') sourceHealth[name].ok++;
    else if (notConfigured) sourceHealth[name].disabled++;
    else sourceHealth[name].fail++;
    sourceHealth[name].lastStatus = info.status;
    sourceHealth[name].lastMs     = info.ms || 0;
    // R-F2519 (log-review F2) — keep a rolling window of the last 10 sweep outcomes so
    // consumers can show "degraded in last N sweeps" instead of only the CURRENT sweep.
    // External-source quality is bursty; a one-sweep view flaps green↔degraded and hides
    // a source that's been intermittently failing.
    const rec = sourceHealth[name].recent || (sourceHealth[name].recent = []);
    rec.push(status === 'ok' || notConfigured ? 1 : 0);
    if (rec.length > 10) rec.shift();
  }
}

function getSourceHealthSummary() {
  // C-33 (R-F3917) — READ THE DURABLE RECORD, which already exists.
  //
  // `sourceHealth` is an in-process object with no backing, so every restart or
  // deploy reset these percentages to zero while the page presented them as a
  // reliability history. Measured: ~4.5h uptime against a 5-minute sweep, i.e. the
  // panel was showing ~53 sweeps and had forgotten everything before the last
  // deploy — so a chronically flapping feed is laundered clean by shipping.
  //
  // Nothing here needed new storage. `recordSourceSweep()` runs on EVERY sweep, one
  // line after `updateSourceHealth()`, and persists `source_history.json` (a bounded
  // 96-entry timestamped ring per source). `getSourceHistory()` already derives a
  // restart-surviving reliability from the last 48 of those, and server.mjs already
  // imports it. This is C-29 in the Node tier: a producer and a consumer that must
  // agree, with nothing forcing them to.
  //
  // The durable window is deliberately BOUNDED rather than all-time: R-F3364 records
  // that a flat all-time counter dilutes a new regression into a growing historical
  // denominator, so the alarm gets blinder the longer it runs.
  let durableByName = new Map();
  try {
    durableByName = new Map(getSourceHistory().map(s => [s.name, s]));
  } catch {
    // Fail soft to the in-process view — never lose the panel over a store read.
  }

  // C-38 (finding 9) — a durable-only name is only a LIVE feed if it was swept
  // recently. source_history.json is never pruned, so a retired or renamed
  // integration keeps its entry forever; unioning names blindly resurrected it with
  // a non-null reliability, so classifySourceHealth filed a feed that is no longer
  // swept at all as healthy/degraded and inflated totalTracked. Recency is what
  // separates "retired months ago" from "not yet swept since this boot".
  const DURABLE_LIVE_WINDOW_MS = 24 * 60 * 60 * 1000;
  const freshEnough = (d) => {
    const last = d && (d.lastOk || 0);
    return typeof last === 'number' && last > 0 && (Date.now() - last) < DURABLE_LIVE_WINDOW_MS;
  };
  const names = new Set([
    ...Object.keys(sourceHealth),
    ...[...durableByName.keys()].filter(n => freshEnough(durableByName.get(n))),
  ]);

  return [...names].map(name => {
    const h = sourceHealth[name] || { ok: 0, fail: 0, disabled: 0, lastStatus: null, lastMs: 0, recent: [] };
    const d = durableByName.get(name);
    const total = h.ok + h.fail;
    const processReliability = total > 0 ? Math.round((h.ok / total) * 100) : null;
    const recent = h.recent || [];
    const degradedInLastN = recent.filter(v => v === 0).length;  // R-F2519 F2

    // C-38 (finding 4) — AN UNCONFIGURED FEED HAS NO RELIABILITY, and must keep the
    // null that R-F2719 depends on. updateSourceHealth deliberately buckets
    // not_configured / disabled_no_key / disabled_no_watchlist / activation_required
    // as `disabled` (never `fail`), so ok+fail stays 0 and reliability stays null,
    // which is what puts them in the `unconfigured` bucket. recordSourceSweep has NO
    // such carve-out — `ok = status === 'ok'` — so every sweep of an unconfigured
    // feed increments totalFail and drives its durable ema to 0. Letting durable win
    // there reclassified Comtrade/CSL from "no API key was ever set" to "degraded,
    // 0%, dead", re-creating the exact conflation R-F2719 removed.
    const NOT_CONFIGURED = ['not_configured', 'disabled_no_key', 'disabled_no_watchlist', 'activation_required'];
    const unconfigured = NOT_CONFIGURED.includes(String(h.lastStatus || ''))
      || ((h.disabled || 0) > 0 && total === 0);

    // Durable wins when it has samples; the in-process figure is the fallback for a
    // source swept in this process but not yet written to history.
    const durable = !unconfigured && !!d && d.reliability !== null && d.reliability !== undefined;
    const reliability = unconfigured ? null : (durable ? d.reliability : processReliability);

    return {
      name,
      ok: h.ok, fail: h.fail, disabled: h.disabled || 0,
      reliability,
      lastStatus: h.lastStatus, lastMs: h.lastMs,
      degradedInLastN, recentWindow: recent.length,
      // The scope of the number, stated rather than implied. A percentage whose
      // window is invisible is exactly what let "since last boot" pass for history.
      durable,
      windowSweeps: durable ? Math.min(48, (d.totalOk || 0) + (d.totalFail || 0)) : total,
      ema: d ? d.ema : null,
      consecutiveFails: d ? d.consecutiveFails : 0,
    };
  }).sort((a, b) => (a.reliability ?? 100) - (b.reliability ?? 100)); // worst first
}

// === Delta/Memory ===
const memory = new MemoryManager(RUNS_DIR);
// Restore alertedSignals from Redis if hot.json is missing (Render restart)
memory.initFromRedis().catch(() => {});

// === LLM + Telegram + Discord ===
const llmProvider = createLLMProvider(config.llm);
const telegramAlerter = new TelegramAlerter(config.telegram);
// R-F2544 — Telegram public intel must be Golden Intel only. This flag's real
// scope is the still-LIVE non-Golden paths: it blocks the manual admin-channel
// endpoints (post/daily-brief/media/poll/welcome/template → 409) and the automatic
// sweep/digest/explorer alert lanes. Setting TELEGRAM_GOLDEN_INTEL_ONLY=0 re-opens
// ONLY those live paths. It does NOT govern the editorial crons (case file / know
// your rights / country read / opportunity) — those lanes are RETIRED (unscheduled,
// content logic deleted) regardless of the flag. handleMorningSignalCron remains
// the sole automatic public intel publisher.
const TELEGRAM_GOLDEN_INTEL_ONLY = !['0', 'false', 'off', 'no'].includes(
  String(process.env.TELEGRAM_GOLDEN_INTEL_ONLY ?? '1').toLowerCase(),
);
function blockNonGoldenTelegramIntel(res, surface) {
  const payload = {
    ok: false,
    blocked: true,
    reason: 'golden_intel_only',
    surface,
  };
  if (res) return res.status(409).json(payload);
  return payload;
}

function telegramChannelBotOrResponse(res, surface) {
  if (!config.telegram.botToken) {
    return res.status(503).json({ configured: false, reason: 'TELEGRAM_BOT_TOKEN is not set', surface });
  }
  if (!config.telegram.channelId) {
    return res.status(409).json({
      configured: false,
      reason: 'telegram_channel_id_required',
      detail: 'Set TELEGRAM_CHANNEL_ID for public Telegram channel publishing. TELEGRAM_CHAT_ID is reserved for private ops/admin bot messages.',
      surface,
    });
  }
  return { botToken: config.telegram.botToken, chatId: config.telegram.channelId, channelId: config.telegram.channelId };
}

async function sendTelegramChannelText(bot, text) {
  return fetch(`https://api.telegram.org/bot${bot.botToken}/sendMessage`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ chat_id: bot.channelId, text, parse_mode: 'Markdown', disable_web_page_preview: true }),
    signal: AbortSignal.timeout(15000),
  });
}
// R-F2292 — P0 hotfix: R-F2288 added this object with BARE shorthand names
// (curateSignals, …) that were never imported → `ReferenceError: curateSignals
// is not defined` at module load → aria-web crash-looped to max-restart → the
// imaria.io front door went down. The functions are already available on the
// imported channelHooks namespace (server.mjs:65 → channelServerHooks re-exports
// them); reference them there. Member access can't ReferenceError.
const channelPublisher = {
    curateSignals: channelHooks.curateSignals,
    formatChannelPost: channelHooks.formatChannelPost,
    formatDailyBrief: channelHooks.formatDailyBrief,
    canPostNow: channelHooks.canPostNow,
    recordPost: channelHooks.recordPost,
    getSchedulerState: channelHooks.getSchedulerState,
};

// === Persistence Initialization — restores Redis backups if local files are missing ===
// initAdminUser MUST wait for initUsersStore, otherwise it reads empty store
(async () => {
  try {
    await initUsersStore();
    await initTokenDenylist();   // R-F3074 — must be loaded before the first requireAuth
    await initLearningStore();
    await initBDStore();
    await initIncidentsStore();
    await initApiKeysStore();
    const { initEntityStore } = await import('./lib/search/entity-store.mjs');
    await initEntityStore();
    console.log('[Persist] All stores initialized');
    // Now safe to create admin user — store has been restored from Redis
    await initAdminUser();
  } catch (e) {
    console.error('[Persist] Store init error:', e.message);
  }
})();
initVapid().catch(err => console.error('[Push] initVapid failed:', err.message));
import('./lib/aria/knowledge.mjs').then(async (m) => {
  await m.initKnowledgeBase();
  const { seedKnowledgeBase } = await import('./lib/aria/seed_knowledge.mjs');
  seedKnowledgeBase();
}).catch(err => console.error('[ARIA KB] init failed:', err.message));
import('./lib/aria/intel_ledger.mjs').then(m => m.initLedger()).catch(err => console.error('[Intel Ledger] init failed:', err.message));
import('./lib/aria/contacts.mjs').then(m => m.initContacts()).catch(err => console.error('[Contacts] init failed:', err.message));
import('./lib/aria/competitors.mjs').then(m => m.initCompetitors()).catch(err => console.error('[Competitors] init failed:', err.message));
import('./lib/aria/query_evolution.mjs').then(m => m.initEvolution()).catch(err => console.error('[QueryEvolution] init failed:', err.message));
import('./lib/aria/prompt_optimizer.mjs').then(m => m.initOptimizer()).catch(err => console.error('[PromptOptimizer] init failed:', err.message));

// === Brain bridge boot self-check (R-F45) ===================================
// Catches the recurring ARIA_API_TOKEN drift between fly + seenode that has
// twice now caused silent learning-loop outages (2026-04-23 WA listener;
// 2026-05-09 brainAbsorb). Fires a one-shot ping at boot so the operator
// sees the misconfig in the deploy log + a Telegram alert (if configured)
// rather than discovering it days later via grep. Doesn't block startup —
// just runs after the first event-loop tick so other init can proceed.
setImmediate(() => {
  runAndCacheBridgeVerdict({ telegramAlerter }).catch(err => {
    console.error('[brainBridge] verification crashed:', err.message);
  });
});

// === Brain bridge periodic re-check (R-F850) ===============================
// The boot probe above is a ONE-SHOT. If the brain is mid-cold-start when it
// fires (the L3 autonomy absorb storm can wedge the event loop for 2-5min
// after a deploy), the 8s probe times out and the cached verdict is stuck at
// `healthy:false` FOREVER — nothing else refreshes it except a manual admin
// ?rerun=1. /api/status (R-F844) then reports "degraded" indefinitely even
// after the brain recovers and live proxy traffic is succeeding. Re-run the
// probe every 60s so /api/status reflects current reality and self-heals once
// the cold-start window ends (CLAUDE.md §14: report "degraded" only when
// actually degraded). Quiet + no telegramAlerter so a healthy brain doesn't
// log every minute and a persistent misconfig doesn't spam alerts (the boot
// one-shot already alerted). Non-overlapping guard + unref() so it never
// stacks probes or keeps the process alive.
const _BRIDGE_RECHECK_MS = 60_000;
let _bridgeRecheckInFlight = false;
const _bridgeRecheckTimer = setInterval(() => {
  if (_bridgeRecheckInFlight) return;
  _bridgeRecheckInFlight = true;
  const _prev = getBrainBridgeVerdict();
  runAndCacheBridgeVerdict({ quiet: true })
    .then(v => {
      if (v && _prev && v.healthy !== _prev.healthy) {
        console.log(
          `[brainBridge] state change: healthy ${_prev.healthy} → ${v.healthy} (${v.reason})`
        );
      }
    })
    .catch(err => console.error('[brainBridge] periodic re-check crashed:', err.message))
    .finally(() => { _bridgeRecheckInFlight = false; });
}, _BRIDGE_RECHECK_MS);
if (typeof _bridgeRecheckTimer.unref === 'function') _bridgeRecheckTimer.unref();

// === SMTP Diagnostics ===
// R-F3253 — ASK THE MAILER, do not re-derive.
//
// This block computed its own answer from bare EMAIL_HOST/USER/PASS and knew
// nothing about the ARIA_SMTP_* fallback that lib/auth/email.mjs actually
// resolves through. On 2026-07-27 the live log carried both lines within the
// same second:
//
//   [EMAIL] SMTP configured — host=ox.livemail.co.uk ... (via ARIA fallback)
//   [Email] SMTP NOT configured — missing env vars: EMAIL_HOST, EMAIL_USER,
//           EMAIL_PASS — emails will be logged to console only
//
// The mailer was right and this was wrong. Worse than wrong: it told the
// operator mail was disabled when it was sending, so a real delivery problem
// would have been read as "expected, SMTP is off". Two implementations of one
// question, and the duplicate had no idea the fallback existed.
//
// `isConfigured` is already imported at the top of this file as
// `smtpIsConfigured` — the single source was in scope the whole time.
if (smtpIsConfigured) {
  console.log('[Email] SMTP configured (per lib/auth/email.mjs — the module that actually sends)');
} else {
  console.warn('[Email] SMTP NOT configured — set EMAIL_HOST/USER/PASS or ARIA_SMTP_HOST/USER/PASS. Mail will be logged to stdout only.');
}

// MONKEY-PATCH: Override _handleBrief on the instance to guarantee the 8-section
// ARKMURUS format even if Seenode's persistent volume has an older telegram.mjs loaded.
// The old telegram.mjs has `handlers = { '/brief': () => this._handleBrief() }` which
// calls this method on the instance — patching here wins regardless of prototype version.
// R-F2908 — goldenBriefCustomerScore / goldenBriefHardRejections REMOVED with the
// stale /brief gate they served. They read `customer_value.score`, a second quality
// measure that disagrees with intel_grade (at review time every signal scoring 96
// was Grade B). Deleted rather than left dangling so nothing re-wires the superseded
// gate; the brief now uses channelHooks.selectPublishableGoldenIntel, the same gate
// as the channel.

async function fetchGoldenIntelForBrief(limit = 5) {
  if (!ARIA_SERVICE_URL) return [];
  try {
    // R-F2908 — fetch and gate through the SAME path the channel uses. This lane
    // previously had its own gate (customer_value.score >= 80 + freshness.stale ===
    // false), which drifted behind the channel in two ways:
    //   * it predates R-F2899, so a classifier-template signal could appear here as
    //     though ARIA had analysed it;
    //   * it predates R-F2896, so it re-derived staleness locally and would blank the
    //     whole section whenever `source_failure_degraded` was the only stale reason —
    //     the exact divergence that left the customer dashboard empty for days.
    // Live at review time every signal clearing the old >=80 gate was Grade B scoring
    // 96, rendered through formatDailyBrief with no corroboration-pending label.
    //
    // Grade A first; fall back to clearly-LABELLED Grade B, mirroring the 17:00
    // channel policy. Each item carries its own grade so the caller can label it.
    const feed = await channelHooks.fetchGoldenIntelSignals({
      limit: Math.max(20, limit * 4),
      grades: 'A,B',
      serviceUrl: ARIA_SERVICE_URL,
      timeoutMs: 8000,
    });
    const gradeA = channelHooks.selectPublishableGoldenIntel(feed, { grade: 'A', limit });
    if (gradeA.length >= limit) return gradeA;
    const gradeB = channelHooks.selectPublishableGoldenIntel(feed, { grade: 'B', limit: limit - gradeA.length });
    return [...gradeA, ...gradeB];
  } catch (e) {
    console.warn('[Telegram] Golden Intel brief fetch failed:', e.message);
    return [];
  }
}

/**
 * R-F2908 — a Grade B item must never read as confirmed. The channel has
 * formatGradeBChannelPost for this; the brief renders inline, so it gets the same
 * disclosure as a prefix. Grade A needs no qualifier — the badge is the claim.
 */
function goldenBriefGradeLabel(signal) {
  const grade = String(signal?.intel_grade || '').toUpperCase();
  if (grade === 'B') return '[GRADE B — single source, corroboration pending] ';
  return '';
}

function telegramBriefText(value, limit = 140) {
  return String(value || '')
    .replace(/[*_`[\]()]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, limit);
}

telegramAlerter._handleBrief = async function() {
  console.log('[Telegram] _handleBrief() called — server.mjs monkey-patch ARKMURUS 8-section');
  try {
    const data = await this._getCachedData();
    if (!data) return `⏳ Intelligence data is loading — please try again in 60 seconds.`;

    const ts  = londonTs();
    const ds  = data.delta?.summary || {};
    const dir = ds.direction;
    const vix = data.fred?.find(f => f.id === 'VIXCLS');
    const oil = data.energy || {};
    const corrs = data.correlations || [];
    const critCorrs = corrs.filter(c => c.severity === 'critical' || c.severity === 'high');
    const goldenIntel = await fetchGoldenIntelForBrief(5);

    let msg = `*ARKMURUS INTELLIGENCE BRIEF*\n_${ts} London_\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n`;

    // ── 1. LEVERAGEABLE IDEAS ─────────────────────────────────────────────────
    const ideas = data.ideas || [];
    if (ideas.length > 0) {
      msg += `*1. LEVERAGEABLE IDEAS*\n`;
      for (const idea of ideas.slice(0, 3)) {
        const thesis     = idea.thesis || idea.title || idea.text || String(idea);
        const instrument = idea.instrument || idea.sector || '';
        const horizon    = idea.horizon || idea.timeHorizon || '';
        const conf       = idea.confidence || '';
        const catalyst   = idea.catalyst || idea.catalysts?.[0] || '';
        msg += `▸ *${thesis.substring(0, 120)}*\n`;
        if (instrument) msg += `  Instrument: ${instrument}`;
        if (horizon)    msg += ` · Horizon: ${horizon}`;
        if (conf)       msg += ` · Confidence: ${conf}`;
        msg += `\n`;
        if (catalyst)   msg += `  Catalyst: ${catalyst.toString().substring(0, 100)}\n`;
        msg += `\n`;
      }
      if (ideas.length > 3) msg += `_+ ${ideas.length - 3} more ideas in /full_\n\n`;
    } else {
      const topCorr  = critCorrs[0];
      const topAlert = (data.supplyChain?.metrics?.alerts || []).find(a => a.type === 'critical');
      if (topCorr || topAlert) {
        msg += `*1. LEVERAGEABLE IDEAS*\n`;
        if (topCorr) {
          msg += `▸ *${topCorr.region} — multi-source ${topCorr.severity} signal*\n`;
          msg += `  Monitor exposure to ${topCorr.region} counterparties and contracts.\n`;
          msg += `  Horizon: 24–72h · Catalyst: ${topCorr.topSignals?.[0]?.text?.substring(0, 80) || 'see /full'}\n\n`;
        }
        if (topAlert) {
          msg += `▸ *Supply chain stress: ${topAlert.message?.substring(0, 100)}*\n`;
          msg += `  Review procurement timelines and alternative sourcing.\n\n`;
        }
        msg += `_Enable LLM (ANTHROPIC_API_KEY) for full trade ideas with instruments and invalidation criteria._\n\n`;
      }
    }

    if (goldenIntel.length > 0) {
      msg += `*GOLDEN INTEL — DECISION SIGNALS*\n`;
      for (const s of goldenIntel.slice(0, 3)) {
        const title = telegramBriefText(s.decision_summary || s.title || 'Untitled signal', 120);
        const action = telegramBriefText(s.recommended_action || 'Review', 80);
        const target = telegramBriefText(s.target || s.source || 'market', 60);
        const quality = telegramBriefText(s.quality_label || 'context', 60);
        const horizon = telegramBriefText(s.action_horizon || 'monitor', 30);
        const evidence = telegramBriefText(s.corroboration || 'single-source', 40);
        // R-F2908 — the grade prefix goes FIRST, before the title, so the uncertainty
        // is read before the claim rather than after it.
        msg += `▸ *${goldenBriefGradeLabel(s)}${title}*\n`;
        msg += `  ${s.priority || 'LOW'}/${s.confidence || 'LOW'} · ${quality} · Horizon: ${horizon} · Evidence: ${evidence}\n`;
        msg += `  Target: ${target} · Action: ${action}\n`;
      }
      msg += `\n`;
    }

    // ── 2. EXECUTIVE THESIS ───────────────────────────────────────────────────
    msg += `*2. EXECUTIVE THESIS*\n`;
    const dirLine = dir === 'risk-off' ? '📉 Risk-off — global stress indicators elevated'
                  : dir === 'risk-on'  ? '📈 Risk-on — conditions broadly constructive'
                  : '↔️ Mixed signals — no dominant regime forming yet';
    msg += `${dirLine}.\n`;
    if (critCorrs.length > 0) {
      const regions = critCorrs.slice(0, 3).map(c => c.region).join(', ');
      msg += `Concurrent stress across *${regions}* suggests coordinated pressure, not isolated events.\n`;
    }
    if (ds.criticalChanges > 0) {
      msg += `*${ds.criticalChanges}* indicators crossed critical thresholds this sweep.\n`;
    }
    if (vix?.value > 25) {
      msg += `VIX at *${vix.value}* confirms elevated market anxiety — reduce leverage on new positions.\n`;
    }
    msg += `\n`;

    // ── 3. SITUATION AWARENESS ────────────────────────────────────────────────
    if (critCorrs.length > 0) {
      msg += `*3. SITUATION AWARENESS*\n`;
      for (const c of critCorrs.slice(0, 4)) {
        const badge = c.severity === 'critical' ? '🔴' : '🟠';
        const top   = c.topSignals?.[0]?.text || '';
        msg += `${badge} *${c.region}* [${(c.sourceCount || c.sources?.length || 1)} sources]\n`;
        if (top) msg += `  └ ${top.substring(0, 140)}\n`;
      }
      msg += `\n`;
    }

    // OSINT top signals
    const urgent = data.tg?.urgent || [];
    if (urgent.length > 0) {
      msg += `📡 *OSINT (${urgent.length} signals — top 2)*\n`;
      for (const s of urgent.slice(0, 2)) {
        msg += `• *[${s.channel || 'OSINT'}]* ${(s.text || '').trim().replace(/\n+/g, ' ').substring(0, 160)}\n`;
      }
      msg += `\n`;
    }

    // ── 4. PATTERN RECOGNITION ────────────────────────────────────────────────
    const multiSourceCorrs = corrs.filter(c => (c.sourceCount || c.sources?.length || 0) >= 3);
    if (multiSourceCorrs.length > 0) {
      msg += `*4. PATTERN RECOGNITION*\n`;
      for (const c of multiSourceCorrs.slice(0, 2)) {
        msg += `🔗 *${c.region}* — ${c.sourceCount || c.sources?.length} independent sources converging`;
        const sig2 = c.topSignals?.[1]?.text;
        if (sig2) msg += `: "${sig2.substring(0, 100)}"`;
        msg += `. Pattern: ${c.severity === 'critical' ? 'strengthening' : 'stable'}.\n`;
      }
      msg += `\n`;
    }

    // ── 5. HISTORICAL PARALLELS ───────────────────────────────────────────────
    const parallels = [];
    if (vix?.value > 30 && oil.brent > 90)
      parallels.push({ period: '2022 Russia-Ukraine shock', match: 'VIX >30 + Brent >$90 — energy-driven inflation with geopolitical disruption', lesson: 'Gold and defence names outperformed; commodity exporters gained. Watch for demand destruction at $100+.' });
    if (dir === 'risk-off' && critCorrs.length >= 3)
      parallels.push({ period: 'Q4 2018 / Q1 2020 stress buildup', match: 'Multi-region risk-off with 3+ concurrent stress zones', lesson: 'Historically precedes 10–20% equity drawdowns within 60 days. Monitor credit spreads for confirmation.' });
    if (critCorrs.some(c => c.region === 'Eastern Europe') && vix?.value > 25)
      parallels.push({ period: 'Feb 2022 pre-invasion week', match: 'Eastern Europe critical + VIX spiking', lesson: 'Positions in European defence ETFs and energy hedges outperformed 40–90% in the 6 months after escalation.' });
    if (critCorrs.some(c => c.region === 'Middle East') && oil.brent > 85)
      parallels.push({ period: '2019 Aramco strike / 2024 Red Sea disruption', match: 'Middle East stress + Brent above $85', lesson: 'Maritime insurance premiums spiked 300%; shipping re-routing cost weeks and billions. Logistics and tanker plays outperformed.' });
    if (critCorrs.some(c => c.region === 'Lusophone Africa') || critCorrs.some(c => c.region === 'West Africa'))
      parallels.push({ period: '2012–2015 Sahel destabilisation', match: 'Lusophone/West Africa stress signals', lesson: 'Arkmurus advantage: instability in the region historically precedes 18–36 month procurement surges for border and peacekeeping equipment.' });
    if (parallels.length > 0) {
      msg += `*5. HISTORICAL PARALLELS*\n`;
      for (const p of parallels.slice(0, 2)) {
        msg += `📜 *Rhymes with: ${p.period}*\n`;
        msg += `Match: ${p.match}\n`;
        msg += `Lesson: ${p.lesson}\n\n`;
      }
    }

    // ── 6. MARKET & ASSET IMPLICATIONS ───────────────────────────────────────
    const hasMarketData = vix?.value || oil.brent;
    if (hasMarketData) {
      msg += `*6. MARKET & ASSET IMPLICATIONS*\n`;
      if (vix?.value) msg += `• Volatility (VIX): *${vix.value}* — ${vix.value > 30 ? '🔴 extreme stress' : vix.value > 20 ? '🟠 elevated' : '🟢 normal'}\n`;
      if (oil.brent)  msg += `• Brent crude: *$${oil.brent}* · WTI: *$${oil.wti || '--'}*\n`;
      const scMats = (data.supplyChain?.metrics?.rawMaterials || []).filter(m => m.risk === 'critical' || m.risk === 'high').slice(0, 3);
      for (const m of scMats) msg += `• ${m.name}: *${m.price}* (${m.change}) — ${m.impact}\n`;
      msg += `\n`;
    }

    // ── 7. DECISION BOARD ─────────────────────────────────────────────────────
    msg += `*7. DECISION BOARD*\n`;
    const topIdea = ideas[0];
    msg += `• Best long: ${topIdea ? topIdea.instrument || topIdea.thesis?.substring(0, 60) : 'await multi-source confirmation'}\n`;
    const sanctions = data.opensanctions?.preDesignation || [];
    msg += `• Best hedge: ${sanctions.length > 0 ? `Exposure review — ${sanctions.length} pre-designation signal(s)` : dir === 'risk-off' ? 'Gold / defensive assets' : 'Monitor VIX for entry'}\n`;
    const topWatch = critCorrs[0];
    msg += `• Watch: ${topWatch ? `${topWatch.region} — next 24–72h` : 'No critical zones currently'}\n`;
    if (ds.totalChanges > 0) msg += `• Monitor: ${ds.totalChanges} delta changes — confirm or reverse in next sweep\n`;
    // BD Brain priority — most actionable BD signal right now
    const bd = data.bdIntelligence;
    const brainPriority = bd?.brain?.weeklyPriority;
    const topTender = bd?.tenders?.[0];
    if (brainPriority?.action) {
      msg += `\n⚡ *BD BRAIN — TOP PRIORITY*\n`;
      msg += `${brainPriority.action.substring(0, 200)}\n`;
      if (brainPriority.whyNow) msg += `_Why now: ${brainPriority.whyNow.substring(0, 120)}_\n`;
    } else if (topTender) {
      msg += `\n🎯 *BD — ACTIVE TENDER*\n`;
      msg += `${topTender.market}: ${topTender.title.substring(0, 100)}\n`;
      if (topTender.winProbability != null) msg += `Win probability: *${topTender.winProbability}%*\n`;
    }
    msg += `\n`;

    // ── 8. SOURCE INTEGRITY ───────────────────────────────────────────────────
    const srcOk    = data.meta?.sourcesOk || 0;
    const srcTotal = data.meta?.sourcesQueried || 0;
    const srcFail  = data.meta?.sourcesFailed || 0;
    msg += `*8. SOURCE INTEGRITY*\n`;
    msg += `${srcOk}/${srcTotal} sources delivered data`;
    if (srcFail > 0) msg += ` · ${srcFail} degraded`;
    const hasLLM = ideas.length > 0 && data.ideasSource === 'llm';
    msg += `\nThesis basis: ${hasLLM ? 'LLM synthesis + hard data' : 'hard data only — LLM not active'}`;
    msg += `\n`;

    msg += `\n━━━━━━━━━━━━━━━━━━━━━━━━\n`;
    msg += `_/full · /osint · /supply · /arms · /predict · /ask [topic]_`;

    return msg;
  } catch (error) {
    // R-F2615 §25 — mark the failed /brief reply so _handleMessage reports an honest
    // 'error' delivery outcome instead of 'delivered' (this monkey-patch is the prod /brief).
    return degradedReply(`Brief failed: ${error.message}`);
  }
};

const discordAlerter = new DiscordAlerter(config.discord || {});

if (llmProvider) console.log(`[Crucix] LLM enabled: ${llmProvider.name} (${llmProvider.model})`);
if (telegramAlerter.isConfigured) {
  console.log('[Crucix] Telegram alerts enabled');

  telegramAlerter.onCommand('/status', async () => {
    const uptime = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(uptime / 3600);
    const m = Math.floor((uptime % 3600) / 60);
    const sourcesOk = currentData?.meta?.sourcesOk || 0;
    const sourcesTotal = currentData?.meta?.sourcesQueried || 0;
    const sourcesFailed = currentData?.meta?.sourcesFailed || 0;
    const llmStatus = llmProvider?.isConfigured ? `✅ ${llmProvider.name}` : '❌ Disabled';
    const nextSweep = lastSweepTime
      ? logTimeShort(new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000))
      : 'pending';
    return [
      `🖥️ *ARIA STATUS*`, ``,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? logTimeShort(new Date(lastSweepTime)) + ' London' : 'never'}`,
      `Next sweep: ${nextSweep} London`,
      `Sweep in progress: ${sweepInProgress ? '🔄 Yes' : '⏸️ No'}`,
      `Sources: ${sourcesOk}/${sourcesTotal} OK${sourcesFailed > 0 ? ` (${sourcesFailed} failed)` : ''}`,
      `LLM: ${llmStatus}`,
      `SSE clients: ${sseClients.size}`,
      `Dashboard: http://localhost:${config.port}`,
    ].join('\n');
  });

  telegramAlerter.onCommand('/sweep', async () => {
    if (sweepInProgress) return '🔄 Sweep already in progress. Please wait.';
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    return '🚀 Manual sweep triggered. You\'ll receive alerts if anything significant is detected.';
  });

  // /brief handled by telegram.mjs _handleBrief() — 8-section BRIEFING_PROMPT.md format

  telegramAlerter.onCommand('/portfolio', async () => {
    return '📊 Portfolio integration requires Alpaca MCP connection.\nUse the Crucix dashboard or Claude agent for portfolio queries.';
  });

  telegramAlerter.onCommand('/trends', async () => {
    const trends     = analyzeTrends();
    const trajectory = analyzeEntityTrajectory(14);
    const msg1 = formatTrendsForTelegram(trends);
    const msg2 = formatEntityTrajectoryForTelegram(trajectory);
    return msg1 + '\n\n' + msg2;
  });

  telegramAlerter.onCommand('/entities', async () => {
    const trajectory = analyzeEntityTrajectory(14);
    return formatEntityTrajectoryForTelegram(trajectory);
  });

  telegramAlerter.onCommand('/correlations', async () => {
    if (!currentData) return 'No data yet — waiting for first sweep.';
    const correlations = correlate(currentData);
    return formatCorrelationsForTelegram(correlations) || 'No significant convergences detected.';
  });

  telegramAlerter.onCommand('/sanctions', async () => {
    if (!currentData) return 'No data yet.';
    const recent = currentData.opensanctions?.recent || [];
    if (recent.length === 0) return 'No recent sanctions updates.';
    let msg = '*RECENT SANCTIONS UPDATES*\n\n';
    for (const e of recent.slice(0, 8)) msg += `• ${e.name} — ${e.datasets.join(', ')}\n`;
    return msg;
  });

  // ── Self-Learning Commands ────────────────────────────────────────────────

  telegramAlerter.onCommand('/opportunities', async () => {
    const stored = getOpportunities();
    const opps = stored.opportunities || [];
    // Refresh from current data if available
    if (currentData) {
      const fresh = await detectOpportunities(currentData);
      return formatOpportunitiesForTelegram(fresh);
    }
    return formatOpportunitiesForTelegram(opps);
  });

  telegramAlerter.onCommand('/bd', async () => {
    const bd = currentData?.bdIntelligence || getBDIntelligence();
    return formatBDSummaryForTelegram(bd);
  });

  telegramAlerter.onCommand('/patterns', async () => {
    const stored = getPatterns();
    return formatPatternsForTelegram(stored);
  });

  telegramAlerter.onCommand('/explore', async (args) => {
    if (args && args.trim()) {
      const query = args.trim();
      const result = await exploreQuery(llmProvider, query);
      if (result.error) return `❌ ${result.error}`;
      let msg = `🌐 *EXPLORATION: ${query}*\n\n`;
      if (result.analysis) msg += result.analysis.substring(0, 1800);
      else msg += result.results.slice(0, 3).map(r => `▸ *${r.title}*\n${r.snippet?.substring(0, 100)}`).join('\n\n');
      return msg;
    }
    // Full sweep exploration
    const findings = await runExploration(llmProvider);
    return formatExplorerFindingsForTelegram(findings);
  });

  telegramAlerter.onCommand('/learn', async (args) => {
    const parts = (args || '').trim().split(/\s+/);
    const subCmd = parts[0];

    if (subCmd === 'status') {
      const stats = getLearningStats();
      const acc = stats.outcomes.accuracy !== null ? `${stats.outcomes.accuracy}%` : 'n/a (need outcomes)';
      return [
        '*🧠 LEARNING STATUS*', '',
        `*Outcomes tracked:* ${stats.outcomes.total} (${stats.outcomes.confirmed} confirmed, ${stats.outcomes.dismissed} dismissed)`,
        `*Signal accuracy:* ${acc}`,
        `*Sources — healthy:* ${stats.sources.healthy} · degraded: ${stats.sources.degraded} · critical: ${stats.sources.critical}`,
        `*Patterns detected:* ${stats.patternCount}`,
        `*Opportunities found:* ${stats.opportunityCount}`,
        '',
        `_/learn confirm <hash> · /learn dismiss <hash>_`,
        `_/sources for per-source reliability_`,
      ].join('\n');
    }

    if ((subCmd === 'confirm' || subCmd === 'dismiss') && parts[1]) {
      const hash = parts[1];
      const outcome = subCmd;
      recordAlertOutcome(hash, '', outcome, {});
      return `✅ Alert ${hash.substring(0, 12)}… marked as *${outcome}*\nLearning weights updated.`;
    }

    return [
      '*🧠 LEARN COMMANDS*', '',
      '`/learn status` — learning accuracy stats',
      '`/learn confirm <id>` — mark alert as accurate',
      '`/learn dismiss <id>` — mark alert as false alarm',
    ].join('\n');
  });

  telegramAlerter.onCommand('/sources', async (args) => {
    const history = getSourceHistory();
    if (history.length === 0) return '📡 No source history yet — runs after first sweep.';

    const critical  = history.filter(s => s.status === 'critical');
    const degraded  = history.filter(s => s.status === 'degraded');
    const healthy   = history.filter(s => s.status === 'healthy');

    let msg = `*📡 SOURCE HEALTH (${history.length} sources)*\n`;
    msg += `🟢 ${healthy.length} healthy · 🟠 ${degraded.length} degraded · 🔴 ${critical.length} critical\n\n`;

    if (critical.length > 0) {
      msg += `*🔴 CRITICAL (fix needed)*\n`;
      for (const s of critical.slice(0, 5)) {
        msg += `▸ ${s.name} — ${s.reliability ?? '?'}% reliability\n`;
      }
      msg += '\n';
    }
    if (degraded.length > 0) {
      msg += `*🟠 DEGRADED (monitor)*\n`;
      for (const s of degraded.slice(0, 5)) {
        msg += `▸ ${s.name} — ${s.reliability ?? '?'}% reliability\n`;
      }
    }
    msg += `\n_/sources fix <name> to auto-repair · /sources all for full list_`;
    return msg;
  });

  telegramAlerter.onCommand('/update', async (args) => {
    const parts = (args || '').trim().split(/\s+/);
    const subCmd = parts[0];

    if (!subCmd || subCmd === 'status') {
      const staged  = getStagedModules();
      const managed = getAutoManagedModules();
      const log     = getUpdateLog(3);
      let msg = `*🔧 SELF-UPDATE STATUS*\n\n`;
      msg += `Auto-managed sources: ${managed.length > 0 ? managed.join(', ') : 'none yet'}\n`;
      msg += `Staged for deployment: ${staged.length}\n`;
      if (staged.length > 0) msg += staged.map(s => `  ▸ ${s.name} (${s.type || 'new'})`).join('\n') + '\n';
      msg += '\n*Recent activity:*\n';
      for (const entry of log) {
        msg += `▸ ${entry.action} — ${entry.timestamp?.substring(0, 16).replace('T', ' ')}\n`;
      }
      msg += '\n_/update add <description> — generate new source_\n_/update apply <name> — deploy staged module_\n_/update staged — list staged modules_';
      return msg;
    }

    if (subCmd === 'staged') {
      return formatStagedForTelegram(getStagedModules());
    }

    if (subCmd === 'add' && parts.length >= 2) {
      const description = parts.slice(1).join(' ');
      const moduleName = description
        .toLowerCase()
        .replace(/[^a-z0-9\s]/g, '')
        .trim()
        .replace(/\s+/g, '_')
        .substring(0, 30);

      if (!llmProvider?.isConfigured) {
        return '❌ LLM not configured — set ANTHROPIC_API_KEY to enable code generation';
      }

      const reply = await telegramAlerter._sendText?.('⏳ Generating source module — this takes ~30s...');
      const result = await generateSourceModule(llmProvider, description, moduleName);

      if (!result.success) return `❌ Generation failed: ${result.error}`;

      stageModule(result.moduleName, result.code, { type: 'new', description });
      return `✅ *Source module generated and staged*\nName: \`${result.moduleName}\`\nLines: ${result.code.split('\n').length}\n\nTo deploy: \`/update apply ${result.moduleName}\`\nTo preview: \`/update preview ${result.moduleName}\``;
    }

    if (subCmd === 'apply' && parts[1]) {
      // R-F1887 (review Class D): sanitize the module name — it flows into
      // gitCommit() + ./sources/${name}.mjs file paths (path traversal).
      const moduleName = _sanitizeModuleName(parts[1]);
      if (!moduleName) return '❌ Invalid module name (alphanumeric/underscore only)';
      const result = await deployModule(moduleName);
      return result.success ? `✅ ${result.message}` : `❌ Deploy failed: ${result.error}`;
    }

    if (subCmd === 'discard' && parts[1]) {
      // R-F1887 (review Class D): sanitize BEFORE building the path — parts[1]
      // was joined into runs/staged/${parts[1]}.mjs.staged and unlinkSync'd, so
      // "../../<x>" was a path-traversal file delete.
      const _m = _sanitizeModuleName(parts[1]);
      if (!_m) return '❌ Invalid module name (alphanumeric/underscore only)';
      const { unlinkSync, existsSync } = await import('node:fs');
      const stagePath = join(ROOT, 'runs', 'staged', `${_m}.mjs.staged`);
      // R-F16: also clear Redis mirror so a future boot doesn't
      // rehydrate a module the operator already discarded.
      try {
        const { discardStagedFromRedis } = await import('./lib/self/code_generator.mjs');
        await discardStagedFromRedis(_m);
      } catch {}
      if (existsSync(stagePath)) {
        unlinkSync(stagePath);
        try { unlinkSync(stagePath + '.meta.json'); } catch {}
        return `🗑️ Staged module \`${_m}\` discarded`;
      }
      return `❌ No staged module named: ${_m}`;
    }

    if (subCmd === 'preview' && parts[1]) {
      const _m = _sanitizeModuleName(parts[1]);   // R-F1887: name → staged file path
      if (!_m) return '❌ Invalid module name (alphanumeric/underscore only)';
      const code = getStagedCode(_m);
      if (!code) return `❌ No staged module: ${_m}`;
      const preview = code.substring(0, 800);
      return `*Preview: ${_m}*\n\`\`\`\n${preview}\n\`\`\`${code.length > 800 ? `\n_...${code.length - 800} more chars_` : ''}`;
    }

    if (subCmd === 'fix' && parts[1]) {
      const sourceName = _sanitizeModuleName(parts[1]);   // R-F1887
      if (!sourceName) return '❌ Invalid source name (alphanumeric/underscore only)';
      const sourceHistory = getSourceHistory();
      const srcInfo = sourceHistory.find(s => s.name.toLowerCase() === sourceName.toLowerCase());
      const errorMsg = srcInfo?.status === 'critical' ? `Source ${sourceName} has ${srcInfo.reliability}% reliability` : `Source ${sourceName} reported as failing`;

      if (!llmProvider?.isConfigured) return '❌ LLM required for auto-fix';

      const result = await generateSourceFix(llmProvider, sourceName, errorMsg);
      if (!result.success) return `❌ Fix generation failed: ${result.error}`;

      stageModule(result.moduleName, result.code, { type: 'fix', description: `Auto-fix for ${sourceName}`, originalError: errorMsg });
      return `🔧 Fix generated for \`${sourceName}\`\nTo apply: \`/update apply ${result.moduleName}\``;
    }

    if (subCmd === 'rollback' && parts[1]) {
      const _m = _sanitizeModuleName(parts[1]);   // R-F1887: name → file paths
      if (!_m) return '❌ Invalid module name (alphanumeric/underscore only)';
      const result = rollbackModule(_m);
      return result.success ? `⏪ ${result.message}` : `❌ Rollback failed: ${result.error}`;
    }

    return [
      '*🔧 UPDATE COMMANDS*', '',
      '`/update status` — show managed sources + recent activity',
      '`/update add <description>` — generate new source module',
      '`/update staged` — list modules awaiting deployment',
      '`/update apply <name>` — deploy a staged module',
      '`/update preview <name>` — preview staged module code',
      '`/update fix <source>` — auto-fix a broken source',
      '`/update discard <name>` — discard staged module',
      '`/update rollback <name>` — rollback to previous version',
    ].join('\n');
  });

  // /aria command — full ARIA Telegram interface
  telegramAlerter.onCommand('/aria', async (args, chatId, userId) => {
    await handleAriaCommand(chatId || config.telegram.chatId, userId || '', args || '');
    return null; // handleAriaCommand sends directly
  });

  // ── New BD commands — OEM, HUMINT, Approach, Deal, Screen, Conference ──

  telegramAlerter.onCommand('/oem', async (args) => {
    if (!args?.trim()) return '⚠️ Usage: /oem [product] [market]\nExample: /oem UAV Angola';
    try {
      const { generateApproach } = await import('./lib/aria/approach.mjs');
      const parts = args.trim().split(' ');
      const product = parts[0];
      const market = parts.slice(1).join(' ') || '';
      const strategy = generateApproach(market || 'Angola', product, '');
      if (!strategy.rankedOEMs?.length) return `No OEM matches for "${product}"`;
      let msg = `🏭 *OEM MATCH — ${product.toUpperCase()}*${market ? ` | ${market}` : ''}\n\n`;
      strategy.rankedOEMs.forEach((o, i) => {
        msg += `*${i+1}. ${o.oem}* (${o.country}) ${o.itar ? '⚠️ITAR' : '✅non-ITAR'}\n`;
        msg += `   Price: ${o.price} | Africa: ${o.africa}\n`;
        msg += `   Products: ${o.products}\n\n`;
      });
      return msg;
    } catch (e) { return `⚠️ OEM search failed: ${e.message}`; }
  });

  telegramAlerter.onCommand('/humint', async (args) => {
    if (!args?.trim()) return '⚠️ Usage: /humint [market]\nExample: /humint Angola';
    try {
      const { getContactsByCountry } = await import('./lib/aria/contacts.mjs');
      const contacts = getContactsByCountry(args.trim());
      if (!contacts.length) return `No contacts found for ${args.trim()}. Add via /api/aria/contacts.`;
      let msg = `👤 *DECISION MAKERS — ${args.trim().toUpperCase()}*\n${contacts.length} contacts\n\n`;
      contacts.slice(0, 6).forEach(c => {
        msg += `*${c.name}*\n`;
        msg += `   ${c.title || c.role}\n`;
        msg += `   ${c.organisation || ''}\n`;
        if (c.influence) msg += `   Influence: ${c.influence}\n`;
        if (c.notes) msg += `   _${(c.notes || '').slice(0, 100)}_\n`;
        msg += '\n';
      });
      return msg;
    } catch (e) { return `⚠️ HUMINT failed: ${e.message}`; }
  });

  telegramAlerter.onCommand('/approach', async (args) => {
    if (!args?.trim()) return '⚠️ Usage: /approach [market] [product]\nExample: /approach Angola UAV';
    try {
      const { generateApproach } = await import('./lib/aria/approach.mjs');
      const { generateGTMStrategy } = await import('./lib/aria/gtm_strategy.mjs');
      const parts = args.trim().split(' ');
      const market = parts[0];
      const product = parts.slice(1).join(' ') || '';
      const approach = generateApproach(market, product, '');
      const gtm = generateGTMStrategy(market);
      let msg = `⚙️ *APPROACH — ${market.toUpperCase()}*\n\n`;
      msg += `Language: ${approach.profile.language} | Formality: ${approach.profile.formality}\n`;
      msg += `Greeting: ${approach.profile.greeting}\n`;
      if (gtm) msg += `Tier: *${gtm.tier}* | Time to deal: ${gtm.timeToFirstDeal}\n`;
      msg += `\n*TOP OEMs:*\n`;
      approach.rankedOEMs.slice(0, 3).forEach(o => {
        msg += `  • ${o.oem} (${o.country}) — ${o.price} ${o.itar ? '⚠️ITAR' : ''}\n`;
      });
      msg += `\n*COMPLIANCE:*\n`;
      approach.compliance.slice(0, 4).forEach(c => { msg += `  ✓ ${c}\n`; });
      if (gtm?.playbook?.steps) {
        msg += `\n*FIRST 3 STEPS:*\n`;
        gtm.playbook.steps.slice(0, 3).forEach((s, i) => { msg += `  ${i+1}. ${s}\n`; });
      }
      return msg;
    } catch (e) { return `⚠️ Approach failed: ${e.message}`; }
  });

  telegramAlerter.onCommand('/deal', async (args) => {
    const parts = (args || '').trim().split(' ');
    const sub = parts[0]?.toLowerCase();
    if (!sub) {
      // Show pipeline summary
      const pipeline = getDealPipeline();
      if (!pipeline.length) return '📊 Pipeline empty. Create a deal: /deal new [market] [opportunity]';
      let msg = `📊 *BD PIPELINE* — ${pipeline.length} deals\n\n`;
      pipeline.slice(0, 8).forEach(d => {
        msg += `*${d.id || '?'}* | ${d.market} | ${d.stage}\n`;
        msg += `  ${(d.title || d.opportunity || '').slice(0, 60)}\n\n`;
      });
      return msg;
    }
    if (sub === 'new') {
      const market = parts[1] || '';
      const opp = parts.slice(2).join(' ');
      if (!market || !opp) return '⚠️ Usage: /deal new [market] [opportunity]';
      try {
        const result = createDeal(market, opp);
        return `✅ Deal *${result.id}* created\n${market} | ${opp.slice(0, 60)}\nStage: IDENTIFIED`;
      } catch (e) { return `⚠️ Failed: ${e.message}`; }
    }
    return '⚠️ Usage: /deal | /deal new [market] [opp]';
  });

  telegramAlerter.onCommand('/screen', async (args) => {
    if (!args?.trim()) return '⚠️ Usage: /screen [entity name]';
    try {
      const { screenEntity } = await import('./lib/compliance/listRefresher.mjs');
      const result = await screenEntity(args.trim());
      const clean = result?.clean !== false;
      return `${clean ? '✅' : '⛔'} *COMPLIANCE SCREEN — ${args.trim()}*\n\nResult: *${clean ? 'CLEAR' : 'FLAGGED'}*\n${result?.details || 'Pre-screen only. Legal review required before proceeding.'}`;
    } catch (e) { return `⚠️ Screen failed: ${e.message}`; }
  });

  telegramAlerter.onCommand('/report', async (args) => {
    const parts = (args || '').trim().split(' ');
    const type = parts[0]?.toLowerCase();
    if (type === 'monthly') {
      try {
        const { generateMonthlyBrief } = await import('./lib/reports/pdf_generator.mjs');
        const pdf = await generateMonthlyBrief(currentData || {});
        const month = new Date().toISOString().slice(0, 7);
        // Send as document via Telegram API
        const FormData = (await import('undici')).FormData || globalThis.FormData;
        if (!FormData) return '⚠️ PDF generated but cannot send via Telegram (FormData not available). Download from: /api/report/monthly';
        const form = new FormData();
        form.append('chat_id', config.telegram.chatId);
        form.append('caption', `📎 ARKMURUS Monthly Intelligence Brief — ${month}`);
        form.append('document', new Blob([pdf], { type: 'application/pdf' }), `ARKMURUS_Brief_${month}.pdf`);
        await fetch(`https://api.telegram.org/bot${config.telegram.botToken}/sendDocument`, { method: 'POST', body: form });
        return null;
      } catch (e) {
        return `⚠️ Report failed: ${e.message}\nDownload from web: /api/report/monthly`;
      }
    }
    if (type === 'approach') {
      const market = parts[1] || '';
      if (!market) return '⚠️ Usage: /report approach [market] [product]';
      try {
        const { generateApproachPack } = await import('./lib/reports/pdf_generator.mjs');
        const { generateApproach } = await import('./lib/aria/approach.mjs');
        const { generateGTMStrategy } = await import('./lib/aria/gtm_strategy.mjs');
        const { getContactsByCountry } = await import('./lib/aria/contacts.mjs');
        const product = parts.slice(2).join(' ') || '';
        const approach = generateApproach(market, product, '');
        const gtm = generateGTMStrategy(market);
        const contacts = getContactsByCountry(market);
        const pdf = await generateApproachPack(market, product, approach, gtm, contacts);
        const FormData = (await import('undici')).FormData || globalThis.FormData;
        if (!FormData) return `⚠️ PDF ready but cannot send. Download from web: POST /api/report/approach {market: "${market}"}`;
        const form = new FormData();
        form.append('chat_id', config.telegram.chatId);
        form.append('caption', `📎 ARKMURUS Approach Pack — ${market}`);
        form.append('document', new Blob([pdf], { type: 'application/pdf' }), `ARKMURUS_Approach_${market}.pdf`);
        await fetch(`https://api.telegram.org/bot${config.telegram.botToken}/sendDocument`, { method: 'POST', body: form });
        return null;
      } catch (e) {
        return `⚠️ Approach PDF failed: ${e.message}`;
      }
    }
    return '📄 Usage:\n/report monthly — Monthly intelligence brief PDF\n/report approach [market] [product] — Approach pack PDF';
  });

  telegramAlerter.onCommand('/conf', async () => {
    try {
      const { searchKnowledge } = await import('./lib/aria/knowledge.mjs');
      const result = searchKnowledge('defence exhibition conference 2026');
      return result || '📅 Conference data available via ARIA. Ask: /aria What defence exhibitions should we attend?';
    } catch { return '📅 Use /aria What defence exhibitions are coming up?'; }
  });

  telegramAlerter.startPolling(config.telegram.botPollingInterval);
}

// === Discord Bot ===
if (discordAlerter.isConfigured) {
  console.log('[Crucix] Discord bot enabled');

  discordAlerter.onCommand('status', async () => {
    const uptime = Math.floor((Date.now() - startTime) / 1000);
    const h = Math.floor(uptime / 3600);
    const m = Math.floor((uptime % 3600) / 60);
    const sourcesOk = currentData?.meta?.sourcesOk || 0;
    const sourcesTotal = currentData?.meta?.sourcesQueried || 0;
    const sourcesFailed = currentData?.meta?.sourcesFailed || 0;
    const llmStatus = llmProvider?.isConfigured ? `✅ ${llmProvider.name}` : '❌ Disabled';
    const nextSweep = lastSweepTime
      ? logTimeShort(new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000))
      : 'pending';
    return [
      `**🖥️ ARIA STATUS**\n`,
      `Uptime: ${h}h ${m}m`,
      `Last sweep: ${lastSweepTime ? logTimeShort(new Date(lastSweepTime)) + ' London' : 'never'}`,
      `Next sweep: ${nextSweep} London`,
      `Sweep in progress: ${sweepInProgress ? '🔄 Yes' : '⏸️ No'}`,
      `Sources: ${sourcesOk}/${sourcesTotal} OK${sourcesFailed > 0 ? ` (${sourcesFailed} failed)` : ''}`,
      `LLM: ${llmStatus}`,
      `SSE clients: ${sseClients.size}`,
      `Dashboard: http://localhost:${config.port}`,
    ].join('\n');
  });

  discordAlerter.onCommand('sweep', async () => {
    if (sweepInProgress) return '🔄 Sweep already in progress. Please wait.';
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    return '🚀 Manual sweep triggered. You\'ll receive alerts if anything significant is detected.';
  });

  discordAlerter.onCommand('brief', async () => {
    if (!currentData) return '⏳ No data yet — waiting for first sweep to complete.';
    const tg = currentData.tg || {};
    const energy = currentData.energy || {};
    const delta = memory.getLastDelta();
    const ideas = (currentData.ideas || []).slice(0, 3);
    const sections = [`**📋 ARIA BRIEF**\n_${londonTs()} London_\n`];
    if (delta?.summary) {
      const dirEmoji = { 'risk-off': '📉', 'risk-on': '📈', 'mixed': '↔️' }[delta.summary.direction] || '↔️';
      sections.push(`${dirEmoji} Direction: **${delta.summary.direction.toUpperCase()}** | ${delta.summary.totalChanges} changes, ${delta.summary.criticalChanges} critical\n`);
    }
    const vix = currentData.fred?.find(f => f.id === 'VIXCLS');
    const hy = currentData.fred?.find(f => f.id === 'BAMLH0A0HYM2');
    if (vix || energy.wti) {
      sections.push(`📊 VIX: ${vix?.value || '--'} | WTI: $${energy.wti || '--'} | Brent: $${energy.brent || '--'}`);
      if (hy) sections.push(`   HY Spread: ${hy.value} | NatGas: $${energy.natgas || '--'}`);
      sections.push('');
    }
    if (tg.urgent?.length > 0) {
      sections.push(`📡 OSINT: ${tg.urgent.length} urgent signals, ${tg.posts || 0} total posts`);
      for (const p of tg.urgent.slice(0, 2)) sections.push(`  • ${(p.text || '').substring(0, 80)}`);
      sections.push('');
    }
    if (ideas.length > 0) {
      sections.push(`**💡 Top Ideas:**`);
      for (const idea of ideas) sections.push(`  ${idea.type === 'long' ? '📈' : idea.type === 'hedge' ? '🛡️' : '👁️'} ${idea.title}`);
    }
    return sections.join('\n');
  });

  discordAlerter.onCommand('portfolio', async () => {
    return '📊 Portfolio integration requires Alpaca MCP connection.\nUse the Crucix dashboard or Claude agent for portfolio queries.';
  });

  discordAlerter.start().catch(err => {
    console.error('[Crucix] Discord bot startup failed (non-fatal):', err.message);
  });
}

// === Express Server ===
const app = express();

// Trust proxy — required on Seenode/Render/Railway (behind reverse proxy)
// Without this, express-rate-limit sees all users as same IP (the proxy)
app.set('trust proxy', 1);

// ── Security headers ──────────────────────────────────────────────────────────
applySecurityHeaders(app);

// ── Request body parsing — tiered limits ─────────────────────────────────────
//
// Stripe webhook MUST receive the raw request body so the signature header
// can be verified against it. Mount the raw parser BEFORE any json parser
// so the json parser sees req._body=true and skips. Once express.json has
// consumed a Buffer, signature verification fails with "no signatures
// match" — an obscure error to debug, hence the explicit ordering here.
app.use('/api/billing/webhook', express.raw({ type: '*/*', limit: '1mb' }));

// ── R-F713 (2026-05-19) — multipart pass-through for /extract-document ───
//
// The aria.html chat composer's 📎 Attach button POSTs multipart/form-data
// to /api/aria/extract-document. The catch-all `app.use('/api/aria', express
// .json())` below consumed the body before ariaProxy could see it, leaving
// the proxy to JSON.stringify(undefined) and ship a JSON envelope to fly's
// multipart-only endpoint → 422 every time. Live evidence 2026-05-19:
// operator hit "Failed to process Peru - Internal Economics Breakdown.pdf:
// 422" twice in a row.
//
// This handler MUST be registered BEFORE the json mount so express never
// touches the body. We stream the raw request body straight to fly,
// preserving Content-Type (multipart with boundary) + Authorization, and
// stream the response back. No parsing on either hop.
//
// Other /api/aria/* endpoints continue through ariaProxy + express.json —
// only the multipart route diverges.


// R-F1848: Proxy WA listener accounts API for web UI access
const WA_LISTENER_URL = process.env.WA_LISTENER_URL || 'http://aria-wa.internal:5070';
// R-F1860: the WA listener authenticates the INTERNAL hop with ARIA_INTERNAL_TOKEN
// (its requireAuth checks token === ARIA_INTERNAL_TOKEN). These routes already
// authenticate the END USER via requireAuth, so the upstream hop must carry the
// SERVICE token — forwarding the user's browser token made aria-wa 401 every QR/
// account request, so no user could ever load a QR. Mirrors the extract-document
// proxy pattern already in this file (ARIA_API_TOKEN || ARIA_INTERNAL_TOKEN).
const WA_SERVICE_AUTH = 'Bearer ' + (process.env.ARIA_INTERNAL_TOKEN || process.env.ARIA_API_TOKEN || '');

async function syncWaLinkedGovernance(userId, grant) {
  const response = await fetch(WA_LISTENER_URL + '/api/wa-listener/governance', { // no-breaker: immediate user safety control must make one bounded authoritative call
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': userId },
    body: JSON.stringify({ governance: grant }),
    signal: AbortSignal.timeout(5000),
  });
  if (!response.ok) throw new Error(`aria-wa governance update failed with HTTP ${response.status}`);
  return response.json();
}

// R-F3578 — channel choice and consent are server policy, not presentation.
// The official channel is the default. Linked-device QR creation remains locked
// until the signed-in user has MFA and creates a complete, time-limited grant.
app.get('/api/wa/governance', requireAuth, (req, res) => {
  const user = findUserById(req.user?.userId);
  if (!user) return res.status(404).json({ error: 'User not found' });
  res.setHeader('Cache-Control', 'no-store');
  return res.json(publicGovernanceState(
    user.waLinkedGrant,
    (process.env.ARIA_WHATSAPP_OFFICIAL_NUMBER || '').trim(),
  ));
});

app.post('/api/wa/governance', requireAuth, express.json({ limit: '50kb' }), async (req, res) => {
  const user = findUserById(req.user?.userId);
  if (!user) return res.status(404).json({ error: 'User not found' });
  const { action, totpCode } = req.body || {};
  if (action === 'pause' || action === 'revoke') {
    if (!user.waLinkedGrant) return res.status(409).json({ error: 'No linked-device consent exists' });
    const now = new Date().toISOString();
    const grant = {
      ...user.waLinkedGrant,
      status: action === 'pause' ? 'paused' : 'revoked',
      pausedAt: action === 'pause' ? now : user.waLinkedGrant.pausedAt,
      revokedAt: action === 'revoke' ? now : user.waLinkedGrant.revokedAt,
    };
    try {
      await syncWaLinkedGovernance(user.id, grant);
    } catch (error) {
      return res.status(503).json({ error: 'governance_sync_failed', message: 'ARIA could not safely change the live linked session. Try again or disconnect the device in WhatsApp.' });
    }
    updateUser(user.id, { waLinkedGrant: grant });
    return res.json(publicGovernanceState(grant, (process.env.ARIA_WHATSAPP_OFFICIAL_NUMBER || '').trim()));
  }
  if (action !== 'accept_linked_risk') return res.status(400).json({ error: 'Unsupported governance action' });
  if (!user.twoFactorEnabled || !user.twoFactorSecret) {
    return res.status(403).json({ error: 'mfa_required', message: 'Enable two-factor authentication before linked-device access.' });
  }
  if (!await verifyTotpCode(totpCode, user.twoFactorSecret)) {
    return res.status(403).json({ error: 'step_up_failed', message: 'A current authenticator code is required.' });
  }
  const issued = issueLinkedGrant(req.body);
  if (!issued.ok) return res.status(400).json(issued);
  try {
    await syncWaLinkedGovernance(user.id, issued.grant);
  } catch (error) {
    // No account commonly exists before first consent; the listener returns
    // success with updated=0. A network failure is different: fail closed so
    // the browser never reports an active grant the enforcement tier did not see.
    return res.status(503).json({ error: 'governance_sync_failed', message: 'WhatsApp enforcement is unavailable; linked access was not enabled.' });
  }
  updateUser(user.id, { waLinkedGrant: issued.grant });
  logAudit({
    adminId: user.id,
    adminEmail: user.email || '',
    action: 'wa_linked_consent_created',
    targetId: user.id,
    targetEmail: user.email || '',
    targetName: user.fullName || '',
    notes: `scope_count=${issued.grant.scopes.length}; expires_at=${issued.grant.expiresAt}`,
  });
  return res.json(publicGovernanceState(issued.grant, (process.env.ARIA_WHATSAPP_OFFICIAL_NUMBER || '').trim()));
});

app.get('/api/wa-listener/accounts', requireAuth, async (req, res) => {
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/accounts', {
      // R-F1909 (G3): pin the JWT user so the listener scopes accounts per-owner.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': req.user?.userId || '' },
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'WA listener unreachable', detail: e.message });
  }
});

// R-F1868 (ARIA finding): this POST route is registered (~1097) BEFORE the
// `app.use('/api/', express.json())` mount (~1202), so without a route-level
// parser req.body is undefined → JSON.stringify(req.body || {}) forwards `{}`
// and the user-supplied `name` is dropped (account created with the auto-id as
// its name). A route-level express.json() runs the parser before the handler.
// ── R-F3587 — PHONE ↔ ACCOUNT BINDING (aria-web side) ───────────────────────
//
// aria-web is the tier that knows who is signed in, so it is the only thing
// allowed to mint a pairing code. The listener never decides who DESERVES a
// code; it only proves which handset answered one. Those two halves together
// are what makes a sender "verified": authenticated session + physical handset.
app.post('/api/wa/binding/code', requireAuth, express.json({ limit: '8kb' }), async (req, res) => {
  const user = findUserById(req.user?.userId);
  if (!user) return res.status(401).json({ error: 'unknown_user' });
  const code = generateCode();                      // 6 digits, crypto random
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/binding/code', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Authorization': WA_SERVICE_AUTH },
      body: JSON.stringify({ userId: user.id, code }),
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return res.status(r.status).json(data);
    // The code is returned ONLY to the authenticated session that asked for it.
    return res.json({ ok: true, code, expiresAt: data.expiresAt,
      instructions: 'Send this code to ARIA on WhatsApp from the handset you want to link.' });
  } catch (e) {
    // Never invent a code the listener has not stored — the user would send
    // something that can never be honoured and conclude ARIA is broken.
    return res.status(503).json({ error: 'wa_listener_unreachable', detail: e.message });
  }
});

app.get('/api/wa/binding', requireAuth, async (req, res) => {
  const uid = req.user?.userId || '';
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/binding/' + encodeURIComponent(uid), {
      // R-F3832 — pin the JWT user so the listener's own ownership check
      // (_waBindingOwns) is load-bearing for real traffic. Without the header the
      // listener treats the caller as admin/internal and trusts the path uid,
      // which leaves the check inert on exactly the path that matters.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': uid },
      signal: AbortSignal.timeout(10000),
    });
    return res.status(r.status).json(await r.json().catch(() => ({})));
  } catch (e) {
    return res.status(503).json({ error: 'wa_listener_unreachable', detail: e.message });
  }
});

app.delete('/api/wa/binding', requireAuth, async (req, res) => {
  const uid = req.user?.userId || '';
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/binding/' + encodeURIComponent(uid), {
      method: 'DELETE',
      // R-F3832 — see the GET above. This is the verb the traversal targeted.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': uid },
      signal: AbortSignal.timeout(10000),
    });
    return res.status(r.status).json(await r.json().catch(() => ({})));
  } catch (e) {
    return res.status(503).json({ error: 'wa_listener_unreachable', detail: e.message });
  }
});

app.post('/api/wa-listener/accounts', requireAuth, express.json({ limit: '100kb' }), async (req, res) => {
  try {
    const user = findUserById(req.user?.userId);
    const grantState = linkedGrantState(user?.waLinkedGrant);
    if (!grantState.active) {
      return res.status(403).json({
        error: grantState.code,
        message: 'Complete the advanced linked-device risk, scope and MFA flow before requesting a QR code.',
      });
    }
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/accounts', {
      method: 'POST',
      // R-F1909 (G3): pin the JWT user so the new account is owned by its creator.
      headers: { 'Content-Type': 'application/json', 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': req.user?.userId || '' },
      body: JSON.stringify({ name: req.body?.name || 'My WhatsApp', governance: user.waLinkedGrant }),
      signal: AbortSignal.timeout(15000),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'WA listener unreachable', detail: e.message });
  }
});

app.get('/api/wa-listener/accounts/:id', requireAuth, async (req, res) => {
  // R-F3832 — this fetch carries Bearer ARIA_INTERNAL_TOKEN, which the listener's
  // requireAuth accepts unconditionally. Raw concatenation let `..%2f..%2fmessages`
  // reach GET /messages, which returns every account's messageStore with no owner
  // filter. Validate first, then encode.
  if (!isValidWaAccountId(req.params.id)) return rejectBadPathSegment(res, 'account id', req.params.id);
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/accounts/' + encodeURIComponent(req.params.id), {
      // R-F1909 (G3): pin the JWT user so the listener owner-gates this account read.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': req.user?.userId || '' },
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'WA listener unreachable', detail: e.message });
  }
});

app.get('/api/wa-listener/accounts/:id/qr', requireAuth, async (req, res) => {
  // R-F3832 — see the sibling handler above.
  if (!isValidWaAccountId(req.params.id)) return rejectBadPathSegment(res, 'account id', req.params.id);
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/accounts/' + encodeURIComponent(req.params.id) + '/qr', {
      // R-F1909 (G3): pin the JWT user — without this any logged-in user could read
      // another user's QR and link (hijack) their WhatsApp session.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': req.user?.userId || '' },
      signal: AbortSignal.timeout(10000),
    });
    const text = await r.text();
    res.status(r.status).type(r.headers.get('content-type') || 'text/html').send(text);
  } catch (e) {
    res.status(503).json({ error: 'WA listener unreachable', detail: e.message });
  }
});

app.delete('/api/wa-listener/accounts/:id', requireAuth, async (req, res) => {
  // R-F3832 — see the sibling handler above. A traversal on this verb reached
  // DELETE /api/wa-listener/binding/<victimUserId>, which reads the uid straight
  // from the path with no ownership check of its own.
  if (!isValidWaAccountId(req.params.id)) return rejectBadPathSegment(res, 'account id', req.params.id);
  try {
    const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/accounts/' + encodeURIComponent(req.params.id), {
      method: 'DELETE',
      // R-F1909 (G3): pin the JWT user so only the owner can delete their account.
      headers: { 'Authorization': WA_SERVICE_AUTH, 'X-WA-User': req.user?.userId || '' },
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(503).json({ error: 'WA listener unreachable', detail: e.message });
  }
});
app.post('/api/aria/extract-document', requireAuth, async (req, res) => {
  // R-F2606 — this streaming upload is registered before the rate limiter and
  // has no body-size cap; reject oversized uploads up front via Content-Length.
  //
  // R-F3988 (C-73) — the limit is the CALLER'S TIER limit, not one literal for
  // everyone. The `25 * 1024 * 1024` this replaces was wrong for every tier at
  // once: free and pro are sold 5 MB and could send 25 MB, while proIntel is
  // sold 50 MB and was refused at 25 — a paid feature that did not exist. The
  // decision lives in lib/billing/uploadLimit.mjs so it is contract-testable;
  // this file boots a live app on import.
  //
  // `length_unknown` (a chunked body, no Content-Length) is passed through with
  // the SAME behaviour as before — the old guard's `Number(undefined) > limit`
  // was false too. That bypass is C-76 and is fixed separately; it is called out
  // here so the next reader does not mistake this branch for a check.
  const _upTier = (() => { try { return findUserById(req.user?.userId)?.tier || null; } catch { return null; } })();
  const _upVerdict = uploadTooLarge(req.headers['content-length'], _upTier);
  if (_upVerdict && _upVerdict.reason === 'too_large') {
    return res.status(413).json({ error: uploadTooLargeMessage(_upVerdict) });
  }
  // R-F3989 (C-74) — consume the uploadsPerDay allowance. Everything else needed
  // to enforce it already existed (tiers.uploadsPerDay, the quotas.mjs 'upload'
  // key, _capForKind, enforce.mjs's documented kind); the only missing piece was
  // a caller, so the cap shown to the customer bounded nothing on the path they
  // actually use.
  //
  // AFTER the size check on purpose: an upload refused for being too large must
  // not burn a day's allowance. _quotaBlock keeps the R-F3618 exemptions, so
  // admins and the internal/WA callers are unmetered exactly as on the other
  // three lanes.
  const _upq = await _quotaBlock(req, 'upload');
  if (_upq) return res.status(429).json({ error: _upq.reason, quota: { current: _upq.current, cap: _upq.cap } });
  const ARIA_URL = process.env.ARIA_SERVICE_URL || '';
  if (!ARIA_URL) {
    return res.status(503).json({ error: 'ARIA service unavailable' });
  }
  // R-F2101 (2026-06-28, ARIA web DD): now gated by requireAuth. Pre-fix this did
  // only a `Bearer `-PREFIX presence check (any string passed — expired JWTs, junk)
  // and then forwarded to the brain with the SERVER's token → effectively
  // unauthenticated document extraction on the brain via our credential. requireAuth
  // properly verifies the JWT (+ tokenVersion) or the internal token, so only real
  // users / internal callers reach it; legit WA/internal callers still pass via the
  // internal-token bypass. (requireAuth only reads the auth header — it does not
  // touch the multipart body, so the streaming upload below is unaffected.)
  const ct = req.headers['content-type'] || '';
  if (!ct.includes('multipart/')) {
    return res.status(400).json({
      error: 'expected multipart/form-data',
      hint: 'use the /api/aria/extract-document-json route for JSON callers',
    });
  }
  // Declared outside the try so the catch can tell "the caller sent too much"
  // apart from "the upstream broke" — those need different status codes, and a
  // 502 on an oversized upload would tell the user to retry something that can
  // never succeed.
  let _meter = null;
  try {
    const { Readable } = await import('node:stream');
    // Use the seenode-side token (matches every other ariaProxy call): if
    // client sent ARIA_API_TOKEN we still re-issue from env so a leaked
    // client token can't escalate. Same pattern as _ariaHeaders().
    const upstreamToken = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    const headers = {
      'Content-Type': ct,
      'Authorization': upstreamToken ? `Bearer ${upstreamToken}` : (req.headers.authorization || ''),  // R-F2383: `clientAuth` was undefined → ReferenceError if env token unset
    };
    if (req.headers['content-length']) {
      headers['Content-Length'] = req.headers['content-length'];
    }
    // R-F3997 (C-78) — measure the bytes that actually arrive.
    //
    // The Content-Length check above cannot see a chunked body: a request with no
    // such header made `Number(undefined) > limit` false, and this line then piped
    // it upstream unmeasured. Refusing chunked outright would bound it but break
    // legitimate streaming clients, and would still be trusting a CLAIM — the
    // header is what the client says, these bytes are what it sent. The meter
    // catches the absent header and the LYING one with the same code, and aborts
    // mid-stream rather than after the whole payload has been paid for.
    _meter = createUploadMeter(maxRequestBytesFor(_upTier));
    req.pipe(_meter.stream);
    const upstream = await fetch(`${ARIA_URL}/api/aria/extract-document`, {
      method: 'POST',
      headers,
      body: Readable.toWeb(_meter.stream),
      duplex: 'half',                 // required by undici when body is a stream
      signal: AbortSignal.timeout(120000), // PDF OCR can take ~60-90s on cold cache
    });
    const respCt = upstream.headers.get('content-type') || 'application/json';
    const bodyText = await upstream.text();
    res.status(upstream.status).type(respCt).send(bodyText);
  } catch (e) {
    const detail = e && e.message ? e.message : String(e);
    // R-F3997 — an aborted-for-size stream is the CALLER's error, not ours. The
    // fetch rejects either way, so without this branch an oversized chunked
    // upload returned 502 proxy_error: it reads as "our service is broken, try
    // again", which is both untrue and unactionable.
    if (_meter && _meter.exceeded()) {
      const _v = uploadTooLarge(Number.MAX_SAFE_INTEGER, _upTier);
      return res.status(413).json({ error: uploadTooLargeMessage(_v) });
    }
    console.warn(`[ARIA proxy] /extract-document threw: ${detail}`);
    res.status(502).json({ error: 'proxy_error', detail });
  }
});

app.use('/api/aria',  express.json({ limit: '500kb' }));
app.use('/api/brain', express.json({ limit: '500kb' }));
app.use('/api/',      express.json({ limit: '100kb' }));
app.use('/api/',      express.urlencoded({ extended: true, limit: '50kb' }));
app.use(express.json());  // fallback for non-API routes

// ── WhatsApp (ARIA listens via Twilio) — mounted BEFORE rate limiting ───────
// WhatsApp webhook uses urlencoded body (not JSON), parsed inside the router
app.use('/api/whatsapp', ariaWhatsApp);

// ── R-F577 (2026-05-16) — public model-card endpoints ────────────────────
//
// imaria.io/model-card.html is published-by-design — it's the
// operator's policy statement (constitution version, audit-log fingerprint,
// adversarial baseline). The previous seenode catch-all at line 4099+
// required auth on every /api/aria/*, breaking the public model card and
// forcing "unavailable" placeholders on a page meant to be public.
//
// R-F577 registers these 3 endpoints BEFORE all auth-gated routes so
// Express's order-of-definition gives them to the no-auth handler.
// They expose ONLY publishable metadata:
//   - /constitution/version   → {version, clause_count, amendment_count}
//   - /chat-audit/stats        → {head_hash, total_entries} (no chain body)
//   - /adversarial/stats        → {last_run.overall_score, last_run.run_at}
// The full constitution text, audit chain, and attack-by-attack
// breakdown remain auth-gated under the catch-all.
//
// Hot-path note: ariaProxy at this point requires the function to be
// defined. We forward-declare a thin helper here that defers the import
// of ariaProxy to request time, since ariaProxy itself is defined below
// at line ~1818.
async function _r577PublicProxy(req, res, path) {
  return ariaProxy(req, res, path, {
    fallback: async ({ lastStatus } = {}) => {
      if (res.headersSent) return;
      res.status(lastStatus || 503).json({
        error: 'fly endpoint unavailable',
        path,
      });
    },
  });
}
app.get('/api/aria/constitution/version', (req, res) =>
  _r577PublicProxy(req, res, '/api/aria/constitution/version'));
app.get('/api/aria/chat-audit/stats', (req, res) =>
  _r577PublicProxy(req, res, '/api/aria/chat-audit/stats'));
app.get('/api/aria/adversarial/stats', (req, res) =>
  _r577PublicProxy(req, res, '/api/aria/adversarial/stats'));
// R-F2617: signing-key fingerprint for the model-card audit section. Publishable
// (16 hex of SHA-256(key); the key itself is never exposed) — replaces the stale
// hardcoded `a39f3328d92bffe4` that lied after the key rotated 2026-05-17.
app.get('/api/aria/audit/key-fingerprint', (req, res) =>
  _r577PublicProxy(req, res, '/api/aria/audit/key-fingerprint'));

// ── Rate limiting + XSS guard — BEFORE route registration ────────────────────
applyRateLimiting(app);
applyInputValidation(app);

// ── R-F2775: OPERATOR/INFRA API ROLE GATE ───────────────────────────────────
// R-F2774 gated the operator PAGES; this gates the APIs behind them. Before this,
// every infra endpoint was `requireAuth` — i.e. readable by ANY signed-up viewer
// (cost ledger, autonomy state, brain internals, student mastery) and in several
// cases RUNNABLE by them (seed runs, weekly adversarial sweeps, diagnostics).
//
// MOUNT POINT IS LOAD-BEARING — do not move this below the explicit /api/aria
// routes. Express matches in registration order: the explicit handlers live at
// ~2848-3760 and the catch-all at ~6027, so a gate registered near the catch-all
// would never fire for any of them. Mounted HERE it sees every /api/aria/* request
// first. Corollary: the four R-F577 public model-card endpoints registered ABOVE
// (~1427-1437) are exempt automatically — their handlers already responded — which
// is why /adversarial/stats stays public while the rest of /adversarial is gated.
//
// The classification lives in lib/auth/infraRoutes.mjs (shared with tests).
// Default is PASS-THROUGH: an unlisted path keeps exactly its prior gate, so the
// failure mode is "not yet gated", never "customer locked out".
app.use('/api/aria', (req, res, next) => {
  // R-F2802 (SECURITY) — a path still percent-encoded after ONE decode is
  // double-encoded, i.e. deliberately crafted to walk past a single-decode
  // classifier. Refuse it outright rather than guess which form the upstream
  // will route on. No legitimate ARIA path contains a literal '%'.
  if (isDoubleEncodedPath(req.path)) {
    return res.status(400).json({ error: 'Malformed request path' });
  }
  const needed = requiredRoleForAriaPath(req.method, req.path);
  if (!needed) return next();               // customer surface — untouched
  return requireInfraRole(needed)(req, res, next);
});

// ── Observability — structured error logging ──────────────────────────────────
const ADMIN_CHAT_ID = process.env.TELEGRAM_ADMIN_CHAT_ID || process.env.TELEGRAM_CHAT_ID;
const notifyAdmin = async (msg) => {
  if (!telegramAlerter?.isConfigured) return;
  try { await telegramAlerter.sendMessage?.(msg); } catch {}
};
configureTelemetry(redisAdapter, notifyAdmin);
initComplianceAudit(redisAdapter);

// ── Procurement dedup + source pruner ────────────────────────────────────────
const procDedup   = new ProcurementDedup(redisAdapter);
const sourcePruner = new SourcePruner(redisAdapter, notifyAdmin);

// Wire pruner + errorTracker into sweep source runner
registerSourceHooks({
  onSuccess: (name, latencyMs) => {
    sourcePruner.recordFetch(name, true,  latencyMs).catch(() => {});
    errorTracker.recordSuccess(name);
  },
  onError: (name, err, latencyMs) => {
    sourcePruner.recordFetch(name, false, latencyMs).catch(() => {});
    errorTracker.record(name, 'fetch_error', err);
  },
  isSuspended: (name) => sourcePruner.isSuspended(name),
});

// ── Compliance list auto-refresh (weekly, non-blocking) ───────────────────────
if (redisAdapter.isConfigured) {
  startComplianceRefreshScheduler(redisAdapter, notifyAdmin).catch(e =>
    console.warn('[Compliance] Refresh scheduler failed to start:', e.message)
  );
}

// ── Inject LLM provider into Telegram ARIA commands ──────────────────────────
setTelegramLLM(llmProvider);

// Site access is protected by the Angular JWT auth layer — no HTTP Basic Auth needed.

// Static HTML dashboard — served from public/
// R-F441 (2026-05-13): force revalidation on HTML so dashboard fixes
// (e.g. R-F433 prompt→modal) take effect on next navigation without a
// hard-refresh. CSS/JS still use ?v=N cache-busters; hashed assets
// stay cacheable. HTML is the only file that gets aggressively held by
// browsers without a fingerprint.
const PUBLIC_DIR = join(ROOT, 'public');

// ── R-F2774: OPERATOR/INFRA PAGE GATE ───────────────────────────────────────
// MUST be registered BEFORE express.static below, or static serves these .html
// anonymously first (the express.static mount is top-level middleware — whichever
// route matches first wins). View pages → poweruser or admin; mutating/admin pages
// → admin only. Non-authorized navigations are redirected (signin / dashboard).
// Every URL form of each page is covered. Operator (admin) always passes.
// R-F2785: the page tables moved to lib/auth/operatorPages.mjs so tests can assert
// the real contract (which pages exist, which file each serves, which role each
// demands) instead of grepping this file for a literal route string.
const _sendOperatorPage = (file) => (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.sendFile(join(PUBLIC_DIR, file));
};
// R-F2818: ONE choke point, not N literal routes. Express route matching compares
// the RAW (undecoded) req.path, so per-route `app.get('/admin.html', gate)`
// registrations were bypassable with `/%61dmin.html` or `//admin.html` — the path
// missed the gate, fell through to express.static, and `send` decoded it and served
// the file. Verified live before this fix (see lib/auth/operatorPages.mjs).
// Matching now runs through the SAME normaliser as the /api/aria gate, and
// double-encoded paths fail closed with 400 exactly as they do at :1468.
app.use((req, res, next) => {
  if (req.method !== 'GET' && req.method !== 'HEAD') return next();
  if (isDoubleEncodedPath(req.path)) {
    return res.status(400).json({ error: 'Malformed request path' });
  }
  const page = operatorPageFor(req.path);
  if (!page) return next();  // not an operator page → express.static may serve it
  return requirePageRole(...page.roles)(req, res, () => _sendOperatorPage(page.file)(req, res));
});

// ── R-F3142: the standalone status PAGE is retired ──────────────────────────
// public/status.html is gone; the Vetting module took its place in the nav.
// /api/status is UNCHANGED and still public — it is the machine-readable
// availability surface, external monitors may already poll it, and terms.html
// §"availability" now names it as the contractual publication point.
//
// A permanent redirect rather than a 404 because the retired URL was published
// in our own Terms of Service and model card for months; a bookmark or an
// external monitor pointed at it must land somewhere truthful, not on a dead
// page. 308 (not 301) so a monitor issuing HEAD/GET keeps its method.
app.get('/status.html', (_req, res) => res.redirect(308, '/api/status'));

// ── R-F3180/R-F3181: the vetting portal (UNAUTHENTICATED) ───────────────────
//
// An applicant or a nominated referee reaches this with a link — they have no
// account, so requireAuth would make the feature impossible. Mounted HERE,
// before the authenticated /api/aria catch-all, and pointed at the brain's
// separate unauthenticated router (routes/vetting_portal.py). Its own tighter
// rate-limit tier is applied in middleware/rateLimiter.mjs.
//
// The token travels in the PATH, never in a query string: query strings land in
// access logs, Referer headers and analytics far more readily, and this token
// is a bearer credential for a screening file.
// The pretty URL an applicant actually receives. express.static cannot serve a
// path segment as a token, so the page is served for any /vetting-portal/<token>
// and reads the token from its own path.
app.get(/^\/vetting-portal\/[^/]+$/, (_req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('X-Robots-Tag', 'noindex, nofollow');
  res.sendFile(join(PUBLIC_DIR, 'vetting-portal.html'));
});

app.use('/api/vetting-portal', express.json({ limit: '24mb' }));

// ── R-F3682 — the proxied suffix is an ALLOWLIST, not a sanitiser ───────────
//
// THE DEFECT (proven live 2026-08-04, unauthenticated, from the public net):
//   GET /api/aria/cost/monthly/status                                  -> 401
//   GET /api/vetting-portal/..%2f..%2fapi%2faria%2fcost%2fmonthly%2fstatus -> 200
// Express percent-DECODES a regex route's capture group, so `%2f` arrives as
// `/` and the `..` segments survive into the template string below; WHATWG URL
// parsing then collapses them. Because this route is (correctly) unauthenticated
// and is mounted BEFORE the authenticated /api/aria catch-all, and because
// _ariaHeaders() attaches the brain service token, one request turned an
// anonymous caller into an authenticated brain caller — bypassing requireAuth,
// the infra-role gate and pinNonAdminUserId in a single hop.
//
// A blocklist ("reject %, reject ..") is the wrong shape: it is a guess about
// which encodings Express and WHATWG will collapse, and the next encoding that
// normalises to `/` re-opens it. The brain exposes exactly TWO portal routes —
// `GET /{token}` and `POST /{token}/documents` (routes/vetting_portal.py:81,160)
// — so the honest boundary is to enumerate them. The rationale, the charset and
// the bounds all live in lib/vetting/portalPath.mjs, which the capability test
// imports so it exercises the shipped validator rather than a copy.
app.all(/^\/api\/vetting-portal\/(.+)$/, async (req, res) => {
  if (!ARIA_SERVICE_URL) {
    return res.status(503).json({ error: 'Service temporarily unavailable' });
  }
  const suffix = req.params[0];
  if (!isValidVettingPortalSuffix(suffix)) {
    // §21a — a refused traversal is security-relevant, so it must reach the
    // brain, not just the console. status 403 makes classifyError return
    // SEVERITY.AUTH (errorTracker.mjs:70-73), which is on the ESCALATE list —
    // a plain Error would classify TRANSIENT and be dropped before the wire.
    const rejected = new Error(
      `vetting-portal suffix rejected (${suffix.length} chars, ` +
      `first 24: ${JSON.stringify(suffix.slice(0, 24))})`,
    );
    rejected.status = 403;
    try { errorTracker.record('vetting_portal_proxy', 'suffix_rejected', rejected); } catch {}
    // Same indistinguishable 404 the brain's portal returns for every failure
    // (routes/vetting_portal.py:51-56) — do not confirm the path shape, and do
    // not reflect attacker-controlled input back into the response body.
    return res.status(404).json({ error: 'Not found' });
  }
  try {
    const r = await fetch(`${ARIA_SERVICE_URL}/api/vetting-portal/${suffix}`, {
      method: req.method,
      headers: _ariaHeaders(),
      body: (req.method === 'POST' || req.method === 'PUT')
        ? JSON.stringify(req.body || {}) : undefined,
      signal: AbortSignal.timeout(60000),
    });
    const body = await r.json().catch(() => ({ error: 'invalid upstream response' }));
    return res.status(r.status).json(body);
  } catch (err) {
    // R-F3682 §21a — was console-only, i.e. DARK: an applicant's upload failing
    // against a wedged brain left no trace anywhere the brain could see. A
    // transport blip still classifies TRANSIENT and stays off the escalation
    // path by design (errorTracker.mjs:96-98) — it is RECORDED either way, so
    // the local counter and brainWireStats can show it.
    try { errorTracker.record('vetting_portal_proxy', 'proxy_error', err); } catch {}
    console.error('[vetting-portal] proxy error:', err?.message || err);
    return res.status(502).json({ error: 'Service temporarily unavailable' });
  }
});

// ── R-F3185: mint a vetting invite AND deliver it, in one step ─────────────
//
// WHY MINT AND SEND TOGETHER, with no "re-send that link" button:
// the plaintext token exists for exactly one moment — it is returned once by
// the brain and never stored, which is what makes a database leak not also a
// link leak. Delivering later would require keeping it. So the send happens
// here, while it is in hand. Needing to reach someone again means minting a
// FRESH link and revoking the old one, which is also the better practice:
// re-sending one link to a second person is precisely what you do not want.
//
// Delivery lives in the WEB tier because both channels do — SMTP in
// lib/auth/email.mjs, WhatsApp on aria-wa. Doing it from the brain would mean
// a second hop carrying a live credential.
app.post('/api/vetting/share', requireAuth, express.json({ limit: '64kb' }), async (req, res) => {
  if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'Service temporarily unavailable' });
  const b = req.body || {};
  const caseId = String(b.case_id || '').trim();
  if (!caseId) return res.status(400).json({ error: 'case_id required' });

  const channel = String(b.channel || 'link').trim().toLowerCase();
  const to = String(b.to || '').trim();
  if (channel !== 'link' && !to) {
    return res.status(400).json({ error: 'a recipient is required for that channel' });
  }

  // 1. Mint. user_id is pinned from the JWT — never taken from the body.
  let minted;
  try {
    const r = await fetch(
      `${ARIA_SERVICE_URL}/api/aria/vetting/case/${encodeURIComponent(caseId)}/invites`
      + `?user_id=${encodeURIComponent(req.user?.userId || '')}`,
      {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({
          kind: b.kind || 'APPLICANT', entry_id: b.entry_id || '',
          referee_name: b.referee_name || '', referee_email: b.referee_email || '',
          ttl_days: b.ttl_days || 14,
        }),
        signal: AbortSignal.timeout(30000),
      });
    minted = await r.json().catch(() => ({}));
    if (!r.ok) return res.status(r.status).json(minted);
  } catch (err) {
    console.error('[vetting-share] mint failed:', err?.message || err);
    return res.status(502).json({ error: 'Could not create the link' });
  }

  const link = `${_portalBase(req)}/vetting-portal/${minted.token}`;
  const isReferee = (minted.kind || '') === 'REFEREE';
  const subject = isReferee
    ? 'Request to confirm an employment reference'
    : 'Upload your screening documents';
  const body = isReferee
    ? `Hello${b.referee_name ? ' ' + b.referee_name : ''},

`
      + `You have been nominated to confirm an employment reference. Please use `
      + `the secure link below. You will be asked only to confirm that one `
      + `engagement — no other information about the applicant is shared with you.

`
      + `${link}

This link expires on ${String(minted.expires_at || '').slice(0, 10)}. `
      + `Please do not forward it.`
    : `Hello,

Please upload the documents needed for your pre-employment `
      + `screening using the secure link below.

${link}

`
      + `This link expires on ${String(minted.expires_at || '').slice(0, 10)}. `
      + `It is private to you — please do not forward it.`;

  // 2. Deliver. A delivery failure must NOT lose the link: the caller still
  //    gets it back and can copy or print the QR, so a flaky mailbox never
  //    strands a freshly minted credential nobody can see again.
  let delivery = { channel, attempted: channel !== 'link', sent: false, detail: '' };
  try {
    if (channel === 'email') {
      // R-F3185 §3b: sendVettingInviteEmail is verified to exist in
      // lib/auth/email.mjs. The first cut of this called sendGenericEmail /
      // sendRawEmail — NEITHER exists, and the optional-chaining fallback would
      // have reported "sent" while sending nothing at all.
      const mail = await import('./lib/auth/email.mjs');
      await mail.sendVettingInviteEmail({
        to, recipientName: b.referee_name || '', link,
        expiresOn: String(minted.expires_at || '').slice(0, 10),
        isReferee, organisation: b.organisation || '',
        applicantName: b.applicant_name || '',
      });
      // isConfigured false means the module's relay wrote it to stdout. That is
      // NOT delivery, and must not be reported as such.
      delivery.sent = !!mail.isConfigured;
      if (!mail.isConfigured) {
        delivery.detail = 'SMTP is not configured on this server, so the message '
          + 'was not sent. Copy the link or share the QR code instead.';
      }
    } else if (channel === 'whatsapp') {
      const r = await fetch(WA_LISTENER_URL + '/api/wa-listener/send', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': WA_SERVICE_AUTH },
        body: JSON.stringify({ to, message: `${subject}

${body}` }),
        signal: AbortSignal.timeout(20000),
      });
      const wa = await r.json().catch(() => ({}));
      delivery.sent = r.ok;
      if (!r.ok) delivery.detail = wa?.error || `WhatsApp send failed (${r.status})`;
    }
  } catch (err) {
    delivery.detail = String(err?.message || err).slice(0, 200);
  }

  // The token is returned ONCE, here, exactly as the brain returns it once to
  // us. There is no endpoint that can show it again.
  return res.json({
    invite_id: minted.invite_id, kind: minted.kind,
    expires_at: minted.expires_at, link, delivery,
  });
});

// R-F3181 — QR for a case invite link. Rendered SERVER-SIDE as an SVG so the
// token never has to be handed to a third-party QR service or a CDN script,
// which for a link into a screening file would be an avoidable disclosure.
// JWT-gated: only the screening officer mints and prints these.
app.get('/api/vetting/qr', requireAuth, async (req, res) => {
  const target = String(req.query.url || '').trim();
  // Only ever encode a link to OUR portal. Without this the endpoint is an
  // open QR generator that renders whatever a caller supplies, over our
  // domain's trust.
  const expected = `${_portalBase(req)}/vetting-portal/`;
  if (!target.startsWith(expected)) {
    return res.status(400).json({ error: 'refusing to encode a non-portal URL' });
  }
  try {
    const QRCode = (await import('qrcode')).default;
    const svg = await QRCode.toString(target, {
      type: 'svg', errorCorrectionLevel: 'M', margin: 1, width: 240,
    });
    res.setHeader('Content-Type', 'image/svg+xml');
    res.setHeader('Cache-Control', 'no-store');   // it encodes a credential
    return res.send(svg);
  } catch (err) {
    console.error('[vetting-qr] render failed:', err?.message || err);
    return res.status(500).json({ error: 'could not render QR code' });
  }
});

function _portalBase(req) {
  const configured = (process.env.PUBLIC_BASE_URL || '').trim().replace(/\/$/, '');
  if (configured) return configured;
  // X-Forwarded-Proto is set by fly's proxy; fall back to the request host.
  const proto = (req.headers['x-forwarded-proto'] || req.protocol || 'https');
  return `${proto}://${req.headers.host}`;
}

app.use(express.static(PUBLIC_DIR, {
  setHeaders: (res, filePath) => {
    if (filePath.endsWith('.html')) {
      res.setHeader('Cache-Control', 'no-cache');
    }
  },
}));
app.get('/', (req, res) => {
  res.setHeader('Cache-Control', 'no-cache');
  res.sendFile(join(PUBLIC_DIR, 'index.html'));
});

// Auth pages — serve .html without extension
const authPages = ['signin', 'signup', 'forgot-password', 'recovery', 'dashboard'];
authPages.forEach(function(page) {
  app.get('/' + page, function(req, res) {
    res.sendFile(join(PUBLIC_DIR, page + '.html'));
  });
});
// R-F2774 — /aria-brain and /vault.htm are now served by the OPERATOR PAGE GATE
// registered above (before express.static), which requires poweruser/admin (brain)
// or admin (vault). The old ungated app.get routes here were removed — they were
// dead code (the gated routes win by earlier registration) and served the pages
// anonymously.
console.log('[Crucix] Static dashboard live at /');

app.get('/api/data', requireAuth, (req, res) => {
  if (!currentData) return res.status(503).json({ error: 'No data yet — first sweep in progress' });
  // R-F978 (2026-05-28): surface sweep freshness so the dashboard can warn
  // when the brief is stale (fresh container before first sweep, or a wedged
  // sweep loop) instead of rendering stale data as if it were live. Shallow
  // copy so the shared currentData object is never mutated. Stale = older
  // than two refresh cycles.
  const freshTs = currentData.meta?.timestamp || lastSweepTime;
  const ageSeconds = freshTs
    ? Math.max(0, Math.floor((Date.now() - new Date(freshTs).getTime()) / 1000))
    : null;
  const staleAfterSeconds = Math.round((config.refreshIntervalMinutes || 5) * 60 * 2);
  res.json({
    ...currentData,
    _freshness: {
      ageSeconds,
      staleAfterSeconds,
      stale: ageSeconds != null && ageSeconds > staleAfterSeconds,
      lastSweep: freshTs || null,
    },
  });
});

// R-F548 (2026-05-15) — fast /healthz for seenode platform liveness probe.
// Pre-R-F548: only /api/health existed and it does meaningful work
// (uptime calc, sweep state, currentData read). When the event loop is
// blocked by the sweep or by the email-reader processing the post-restart
// burst (398-email IMAP inbox + LinkedIn alerts arriving in real time),
// /api/health can stall past seenode's liveness probe timeout → seenode
// kills the container as "unresponsive" even though Node is alive.
// /healthz responds INSTANTLY (no async, no shared state read) so the
// probe always succeeds while the heavy paths run.
app.get('/healthz', (_req, res) => {
  res.status(200).type('text/plain').send('ok');
});

app.get('/api/health', (req, res) => {
  res.json({
    status: 'ok',
    build_rev: CRUCIX_BUILD_REV,
    uptime: Math.floor((Date.now() - startTime) / 1000),
    lastSweep: lastSweepTime,
    nextSweep: lastSweepTime
      ? new Date(new Date(lastSweepTime).getTime() + config.refreshIntervalMinutes * 60000).toISOString()
      : null,
    sweepInProgress,
    sweepStartedAt,
    // R-F2867 — every queried source must be accounted for, and no count may be
    // invented. This block used to ship only ok/failed (hiding partial, suspended
    // and not_configured, so ok+failed != total — live: 46/0/50, four sources
    // unexplained) and fell back to a FABRICATED `|| 36` total before the first
    // sweep. R-F2853 fixed the same shape on the briefing payload; this is the
    // surface public/dashboard.html actually reads.
    ...buildHealthSourceBuckets(currentData?.meta),
    llmEnabled: !!config.llm.provider,
    llmProvider: config.llm.provider,
    telegramEnabled: !!(config.telegram.botToken && config.telegram.chatId),
    refreshIntervalMinutes: config.refreshIntervalMinutes,
    language: currentLanguage,
  });
});

// Brain absorb diagnostic — surfaces the resolved BRAIN_URL, token presence,
// and per-module success/fail counters so the operator can see in real time
// whether the seenode→fly bridge is actually working. Added 2026-04-19 after
// 380+ emails processed but only 1 absorbed showed the failure was silent.
// R-F2775: was requireAuth (any signed-up viewer). Per-module absorb success/fail
// counters for the seenode→fly bridge — operator diagnostics.
app.get('/api/brain-absorb/diag', requireInfraRole('poweruser', 'admin'), (req, res) => {
  res.json(getBrainAbsorbStats());
});

// R-F45: surface the boot-time brain-bridge verdict so an operator can
// curl one endpoint to see whether the bridge passed self-check without
// reading the deploy log line-by-line. Returns the verdict from boot
// (cached) + a "rerun" option that re-pings synchronously.
// R-F2775: was ANONYMOUS for the cached read. The boot bridge verdict exposes
// internal wiring state (bridge healthy? token present?) — operator surface, not
// customer. `?rerun=1` keeps its stricter admin gate below (it costs work + LLM).
app.get('/api/brain-absorb/verify', requireInfraRole('poweruser', 'admin'), async (req, res) => {
  if (req.query?.rerun === '1') {
    // R-F2474 — rerun triggers a SYNCHRONOUS bridge re-ping (work + LLM cost);
    // gate it behind admin so it can't be spammed unauthenticated. The cached
    // read-through below stays public (read-only, zero side effects).
    return requireAdmin(req, res, async () => {
      const v = await runAndCacheBridgeVerdict({ telegramAlerter });
      return res.json(v);
    });
  }
  const cached = getBrainBridgeVerdict();
  if (!cached) return res.status(503).json({ error: 'boot self-check has not yet run; try ?rerun=1' });
  res.json(cached);
});

// C-27 / R-F3889 — the brain wire's own health, readable.
// R-F2821 instrumented this wire precisely because "a signal that silently fails
// is still dark" (§21a) — it counts delivered/dropped/throttled and records the
// last HTTP error instead of swallowing it. But `brainWireStats()` had NO caller
// outside test/, so in production the counters accumulated where nothing could
// read them and the wire was as unobservable as before the fix. An instrument
// nobody can read is indistinguishable from health — the same shape as the three
// Phase A gates certified by an absence (§1), route_audit returning {} for a
// 770-route app (§16), and the cost meter reading $0.00 through a store-less
// process (§17).
//
// Operator-gated, matching /api/brain-absorb/diag: it reveals whether the brain
// is reachable and whether a token is present (R-F2775).
app.get('/api/health/brain-wire', requireInfraRole('poweruser', 'admin'), (req, res) => {
  const stats = errorTracker.brainWireStats();
  // `configured` is the load-bearing field: an unset ARIA_SERVICE_URL yields all
  // -zero counters, which is byte-identical to a healthy-but-quiet tier. Report
  // the distinction explicitly rather than leaving the reader to infer it.
  const healthy = stats.configured && !stats.lastError && stats.droppedNoTarget === 0;
  res.json({
    ...stats,
    healthy,
    // Never let "no signal yet" read as "delivering fine".
    state: !stats.configured
      ? 'unconfigured'
      : stats.lastError
        ? 'failing'
        : stats.delivered > 0
          ? 'delivering'
          : 'no_signal_yet',
    target_env_var: 'ARIA_SERVICE_URL',
  });
});

// Cross-server health — does Node see fly.io and vice versa?
// Mirrors /api/aria/health/cross on the Python side. Added 2026-04-18
// after the DD-depth audit found that the two servers had drifted apart
// (each had sources the other didn't know about) without anyone noticing.
// R-F2860 — the external liveness observer is itself OBSERVABLE (an observability
// tool that cannot be observed is the very blind spot it exists to fix). These refs
// are assigned when the observer starts (server 'listening', below) and read here.
let _livenessObserverRef = null;
let _livenessOutageStoreRef = null;
app.get('/api/health/aria-intel-observer', (req, res) => {
  if (!_livenessObserverRef) {
    return res.json({ enabled: false, reason: 'ARIA_SERVICE_URL unset or observer not started' });
  }
  let recent_outages = [];
  try { recent_outages = (_livenessOutageStoreRef?.read() || []).slice(-10); } catch { /* best-effort */ }
  res.json({ enabled: true, ..._livenessObserverRef.snapshot(), recent_outages });
});

// This endpoint makes the drift loud — if either side is down or not
// seeing the other, it surfaces in one call.
app.get('/api/health/cross', async (req, res) => {
  const flyUrl = (process.env.ARIA_FLY_URL || 'https://aria-intel.fly.dev').replace(/\/$/, '');
  const out = {
    ok: false,
    generated_at: new Date().toISOString(),
    node: {
      server: 'seenode-node',
      ok: true,
      uptime_s: Math.floor((Date.now() - startTime) / 1000),
      last_sweep: lastSweepTime,
      sources_ok: currentData?.meta?.sourcesOk || 0,
      sources_failed: currentData?.meta?.sourcesFailed || 0,
      sources_total: currentData?.meta?.sourcesQueried || 0,
    },
    fly: { server: 'fly.io-aria_service', url: flyUrl, ok: false },
  };
  // R-F2776: honest fly-side classification. A timeout renders NO verdict
  // (ok === null), a transport refusal is real evidence of offline, and a slow but
  // healthy brain is online-degraded — never offline. Logic + the full rationale
  // live in lib/health/crossHealth.mjs so it is testable against the real function.
  out.fly = await probeFlyHealth({ flyUrl });
  out.ok = combineCrossOk(out.node.ok, out.fly.ok);
  res.json(out);
});

// R-F2775: was ANONYMOUS. This is the operator source-health panel (sources.html,
// an R-F2774 operator page) — it enumerates every configured integration plus its
// degraded/unconfigured/not-checked buckets, i.e. a map of which of ARIA's feeds are
// currently blind. No non-test caller outside public/ (verified by grep), so gating
// it breaks nothing. Read-only → poweruser suffices.
app.get('/api/source-health', requireInfraRole('poweruser', 'admin'), (req, res) => {
  const summary = getSourceHealthSummary();
  // R-F2719 (Codex #6) — an unconfigured integration (no API key/watchlist → reliability
  // null) or one not yet swept is NOT healthy. Bucket them separately so the count
  // MEASURES health instead of asserting it (was: null || >=80 counted as healthy).
  const b = classifySourceHealth(summary, 80);
  res.json({
    sources:           summary,
    degraded:          b.degradedNames,
    unconfigured:      b.unconfiguredNames,   // R-F2719 — Comtrade/CSL etc.: never feeding, not "healthy"
    notChecked:        b.notCheckedNames,     // R-F2719 — configured but not yet swept
    totalTracked:      summary.length,
    healthyCount:      b.counts.healthy,      // R-F2719 — only reliability >= 80
    degradedCount:     b.counts.degraded,
    unconfiguredCount: b.counts.unconfigured,
    notCheckedCount:   b.counts.notChecked,
    asOf:              lastSweepTime,
  });
});

app.get('/api/locales', (req, res) => {
  res.json({ current: currentLanguage, supported: getSupportedLocales() });
});

app.get('/api/search', requireAuth, async (req, res) => {
  const query = req.query.q;
  if (!query) return res.json({ error: 'No query provided' });
  console.log(`[Search] "${query}"`);
  try {
    const { runSearch } = await import('./lib/search/engine.mjs');
    const result = await runSearch(query, currentData);
    res.json({ success: true, ...result });
  } catch (error) {
    console.error('[Search] Error:', error);
    res.json({ success: false, error: error.message });
  }
});

// ── Deep intelligence search — SSE streaming ──────────────────────────────────
// EventSource cannot set headers — accept token via query param for this endpoint only
// R-F1793 — issue a short-lived single-use SSE ticket. EventSource cannot send
// an Authorization header, so an authenticated client (header) calls this and
// passes the returned ticket as ?ticket= to /api/search/deep, instead of the
// long-lived JWT (which leaked to logs/history/Referer — aria-web audit #9).
app.post('/api/sse/ticket', requireAuth, (req, res) => {
  res.json({ ticket: issueSseTicket(req.user), expiresInMs: 60000 });
});

app.get('/api/search/deep', async (req, res) => {
  // R-F1793 — Authorization header (preferred) OR a short-lived single-use SSE
  // ticket via ?ticket=. The long-lived JWT is NO LONGER accepted in the query
  // string (credential leak to access logs / browser history / Referer).
  const header = req.headers.authorization?.replace('Bearer ', '');
  if (header) {
    try { req.user = verifyToken(header); }
    catch { return res.status(401).json({ error: 'Invalid token' }); }
  } else {
    const payload = redeemSseTicket(req.query.ticket);
    if (!payload) return res.status(401).json({ error: 'Authentication required (SSE ticket invalid or expired)' });
    req.user = payload;
  }
  // Defense-in-depth: never emit a Referer carrying any query string from this page.
  res.setHeader('Referrer-Policy', 'no-referrer');

  const query = req.query.q?.trim();
  if (!query || query.length < 2) return res.status(400).json({ error: 'Query required' });

  // SSE headers
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('Connection', 'keep-alive');
  res.setHeader('X-Accel-Buffering', 'no');
  res.flushHeaders();

  const send = (data) => {
    try { res.write(`data: ${JSON.stringify(data)}\n\n`); } catch {}
  };

  console.log(`[DeepSearch] "${query}" by ${req.user?.email || 'unknown'}`);
  try {
    const { runDeepSearch } = await import('./lib/search/deep-engine.mjs');
    const result = await runDeepSearch(query, {
      cachedData:  currentData,
      llmProvider,
      onEvent:     send,
    });
    send({ type: 'result', data: result });
  } catch (err) {
    console.error('[DeepSearch] Error:', err.message);
    errorTracker.record('deep_search', 'handler_error', err); // R-F2605
    send({ type: 'error', message: err.message });
  } finally {
    res.end();
  }
});

// ── Power entity search ────────────────────────────────────────────────────────
app.get('/api/search/entity', requireAuth, async (req, res) => {
  const query = req.query.q;
  if (!query || query.trim().length < 2) return res.status(400).json({ error: 'Query required' });
  console.log(`[EntitySearch] "${query}"`);
  try {
    const { runEntitySearch } = await import('./lib/search/engine.mjs');
    const result = await runEntitySearch(query.trim(), currentData, llmProvider);
    res.json({ success: true, ...result });
  } catch (error) {
    console.error('[EntitySearch] Error:', error);
    errorTracker.record('entity_search', 'handler_error', error); // R-F2605
    res.status(500).json({ success: false, error: error.message });
  }
});

app.post('/api/sweep', requireAuth, async (req, res) => {
  try {
    if (sweepInProgress) return res.json({ success: false, message: 'Sweep already in progress' });
    runSweepCycle().catch(err => console.error('[Crucix] Manual sweep failed:', err.message));
    res.json({ success: true, message: 'Sweep triggered' });
  } catch (error) {
    res.json({ success: false, error: error.message });
  }
});

// ── Self-Learning API ─────────────────────────────────────────────────────────

app.get('/api/learning/stats', requireAuth, (req, res) => {
  res.json(getLearningStats());
});

app.get('/api/learning/outcomes', requireAuth, (req, res) => {
  const limit = parseInt(req.query.limit) || 50;
  res.json(getOutcomes(limit));
});

app.post('/api/learning/outcome', requireAuth, (req, res) => {
  const { hash, text, outcome, source, region, tier } = req.body || {};
  if (!hash || !outcome) return res.status(400).json({ error: 'hash and outcome required' });
  if (!['confirmed', 'dismissed', 'pending'].includes(outcome)) {
    return res.status(400).json({ error: 'outcome must be confirmed|dismissed|pending' });
  }
  const entry = recordAlertOutcome(hash, text || '', outcome, { source, region, tier });
  res.json({ success: true, entry });
});

app.get('/api/opportunities', requireAuth, async (req, res) => {
  // R-F1869 (audit DD-13): without try/catch a throw in detectOpportunities
  // became an unhandledRejection — the response never sent and the request
  // hung, accumulating dead connections. Always send a response.
  try {
    if (currentData) {
      const fresh = await detectOpportunities(currentData);
      return res.json({ opportunities: fresh, source: 'live', asOf: lastSweepTime });
    }
    const stored = getOpportunities();
    res.json({ ...stored, source: 'cached' });
  } catch (e) {
    console.error('[opportunities] detect failed:', e?.message);
    res.status(500).json({ error: 'opportunity detection failed', opportunities: [] });
  }
});

// R-F914 — merge the brain's intel-derived leads into the BD page. The Node
// OSINT sweep only yields HOT/WARM *tenders*; operator-evidenced 2026-05-26 the
// page showed 0 sales leads while the brain held real market intelligence
// (Angola/Kenya/Rwanda windows, 48k ledger signals) that was never surfaced —
// the two stores diverged. aria-intel's /proactive/lead-hunt?structured=1
// returns scored lead cards (cached 6h on the brain so this is cheap), mapped
// here into the page's brain.salesLeads shape. Best-effort: the BD page still
// renders the sweep leads if the brain is unreachable.
async function _mergeBrainLeads(bd) {
  const ARIA_URL = process.env.ARIA_SERVICE_URL || '';
  const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
  if (!ARIA_URL || !token) return bd;
  const r = await fetch(`${ARIA_URL}/api/aria/proactive/lead-hunt?structured=1`, {
    method: 'POST',
    headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
    body: '{}',
    signal: AbortSignal.timeout(10000),
  });
  if (!r.ok) return bd;
  const data = await r.json();
  const structured = Array.isArray(data.structured) ? data.structured : [];
  if (!structured.length) return bd;
  // R-F2011 (completes ARIA's R-F2009): the brain lead-hunt no longer invents
  // buyers, win-odds, timelines, OEM angles, compliance flags or next-actions —
  // it returns only what the live signals actually say. Map the honest shape
  // {market, signal_summary, signal_count} and DROP the old invented fields so
  // the web tier never re-introduces fabrication. (Frontend already renders
  // signalCount instead of HOT/WARM and shows no fabricated action items.)
  const mapped = structured.map(l => ({
    lead:          l.market || '',
    market:        l.market || '',
    urgency:       'SIGNAL',
    type:          'INTEL',
    signalSummary: l.signal_summary || '',
    signalCount:   l.signal_count || 0,
    source:        'brain_lead_hunt',
  }));
  const out = { ...bd };
  out.brain = { ...(out.brain || {}) };
  const existing = Array.isArray(out.brain.salesLeads) ? out.brain.salesLeads : [];
  const seen = new Set(existing.map(l => (l.market || '') + '|' + (l.lead || '')));
  const merged = existing.slice();
  for (const l of mapped) {
    const k = (l.market || '') + '|' + (l.lead || '');
    if (!seen.has(k)) { merged.push(l); seen.add(k); }
  }
  out.brain.salesLeads = merged;
  return out;
}

app.get('/api/bd-intelligence', requireAdmin, async (req, res) => {
  let bd = (currentData?.bdIntelligence) || getBDIntelligence()
    || { tenders: [], ideas: [], strategy: null, pipeline: [], counts: { activeTenders: 0, contractAwards: 0, strategicIdeas: 0, pipelineDeals: 0 } };
  try { bd = await _mergeBrainLeads(bd); }
  catch (e) { console.warn('[BD] R-F914 brain-lead merge skipped:', e.message); }
  res.json(bd);
});

app.get('/api/bd-intelligence/pipeline', requireAdmin, (req, res) => {
  res.json(getDealPipeline());
});

app.post('/api/bd-intelligence/pipeline/:id/stage', requireAdmin, (req, res) => {
  const { id } = req.params;
  const { stage, notes } = req.body || {};
  if (!stage) return res.status(400).json({ error: 'stage required' });
  const result = updateDealStage(id, stage, notes || '');
  res.json(result);
});

app.post('/api/bd-intelligence/pipeline/:id/outcome', requireAdmin, (req, res) => {
  const { id } = req.params;
  const { market, type, outcome, reason } = req.body || {};
  if (!outcome || !['WON', 'LOST', 'NO_BID'].includes(outcome)) {
    return res.status(400).json({ error: 'outcome must be WON, LOST, or NO_BID' });
  }
  try {
    recordOutcome(id, market || 'Unknown', type || 'TENDER', outcome, reason || '');
    res.json({ ok: true, dealId: id, outcome });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/bd-intelligence/feedback', requireAdmin, (req, res) => {
  // Thumbs up/down on brain leads or tenders
  const { signalText, market, feedback, reason } = req.body || {};
  if (!feedback || !['positive', 'negative'].includes(feedback)) {
    return res.status(400).json({ error: 'feedback must be positive or negative' });
  }
  try {
    const outcome = feedback === 'positive' ? 'confirmed' : 'dismissed';
    // Reuse alert outcome recording to feed source weighting
    const hash = Buffer.from((signalText || '').slice(0, 80)).toString('base64').slice(0, 16);
    recordAlertOutcome(hash, signalText || '', outcome, { source: market, region: market, tier: 'bd' });
    if (market && feedback === 'positive') {
      recordOutcome(hash, market, 'LEAD', 'WON', reason || 'user confirmed lead');
    } else if (market && feedback === 'negative') {
      recordOutcome(hash, market, 'LEAD', 'LOST', reason || 'user dismissed lead');
    }
    res.json({ ok: true });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Compliance pre-screening ──────────────────────────────────────────────────
app.post('/api/compliance/screen', requireAuth, (req, res) => {
  const { sellerCountry, buyerCountry, productCategory, dealValueUSD, notes } = req.body || {};
  if (!sellerCountry || !buyerCountry || !productCategory) {
    return res.status(400).json({ error: 'sellerCountry, buyerCountry, productCategory required' });
  }
  try {
    const result = screenDeal({ sellerCountry, buyerCountry, productCategory, dealValueUSD, notes, brokerCountry: 'GB' });
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/compliance/products', requireAuth, (req, res) => {
  res.json(getProductCategories());
});

// ── Shareable brief ───────────────────────────────────────────────────────────
// R-F384: file-based share-token store (was Upstash via redisGet/redisSet).
// Expired tokens are pruned opportunistically on every write so the file
// never grows unbounded across the 7-day TTL window.
const _shareStore = new PersistStore(
  'crucix:share_tokens',
  join(process.cwd(), 'data', 'share_tokens.json'),
  () => ({}),
);
await _shareStore.init();

function _shareSet(token, payload) {
  const all = _shareStore.read() || {};
  const now = Date.now();
  for (const t of Object.keys(all)) {
    if (!all[t]?.expiresAt || all[t].expiresAt < now) delete all[t];
  }
  all[token] = payload;
  _shareStore.write(all);
}

function _shareGet(token) {
  const all = _shareStore.read() || {};
  // R-F3838 — own-property lookup only. The route's length bound already keeps
  // `__proto__`/`constructor` out, but a store lookup should not depend on a
  // regex somewhere else staying strict.
  if (!Object.prototype.hasOwnProperty.call(all, token)) return null;
  const entry = all[token];
  if (!entry || !entry.expiresAt || Date.now() > entry.expiresAt) return null;
  return entry;
}

app.post('/api/share/brief', requireAdmin, async (req, res) => {
  const bd = getBDIntelligence();
  if (!bd) return res.status(503).json({ error: 'No BD data available — run a sweep first' });

  // R-F2094 (2026-06-28 DD): crypto-strong token. Math.random()'s V8 PRNG state is
  // recoverable, so 7-day public BD-intel links were theoretically predictable.
  const token    = randomBytes(24).toString('base64url');
  const expiresAt = Date.now() + 7 * 24 * 60 * 60 * 1000; // 7 days
  const payload  = { bd, createdAt: new Date().toISOString(), expiresAt };

  _shareSet(token, payload);

  const host = req.get('host');
  const proto = req.headers['x-forwarded-proto'] || 'https';
  res.json({ token, url: `${proto}://${host}/s/${token}`, expiresAt: new Date(expiresAt).toISOString() });
});

app.get('/s/:token', async (req, res) => {
  const { token } = req.params;
  // R-F3838 — the guard was /^[a-z0-9]{20,30}$/ while _shareSet mints
  // randomBytes(24).toString('base64url'): 32 chars of [A-Za-z0-9_-]. Too long,
  // wrong case, wrong charset — so EVERY share link 400'd and the feature had
  // never once worked. Now matched to what is actually minted, with the length
  // bound kept loose enough to survive a token-size change but tight enough that
  // no prototype key ('__proto__', 'constructor') can reach the store lookup.
  if (!/^[A-Za-z0-9_-]{20,64}$/.test(token)) return res.status(400).send('Invalid token');

  const payload = _shareGet(token);
  if (!payload) return res.status(404).send('<h2>Brief not found or expired</h2>');

  const { bd } = payload;
  const hot  = (bd.brain?.salesLeads || []).filter(l => l.urgency === 'HOT');
  const warm = (bd.brain?.salesLeads || []).filter(l => l.urgency === 'WARM');
  const tenders = (bd.tenders || []).filter(t => t.leadQuality === 'HOT' || t.leadQuality === 'WARM');
  const strat = bd.strategy;

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Arkmurus BD Intelligence Brief</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #f5f6fa; color: #1a2332; line-height: 1.6; }
  .header { background: #1a2332; color: #fff; padding: 28px 40px; }
  .header h1 { font-size: 1.5rem; font-weight: 700; letter-spacing: -0.3px; }
  .header .sub { font-size: 0.85rem; color: #90a4ae; margin-top: 4px; }
  .container { max-width: 900px; margin: 0 auto; padding: 32px 24px; }
  .section { margin-bottom: 28px; }
  .section-title { font-size: 0.7rem; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #78909c; margin-bottom: 12px; }
  .card { background: #fff; border-radius: 8px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); padding: 16px 20px; margin-bottom: 10px; border-left: 4px solid #ccc; }
  .card.hot { border-left-color: #e53935; }
  .card.warm { border-left-color: #ff9800; }
  .card.strategy { border-left-color: #7b1fa2; }
  .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.68rem; font-weight: 700; color: #fff; margin-right: 6px; }
  .hot-badge { background: #e53935; }
  .warm-badge { background: #ff9800; }
  .label { font-size: 0.72rem; color: #78909c; }
  .val { font-size: 0.85rem; color: #1a2332; margin-left: 6px; }
  .title { font-size: 0.95rem; font-weight: 600; color: #1a2332; margin: 6px 0; }
  .next-step { background: #f0faf4; border-left: 3px solid #4caf50; padding: 8px 12px; border-radius: 0 4px 4px 0; font-size: 0.82rem; color: #2e7d32; margin-top: 8px; }
  .meta { font-size: 0.75rem; color: #90a4ae; margin-top: 6px; }
  a { color: #1976d2; }
  .footer { text-align: center; font-size: 0.72rem; color: #90a4ae; padding: 20px; border-top: 1px solid #e0e0e0; margin-top: 32px; }
  .disclaimer { background: #fff8e1; border: 1px solid #ffe082; border-radius: 6px; padding: 10px 14px; font-size: 0.78rem; color: #5d4037; margin-top: 24px; }
</style>
</head>
<body>
<div class="header">
  <h1>Arkmurus BD Intelligence Brief</h1>
  <div class="sub">Generated ${new Date(payload.createdAt).toUTCString()} &nbsp;·&nbsp; Valid 7 days</div>
</div>
<div class="container">

${hot.length > 0 ? `
<div class="section">
  <div class="section-title">🔥 HOT Sales Leads — Act Now</div>
  ${hot.map(l => `
  <div class="card hot">
    <span class="badge hot-badge">HOT</span>
    <strong>${escHtml(l.market)}</strong>
    ${l.estimatedValue ? `<span style="float:right;font-weight:700;color:#e53935">${escHtml(l.estimatedValue)}</span>` : ''}
    <div class="title">${escHtml(l.lead)}</div>
    ${l.procurementAuthority ? `<div><span class="label">Authority:</span><span class="val">${escHtml(l.procurementAuthority)}</span></div>` : ''}
    ${l.oemRecommendation ? `<div><span class="label">OEM:</span><span class="val">${escHtml(l.oemRecommendation)}</span></div>` : ''}
    ${l.nextStep ? `<div class="next-step"><strong>Next 48h:</strong> ${escHtml(l.nextStep)}</div>` : ''}
    ${safeExternalUrl(l.portalUrl) ? `<div class="meta"><a href="${escHtml(safeExternalUrl(l.portalUrl))}" target="_blank" rel="noopener noreferrer">Procurement Portal →</a></div>` : ''}
  </div>`).join('')}
</div>` : ''}

${warm.length > 0 ? `
<div class="section">
  <div class="section-title">⚡ WARM Leads — Qualify This Week</div>
  ${warm.map(l => `
  <div class="card warm">
    <span class="badge warm-badge">WARM</span>
    <strong>${escHtml(l.market)}</strong>
    ${l.estimatedValue ? `<span style="float:right;color:#ff9800;font-weight:600">${escHtml(l.estimatedValue)}</span>` : ''}
    <div class="title">${escHtml(l.lead)}</div>
    ${l.oemRecommendation ? `<div><span class="label">OEM:</span><span class="val">${escHtml(l.oemRecommendation)}</span></div>` : ''}
    ${l.nextStep ? `<div class="meta">→ ${escHtml(l.nextStep)}</div>` : ''}
  </div>`).join('')}
</div>` : ''}

${tenders.length > 0 ? `
<div class="section">
  <div class="section-title">Verified Tenders & Contracts</div>
  ${tenders.map(t => `
  <div class="card ${t.leadQuality === 'HOT' ? 'hot' : 'warm'}">
    <span class="badge ${t.leadQuality === 'HOT' ? 'hot-badge' : 'warm-badge'}">${escHtml(t.leadQuality)}</span>
    <span class="badge" style="background:#546e7a">${escHtml(t.type)}</span>
    <strong>${escHtml(t.market)}</strong>
    ${t.winProbability != null ? `<span style="float:right;font-weight:700;font-size:0.8rem">Win ${t.winProbability}%</span>` : ''}
    <div class="title">${escHtml(t.title)}</div>
    <div class="meta">${escHtml(t.source)} · ${escHtml(t.date || '')}
    ${safeExternalUrl(t.url) ? ` · <a href="${escHtml(safeExternalUrl(t.url))}" target="_blank" rel="noopener noreferrer">View Tender →</a>` : ''}</div>
  </div>`).join('')}
</div>` : ''}

${strat?.topPriority ? `
<div class="section">
  <div class="section-title">AI Strategic Priority</div>
  <div class="card strategy">
    <div class="title">${escHtml(strat.topPriority.action || strat.topPriority.description || '')}</div>
    ${strat.topPriority.whyNow ? `<div class="meta">Why now: ${escHtml(strat.topPriority.whyNow)}</div>` : ''}
    ${strat.topPriority.firstStep ? `<div class="next-step">${escHtml(strat.topPriority.firstStep)}</div>` : ''}
  </div>
</div>` : ''}

<div class="disclaimer">
  This brief is confidential and intended for the named recipient only. Intelligence is AI-generated from open sources
  and must be independently verified before commercial decisions. All export activity is subject to applicable licensing
  and regulatory requirements.
</div>
</div>
<div class="footer">Powered by Arkmurus Crucix Intelligence Platform &nbsp;·&nbsp; arkmurus.com</div>
</body>
</html>`;

  res.setHeader('Content-Type', 'text/html; charset=utf-8');
  res.setHeader('X-Robots-Tag', 'noindex, nofollow');
  res.send(html);
});

function escHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

app.get('/api/patterns', requireAuth, (req, res) => {
  res.json(getPatterns());
});

app.get('/api/explorer', requireAuth, (req, res) => {
  res.json(getExplorerFindings());
});

app.post('/api/explorer/run', requireAuth, async (req, res) => {
  try {
    const findings = await runExploration(llmProvider, req.body || {});
    res.json({ success: true, ...findings });
  } catch (err) {
    res.json({ success: false, error: err.message });
  }
});

// ── Brain ML endpoints — proxy to Python aria_service (R-F382) ──
//
// Pre-R-F382: these read from Upstash Redis keys
// (crucix:brain:generated_leads / bd_brief:latest / last_run / run_history)
// under the assumption that the Python brain wrote there. In practice
// the keys were NEVER written — grep confirms only these 4 readers
// reference them. The endpoints have been silently returning empty
// responses for the entire lifetime of the seenode service.
//
// Post-R-F382: stop reading dead Upstash keys. Endpoints preserve their
// HTTP contract (same response shape, same status codes) so existing
// callers (waListener / telegramCommands / ariaWhatsApp / frontend)
// continue to work. They now return their empty default directly,
// without an Upstash round-trip.
//
// The real brain data (leads, briefs, run history) lives in the Python
// aria_service via SQLite — accessed through /api/aria/* proxy.

// R-F976 (2026-05-28): these four GETs were dead since the Upstash removal
// (R-F382) — they returned falsey-OK bodies ([], {last_run:null}) that a
// caller cannot distinguish from "the brain genuinely has no data". The real
// data lives in the Python brain under /api/aria/* (leads via proactive
// lead-hunt, sources via brain/stats). Return an honest 410 Gone with a
// pointer so a stale emptiness is never mistaken for live intelligence. The
// only consumer is the unmounted admin Angular bundle (frontend/dist, not
// served); live WA/Telegram callers hit the brain directly and .catch().
const _brainStubGone = (movedTo) => (req, res) =>
  res.status(410).json({
    error: 'endpoint_removed',
    deprecated: true,
    moved_to: movedTo,
    note: 'Node /api/brain/* is dead since the Upstash removal (R-F382). Real brain data lives in the Python service under /api/aria/*.',
  });

app.get('/api/brain/leads', requireAuth, _brainStubGone('/api/aria/proactive/lead-hunt?structured=1'));
app.get('/api/brain/brief', requireAuth, _brainStubGone('/api/aria/brain/stats'));
app.get('/api/brain/status', requireAuth, _brainStubGone('/api/aria/health/perf'));
app.get('/api/brain/history', requireAuth, _brainStubGone('/api/aria/brain/stats'));

// ── Brain API bridge — WhatsApp/Zoom call /api/brain/* routes ────────────────
// These map to existing local functions so integrations work without the Python brain

app.post('/api/brain/signal', requireAuth, async (req, res) => {
  // Accept signals from WhatsApp/Zoom/Email — store as intelligence.
  // requireAuth was missing on the original definition, leaving the endpoint
  // open for anyone to push arbitrary signals into the brain (or use it as
  // an unauthenticated relay to BRAIN_URL). Closed 2026-04-09.
  try {
    const { content, source, signal_type, trigger, market, metadata } = req.body || {};
    if (!content) return res.status(400).json({ error: 'content required' });
    // R-F900 — forward to the brain's REAL signal endpoint. Pre-R-F900 this hit
    // `/api/brain/signal` (404 — no such router; it's `/api/aria/brain/signal`
    // per R-F887) with NO auth header (brain → 401), keyed off BRAIN_URL
    // (=BRAIN_SERVICE_URL, often unset on aria-web). All three failures fell
    // through to a FALSE `{status:"queued"}` the caller trusted. Use the same
    // base URL + token the working /api/aria proxy uses, the correct path, and
    // return an HONEST error instead of a fake ack.
    const brainBase = process.env.ARIA_SERVICE_URL || BRAIN_URL || '';
    const brainTok = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    if (brainBase) {
      try {
        const r = await fetch(`${brainBase}/api/aria/brain/signal`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            ...(brainTok ? { 'Authorization': `Bearer ${brainTok}` } : {}),
          },
          body: JSON.stringify(req.body), signal: AbortSignal.timeout(5000),
        });
        if (r.ok) return res.json(await r.json());
        return res.status(502).json({ error: `brain signal forward failed: HTTP ${r.status}`, source });
      } catch (e) {
        return res.status(502).json({ error: `brain signal unreachable: ${e.message}`, source });
      }
    }
    return res.status(503).json({ error: 'no brain base URL configured (set ARIA_SERVICE_URL)', source });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/brain/sweep', requireAuth, (req, res) => {
  // R-F976 (2026-05-28): this never started a sweep. The BRAIN_URL forward hit
  // `/api/brain/sweep` — a route the Python brain does not serve (it uses
  // /api/aria/*) — then fell through to a FALSE `{status:'sweep_started'}` ack
  // while triggering nothing. The OSINT sweep is owned by THIS (Node) tier; the
  // real, working trigger is POST /api/sweep. Return an honest pointer.
  res.status(410).json({
    error: 'endpoint_removed',
    deprecated: true,
    moved_to: 'POST /api/sweep',
    note: 'This route never triggered a sweep. Use POST /api/sweep — the real sweep trigger owned by this tier.',
  });
});

app.post('/api/brain/counterparty-risk', requireAuth, async (req, res) => {
  const { entity_name } = req.body || {};
  if (!entity_name) return res.status(400).json({ error: 'entity_name required' });
  try {
    // Forward to Python brain first
    if (BRAIN_URL) {
      try {
        const r = await fetch(`${BRAIN_URL}/api/brain/counterparty-risk`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(req.body), signal: AbortSignal.timeout(15000),
        });
        if (r.ok) return res.json(await r.json());
      } catch {}
    }
    // Fallback: use local compliance screening
    const result = await screenEntity(entity_name, redisAdapter);
    res.json(result);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.get('/api/brain/pipeline/summary', requireAdmin, (req, res) => {
  const pipeline = getDealPipeline();
  const open = pipeline.filter(d => !['WON','LOST','NO_BID'].includes(d.stage));
  const won  = pipeline.filter(d => d.stage === 'WON');
  const lost = pipeline.filter(d => d.stage === 'LOST' || d.stage === 'NO_BID');
  const totalValue = open.reduce((s, d) => s + (d.value || 0), 0);
  const winRate = (won.length + lost.length) > 0 ? won.length / (won.length + lost.length) : 0;
  const stale = open.filter(d => {
    const days = (Date.now() - new Date(d.updatedAt || d.detectedAt).getTime()) / 86400000;
    return days > 14;
  }).map(d => ({ id: d.id, market: d.market, days_stale: Math.round((Date.now() - new Date(d.updatedAt || d.detectedAt).getTime()) / 86400000) }));
  res.json({
    open_deals: open.length, won_deals: won.length, lost_deals: lost.length,
    total_pipeline_value: totalValue, win_rate: winRate,
    stale_alerts: stale.slice(0, 5),
    top_deals: open.slice(0, 6).map(d => ({ id: d.id, market: d.market, stage: d.stage, opportunity: d.title || d.sourceTitle })),
  });
});

app.get('/api/brain/pipeline/deal/:id', requireAdmin, (req, res) => {
  const pipeline = getDealPipeline();
  const deal = pipeline.find(d => d.id === req.params.id);
  if (!deal) return res.status(404).json({ error: 'Deal not found' });
  const daysInStage = Math.round((Date.now() - new Date(deal.updatedAt || deal.detectedAt).getTime()) / 86400000);
  res.json({ ...deal, days_in_stage: daysInStage, stale: daysInStage > 14, opportunity: deal.title || deal.sourceTitle, pipeline_value: deal.value || 0, win_probability: (deal.score || 50) / 100 });
});

app.post('/api/brain/pipeline/create', requireAdmin, (req, res) => {
  const { market, opportunity, note } = req.body || {};
  if (!market || !opportunity) return res.status(400).json({ error: 'market and opportunity required' });
  const result = createDeal(market, opportunity);
  if (note && result.deal) result.deal.notes = [{ ts: new Date().toISOString(), note }];
  res.json(result);
});

app.post('/api/brain/pipeline/advance', requireAdmin, (req, res) => {
  const { deal_id, stage } = req.body || {};
  if (!deal_id || !stage) return res.status(400).json({ error: 'deal_id and stage required' });
  res.json(updateDealStage(deal_id, stage));
});

app.get('/api/brain/oem/search', requireAuth, async (req, res) => {
  try {
    const { searchOEMs } = await import('./lib/intel/oem_db.mjs');
    const { capability, destination, limit } = req.query;
    const query = [capability, destination].filter(Boolean).join(' ');
    let results = searchOEMs(query);
    if (limit) results = results.slice(0, parseInt(limit) || 10);
    res.json({ results, count: results.length });
  } catch (e) { res.json({ results: [], count: 0 }); }
});

app.get('/api/brain/humint/contacts', requireAuth, async (req, res) => {
  try {
    const { getContactsByCountry, searchContacts } = await import('./lib/aria/contacts.mjs');
    const { market } = req.query;
    const contacts = market ? getContactsByCountry(market) : searchContacts('');
    res.json({ contacts: contacts.slice(0, 10) });
  } catch (e) { res.json({ contacts: [] }); }
});

app.get('/api/brain/humint/windows', requireAuth, async (req, res) => {
  try {
    const { getAllContacts } = await import('./lib/aria/contacts.mjs');
    const all = getAllContacts();
    // Contacts with relationship windows: recently appointed (< 180 days in role)
    const windows = all.filter(c => c.appointed_date).map(c => {
      const daysInRole = Math.round((Date.now() - new Date(c.appointed_date).getTime()) / 86400000);
      const daysRemaining = Math.max(0, 180 - daysInRole);
      return { ...c, full_name: c.name, days_in_role: daysInRole, days_remaining: daysRemaining, relationship_window_active: daysRemaining > 0,
        urgency: daysRemaining < 30 ? 'CRITICAL' : daysRemaining < 90 ? 'HIGH' : 'MEDIUM' };
    }).filter(c => c.relationship_window_active).sort((a, b) => a.days_remaining - b.days_remaining);
    res.json({ windows });
  } catch (e) { res.json({ windows: [] }); }
});

app.get('/api/brain/approach/quick', requireAuth, async (req, res) => {
  try {
    const { generateApproach } = await import('./lib/aria/approach.mjs');
    const { market, capability, urgency } = req.query;
    if (!market) return res.status(400).json({ error: 'market required' });
    const strategy = generateApproach(market, capability || '', urgency || '');
    res.json(strategy);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// R-F384: conferences calendar + brief endpoints — Upstash reads removed.
// These keys were never written by any code path (grep confirms zero writers),
// so the redisGet was always returning null and the endpoints fell through to
// these defaults. Drop the dead remote read; behavior unchanged.
app.get('/api/brain/conference/calendar', requireAuth, (req, res) => {
  res.json({ upcoming: [] });
});

app.get('/api/brain/conference/brief', requireAuth, (req, res) => {
  const { name } = req.query;
  if (!name) return res.status(400).json({ error: 'name required' });
  res.json({ name, dates: 'TBC', location: 'TBC', arkmurus_objectives: [], must_meet: [] });
});

// ── ARIA endpoints — proxy to Python aria_service ──
// R-F382: upstashLRange() helper removed (was used only by the
// /api/aria/thoughts + /api/aria/curiosity fallbacks, which themselves
// were removed in this commit). Real source for thoughts/curiosity is
// the Python aria_service via the ariaProxy chain.

const BRAIN_URL = process.env.BRAIN_SERVICE_URL; // e.g. https://crucix-brain.onrender.com
const ARIA_SERVICE_URL = process.env.ARIA_SERVICE_URL || ''; // Python ARIA service, e.g. http://localhost:8000

// ── ARIA Proxy Helper — routes to Python service first, falls back to local Node.js ──
// Build the headers used for every fly.io ARIA call. Adds the bearer token
// when ARIA_API_TOKEN is set in env (matches the soft-rollout pattern on
// the Python side: token unset = no auth, token set = enforced both ends).
function _ariaHeaders(extra = {}) {
  const headers = { 'Content-Type': 'application/json', ...extra };
  // R-F456 (2026-05-14): fly's _accepted_tokens accepts EITHER
  // ARIA_API_TOKEN OR ARIA_INTERNAL_TOKEN (see aria_service/routes/aria.py
  // line 165). seenode previously only forwarded ARIA_API_TOKEN, so any
  // deploy that set ARIA_INTERNAL_TOKEN alone produced a silent 401 cascade
  // across the non-public-bypass endpoints (/rlaif/stats, /critique/stats,
  // /pending-actions, /security/counter-intel/scan,
  // /learning/coverage, /sanctions/divergence, /vendors). Public-bypass
  // endpoints (/health, /adversarial/stats, etc.) kept working — which
  // hid the cause for weeks. Fall through to the internal token when the
  // API token isn't set.
  const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN;
  if (token) headers['Authorization'] = `Bearer ${token}`;
  return headers;
}

// ── R-F2620 — inbound marketing leads from the public landing form ───────────
// The landing form (index.html) used to drop every sign-up on the floor. These
// two routes give it a real destination: POST /api/leads is PUBLIC (rate-limited
// like every other route) and forwards the lead to the aria-intel brain with the
// service token; GET /api/leads is admin-only (leads are PII) and lists them for
// the operator. The viewing surface is public/leads.html.
// R-F3531 — every field the assessment GRADES must survive every hop. This
// proxy previously rebuilt the body from three fields, so organisation,
// jurisdiction and role were dropped here even when the form sent them: the
// brain read `body.get("company")` from a payload that could never contain it,
// and every lead was stuck at 1/4 facts forever. Keep this list in step with
// relationship_intelligence.INTAKE_FIELDS — a test asserts it.
app.post('/api/leads', async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ ok: false, error: 'Lead capture is temporarily unavailable.' });
    const body = req.body || {};
    const name = String(body.name || '').trim().slice(0, 200);
    const email = String(body.email || '').trim().slice(0, 200);
    const useCase = String(body.use_case || body.useCase || '').trim().slice(0, 120);
    const company = String(body.company || '').trim().slice(0, 200);
    const country = String(body.country || '').trim().slice(0, 100);
    const role = String(body.role || '').trim().slice(0, 160);
    if (!name || !email.includes('@')) {
      return res.status(400).json({ ok: false, error: 'A name and a valid email are required.' });
    }
    // R-F3999 (C-80) — bot and mail-abuse bounds. This route is unauthenticated by
    // necessity (a prospect has no account) and mails a caller-chosen address, so
    // it was the one anonymous outbound-mail path in the app.
    //
    // Both refusals return the SAME 200 shape as a success. Telling a bot it was
    // detected teaches it what to change, and a distinct response for the
    // destination bound would let an attacker probe which addresses have already
    // been targeted. Nothing is recorded and nothing is sent; the caller cannot
    // tell the difference. The honeypot costs a real user nothing — they never
    // see the field.
    const _hpTripped = leadHoneypotTripped(body);
    const _destBounded = _hpTripped ? false : leadDestinationBlocked(email);
    if (_hpTripped || _destBounded) {
      // R-F4018 (C-93) — the drop is silent TO THE CALLER, never to us.
      //
      // As first written this branch returned and emitted nothing, which made it a
      // dark path (§21a): a discarded submission left no trace anywhere, so if the
      // honeypot ever caught a REAL prospect — a browser autofilling the decoy, a
      // legitimate user retrying a fourth time — nobody could have known. An
      // anti-abuse control that cannot be audited is indistinguishable from a bug
      // that eats leads.
      //
      // The two reasons are recorded SEPARATELY because they mean different
      // things: `honeypot` should be almost entirely bots and a rising count is a
      // signal the decoy is catching humans, while `destination_bounded` is
      // expected to be non-zero and merely bounds one address. Collapsing them
      // would hide exactly the case worth watching.
      try {
        errorTracker.record('leads', _hpTripped ? 'honeypot_drop' : 'destination_bounded',
          new Error(_hpTripped ? 'lead decoy field was filled' : 'lead destination rate bounded'));
      } catch { /* observability must never break the request */ }
      // R-F4018 (C-93) — MIRROR what a genuine request would have answered.
      //
      // This returned a hardcoded 'sent', which was an ORACLE: the genuine path
      // reports what the mail step actually decided, so on any deployment where
      // SMTP is unconfigured every real submission answers 'not_sent' while every
      // dropped one answered 'sent'. Detecting the honeypot would have been one
      // request. Caught by driving the two paths against each other end to end
      // rather than by reading the code, which had looked identical.
      //
      // The mirrored value needs no work done: for a new lead the genuine outcome
      // is decided entirely by whether mail is configured (_mailLeadVerification
      // returns 'not_sent' when it is not, and 'sent' on a successful send).
      return res.status(200).json({
        ok: true,
        verification: smtpIsConfigured ? 'sent' : 'not_sent',
      });
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12000);
    let r, data;
    try {
      r = await fetch(`${ARIA_SERVICE_URL}/api/aria/leads/inbound`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({ name, email, use_case: useCase, company, country, role, source: 'landing' }),
        signal: ctrl.signal,
      });
      data = await r.json().catch(() => ({}));
    } finally {
      clearTimeout(timer);
    }
    // Relay the brain's honest verdict — do NOT fake a success on failure (§22).
    // Kept in the exact `if (!r.ok) return res.status(r.status || 502)` shape the
    // R-F2581 status-preservation contract recognises: this route now has work to
    // do on the success path (sending the confirmation), so it cannot use the
    // single-expression ternary form, and inventing a third spelling would fail a
    // guard that is right to be strict about status masking.
    if (!r.ok) return res.status(r.status || 502).json({ ok: false, error: data?.error || 'Could not record your details right now.' });
    // The plaintext challenge exists only in this variable, for one send. It is
    // never logged and never returned to the browser — the reply says whether a
    // mail went out, which is all the visitor needs and all they may know.
    const verification = await _mailLeadVerification(req, {
      leadId: data?.lead_id, name, email, challenge: data?.verification,
      alreadyVerified: !!data?.already_verified,
    });
    return res.status(200).json({ ok: true, verification });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Could not reach the lead service. Please try again shortly.' });
  }
});

// Send one ownership-confirmation link. Returns the honest outcome string that
// the landing page and the operator surface both render: 'sent' | 'not_sent' |
// 'not_required'. Never throws — a mail failure must not lose a recorded lead,
// but it must never be reported as a send either (§22).
async function _mailLeadVerification(req, { leadId, name, email, challenge, alreadyVerified }) {
  if (alreadyVerified) return 'not_required';
  const token = challenge?.token;
  if (!leadId || !token) return 'not_required';
  if (!smtpIsConfigured) {
    console.warn('[leads] SMTP not configured — confirmation link NOT sent for a recorded access request');
    return 'not_sent';
  }
  try {
    const link = `${_portalBase(req)}/lead-verify.html?lead=${encodeURIComponent(leadId)}&token=${encodeURIComponent(token)}`;
    const expiresOn = challenge?.expires_at
      ? new Date(challenge.expires_at).toUTCString().replace(/ GMT$/, ' UTC')
      : '';
    const result = await sendLeadVerificationEmail({ to: email, recipientName: name, link, expiresOn });
    return result?.sent ? 'sent' : 'not_sent';
  } catch (e) {
    console.warn('[leads] confirmation send failed:', e?.message || e);
    return 'not_sent';
  }
}

// PUBLIC by necessity — the person confirming their address has no account.
// The single-use token IS the credential; the brain returns one generic failure
// for every rejection so this cannot be used to enumerate who applied.
app.post('/api/leads/verify', async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ ok: false, error: 'Verification is temporarily unavailable.' });
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/leads/inbound/verify`, {
      method: 'POST',
      headers: _ariaHeaders(),
      body: JSON.stringify({
        lead_id: String(req.body?.lead_id || '').slice(0, 64),
        token: String(req.body?.token || '').slice(0, 256),
      }),
      signal: AbortSignal.timeout(12000),
    });
    const data = await r.json().catch(() => ({}));
    return res.status(r.status).json(data);
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Could not reach the verification service. Please try again shortly.' });
  }
});

// Operator actions. `actor` is stamped from the authenticated JWT and the
// client-supplied value is discarded — an attestation must name the operator
// who actually made it, not whoever the browser claims.
app.patch('/api/leads/:leadId', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ ok: false, error: 'aria service unavailable' });
    const actor = req.user?.email || req.user?.userId || '';
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/leads/inbound/${encodeURIComponent(req.params.leadId)}`, {
      method: 'PATCH',
      headers: _ariaHeaders(),
      body: JSON.stringify({ ...(req.body || {}), actor }),
      signal: AbortSignal.timeout(12000),
    });
    const data = await r.json().catch(() => ({}));
    return res.status(r.status).json(data);
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Could not reach the lead service.' });
  }
});

app.post('/api/leads/:leadId/resend-verification', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ ok: false, error: 'aria service unavailable' });
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/leads/inbound/${encodeURIComponent(req.params.leadId)}/reverify`, {
      method: 'POST',
      headers: _ariaHeaders(),
      signal: AbortSignal.timeout(12000),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return res.status(r.status).json(data);
    const verification = await _mailLeadVerification(req, {
      leadId: data?.lead_id, name: data?.name, email: data?.email,
      challenge: data?.verification, alreadyVerified: false,
    });
    // Report the SEND, not the token issue. "Link reissued but not emailed" is a
    // different outcome from "sent" and the operator has to be able to see it.
    return res.status(200).json({ ok: true, verification });
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Could not reach the lead service.' });
  }
});

app.get('/api/leads', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'aria service unavailable' });
    const limit = Math.max(1, Math.min(parseInt(req.query.limit, 10) || 100, 500));
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/leads/inbound?limit=${limit}`, { headers: _ariaHeaders() });
    const data = await r.json().catch(() => ({}));
    return res.status(r.ok ? 200 : (r.status || 502)).json(data);
  } catch (e) {
    return res.status(502).json({ error: 'Could not reach the lead service.' });
  }
});

app.delete('/api/leads/:leadId', requireAdmin, (req, res) =>
  ariaProxy(
    req,
    res,
    '/api/aria/leads/inbound/' + encodeURIComponent(req.params.leadId),
    { method: 'DELETE' },
  ));

// R-F2670 — Design partners (Phase A gate #7): frictionless operator logging.
// Admin-only (real prospect contact details); proxies to the aria-intel tracker
// so /design-partners.html can list + add without a raw curl. GET lists +
// returns gate stats; POST logs one conversation.
// R-F3328 — fetch the tracker's records so a route can read the entry it is
// about to act on. The design-partner API addresses records BY INDEX, and the
// contact email must come from the stored record, never from the request body:
// the record is what the operator approved.
async function _fetchDesignPartners() {
  const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/admin/design-partners`, { headers: _ariaHeaders() });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    // Carry the upstream status so the caller can RELAY it (R-F2581 contract:
    // a 401/403 from the tracker must not reach the page as a generic 502).
    const err = new Error(`the design-partner service returned HTTP ${r.status}`);
    err.status = r.status;
    throw err;
  }
  return Array.isArray(data.entries) ? data.entries : [];
}

app.get('/api/design-partners', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'aria service unavailable' });
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/admin/design-partners`, { headers: _ariaHeaders() });
    const data = await r.json().catch(() => ({}));
    // R-F3328 — tell the page whether each approved partner can actually SIGN
    // IN. The tracker (aria-intel) has no idea accounts exist; without this the
    // UI can only show "engaged ✓", which is exactly the false-reassurance that
    // let an approved partner sit for hours with no login. has_account is read
    // from the real user store, so an empty account is visible as one.
    if (r.ok && Array.isArray(data.entries)) {
      data.entries = data.entries.map((e) => {
        let account = null;
        try {
          const u = e && e.contact ? findUserByEmail(String(e.contact)) : null;
          if (u) account = { status: u.status, tier: u.tier || DEFAULT_TIER, credentialIssuedAt: u.credentialIssuedAt || null };
        } catch { /* a lookup failure must not blank the list */ }
        return { ...e, has_account: !!account, account };
      });
    }
    return res.status(r.ok ? 200 : (r.status || 502)).json(data);
  } catch (e) {
    return res.status(502).json({ error: 'Could not reach the design-partner service.' });
  }
});
app.post('/api/design-partners', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'aria service unavailable' });
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/admin/design-partners`, {
      method: 'POST',
      headers: { ..._ariaHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(req.body || {}),
    });
    const data = await r.json().catch(() => ({}));
    return res.status(r.ok ? 200 : (r.status || 502)).json(data);
  } catch (e) {
    return res.status(502).json({ error: 'Could not reach the design-partner service.' });
  }
});

// R-F2673 — admin: approve/decline/update one design-partner record. Client
// POSTs {status, notes?}; forwarded to the aria-intel tracker as a PATCH by
// index. Admin-only (moving a record to a qualifying status is what closes the
// operator-owned gate #7). POST (not PATCH) so the existing API.post client
// helper works — app.js has no patch method.
app.post('/api/design-partners/:index/status', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'aria service unavailable' });
    const index = parseInt(req.params.index, 10);
    if (!Number.isInteger(index) || index < 0) return res.status(400).json({ error: 'invalid index' });
    const patch = {};
    if (typeof req.body?.status === 'string') patch.status = req.body.status;
    if (typeof req.body?.notes === 'string') patch.notes = req.body.notes;
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/admin/design-partners/${index}`, {
      method: 'PATCH',
      headers: { ..._ariaHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return res.status(r.status || 502).json(data);

    // R-F3328 — THE FIX. Moving a record to an access-granting status is the
    // approval decision, so it must also give the human a way in. Before this,
    // the response above was the end of the chain: the label changed and the
    // partner got nothing (verified live — Ray Ingram, approved 2026-07-28
    // 10:35, no account existed). Provisioning runs AFTER the status write so a
    // provisioning failure can never lose the operator's decision, and its
    // outcome is returned rather than swallowed (§19e/§22): the page shows the
    // credential when SMTP could not deliver it.
    if (ACCESS_GRANTING_STATUSES.includes(patch.status)) {
      const entry = (data && data.updated) || null;
      if (entry) {
        try {
          const provisioning = await provisionDesignPartnerAccess(entry);
          if (provisioning.provisioned || provisioning.outcome === 'existing_account') {
            errorTracker.recordSuccess('design_partner_provision');
            const admin = findUserById(req.user.userId);
            logAudit({
              adminId: req.user.userId, adminEmail: admin?.email || '',
              action: 'design_partner_access_granted',
              targetId: provisioning.userId || '', targetEmail: provisioning.email || '',
              targetName: entry.name || '',
              notes: `status=${patch.status} outcome=${provisioning.outcome} email_sent=${provisioning.emailSent}`,
            });
          } else {
            // §21a — a partner approved but NOT provisioned is a gap the brain
            // must know about; it is the exact silence this ticket closes.
            errorTracker.record('design_partner_provision', provisioning.outcome, null, null,
              { email: provisioning.email || '', reason: provisioning.reason });
          }
          return res.json({ ...data, provisioning });
        } catch (provErr) {
          errorTracker.record('design_partner_provision', 'handler_error', provErr);
          return res.json({
            ...data,
            provisioning: {
              provisioned: false, outcome: 'error',
              reason: `Status saved, but issuing the login failed: ${provErr.message}. Use "Issue login" to retry.`,
              emailSent: false, emailReason: 'not attempted',
            },
          });
        }
      }
    }
    return res.json(data);
  } catch (e) {
    return res.status(502).json({ error: 'Could not reach the design-partner service.' });
  }
});

// R-F3328 — issue (or re-check) a partner's login on demand, addressed by the
// same index the tracker uses. Two reasons this exists rather than only running
// inside the status change: partners approved BEFORE this shipped are already
// sitting at status=engaged with no account (nothing would re-fire for them),
// and the credential is shown once, so the operator needs a way to retry a send
// that failed. Idempotent — provisionDesignPartnerAccess never touches an
// existing account, so pressing it twice cannot reset anyone's password.
app.post('/api/design-partners/:index/provision', requireAdmin, async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'aria service unavailable' });
    const index = parseInt(req.params.index, 10);
    if (!Number.isInteger(index) || index < 0) return res.status(400).json({ error: 'invalid index' });
    let entries;
    try {
      entries = await _fetchDesignPartners();
    } catch (e) {
      return res.status(e.status || 502).json({ error: `Could not read the design-partner record: ${e.message}` });
    }
    const entry = entries[index];
    if (!entry) return res.status(404).json({ error: `No design-partner record at index ${index}` });
    // Access follows the approval decision, so an un-approved record cannot be
    // provisioned by calling this route directly.
    if (!ACCESS_GRANTING_STATUSES.includes(entry.status)) {
      return res.status(400).json({
        error: `${entry.name || 'This partner'} is "${entry.status}". Approve them first `
             + `(${ACCESS_GRANTING_STATUSES.join(' or ')}) : access follows approval.`,
      });
    }
    const provisioning = await provisionDesignPartnerAccess(entry);
    if (provisioning.provisioned || provisioning.outcome === 'existing_account') {
      errorTracker.recordSuccess('design_partner_provision');
      const admin = findUserById(req.user.userId);
      logAudit({
        adminId: req.user.userId, adminEmail: admin?.email || '',
        action: 'design_partner_access_granted',
        targetId: provisioning.userId || '', targetEmail: provisioning.email || '',
        targetName: entry.name || '',
        notes: `manual issue · outcome=${provisioning.outcome} email_sent=${provisioning.emailSent}`,
      });
    } else {
      errorTracker.record('design_partner_provision', provisioning.outcome, null, null,
        { email: provisioning.email || '', reason: provisioning.reason });
    }
    return res.json({ provisioning });
  } catch (e) {
    try { errorTracker.record('design_partner_provision', 'handler_error', e); } catch { /* best-effort */ }
    return res.status(500).json({ error: e.message || 'Failed to issue the login' });
  }
});

// R-F2673 — PUBLIC "Become a Design Partner" application funnel (partners.html).
// No login: a prospect applies here. Forwarded to the aria-intel tracker with the
// service token, FORCING status='applied' + source='public_application' server-
// side — a self-service applicant can NEVER choose a qualifying status, so this
// funnel cannot move gate #7 (operator-owned per CLAUDE.md §1; only an operator
// promoting them to contacted/engaged/onboarded counts). Rate-limited like every
// route; relays the brain's honest verdict, never a fake success (§22).
app.post('/api/design-partners/apply', async (req, res) => {
  try {
    if (!ARIA_SERVICE_URL) return res.status(503).json({ ok: false, error: 'Applications are temporarily unavailable — please try again shortly.' });
    const body = req.body || {};
    const name = String(body.name || '').trim().slice(0, 200);
    const email = String(body.email || body.contact || '').trim().slice(0, 200);
    const company = String(body.company || '').trim().slice(0, 200);
    const useCase = String(body.use_case || body.useCase || body.notes || '').trim().slice(0, 1000);
    if (!name || !email.includes('@')) {
      return res.status(400).json({ ok: false, error: 'A name and a valid email are required.' });
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 12000);
    try {
      const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/admin/design-partners`, {
        method: 'POST',
        headers: { ..._ariaHeaders(), 'Content-Type': 'application/json' },
        // status/source are FORCED here — never taken from the client body.
        body: JSON.stringify({
          name, contact: email, company, notes: useCase,
          status: 'applied', source: 'public_application',
        }),
        signal: ctrl.signal,
      });
      const data = await r.json().catch(() => ({}));
      return res.status(r.ok ? 200 : (r.status || 502)).json(
        r.ok ? { ok: true } : { ok: false, error: data.detail || data.error || 'Could not record your application right now.' }
      );
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    return res.status(502).json({ ok: false, error: 'Could not reach the application service. Please try again shortly.' });
  }
});

// ── §25a web delivery-outcome (R-F1565) ──────────────────────────────────────
// Mirrors the WA listener's reportOutcome (services/wa-listener/
// aria_wa_listener.mjs ~L1023): every output surface reports whether the user
// actually got a real answer so ARIA's brain can FEEL the web limb (CLAUDE.md
// §25/§25a). Same /api/aria/outcome endpoint + payload shape the WA path uses.
// Best-effort: never throws, short timeout — outcome reporting must never break
// the reply path.
function reportOutcome(surface, requestId, intendedResult, actualOutcome, latencyMs, detail) {
  try {
    if (!ARIA_SERVICE_URL || !requestId) return;
    const _payload = JSON.stringify({
      surface,
      request_id: requestId,
      intended_result: intendedResult,
      actual_outcome: actualOutcome,
      latency_ms: latencyMs || 0,
      detail: detail || '',
    });
    // R-F2101 (2026-06-28, ARIA web DD §25): ONE retry so a brain blip / mid-deploy
    // doesn't blind the proprioception loop to a delivery outcome. Still fully
    // best-effort + fire-and-forget — never awaited, never throws.
    const _send = (attempt) => {
      fetch(`${ARIA_SERVICE_URL}/api/aria/outcome`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: _payload,
        signal: AbortSignal.timeout(3000),
      }).catch((_oe) => {
        if (attempt < 1) setTimeout(() => _send(attempt + 1), 1500);
        else console.warn('[R-F1638] reportOutcome failed (after 1 retry):', _oe?.message);
      });
    };
    _send(0);
  } catch { /* outcome reporting must never break the reply path */ }
}

// ── R-F1615 — web async-complete-and-push (server-side poll) ─────────────────
// The /api/aria/chat handler used to hold ONE synchronous fetch open for up to
// 600s while the brain crawled+synthesised. A deploy/restart of the brain (or
// any mid-flight stall) tore that single connection down → the browser got a
// 502. The brain already supports async-complete-and-push (routes/aria.py:7579
// async_mode + /chat/result/{job_id}), proven on the WA listener
// (services/wa-listener/aria_wa_listener.mjs askARIAAsync). This mirrors it
// server-side: POST async_mode:true → job_id (<1s) → POLL the result endpoint.
//
// Why POLL and not callback: the brain's _is_callback_allowed SSRF allowlist
// (routes/aria.py:9590) only permits the WA listener origin. Adding the web app
// to that allowlist is a brain-side change outside this fix's blast radius, so
// we deliberately use the server-side poll (no callback) — same total budget,
// but each poll is a SHORT request, so a mid-deploy brain blip only kills one
// poll tick (which we retry) instead of the whole 10-min answer (R-F1392
// pattern: tolerate transient not_found/errors instead of giving up).
//
// Returns the brain's final chat-result dict (same shape sync /chat returned),
// or null if the brain doesn't support async mode (caller falls back to sync).
async function _ariaChatAsyncPoll(message, sid, personaUserId, persona) {
  // Total budget — same env var the old sync path used (default 600s).
  const BUDGET_MS = parseInt(process.env.ARIA_CHAT_PROXY_TIMEOUT_MS || '600000', 10);
  const t0 = Date.now();

  // 1. Dispatch — get a job_id fast. No callback_url (server-side poll only).
  let job;
  const dispatch = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat`, {
    method: 'POST',
    headers: _ariaHeaders(),
    body: JSON.stringify({
      message, session_id: sid, user_id: personaUserId, persona,
      async_mode: true,
    }),
    signal: AbortSignal.timeout(30000),   // dispatch returns in <1s normally
  });
  if (!dispatch.ok) {
    // Surface non-OK so the caller logs WHY and falls through (sync/local).
    const body = await dispatch.text().catch(() => '');
    const err = new Error(`async dispatch HTTP ${dispatch.status}: ${body.slice(0, 200)}`);
    err._httpStatus = dispatch.status;
    throw err;
  }
  job = await dispatch.json();
  const jobId = job && job.job_id;
  if (!jobId) {
    // Older brain build without async chat support — it returned the sync
    // result directly. Honour it (zero-regression for un-upgraded brains).
    return (job && (job.response || job.answer)) ? job : null;
  }

  // 2. Poll — fast at first, then back off. Each poll is a short request, so a
  //    mid-deploy brain blip costs one tick, not the whole answer.
  const FAST_MS = 1000, SLOW_MS = 5000, FAST_PHASE_MS = 30000;
  let notFoundStreak = 0;   // R-F1392 — transient store blips read as not_found
  while (Date.now() - t0 < BUDGET_MS) {
    const elapsed = Date.now() - t0;
    await new Promise(r => setTimeout(r, elapsed < FAST_PHASE_MS ? FAST_MS : SLOW_MS));
    let st;
    try {
      const pr = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat/result/${jobId}`, {
        method: 'GET', headers: _ariaHeaders(),
        signal: AbortSignal.timeout(15000),
      });
      // 503 = store temporarily unavailable (keep polling); any non-OK = retry.
      if (!pr.ok) continue;
      st = await pr.json();
    } catch { continue; }   // transient poll error (deploy/blip) — keep waiting
    if (!st) continue;
    if (st.status === 'not_found') {
      if (++notFoundStreak >= 3) throw new Error('chat job expired');
      continue;
    }
    notFoundStreak = 0;
    if (st.status === 'done')   return st.result || {};
    if (st.status === 'failed') throw new Error(st.error || 'chat job failed');
    // status === 'processing' → keep polling
  }
  throw new Error('chat job timed out');
}

// R-F1687 (2026-06-19) — canonical, stable per-account key for bucketing
// conversation history. THE fix for the empty-sidebar bug: the /chat/stream
// proxy previously sent `user_id: req.user?.id` — but the auth token payload
// has `userId`, not `id`, so it sent '' on every turn. With an empty user_id
// the Python brain fell back to `session_id.rsplit("_",1)[0]` = a per-SESSION
// bucket, so every conversation landed under its own `{slug}_{ts}` key and the
// sidebar (which lists the bare `{slug}` bucket) showed nothing.
//
// This MUST return exactly the slug the web UI queries with. aria.html builds
//   USER_ID_SLUG = (user.email || user.username || user.id).replace(/[^A-Za-z0-9]/g,'')
// so a chat WRITE and a sidebar LIST land in the same bucket. Email is stable
// across deploys / account-store rebuilds; user.id is not (R-F1687 also moves
// users.json onto the /data volume so the id stops churning).
// R-F3831/R-F3832 — ONE refusal path for a request-controlled path segment that
// would otherwise be interpolated into a token-bearing upstream URL.
//
// Declared as a function declaration (hoisted) so the /api/wa-listener routes
// registered earlier in this file can call it at request time — the same reason
// requireRole is declared that way (see the note at its definition).
//
// §21a: a refused traversal is security-relevant, so it reaches the brain rather
// than the console. status 403 makes classifyError return SEVERITY.AUTH
// (lib/observability/errorTracker.mjs), which is on the ESCALATE list — a plain
// Error classifies TRANSIENT and is dropped before the wire, i.e. it would look
// wired and be dark.
//
// The response never echoes the attempted segment back to the caller.
function rejectBadPathSegment(res, kind, value) {
  const seen = typeof value === 'string' ? value : typeof value;
  const segmentRejected = new Error(
    `${kind} path segment rejected (${seen.length} chars, ` +
    `first 24: ${JSON.stringify(String(seen).slice(0, 24))})`,
  );
  segmentRejected.status = 403;
  try { errorTracker.record('proxy_path', 'segment_rejected', segmentRejected); } catch {}
  return res.status(400).json({ error: `Invalid ${kind}` });
}

function stableUserId(req) {
  try {
    const uid = req.user?.userId;
    if (uid) {
      const key = conversationKeyForUser(findUserById(uid));
      if (key) return key;
    }
  } catch { /* fall through to fallbacks */ }
  // Internal-token path sets req.user = { id: 'aria-internal' } — preserve it.
  if (req.user?.id)    return slugifyIdentity(req.user.id);
  if (req.user?.email) return slugifyIdentity(req.user.email);
  return '';
}

async function ariaProxy(req, res, path, { method = 'GET', fallback, timeoutMs } = {}) {
  let lastStatus = 0;
  let lastErr = '';
  if (ARIA_SERVICE_URL) {
    try {
      const url = `${ARIA_SERVICE_URL}${path}`;
      const headers = _ariaHeaders();
      // Diagnostic visibility: log whether we have a bearer token at all.
      // Past incident 2026-04-09: /forget kept returning 503 because the
      // fly.io call was silently 401'ing on a missing ARIA_API_TOKEN env
      // var, but ariaProxy logged nothing on non-2xx responses, so the
      // root cause was invisible. This block makes the failure mode
      // visible in seenode logs.
      const hasBearer = !!headers['Authorization'];
      // Per-route timeout. Default 30s is fine for lookups + chat, but
      // crawl / investigate / deep_research can take up to 3 min on fly.io.
      // Past incident 2026-04-18 — /teach URL crawls on guides.fscj.edu
      // and libguides.csn.edu fired 503 "Crawl unavailable" because the
      // proxy's 30s timeout aborted BEFORE fly.io's crawl finished.
      //
      // R-F455 (2026-05-14): default now env-var tunable via
      // ARIA_PROXY_TIMEOUT_MS. Session B brain-cascade diagnosis showed
      // full /health on fly takes 5-15s under load; with the 30s ceiling
      // the dashboard banner reported "ARIA service offline" on every
      // slow but healthy tick. Default bumped to 45s — leaves headroom
      // for legitimate slowness without making genuine outages drag.
      const _defaultTimeout = parseInt(
        process.env.ARIA_PROXY_TIMEOUT_MS || '45000', 10
      ) || 45000;
      const resolvedTimeout = (typeof timeoutMs === 'number' && timeoutMs > 0)
        ? timeoutMs
        : _defaultTimeout;
      const opts = {
        method: method || req.method,
        headers,
        signal: AbortSignal.timeout(resolvedTimeout),
      };
      // R-F2048 — forward the body for all write methods (was POST-only, which
      // silently dropped PUT/PATCH bodies, e.g. vault update_status).
      if (req.body && (method === 'POST' || method === 'PUT' || method === 'PATCH')) opts.body = JSON.stringify(req.body);
      const r = await fetch(url, opts);
      lastStatus = r.status;
      if (r.ok) {
        const ct = r.headers.get('content-type') || '';
        if (ct.includes('application/json')) return res.json(await r.json());
        return res.type(ct).send(await r.text());
      }
      // R-F2579 — RELAY upstream AUTH statuses (401/403) verbatim instead of
      // letting them fall through to the generic 503 "ARIA service offline"
      // fallback. An upstream 401/403 from aria-intel is a DEFINITIVE authorization
      // decision (e.g. an operator-only control-plane endpoint reached with a
      // non-operator token under ARIA token scoping) — NOT a service outage. The
      // old behaviour masked it as 503, so the browser could never tell "auth-gated"
      // from "down" and the /aria-brain dashboard showed the red "DATA UNAVAILABLE"
      // banner for panels that were merely operator-gated. The frontend already
      // treats 401/403 as auth-gated (public/aria-brain.html:485) — it just never
      // saw the real status. Only genuine failures (5xx / network / timeout) should
      // reach the fallback below.
      if (r.status === 401 || r.status === 403) {
        const ct = r.headers.get('content-type') || '';
        let body = '';
        try { body = await r.text(); } catch { /* swallow */ }
        console.warn(`[ARIA proxy] ${path} → fly.io HTTP ${r.status} bearer=${hasBearer} (auth status relayed)`);
        if (ct.includes('application/json') && body) {
          try { return res.status(r.status).json(JSON.parse(body)); } catch { /* fall through */ }
        }
        return res.status(r.status).json({ error: 'auth_required', fly_status: r.status });
      }
      // Non-2xx — capture the body for diagnostics and log it. ariaProxy
      // historically swallowed non-2xx responses silently, which made it
      // impossible to tell whether fly.io rejected the request, returned
      // a 5xx, or whether the proxy itself was misconfigured.
      try {
        lastErr = (await r.text()).slice(0, 300);
      } catch {/* swallow */}
      // R-F2814 (Stage A, R-F2813) — RELAY the readiness "warming up" 503 verbatim.
      // When aria-intel is mid-warmup after a restart it returns 503 {error:'warming_up'}.
      // Pass it through honestly (same reasoning as the 401/403 relay above: a
      // definitive upstream state, not an outage) so the client can show "ARIA is
      // starting up, retry shortly" instead of the generic "service unavailable"
      // fallback masking a transient warmup as a hard outage.
      if (r.status === 503 && lastErr.includes('warming_up')) {
        console.warn(`[ARIA proxy] ${path} → fly.io warming up (readiness 503 relayed)`);
        try { return res.status(503).json(JSON.parse(lastErr)); }
        catch { return res.status(503).json({ error: 'warming_up', message: 'ARIA is starting up — retry shortly.' }); }
      }
      console.warn(
        `[ARIA proxy] ${path} → fly.io HTTP ${r.status} bearer=${hasBearer} body=${lastErr}`,
      );
    } catch (e) {
      lastErr = e && e.message ? e.message : String(e);
      console.warn(`[ARIA proxy] ${path} threw: ${lastErr}`);
      // R-F1565 — wire this previously-dark ops failure to the brain. record()
      // is best-effort and only escalates significant severities (CRITICAL/
      // AUTH/STRUCTURAL) to /api/aria/brain/signal — a routine fly.io timeout
      // classifies TRANSIENT and is NOT escalated, so this won't flood the
      // gap pipeline; auth/structural proxy breakage now becomes coder-visible.
      try {
        errorTracker.record('aria_intel_proxy', 'proxy_threw', e, null, { path });
      } catch { /* telemetry must never break the proxy path */ }
    }
  } else {
    console.warn(`[ARIA proxy] ${path} skipped — ARIA_SERVICE_URL not set`);
  }
  // Fallback to local Node.js implementation. Pass the captured status +
  // error so the fallback can include it in the response (vs the previous
  // generic "ARIA service unavailable" with no diagnostic detail).
  if (fallback) return fallback({ lastStatus, lastErr });
  res.status(503).json({
    error: 'ARIA service unavailable',
    fly_status: lastStatus,
    fly_error: lastErr,
  });
}

// Send sweep data to Python ARIA service (called after each sweep)
// Bumped to 30s — sweep payload can be 2-5 MB and ARIA service has cold-start
// disk I/O when persisting to Redis, so 10s was timing out ~30% of sweeps.
//
// R-F565 (2026-05-16) — diagnostic enrichment. The 2026-05-16 08:38:59 log
// produced one occurrence of `[ARIA] sweep ingest failed: The operation
// was aborted due to timeout` with no payload size, no duration, no
// indication of which host. With sweeps firing every 5 min, a single
// failure in isolation could be a flaky network blip, a payload bloat,
// or ARIA backend stalling on cold-start I/O — we couldn't tell. The
// catch block now logs:
//   - elapsed ms before the abort fired (was it ~30s = the timeout, or
//     a fast network reset?)
//   - payload byte count (bloat regression check)
//   - target host + error code via enrichFetchError
// so the next single occurrence is self-diagnosing instead of opaque.
async function pushSweepToARIA(data) {
  if (!ARIA_SERVICE_URL) return;
  const t0 = Date.now();
  let bodySize = 0;
  try {
    const body = JSON.stringify(data);
    bodySize = body.length;
    const response = await fetch(`${ARIA_SERVICE_URL}/api/aria/ingest`, {
      method: 'POST',
      headers: {
        ..._ariaHeaders(),
        'X-ARIA-Ingest-Async': '1',
      },
      body,
      signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) {
      const text = await response.text().catch(() => '');
      throw new Error(`ingest_http_${response.status}: ${text.slice(0, 180)}`);
    }
  } catch (e) {
    const elapsedMs = Date.now() - t0;
    const cause = (e && e.cause && typeof e.cause === 'object') ? e.cause : {};
    const code = cause.code || cause.errno || e?.code || e?.name || 'unknown';
    let host = 'unknown';
    try { host = new URL(ARIA_SERVICE_URL).host; } catch {}
    const sizeKB = Math.round(bodySize / 1024);
    console.warn(
      `[ARIA] sweep ingest failed: ${e?.message || 'no message'} `
      + `· code=${code} · host=${host} · payload=${sizeKB}KB `
      + `· elapsed=${elapsedMs}ms`,
    );
    // R-F1565 — this ops failure was dark (console-only): wire it to the brain
    // via errorTracker.record so the failure reaches /api/aria/brain/signal
    // (R-F900 _reportToBrain) and becomes coder-visible. Best-effort; record()
    // never throws and only escalates significant severities to the brain.
    try {
      errorTracker.record('aria_sweep_ingest', 'ingest_failed', e, null,
        { host, payloadKB: sizeKB, elapsedMs, code });
    } catch { /* telemetry must never break the sweep path */ }
  }
}

app.get('/api/aria/identity', requireAuth, async (req, res) => {
  // R-F384: removed dead Upstash fallback (crucix:brain:aria:identity).
  // fly.io migrated to SQLite under R-F261, so the Upstash key was stale; the
  // ariaProxy chain to the Python service is the live source. When the proxy
  // can't reach fly.io, return a static identity card.
  ariaProxy(req, res, '/api/aria/identity', { fallback: async () => {
    res.json({
      name: 'ARIA', full_name: 'Arkmurus Research Intelligence Agent',
      status: llmProvider?.isConfigured ? 'online' : 'no_llm', mode: 'local',
      llm_provider: llmProvider?.name || null, age_days: 0, total_sweeps: 0, total_leads: 0,
      domain: 'Defence procurement, Lusophone Africa, Export controls',
    });
  }});
});

app.get('/api/aria/thoughts', requireAuth, async (req, res) => {
  // R-F382: fallback Upstash reads removed. The aria:thoughts and
  // aria:thought:<id> keys are never written by any current code path
  // — the fallback was returning [] in practice. Real source is the
  // Python aria_service (proxied above).
  ariaProxy(req, res, '/api/aria/thoughts', { fallback: async () => res.json([]) });
});

app.get('/api/aria/curiosity', requireAuth, async (req, res) => {
  // R-F382: fallback Upstash read removed. aria:identity is never
  // written by seenode; real source is the Python aria_service.
  ariaProxy(req, res, '/api/aria/curiosity', { fallback: async () => res.json({ open_threads: [] }) });
});

// ARIA Knowledge Base API
app.get('/api/aria/knowledge', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/knowledge', { fallback: async () => {
    try {
      const { getKBStats } = await import('./lib/aria/knowledge.mjs');
      res.json(getKBStats());
    } catch { res.json({ totalFacts: 0, totalQueries: 0, totalLearnings: 0 }); }
  }});
});

app.post('/api/aria/knowledge/fact', requireAdmin, async (req, res) => {  // R-F1818 (audit H4): writes CONFIRMED facts — admin only
  ariaProxy(req, res, '/api/aria/knowledge/fact', { method: 'POST', fallback: async () => {
    try {
      const { topic, content, confidence } = req.body || {};
      if (!topic || !content) return res.status(400).json({ error: 'topic and content required' });
      const { storeFact } = await import('./lib/aria/knowledge.mjs');
      storeFact(topic, content, 'user', confidence || 'CONFIRMED');
      res.json({ ok: true, message: 'Fact stored' });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});

// R-F2048 — the Agent Signup Vault is the CONTROLLED data-point-site catalogue.
// Adding/editing/removing sites is restricted to the ARIA admin/dev team. The
// vault.html page is admin-gated client-side; these enforce it SERVER-side so a
// non-admin token cannot write directly. (GET stays via the catch-all proxy; the
// page itself is admin-only.)
app.post('/api/aria/vault', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/vault', { method: 'POST' }));
app.put('/api/aria/vault/:siteId', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/vault/' + encodeURIComponent(req.params.siteId), { method: 'PUT' }));
app.delete('/api/aria/vault/:siteId', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/vault/' + encodeURIComponent(req.params.siteId), { method: 'DELETE' }));
// R-F2192 — clear ALL vault entries (admin). keep_portals=true preserves auto-discovered portals.
app.delete('/api/aria/vault', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/vault' + (req.query.keep_portals ? ('?keep_portals=' + encodeURIComponent(req.query.keep_portals)) : ''), { method: 'DELETE' }));

// R-F2338 (SECURITY) — DD-memory reset is a DESTRUCTIVE wipe-all (every report + index,
// all VLS proofs/chains, watchlist + alerts, DD vault). The Python handler
// (aria.py /dd/admin/reset) has NO auth and its docstring claimed "admin-gated at the web
// tier" — but there was NO explicit route here, so it fell through the `/api/aria/*`
// catch-all which is only `requireAuth` → ANY authenticated user (incl. free-tier) could
// wipe all DD data. Register the explicit `requireAdmin` gate (mirrors DELETE /vault),
// which sits BEFORE the catch-all so admin auth is enforced server-side.
app.post('/api/aria/dd/admin/reset', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/dd/admin/reset' + (req.query.confirm ? ('?confirm=' + encodeURIComponent(req.query.confirm)) : ''), { method: 'POST' }));

// R-F2045 — per-USER data sources (any signed-in user, scoped to themselves).
// user_id is pinned from the JWT and never trusted from the client; the brain
// scopes every read/write to agent_id="user:<uid>".
app.get('/api/aria/user/sources', requireAuth, (req, res) => {
  const p = new URLSearchParams(); p.set('user_id', req.user?.userId || '');
  ariaProxy(req, res, `/api/aria/user/sources?${p.toString()}`);
});
app.post('/api/aria/user/sources', requireAuth, (req, res) => {
  const p = new URLSearchParams(); p.set('user_id', req.user?.userId || '');
  ariaProxy(req, res, `/api/aria/user/sources?${p.toString()}`, { method: 'POST' });
});
app.delete('/api/aria/user/sources/:siteId', requireAuth, (req, res) => {
  const p = new URLSearchParams(); p.set('user_id', req.user?.userId || '');
  ariaProxy(req, res, `/api/aria/user/sources/${encodeURIComponent(req.params.siteId)}?${p.toString()}`, { method: 'DELETE' });
});
});

app.get('/api/aria/ledger', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/ledger', { fallback: async () => {
    try {
      const { getLedgerStats } = await import('./lib/aria/intel_ledger.mjs');
      res.json(getLedgerStats());
    } catch { res.json({ totalSignals: 0 }); }
  }});
});

app.get('/api/aria/ledger/country/:country', requireAuth, async (req, res) => {
  ariaProxy(req, res, `/api/aria/ledger/country/${encodeURIComponent(req.params.country)}`, { fallback: async () => {
    try {
      const { getCountrySituation } = await import('./lib/aria/intel_ledger.mjs');
      const sit = getCountrySituation(req.params.country);
      res.json(sit || { country: req.params.country, signalCount: 0, recentSignals: [] });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// Contact Intelligence API
app.get('/api/aria/contacts', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/contacts', { fallback: async () => {
    try {
      const { getAllContacts } = await import('./lib/aria/contacts.mjs');
      res.json({ contacts: getAllContacts() });
    } catch (e) { res.json({ contacts: [] }); }
  }});
});

app.get('/api/aria/contacts/country/:country', requireAuth, async (req, res) => {
  ariaProxy(req, res, `/api/aria/contacts/country/${encodeURIComponent(req.params.country)}`, { fallback: async () => {
    try {
      const { getContactsByCountry } = await import('./lib/aria/contacts.mjs');
      res.json({ contacts: getContactsByCountry(req.params.country) });
    } catch (e) { res.json({ contacts: [] }); }
  }});
});

app.post('/api/aria/contacts', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/contacts', { method: 'POST', fallback: async () => {
    try {
      const { addContact } = await import('./lib/aria/contacts.mjs');
      const { name, country, role, title, organisation, influence, notes } = req.body || {};
      if (!name || !country) return res.status(400).json({ error: 'name and country required' });
      addContact({ name, country, role, title, organisation, influence, notes });
      res.json({ ok: true, message: 'Contact added' });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// Approach Strategy Generator API
app.post('/api/aria/approach', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/approach', { method: 'POST', fallback: async () => {
    try {
      const { generateApproach } = await import('./lib/aria/approach.mjs');
      const { market, product, context } = req.body || {};
      if (!market) return res.status(400).json({ error: 'market required' });
      const strategy = generateApproach(market, product || '', context || '');
      res.json(strategy);
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// Orchestrator dead-letter queue (failed tasks)
app.get('/api/admin/dlq', requireAdmin, async (req, res) => {
  try {
    const { getDLQ } = await import('./lib/orchestrator/retry.mjs');
    res.json({ queue: getDLQ() });
  } catch { res.json({ queue: [] }); }
});

// ARIA Correction API (user feedback for training quality)
app.post('/api/aria/correct', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/correct', { method: 'POST', fallback: async () => {
    try {
      const { originalQuery, originalResponse, correction, correctAnswer } = req.body || {};
      if (!correction) return res.status(400).json({ error: 'correction required' });
      const { recordCorrection } = await import('./lib/aria/training_data.mjs');
      recordCorrection(originalQuery || '', originalResponse || '', correction, correctAnswer || '');
      const { storeLearning } = await import('./lib/aria/knowledge.mjs');
      storeLearning(correction, originalQuery || '');
      res.json({ ok: true, message: 'Correction recorded — ARIA will learn from this' });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// ── Document-intelligence learning loop (proxy to aria_service) ────────────
app.post('/api/aria/document/verify', requireAuth, (req, res) => {
  // R-F1909 (G3): pin user_id from the JWT so the brain owner-gates the verify
  // (an IDOR write — verifying flips the DD pre-run gate). Client value ignored.
  try { req.body = req.body || {}; req.body.user_id = req.user?.userId || ''; } catch {}
  return ariaProxy(req, res, '/api/aria/document/verify', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Document verify unavailable — backend offline' });
  }});
});

app.post('/api/aria/document/correct', requireAuth, (req, res) => {
  // R-F1909 (G3): pin user_id from the JWT so the brain owner-gates the field correction.
  try { req.body = req.body || {}; req.body.user_id = req.user?.userId || ''; } catch {}
  return ariaProxy(req, res, '/api/aria/document/correct', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Document correct unavailable — backend offline' });
  }});
});

app.get('/api/aria/document/extraction/:id', requireAuth, (req, res) => {
  // R-F1826 (audit H7): pin user_id from the JWT so the brain enforces extraction
  // ownership (records hold uploaded document content — PII/contracts).
  const userId = req.user?.userId || '';
  return ariaProxy(req, res, `/api/aria/document/extraction/${encodeURIComponent(req.params.id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => {
    res.status(503).json({ error: 'Document extraction lookup unavailable' });
  }});
});

app.get('/api/aria/document/extractions/recent', requireAuth, (req, res) => {
  // R-F1909 (G3, audit M2): OVERRIDE user_id with the JWT value — the client
  // query was forwarded verbatim, so ?user_id=victim would have defeated the
  // brain's owner filter and listed every tenant's extractions. Mirrors /dd/reports.
  const params = new URLSearchParams(req.query || {});
  params.set('user_id', req.user?.userId || '');
  const qs = params.toString();
  ariaProxy(req, res, `/api/aria/document/extractions/recent${qs ? '?' + qs : ''}`, { fallback: async () => {
    res.status(503).json({ count: 0, extractions: [], error: 'backend offline' });
  }});
});

// ── ARIA brain health (proxy to Python aria_service) ───────────────────────
app.get('/api/aria/brain/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/brain/stats', { fallback: async () => {
    res.status(503).json({ error: 'Brain stats unavailable', health: 'offline', modules: {} });
  }}));

app.get('/api/aria/brain/alerts', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/brain/alerts', { fallback: async () => {
    res.status(503).json({ alerts: [{ severity: 'critical', title: 'ARIA backend offline' }] });
  }}));

// ── ARIA brain absorb — write-side of the brain (proxy to Python) ─────────
// Past gap (verified live 2026-04-19 00:50): seenode-side modules
// (brainAbsorb in learning_store.mjs, the email reader, waListener,
// pattern_analyzer, opportunity_engine, etc.) post to ${ARIA_SERVICE_URL}
// /api/aria/brain/absorb. ARIA_SERVICE_URL points at seenode itself
// (intel.sursec.co.uk) so the request hits THIS server, not fly.io. The
// /brain/stats route was proxied above but /brain/absorb wasn't — so
// every signal returned 404, swallowed by the fire-and-forget catch.
// Result: 50 backfilled emails → 0 brain signals counted.
app.post('/api/aria/brain/absorb', requireAuth, (req, res) => {
  // R-F1865 (audit DD-25): pin user_id from the JWT so an absorbed signal is
  // attributed to the authenticated caller, never to a forged body value.
  try { req.body = req.body || {}; req.body.user_id = req.user?.userId || ''; } catch {}
  return ariaProxy(req, res, '/api/aria/brain/absorb', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Brain absorb unavailable — Python aria_service offline', skipped: true });
  }});
});

// ── ARIA read-document — email body + attachment ingest (proxy to Python) ──
// Same gap as /brain/absorb above: emailReader.mjs and waListener.mjs
// post here for body ingest, but seenode had no proxy → 404 → emails
// never indexed in ChromaDB. The `read` endpoint was proxied (see
// line ~2397) but `read-document` was missed.
app.post('/api/aria/read-document', requireAuth, (req, res) => {
  // R-F1852 (audit, DD stage 4): stamp the owner onto the body so an async
  // read-document job persists user_id and /read-document/result/{id} can enforce
  // ownership. Pinned from the JWT (same scheme as /dd/orchestrate) so it can't be
  // forged on the wire. Async (large/scanned) docs are the case that returns the
  // full extracted text via the polled result endpoint.
  try {
    req.body = req.body || {};
    req.body.user_id = req.user?.userId || '';
  } catch {}
  return ariaProxy(req, res, '/api/aria/read-document', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Document ingest unavailable — Python aria_service offline' });
  }});
});

// R-F1865 (audit DD-16): meeting-notes ingest — Python (aria.py:15401) reads
// user_id from the body/header for attribution. Pin it from the JWT so it can't
// be forged on the wire (same scheme as /dd/orchestrate + /read-document). Must
// sit before the catch-all (which would forward the client body verbatim).
app.post('/api/aria/meeting-notes/process', requireAuth, (req, res) => {
  try { req.body = req.body || {}; req.body.user_id = req.user?.userId || ''; } catch {}
  return ariaProxy(req, res, '/api/aria/meeting-notes/process', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Meeting-notes processing unavailable — ARIA service offline' });
  }});
});

app.get('/api/aria/student/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/stats', { fallback: async () => {
    res.status(503).json({ error: 'Mastery stats unavailable', mastery: {} });
  }}));

// ── ARIA compliance sub-endpoints (proxy to Python aria_service) ────────────
app.post('/api/aria/compliance/screen', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/compliance/screen', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Compliance screening unavailable — ARIA service offline', status: 'UNKNOWN', result: 'UNKNOWN' });
  }}));

app.post('/api/aria/compliance/classify', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/compliance/classify', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Classification unavailable — ARIA service offline', classifications: [] });
  }}));

app.post('/api/aria/compliance/sanctions', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/compliance/sanctions', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Sanctions check unavailable — ARIA service offline', matches: [] });
  }}));

app.post('/api/aria/compliance/risk', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/compliance/risk', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Risk assessment unavailable — ARIA service offline', risk_level: 'UNKNOWN' });
  }}));

// ── ARIA proactive endpoints ────────────────────────────────────────────────
app.post('/api/aria/proactive/strategic-ideas', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/proactive/strategic-ideas', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Strategic ideas unavailable — ARIA service offline', ideas: '' });
  }}));

app.post('/api/aria/proactive/lead-hunt', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/proactive/lead-hunt', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Lead hunt unavailable — ARIA service offline', leads: '' });
  }}));

// ── ARIA self-coding (proxy) ───────────────────────────────────────────────
app.post('/api/aria/self/code', requireAdmin, (req, res) =>  // R-F1818 (audit H4): self-coding trigger — admin only
  ariaProxy(req, res, '/api/aria/self/code', { method: 'POST', fallback: async () => {
    res.status(503).json({ ok: false, error: 'Self-coding unavailable — ARIA service offline' });
  }}));

// ── ARIA vision status (proxy) — diagnostic for image OCR backends ─────────
app.get('/api/aria/vision-status', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/vision-status', { fallback: async () => {
    res.status(503).json({ ok: false, error: 'Vision status unavailable — ARIA service offline' });
  }}));

// ── ARIA image OCR (proxy) — used by waListener for group images ───────────
// Dedicated proxy with a longer body limit (images can be 8MB base64).
app.post('/api/aria/ocr',
  express.json({ limit: '12mb' }),
  requireAuth,
  (req, res) => {
    // R-F1865 (audit DD-07): pin the JWT owner onto the body so an async OCR
    // job persists user_id → GET /ocr/result/{id} can enforce ownership.
    // Pinned (not trusted from the client) like /dd/orchestrate + /read-document.
    try { req.body = req.body || {}; req.body.user_id = req.user?.userId || ''; } catch {}
    return ariaProxy(req, res, '/api/aria/ocr', { method: 'POST', fallback: async () => {
      res.status(503).json({ text: '', method: 'none', error: 'OCR unavailable — ARIA service offline' });
    }});
  });

// ── ARIA RAG store (proxy) — persistent retrieval-augmented generation ────
app.get('/api/aria/rag/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/rag/stats', { fallback: async () => {
    res.status(503).json({ available: false, error: 'RAG unavailable — ARIA service offline' });
  }}));

app.get('/api/aria/rag/sources', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/rag/sources${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => {
    res.status(503).json({ available: false });
  }}));

// ── ARIA Brain Dashboard proxy routes (2026-04-17) ────────────────────────
// All endpoints needed by /aria-brain.html dashboard — proxied to fly.io.
const _brainFallback = () => ({ error: 'ARIA service offline' });
app.get('/api/aria/health', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/health', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/operating-mode', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/operating-mode', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/operating-mode/set', requireAdmin, (req, res) =>  // R-F1818 (audit H4): global mode change — admin only
  ariaProxy(req, res, '/api/aria/operating-mode/set', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/circuit-breakers', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/circuit-breakers', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/autonomous/dlq', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomous/dlq', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/autonomous/status', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomous/status', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/metrics/grounded_rate', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/metrics/grounded_rate${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/metrics/contradiction_rate', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/metrics/contradiction_rate', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/metrics/fact_decay', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/metrics/fact_decay', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/predictor/block_rate', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/predictor/block_rate', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/student/mastery', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/mastery', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/student/mastery/heatmap', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/mastery/heatmap', { fallback: async () => res.status(503).json(_brainFallback()) }));
// R-F2383 — removed dead duplicate GET /api/aria/adversarial/stats (the public
// _r577PublicProxy registration above wins; this later auth'd def never fired).
// R-F57: trigger a fresh weekly run from the dashboard. Long-running
// (each attack is an LLM round-trip + verifier check), 5-min timeout.
app.post('/api/aria/adversarial/run_weekly', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/adversarial/run_weekly', { method: 'POST', timeoutMs: 300000, fallback: async () => res.status(503).json(_brainFallback()) }));
// R-F2383 — removed dead duplicate GET /api/aria/chat-audit/stats (public
// _r577PublicProxy above wins; this later auth'd def never fired).
app.get('/api/aria/chat-audit/recent', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/chat-audit/recent${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/chat-audit/verify', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/chat-audit/verify${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
// Week 4: Composite autonomy + calibration
app.get('/api/aria/autonomy/composite', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomy/composite', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/autonomy/history', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/autonomy/history${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/autonomy/baseline', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomy/baseline', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/autonomy/baseline', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomy/baseline', { fallback: async () => res.status(503).json(_brainFallback()) }));

// /aria-brain dashboard panels that were missing proxies (added 2026-04-18).
// Past incident: the Autonomy Surface and Learning & Verification panels
// showed "No data" because fetchJson('/autonomy/surface') and
// fetchJson('/learning/stats') were hitting Express with no matching route
// → 404 → null response → "No data" render. The Python endpoints exist
// on fly.io; we just weren't routing to them.
app.get('/api/aria/autonomy/surface', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/autonomy/surface', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/learning/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/learning/stats', { fallback: async () => res.status(503).json(_brainFallback()) }));
// Also expose the new Track C + "Fire on ARIA" panels so they can be
// wired into the dashboard without another server.mjs change later.
app.get('/api/aria/capability-card', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/capability-card${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/consistency/scores', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/consistency/scores', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/consistency/run', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/consistency/run', { method: 'POST', timeoutMs: 200000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/calibration/auto-tune', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/calibration/auto-tune', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/calibration/auto-tune/run', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/calibration/auto-tune/run', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/pending-actions', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/pending-actions', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/vendors', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/vendors', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/dd/sources', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/dd/sources', { fallback: async () => res.status(503).json(_brainFallback()) }));
// R-F51 watchlist alerts — three endpoints proxied to fly. Query string is
// preserved (since_hours, user_id) so the FE can pass the read-state tag.
// R-F1865 (audit DD-17): pin user_id from the JWT (strip any client value) so
// the brain returns only the caller's own watchlist alerts. Pre-fix the query
// string was forwarded verbatim → ?user_id=victim leaked another user's alerts.
// Mirrors /dd/reports (R-F607).
app.get('/api/aria/dd/watchlist/alerts', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';
  const params = new URLSearchParams(existingQs);
  params.set('user_id', userId);
  return ariaProxy(req, res, `/api/aria/dd/watchlist/alerts?${params.toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/dd/watchlist/alerts/unread-count', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';
  const params = new URLSearchParams(existingQs);
  params.set('user_id', userId);
  return ariaProxy(req, res, `/api/aria/dd/watchlist/alerts/unread-count?${params.toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.post('/api/aria/dd/watchlist/alerts/read', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/dd/watchlist/alerts/read', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
// R-F52 DD report library
//
// R-F607 (2026-05-16) — DD reports per-user scoping. Pre-R-F607 every
// authenticated user got the global Redis index (any user could read any
// other user's DD runs). The proxy now appends `user_id` from the JWT
// to the upstream URL so the Python list_reports filters to "your own
// runs only". The Python endpoint also accepts `user_email_domain` for
// R-F608 same-company visibility — wired here via findUserById lookup
// so the JWT-only payload doesn't have to carry email.
// R-F2075 (2026-06-28) — DD reports LIST per-user scoping. The R-F607 comment
// above CLAIMED the proxy appends user_id for /dd/reports, but no such route ever
// existed: the plural list hit the catch-all (server.mjs ~5110) which forwards the
// query string VERBATIM with no user_id, and the brain treats an empty user_id as
// "admin / see everything" → any authenticated web user could read EVERY user's DD
// report metadata (entity names, jurisdictions, risk, dates). Latent only while
// legacy reports have user_id=null; becomes a live cross-tenant leak the moment any
// user runs a web DD (orchestrate stamps their user_id). Mirror /dd/report/:id
// (R-F1820): strip any client-supplied user_id and pin the JWT identity, plus the
// email domain for R-F608 same-company visibility. Audited 2026-06-28 (4-step DD).
app.get('/api/aria/dd/reports', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';
  const params = new URLSearchParams(existingQs);
  params.delete('user_id');                 // never trust a client-supplied owner
  params.set('user_id', userId);
  params.delete('user_email_domain'); // R-F2238: fail-CLOSED — strip any client-supplied domain unconditionally (even if the lookup below throws)
  try {
    const u = findUserById(userId);
    const email = String(u?.email || '').trim().toLowerCase();
    const domain = email.includes('@') ? email.split('@').pop() : '';
    if (domain) params.set('user_email_domain', domain);
  } catch {}
  return ariaProxy(req, res, `/api/aria/dd/reports?${params.toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
// R-F2355 (2026-07-02 DD) — GET /dd/watchlist had NO dedicated proxy route, so it hit the
// catch-all which forwards the query verbatim with no user_id → the brain returned the
// GLOBAL watchlist, leaking every tenant's watched companies (another user's DD company
// showed on this user's watchlist). Same class as the R-F2075 reports leak. Pin the JWT
// identity here; strip any client-supplied user_id/domain (fail-CLOSED, R-F2238).
app.get('/api/aria/dd/watchlist', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';
  const params = new URLSearchParams(existingQs);
  params.delete('user_id');
  params.set('user_id', userId);
  params.delete('user_email_domain');
  try {
    const u = findUserById(userId);
    const email = String(u?.email || '').trim().toLowerCase();
    const domain = email.includes('@') ? email.split('@').pop() : '';
    if (domain) params.set('user_email_domain', domain);
  } catch {}
  return ariaProxy(req, res, `/api/aria/dd/watchlist?${params.toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
// R-F2097 (2026-06-28 DD) — pin the JWT user_id + email-domain onto the entity-keyed
// DD vault/case endpoints so the brain (R-F2097) scopes them to the caller's owned
// entities. Pre-fix these had NO explicit route → hit the catch-all (~5135) which
// forwards the client query verbatim with no user_id → the brain returned ALL
// tenants' cases (live-confirmed IDOR). Mirrors the /dd/reports pin (R-F2075). MUST
// sit before the catch-all. `:cid` is one path segment — canonical ids use colons,
// not slashes (company:GB:0768900200018-89), so a single-segment param is correct.
function _ddPinUserParams(req) {
  const userId = req.user?.userId || '';
  const existingQs = req.url.includes('?') ? req.url.slice(req.url.indexOf('?') + 1) : '';
  const params = new URLSearchParams(existingQs);
  params.delete('user_id');                 // never trust a client-supplied owner
  params.set('user_id', userId);
  params.delete('user_email_domain'); // R-F2238: fail-CLOSED — strip any client-supplied domain unconditionally (even if the lookup below throws)
  try {
    const u = findUserById(userId);
    const email = String(u?.email || '').trim().toLowerCase();
    const domain = email.includes('@') ? email.split('@').pop() : '';
    if (domain) params.set('user_email_domain', domain);
  } catch {}
  return params;
}

// R-F3225 — all customer watchlist mutations are owner-pinned, including for
// privileged users. The generic proxy intentionally grants broader operator
// access and therefore cannot safely carry customer schedule/delete writes.
app.post('/api/aria/dd/watchlist', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/watchlist?${_ddPinUserParams(req).toString()}`, {
    method: 'POST',
    fallback: async () => res.status(503).json(_brainFallback()),
  });
});
app.patch('/api/aria/dd/watchlist/:name/schedule', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const name = encodeURIComponent(req.params.name || '');
  return ariaProxy(req, res, `/api/aria/dd/watchlist/${name}/schedule?${_ddPinUserParams(req).toString()}`, {
    method: 'PATCH',
    fallback: async () => res.status(503).json(_brainFallback()),
  });
});
app.delete('/api/aria/dd/watchlist/alerts/:alertId', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const alertId = encodeURIComponent(req.params.alertId || '');
  return ariaProxy(req, res, `/api/aria/dd/watchlist/alerts/${alertId}?${_ddPinUserParams(req).toString()}`, {
    method: 'DELETE',
    fallback: async () => res.status(503).json(_brainFallback()),
  });
});
app.delete('/api/aria/dd/watchlist/:name', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const name = encodeURIComponent(req.params.name || '');
  return ariaProxy(req, res, `/api/aria/dd/watchlist/${name}?${_ddPinUserParams(req).toString()}`, {
    method: 'DELETE',
    fallback: async () => res.status(503).json(_brainFallback()),
  });
});
// R-F3071 — pin the owner for the vault STATS panel too. R-F2097 added explicit
// pinned routes for search + case and left stats to the generic catch-all, which
// does not pin for admin/privileged callers — so the operator's dd-reports.html
// showed platform-wide totals next to an owner-scoped search, the same
// "headline matches nothing you can open" incoherence this R-number fixes for
// customers. dd-reports.html is the CUSTOMER surface; the platform-wide view
// belongs on aria-brain.html.
app.get('/api/aria/dd/vault/stats', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/vault/stats?${_ddPinUserParams(req).toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/dd/vault/search', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/vault/search?${_ddPinUserParams(req).toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/dd/vault/case/:cid', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/vault/case/${encodeURIComponent(req.params.cid)}?${_ddPinUserParams(req).toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.delete('/api/aria/dd/vault/case/:cid', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/vault/case/${encodeURIComponent(req.params.cid)}?${_ddPinUserParams(req).toString()}`, { method: 'DELETE', fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/dd/case/:cid', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/case/${encodeURIComponent(req.params.cid)}?${_ddPinUserParams(req).toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/dd/report/:run_id', requireAuth, (req, res) => {
  // R-F1820: pin user_id from the JWT (strip any client value) so the brain can
  // enforce report ownership. R-F2291: ALSO pin user_email_domain (via
  // _ddPinUserParams, fail-closed) so the brain can honour same-company sharing —
  // without it, opening a SHARED report 404'd ("no content on click"). Mirrors
  // /dd/reports + /dd/case.
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/report/${encodeURIComponent(req.params.run_id)}?${_ddPinUserParams(req).toString()}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.delete('/api/aria/dd/report/:run_id', requireAuth, (req, res) => {
  // R-F1820: ownership-pinned delete (was unguarded via the catch-all).
  // R-F2291: pin user_email_domain too so a colleague can delete a company-shared
  // report (was 404 "delete fails"); cross-COMPANY delete stays blocked.
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/dd/report/${encodeURIComponent(req.params.run_id)}?${_ddPinUserParams(req).toString()}`, { method: 'DELETE', fallback: async () => res.status(503).json(_brainFallback()) });
});

// R-F2837 — download a DD report as PDF.
//
// MUST sit beside the JSON route and reuse _ddPinUserParams. The brain enforces
// ownership on /dd/report/{id} from the pinned user_id + email-domain and 404s
// otherwise (R-F1820/R-F2291); fetching without that pin would hand any
// authenticated user any tenant's report as a PDF — the same IDOR class as the
// R-F2075 reports leak and the R-F2355 watchlist leak. The ACL is the brain's;
// this route's job is to not bypass it.
app.get('/api/aria/dd/report/:run_id/pdf', requireAuth, async (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const runId = String(req.params.run_id || '').trim();
  if (!runId) return res.status(400).json({ error: 'run_id required' });

  const base = process.env.ARIA_SERVICE_URL || '';
  if (!base) return res.status(503).json({ error: 'Report service unavailable' });

  try {
    const qs = _ddPinUserParams(req).toString();
    const upstream = await fetch(
      `${base}/api/aria/dd/report/${encodeURIComponent(runId)}?${qs}`,
      { headers: _ariaHeaders({ Accept: 'application/json' }) },
    );
    // Pass the brain's verdict through unchanged — a 404 here means "not yours
    // or not found", and must stay a 404 rather than becoming a 500.
    if (!upstream.ok) {
      return res.status(upstream.status === 404 ? 404 : 502)
        .json({ error: upstream.status === 404 ? 'Report not found' : 'Report service error' });
    }
    const report = await upstream.json();

    const { generateDueDiligencePDF } = await import('./lib/reports/pdf_generator.mjs');
    const pdf = await generateDueDiligencePDF(report, { docRef: runId });

    const entity = String(
      report?.target?.name || report?.identity?.entity_name || 'report',
    ).replace(/[^\w\-]+/g, '_').slice(0, 60) || 'report';

    // R-F3837 — the run id was interpolated RAW while `entity` beside it was
    // sanitised. Node rejects CRLF in a header value, so this is filename
    // spoofing via a `"` (…filename="x"; filename="invoice.exe") rather than
    // header injection — but the fix is the transform already used one line up.
    // Only the FILENAME is sanitised: the upstream fetch and the PDF's printed
    // docRef must keep the true run id.
    const safeRunId = runId.replace(/[^\w\-]+/g, '_').slice(0, 60) || 'report';

    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition',
      `attachment; filename="ARIA_DD_${entity}_${safeRunId}.pdf"`);
    res.setHeader('Cache-Control', 'no-store');   // reports are tenant data
    return res.send(pdf);
  } catch (e) {
    try { errorTracker?.record?.('dd_pdf_export', e); } catch {}
    return res.status(500).json({ error: 'PDF generation failed' });
  }
});

// R-F1852 (audit, DD stage 4) — explicit ownership-pinned poll routes for async
// job results + the DD entity-graph. These MUST sit before the catch-all proxy,
// which forwards the client query string verbatim (so ?user_id=victim would defeat
// the brain's ownership check). Each strips any client value and pins user_id from
// the JWT, exactly like /dd/report (R-F1820). 404s leak nothing on cross-tenant access.
app.get('/api/aria/chat/result/:job_id', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/chat/result/${encodeURIComponent(req.params.job_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/read-document/result/:job_id', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/read-document/result/${encodeURIComponent(req.params.job_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/entity-graph/:run_id', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/entity-graph/${encodeURIComponent(req.params.run_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
// R-F1865 (audit DD-02..07) — ownership-pinned GET routes for the per-user
// record endpoints (trace / scratchpad / feedback / honesty / verify /
// ocr-result). Each MUST sit before the catch-all proxy, which forwards the
// client query string verbatim (so ?user_id=victim would defeat the brain's
// ownership check). Each strips any client value and pins user_id from the
// JWT, exactly like /dd/report (R-F1820) + read-document/result (R-F1852).
// The Python side 404s (or not_found) on cross-tenant access — leaks nothing.
// These prefixes also have non-id sub-routes (list / stats / recent / sources)
// that take NO path param and must keep flowing to the catch-all. Express would
// otherwise bind ":id"='list' etc. and forward it to the wrong Python route, so
// each handler skips the reserved words via next().
const _DD16_RESERVED = new Set(['list', 'stats', 'recent', 'sources', 'export', 'result']);
app.get('/api/aria/ocr/result/:job_id', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/ocr/result/${encodeURIComponent(req.params.job_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/trace/:trace_id', requireAuth, (req, res, next) => {
  if (_DD16_RESERVED.has(req.params.trace_id)) return next();  // /trace/stats → catch-all
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/trace/${encodeURIComponent(req.params.trace_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/scratchpad/:trace_id', requireAuth, (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/scratchpad/${encodeURIComponent(req.params.trace_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/feedback/:feedback_id', requireAuth, (req, res, next) => {
  if (_DD16_RESERVED.has(req.params.feedback_id)) return next();  // /feedback/list|stats → catch-all
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/feedback/${encodeURIComponent(req.params.feedback_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/honesty/:judgment_id', requireAuth, (req, res, next) => {
  if (_DD16_RESERVED.has(req.params.judgment_id)) return next();  // /honesty/list|stats → catch-all
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/honesty/${encodeURIComponent(req.params.judgment_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
app.get('/api/aria/verify/:verification_id', requireAuth, (req, res, next) => {
  if (_DD16_RESERVED.has(req.params.verification_id)) return next();  // /verify/list|stats → catch-all
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  return ariaProxy(req, res, `/api/aria/verify/${encodeURIComponent(req.params.verification_id)}?user_id=${encodeURIComponent(userId)}`, { fallback: async () => res.status(503).json(_brainFallback()) });
});
// R-F607 (2026-05-16) — stamp originating user identity onto the
// orchestrate request body so the persisted report carries `user_id`
// (and user_email + derived domain for R-F608 same-company sharing).
// Pinning to the JWT-resolved values means the client can't forge
// these on the wire.
// R-F2765 — resolve the caller's tier and enforce one unit of a per-tier quota on
// the web path. Returns null when ALLOWED or EXEMPT (system / internal token /
// localhost bypass have no JWT userId → enforceQuota exempts them). Returns the
// checkAndConsume verdict when the cap is hit. Keeps the load-bearing exemption
// in one place; see lib/billing/enforce.mjs.
// R-F3618 — an admin is NOT a metered customer, and this was the one quota site
// that still metered them.
//
// roles.mjs states the model outright: "Billing TIER (free/pro/proIntel) is orthogonal
// and gates customer features only." R-F2981 applied that to the DD orchestrate route
// (server.mjs, `_ddPrivileged`) and to the brain-side consume route
// (`isPrivileged(_user)`), after the operator's own admin account — which has no
// `tier` field and therefore defaults to FREE — was blocked mid-demo by 'ddRun cap
// 5/5'. It missed `_quotaBlock`, even though the comment above claims this helper
// "keeps the load-bearing exemption in one place".
//
// So the identical defect survived on the chat lane: an admin was capped at the free
// tier's messagesPerDay: 50. Surfaced 2026-08-01 when a second admin was created with
// an explicit `tier: "free"` and the operator asked whether the admin role grants full
// access. It did not.
//
// Fixing it HERE rather than at the two call sites is the point: this is the shared
// helper, so any future metered web route inherits the exemption instead of
// re-discovering the bug. The §17 $300/mo LLM cap remains the hard backstop, and
// non-privileged users stay tier-capped exactly as before.
async function _quotaBlock(req, kind) {
  const uid = req.user?.userId;
  if (!uid) return null;   // system / internal token / localhost bypass — unchanged
  const user = findUserById(uid);
  if (isPrivileged(user)) return null;
  return enforceQuota(uid, user?.tier, kind);
}

app.post('/api/aria/dd/orchestrate', requireAuth, async (req, res) => {
  const userId = req.user?.userId || '';
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  let userEmail = '';
  let userTier = null;
  let _ddPrivileged = false;
  try {
    const u = findUserById(userId);
    userEmail = String(u?.email || '').trim();
    userTier = u?.tier || null;
    _ddPrivileged = isPrivileged(u);   // R-F2981
  } catch {}
  // R-F2765 — enforce the tier DD-runs/month cap before dispatching the expensive
  // DD orchestrator (userId guaranteed by the 401 above). Prevents runaway Claude
  // spend post-switch. System callers never reach this route (requireAuth).
  // R-F2981 — admins/operators are NOT customer-metered. The operator's admin
  // account has no `tier` field → defaulted to free (5 DD-runs/mo) and blocked
  // demos + ops (live 2026-07-24: a Silverbrook dry-run failed 'ddRun cap 5/5').
  // Exempt privileged roles; regular users stay tier-capped and the §17 $300/mo
  // LLM cost cap remains the hard backstop.
  const _ddq = _ddPrivileged ? null : await enforceQuota(userId, userTier, 'ddRun');
  if (_ddq) return res.status(429).json({ error: _ddq.reason, quota: { current: _ddq.current, cap: _ddq.cap } });
  req.body = req.body || {};
  req.body.user_id = userId;
  if (userEmail) req.body.user_email = userEmail;
  if (userTier) req.body.user_tier = userTier;   // R-F2767 — per-tier Claude cost attribution
  const t0 = Date.now();
  const requestId = req.body.request_id || `web_dd_${userId.replace(/[^a-zA-Z0-9_]/g, '_')}_${Date.now()}`;
  return ariaProxy(req, res, '/api/aria/dd/orchestrate', {
    method: 'POST',
    timeoutMs: parseInt(process.env.ARIA_DD_PROXY_TIMEOUT_MS || '810000', 10),
    fallback: async ({ lastStatus, lastErr } = {}) => {
      // R-F2405 — MUST use an outcome_wire-valid value; the brain
      // (/api/aria/outcome) 400-rejects anything outside
      // {delivered_real_answer,timeout_fallback,error,send_failed}, and
      // reportOutcome fire-and-forgets so the 400 was swallowed — the flagship
      // web DD *delivery* leg recorded NO outcome, ever. 'timeout'→'timeout_fallback'.
      reportOutcome('web', requestId, 'dd_report', 'timeout_fallback', Date.now() - t0, lastErr || 'brain timeout');
      res.status(503).json(_brainFallback());
    },
  }).then(() => {
    reportOutcome('web', requestId, 'dd_report', 'delivered_real_answer', Date.now() - t0);   // R-F2405: 'delivered'→valid value
  }).catch((e) => {
    reportOutcome('web', requestId, 'dd_report', 'error', Date.now() - t0, String(e && e.message || e));  // R-F2405: was swallowed silently
  });
});
app.get('/api/aria/rlaif/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/rlaif/stats', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/rlaif/evaluate', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/rlaif/evaluate', { method: 'POST', timeoutMs: 60000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/critique/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/critique/stats', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/critique/export', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/critique/export${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => res.status(503).json(_brainFallback()) }));
// Source uptime monitor (2026-04-18)
app.get('/api/aria/sources/uptime', (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/uptime', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/sources/uptime/run', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/uptime/run', { method: 'POST', timeoutMs: 300000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/sources/uptime/suspend', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/uptime/suspend', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/sources/uptime/unsuspend', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/uptime/unsuspend', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
// Query decomposer + publisher router (2026-04-18)
app.post('/api/aria/query/decompose', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/query/decompose', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/publisher/fetch', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/publisher/fetch', { method: 'POST', timeoutMs: 30000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/sources/seed/catalogue', (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/seed/catalogue', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/sources/seed/run', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/sources/seed/run', { method: 'POST', timeoutMs: 60000, fallback: async () => res.status(503).json(_brainFallback()) }));
// R-F407 (2026-05-13) — Hallucination dashboard surface. Combines
// R-F401 self_claim_guard counters + R-F403 stream_guard_observer.
app.get('/api/aria/hallucination/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/hallucination/stats', { fallback: async () => res.status(503).json(_brainFallback()) }));
// Self-diagnostic (2026-04-18)
app.get('/api/aria/diagnostic/details', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/diagnostic/details', { timeoutMs: 30000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/diagnostic/run', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/diagnostic/run', { method: 'POST', timeoutMs: 30000, fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/calibration/review', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/calibration/review', { fallback: async () => res.status(503).json(_brainFallback()) }));
app.post('/api/aria/calibration/baseline', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/calibration/baseline', { method: 'POST', fallback: async () => res.status(503).json(_brainFallback()) }));
app.get('/api/aria/calibration/baseline', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/calibration/baseline', { fallback: async () => res.status(503).json(_brainFallback()) }));

// R-F2606 — pin user_id on RAG proxy bodies from the JWT (mirror of the
// /api/aria/document/verify routes) so a client cannot read/write another
// tenant's RAG namespace by supplying user_id. Non-admins are always forced to
// their own userId; an admin may target another user_id explicitly.
function _pinBodyUserId(req) {
  try {
    req.body = req.body || {};
    const isAdmin = req.user?.role === 'admin';
    if (!isAdmin || !req.body.user_id) {
      req.body.user_id = req.user?.userId || '';
    }
  } catch {}
}

app.post('/api/aria/rag/search',
  express.json({ limit: '256kb' }),
  requireAuth,
  (req, res) => { _pinBodyUserId(req); return ariaProxy(req, res, '/api/aria/rag/search', { method: 'POST', fallback: async () => {
    res.status(503).json({ results: [], error: 'RAG search unavailable' });
  }}); });

app.post('/api/aria/rag/ingest',
  express.json({ limit: '4mb' }),
  requireAuth,
  (req, res) => { _pinBodyUserId(req); return ariaProxy(req, res, '/api/aria/rag/ingest', { method: 'POST', fallback: async () => {
    res.status(503).json({ ingested: false, error: 'RAG ingest unavailable' });
  }}); });

app.post('/api/aria/rag/backfill', requireAuth, (req, res) => {
  _pinBodyUserId(req);
  return ariaProxy(req, res, '/api/aria/rag/backfill', { method: 'POST', fallback: async () => {
    res.status(503).json({ ok: false, error: 'RAG backfill unavailable' });
  }});
});

// ── ARIA reasoning independence (proxy) — the ARIA-LLM trajectory metric ───
app.get('/api/aria/independence', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/independence', { fallback: async () => {
    res.status(503).json({ error: 'Independence report unavailable — ARIA service offline' });
  }}));

app.get('/api/aria/reasoning-library/stats', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/reasoning-library/stats', { fallback: async () => {
    res.status(503).json({ error: 'Reasoning library stats unavailable' });
  }}));

app.post('/api/aria/reasoning-library/find', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/reasoning-library/find', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Reasoning library lookup unavailable' });
  }}));

app.post('/api/aria/reasoning-library/feedback', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/reasoning-library/feedback', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Reasoning library feedback unavailable' });
  }}));

app.post('/api/aria/reasoning-library/consolidate', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/reasoning-library/consolidate', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Reasoning library consolidate unavailable' });
  }}));

app.post('/api/aria/reasoning/test', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/reasoning/test', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Reasoning test unavailable' });
  }}));

app.get('/api/aria/training-data/library-export', requireAdmin, (req, res) =>
  ariaProxy(req, res, '/api/aria/training-data/library-export', { fallback: async () => {
    res.status(503).json({ error: 'Library export unavailable' });
  }}));

// ── ARIA student mode (active learning) ────────────────────────────────────
// R-F2383 — removed dead duplicate GET /api/aria/student/stats + /student/mastery
// (both registered earlier at their first definitions, which win; these never fired).
app.get('/api/aria/student/curriculum', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/curriculum', { fallback: async () => {
    res.status(503).json({ error: 'Curriculum unavailable' });
  }}));

app.post('/api/aria/student/quiz', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/quiz', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Quiz unavailable' });
  }}));

app.post('/api/aria/student/study', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/student/study', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Study session unavailable' });
  }}));

// ── ARIA fuzzy sanctions / conflict / tech classifier (proxy) ───────────────
app.post('/api/aria/sanctions/fuzzy', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/sanctions/fuzzy', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Fuzzy sanctions screen unavailable — ARIA service offline', matches: [] });
  }}));

app.get('/api/aria/conflict/events/:country', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/conflict/events/${encodeURIComponent(req.params.country)}${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => {
    res.status(503).json({ error: 'Conflict tracker unavailable — ARIA service offline', total_events: 0 });
  }}));

app.get('/api/aria/conflict/correlate/:country', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/conflict/correlate/${encodeURIComponent(req.params.country)}${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => {
    res.status(503).json({ error: 'Conflict correlation unavailable — ARIA service offline' });
  }}));

app.post('/api/aria/tech/classify', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/tech/classify', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Tech classifier unavailable — ARIA service offline' });
  }}));

app.get('/api/aria/tech/explain/:designation', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/tech/explain/${encodeURIComponent(req.params.designation)}`, { fallback: async () => {
    res.status(503).json({ error: 'Tech explainer unavailable — ARIA service offline' });
  }}));

app.get('/api/aria/knowledge/contradictions', requireAuth, (req, res) =>
  ariaProxy(req, res, `/api/aria/knowledge/contradictions${req.url.includes('?') ? req.url.slice(req.url.indexOf('?')) : ''}`, { fallback: async () => {
    res.status(503).json({ error: 'Contradictions API unavailable — ARIA service offline', contradictions: [] });
  }}));

// ── ARIA deep research endpoints (proxy) ────────────────────────────────────
app.post('/api/aria/investigate', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/investigate', {
    method: 'POST',
    timeoutMs: 200000,  // deep investigation can take 2-3 min
    fallback: async () => {
      res.status(503).json({ error: 'Investigation unavailable — ARIA service offline' });
    },
  }));

app.post('/api/aria/crawl', requireAuth, (req, res) =>
  // Crawls are slow — fly.io's crawl_website can take up to 150s on a
  // JS-heavy site with 20 pages. Give the proxy 200s so we're comfortably
  // above the typical worst case. Falls back to 503 only if fly.io is
  // actually down (not just slow).
  ariaProxy(req, res, '/api/aria/crawl', {
    method: 'POST',
    timeoutMs: 200000,
    fallback: async ({ lastStatus, lastErr } = {}) => {
      res.status(503).json({
        error: 'Crawl unavailable — ARIA service offline',
        fly_status: lastStatus || 0,
        fly_error: (lastErr || '').slice(0, 200),
      });
    },
  }));

app.post('/api/aria/profile', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/profile', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Profile build unavailable — ARIA service offline' });
  }}));

app.post('/api/aria/investigate/person', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/investigate/person', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Person investigation unavailable — ARIA service offline' });
  }}));

app.post('/api/aria/investigate/company', requireAuth, (req, res) =>
  ariaProxy(req, res, '/api/aria/investigate/company', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'Company investigation unavailable — ARIA service offline' });
  }}));

// Training Data API (for future proprietary LLM)
app.get('/api/aria/training-data/stats', requireAdmin, async (req, res) => {
  ariaProxy(req, res, '/api/aria/training-data/stats', { fallback: async () => {
    try {
      const { getTrainingStats } = await import('./lib/aria/training_data.mjs');
      res.json(getTrainingStats());
    } catch (e) { res.json({ conversations: 0, error: e.message }); }
  }});
});

app.get('/api/aria/training-data/export', requireAdmin, async (req, res) => {
  ariaProxy(req, res, `/api/aria/training-data/export${req.query.format ? '?format=' + req.query.format : ''}`, { fallback: async () => {
    try {
      const { exportTrainingData } = await import('./lib/aria/training_data.mjs');
      const data = exportTrainingData();
      if (req.query.format === 'jsonl') {
        res.setHeader('Content-Type', 'application/jsonl');
        res.setHeader('Content-Disposition', 'attachment; filename="aria_training_data.jsonl"');
        res.send(data.data.map(d => JSON.stringify(d)).join('\n'));
      } else {
        res.json(data);
      }
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// PDF Report Generation
app.get('/api/report/monthly', requireAuth, async (req, res) => {
  try {
    const { generateMonthlyBrief } = await import('./lib/reports/pdf_generator.mjs');
    const data = currentData || {};
    const pdf = await generateMonthlyBrief(data);
    const month = new Date().toISOString().slice(0, 7);
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="ARKMURUS_Brief_${month}.pdf"`);
    res.send(pdf);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/report/approach', requireAuth, async (req, res) => {
  try {
    const { market, product } = req.body || {};
    if (!market) return res.status(400).json({ error: 'market required' });
    const { generateApproachPack } = await import('./lib/reports/pdf_generator.mjs');
    const { generateApproach } = await import('./lib/aria/approach.mjs');
    const { generateGTMStrategy } = await import('./lib/aria/gtm_strategy.mjs');
    const { getContactsByCountry } = await import('./lib/aria/contacts.mjs');
    const approach = generateApproach(market, product || '', '');
    const gtm = generateGTMStrategy(market);
    const contacts = getContactsByCountry(market);
    const pdf = await generateApproachPack(market, product || '', approach, gtm, contacts);
    res.setHeader('Content-Type', 'application/pdf');
    res.setHeader('Content-Disposition', `attachment; filename="ARKMURUS_Approach_${market}.pdf"`);
    res.send(pdf);
  } catch (e) { res.status(500).json({ error: e.message }); }
});

// Go-To-Market Strategy API
app.get('/api/aria/gtm/:market', requireAuth, async (req, res) => {
  ariaProxy(req, res, `/api/aria/gtm/${encodeURIComponent(req.params.market)}`, { fallback: async () => {
    try {
      const { generateGTMStrategy } = await import('./lib/aria/gtm_strategy.mjs');
      const strategy = generateGTMStrategy(req.params.market);
      res.json(strategy || { error: 'Market not found' });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// On-demand research trigger — immediately explores a specific topic
// NOTE: Research still uses Node.js web_explorer locally (not proxied) because it needs local LLM + explorer
app.post('/api/aria/research', requireAuth, async (req, res) => {
  try {
    const { topic, market } = req.body || {};
    if (!topic) return res.status(400).json({ error: 'topic required' });
    const safe = s => (s || '').trim().slice(0, 100).replace(/['"<>]/g, '');
    const t = safe(topic);
    const m = safe(market);
    const queries = [
      t + ' defence procurement 2026',
      t + ' military tender contract',
      (m ? m + ' ' : '') + t + ' latest news',
    ];
    const { runExploration } = await import('./lib/self/web_explorer.mjs');
    const findings = await runExploration(llmProvider, { queries });
    // Store findings to both local and Python knowledge bases
    try {
      const { storeFact, recordQuery } = await import('./lib/aria/knowledge.mjs');
      for (const ins of (findings.insights || []).slice(0, 3)) {
        storeFact(topic + ' — ' + (ins.title || '').slice(0, 40), (ins.summary || ins.title || '').slice(0, 300), 'research', 'ASSESSED');
        // Also store in Python service
        if (ARIA_SERVICE_URL) {
          fetch(`${ARIA_SERVICE_URL}/api/aria/knowledge/fact`, {
            method: 'POST', headers: _ariaHeaders(),
            body: JSON.stringify({ topic: topic + ' — ' + (ins.title || '').slice(0, 40), content: (ins.summary || ins.title || '').slice(0, 300), confidence: 'ASSESSED' }),
          }).catch((_kf) => { console.warn('[R-F1638] knowledge/fact POST failed:', _kf?.message); });
        }
      }
      recordQuery(topic, (findings.insights?.[0]?.summary || '').slice(0, 200), market || '');
    } catch {}
    res.json({ ok: true, insights: findings.insights?.length || 0, salesIdeas: findings.salesIdeas?.length || 0, findings });
  } catch (e) {
    // R-F2182 — wire the LOCAL research-path failure to the brain (was DARK: a
    // 500 with no brain signal). This path runs runExploration locally and serves
    // the user directly, so the brain otherwise never learns it failed.
    try { errorTracker.record('aria_research', 'research_failed', e); } catch { /* best-effort */ }
    res.status(500).json({ error: e.message });
  }
});

// Proxy: POST /api/aria/read — Let ARIA read a URL and extract intelligence
app.post('/api/aria/read', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/read', { method: 'POST', fallback: async () => {
    res.status(503).json({ error: 'ARIA service unavailable — /api/aria/read requires the Python aria_service' });
  }});
});

app.post('/api/aria/knowledge/learn', requireAuth, async (req, res) => {
  ariaProxy(req, res, '/api/aria/knowledge/learn', { method: 'POST', fallback: async () => {
    try {
      const { correction, context } = req.body || {};
      if (!correction) return res.status(400).json({ error: 'correction required' });
      const { storeLearning } = await import('./lib/aria/knowledge.mjs');
      storeLearning(correction, context || '');
      res.json({ ok: true, message: 'Learning stored' });
    } catch (e) { res.status(500).json({ error: e.message }); }
  }});
});

// ARIA chat — Python service primary, local LLM fallback
app.post('/api/aria/chat', requireAuth, async (req, res) => {
  const { message, session_id, skip_aria_service } = req.body || {};
  if (!message) return res.status(400).json({ error: 'message required' });
  // R-F1687: stable per-account key (email-slug) so this path buckets
  // conversations under the account, identical to /chat/stream + the sidebar.
  const _stableUid = stableUserId(req);
  const sid = session_id || `${_stableUid || req.user?.userId || 'anon'}_${Date.now()}`;
  // §25a (R-F1565) — delivery-outcome instrumentation for the MAIN web answer
  // path so the brain knows whether a web user actually received a real answer.
  const _outT0 = Date.now();
  const _outReqId = (req.headers['x-request-id'] || `web_${sid}`).toString();
  // R-F48b: resolve persona from authenticated user record so the
  // Python brain picks the right overlay. Empty string = let Python
  // default to broker (current behaviour for legacy users w/o sector).
  let _persona = '';
  let _personaUserId = req.user?.userId || '';
  try {
    if (_personaUserId) {
      const u = findUserById(_personaUserId);
      if (u && u.sector) _persona = String(u.sector).trim();
    }
  } catch {}

  // ── Trivial-question short-circuit (highest priority) ─────────────────
  // Greetings, liveness probes, identity questions, 'test'/'ping', 'thanks'
  // never go anywhere near the LLM stack, fly.io, or any context layer.
  // Past incident 2026-04-08: 'Aria, are you online?' was hitting the local
  // ariaLocalChat fallback which built 7 layers of Angola/Lusophone context
  // and the LLM (steered by the heavy Lusophone system prompt) returned the
  // same Angola briefing for every greeting. Mirrored from the Python-side
  // fix in aria_service/intel/reasoning_library.trivial_reply().
  const _trivial = trivialReply(message);
  if (_trivial !== null) {
    console.log(`[chat] trivial short-circuit (server.mjs): ${message.slice(0, 80)} → fixed reply`);
    return res.json({
      response: _trivial,
      session_id: sid,
      service: 'trivial',
      engine: 'short-circuit',
    });
  }

  // R-F2765 — enforce the tier messages/day cap (skipped for system/internal
  // callers, e.g. the WhatsApp listener on the internal token). AFTER the trivial
  // short-circuit so greetings / liveness probes never count against quota.
  const _mq = await _quotaBlock(req, 'message');
  if (_mq) return res.status(429).json({ error: _mq.reason, quota: { current: _mq.current, cap: _mq.cap } });

  // Persist session to Redis for cross-browser recovery
  const sessionKey = `crucix:chat:session:${sid}`;

  // Try Python ARIA service first (has its own LLM + 8-layer context + neural memory).
  //
  // Timeout layering (2026-04-08 round 5d, finalised):
  //   waListener.askARIA  → 240s   (outermost, user-facing)
  //   server.mjs → fly.io → 300s   (this line, must EXCEED outer)
  //   fly.io chat_ep      → 120s on the LLM call inside
  //
  // The inner timeout MUST be larger than the outer or network hops + JSON
  // serialization eat the difference and Python gets aborted right when it's
  // about to return. 60-second buffer is comfortable on a 4-minute budget.
  //
  // History:
  //   round 5c (441d02d): bumped 90s → 240s. That fixed the silent fallback
  //     to ariaLocalChat for short questions but not for the Ghana question
  //     because the OUTER waListener timeout was still 180s and fired first.
  //   round 5d (this):    aligned chain — waListener at 240s, server.mjs at
  //     300s with explicit headroom comment.
  // 2026-04-26: skip_aria_service is set by the WhatsApp listener fallback
  // when its streaming attempt already failed at the transport layer (fly.io
  // unreachable / DNS / abort). Retrying the same fly.io path through /chat
  // would just burn another 6 minutes for the same "fetch failed". Honor
  // the hint and go straight to the local Node fallback.
  if (ARIA_SERVICE_URL && !skip_aria_service) {
    try {
      // R-F1615: async-complete-and-push (server-side poll) instead of ONE
      // synchronous 600s fetch. The old single connection was torn down by any
      // brain deploy/restart mid-answer → browser 502. Now we dispatch an async
      // job (job_id in <1s) and poll the brain's /chat/result endpoint with the
      // SAME total budget — but each poll is a short request, so a mid-deploy
      // blip only costs one retried tick, not the whole answer.
      //
      // R-F525 (2026-05-14): total budget is 600s (env ARIA_CHAT_PROXY_TIMEOUT_MS).
      // Full DD on a URL takes 5-10 min with DeepSeek synthesis; the WA listener
      // long budget matches (services/wa-listener/aria_wa_listener.mjs) so the
      // outer listener abort doesn't fire before this completes.
      const data = await _ariaChatAsyncPoll(message, sid, _stableUid, _persona);
      if (data && (data.response || data.answer)) {
        data.service = 'python';
        data.engine = 'aria-8layer';
        // Persist to Redis
        try { await redisAdapter.hset?.(sessionKey, 'lastMessage', message, 'lastResponse', (data.response || data.answer)?.slice(0, 500) || '', 'updatedAt', new Date().toISOString()); } catch {}
        // §25a (R-F1565) — record the TRUE outcome. R-F1965: a DEGRADED non-answer
        // (data.degraded / data.llm_failure — the brain's "Cannot reason…" fallback)
        // must NOT be logged as delivered_real_answer, or the brain stays blind to
        // its own failure (today's aria_llm outage was invisible this way).
        reportOutcome('web', _outReqId, 'chat_answer', classifyDeliveryOutcome(data),
                      Date.now() - _outT0, degradedDetail(data));
        return res.json(data);
      }
      // No usable answer (e.g. brain w/o async support returned nothing) —
      // fall through to the legacy paths below.
      console.warn('[ARIA] Python async chat returned no answer — falling through to local.');
    } catch (e) {
      console.warn('[ARIA] Python service unreachable, trying brain/local:', e.message);
      // §25a (R-F1565) — primary guarded path errored; record so the brain
      // knows the web user did NOT get a guarded answer (fell to fallback).
      reportOutcome('web', _outReqId, 'chat_answer', 'error', Date.now() - _outT0, e?.message);
    }
  } else if (skip_aria_service) {
    console.warn('[ARIA] skip_aria_service=true — bypassing fly.io, going straight to local Node fallback');
  }

  // Try old Flask brain service
  if (BRAIN_URL) {
    try {
      // R-F3661: was unauthenticated. BRAIN_URL and ARIA_SERVICE_URL carry the
      // SAME value in production (both point at aria-intel), so this "old Flask
      // brain" fallback was hitting the Bearer-gated brain with no token and
      // could only ever 401 — a fallback that could never catch anything.
      const r = await fetch(`${BRAIN_URL}/api/aria/chat`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({ message, session_id: sid }),
        signal: AbortSignal.timeout(300000),  // 5 minutes — must exceed waListener 240s
      });
      if (r.ok) {
        const data = await r.json();
        data.service = 'flask';
        data.engine = 'brain-legacy';
        return res.json(data);
      }
    } catch (e) { console.warn('[ARIA] brain service unreachable, using local LLM:', e.message); }
  }

  // Local Node.js LLM fallback
  // 2026-04-08 round 5c: this was the silent culprit. Falling through to the
  // local fallback bypasses the entire fly.io fix chain (officeholder_guard,
  // source_verifier, confidence_footer, constitution clauses 9-11). Log
  // EXPLICITLY when we hit this path so the failure mode is visible in logs.
  //
  // R-F156 (2026-05-10) — F8 Stage 1: the existing result.warning field was
  // invisible to WhatsApp users (the listener at waListener.mjs:2596 pulls
  // data.response only). Stage 1 added visible banner + ERROR log + counter.
  //
  // R-F160 (2026-05-10) — F8 Stage 2 (operator authorised): refuse DD-style
  // queries on fallback path. The unguarded local LLM is acceptable for
  // chitchat / liveness / quick lookups — it is NOT acceptable for compliance
  // /  due-diligence / sanctions / counterparty-investigation queries which
  // require the full fly.io guard chain (constitution + output guards + DD
  // orchestrator + verification gate + sanctions claim guard + citation
  // enforcement). Refuse-with-honest-explanation > unguarded-but-polished.
  const _ddIntentPattern = /\b(due\s+diligence|\bdd\b|investigate|background\s+check|compliance\s+check|sanctions?\s+(?:check|screen)|verify\s+(?:this|the)\s+(?:company|entity|person)|kyc|aml|vetting|risk\s+(?:assessment|profile)|run\s+(?:a\s+)?(?:full\s+)?(?:dd|check)|screen\s+(?:this|the)|trace\s+(?:ubo|ownership)|adverse\s+media)\b|https?:\/\//i;
  if (_ddIntentPattern.test(message)) {
    globalThis.__ariaUnguardedFallbackCount = (globalThis.__ariaUnguardedFallbackCount || 0) + 1;
    globalThis.__ariaUnguardedFallbackLast = new Date().toISOString();
    globalThis.__ariaUnguardedFallbackRefusedCount = (globalThis.__ariaUnguardedFallbackRefusedCount || 0) + 1;
    console.error(
      `[ARIA] ⚠ UNGUARDED FALLBACK REFUSED — fly.io brain unreachable AND message contains ` +
      `DD/compliance/investigation intent. Refusing rather than producing unguarded reply. ` +
      `Cumulative refused: ${globalThis.__ariaUnguardedFallbackRefusedCount}. ` +
      `Message preview: ${message.slice(0, 120)}`
    );
    return res.json({
      response: (
        '⚠ I cannot answer this query right now.\n\n' +
        'Your message looks like a due-diligence / compliance / investigation ' +
        'request — those queries need to run through fly.io ARIA so the constitutional ' +
        'guards, sanctions checks, DD orchestrator, citation enforcement and verification ' +
        'gate can fire. fly.io ARIA is currently unreachable, and I will NOT produce a ' +
        'compliance/DD response from the unguarded local fallback (the response would ' +
        'bypass every safety control and could quote fabricated facts about real entities).\n\n' +
        'Please retry in 60-90 seconds — fly.io is usually back within a minute. ' +
        'If the issue persists for >5 minutes, check fly.io status: ' +
        '`fly status -a aria-intel`.'
      ),
      service: 'local',
      engine: 'node-fallback-refused',
      refused: true,
      refused_reason: 'dd_compliance_intent_detected_on_unguarded_fallback',
      warning: 'F8 Stage 2 (R-F160): unguarded fallback refused for DD/compliance intent',
    });
  }
  globalThis.__ariaUnguardedFallbackCount = (globalThis.__ariaUnguardedFallbackCount || 0) + 1;
  globalThis.__ariaUnguardedFallbackLast = new Date().toISOString();
  console.error(
    `[ARIA] ⚠ UNGUARDED FALLBACK to local Node LLM — ALL fly.io guards bypassed ` +
    `(officeholder/commitment/tool_claim/propaganda/ground_truth/sanctions guards SKIPPED, ` +
    `DD orchestrator NOT invoked, verification gate NOT invoked). ` +
    `Cumulative since process start: ${globalThis.__ariaUnguardedFallbackCount}. ` +
    `Message preview: ${message.slice(0, 120)}`
  );
  const result = await ariaLocalChat(message, sid, llmProvider, currentData);
  result.service = 'local';
  result.engine = 'node-fallback';
  result.warning = 'Reply generated by local Node fallback — fly.io guards bypassed. May contain unverified claims.';
  result.unguarded = true;
  // R-F156: prepend visible warning banner to whichever response field
  // the consumer reads. WA listener uses .response; web chat may use
  // .answer or .text. Cover all three.
  const _banner = (
    '[⚠ UNGUARDED FALLBACK — fly.io brain was unreachable. This reply was ' +
    'generated by the local Node fallback and has NOT been through ARIA\'s ' +
    'constitutional / DD / sanctions / citation guards. Treat as draft, not ' +
    'as a vetted ARIA response. Retry the same query in 60s once fly.io ' +
    'recovers for a guarded reply.]\n\n'
  );
  for (const _f of ['response', 'answer', 'text']) {
    if (typeof result[_f] === 'string' && result[_f].length > 0 && !result[_f].startsWith('[⚠ UNGUARDED')) {
      result[_f] = _banner + result[_f];
    }
  }
  // §25a (R-F1565) — the user got only the UNGUARDED local fallback, not a real
  // guarded ARIA answer. Report as a non-success outcome so the brain treats it
  // as a delivery degradation it can self-heal from (fly.io was unreachable).
  reportOutcome('web', _outReqId, 'chat_answer', 'error', Date.now() - _outT0, 'unguarded_local_fallback');
  res.json(result);
});

// R-F156 — operator-facing visibility for the unguarded-fallback counter.
// Lightweight: in-memory counter; resets on process restart. Sufficient for
// Phase A diagnostics. If durability matters later, move to Redis.
app.get('/api/aria/unguarded-fallback/stats', requireAuth, (req, res) => {
  if (!globalThis.__ariaProcessStart) globalThis.__ariaProcessStart = new Date().toISOString();
  res.json({
    cumulative_count: globalThis.__ariaUnguardedFallbackCount || 0,
    last_fired_at: globalThis.__ariaUnguardedFallbackLast || null,
    process_start: globalThis.__ariaProcessStart,
    note: (
      'Counts UNGUARDED-FALLBACK firings since process start (in-memory). ' +
      'Each firing = a chat turn where fly.io ARIA was unreachable and the ' +
      'response was generated by the local Node LLM bypassing all fly.io ' +
      'guards. High count or recent firing = investigate fly.io reachability.'
    ),
  });
});

// ARIA chat streaming — SSE proxy to Python ARIA service
app.post('/api/aria/chat/stream', requireAuth, async (req, res) => {
  const { message, session_id, auto_tools, group_context, keep_history } = req.body || {};
  if (!message) return res.status(400).json({ error: 'message required' });
  // R-F1687: stable per-account key (email-slug) — bucket conversations under
  // the account, matching the slug the sidebar lists with.
  const _stableUid = stableUserId(req);
  const sid = session_id || `${_stableUid || req.user?.userId || 'anon'}_${Date.now()}`;
  // R-F2202: pin the user's email (JWT-resolved, not trusted from the client) so a
  // DD run via CHAT shares to same-company colleagues like the /dd/orchestrate button
  // (R-F608). The JWT carries no email, so look it up from the user store by id.
  let _userEmail = '';
  let _userTier = '';   // R-F2767 — forwarded to Python for per-tier Claude cost attribution
  try {
    const _u2202 = findUserById(req.user?.userId || '');
    _userEmail = String(_u2202?.email || '').trim();
    _userTier = String(_u2202?.tier || '').trim();
  } catch {}
  // R-F48b: resolve persona from authenticated user record (sector
  // field captured at registration). Empty → Python falls back to
  // broker overlay = current default behaviour.
  let _persona = '';
  try {
    if (req.user?.userId) {
      const u = findUserById(req.user.userId);
      if (u && u.sector) _persona = String(u.sector).trim();
    }
  } catch {}

  // R-F1697 §25/§25a — delivery-outcome proprioception for the WEB STREAMING
  // chat path (the limb the UI actually uses). Pre-fix this path was DARK: only
  // the non-stream /chat reported outcomes, so the brain never knew whether a
  // web chat user received a real answer / hit an error / lost the brain
  // mid-deploy. Now every terminal state reports to /api/aria/outcome (success
  // AND failure → failure records a gap → self-heal). Idempotent: fires once.
  const _outT0 = Date.now();
  const _outReqId = (req.headers['x-request-id'] || `web_stream_${sid}`).toString();
  let _outReported = false;
  function _reportChat(outcome, detail) {
    if (_outReported) return;
    _outReported = true;
    reportOutcome('web', _outReqId, 'chat_answer', outcome, Date.now() - _outT0, detail);
  }

  // Trivial short-circuit — no need to call Python for greetings
  const _trivial = trivialReply(message);
  if (_trivial !== null) {
    res.setHeader('Content-Type', 'text/event-stream');
    res.setHeader('Cache-Control', 'no-cache');
    res.setHeader('X-Accel-Buffering', 'no');
    res.setHeader('Connection', 'keep-alive');
    res.flushHeaders();
    res.write(`data: ${JSON.stringify({type:'chunk',text:_trivial})}\n\n`);
    res.write(`data: ${JSON.stringify({type:'done',session_id:sid,trivial:true})}\n\n`);
    _reportChat('delivered_real_answer', 'trivial');
    return res.end();
  }

  // R-F2765 — enforce the tier messages/day cap BEFORE flushing SSE headers, so a
  // limit hit returns a clean JSON 429 rather than a half-open stream. System /
  // internal callers (no JWT userId) are exempt.
  const _mq = await _quotaBlock(req, 'message');
  if (_mq) {
    _reportChat('send_failed', 'quota_exceeded');
    return res.status(429).json({ error: _mq.reason, quota: { current: _mq.current, cap: _mq.cap } });
  }

  if (!ARIA_SERVICE_URL) {
    // No Python service — fall back to non-streaming local
    _reportChat('send_failed', 'no_aria_service');
    return res.status(503).json({ error: 'Streaming requires ARIA Python service' });
  }

  // Set SSE headers
  res.setHeader('Content-Type', 'text/event-stream');
  res.setHeader('Cache-Control', 'no-cache');
  res.setHeader('X-Accel-Buffering', 'no');
  res.setHeader('Connection', 'keep-alive');
  res.flushHeaders();

  try {
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat/stream`, {
      method: 'POST',
      headers: _ariaHeaders(),
      body: JSON.stringify({
        message,
        session_id: sid,
        user_id: _stableUid,   // R-F1687: was `req.user?.id` (undefined → '')
        ...(_userEmail ? { user_email: _userEmail } : {}),   // R-F2202: chat-DD company sharing
        ...(_userTier ? { user_tier: _userTier } : {}),      // R-F2767: per-tier Claude cost attribution
        persona: _persona,
        auto_tools: auto_tools !== false,
        group_context: group_context || '',
        // R-F1691 #7 — edit-&-resend: trim backend history to N prior messages.
        ...(Number.isInteger(keep_history) && keep_history >= 0 ? { keep_history } : {}),
      }),
      // R-F525 (2026-05-14): 300s → 600s, env-tunable. Streaming DD
      // requests can run 5-10 min; we don't want the outer fetch to
      // abort before the SSE stream completes its final `done` event.
      signal: AbortSignal.timeout(parseInt(process.env.ARIA_STREAM_PROXY_TIMEOUT_MS || '600000', 10)),
    });

    if (!r.ok) {
      const errBody = await r.text().catch(() => '');
      res.write(`data: ${JSON.stringify({type:'error',message:`Python ${r.status}: ${errBody.slice(0,200)}`})}\n\n`);
      _reportChat('error', `python_${r.status}`);   // R-F1697 §25
      return res.end();
    }

    // Pipe the SSE stream from Python directly to the browser
    const reader = r.body.getReader();
    const decoder = new TextDecoder();
    let _sawContent = false;   // R-F1697 — did a real answer chunk reach the user?
    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const chunk = decoder.decode(value, { stream: true });
        if (!_sawContent && containsAnswerChunk(chunk)) _sawContent = true;
        res.write(chunk);
      }
      // Stream finished cleanly — report whether real content was delivered.
      _reportChat(_sawContent ? 'delivered_real_answer' : 'error', _sawContent ? '' : 'empty_stream');
    } catch (e) {
      // Client disconnected or stream error mid-pipe.
      if (!res.writableEnded) {
        res.write(`data: ${JSON.stringify({type:'error',message:e.message})}\n\n`);
      }
      _reportChat(_sawContent ? 'delivered_real_answer' : 'send_failed', 'stream_interrupted: ' + (e?.message || ''));
    }
  } catch (e) {
    // Proxy-level failure — most commonly the brain restarting mid-deploy. The
    // brain must FEEL this (§25): the web limb could not deliver.
    console.warn('[ARIA] Stream proxy error:', e.message);
    if (!res.writableEnded) {
      res.write(`data: ${JSON.stringify({type:'error',message:e.message})}\n\n`);
    }
    _reportChat('send_failed', 'proxy_error: ' + (e?.message || ''));
  }

  if (!res.writableEnded) res.end();
});

// Session recovery — get conversation history from Python ARIA's Redis
app.get('/api/aria/session/:sessionId', requireAuth, async (req, res) => {
  const sid = req.params.sessionId;
  if (ARIA_SERVICE_URL) {
    try {
      // Python ARIA stores sessions in Redis under crucix:aria:session:{sid}
      const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({ message: '__session_recovery__', session_id: sid }),
        signal: AbortSignal.timeout(5000),
      });
      // For now, just confirm the session exists
    } catch {}
  }
  res.json({ session_id: sid, note: 'Session persisted in ARIA service Redis (24h TTL)' });
});

// ── Conversation History CRUD (proxy to Python ARIA service) ─────────────

app.get('/api/aria/conversations', requireAuth, async (req, res) => {
  // R-F116 (2026-05-09): was reading req.user?.id (always undefined —
  // JWT payload field is userId, not id) so every conversations fetch
  // returned 401 → aria.html showed 'Failed to load.' in the convos
  // panel. Aligning with the rest of the handlers in this file.
  // R-F1687: server-authoritative email-slug — identical to the write-side
  // bucket (stableUserId in /chat + /chat/stream), so LIST and WRITE always
  // address the same `crucix:aria:conversations:{slug}` key. Also closes the
  // pre-fix info-leak where this endpoint trusted the client's ?user_id.
  const userId = stableUserId(req);
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const offset = parseInt(req.query.offset) || 0;
  const limit = parseInt(req.query.limit) || 30;
  // R-F1778: `fallback` MUST be a function — ariaProxy invokes it as
  // `fallback({lastStatus,lastErr})`. R-F1687 mistakenly passed a plain OBJECT
  // here, so on EVERY aria-intel non-2xx/throw/timeout (cold-start, brain
  // wedge, transient 5xx) ariaProxy threw `TypeError: fallback is not a
  // function` → Express 5 default handler → HTTP 500 → the sidebar painted
  // "Failed to load conversations: HTTP 500" instead of degrading gracefully.
  // Now we emit a clean 503 carrying an empty list + the fly diagnostics so the
  // FE can tell "backend briefly down, retry" apart from "genuinely no chats".
  await ariaProxy(req, res, `/api/aria/conversations?user_id=${userId}&offset=${offset}&limit=${limit}`, {
    fallback: async ({ lastStatus, lastErr } = {}) => res.status(503).json({
      error: 'ARIA service unavailable',
      conversations: [],
      user_id: userId,
      fly_status: lastStatus,
      fly_error: lastErr,
    }),
  });
});

// R-F1813 (audit C1): explicit pinned routes for conversation export/search +
// admin/brain. Without these, export/search are shadowed by /:sessionId and
// admin/brain falls to the catch-all (which forwards the client query string),
// letting an attacker pass ?user_id=<victim> and defeat the brain ownership check.
// Pin user_id from the JWT and STRIP any client-supplied user_id.
app.get('/api/aria/conversations/export', requireAuth, async (req, res) => {
  const userId = stableUserId(req);
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const sid = encodeURIComponent(req.query.session_id || '');
  const fmt = encodeURIComponent(req.query.format || 'json');
  await ariaProxy(req, res, `/api/aria/conversations/export?session_id=${sid}&format=${fmt}&user_id=${encodeURIComponent(userId)}`, { fallback: null });
});

app.get('/api/aria/conversations/search', requireAuth, async (req, res) => {
  const userId = stableUserId(req);
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const q = encodeURIComponent(req.query.q || '');
  const limit = encodeURIComponent(req.query.limit || '50');
  await ariaProxy(req, res, `/api/aria/conversations/search?q=${q}&limit=${limit}&user_id=${encodeURIComponent(userId)}`, { fallback: null });
});

app.get('/api/aria/admin/brain/:sessionId', requireAuth, async (req, res) => {
  const userId = stableUserId(req);
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  const sid = encodeURIComponent(req.params.sessionId);
  const query = encodeURIComponent(req.query.query || '');
  await ariaProxy(req, res, `/api/aria/admin/brain/${sid}?query=${query}&user_id=${encodeURIComponent(userId)}`, { fallback: null });
});

app.get('/api/aria/conversations/:sessionId', requireAuth, async (req, res) => {
  // R-F606 (2026-05-16): forward the JWT-derived user_id to the Python
  // backend so it can enforce ownership. Pre-R-F606 we proxied only the
  // session_id and Python returned the conversation unconditionally.
  // R-F3831 — validate BEFORE the token-bearing proxy call, then encode. Raw
  // interpolation here let `..%2f..%2f` walk out of the conversations prefix and
  // reach any non-operator-gated brain path carrying _ariaHeaders().
  const sid = req.params.sessionId;
  if (!isValidSessionId(sid)) return rejectBadPathSegment(res, 'session id', sid);
  const userId = stableUserId(req);   // R-F1687: email-slug, matches write-side bucket
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  await ariaProxy(req, res, `/api/aria/conversations/${encodeURIComponent(sid)}/detail?user_id=${encodeURIComponent(userId)}`, {
    fallback: null,
  });
});

app.delete('/api/aria/conversations/:sessionId', requireAuth, async (req, res) => {
  // R-F3831 — see the GET handler above. This verb was the worst of the three:
  // a traversal here issued an attacker-chosen DELETE at the brain carrying the
  // service token, around pinNonAdminUserId and the R-F2775 infra gate.
  const sid = req.params.sessionId;
  if (!isValidSessionId(sid)) return rejectBadPathSegment(res, 'session id', sid);
  // R-F606 (2026-05-16): JWT field is `userId`, not `id` — pre-fix this
  // sent user_id='' on every delete, which combined with the store-layer
  // bug let any authenticated user destroy any other user's conversation
  // (zrem from your own empty set + unconditional delete of the target's
  // meta + session keys). Both halves fixed in this R-number.
  const userId = stableUserId(req);   // R-F1687: email-slug, matches write-side bucket
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'ARIA service unavailable' });
  try {
    const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/conversations/${encodeURIComponent(sid)}?user_id=${encodeURIComponent(userId)}`, {
      method: 'DELETE',
      headers: _ariaHeaders(),
      signal: AbortSignal.timeout(10000),
    });
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.put('/api/aria/conversations/:sessionId/title', requireAuth, async (req, res) => {
  // R-F3831 — see the GET handler above; this verb gave arbitrary brain WRITES.
  const sid = req.params.sessionId;
  if (!isValidSessionId(sid)) return rejectBadPathSegment(res, 'session id', sid);
  const { title } = req.body || {};
  if (!title) return res.status(400).json({ error: 'title required' });
  // R-F606 (2026-05-16): pin user_id to the JWT-resolved value, not to
  // whatever the client sent in the body. Python now enforces ownership.
  // R-F1687: use the stable email-slug so rename addresses the same bucket
  // as list/detail/write.
  const userId = stableUserId(req);
  if (!userId) return res.status(401).json({ error: 'Authentication required' });
  if (!ARIA_SERVICE_URL) return res.status(503).json({ error: 'ARIA service unavailable' });
  try {
    const r = await fetch(
      `${ARIA_SERVICE_URL}/api/aria/conversations/${encodeURIComponent(sid)}/title?user_id=${encodeURIComponent(userId)}`,
      {
        method: 'PUT',
        headers: _ariaHeaders(),
        body: JSON.stringify({ title }),
        signal: AbortSignal.timeout(10000),
      },
    );
    const data = await r.json();
    res.status(r.status).json(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.post('/api/aria/think', requireAuth, async (req, res) => {
  const { question, context, fast, session_id } = req.body || {};
  if (!question) return res.status(400).json({ error: 'question required' });
  // R-F2316 — pin the owner (email-slug, same bucket as /chat + delete) so the Python
  // /think handler can PERSIST the deep-analysis turn to this conversation. Forward the
  // client's session_id (the conversation the user is in). Never trust a client user_id.
  const _thinkUid = stableUserId(req);

  // Try Python ARIA service first.
  // Round 5c → 5d: bumped 90s → 240s → 300s. Inner timeout must exceed
  // outer waListener timeout (240s) by enough to absorb network hops.
  if (ARIA_SERVICE_URL) {
    try {
      const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/think`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({ question, context: context || {}, fast: fast || false, session_id: session_id || '', user_id: _thinkUid }),
        signal: AbortSignal.timeout(300000),
      });
      if (r.ok) return res.json(await r.json());
    } catch (e) { console.warn('[ARIA] Python think failed, trying brain/local:', e.message); }
  }

  // Try old Flask brain service
  if (BRAIN_URL) {
    try {
      // R-F3661: same unauthenticated legacy fallback as /api/aria/chat above.
      const r = await fetch(`${BRAIN_URL}/api/aria/think`, {
        method: 'POST',
        headers: _ariaHeaders(),
        body: JSON.stringify({ question, context: context || {}, fast: fast || false }),
        signal: AbortSignal.timeout(300000),
      });
      if (r.ok) return res.json(await r.json());
    } catch (e) { console.warn('[ARIA] brain think failed, using local LLM:', e.message); }
  }

  // Local Node.js LLM fallback
  const result = await ariaLocalThink(question, context || {}, llmProvider, currentData);
  res.json(result);
});

// ── ARIA Messaging — send WhatsApp & email via ARIA ─────────────────────────

// R-F2094 (2026-06-28 DD): requireAdmin, NOT requireAuth. Sends through the
// operator's connected WhatsApp session — with self-serve signup live, any
// auto-approved viewer could impersonate ARIA to arbitrary chats. Operator-only.
app.post('/api/aria/send-whatsapp', requireAdmin, async (req, res) => {
  const { group_id, chat_id, message, ask_aria, question } = req.body || {};
  const target = group_id || chat_id;

  let waSock;
  try {
    const { getWASock } = await import('./lib/whatsapp/waListener.mjs');
    waSock = getWASock();
  } catch {
    return res.status(503).json({ error: 'WhatsApp listener not available' });
  }

  if (!waSock.isConnected) {
    return res.status(503).json({ error: 'WhatsApp not connected' });
  }

  if (ask_aria && question) {
    const sid = `wa_api_${req.user?.id || 'admin'}_${Date.now()}`;
    let ariaReply;
    if (ARIA_SERVICE_URL) {
      try {
        const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat`, {
          method: 'POST',
          headers: _ariaHeaders(),
          body: JSON.stringify({ message: question, session_id: sid }),
          signal: AbortSignal.timeout(60000),
        });
        if (r.ok) { const d = await r.json(); ariaReply = d.response || d.answer; }
      } catch {}
    }
    if (!ariaReply) {
      const result = await ariaLocalChat(question, sid, llmProvider, currentData);
      ariaReply = result.response || result.answer;
    }
    if (!ariaReply) return res.status(502).json({ error: 'ARIA did not respond' });

    if (!target) return res.json({ ok: true, response: ariaReply, note: 'No target specified — response not sent to WhatsApp' });

    const ok = await waSock.sendMessage(target, `*ARIA* — ${ariaReply}`);
    return res.json({ ok, response: ariaReply, sent_to: target });
  }

  if (!target || !message) {
    return res.status(400).json({ error: 'group_id/chat_id and message required (or ask_aria + question)' });
  }
  const ok = await waSock.sendMessage(target, message);
  res.json({ ok, sent_to: target, length: message.length });
});

// R-F2094 (2026-06-28 DD): requireAdmin, NOT requireAuth. Sends from the company
// SMTP signed "ARIA — Arkmurus"; with self-serve signup live, requireAuth made
// this an OPEN EMAIL RELAY (any viewer → arbitrary recipients from our domain).
app.post('/api/aria/send-email', requireAdmin, async (req, res) => {
  const { to, subject, text, html, instruction, original_subject, original_body, cc, bcc } = req.body || {};

  let emailSend;
  try {
    const { sendEmail } = await import('./lib/aria/emailReader.mjs');
    emailSend = sendEmail;
  } catch {
    return res.status(503).json({ error: 'Email module not available' });
  }

  if (!to) return res.status(400).json({ error: 'to required' });

  if (instruction) {
    const sid = `email_${req.user?.id || 'admin'}_${Date.now()}`;
    const prompt = `Compose a professional email as ARIA on behalf of Arkmurus.
TO: ${to}
${original_subject ? `ORIGINAL SUBJECT: ${original_subject}` : ''}
${original_body ? `ORIGINAL EMAIL:\n${original_body.slice(0, 2000)}` : ''}
INSTRUCTION: ${instruction}
Write the email body only. Be concise and professional. Sign off as "ARIA — Arkmurus Intelligence".`;

    let composedBody;
    if (ARIA_SERVICE_URL) {
      try {
        const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat`, {
          method: 'POST',
          headers: _ariaHeaders(),
          body: JSON.stringify({ message: prompt, session_id: sid }),
          signal: AbortSignal.timeout(60000),
        });
        if (r.ok) { const d = await r.json(); composedBody = d.response || d.answer; }
      } catch {}
    }
    if (!composedBody) {
      const result = await ariaLocalChat(prompt, sid, llmProvider, currentData);
      composedBody = result.response || result.answer;
    }
    if (!composedBody) return res.status(502).json({ error: 'ARIA failed to compose email' });

    const emailSubject = subject || (original_subject ? `Re: ${original_subject}` : 'Arkmurus Intelligence Update');
    const result = await emailSend({ to, subject: emailSubject, text: composedBody, cc, bcc });
    return res.json({ ok: result.sent, messageId: result.messageId, subject: emailSubject, body: composedBody, to });
  }

  if (!subject) return res.status(400).json({ error: 'subject required' });
  const result = await emailSend({ to, subject, text, html, cc, bcc });
  if (result.sent) {
    res.json({ ok: true, messageId: result.messageId });
  } else {
    res.status(500).json({ error: result.reason });
  }
});

// ── Self-update API ───────────────────────────────────────────────────────────

app.get('/api/self/staged', requireAdmin, (req, res) => {
  res.json({ staged: getStagedModules() });
});

app.post('/api/self/generate', requireAdmin, async (req, res) => {
  const { description } = req.body || {};
  // R-F1887 (review Class D): sanitize moduleName — it flows into
  // ./sources/${name}.mjs file paths (path traversal). R-F1867 fixed /apply +
  // /rollback but missed /generate.
  const moduleName = _sanitizeModuleName((req.body || {}).moduleName);
  if (!description || !moduleName) return res.status(400).json({ error: 'description and moduleName required (alphanumeric/underscore)' });
  const result = await generateSourceModule(llmProvider, description, moduleName);
  if (result.success) {
    stageModule(result.moduleName, result.code, { description });
    res.json({ success: true, moduleName: result.moduleName, staged: true });
  } else {
    res.status(500).json({ success: false, error: result.error });
  }
});

// R-F1867 (audit DD-12): moduleName flows into gitCommit() (now spawnSync-safe)
// AND into file paths (`./sources/${moduleName}.mjs`), so an unsanitized value
// is both a (former) shell-injection and a path-traversal vector. Constrain it
// to a safe identifier at the HTTP boundary — mirrors the Telegram /update path.
function _sanitizeModuleName(name) {
  return String(name || '').toLowerCase().replace(/[^a-z0-9_]/g, '').substring(0, 40);
}

app.post('/api/self/apply', requireAdmin, async (req, res) => {
  const moduleName = _sanitizeModuleName((req.body || {}).moduleName);
  if (!moduleName) return res.status(400).json({ error: 'moduleName required (alphanumeric/underscore)' });
  const result = await deployModule(moduleName);
  res.json(result);
});

app.post('/api/self/rollback', requireAdmin, (req, res) => {
  const moduleName = _sanitizeModuleName((req.body || {}).moduleName);
  if (!moduleName) return res.status(400).json({ error: 'moduleName required (alphanumeric/underscore)' });
  res.json(rollbackModule(moduleName));
});

app.get('/api/self/update-log', requireAdmin, (req, res) => {
  res.json({ log: getUpdateLog(parseInt(req.query.limit) || 20) });
});

app.post('/webhook', async (req, res) => {
  // SECURITY 2026-04-09 + R-F831 2026-05-23: Telegram lets you configure a
  // `secret_token` when calling setWebhook; Telegram sends it back as
  // X-Telegram-Bot-Api-Secret-Token on every delivery. Without this
  // check, anyone who knows the URL can POST a fake update and have it
  // processed by telegramAlerter._handleMessage.
  //
  // R-F831 (2026-05-23): tightened. In production (NODE_ENV=production
  // OR FLY_APP_NAME set) we now REFUSE unsigned deliveries — no more
  // soft "accept-with-warning". Dev keeps the soft path so local
  // testing works.
  const expected = (process.env.TELEGRAM_WEBHOOK_SECRET || '').trim();
  if (expected) {
    const presented = (req.headers['x-telegram-bot-api-secret-token'] || '').toString().trim();
    if (presented !== expected) {
      console.warn('[Webhook] rejecting — invalid or missing X-Telegram-Bot-Api-Secret-Token');
      return res.sendStatus(401);
    }
  } else {
    const isProd = (process.env.NODE_ENV === 'production') || !!process.env.FLY_APP_NAME;
    if (isProd) {
      // R-F831 strict mode: prod cannot accept an unsigned webhook.
      // Operator must set TELEGRAM_WEBHOOK_SECRET (and matching setWebhook
      // call) for the bot to function.
      if (!global.__telegramWebhookSecretWarned) {
        console.error('[Webhook] TELEGRAM_WEBHOOK_SECRET not set in production — REFUSING all deliveries. Set the env var + call setWebhook with the same secret.');
        global.__telegramWebhookSecretWarned = true;
      }
      return res.sendStatus(503);
    }
    if (!global.__telegramWebhookSecretWarned) {
      console.warn('[Webhook] TELEGRAM_WEBHOOK_SECRET not set — DEV mode soft-accept. Set the env var to enforce.');
      global.__telegramWebhookSecretWarned = true;
    }
  }
  try {
    const update = req.body;
    if (!update || !update.message) { res.sendStatus(200); return; }
    if (telegramAlerter && telegramAlerter.isConfigured) {
      // R-F3617 — go through the SAME dispatcher the polling loop uses. This called
      // `_handleMessage` directly, which skips `_handleChannelKeyword` entirely: on a
      // webhook deployment every public subscriber reply would be dropped, which is
      // exactly the R-F3610 defect arriving through the other transport. Dormant
      // today (no webhook is set), and that is why it would not have been noticed.
      // `dispatchMessage` still enforces the allow-list before `_handleMessage`, and
      // `_handleMessage` re-checks it (R-F1821) — the security posture is unchanged.
      await telegramAlerter.dispatchMessage(update.message);
    }
    res.sendStatus(200);
  } catch (error) {
    console.error('[Webhook] Error:', error);
    res.sendStatus(500);
  }
});

app.get('/webhook', (req, res) => res.send('Webhook is working!'));

// ── Auth Middleware ───────────────────────────────────────────────────────────

function requireAuth(req, res, next) {
  // R-F831 (2026-05-23): same-process localhost bypass — kept because
  // the embedded Telegram bot calls /api/data on this same process.
  // BUT now requires either:
  //   - the request to LITERALLY come from the same machine
  //     (ip in {127.0.0.1, ::1, ::ffff:127.0.0.1}) AND
  //   - ARIA_DISABLE_LOCALHOST_BYPASS != 1 (operator can lock it down)
  //
  // Fly.io traffic always arrives from the fly-proxy IP — never
  // 127.0.0.1 — so the bypass is unreachable from external traffic.
  // The post-migration risk vector is a co-tenant container, but Fly
  // gives each app its own network namespace; the only thing that can
  // hit 127.0.0.1 is this process itself.
  // R-F3833 — was `req.ip || req.socket?.remoteAddress`. With `trust proxy: 1`
  // req.ip is derived from X-Forwarded-For, so a 6PN peer connecting directly to
  // aria-web.internal:3117 could send `X-Forwarded-For: 127.0.0.1` and reach
  // next() with req.user never set. requireInfraRole already keyed off the real
  // peer and documented this vector; this gate was simply never updated.
  if (localhostBypassAllowed(req)) return next();

  const token = req.headers.authorization?.replace('Bearer ', '');
  if (!token) return res.status(401).json({ error: 'Authentication required' });

  // R-F3074 — a token the user has logged out is dead, even though its
  // signature and exp still verify. Checked BEFORE the internal-token branch
  // is irrelevant (that token is never issued to a browser) but AFTER the
  // presence check so the cost is one object lookup on authenticated traffic.
  if (isTokenRevoked(token)) {
    return res.status(401).json({ error: 'Session ended — please log in again' });
  }

  // Allow ARIA internal token (used by WhatsApp, email reader, proactive system).
  // SECURITY 2026-04-09: removed the hardcoded 'aria-internal' fallback. The
  // previous default value was readable in the public source repo, so anyone
  // could send `Authorization: Bearer aria-internal` and impersonate an admin
  // whenever ARIA_INTERNAL_TOKEN was unset. We now require the env var to be
  // explicitly set; if not, internal-token auth is simply unavailable and
  // callers fall through to the JWT path.
  const internalToken = (process.env.ARIA_INTERNAL_TOKEN || '').trim();
  if (internalToken && token === internalToken) {
    req.user = { id: 'aria-internal', role: 'admin' };
    return next();
  }

  try {
    const payload = verifyToken(token);
    // R-F3332 — ONE lookup serves both live-record checks below. The JWT is a
    // login-time snapshot; neither a force-logout nor a pending rotation can be
    // read from it.
    const liveUser = findUserById(payload.userId);
    // Token version check — invalidates sessions after force-logout
    if (payload.ver !== undefined) {
      if (liveUser && (liveUser.tokenVersion || 0) !== payload.ver) {
        return res.status(401).json({ error: 'Session revoked — please log in again' });
      }
    }
    // R-F3332 — an account still holding an ISSUED temporary credential (see
    // lib/auth/passwordRotation.mjs) can do exactly three things: read /me,
    // change its password, log out. This is THE enforcement point; the redirect
    // in app.js is only the UX that follows it.
    if (rotationBlocked(liveUser, req.method, req.path)) {
      return res.status(403).json({
        error: 'Set a new password before continuing — the one you were issued is temporary.',
        code: ROTATION_REQUIRED_CODE,
      });
    }
    req.user = payload;
    // R-F2871 — the Bearer is now fully verified (signature + force-logout
    // check), so mirror it into the page-gate cookie. This is what lets an
    // existing session self-heal: the next API call the page makes restores
    // access to the operator pages, with no sign-out/sign-in dance.
    // Deliberately NOT done for the localhost bypass (no identity) or the
    // ARIA_INTERNAL_TOKEN path (a service caller has no browser session).
    _mintPageCookie(req, res, token);
    next();
  } catch {
    return res.status(401).json({ error: 'Invalid or expired token' });
  }
}

// R-F2170: generalized role gate. Canonical roles + the satisfies-decision live in
// lib/auth/roles.mjs (shared with tests, since server.mjs boots on import). requireRole
// is the source of truth; the aria-app middleware mirrors it for UX only.
// NB: declared as a function declaration (hoisted) so routes registered earlier in the
// file — and requireAdmin below — can reference it at module-eval time.
function requireRole(...allowed) {
  return (req, res, next) => requireAuth(req, res, () => {
    if (!roleSatisfies(req.user?.role, allowed)) {
      return res.status(403).json({ error: `Access requires role: ${allowed.join(' or ')}` });
    }
    next();
  });
}

// R-F2775: role gate for INFRA endpoints, with the same-process bypass applied
// coherently.
//
// requireAuth's localhost bypass calls next() WITHOUT setting req.user. Composing
// requireRole on top of it therefore 403s every same-process caller: the bypass
// grants access, then the role check sees `req.user?.role === undefined` and
// denies. (This is pre-existing and also affects today's requireAdmin routes that
// the WA listener proxies call over localhost — flagged separately; not widened
// here.) Left unhandled it would have been a REGRESSION on the endpoints this
// R-number gates, which were previously anonymous: a localhost health poll of
// /api/source-health would have started failing 403.
//
// So: same-process callers keep EXACTLY their prior access (the bypass, unchanged),
// and everyone else is role-checked. Deliberately not "localhost ⇒ admin" — that
// would widen the trust model rather than preserve it.
// The bypass keys off req.socket.remoteAddress (the REAL TCP peer), deliberately
// NOT req.ip. With `trust proxy: 1` (:1212) req.ip is derived from X-Forwarded-For,
// and aria-web listens on 0.0.0.0 and is reachable as aria-web.internal:3117 over
// Fly's 6PN — so a peer on the private network connecting DIRECTLY (no proxy hop)
// can send `X-Forwarded-For: 127.0.0.1` and make req.ip read as loopback. The
// socket peer address cannot be forged that way. Genuine same-process callers are
// unaffected: their real peer address IS 127.0.0.1.
function requireInfraRole(...allowed) {
  return (req, res, next) => {
    // R-F3833 — behaviour unchanged; this gate was already correct. It now shares
    // the one implementation so a sixth copy has nowhere to drift to.
    if (localhostBypassAllowed(req)) return next();
    return requireRole(...allowed)(req, res, next);
  };
}

function requireAdmin(req, res, next) {
  // R-F2170: delegates to the generalized gate (admin-only). Behaviour unchanged —
  // many routes above reference this hoisted name at registration time.
  return requireRole('admin')(req, res, next);
}

// ── R-F2774: server-side operator-page gate + auth cookie ────────────────────
// Auth is normally localStorage/Bearer, sent ONLY on API fetches — a page
// NAVIGATION carries no Bearer header, so the server can't authenticate someone
// typing /vault.html. We mirror the JWT into an httpOnly `crucix_token` cookie at
// login so the server CAN gate page navigations by role. This is the REAL page
// gate; the in-page Auth.require*() calls are cosmetic (they run only after the
// HTML is already delivered). APIs are unchanged — they still use the Bearer
// header from localStorage, NOT this cookie.
const _AUTH_COOKIE = 'crucix_token';
function _setAuthCookie(res, token) {
  // Secure: HTTPS only (trust proxy set at :1209). SameSite=Lax: sent on top-level
  // navigations, blocked on cross-site POST → CSRF-safe (and the cookie only gates
  // GET page reads — it never authenticates a mutating API; those use the Bearer).
  res.cookie(_AUTH_COOKIE, token, { httpOnly: true, secure: true, sameSite: 'lax', maxAge: 7 * 24 * 3600 * 1000, path: '/' });
}
function _clearAuthCookie(res) {
  res.clearCookie(_AUTH_COOKIE, { httpOnly: true, secure: true, sameSite: 'lax', path: '/' });
}
// R-F2871 — refresh the page-gate cookie from a valid Bearer.
//
// R-F2774 minted this cookie at LOGIN ONLY, with no refresh path. Any session
// that predated R-F2774, or that crossed the 7-day cookie TTL, kept a perfectly
// valid JWT in localStorage — APIs worked, the dashboard rendered, the nav showed
// the links — while every operator page bounced to /signin.html. Reported by the
// admin on 2026-07-22 as "no access to brain / source health / vault"; the account
// was never at fault (system-status: admins=1, matchesEnv=true, anomaly=ok).
//
// Called ONLY from requireAuth's verified-JWT branch, so the cookie can never
// carry anything the caller does not already hold. Cheap and idempotent: if the
// cookie already matches the Bearer we send nothing, so a normal API call does
// not grow a redundant Set-Cookie header.
function _mintPageCookie(req, res, token) {
  try {
    if (!token || res.headersSent) return;
    if (_cookieToken(req) === token) return;   // already current — no-op
    _setAuthCookie(res, token);                // identical flags to login
  } catch {
    // A cookie refresh must NEVER break the API call it rode in on.
  }
}

function _cookieToken(req) {
  const raw = req.headers.cookie || '';
  for (const part of raw.split(';')) {
    const i = part.indexOf('=');
    if (i < 0) continue;
    if (part.slice(0, i).trim() === _AUTH_COOKIE) return decodeURIComponent(part.slice(i + 1).trim());
  }
  return '';
}
// Gate an operator/infra PAGE by role. Fail → REDIRECT (browser UX): no/invalid
// session → /signin.html; authenticated-but-insufficient-role → /dashboard.html.
// The operator (admin) always passes (admin ⊇ poweruser). Localhost bypass mirrors
// requireAuth so same-process operator tooling is unaffected.
function requirePageRole(...allowed) {
  return (req, res, next) => {
    // R-F3833 — same forgery as requireAuth, and this one renders operator/infra
    // PAGES (vault, aria-brain, dashboard) to an unauthenticated 6PN peer. Not in
    // the original audit; found by sweeping every loopback literal in the tier.
    if (localhostBypassAllowed(req)) return next();
    const token = _cookieToken(req);
    if (!token) return res.redirect(302, '/signin.html');
    let payload;
    try {
      payload = verifyToken(token);
      if (payload.ver !== undefined) {
        const u = findUserById(payload.userId);
        if (u && (u.tokenVersion || 0) !== payload.ver) return res.redirect(302, '/signin.html');
      }
    } catch { return res.redirect(302, '/signin.html'); }
    if (!roleSatisfies(payload.role, allowed)) return res.redirect(302, '/dashboard.html');
    req.user = payload;
    return next();
  };
}

// ── Auth Routes ───────────────────────────────────────────────────────────────

app.post('/api/auth/register', async (req, res) => {
  try {
    const {
      username, email, password, fullName,
      // R-F48b organisation context — all optional (legacy 1-screen
      // signup omits these and gets default-empty values that the
      // Python brain interprets as "broker" persona).
      accountType, companyName, companyCountry, companySize,
      sector, jobTitle,
      useCases, regions, languages, volumeEstimate, complianceNeeds,
      purposeStatement,
    } = req.body || {};
    if (!username || username.length < 3)  return res.status(400).json({ error: 'Username must be at least 3 characters' });
    if (!email || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return res.status(400).json({ error: 'Invalid email address' });
    if (!password || password.length < 8)  return res.status(400).json({ error: 'Password must be at least 8 characters' });

    // R-F2035 — disposable / throwaway email block. Self-serve signup is an open
    // door; throwaway addresses are the main abuse vector (one human, unlimited
    // accounts). Reject before any account work.
    if (isDisposableEmail(email)) {
      return res.status(400).json({ error: 'Please register with a permanent (non-disposable) email address.' });
    }

    // R-F2035 — MANDATORY email-verify gate. The self-serve flow grants access on
    // email verification, so registration cannot proceed if we can't send the
    // code. Pre-R-F2035, an unconfigured-SMTP path silently created users in
    // pending_approval WITHOUT verifying email ownership — a hole that self-serve
    // (verify → instant active) must not have. Fail LOUDLY + globally (same for
    // new and existing emails, so no account-enumeration), never silently bypass.
    if (!smtpIsConfigured) {
      console.error('[Auth] register BLOCKED — SMTP not configured; cannot send verification code (R-F2035).');
      return res.status(503).json({ error: 'Registration is temporarily unavailable (email verification offline). Please try again shortly.' });
    }

    // Anti-enumeration: return the SAME response whether the email/username
    // exists or not, so an attacker can't probe for account existence via
    // HTTP status code. Pre-2026-04-20 this returned 409 with a distinct
    // message on duplicates. Now the server silently no-ops on collision
    // but sends a "someone tried to register with your email" notification
    // (when SMTP is configured) so the legitimate owner gets a signal.
    // Adds a synthetic delay to match the real-register timing budget.
    const emailExists = !!findUserByEmail(email);
    const usernameExists = !!findUserByUsername(username);
    // R-F2609 — the conversation-history bucket key is a lossy [^A-Za-z0-9] slug of the
    // email (conversationKeyForUser), so two DISTINCT emails can map to the SAME bucket
    // (e.g. john.doe@ vs johndoe@) and then read/rename/delete each other's chat + DD
    // history. Enforce slug-injectivity at the door: refuse a signup whose bucket key
    // collides with an existing account. Handled exactly like an email/username collision
    // (silent generic success — anti-enumeration preserved). No key change or data
    // migration; existing buckets are untouched. Only runs when the email itself is new.
    const _newBucketKey = slugifyIdentity(email);
    const slugCollides = !emailExists && !!_newBucketKey &&
      listUsers().some(u => conversationKeyForUser(u) === _newBucketKey);
    if (emailExists || usernameExists || slugCollides) {
      if (slugCollides && !emailExists && !usernameExists) {
        console.warn(`[Auth] register BLOCKED slug-collision bucket=${_newBucketKey} email=${_maskEmail(email)} — a distinct existing account already owns this conversation bucket (R-F2609)`);
      }
      console.log(`[Auth] Register attempt for existing ${emailExists ? 'email' : (usernameExists ? 'username' : 'bucket-slug')}: ${_maskEmail(email)} / ${username} — responding with generic success to prevent enumeration`);
      // Optional: if SMTP is configured and the email exists, email the
      // legitimate owner. This is the security-best-practice flow.
      // R-F3253 — gate on the MAILER's own answer, not on bare EMAIL_* vars.
      // This is a send gate, not a diagnostic: with credentials supplied
      // through the ARIA_SMTP_* fallback (which is how the live box is
      // configured), these three vars are unset and the duplicate-registration
      // warning to the legitimate account owner was silently never sent —
      // a security-notification path that looked wired and was not.
      if (emailExists && smtpIsConfigured) {
        try {
          const { sendEmail } = await import('./lib/auth/email.mjs');
          await sendEmail(email, 'Someone tried to register with your email',
            `Someone just tried to create a new Arkmurus account using your email address. ` +
            `You already have an account — if this was you, use the login or password-reset ` +
            `flow instead. If it wasn't you, no action is needed; no new account was created.`
          ).catch(() => {});
        } catch {}
      }
      // Synthetic jitter to blunt timing side-channels
      await new Promise(r => setTimeout(r, 80 + Math.random() * 60));
      return res.json({
        message: 'Account created. Your registration is awaiting admin approval — you will be notified once activated.',
        needsVerification: false,
        email,
      });
    }

    // R-F2034 — self-serve onboarding: SMTP is guaranteed configured here (the
    // mandatory-verify gate above 503s otherwise), so every registration takes
    // the email-verify path. The silent "no SMTP → pending_approval" bypass was
    // REMOVED (R-F2035): there is no path that activates an account without a
    // verified email. Account becomes active in /verify-email once the code is
    // confirmed (R-F2034).
    createUser({
      username, email, password, fullName,
      accountType, companyName, companyCountry, companySize,
      sector, jobTitle,
      useCases, regions, languages, volumeEstimate, complianceNeeds,
      purposeStatement,
    });
    const rawUser = findUserByEmail(email); // raw record includes verificationCode

    await sendVerificationEmail(email, rawUser.fullName, rawUser.verificationCode).catch(() => {});
    console.log(`[Auth] New registration, verification email sent: ${email}`);
    res.json({ message: 'Account created. Please check your email for a 6-digit verification code.', needsVerification: true, email });
  } catch (err) {
    console.error('[Auth] Register error:', err.message);
    res.status(500).json({ error: 'Registration failed' });
  }
});

// R-F2383 — per-account login brute-force lockout. IP rate-limiting alone
// (TIERS.auth) lets an attacker rotating IPs grind a single account's password;
// this adds a per-email failure counter + temporary lockout, mirroring the
// reset-code throttle (_resetAttempts).
const _loginAttempts = new Map(); // email_lower → { count, firstAt, lockedUntil }
const _LOGIN_MAX_ATTEMPTS = 8;
const _LOGIN_WINDOW_MS = 15 * 60 * 1000;
const _LOGIN_LOCKOUT_MS = 15 * 60 * 1000;
function _loginThrottleCheck(emailLower) {
  const now = Date.now();
  const e = _loginAttempts.get(emailLower);
  if (!e) return { allowed: true };
  if (e.lockedUntil && e.lockedUntil > now) return { allowed: false };
  if (e.firstAt && now - e.firstAt > _LOGIN_WINDOW_MS) { _loginAttempts.delete(emailLower); return { allowed: true }; }
  return { allowed: true };
}
function _loginThrottleFail(emailLower) {
  const now = Date.now();
  const e = _loginAttempts.get(emailLower) || { count: 0, firstAt: now };
  e.count += 1; e.firstAt = e.firstAt || now;
  if (e.count >= _LOGIN_MAX_ATTEMPTS) e.lockedUntil = now + _LOGIN_LOCKOUT_MS;
  _loginAttempts.set(emailLower, e);
  // R-F3860 — this map is keyed by a caller-supplied email and is reachable
  // unauthenticated, so without a sweep it grows one entry per distinct
  // address for the life of the process. Swept here, on the write that grows it.
  pruneAttemptMap(_loginAttempts, _LOGIN_WINDOW_MS);
  // R-F2608 — opportunistic sweep so expired entries don't accumulate. Only
  // pays the O(n) cost when the map is over the cap; a steady-state login flow
  // never hits this.
  if (_loginAttempts.size > 5000) {
    for (const [k, v] of _loginAttempts) {
      const lockActive = v.lockedUntil && v.lockedUntil > now;
      const windowActive = v.firstAt && now - v.firstAt <= _LOGIN_WINDOW_MS;
      if (!lockActive && !windowActive) _loginAttempts.delete(k);
    }
  }
}
function _loginThrottleClear(emailLower) { _loginAttempts.delete(emailLower); }

// R-F2605 — mask email in log lines to avoid PII in seenode/fly logs. Keeps
// enough of the local part to correlate a support ticket without logging the
// full address.
function _maskEmail(e){ const s=String(e||''); const [u,d]=s.split('@'); return d ? (u.slice(0,2)+'***@'+d) : '***'; }

app.post('/api/auth/login', async (req, res) => {
  try {
    const { email, password } = req.body || {};
    if (!email || !password) {
      console.warn(`[Auth] login: missing email or password ip=${req.ip}`);
      return res.status(400).json({ error: 'Email and password required' });
    }
    const _emLower = String(email).trim().toLowerCase();
    if (!_loginThrottleCheck(_emLower).allowed) {
      console.warn(`[Auth] login LOCKED email=${_maskEmail(email)} ip=${req.ip}`);
      errorTracker.record('auth', 'login_throttle_lockout', null, null, { ip: req.ip }); // R-F2605 — brute-force signal
      return res.status(429).json({ error: 'Too many failed attempts. Please wait a few minutes and try again.' });
    }

    // R-F427: log distinct failure paths so the operator can diagnose
    // "Invalid credentials" reports from seenode logs. The HTTP response
    // stays generic to avoid enumeration; the log line names the cause.
    const user = findUserByEmail(email);
    if (!user) {
      _loginThrottleFail(_emLower);   // R-F2383 — throttle enumeration + brute force
      console.warn(`[Auth] login FAIL no-user email=${_maskEmail(email)} ip=${req.ip}`);
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    if (!verifyPassword(password, user.passwordHash)) {
      _loginThrottleFail(_emLower);   // R-F2383 — per-account lockout after repeated wrong passwords
      console.warn(`[Auth] login FAIL password-mismatch email=${_maskEmail(email)} id=${user.id} status=${user.status} ip=${req.ip}`);
      return res.status(401).json({ error: 'Invalid credentials' });
    }
    _loginThrottleClear(_emLower);    // R-F2383 — successful password clears the counter

    if (user.status === 'pending_approval') {
      console.warn(`[Auth] login BLOCKED pending_approval email=${_maskEmail(email)} ip=${req.ip}`);
      return res.status(403).json({ error: 'Your account is pending admin approval. You will be notified once activated.' });
    }
    if (user.status === 'pending_verification') {
      console.warn(`[Auth] login BLOCKED pending_verification email=${_maskEmail(email)} ip=${req.ip}`);
      return res.status(403).json({ error: 'Please verify your email first', needsVerification: true });
    }
    if (user.status === 'suspended') {
      console.warn(`[Auth] login BLOCKED suspended email=${_maskEmail(email)} ip=${req.ip}`);
      return res.status(403).json({ error: 'Account suspended. Contact an administrator.' });
    }

    // If 2FA is enabled, issue a short-lived pre-auth token instead of the real JWT
    if (user.twoFactorEnabled && user.twoFactorSecret) {
      // R-F3834 — stage:'pre2fa' is what stops this being a full session token.
      // Without it requireAuth accepted it for five minutes (ver 0 matches any
      // account never force-logged-out), so password-only access to a 2FA
      // account was enough to change the password or disable 2FA. Passing the
      // real tokenVersion additionally lets a force-logout kill it mid-flow.
      // No auth cookie is set here: the second factor has not been presented yet.
      const preToken = createToken(user.id, user.role, '5m', user.tokenVersion || 0, 'pre2fa');
      return res.json({ requires2FA: true, preToken });
    }

    const token = createToken(user.id, user.role, '7d', user.tokenVersion || 0);
    const cleanUser = updateUser(user.id, { lastLogin: new Date().toISOString() });
    console.log(`[Auth] login OK email=${_maskEmail(user.email)} id=${user.id} role=${user.role} ip=${req.ip}`);
    _setAuthCookie(res, token);   // R-F2774 — mirror JWT to httpOnly cookie for server-side page gating
    res.json({ token, user: cleanUser });
  } catch (err) {
    console.error('[Auth] Login error:', err.message);
    errorTracker.record('auth', 'login_handler_error', err); // R-F2605
    res.status(500).json({ error: 'Login failed' });
  }
});

// ── R-F3086 — the ONE place a TOTP code is checked ───────────────────────────
// otplib v13 (package.json pins ^13.4.0) removed the `TOTP.verify()` static and
// changed `generateURI` to a single options object. Three call sites still used
// the v12 shapes, so the entire 2FA subsystem was dead:
//   * /2fa/setup      → generateURI('TOTP', {...}) threw "Cannot read properties
//                       of undefined (reading 'split')" → 500 "2FA setup failed"
//   * /2fa/enable     → TOTP.verify is not a function → 500
//   * /2fa/disable    → same → 500
//   * /2fa/authenticate → same → 500
// Verified against the installed otplib 13.4.0, not from memory.
//
// Scope, stated precisely: 2FA has NEVER worked here. The feature commit
// (7a5e29d3, 2026-04-02) added no otplib dependency; 045ffdae added it the same
// day directly at ^13.4.0, so the v12 call shapes this code was written against
// were never the installed ones. Nobody is locked out — `twoFactorEnabled` can
// only be set by /2fa/enable, which always 500'd — but the account-security
// feature has been dead since the day it shipped, and the sign-in half IS wired
// (signin.html:174 handles requires2FA), so it would have locked out anyone who
// had managed to turn it on. There is still no UI to enable 2FA; these routes
// are API-only. Surfacing that is a product decision, not this R-number's.
//
// The v13 replacement is `verifySync()`, which returns an OBJECT
// `{ valid, delta, ... }` — NOT a boolean. A naive swap keeps `if (!valid)`
// working syntactically while `{valid:false}` is TRUTHY, i.e. every code would
// be accepted: a silent auth BYPASS that is strictly worse than the outage it
// replaces. Reading `.valid` is the whole point of centralising this.
async function verifyTotpCode(code, secret) {
  if (!code || !secret) return false;
  const { verifySync } = await import('otplib');
  const result = verifySync({
    token: String(code).replace(/\s/g, ''),
    secret,
  });
  return result?.valid === true;
}

// ── 2FA: verify TOTP code after password (second step) ───────────────────────
app.post('/api/auth/2fa/authenticate', async (req, res) => {
  try {
    const { preToken, code } = req.body || {};
    if (!preToken || !code) return res.status(400).json({ error: 'preToken and code required' });
    let payload;
    // R-F3834 — the ONLY acceptor of a pre-auth token, and it demands that exact
    // stage: a full session token is refused here too, so the stage check is not
    // one-directional.
    try { payload = verifyToken(preToken, { stage: 'pre2fa' }); } catch { return res.status(401).json({ error: 'Pre-auth token invalid or expired' }); }
    const user = findUserById(payload.userId);
    if (!user || !user.twoFactorSecret) return res.status(401).json({ error: 'Invalid session' });
    // R-F3834 — the pre-auth token now carries `ver`, and THIS is what makes that
    // claim load-bearing. Without this check the version rides along unread, which
    // is the "looks wired and is dark" failure §21a exists to prevent: a
    // force-logout, suspension or password change (R-F3835 bumps tokenVersion)
    // landing inside the 5-minute window would leave the half-finished login still
    // redeemable for a full 7-day session.
    if (payload.ver !== undefined && (user.tokenVersion || 0) !== payload.ver) {
      return res.status(401).json({ error: 'Session revoked — please log in again' });
    }
    const valid = await verifyTotpCode(code, user.twoFactorSecret);   // R-F3086
    if (!valid) return res.status(401).json({ error: 'Invalid authenticator code' });
    const token = createToken(user.id, user.role, '7d', user.tokenVersion || 0);
    const cleanUser = updateUser(user.id, { lastLogin: new Date().toISOString() });
    _setAuthCookie(res, token);   // R-F2774
    res.json({ token, user: cleanUser });
  } catch (err) {
    console.error('[Auth] 2FA authenticate error:', err.message);
    res.status(500).json({ error: '2FA verification failed' });
  }
});

// ── 2FA: generate secret + QR code (setup step 1) ────────────────────────────
// R-F2774 — server-side logout: clears the httpOnly auth cookie (the client can't
// touch an httpOnly cookie). The client also clears its localStorage token.
app.post('/api/auth/logout', (req, res) => {
  _clearAuthCookie(res);
  // R-F3074 — actually end the session. The app authenticates with the bearer
  // in localStorage, not the cookie, so clearing the cookie alone left the
  // token live for the rest of its 7-day life (verified: logout → 200, same
  // token → /api/auth/me 200). Revoke THIS token only, so signing out on one
  // device does not sign the same person out everywhere.
  const token = req.headers.authorization?.replace('Bearer ', '');
  if (token) {
    let expiresAt = 0;
    try { expiresAt = verifyToken(token)?.exp || 0; } catch { /* expired/invalid — nothing to revoke */ }
    if (expiresAt) revokeToken(token, expiresAt);
  }
  res.json({ ok: true });
});

app.post('/api/auth/2fa/setup', requireAuth, async (req, res) => {
  try {
    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    const { generateSecret, generateURI } = await import('otplib');
    const secret = generateSecret();
    // R-F3086 — otplib v13 takes ONE options object; the v12 ('TOTP', {...})
    // form threw "Cannot read properties of undefined (reading 'split')" and
    // made this endpoint a guaranteed 500.
    const uri = generateURI({
      strategy: 'totp',
      issuer: 'Arkmurus Intelligence',
      label: user.email,
      secret,
    });
    const QRCode = (await import('qrcode')).default;
    const qrDataUrl = await QRCode.toDataURL(uri);
    updateUser(user.id, { twoFactorSecret: secret, twoFactorEnabled: false });
    res.json({ secret, qrDataUrl });
  } catch (err) {
    console.error('[Auth] 2FA setup error:', err.message);
    res.status(500).json({ error: '2FA setup failed' });
  }
});

// ── 2FA: confirm code and enable ─────────────────────────────────────────────
app.post('/api/auth/2fa/enable', requireAuth, async (req, res) => {
  try {
    const { code } = req.body || {};
    if (!code) return res.status(400).json({ error: 'Authenticator code required' });
    const user = findUserById(req.user.userId);
    if (!user?.twoFactorSecret) return res.status(400).json({ error: 'Run /api/auth/2fa/setup first' });
    const valid = await verifyTotpCode(code, user.twoFactorSecret);   // R-F3086
    if (!valid) return res.status(400).json({ error: 'Invalid code — check your authenticator app and try again' });
    updateUser(user.id, { twoFactorEnabled: true });
    res.json({ message: '2FA enabled successfully' });
  } catch (err) {
    res.status(500).json({ error: '2FA enable failed' });
  }
});

// ── 2FA: disable ─────────────────────────────────────────────────────────────
app.post('/api/auth/2fa/disable', requireAuth, async (req, res) => {
  try {
    const { code } = req.body || {};
    if (!code) return res.status(400).json({ error: 'Authenticator code required to disable 2FA' });
    const user = findUserById(req.user.userId);
    if (!user?.twoFactorSecret) return res.status(400).json({ error: '2FA is not enabled' });
    const valid = await verifyTotpCode(code, user.twoFactorSecret);   // R-F3086
    if (!valid) return res.status(400).json({ error: 'Invalid code' });
    updateUser(user.id, { twoFactorEnabled: false, twoFactorSecret: null });
    res.json({ message: '2FA disabled' });
  } catch (err) {
    res.status(500).json({ error: '2FA disable failed' });
  }
});

// ── R-F3836: verify-email attempt throttle, keyed by EMAIL ───────────────────
// Mirrors the R-F609 reset-password throttle below (_resetThrottle*), for the
// same reason and with the same shape.
//
// The R-F2035 lockout it supplements counts attempts on the USER RECORD, so it
// can only fire for an email that exists. That made the lockout itself an
// oracle: five wrong codes against a real pending account returned 429, while
// the same five against an unregistered address returned 400 forever. Closing
// the 404/400/200 three-way split without closing this would just move the leak.
//
// Keyed by email and checked BEFORE any user lookup, so the response is
// identical whether or not the address is registered.
const _verifyAttempts = new Map();   // emailLower -> { count, firstAt, lockedUntil }
const _VERIFY_WINDOW_MS = 15 * 60 * 1000;
const _VERIFY_LOCKOUT_MS = 60 * 60 * 1000;

function _verifyThrottleCheck(emailLower) {
  const now = Date.now();
  const entry = _verifyAttempts.get(emailLower);
  if (!entry) return { allowed: true };
  if (entry.lockedUntil && entry.lockedUntil > now) return { allowed: false };
  if (entry.firstAt && now - entry.firstAt > _VERIFY_WINDOW_MS) {
    _verifyAttempts.delete(emailLower);
  }
  return { allowed: true };
}

function _verifyThrottleRecordFailure(emailLower) {
  const now = Date.now();
  const entry = _verifyAttempts.get(emailLower) || { count: 0, firstAt: now };
  entry.count += 1;
  entry.firstAt = entry.firstAt || now;
  if (entry.count >= MAX_VERIFY_ATTEMPTS) entry.lockedUntil = now + _VERIFY_LOCKOUT_MS;
  _verifyAttempts.set(emailLower, entry);
  // R-F3860 — this map is keyed by a caller-supplied email and is reachable
  // unauthenticated, so without a sweep it grows one entry per distinct
  // address for the life of the process. Swept here, on the write that grows it.
  pruneAttemptMap(_verifyAttempts, _VERIFY_WINDOW_MS);
  return entry;
}

function _verifyThrottleClear(emailLower) {
  _verifyAttempts.delete(emailLower);
}

app.post('/api/auth/verify-email', async (req, res) => {
  try {
    const { email, code } = req.body || {};
    if (!email || !code) return res.status(400).json({ error: 'Email and code required' });
    const _emailLower = String(email).toLowerCase().trim();

    // R-F3836 — ONE refusal for every failure mode. Previously an unknown email
    // 404'd "User not found", a known email with a wrong code 400'd "Invalid
    // verification code", and an already-verified account 200'd "already
    // verified" — three distinguishable answers, i.e. an oracle that tells an
    // attacker walking a breach list which addresses hold live verified accounts.
    // Registration (:5777) and /forgot-password already refuse to answer that;
    // this endpoint was the side door.
    //
    // The cost is honest and small: someone re-clicking a stale verification link
    // now reads the generic refusal instead of "already verified — please log in".
    // They can still simply sign in, which is what they wanted.
    const INVALID_CODE = 'Invalid or expired verification code.';

    // Throttle BEFORE the lookup, so a locked-out email cannot probe existence
    // through the timing or the shape of the refusal (R-F609's rationale).
    if (!_verifyThrottleCheck(_emailLower).allowed) {
      console.warn(`[Auth] verify-email REJECTED throttle email=${_emailLower} ip=${req.ip}`);
      return res.status(400).json({ error: INVALID_CODE });
    }

    const user = findUserByEmail(email);
    if (!user) {
      _verifyThrottleRecordFailure(_emailLower);
      return res.status(400).json({ error: INVALID_CODE });
    }
    if (user.status === 'active') {
      _verifyThrottleRecordFailure(_emailLower);
      return res.status(400).json({ error: INVALID_CODE });
    }

    // R-F2035 — verify-code brute-force lockout. A 6-digit code is grindable; cap
    // wrong attempts, then BURN the code (force a fresh resend) so brute force
    // degrades into a resend-rate problem, not a 1e6 guessing game.
    // R-F2383 — timing-safe compare (align with the reset-code flow R-F609).
    const _vcExpected = String(user.verificationCode || '');
    const _vcProvided = String(code || '');
    let _vcMatch = false;
    if (_vcExpected && _vcProvided.length === _vcExpected.length) {
      try { _vcMatch = timingSafeEqual(Buffer.from(_vcExpected, 'utf8'), Buffer.from(_vcProvided, 'utf8')); } catch { _vcMatch = false; }
    }
    if (!_vcMatch) {
      _verifyThrottleRecordFailure(_emailLower);
      const attempts = (user.verificationAttempts || 0) + 1;
      if (attempts >= MAX_VERIFY_ATTEMPTS) {
        // R-F2035's code burn is the real defence and is UNCHANGED. Only the
        // RESPONSE becomes uniform: the old 429 fired solely for emails that
        // exist, so it announced "this address is a live pending account".
        // Nothing consumes the dropped `needsResend` flag (grepped: no client).
        updateUser(user.id, { verificationCode: null, verificationExpiry: null, verificationAttempts: 0 });
        console.warn(`[Auth] verify-email LOCKOUT email=${email} — ${attempts} wrong codes, code burned`);
        return res.status(400).json({ error: INVALID_CODE });
      }
      updateUser(user.id, { verificationAttempts: attempts });
      // R-F3836 — byte-identical to the unknown-email and already-verified
      // refusals above, so none of the three can be told apart.
      return res.status(400).json({ error: INVALID_CODE });
    }
    // Reached only after a CORRECT code, so this message reveals nothing an
    // attacker did not already have to know — kept as real UX for a real user.
    if (user.verificationExpiry && new Date(user.verificationExpiry) < new Date()) {
      return res.status(400).json({ error: 'Verification code expired. Request a new one.', needsResend: true });
    }
    // Correct code: this email is not being probed. Drop its failure history so a
    // legitimate user who fat-fingered earlier is not carrying a lockout.
    _verifyThrottleClear(_emailLower);

    // ── R-F2034: self-serve INSTANT approval ────────────────────────────────
    // A verified email IS the approval (operator policy 2026-06-27). The manual
    // admin-approval bottleneck is gone: evaluate the auto-approval policy and,
    // on approve, flip straight to `active`. The decision + its signals are
    // audited so there's a human-reversible record of every automated approval.
    const decision = evaluateAutoApproval(user);
    if (decision.approve) {
      updateUser(user.id, {
        status: 'active',
        verificationCode: null, verificationExpiry: null, verificationAttempts: 0,
      });
      logAudit({
        adminId: 'system', adminEmail: 'auto-approve@onboarding',
        action: 'auto_approve', targetId: user.id, targetEmail: email,
        targetName: user.fullName,
        notes: `self-serve: ${decision.reason} · signals=${JSON.stringify(decision.signals)}`,
      });
      await sendVerificationSuccessEmail(email, user.fullName).catch(() => {});
      await sendWelcomeEmail(email, user.fullName).catch(() => {});
      if (telegramAlerter?.isConfigured) {
        telegramAlerter.sendMessage(
          `✅ *New user joined (self-serve)*\n\nName: ${user.fullName}\nEmail: ${email}\nUsername: @${user.username}\nTier: free`
        ).catch(() => {});
      }
      console.log(`[Auth] verify-email OK → AUTO-APPROVED active email=${email} reason=${decision.reason}`);
      return res.json({ message: 'Email verified — your account is active. You can log in now.', active: true });
    }

    // Policy declined auto-approval (e.g. flagged for review) — verify the email
    // but hold at pending_approval for an admin (audited with the reason).
    updateUser(user.id, { status: 'pending_approval', verificationCode: null, verificationExpiry: null, verificationAttempts: 0 });
    logAudit({
      adminId: 'system', adminEmail: 'auto-approve@onboarding',
      action: 'auto_approve_declined', targetId: user.id, targetEmail: email,
      targetName: user.fullName,
      notes: `held for review: ${decision.reason} · signals=${JSON.stringify(decision.signals)}`,
    });
    await sendVerificationSuccessEmail(email, user.fullName).catch(() => {});
    await sendPendingApprovalEmail(email, user.fullName).catch(() => {});
    if (telegramAlerter?.isConfigured) {
      telegramAlerter.sendMessage(
        `👤 *User verified — held for review* (${decision.reason})\n\nName: ${user.fullName}\nEmail: ${email}\nGo to Admin → Users.`
      ).catch(() => {});
    }
    res.json({ message: 'Email verified. Your account is under review — you will be notified once activated.' });
  } catch (err) {
    console.error('[Auth] Verify email error:', err.message);
    res.status(500).json({ error: 'Verification failed' });
  }
});

app.post('/api/auth/resend-verification', async (req, res) => {
  try {
    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });

    // R-F3836 — the unknown-email branch was already uniform; the already-verified
    // branch was not, and 400 vs 200 is all an attacker needs to separate a live
    // verified account from an address nobody has registered. Both now return the
    // same acknowledgement and neither sends mail.
    const RESEND_ACK = 'If that email exists, a code has been sent.';
    const user = findUserByEmail(email);
    if (!user) return res.json({ message: RESEND_ACK });
    if (user.status === 'active') return res.json({ message: RESEND_ACK });

    // Rate limit: reject if last code sent <60s ago
    if (user.verificationExpiry) {
      const expiryTime  = new Date(user.verificationExpiry).getTime();
      const issuedApprox = expiryTime - 15 * 60 * 1000;
      if (Date.now() - issuedApprox < 60 * 1000) {
        return res.status(429).json({ error: 'Please wait 60 seconds before requesting a new code' });
      }
    }

    const newCode = generateCode();
    updateUser(user.id, {
      verificationCode: newCode,
      verificationExpiry: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      verificationAttempts: 0,  // R-F2035 — fresh code resets the brute-force counter
    });
    await sendVerificationEmail(email, user.fullName, newCode).catch(() => {});
    res.json({ message: 'Verification email resent.' });
  } catch (err) {
    console.error('[Auth] Resend verification error:', err.message);
    res.status(500).json({ error: 'Failed to resend verification' });
  }
});

app.get('/api/auth/me', requireAuth, (req, res) => {
  try {
    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    // Return clean user (no passwordHash) — findUserById returns raw; strip here
    const { passwordHash, verificationCode, verificationExpiry, resetCode, resetExpiry, verificationAttempts, ...clean } = user;
    // R-F2349 — derive the shared avatarUrl (cleanUser() does this, but this
    // handler strips inline) so the sidebar + Network self-avatar get the photo.
    clean.avatarUrl = clean.avatarUpdatedAt
      ? `/api/profile/photo/${clean.id}?v=${Date.parse(clean.avatarUpdatedAt) || 0}` : null;
    res.json(clean);
  } catch (err) {
    res.status(500).json({ error: 'Failed to fetch user' });
  }
});

// ── R-F2835: cross-tier quota consumption for brain-initiated DD ─────────────
//
// The DD quota (5/month on free, lib/billing/tiers.mjs) was enforced ONLY on the
// web path (server.mjs:3532). A DD triggered from CHAT runs as a tool INSIDE the
// brain and never traverses that route, so it consumed nothing: a grep of
// aria_service/ for ddRunsPerMonth|dd_runs_per_month|dd_quota returns ZERO hits.
// A free user capped at 50 messages/day could therefore trigger up to 50 DD runs
// per day — 10x the MONTHLY cap, daily. Revenue leak and §17 cost exposure.
//
// Billing belongs to this tier (it owns users, tiers and Stripe), so the brain asks
// rather than keeping its own counter. Authenticated with ARIA_INTERNAL_TOKEN over
// Fly's 6PN private network — the same internal hop the WA listener already uses
// (R-F1860). Never reachable from the public internet: requireInfraRole rejects a
// caller without the internal token.
app.post('/api/internal/quota/consume', async (req, res) => {
  const auth = String(req.headers.authorization || '');
  const expected = process.env.ARIA_INTERNAL_TOKEN || process.env.ARIA_API_TOKEN || '';
  if (!expected || auth !== `Bearer ${expected}`) {
    return res.status(401).json({ error: 'internal token required' });
  }
  const userId = String((req.body && req.body.user_id) || '').trim();
  const kind = String((req.body && req.body.kind) || 'ddRun').trim();
  if (!userId) return res.status(400).json({ error: 'user_id required' });
  if (!['ddRun', 'message', 'upload'].includes(kind)) {
    return res.status(400).json({ error: `unknown quota kind: ${kind}` });
  }
  try {
    const _user = findUserById(userId);
    const tier = _user?.tier || null;
    // R-F2981 — privileged accounts (admins/operators) are not customer-metered.
    // The operator's admin account defaults to free (5 DD-runs/mo, no `tier` field)
    // and blocked demos/ops; exempt privileged roles on the brain-side consume path
    // to match the web path. The §17 $300/mo cost cap remains the hard backstop.
    if (isPrivileged(_user)) {
      return res.json({ allowed: true, exempt: 'privileged', kind, tier: tier || 'admin' });
    }
    // enforceQuota() returns NULL when allowed/exempt, and the checkAndConsume
    // verdict only when the cap is hit (lib/billing/enforce.mjs). Normalise to an
    // explicit shape so the brain never has to infer allowance from an absence —
    // "no verdict" meaning "allowed" is the shape that produced three fabricated
    // gates this month.
    const blocked = await enforceQuota(userId, tier, kind);
    if (!blocked) {
      return res.json({ allowed: true, kind, tier: tier || 'free' });
    }
    return res.json({ allowed: false, kind, tier: tier || 'free', ...blocked });
  } catch (err) {
    // Fail OPEN, loudly. Denying a paying user because this tier hiccuped is worse
    // than one uncounted run, and the §17 $300/mo cap remains the hard backstop.
    // Never silent (§21a): the brain records the degradation.
    errorTracker.record('quota_internal', 'consume_failed', err);
    return res.status(200).json({
      allowed: true, current: 0, cap: 0, degraded: true,
      reason: 'quota service unavailable — run allowed, not counted',
    });
  }
});

// ── R-F2825: public landing metrics — the ONE number we can actually count ───
// The landing hero shipped four hardcoded literals, two of them false against the
// code. `records` is the only one with a real live source, so it is the only one
// wired: the brain's /api/aria/memory/tiers reports knowledge.count. The rest are
// stated truthfully in the markup and locked to their source files by
// scripts/audit/landing_claim_truth.mjs — a CI guard beats a fake live wire.
//
// Fetched server-side (the brain endpoint is authenticated and must stay that way)
// and cached, so the public page cannot become a load amplifier on the brain.
// On ANY failure this returns null and the page keeps showing "—". It must never
// serve a stale or invented number: an unbacked figure on the front page of a
// never-false-clean product is the same defect class as a false clean.
let _publicMetricsCache = { at: 0, value: null };
// R-F4013 (C-90) — the TTLs moved to lib/metrics/publicMetricsCache.mjs, which
// holds two: a long one for a real measurement and a short one for a remembered
// failure. The single constant that used to live here is gone rather than left
// orphaned: a lone `PUBLIC_METRICS_TTL_MS` sitting beside a route that no longer
// reads it would read as though it still governed the cache, which is the exact
// shape of defect this workstream has been closing.

app.get('/api/public/metrics', async (req, res) => {
  const now = Date.now();
  // R-F4013 (C-90) — a FAILURE is now cached too, briefly.
  //
  // This route is unauthenticated and previously cached only a success, so while
  // the brain is slow, restarting (~10 min boot) or down, EVERY anonymous request
  // made its own upstream call, waited the full 8s timeout and wrote an
  // errorTracker record. One visitor was one upstream call; a crawler was
  // thousands. The 8s bound below is unchanged — what changed is that a failure is
  // remembered for 30s instead of rediscovered by every caller, which also stops
  // the error-ledger flood. See lib/metrics/publicMetricsCache.mjs for why the two
  // TTLs are deliberately asymmetric.
  if (!shouldQueryUpstream(_publicMetricsCache, now)) {
    return res.json({ ..._publicMetricsCache.value, cached: true });
  }
  let records = null;
  try {
    const base = process.env.ARIA_SERVICE_URL || '';
    const token = process.env.ARIA_API_TOKEN || process.env.ARIA_INTERNAL_TOKEN || '';
    if (base) {
      const r = await fetch(`${base}/api/aria/memory/tiers`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
        signal: AbortSignal.timeout(8000),
      });
      if (r.ok) {
        const d = await r.json();
        const n = d && d.knowledge && Number(d.knowledge.count);
        if (Number.isFinite(n) && n > 0) records = n;
      }
    }
  } catch (e) {
    // Fall through to records:null — an honest "—" beats a stale number.
    errorTracker.record('public_metrics', 'brain_fetch_failed', e);
  }
  // Store BOTH outcomes — the success-only write was the defect.
  _publicMetricsCache = nextCacheEntry(records, now);
  res.json({ ..._publicMetricsCache.value, cached: false });
});

// ── R-F2822: navigation entitlement, computed SERVER-SIDE ────────────────────
// The sidebar hand-maintained a `data-admin` flag on 3 of 11 gated links, so it
// drifted from lib/auth/operatorPages.mjs — the table the real gate uses — in
// BOTH directions: five links (bd-intelligence, vls-chain, sources, vault,
// aria-brain) rendered to every user and then 302'd them back to /dashboard.html
// with no explanation, while leads.html and design-partners.html were hidden from
// powerusers who ARE entitled to them (OPERATOR_VIEW_PAGES allows poweruser, but
// the reveal keyed on role === 'admin').
//
// This endpoint makes the SERVER the single source of truth: it answers "which
// gated pages may THIS caller navigate to", using the exact table + roleSatisfies()
// the gate itself uses (server.mjs:4808). The browser therefore performs NO
// authorization reasoning of its own — it only shows or hides what it is told.
// A new operator page added to operatorPages.mjs gets correct nav treatment with
// no sidebar edit, which is what stops this drifting again.
//
// NB this is not a security boundary — requirePageRole() is. It exists so the nav
// stops lying about what the user can do. It is deliberately fetched fresh rather
// than read from Auth.me(), which returns a login-time localStorage snapshot
// (public/js/app.js:154-157) and would go stale on a role change.
app.get('/api/auth/nav-pages', requireAuth, (req, res) => {
  try {
    // R-F2872 — resolve from the LIVE user record, not the token snapshot.
    // The JWT's `role` is baked in at login (users.mjs createToken), so reading
    // req.user.role went stale on a role change in exactly the way this
    // endpoint's own comment says it is avoiding: the FETCH was fresh, the DATA
    // inside it was not. Elevating a user to admin had no effect on their nav
    // until they next logged in, with nothing telling them why.
    // Falls back to the token role (and then 'analyst') so the internal-token
    // pseudo-user, which has no row, still resolves cleanly.
    const live = req.user?.userId ? findUserById(req.user.userId) : null;
    const role = live?.role || req.user?.role || 'analyst';
    res.json({ role, allowed: navPagesForRole(role) });
  } catch (err) {
    // Fail CLOSED: an empty allow-list hides the gated links rather than showing
    // links that would bounce. The server gate is unaffected either way.
    res.status(200).json({ role: null, allowed: [], error: 'nav_entitlement_unavailable' });
  }
});

app.put('/api/auth/profile', requireAuth, (req, res) => {
  try {
    const {
      fullName, telegramUsername, notifyDigest, notifyFlash, notifyPush,
      // R-F48b: editable org-context fields. Same shape as registration.
      accountType, companyName, companyCountry, companySize,
      sector, jobTitle,
      useCases, regions, languages, volumeEstimate, complianceNeeds,
      purposeStatement,
    } = req.body || {};
    const updates = {};
    if (fullName         !== undefined) updates.fullName         = fullName;
    if (telegramUsername !== undefined) updates.telegramUsername = telegramUsername;
    if (notifyDigest     !== undefined) updates.notifyDigest     = !!notifyDigest;
    if (notifyFlash      !== undefined) updates.notifyFlash      = !!notifyFlash;
    if (notifyPush       !== undefined) updates.notifyPush       = !!notifyPush;
    // R-F48b org-context — server-side bounds match createUser()
    if (accountType      !== undefined) updates.accountType      = String(accountType || '').slice(0, 32);
    if (companyName      !== undefined) updates.companyName      = String(companyName || '').slice(0, 200);
    if (companyCountry   !== undefined) updates.companyCountry   = String(companyCountry || '').slice(0, 80);
    if (companySize      !== undefined) updates.companySize      = String(companySize || '').slice(0, 32);
    if (sector           !== undefined) updates.sector           = String(sector || '').slice(0, 64);
    if (jobTitle         !== undefined) updates.jobTitle         = String(jobTitle || '').slice(0, 120);
    if (volumeEstimate   !== undefined) updates.volumeEstimate   = String(volumeEstimate || '').slice(0, 32);
    if (purposeStatement !== undefined) updates.purposeStatement = String(purposeStatement || '').slice(0, 600);
    if (useCases         !== undefined && Array.isArray(useCases)) {
      updates.useCases = useCases.slice(0, 20).map(s => String(s).slice(0, 64));
    }
    if (regions          !== undefined && Array.isArray(regions)) {
      updates.regions = regions.slice(0, 30).map(s => String(s).slice(0, 64));
    }
    if (languages        !== undefined && Array.isArray(languages)) {
      updates.languages = languages.slice(0, 20).map(s => String(s).slice(0, 16));
    }
    if (complianceNeeds  !== undefined && Array.isArray(complianceNeeds)) {
      updates.complianceNeeds = complianceNeeds.slice(0, 20).map(s => String(s).slice(0, 64));
    }
    const updated = updateUser(req.user.userId, updates);
    res.json(updated);
  } catch (err) {
    res.status(500).json({ error: 'Failed to update profile' });
  }
});

app.put('/api/auth/password', requireAuth, (req, res) => {
  try {
    const { currentPassword, newPassword } = req.body || {};
    if (!currentPassword || !newPassword) return res.status(400).json({ error: 'Current and new password required' });
    if (newPassword.length < 8) return res.status(400).json({ error: 'New password must be at least 8 characters' });

    const user = findUserById(req.user.userId);
    if (!user) return res.status(404).json({ error: 'User not found' });
    if (!verifyPassword(currentPassword, user.passwordHash)) {
      return res.status(401).json({ error: 'Current password incorrect' });
    }

    // R-F3332 — clearing the rotation flag lives with the write that satisfies
    // it. A gate whose clear path sits somewhere else is how an account gets
    // locked out permanently.
    //
    // R-F3835 — bump tokenVersion so every OTHER live session dies with the old
    // password. Without this a token stolen beforehand stayed valid for the rest
    // of its 7-day life, so changing your password did not evict the thief. The
    // admin-driven paths already did this (recovery-reset :6728, revokeTokens on
    // force-logout/suspend); only the two a user drives for themselves were missed.
    const nextVersion = (user.tokenVersion || 0) + 1;
    updateUser(req.user.userId, {
      passwordHash: hashPassword(newPassword),
      tokenVersion: nextVersion,
      ...rotationClearedFields(),
    });

    // ...and re-issue, because the bump also invalidates the CALLER's token.
    // public/set-password.html (the R-F3332 rotation flow) navigates to
    // /dashboard.html straight after this call reusing its stored token; without
    // a replacement the security fix becomes a lockout. The cookie must be
    // refreshed too — requirePageRole authenticates page NAVIGATIONS from it.
    const replacement = createToken(user.id, user.role, '7d', nextVersion);
    _setAuthCookie(res, replacement);
    res.json({ message: 'Password updated successfully', token: replacement });
  } catch (err) {
    res.status(500).json({ error: 'Failed to update password' });
  }
});

app.post('/api/auth/forgot-password', async (req, res) => {
  try {
    const { email } = req.body || {};
    if (!email) return res.status(400).json({ error: 'Email required' });

    const user = findUserByEmail(email);
    // Always return 200 — do not reveal if email exists
    if (user) {
      const resetCode = generateCode();
      updateUser(user.id, {
        resetCode,
        resetExpiry: new Date(Date.now() + 15 * 60 * 1000).toISOString(),
      });
      await sendPasswordResetEmail(email, user.fullName, resetCode).catch(() => {});
    }
    res.json({ message: 'If that email is registered, a reset code has been sent.' });
  } catch (err) {
    console.error('[Auth] Forgot password error:', err.message);
    res.status(500).json({ error: 'Failed to process request' });
  }
});

// R-F609 (2026-05-16) — reset-attempt throttle. Pre-R-F609 the 6-digit
// reset code was checked with a non-timing-safe `!==` compare AND there
// was no attempt limiter — an attacker could brute-force the 1M-key
// space within minutes. Now: 5 wrong codes / 15 min for the same email
// → 1-hour lockout. The IP rate-limit (TIERS.auth 10/15min) is still in
// front, but the per-email counter survives IP rotation. In-memory Map
// is fine for the single-instance seenode deploy; for multi-instance we
// would need Redis. Generic 400 response on lockout so attackers can't
// tell when they're locked vs simply wrong.
const _resetAttempts = new Map(); // email_lower → { count, firstAt, lockedUntil }
const _RESET_MAX_ATTEMPTS = 5;
const _RESET_WINDOW_MS = 15 * 60 * 1000;
const _RESET_LOCKOUT_MS = 60 * 60 * 1000;

function _resetThrottleCheck(emailLower) {
  const now = Date.now();
  const entry = _resetAttempts.get(emailLower);
  if (!entry) return { allowed: true };
  if (entry.lockedUntil && entry.lockedUntil > now) {
    return { allowed: false, reason: 'locked' };
  }
  // Reset the window if the first attempt aged out.
  if (entry.firstAt && now - entry.firstAt > _RESET_WINDOW_MS) {
    _resetAttempts.delete(emailLower);
    return { allowed: true };
  }
  return { allowed: true };
}

function _resetThrottleRecordFailure(emailLower) {
  const now = Date.now();
  const entry = _resetAttempts.get(emailLower) || { count: 0, firstAt: now };
  entry.count += 1;
  entry.firstAt = entry.firstAt || now;
  if (entry.count >= _RESET_MAX_ATTEMPTS) {
    entry.lockedUntil = now + _RESET_LOCKOUT_MS;
  }
  _resetAttempts.set(emailLower, entry);
  // R-F3860 — this map is keyed by a caller-supplied email and is reachable
  // unauthenticated, so without a sweep it grows one entry per distinct
  // address for the life of the process. Swept here, on the write that grows it.
  pruneAttemptMap(_resetAttempts, _RESET_WINDOW_MS);
  return entry;
}

function _resetThrottleClear(emailLower) {
  _resetAttempts.delete(emailLower);
}

app.post('/api/auth/reset-password', async (req, res) => {
  try {
    const { email, code, newPassword } = req.body || {};
    if (!email || !code || !newPassword) return res.status(400).json({ error: 'Email, code, and new password required' });
    if (newPassword.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters' });

    const emailLower = String(email).toLowerCase().trim();

    // R-F609: throttle BEFORE any user lookup so a locked-out email
    // can't probe whether a user exists via the timing of the 400.
    const throttle = _resetThrottleCheck(emailLower);
    if (!throttle.allowed) {
      console.warn(`[Auth] reset-password REJECTED throttle email=${emailLower} ip=${req.ip}`);
      return res.status(400).json({ error: 'Invalid or expired reset code' });
    }

    const user = findUserByEmail(emailLower);

    // R-F609: timing-safe 6-digit code compare. Pre-fix used `!==`
    // which leaks byte-by-byte timing — combined with the absent
    // throttle that made the 6-digit space practically brute-forceable.
    const expectedCode = user ? String(user.resetCode || '') : '';
    const providedCode = String(code || '');
    let codeOk = false;
    if (expectedCode && providedCode.length === expectedCode.length) {
      try {
        const crypto = await import('node:crypto');
        codeOk = crypto.timingSafeEqual(
          Buffer.from(expectedCode, 'utf8'),
          Buffer.from(providedCode, 'utf8'),
        );
      } catch {
        codeOk = false;
      }
    }

    if (!user || !codeOk) {
      _resetThrottleRecordFailure(emailLower);
      console.warn(`[Auth] reset-password FAIL code-mismatch email=${emailLower} ip=${req.ip}`);
      return res.status(400).json({ error: 'Invalid or expired reset code' });
    }
    if (user.resetExpiry && new Date(user.resetExpiry) < new Date()) {
      _resetThrottleRecordFailure(emailLower);
      return res.status(400).json({ error: 'Reset code expired. Request a new one.' });
    }

    // R-F3835 — a forgotten-password reset is the strongest signal an account may
    // be compromised, so it must evict every live session. No replacement token is
    // minted here: this route is UNAUTHENTICATED, and issuing a session would let
    // anyone holding a reset code skip the sign-in step entirely. The user signs
    // in with the new password, exactly as the response already says.
    updateUser(user.id, {
      passwordHash: hashPassword(newPassword),
      tokenVersion: (user.tokenVersion || 0) + 1,
      resetCode: null,
      resetExpiry: null,
    });

    // R-F609: clear the throttle and notify the owner. Notification is
    // best-effort (SMTP may be down on the seenode deploy); we don't
    // block the successful reset on email-send failure.
    _resetThrottleClear(emailLower);
    sendPasswordChangedNotification(user.email, user.fullName, req.ip || '').catch(
      (err) => console.warn('[Auth] post-reset email send failed:', err.message),
    );

    console.log(`[Auth] reset-password OK email=${user.email} ip=${req.ip}`);
    res.json({ message: 'Password reset successfully. You can now log in.' });
  } catch (err) {
    console.error('[Auth] Reset password error:', err.message);
    res.status(500).json({ error: 'Failed to reset password' });
  }
});

// ── R-F425: Recovery-token reset (operator escape hatch when SMTP is broken) ─
//
// Problem this solves: on a deploy where SMTP isn't configured,
// /api/auth/forgot-password silently no-ops (server.mjs:3481 always returns
// 200 to avoid enumeration). If the operator is also locked out of their
// admin account, there's no in-app path back to a working login — the legacy
// fix was a server-shell script, which doesn't work on PaaS hosts that only
// accept `git push` deploys.
//
// This endpoint provides a deliberate, explicit recovery flow:
//   1. Operator sets ADMIN_RECOVERY_TOKEN=<cryptographically random, >=32 chars>
//      on the deploy host (one env var, no shell needed).
//   2. They visit /recovery.html, enter their email + new password + the
//      token, and submit.
//   3. Server rewrites the password and they log in normally.
//   4. Operator rotates ADMIN_RECOVERY_TOKEN afterwards.
//
// Endpoint returns 404 when the env var is unset so attackers can't probe
// whether the recovery flow is enabled. Rate-limited via TIERS.auth.
app.post('/api/auth/recovery-reset', async (req, res) => {
  const expected = (process.env.ADMIN_RECOVERY_TOKEN || '').trim();
  if (!expected || expected.length < 32) {
    // Don't 503 — that signals the endpoint exists. 404 keeps it indistinguishable.
    return res.status(404).json({ error: 'Not found' });
  }

  const { email, newPassword, recoveryToken, createIfMissing } = req.body || {};
  if (typeof recoveryToken !== 'string' || recoveryToken.length !== expected.length) {
    console.warn(`[Auth] recovery-reset rejected (length mismatch) ip=${req.ip}`);
    return res.status(401).json({ error: 'Invalid recovery token' });
  }

  // Timing-safe compare to defeat byte-by-byte probing.
  const crypto = await import('node:crypto');
  const a = Buffer.from(recoveryToken, 'utf8');
  const b = Buffer.from(expected, 'utf8');
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) {
    console.warn(`[Auth] recovery-reset rejected (token mismatch) ip=${req.ip} email=${email}`);
    return res.status(401).json({ error: 'Invalid recovery token' });
  }

  if (!email || typeof email !== 'string') {
    return res.status(400).json({ error: 'Email required' });
  }
  if (!newPassword || typeof newPassword !== 'string' || newPassword.length < 8) {
    return res.status(400).json({ error: 'New password must be at least 8 characters' });
  }

  const normEmail = email.toLowerCase().trim();
  const user = findUserByEmail(normEmail);

  if (!user) {
    // R-F428: optional one-shot admin provisioning. When the operator is
    // locked out *and* no admin row exists for their email, the standard
    // recovery flow has nothing to rotate. createIfMissing lets the same
    // token mint the missing admin row in the same round-trip, so the
    // operator doesn't have to redeploy with new ADMIN_EMAIL/ADMIN_PASSWORD
    // env vars (which only fire when there are zero admins anyway). Single-
    // admin invariant preserved: if another admin row already exists with a
    // different email, we refuse rather than silently mint a parallel admin
    // — operator must delete the other row first (or accept it as canonical).
    if (createIfMissing === true) {
      const allUsers = listUsers();
      const otherAdmins = allUsers.filter(u => u.role === 'admin' && u.email !== normEmail);
      if (otherAdmins.length > 0) {
        console.warn(`[Auth] recovery-reset mint REFUSED for ${normEmail}: ${otherAdmins.length} other admin row(s) exist (${otherAdmins.map(a => a.email).join(', ')}) ip=${req.ip}`);
        return res.status(409).json({
          error: `Another admin already exists (${otherAdmins.map(a => a.email).join(', ')}). Either reset that account's password or delete it first; recovery refuses to mint a parallel admin.`,
        });
      }
      // Mint a fresh admin row. Username derived from the local-part of the
      // email + short random suffix to avoid collisions.
      const tmpUsername = (normEmail.split('@')[0] || 'admin').slice(0, 24) + '-' + generateCode().slice(0, 4);
      createUser({
        username: tmpUsername,
        email: normEmail,
        password: newPassword,
        fullName: 'Arkmurus Administrator',
        role: 'admin',
      });
      const fresh = findUserByEmail(normEmail);
      updateUser(fresh.id, {
        status: 'active',
        verificationCode: null,
        verificationExpiry: null,
      });
      console.log(`[Auth] recovery-reset MINTED admin ${normEmail} (id=${fresh.id}) via createIfMissing ip=${req.ip}`);
      return res.json({
        message: 'Admin account created. You can sign in with the new password now.',
        created: true,
      });
    }
    console.warn(`[Auth] recovery-reset: no user for email=${normEmail} ip=${req.ip} (pass createIfMissing:true to mint as admin)`);
    return res.status(404).json({ error: 'No user with that email. To create an admin account in one step, retry with the "Create admin if missing" option.' });
  }

  updateUser(user.id, {
    passwordHash: hashPassword(newPassword),
    status: 'active',
    tokenVersion: (user.tokenVersion || 0) + 1,
    resetCode: null,
    resetExpiry: null,
  });

  console.log(`[Auth] recovery-reset SUCCESS for ${user.email} (id=${user.id}) ip=${req.ip} — existing JWTs invalidated`);
  res.json({ message: 'Password reset. You can sign in with the new password now.' });
});

// ── R-F427: public auth posture diagnostic ──────────────────────────────────
//
// Lets the operator self-diagnose login / reset failures from the outside
// without ever needing shell access to the host. Returns counts + booleans
// only — no emails, hashes, or secret values are leaked. Falls under the
// standard /api/* rate limit (150 req / 15 min) rather than the strict auth
// tier so the operator isn't accidentally locked out of diagnostics while
// triaging a real outage.
app.get('/api/auth/system-status', (req, res) => {
  // R-F429: read user/admin state LIVE from listUsers() on every request.
  // The boot-time _adminIdentitySnapshot in users.mjs is only refreshed by
  // initAdminUser; runtime mutations (recovery-reset --createIfMissing in
  // R-F428, signup, future admin user mgmt) didn't update it, so the
  // endpoint was lying about admin count immediately after a mint. Only
  // `bootedAt` is read from the snapshot now; it's the one field that
  // genuinely must be boot-frozen.
  const snap = getAdminIdentitySnapshot();
  const allUsers = listUsers();
  const adminRows = allUsers.filter(u => u && u.role === 'admin');
  const envEmail = (process.env.ADMIN_EMAIL || '').toLowerCase().trim() || null;
  const matchesEnv = !!envEmail && adminRows.length === 1 && adminRows[0].email === envEmail;
  const dedicatedSet = !!process.env.EMAIL_HOST;
  const ariaFallbackAvailable =
    !dedicatedSet &&
    !!(process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST) &&
    !!process.env.ARIA_EMAIL_USER &&
    !!process.env.ARIA_EMAIL_PASS;
  const smtpConfigured =
    (dedicatedSet && !!process.env.EMAIL_USER && !!process.env.EMAIL_PASS) ||
    ariaFallbackAvailable;
  const smtpVia = smtpConfigured
    ? (dedicatedSet ? 'dedicated' : 'aria-fallback')
    : null;
  const smtpHost = smtpConfigured
    ? (dedicatedSet
        ? process.env.EMAIL_HOST
        : (process.env.ARIA_SMTP_HOST || process.env.ARIA_EMAIL_HOST))
    : null;
  const smtpUser = smtpConfigured
    ? (dedicatedSet ? process.env.EMAIL_USER : process.env.ARIA_EMAIL_USER)
    : null;
  const smtpPort = smtpConfigured
    ? parseInt(
        process.env.EMAIL_PORT ||
        (dedicatedSet ? '587' : (process.env.ARIA_SMTP_PORT || '465'))
      )
    : null;

  const recoveryTokenSet = !!process.env.ADMIN_RECOVERY_TOKEN;
  const recoveryTokenLen = (process.env.ADMIN_RECOVERY_TOKEN || '').length;
  const recoveryEnabled = recoveryTokenSet && recoveryTokenLen >= 32;

  let adminAnomaly = 'ok';
  if (adminRows.length === 0) adminAnomaly = 'no-admin';
  else if (adminRows.length > 1) adminAnomaly = 'multiple-admins';
  else if (envEmail && !matchesEnv) adminAnomaly = 'env-mismatch';

  res.json({
    bootedAt: snap.bootedAt,
    buildRev: CRUCIX_BUILD_REV,
    users: {
      total: allUsers.length,
      admins: adminRows.length,
    },
    admin: {
      envEmailSet: !!envEmail,
      matchesEnv,
      anomaly: adminAnomaly,
      // R-F432: bootstrap-trace so the operator can diagnose why
      // initAdminUser didn't auto-create an admin on this boot. We expose
      // env-var LENGTHS only (never values), plus the exact skip reason.
      bootstrap: getBootstrapTrace(),
    },
    smtp: {
      configured: smtpConfigured,
      via: smtpVia,
      host: smtpHost,
      user: smtpUser,
      port: smtpPort,
    },
    recoveryReset: {
      enabled: recoveryEnabled,
      tokenSet: recoveryTokenSet,
      tokenLengthOk: recoveryTokenLen >= 32,
    },
    endpoints: {
      login: '/api/auth/login',
      forgotPassword: '/api/auth/forgot-password',
      resetPassword: '/api/auth/reset-password',
      recoveryReset: '/api/auth/recovery-reset',
      recoveryPage: '/recovery.html',
    },
  });
});

// ── Admin: SMTP test ──────────────────────────────────────────────────────────
app.post('/api/admin/test-email', requireAdmin, async (req, res) => {
  const { to } = req.body || {};
  if (!to) return res.status(400).json({ error: 'to address required' });
  const result = await sendAdminNotification(
    'Arkmurus SMTP Test',
    `<p>This is a test email sent at ${new Date().toISOString()}.</p><p>If you received this, SMTP is configured correctly.</p>`
  ).catch(err => ({ sent: false, reason: err.message }));
  // Also try sending to the provided address
  const result2 = await sendVerificationEmail(to, 'Test User', '123456').catch(err => ({ sent: false, reason: err.message }));
  res.json({
    adminEmail: result,
    testEmail:  result2,
    smtpConfig: {
      host:      process.env.EMAIL_HOST     || '(not set)',
      port:      process.env.EMAIL_PORT     || '587 (default)',
      user:      process.env.EMAIL_USER     || '(not set)',
      passSet:   !!(process.env.EMAIL_PASS),
      secure:    process.env.EMAIL_SECURE   || 'false (default)',
      adminDest: process.env.ADMIN_EMAIL    || 'aria@imaria.io (default)',
    },
  });
});

// ── Admin Telegram test — verify alerter is configured + reaches chat ───────
// Mirrors /api/admin/test-email. Sends a sync test message to the
// configured admin Telegram channel. Useful for first-boot verification
// and whenever you rotate the bot token or chat id.
app.post('/api/admin/test-telegram', requireAdmin, async (req, res) => {
  if (!telegramAlerter?.isConfigured) {
    return res.status(503).json({
      configured: false,
      reason: 'TELEGRAM_BOT_TOKEN and/or TELEGRAM_CHAT_ID are not set.',
      env_check: {
        TELEGRAM_BOT_TOKEN: !!process.env.TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID:   !!process.env.TELEGRAM_CHAT_ID,
      },
    });
  }
  const msg =
    `🧪 *Arkmurus Telegram alerter test*\n\n` +
    `Fired by admin at ${new Date().toISOString()}. ` +
    `If you see this message, the pipeline is live — the same route ` +
    `fires for new-user-registration alerts, intel digests, and ` +
    `critical security-audit failures.`;
  try {
    await telegramAlerter.sendMessage(msg);
    return res.json({
      configured: true,
      sent: true,
      sent_at: new Date().toISOString(),
    });
  } catch (err) {
    return res.status(502).json({
      configured: true,
      sent: false,
      error: err?.message || String(err),
    });
  }
});

// ── Admin Channel Publisher — delegated to channelServerHooks ─────────────────
app.get('/api/admin/channel/state', requireAdmin, (req, res) => {
  try {
    // R-F2717 (#12) — the scheduled Golden path updates the CRON scheduler
    // (getSchedulerState2 via markPosted), NOT the legacy publisher (getSchedulerState
    // via recordPost). Reading only the publisher made this report "no posts" after a
    // successful scheduled Golden post. Include the cron scheduler + the durable
    // outcome ledger (§25) so a delivered post is actually visible here.
    res.json({
      ...(channelHooks.getSchedulerState() || {}),
      scheduler: channelHooks.getSchedulerState2 ? channelHooks.getSchedulerState2() : null,
      recent_outcomes: channelHooks.getRecentChannelOutcomes ? channelHooks.getRecentChannelOutcomes(25) : [],
    });
  } catch (err) {
    res.status(500).json({ error: 'Failed to get channel state', detail: err.message });
  }
});

app.post('/api/admin/channel/post', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_post');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_post');
    if (!bot || res.headersSent) return;
    const { title, summary, source, severity, country, sector } = req.body || {};
    if (!title) return res.status(400).json({ error: 'title is required' });

    const signal = { title, summary: summary || title, source: source || 'Admin', severity: severity || 'medium', timestamp: new Date().toISOString(), country, sector };
    const result = await channelHooks.publishSignal(signal, bot, { generateImage: true, registerKeyword: true, crossPostLinkedIn: false });
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: 'Failed to post', detail: err.message });
  }
});

app.post('/api/admin/channel/daily-brief', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_daily_brief');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_daily_brief');
    if (!bot || res.headersSent) return;
    const briefData = req.body || {};
    const post = channelHooks.formatDailyBrief(briefData);
    await sendTelegramChannelText(bot, post);
    channelHooks.recordPost();
    res.json({ posted: true, type: 'daily-brief', length: post.length });
  } catch (err) {
    res.status(502).json({ error: 'Failed to post daily brief', detail: err.message });
  }
});

// ── Channel Media Routes ──────────────────────────────────────────────────────
app.get('/api/admin/channel/media/infographic', requireAdmin, async (req, res) => {
  try {
    const { generateInfographicCard } = await import('./lib/telegram/channelMedia.mjs');
    const { title, subtitle, source, type } = req.query;
    const svg = generateInfographicCard({
      title: title || 'ARIA Intelligence',
      subtitle: subtitle || '',
      source: source || 'ARIA Intelligence',
      type: type || 'intel',
    });
    res.setHeader('Content-Type', 'image/svg+xml');
    res.send(svg);
  } catch (err) {
    res.status(500).json({ error: 'Failed to generate infographic', detail: err.message });
  }
});

app.post('/api/admin/channel/media/post-with-image', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_media_post');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_media_post');
    if (!bot || res.headersSent) return;
    const { title, summary, source, severity, country, sector, type } = req.body || {};
    if (!title) return res.status(400).json({ error: 'title is required' });

    const signal = { title, summary: summary || title, source: source || 'Admin', severity: severity || 'medium', timestamp: new Date().toISOString(), country, sector };
    const result = await channelHooks.publishSignal(signal, bot, { generateImage: true, registerKeyword: true, crossPostLinkedIn: false });
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: 'Failed to post with image', detail: err.message });
  }
});

// ── Channel Interactive Routes ────────────────────────────────────────────────
app.get('/api/admin/channel/interactive/stats', requireAdmin, async (req, res) => {
  try {
    const { getEngagementStats } = await import('./lib/telegram/channelInteractive.mjs');
    res.json(getEngagementStats());
  } catch (err) {
    res.status(500).json({ error: 'Failed to get engagement stats', detail: err.message });
  }
});

app.post('/api/admin/channel/interactive/poll', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_poll');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_poll');
    if (!bot || res.headersSent) return;
    const { sendPoll, buildPoll } = await import('./lib/telegram/channelMedia.mjs');
    const { question, options, isQuiz, correctOptionId, explanation } = req.body || {};
    if (!question || !options || options.length < 2) return res.status(400).json({ error: 'question and 2+ options required' });

    const pollData = buildPoll({ question, options, isQuiz, correctOptionId, explanation });
    const result = await sendPoll(bot, pollData);
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: 'Failed to send poll', detail: err.message });
  }
});

// ── LinkedIn Publisher Routes ─────────────────────────────────────────────────
app.get('/api/admin/linkedin/status', requireAdmin, async (req, res) => {
  try {
    const { getConfig, getState } = await import('./lib/linkedin/linkedinPublisher.mjs');
    res.json({ config: getConfig(), state: getState() });
  } catch (err) {
    res.status(500).json({ error: 'Failed to get LinkedIn status', detail: err.message });
  }
});

app.post('/api/admin/linkedin/post', requireAdmin, async (req, res) => {
  try {
    const { postTextUpdate, formatForLinkedIn, canPostNow } = await import('./lib/linkedin/linkedinPublisher.mjs');
    const { text, extraTags } = req.body || {};
    if (!text) return res.status(400).json({ error: 'text is required' });

    const { canPost, reason } = canPostNow();
    if (!canPost) return res.status(429).json({ error: reason });

    const liPost = formatForLinkedIn(text, { extraTags });
    const result = await postTextUpdate(liPost);
    res.json(result);
  } catch (err) {
    res.status(502).json({ error: 'Failed to post to LinkedIn', detail: err.message });
  }
});

// ── Channel Scheduler Admin Routes ────────────────────────────────────────────
app.get('/api/admin/channel/schedule', requireAdmin, async (req, res) => {
  try {
    res.json(channelHooks.getSchedulerState2());
  } catch (err) {
    res.status(500).json({ error: 'Failed to get schedule', detail: err.message });
  }
});

app.post('/api/admin/channel/welcome', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_welcome');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_welcome');
    if (!bot || res.headersSent) return;
    const post = channelHooks.buildWelcomePost();
    await sendTelegramChannelText(bot, post);
    res.json({ posted: true, length: post.length });
  } catch (err) {
    res.status(502).json({ error: 'Failed to post welcome', detail: err.message });
  }
});

app.post('/api/admin/channel/post-template', requireAdmin, async (req, res) => {
  try {
    if (TELEGRAM_GOLDEN_INTEL_ONLY) return blockNonGoldenTelegramIntel(res, 'admin_channel_template');
    const bot = telegramChannelBotOrResponse(res, 'admin_channel_template');
    if (!bot || res.headersSent) return;
    const { template, data } = req.body || {};
    if (!template || !data) return res.status(400).json({ error: 'template and data required' });

    let post;
    switch (template) {
      case 'case_file': post = channelHooks.buildCaseFile(data); break;
      case 'know_your_rights': post = channelHooks.buildKnowYourRights(data); break;
      case 'country_read': post = channelHooks.buildCountryRead(data); break;
      case 'morning_signal': post = channelHooks.buildMorningSignal(data); break;
      default: return res.status(400).json({ error: 'Unknown template: ' + template });
    }
    await sendTelegramChannelText(bot, post);
    channelHooks.markPosted(template);
    res.json({ posted: true, template, length: post.length });
  } catch (err) {
    res.status(502).json({ error: 'Failed to post template', detail: err.message });
  }
});

// ── Reply Keyword Router - handle user replies ────────────────────────────────
app.post('/api/admin/channel/reply', requireAdmin, async (req, res) => {
  try {
    const { text, userId } = req.body || {};
    if (!text) return res.status(400).json({ error: 'text is required' });
    const response = await channelHooks.handleReply(text, userId);
    res.json({ parsed: channelHooks.parseReply(text), response });
  } catch (err) {
    res.status(502).json({ error: 'Failed to process reply', detail: err.message });
  }
});

// ── Admin User Management Routes ──────────────────────────────────────────────

app.get('/api/admin/users', requireAdmin, (req, res) => {
  try {
    res.json(listUsers());
  } catch (err) {
    res.status(500).json({ error: 'Failed to list users' });
  }
});

app.put('/api/admin/users/:id', requireAdmin, async (req, res) => {
  try {
    const { role, status, notifyDigest, notifyFlash } = req.body || {};
    // R-F2036 — payment gate: paid tiers (pro/proIntel) are granted ONLY via the
    // Stripe billing webhook, never by admin action. This endpoint already
    // ignores `tier`, but reject it EXPLICITLY so it can never silently become a
    // back-door upgrade path (defense-in-depth + clear intent).
    if (req.body && req.body.tier !== undefined) {
      return res.status(400).json({ error: 'Tier cannot be changed here — paid tiers are managed via billing/Stripe only (R-F2036).' });
    }
    // R-F2170: validate role against the canonical set so a typo can't store an
    // unreachable role (which would lock the user out of every panel).
    if (role !== undefined && !ROLES.includes(role)) {
      return res.status(400).json({ error: `Invalid role '${role}'. Allowed: ${ROLES.join(', ')}` });
    }
    const existingUser = findUserById(req.params.id);
    if (!existingUser) return res.status(404).json({ error: 'User not found' });
    const admin = findUserById(req.user.userId);
    const updates = {};
    if (role         !== undefined) updates.role         = role;
    if (status       !== undefined) updates.status       = status;
    if (notifyDigest !== undefined) updates.notifyDigest = !!notifyDigest;
    if (notifyFlash  !== undefined) updates.notifyFlash  = !!notifyFlash;
    const updated = updateUser(req.params.id, updates);

    // R-F2986 — a suspend or a role change MUST cut the target's existing
    // sessions, not just block future logins. requireAuth (above) trusts the
    // token's baked-in role and never re-reads user.status, so without a
    // tokenVersion bump a suspended user keeps full access on their live
    // 7-day JWT and a demoted admin keeps role:admin until the token expires.
    // force-logout already does exactly this (revokeTokens); mirror it here.
    const roleChanged   = role   !== undefined && role   !== existingUser.role;
    const nowSuspended  = status === 'suspended' && existingUser.status !== 'suspended';
    if (roleChanged || nowSuspended) {
      try {
        revokeTokens(req.params.id);
      } catch (revErr) {
        console.error(`[Auth] R-F2986 revokeTokens failed for ${req.params.id}: ${revErr.message}`);
      }
    }

    // Emails + audit on status change
    if (status && status !== existingUser.status) {
      // R-F3654: the unsuspend arm used to be the THIRD branch of this chain,
      // behind a bare `status === 'active'` — so it could never execute. Every
      // reactivation of a suspended user therefore sent the WELCOME email
      // instead of the reactivation email, and wrote `approve` into the audit
      // log instead of `unsuspend`, which quietly falsified the admin audit
      // trail for that action. Ordering the specific case before the general
      // one is the fix; the two conditions are no longer overlapping.
      if (status === 'active' && existingUser.status === 'suspended') {
        await sendReactivationEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'unsuspend', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      } else if (status === 'active') {
        await sendWelcomeEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'approve', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      } else if (status === 'suspended') {
        await sendSuspensionEmail(existingUser.email, existingUser.fullName).catch(() => {});
        logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'suspend', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName });
      }
    }
    if (role && role !== existingUser.role) {
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'role_change', targetId: existingUser.id, targetEmail: existingUser.email, targetName: existingUser.fullName, notes: `${existingUser.role} → ${role}` });
    }

    res.json(updated);
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to update user' });
  }
});

app.post('/api/admin/users/:id/approve', requireAdmin, async (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    if (target.status === 'active') return res.status(400).json({ error: 'User is already active' });
    updateUser(target.id, { status: 'active' });
    await sendWelcomeEmail(target.email, target.fullName).catch(() => {});
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'approve', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    console.log(`[Auth] User approved: ${target.email} by ${admin?.email || req.user.userId}`);
    res.json({ ok: true, message: `${target.fullName} approved — welcome email sent` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to approve user' });
  }
});

app.post('/api/admin/users/:id/reject', requireAdmin, async (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    await sendRejectionEmail(target.email, target.fullName).catch(() => {});
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'reject', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    console.log(`[Auth] User rejected and removed: ${target.email} by ${admin?.email || req.user.userId}`);
    deleteUser(target.id);
    res.json({ ok: true, message: `${target.fullName} rejected — rejection email sent` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to reject user' });
  }
});

app.post('/api/admin/users/:id/force-logout', requireAdmin, (req, res) => {
  try {
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    if (req.params.id === req.user.userId) return res.status(400).json({ error: 'Cannot force-logout yourself' });
    revokeTokens(req.params.id);
    const admin = findUserById(req.user.userId);
    logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'force_logout', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    res.json({ ok: true, message: `${target.fullName}'s session has been revoked` });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to revoke session' });
  }
});

app.get('/api/admin/audit', requireAdmin, (req, res) => {
  const limit = Math.min(parseInt(req.query.limit) || 100, 500);
  res.json(getAuditLog(limit));
});

// ── Proxy routes for listener slash commands ─────────────────────────────
// The WhatsApp listener historically called fly.io directly via _ariaFetch
// for /forget, /purgecases, /purge-signals, and /report. That required
// ARIA_API_TOKEN to be in the listener process environment, which broke
// when seenode ran the listener as a separate worker without the env var
// (past incident 2026-04-09: /forget returned HTTP 401 because the
// listener process had no token, even though server.mjs did). These
// proxy routes let the listener call localhost:port/api/aria/<path>
// with INT_TOKEN, then ariaProxy uses _ariaHeaders() to inject the
// correct bearer token for the fly.io call. Single env var dependency,
// centralised in server.mjs.
app.post('/api/aria/session/forget',
  express.json({ limit: '8kb' }),
  requireAuth,
  (req, res) => ariaProxy(req, res, '/api/aria/session/forget', { method: 'POST', fallback: async ({ lastStatus, lastErr } = {}) => {
    res.status(503).json({
      error: 'session/forget unavailable',
      fly_status: lastStatus || 0,
      fly_error: lastErr || '',
      hint: lastStatus === 401
        ? 'fly.io rejected the bearer token — ARIA_API_TOKEN env var on seenode is missing, wrong, or out of sync with fly.io'
        : (lastStatus === 0 ? 'no response from fly.io — connectivity or ARIA_SERVICE_URL issue' : `fly.io returned HTTP ${lastStatus}`),
    });
  }}));

app.post('/api/aria/admin/purge-cases',
  express.json({ limit: '8kb' }),
  requireAdmin,  // R-F1818 (audit H4): destructive purge — admin only (was requireAuth)
  (req, res) => ariaProxy(req, res, '/api/aria/admin/purge-cases', { method: 'POST', fallback: async ({ lastStatus, lastErr } = {}) => {
    res.status(503).json({
      error: 'purge-cases unavailable',
      fly_status: lastStatus || 0,
      fly_error: lastErr || '',
    });
  }}));

app.post('/api/aria/admin/purge-signals',
  express.json({ limit: '16kb' }),
  requireAdmin,  // R-F1818 (audit H4): destructive purge — admin only (was requireAuth)
  (req, res) => ariaProxy(req, res, '/api/aria/admin/purge-signals', { method: 'POST', fallback: async ({ lastStatus, lastErr } = {}) => {
    res.status(503).json({
      error: 'purge-signals unavailable',
      fly_status: lastStatus || 0,
      fly_error: lastErr || '',
    });
  }}));

app.post('/api/aria/report',
  express.json({ limit: '32kb' }),
  requireAuth,
  (req, res) => ariaProxy(req, res, '/api/aria/report', { method: 'POST', fallback: async ({ lastStatus, lastErr } = {}) => {
    res.status(503).json({
      error: 'report builder unavailable',
      fly_status: lastStatus || 0,
      fly_error: lastErr || '',
    });
  }}));

// ── R-F110 (2026-05-09) + R-F117 (2026-05-09): catch-all /api/aria/* proxy ──
//
// New fly endpoints shipped this session (R-F68/F70/F71/F72/F73/F74/F75/
// F76/F77/F78/F80/F83/F84/F87a/F88/F89/F90/F104/F107/etc.) all 404'd at
// seenode because no explicit proxy route existed. Catch-all forwards
// any unmatched /api/aria/* path to fly with the authenticated session's
// bearer header. Comes AFTER all explicit routes so it only catches the
// gaps. Auth required; method-agnostic.
//
// R-F117 (2026-05-09): switched from app.all('/api/aria/*') to
// middleware-based registration. Express 5.1 uses path-to-regexp v7
// which deprecated the bare `*` wildcard suffix — `app.all('/api/aria/*')`
// either crashes at registration or silently registers no handler depending
// on the runtime version. The `app.use('/api/aria', ...)` pattern works
// reliably across Express 4 + 5 + 5.1. Order matters: explicit routes
// above this point already handle their paths and call res.send(), at
// which point Express stops the middleware chain — this only fires for
// unmatched /api/aria/* requests.
app.use('/api/aria', requireAuth, async (req, res, next) => {
  // Defensive: skip if a prior handler already responded
  if (res.headersSent) return next();
  // req.originalUrl preserves the full /api/aria prefix + path + query;
  // req.url is relative to the mount point (/api/aria) so we need the
  // original to forward verbatim.
  //
  // R-F2211 — central IDOR guard. Pin user_id to the JWT identity for every
  // non-admin request (query AND body), overriding any client value, so a
  // normal user can't read another tenant by forging ?user_id / body.user_id.
  // This kills the leak CLASS for the catch-all instead of patching each new
  // owner-scoped endpoint. Admin/internal keep see-all (see proxyPin.mjs).
  const fullPath = pinNonAdminUserId(req.originalUrl, req.user);
  if (!isPrivileged(req.user) && req.body && typeof req.body === 'object') {
    try {
      req.body.user_id = (req.user && req.user.userId) || '';
      // R-F2383 — mirror the query-side domain strip so a non-admin can't forge
      // body.user_email_domain to read another company's shared data.
      delete req.body.user_email_domain;
      delete req.body.user_email;
    } catch { /* immutable body — ignore */ }
  }
  ariaProxy(req, res, fullPath, {
    method: req.method,
    fallback: async ({ lastStatus, lastErr } = {}) => {
      if (res.headersSent) return;
      // R-F2775 — the verbose form leaked backend topology to every authenticated
      // caller: "fly endpoint unavailable" + the upstream path + the upstream HTTP
      // status + the raw upstream error string tells an attacker there is a second
      // service behind this one, where the request landed, and how it failed.
      // Privileged callers still get the full detail (they operate the thing);
      // customers get a generic 503.
      const generic = { error: 'Service temporarily unavailable' };
      // poweruser included: R-F2773 created the role precisely to OPERATE these
      // panels read-only, and a diagnostic surface that hides the upstream status
      // from the person diagnosing it is useless. isPrivileged() alone is
      // admin/internal only, so widen it here to the infra-read role.
      if (isPrivileged(req.user) || roleSatisfies(req.user?.role, ['poweruser'])) {
        Object.assign(generic, {
          error: 'fly endpoint unavailable',
          path: fullPath,
          fly_status: lastStatus || 0,
          fly_error: lastErr || '',
        });
      }
      res.status(lastStatus || 503).json(generic);
    },
  });
});

// ── Diagnostic: env-check for the seenode → fly.io proxy chain ─────────────
// Past incident 2026-04-09: /forget proxy returned 503 because ARIA_API_TOKEN
// was missing in server.mjs's process env on seenode (chat worked through a
// different code path that hand-built headers without going through the
// proxy). This endpoint reports whether the critical env vars are present
// (boolean only — never returns the actual values) so we can verify the
// proxy chain in 5 seconds without inspecting seenode env config manually.
//
// SECURITY NOTE: this endpoint is intentionally OPEN (no requireAuth /
// requireAdmin gate) so it's reachable directly from the browser address
// bar without devtools-console workarounds. The data it returns is
// booleans + lengths only — never the actual secret values. The
// information leak is "this Node app has these env vars set", which any
// attacker probing the app surface would discover anyway. For a single-
// user dev system this is an acceptable trade for fast diagnostics.
//
// If this is ever deployed to a multi-user or untrusted environment,
// re-add `requireAdmin` here AND change the path away from /api/admin/
// to make the security posture explicit.
app.get('/api/admin/env-check', requireAdmin, (req, res) => {
  // R-F2094 (2026-06-28 DD): re-added requireAdmin. The route's own comment said
  // to do this once the host became multi-user — self-serve signup made that true,
  // and unauth it leaked the secret-presence map + token sha256 fingerprint + pid.
  const token = process.env.ARIA_API_TOKEN || '';
  // SHA-256 fingerprint (first 12 hex chars) — non-reversible. Lets us
  // compare the actual env value against an expected value without ever
  // exposing the token. Same input → same fingerprint; different input
  // (even by one character) → different fingerprint.
  const tokenSha = token
    ? createHash('sha256').update(token).digest('hex').slice(0, 12)
    : '';
  // R-F1286 — DROPPED the first4/last4 disclosure. This endpoint is on a PUBLIC
  // host (imaria.io); exposing a token's prefix+suffix narrows a brute
  // force and is needless. The non-reversible sha256 fingerprint + length already
  // let a human verify the live token matches an expected value, with zero leak.
  const envState = {
    ARIA_SERVICE_URL: !!ARIA_SERVICE_URL,
    ARIA_API_TOKEN_present: !!process.env.ARIA_API_TOKEN,
    ARIA_API_TOKEN_length: token.length,
    ARIA_API_TOKEN_sha256_prefix: tokenSha,
    ARIA_INTERNAL_TOKEN_present: !!process.env.ARIA_INTERNAL_TOKEN,
    INT_TOKEN_present: !!process.env.INT_TOKEN,
    JWT_SECRET_present: !!process.env.JWT_SECRET,
    BRAIN_URL_present: !!process.env.BRAIN_URL,
    NODE_ENV: process.env.NODE_ENV || '(unset)',
    pid: process.pid,
    uptime_seconds: Math.round(process.uptime()),
  };
  res.json({
    env: envState,
    note: 'sha256_prefix is the first 12 chars of SHA-256(token) — non-reversible. Same input always gives the same fingerprint; compare it against the expected value to verify the live token without ever exposing it (R-F1286: no partial-token disclosure).',
  });
});

// ── User-panel consistency check — find phantom admins ────────────────────
// Cross-references the audit log against the actual user store to surface
// admin actions whose adminId doesn't resolve to a real user. This is the
// detection tool for the "missing users in user panel" symptom: phantom
// admins from the closed `aria-internal` hardcoded fallback (commit
// 9c0830d) would have left audit entries with adminId='aria-internal'
// (or any other value not in users.json).
//
// Returns:
//   {
//     totalAuditEntries: <int>,
//     totalUsers: <int>,
//     uniqueAdminIds: <int>,
//     unresolvedAdminIds: ["aria-internal", ...],  // phantom admin ids
//     phantomEntryCount: <int>,                     // total audit entries by phantoms
//     phantomActions: { "approve": 3, "delete": 1, ... },  // breakdown by action
//     samples: [{adminId, action, targetEmail, ts, ...}, ...],  // first 20 phantom entries
//     isClean: <bool>
//   }
app.get('/api/admin/audit-user-consistency', requireAdmin, (req, res) => {
  try {
    const allAudit = getAuditLog(500);  // full window
    const allUsers = listUsers();
    const userIdSet = new Set(allUsers.map(u => u.id));
    const adminIdsInAudit = new Set();
    const unresolvedAdminIds = new Set();
    const phantomEntries = [];
    const phantomActions = {};

    for (const entry of allAudit) {
      const aid = entry.adminId;
      if (!aid) continue;
      adminIdsInAudit.add(aid);
      if (!userIdSet.has(aid)) {
        unresolvedAdminIds.add(aid);
        phantomEntries.push(entry);
        phantomActions[entry.action] = (phantomActions[entry.action] || 0) + 1;
      }
    }

    res.json({
      totalAuditEntries: allAudit.length,
      totalUsers: allUsers.length,
      uniqueAdminIds: adminIdsInAudit.size,
      unresolvedAdminIds: Array.from(unresolvedAdminIds),
      phantomEntryCount: phantomEntries.length,
      phantomActions,
      samples: phantomEntries.slice(0, 20).map(e => ({
        ts:          e.ts,
        adminId:     e.adminId,
        adminEmail:  e.adminEmail || '(none)',
        action:      e.action,
        targetEmail: e.targetEmail,
        targetName:  e.targetName,
        notes:       e.notes,
      })),
      isClean: unresolvedAdminIds.size === 0,
    });
  } catch (err) {
    console.error('[Audit Consistency] Error:', err);
    res.status(500).json({ error: err.message || 'Consistency check failed' });
  }
});

app.delete('/api/admin/users/:id', requireAdmin, async (req, res) => {
  try {
    if (req.params.id === req.user.userId) {
      return res.status(400).json({ error: 'Cannot delete your own account' });
    }
    const target = findUserById(req.params.id);
    if (!target) return res.status(404).json({ error: 'User not found' });
    const admin = findUserById(req.user.userId);
    // Send rejection email if account was pending
    if (target.status === 'pending_approval' || target.status === 'pending_verification') {
      await sendRejectionEmail(target.email, target.fullName).catch(() => {});
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'reject', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    } else {
      logAudit({ adminId: req.user.userId, adminEmail: admin?.email || '', action: 'delete', targetId: target.id, targetEmail: target.email, targetName: target.fullName });
    }
    deleteUser(req.params.id);
    res.json({ message: 'User deleted' });
  } catch (err) {
    res.status(500).json({ error: err.message || 'Failed to delete user' });
  }
});

// ── Observability Admin Routes ────────────────────────────────────────────────
const errHandlers = errorTracker.apiHandler();
app.get('/api/admin/errors',           requireAdmin, errHandlers.getErrors);
app.get('/api/admin/source-health-errors', requireAdmin, errHandlers.getSourceHealth);
app.get('/api/admin/error-dashboard',  requireAdmin, errHandlers.getDashboard);

// ── Source Pruner Admin Routes ────────────────────────────────────────────────
// R-F1869 (audit DD-14): wrap each await so a sourcePruner throw can't become
// an unhandledRejection that hangs the response and leaks connections.
app.get('/api/admin/source-prune-report', requireAdmin, async (req, res) => {
  try {
    res.json(await sourcePruner.getSourceHealthReport());
  } catch (e) {
    console.error('[source-prune-report] failed:', e?.message);
    res.status(500).json({ error: 'source health report failed' });
  }
});
app.post('/api/admin/sources/:name/enable', requireAdmin, async (req, res) => {
  try {
    await sourcePruner.setSourceEnabled(req.params.name, true);
    res.json({ status: 'enabled', source: req.params.name });
  } catch (e) {
    console.error('[sources/enable] failed:', e?.message);
    res.status(500).json({ error: 'enable failed', source: req.params.name });
  }
});
app.post('/api/admin/sources/:name/disable', requireAdmin, async (req, res) => {
  try {
    await sourcePruner.setSourceEnabled(req.params.name, false);
    res.json({ status: 'disabled', source: req.params.name });
  } catch (e) {
    console.error('[sources/disable] failed:', e?.message);
    res.status(500).json({ error: 'disable failed', source: req.params.name });
  }
});

// ── Compliance entity screening (live lists) + version info ───────────────────
app.post('/api/compliance/entity-screen', requireAuth, async (req, res) => {
  const { entity_name } = req.body || {};
  if (!entity_name) return res.status(400).json({ error: 'entity_name required' });
  if (!redisAdapter.isConfigured) {
    return res.status(503).json({ error: 'Redis not configured — live compliance lists unavailable' });
  }
  try {
    const result = await screenEntity(entity_name, redisAdapter);
    res.json(result);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/compliance/versions', requireAuth, async (req, res) => {
  if (!redisAdapter.isConfigured) return res.json({ versions: {}, last_fetch: null });
  try {
    res.json(await getComplianceVersions(redisAdapter));
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Compliance audit trail ──────────────────────────────────────────────────
// R-F2606 — getComplianceAuditLog (lib/aria/complianceAudit.mjs → getAuditLog)
// returns the org-wide immutable log of EVERY user's compliance actions
// (screening/sanctions/classification queries + results). `user` is a filter,
// not an ownership scope — a non-admin caller would see all tenants' entries.
// Tightened requireAuth → requireAdmin (safe fix; no per-user scoping exists).
app.get('/api/compliance/audit', requireAdmin, async (req, res) => {
  try {
    const filters = {
      type:     req.query.type,
      user:     req.query.user,
      dateFrom: req.query.dateFrom,
      dateTo:   req.query.dateTo,
      entity:   req.query.entity,
      limit:    req.query.limit ? parseInt(req.query.limit) : 100,
    };
    const entries = await getComplianceAuditLog(filters);
    res.json({ entries, count: entries.length });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// R-F2606 — same org-wide compliance-log exposure as /api/compliance/audit; gate to admin.
app.get('/api/compliance/audit/export', requireAdmin, async (req, res) => {
  try {
    const format = req.query.format === 'csv' ? 'csv' : 'json';
    const filters = {
      type:     req.query.type,
      user:     req.query.user,
      dateFrom: req.query.dateFrom,
      dateTo:   req.query.dateTo,
      entity:   req.query.entity,
      limit:    req.query.limit ? parseInt(req.query.limit) : undefined,
    };
    const data = await exportAuditLog(format, filters);
    const contentType = format === 'csv' ? 'text/csv' : 'application/json';
    const filename = `compliance_audit_${new Date().toISOString().slice(0, 10)}.${format}`;
    res.setHeader('Content-Type', contentType);
    res.setHeader('Content-Disposition', `attachment; filename="${filename}"`);
    res.send(data);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Active tenders (deduped procurement portals) ──────────────────────────────
app.get('/api/tenders/active', requireAuth, async (req, res) => {
  try {
    const tenders = await procDedup.getActiveTenders(req.query.market);
    res.json(tenders);
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

// ── Billing Routes (Stripe — env-gated, no-op until STRIPE_SECRET_KEY set) ────
//
// Mounts /api/billing/{config,me,checkout,portal,webhook}. The webhook's
// raw-body parser is registered earlier (above express.json) so signature
// verification works. See lib/billing/{tiers,stripe,quotas,routes}.mjs for
// the per-tier limits and the env vars required to flip Stripe on.
app.use('/api/billing', createBillingRouter({
  requireAuth,
  findUserById,
  updateUser,
  listUsers,
}));

// ── R-F42 Public API (Lifter #5 from strategic review) ────────────────────────
//
// Mounts /api/keys/* (user-managed key CRUD, JWT-auth) and /api/v1/*
// (public chat surface, API-key auth). Both 503 when ENABLE_PUBLIC_API is
// unset so this is behaviour-neutral until the operator flips the env var
// (gated on first paying Pro Intelligence customer per the strategic review).
//
// The v1 chat endpoint reuses the same fly.io upstream + persona resolution
// + trivial short-circuit as /api/aria/chat. We don't rebuild the chain —
// we route requests through _publicApiChatProxy which mirrors the auth'd
// path's behaviour and returns the response object directly. Failure modes
// are surfaced as JSON, not propagated as exceptions, so a fly outage on
// the public surface shows up as a 502 with a useful detail field.
async function _publicApiChatProxy({ userId, message, sessionId }) {
  const sid = sessionId || `apiv1_${userId}_${Date.now()}`;

  // Trivial short-circuit (matches /api/aria/chat at line 2537-2546).
  const _trivial = trivialReply(message);
  if (_trivial !== null) {
    return { response: _trivial, session_id: sid, service: 'trivial', engine: 'short-circuit' };
  }

  // Resolve persona from the user record so the brain picks the right overlay.
  let _persona = '';
  try {
    const u = findUserById(userId);
    if (u && u.sector) _persona = String(u.sector).trim();
  } catch {}

  if (!ARIA_SERVICE_URL) {
    throw new Error('ARIA_SERVICE_URL not configured — fly upstream unreachable');
  }
  const r = await fetch(`${ARIA_SERVICE_URL}/api/aria/chat`, {
    method: 'POST',
    headers: _ariaHeaders(),
    body: JSON.stringify({ message, session_id: sid, user_id: userId, persona: _persona }),
    signal: AbortSignal.timeout(240000),
  });
  if (!r.ok) {
    const body = await r.text().catch(() => '');
    throw new Error(`fly upstream ${r.status}: ${body.slice(0, 200)}`);
  }
  const data = await r.json();
  data.service = data.service || 'python';
  data.engine = data.engine || 'aria-8layer';
  data.session_id = sid;
  return data;
}
// R-F3139 — vetting pass-through for the public API and MCP.
//
// The tenant is ALWAYS `userId` (the key's owner), appended by US. It is never
// read from the caller's body or query: the Python side derives its tenant
// from `user_id`, so a client-settable value would collapse the whole
// boundary that R-F3137 put in the primary key.
async function _vettingProxy({
  res = null, method = 'GET', path = '', userId = '',
  body = null, query = {}, raw = false,
} = {}) {
  if (!ARIA_SERVICE_URL) {
    const payload = { error: 'vetting service unavailable' };
    if (raw) return { status: 503, payload };
    return res.status(503).json(payload);
  }
  const params = new URLSearchParams({ ...query, user_id: userId });
  const url = `${ARIA_SERVICE_URL}/api/aria/vetting${path}?${params}`;
  const opts = {
    method,
    headers: _ariaHeaders(),
    signal: AbortSignal.timeout(45000),
  };
  if (body && (method === 'POST' || method === 'PATCH' || method === 'PUT')) {
    opts.body = JSON.stringify(body);
  }
  let status = 502;
  let payload = { error: 'vetting upstream unreachable' };
  try {
    const r = await fetch(url, opts);
    status = r.status;
    payload = await r.json().catch(() => ({ error: 'invalid upstream response' }));
  } catch (err) {
    payload = { error: 'vetting upstream unreachable', detail: String(err?.message || err).slice(0, 160) };
  }
  if (raw) return { status, payload };
  return res.status(status).json(payload);
}

app.use('/api/keys', createKeysRouter({ requireAuth, findUserById }));
app.use('/api/v1', createV1Router({
  findUserById,
  chatProxy: _publicApiChatProxy,
  vettingProxy: _vettingProxy,
}));

// ── R-F3140 — MCP server at /mcp ─────────────────────────────────────────
// Same `crx_…` keys, same tier gate, same scopes as /api/v1. See
// lib/mcp/routes.mjs for why MCP lives here rather than in the Python brain.
async function _mcpAuthenticate(req) {
  const m = (req.headers.authorization || '').match(/^Bearer\s+(.+)$/i);
  if (!m) return null;
  const keyRecord = authenticateKey(m[1].trim());
  if (!keyRecord) return null;
  const user = findUserById(keyRecord.userId);
  if (!user) return null;
  if (user.status && user.status !== 'active') {
    return { error: `account ${user.status} — contact support`, status: 403 };
  }
  if (user.role !== 'admin' && !tierAllows(user.tier || DEFAULT_TIER, 'publicApiEnabled')) {
    return { error: 'MCP access requires the Pro Intelligence tier', status: 403 };
  }
  // R-F3150 — the SAME per-key rate limit + daily quota /api/v1 applies. MCP
  // authenticated a key and then skipped both, so the surface most likely to
  // hammer the limiter (an LLM in a loop, not a human clicking) was the one
  // exempt from it.
  const budget = await consumeApiKeyBudget(keyRecord, user);
  if (!budget.allowed) {
    return { error: budget.reason, status: 429 };
  }
  return { keyRecord, user, scopes: scopesFor(keyRecord) };
}

app.use('/mcp', createMcpRouter({
  authenticate: _mcpAuthenticate,
  chatProxy: _publicApiChatProxy,
  vettingProxy: _vettingProxy,
  enabled: publicApiEnabled,
}));

console.log(
  `[PublicAPI] R-F42 routes mounted — ENABLE_PUBLIC_API=${publicApiEnabled() ? 'on' : 'off (503)'}`,
);
console.log(`[MCP] R-F3140 server mounted at /mcp`);

// ── Reports Routes (audit-grade PDF export — Lifter #3 from strategic review) ─
//
// Mounts /api/reports/{pdf,verify}. The PDF endpoint fetches canonical
// content from the Python brain via the internal token, signs with
// REPORT_SIGNING_KEY (or returns unsigned with a warning when unset),
// and streams the PDF back. The verify endpoint is public so a third
// party can confirm a PDF's integrity without an account.
app.use('/api/reports', createReportsRouter({
  requireAuth,
  findUserById,
  brainBaseUrl: (process.env.ARIA_FLY_URL
    || process.env.ARIA_BRAIN_URL
    || process.env.BRAIN_URL
    || 'https://aria-intel.fly.dev').trim(),
  brainInternalToken: (process.env.ARIA_INTERNAL_TOKEN
    || process.env.ARIA_API_TOKEN
    || '').trim(),
}));

// ── Status Routes (public status page — Lifter from strategic review §5.3) ────
//
// Mounts /api/status (public GET + admin mutations). Backed by
// runs/incidents.json with Redis mirror. Aggregates the brain-bridge
// boot verdict so a consumer sees the seenode → fly bridge state in real
// time.
//
// R-F3142 — this endpoint's HUMAN page (public/status.html) was retired and
// /status.html now 308s here. The endpoint itself is unchanged and is now the
// surface terms.html names as our contractual publication point for
// availability, so it is load-bearing on its own, not page decoration.
app.use('/api/status', createStatusRouter({
  requireAdmin,
  getBrainBridgeVerdict,
}));

// ── Push Notification Routes ──────────────────────────────────────────────────

app.get('/api/push/vapid-public-key', (req, res) => {
  const publicKey = getVapidPublicKey();
  if (!publicKey) return res.status(503).json({ error: 'Push notifications not initialized' });
  res.json({ publicKey });
});

app.post('/api/push/subscribe', requireAuth, (req, res) => {
  try {
    const { subscription } = req.body || {};
    if (!subscription) return res.status(400).json({ error: 'subscription object required' });
    saveSubscription(req.user.userId, subscription);
    updateUser(req.user.userId, { notifyPush: true });
    res.json({ message: 'Subscribed to push notifications' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to save subscription' });
  }
});

app.delete('/api/push/unsubscribe', requireAuth, (req, res) => {
  try {
    removeSubscription(req.user.userId);
    updateUser(req.user.userId, { notifyPush: false });
    res.json({ message: 'Unsubscribed from push notifications' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to remove subscription' });
  }
});

app.post('/api/push/test', requireAdmin, async (req, res) => {
  try {
    await pushFlash('Test Alert', 'This is a test push notification from Arkmurus');
    res.json({ message: 'Test push sent' });
  } catch (err) {
    res.status(500).json({ error: 'Failed to send test push' });
  }
});

// ── Chat REST API ─────────────────────────────────────────────────────────────

// GET /api/chat/users — list all users (id, username, fullName) for contact list
app.get('/api/chat/users', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  const users = listUsers().filter(u => u.status === 'active' && u.id !== req.user.userId);
  res.json(users.map(u => ({ id: u.id, username: u.username, fullName: u.fullName, role: u.role })));
});

// GET /api/chat/conversations — summary list for sidebar
app.get('/api/chat/conversations', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  const summaries = getConversationSummaries(req.user.userId);
  // Enrich with user info
  const enriched = summaries.map(s => {
    const u = findUserById(s.userId);
    return {
      ...s,
      username: u?.username || 'Unknown',
      fullName: u?.fullName || 'Unknown',
      role: u?.role || 'viewer'
    };
  });
  res.json(enriched);
});

// GET /api/chat/messages/:userId — conversation history
app.get('/api/chat/messages/:userId', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  if (!findUserById(req.params.userId) && req.params.userId !== 'aria') return res.status(404).json({ error: 'User not found' });
  const msgs = getConversation(req.user.userId, req.params.userId, 100);
  markRead(req.user.userId, req.params.userId);
  res.json(msgs);
});

// R-F2732 — group lifecycle is server-authoritative; membership is checked in
// the store on every read and send, never trusted from client-side state.
let notifyNetworkConversation = () => {};
app.post('/api/chat/groups', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  try {
    const requested = Array.isArray(req.body?.memberIds) ? req.body.memberIds.map(String) : [];
    const valid = [...new Set(requested)].filter(id => {
      const user = findUserById(id);
      return id !== req.user.userId && user?.status === 'active' && user.networkVisible;
    });
    if (valid.length !== new Set(requested.filter(id => id !== req.user.userId)).size) return res.status(400).json({ error: 'Every group member must be an active user' });
    const group = createGroup(req.user.userId, req.body?.name, valid);
    notifyNetworkConversation(group);
    res.status(201).json(group);
  } catch (error) {
    res.status(400).json({ error: error.message });
  }
});

app.get('/api/chat/conversation/:conversationId', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  const conversation = getConversationById(req.params.conversationId, req.user.userId, 100);
  if (!conversation) return res.status(404).json({ error: 'Conversation not found' });
  markConversationRead(req.user.userId, conversation.id);
  res.json(conversation);
});

// GET /api/chat/unread — total unread count for badge
app.get('/api/chat/unread', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  res.json({ count: unreadCount(req.user.userId) });
});

// ── R-F2349 · Profile photo API ───────────────────────────────────────────
// ONE photo per user, uploaded on the main profile, shared by the Network
// roster + sidebar avatar. Stored as a file on the durable volume keyed by id.
const _AVATAR_MAX_BYTES = 600 * 1024;   // decoded cap; client resizes to ~256px

// POST /api/profile/photo { dataUrl } — set my photo (self only).
app.post('/api/profile/photo', requireAuth, express.json({ limit: '3mb' }), (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  const uid = String(req.user.userId).replace(/[^A-Za-z0-9]/g, '');
  const dataUrl = String(req.body?.dataUrl || '');
  const m = dataUrl.match(/^data:(image\/(?:jpeg|png|webp));base64,([A-Za-z0-9+/=]+)$/);
  if (!m || !uid) return res.status(400).json({ error: 'Expected a JPEG, PNG or WebP image.' });
  let buf;
  try { buf = Buffer.from(m[2], 'base64'); } catch { return res.status(400).json({ error: 'Bad image data.' }); }
  if (!buf.length || buf.length > _AVATAR_MAX_BYTES) {
    return res.status(413).json({ error: 'Image too large — please keep it under 600 KB.' });
  }
  try {
    writeFileSync(join(AVATAR_DIR, uid), buf);
    const updated = updateUser(uid, { avatarUpdatedAt: new Date().toISOString(), avatarMime: m[1] });
    return res.json({ ok: true, avatarUrl: updated?.avatarUrl || null });
  } catch (e) {
    return res.status(500).json({ error: 'Could not save your photo.' });
  }
});

// GET /api/profile/photo/:id — public (an <img> cannot send a Bearer token).
// Versioned via ?v= so the immutable cache is busted whenever the photo changes.
app.get('/api/profile/photo/:id', (req, res) => {
  const id = String(req.params.id || '').replace(/[^A-Za-z0-9]/g, '');
  const u = id && findUserById(id);
  if (!u || !u.avatarUpdatedAt) return res.status(404).end();
  const file = join(AVATAR_DIR, id);
  if (!existsSync(file)) return res.status(404).end();
  try {
    res.setHeader('Content-Type', u.avatarMime || 'image/jpeg');
    res.setHeader('X-Content-Type-Options', 'nosniff');   // defense-in-depth (R-F2349 review)
    res.setHeader('Cache-Control', 'public, max-age=31536000, immutable');
    return res.end(readFileSync(file));
  } catch { return res.status(404).end(); }
});

// DELETE /api/profile/photo — remove my photo (self only).
app.delete('/api/profile/photo', requireAuth, (req, res) => {
  if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
  const uid = String(req.user.userId).replace(/[^A-Za-z0-9]/g, '');
  try { if (existsSync(join(AVATAR_DIR, uid))) unlinkSync(join(AVATAR_DIR, uid)); } catch {}
  try { updateUser(uid, { avatarUpdatedAt: null, avatarMime: null }); } catch {}
  return res.json({ ok: true });
});

app.get('/events', (req, res) => {
  // SECURITY 2026-04-09: this stream broadcasts the entire sweep payload
  // (intel signals, news, opportunities, BD pipeline state) — previously
  // anyone with the URL could subscribe and receive confidential data.
  // R-F2389 — no longer accepts a raw JWT in ?token= (that leaks the 7-day
  // credential into access logs / browser history / Referer). Mirrors
  // /api/search/deep: localhost, OR the internal service token, OR an
  // Authorization: Bearer JWT header, OR a short-lived single-use SSE ticket
  // via ?ticket= (issued by POST /api/sse/ticket).
  // R-F3833 — was keyed off the forgeable req.ip, so a 6PN peer sending
  // `X-Forwarded-For: 127.0.0.1` could subscribe to the entire sweep payload
  // (intel signals, news, opportunities, BD pipeline state) unauthenticated.
  if (!localhostBypassAllowed(req)) {
    const header = req.headers.authorization?.replace('Bearer ', '') || '';
    const internalToken = (process.env.ARIA_INTERNAL_TOKEN || '').trim();
    if (internalToken && header === internalToken) {
      /* internal service — allowed */
    } else if (header) {
      try { verifyToken(header); }
      catch { return res.status(401).json({ error: 'Invalid or expired token' }); }
    } else {
      const payload = redeemSseTicket(req.query.ticket);
      if (!payload) return res.status(401).json({ error: 'Authentication required (SSE ticket invalid or expired)' });
      req.user = payload;
    }
  }
  res.writeHead(200, {
    'Content-Type': 'text/event-stream',
    'Cache-Control': 'no-cache',
    'Connection': 'keep-alive',
    // R-F2383 — dropped `Access-Control-Allow-Origin: *`: this is an authenticated
    // stream (JWT/internal-token) and same-origin EventSource needs no CORS grant;
    // `*` on authed data is a needless misconfiguration.
    'Referrer-Policy': 'no-referrer',
  });
  res.write('data: {"type":"connected"}\n\n');
  sseClients.add(res);
  req.on('close', () => sseClients.delete(res));
});

function broadcast(data) {
  const msg = `data: ${JSON.stringify(data)}\n\n`;
  for (const client of sseClients) {
    try { client.write(msg); } catch { sseClients.delete(client); }
  }
}

// === Sweep Cycle ===
async function runSweepCycle() {
  if (sweepInProgress) {
    console.log('[Crucix] Sweep already in progress, skipping');
    return;
  }

  sweepInProgress = true;
  sweepStartedAt = new Date().toISOString();
  broadcast({ type: 'sweep_start', timestamp: sweepStartedAt });
  console.log(`\n${'='.repeat(60)}`);
  console.log(`[Crucix] Starting sweep at ${logTime()} (London)`);
  console.log(`${'='.repeat(60)}`);

  try {
    const rawData = await fullBriefing();

    // Push top defence/procurement signals into the Python brain queue (fire-and-forget)
    pushSignalsToBrain(rawData).catch((_ps) => { console.warn('[R-F1638] pushSignalsToBrain failed:', _ps?.message); });

    console.log('[Crucix] Fetching extended intelligence sources...');
    const [unscData, centralBanksData, thinkTanksData, tradeData, opensanctionsData] =
      await Promise.allSettled([
        fetchUNSecurityCouncil(),
        fetchCentralBanks(),
        fetchThinkTanks(),
        fetchTradeFLows(),
        fetchOpenSanctions(),
      ]).then(results => results.map(r => r.status === 'fulfilled' ? r.value : null));

    rawData.unsc          = unscData;
    rawData.centralBanks  = centralBanksData;
    rawData.thinkTanks    = thinkTanksData;
    rawData.tradeFlows    = tradeData;
    rawData.opensanctions = opensanctionsData;
    // rawData.gdelt is already set by fullBriefing() — no duplicate call needed

    writeFileSync(join(RUNS_DIR, 'latest.json'), JSON.stringify(rawData, null, 2));
    lastSweepTime = new Date().toISOString();

    // Update source health tracker (in-memory + persistent learning store)
    updateSourceHealth(rawData.timing);
    for (const [name, info] of Object.entries(rawData.timing || {})) {
      try { recordSourceSweep(name, info.status, info.ms); } catch {}
    }

    console.log('[Crucix] Synthesizing dashboard data...');
    const synthesized = await synthesize(rawData);
    // R-F2315 — synthesize() returns a fresh V2 object that DROPS opensanctions,
    // so currentData.opensanctions was always undefined → the channel's sanctions
    // spotlight (R-F2312) + daily sanctions signals (R-F2310) never fired in prod
    // (green unit tests masked it — they passed the field in directly). Re-attach it.
    synthesized.opensanctions = rawData.opensanctions;
    // R-F2416 — synthesize() also drops the OFAC + export-control sweep sources.
    // These are now REAL feeds (Federal Register: recent OFAC sanctions actions
    // + BIS export-control RULES), so re-attach them for the dashboard's
    // "Sanctions & Export Actions" widget. Honest-empty when the source failed
    // (status 'error', updates []). Same drop-and-reattach shape as opensanctions.
    synthesized.ofacActions = (rawData.sources && rawData.sources.OFAC) || null;
    synthesized.exportControlActions = (rawData.sources && rawData.sources.ExportControls) || null;
    // R-F2601 — trade.gov CSL is search/watchlist based. Re-attach the source
    // so the Golden Intel promotion bridge can push only concrete official hits.
    synthesized.csl = (rawData.sources && rawData.sources.CSL) || null;

    const delta = memory.addRun(synthesized);
    synthesized.delta = delta;

    archiveRunWithEntities(synthesized);

    const correlations = correlate(synthesized);
    synthesized.correlations = correlations;
    if (correlations.length > 0) {
      console.log(`[Crucix] ${correlations.length} regional correlations detected`);
    }

    // Polymarket arbitrage: compare market odds vs OSINT severity
    const arbitrage = detectArbitrage(synthesized.polymarket, correlations);
    synthesized.arbitrage = arbitrage;
    if (arbitrage.length > 0) {
      console.log(`[Crucix] ${arbitrage.length} Polymarket arbitrage signals detected`);
    }

    if (llmProvider?.isConfigured) {
      try {
        console.log('[Crucix] Generating LLM trade ideas...');
        const previousIdeas = memory.getLastRun()?.ideas || [];
        const llmIdeas = await generateLLMIdeas(llmProvider, synthesized, delta, previousIdeas);
        if (llmIdeas) {
          synthesized.ideas = llmIdeas;
          synthesized.ideasSource = 'llm';
          console.log(`[Crucix] LLM generated ${llmIdeas.length} ideas`);
        } else {
          synthesized.ideas = [];
          synthesized.ideasSource = 'llm-failed';
        }
      } catch (llmErr) {
        console.error('[Crucix] LLM ideas failed (non-fatal):', llmErr.message);
        synthesized.ideas = [];
        synthesized.ideasSource = 'llm-failed';
      }
    } else {
      synthesized.ideas = [];
      synthesized.ideasSource = 'disabled';
    }

    // Entity trajectory — computed from archive history
    synthesized.entityTrajectory = analyzeEntityTrajectory(14);

    // Self-learning: detect sales opportunities on every sweep (with retry)
    const opportunities = await reliableRun('Opportunity Detection', detectOpportunities, [synthesized], {
      maxRetries: 1,
      onFailure: async (name, err) => {
        if (telegramAlerter?.isConfigured) telegramAlerter.sendMessage(`⚠️ ${name} failed: ${err.message}`).catch(() => {});
      },
    });
    synthesized.opportunities = opportunities || [];
    if (opportunities?.length > 0) {
      console.log(`[Self] ${opportunities.length} opportunity/ies detected (top: ${opportunities[0]?.market} score:${opportunities[0]?.score})`);
    }

    // Inject saved patterns + explorer findings into synthesized data for BD brain
    try {
      const { getPatterns, getExplorerFindings } = await import('./lib/self/learning_store.mjs');
      const patterns = getPatterns();
      if (patterns?.patterns?.length) synthesized.patterns = patterns.patterns;
      const explorer = getExplorerFindings();
      if (explorer?.findings) synthesized.explorerFindings = explorer;
    } catch (e) { console.warn('[Crucix] Pattern/explorer inject failed (non-fatal):', e.message); }

    // BD Intelligence: real tenders + strategic ideas (with retry — most valuable output)
    const bdResult = await reliableRun('BD Intelligence', runBDIntelligence, [synthesized, null, llmProvider], {
      maxRetries: 2, delayMs: 5000,
      onFailure: async (name, err) => {
        if (telegramAlerter?.isConfigured) telegramAlerter.sendMessage(`🚨 ${name} FAILED after retries: ${err.message}`).catch(() => {});
      },
    });
    if (bdResult) {
      synthesized.bdIntelligence = bdResult;
      console.log(`[BD] ${bdResult.counts.activeTenders} tenders · ${bdResult.counts.strategicIdeas} ideas · ${bdResult.counts.pipelineDeals} pipeline`);
    } else {
      console.error('[BD] BD intelligence failed after retries — using cached data');
    }

    // R-F2557 — push BD opportunities + OpenSanctions findings to the Python Golden
    // Intel promotion bridge. Node/Python share no store, so this HTTP push is the
    // only way these Node-tier findings reach the signal feed. Non-fatal.
    try {
      const { pushPromotionsToBrain } = await import('./apis/promotion_bridge.mjs');
      await pushPromotionsToBrain(synthesized);
    } catch (e) { console.warn('[PromotionBridge] push failed (non-fatal):', e.message); }

    // Check restart flag — apply pending self-updates after sweep completes
    if (isRestartPending()) {
      console.log('[Self] Restart flag detected — will restart after current sweep to apply updates');
    }

    // Telegram alerts handled exclusively by onSweepComplete (3-hour cadence + new intel check)

    memory.pruneAlertedSignals();
    currentData = synthesized;

    // Store significant signals in Intel Ledger for ARIA long-term memory
    try {
      const { ingestSweepSignals } = await import('./lib/aria/intel_ledger.mjs');
      ingestSweepSignals(currentData);
    } catch (e) { console.warn('[Intel Ledger] Ingest error (non-fatal):', e.message); }

    // Scan for competitor movements
    try {
      const { scanForCompetitorMoves } = await import('./lib/aria/competitors.mjs');
      scanForCompetitorMoves(currentData);
    } catch (e) { console.warn('[Competitors] Scan error (non-fatal):', e.message); }

    if (!TELEGRAM_GOLDEN_INTEL_ONLY && telegramAlerter && telegramAlerter.isConfigured) {
      try {
        await telegramAlerter.onSweepComplete(currentData);
        // alert cadence managed by telegram.mjs
      } catch (err) {
        console.error('[Crucix] Telegram alert error:', err.message);
      }
    } else if (TELEGRAM_GOLDEN_INTEL_ONLY) {
      console.log('[Telegram] Sweep alerts skipped — Golden Intel only');
    }

    // ── Channel Publisher: delegated to channelServerHooks ────────────────
    try {
      const bot = { botToken: config.telegram.botToken, chatId: config.telegram.chatId, channelId: config.telegram.channelId };
      const result = await channelHooks.runChannelSweep(currentData, bot);
      if (result.posted > 0) console.log('[ChannelSweep] Posted', result.posted, 'signals');
    } catch (err) {
      console.error('[ChannelSweep] Error:', err.message);
    }

    // Flash push for critical correlations
    const critFlash = (currentData.correlations || []).filter(c => c.severity === 'critical');
    if (critFlash.length > 0) {
      const top = critFlash[0];
      pushFlash(
        `Critical Intel: ${top.region}`,
        `Multi-source critical signal detected — ${top.topSignals?.[0]?.text?.substring(0, 80) || 'view dashboard for details'}`,
        '/dashboard/brief'
      ).catch(e => console.warn('[Push] flash push failed:', e.message));
    }

    broadcast({ type: 'update', data: currentData });

    // Push sweep data to Python ARIA service for intel layer updates
    pushSweepToARIA(currentData).catch(() => {});

    {
      const m = currentData.meta || {};
      const partFrag = m.sourcesPartial ? ` (${m.sourcesPartial} partial)` : '';
      const failFrag = m.sourcesFailed  ? ` · ${m.sourcesFailed} failed` : '';
      console.log(`[Crucix] Sweep complete — ${m.sourcesOk}/${m.sourcesQueried} sources fully OK${partFrag}${failFrag}`);
    }
    console.log(`[Crucix] ${currentData.ideas.length} ideas (${synthesized.ideasSource}) | ${currentData.news.length} news | ${currentData.newsFeed.length} feed items`);
    if (delta?.summary) console.log(`[Crucix] Delta: ${delta.summary.totalChanges} changes, ${delta.summary.criticalChanges} critical, direction: ${delta.summary.direction}`);
    if (correlations.length > 0) console.log(`[Crucix] Correlations: ${correlations.map(c => `${c.region}(${c.severity})`).join(', ')}`);
    console.log(`[Crucix] Next sweep at ${logTimeShort(new Date(Date.now() + config.refreshIntervalMinutes * 60000))} (London)`);

    // Auto-classify pending outcomes using ALL current sweep signals (non-blocking)
    try {
      const { autoClassifyOutcomes, pruneOldData } = await import('./lib/self/learning_store.mjs');
      const allSignals = [
        ...(correlations || []).flatMap(c => c.topSignals || []),
        ...(synthesized.tg?.urgent || []),
        ...(synthesized.tg?.top || []),
        ...(synthesized.defenseNews?.updates || []).map(d => ({ text: d.title + ' ' + (d.content || ''), source: d.source })),
        ...(synthesized.newsFeed || []).map(n => ({ text: n.title + ' ' + (n.description || ''), source: n.source })),
      ].slice(0, 300);
      const classified = autoClassifyOutcomes(allSignals);
      if (classified > 0) console.log(`[Crucix] Auto-classified ${classified} pending signal outcome(s)`);
      pruneOldData();
    } catch (e) { console.warn('[Crucix] Auto-classify error (non-fatal):', e.message); }

    // Graceful restart to apply any self-deployed modules
    if (isRestartPending()) {
      clearRestartFlag();
      triggerGracefulRestart(5000);
    }

  } catch (err) {
    console.error('[Crucix] Sweep failed:', err.message);
    broadcast({ type: 'sweep_error', error: err.message });
  } finally {
    sweepInProgress = false;
  }
}

// === Startup ===
async function start() {
  const port = config.port;

  console.log(`
  ╔══════════════════════════════════════════════╗
  ║           ARIA INTELLIGENCE ENGINE         ║
  ║          Local Palantir · 36+ Sources        ║
  ╠══════════════════════════════════════════════╣
  ║  Dashboard:  http://localhost:${port}${' '.repeat(Math.max(0, 14 - String(port).length))}║
  ║  Search:     http://localhost:${port}/search.html${' '.repeat(Math.max(0, 8 - String(port).length))}║
  ║  Health:     http://localhost:${port}/api/health${' '.repeat(Math.max(0, 4 - String(port).length))}║
  ║  Refresh:    Every ${config.refreshIntervalMinutes} min${' '.repeat(20 - String(config.refreshIntervalMinutes).length)}║
  ║  LLM:        ${(config.llm.provider || 'disabled').padEnd(31)}║
  ║  Telegram:   ${config.telegram.botToken ? 'enabled' : 'disabled'}${' '.repeat(config.telegram.botToken ? 24 : 23)}║
  ║  Discord:    ${config.discord?.botToken ? 'enabled' : config.discord?.webhookUrl ? 'webhook only' : 'disabled'}${' '.repeat(config.discord?.botToken ? 24 : config.discord?.webhookUrl ? 20 : 23)}║
  ╚══════════════════════════════════════════════╝
  `);

  const server = createServer(app);

  // ── Socket.io — Real-time Chat ─────────────────────────────────────────────
  // R-F829 (2026-05-23): replace `origin:'*'` with an explicit allowlist.
  // '*' is incompatible with credentials and let any origin's JS connect
  // to the chat socket. Allow the deployment origin (APP_URL) + the Fly
  // app URL + localhost for dev. Browsers actually serving pages from
  // those origins are the only legitimate socket-io clients.
  const _io_allowed_origins = (() => {
    const set = new Set();
    const add = (u) => { if (u) set.add(u.replace(/\/$/, '')); };
    add(process.env.APP_URL);
    add('https://imaria.io');         // R-F2655: sole canonical production host.
    // R-F3343 — www SERVES THE APP, so its origin has to be trusted.
    //
    // R-F2655 narrowed this list to the apex as "the sole canonical production
    // host". That is only safe if www redirects to the apex, and it does not:
    // probed live 2026-07-28, https://www.imaria.io/ returns 200 with NO
    // redirect, while https://intel.imaria.io/ does not resolve at all (000).
    // So a browser genuinely can be sitting on www, and its socket handshake
    // carries Origin: https://www.imaria.io — which this allowlist rejected with
    // `cb(new Error(...))` a few lines below. Real-time chat was dead there, with
    // no server-side error a user could see: the connection is simply refused.
    //
    // The tree was in an incoherent middle state — www serves, the allowlist
    // excludes it — and there are two coherent ends: redirect www to the apex, or
    // trust the origin we actually serve from. Redirecting changes user-visible
    // URLs and is an operator call about canonicalisation, so this takes the
    // narrow, reversible half: trust what we serve. If www is later redirected,
    // this entry becomes harmless and can go with that change.
    add('https://www.imaria.io');
    add('https://aria-web.fly.dev');
    add('http://localhost:3117');
    add(`http://localhost:${port}`);
    return Array.from(set);
  })();
  const io = new SocketIOServer(server, {
    cors: {
      origin: (origin, cb) => {
        // Same-origin requests (server-to-server, curl) send no Origin
        // header — allow them. Browser requests with an unknown Origin
        // are rejected.
        if (!origin) return cb(null, true);
        if (_io_allowed_origins.includes(origin.replace(/\/$/, ''))) {
          return cb(null, true);
        }
        return cb(new Error(`Socket.io: origin ${origin} not allowed`));
      },
      methods: ['GET', 'POST'],
      credentials: true,
    },
  });

  // Map userId → Set of socket ids (one user may have multiple tabs)
  const onlineUsers = new Map(); // userId → Set<socketId>

  io.use((socket, next) => {
    const token = socket.handshake.auth?.token;
    try {
      const payload = verifyToken(token);
      socket.userId = payload.userId;
      socket.userRole = payload.role;
      next();
    } catch {
      next(new Error('Authentication error'));
    }
  });

  // R-F2342 — ARIA Network: presence is OPT-IN. A user only appears in the
  // network roster (and only broadcasts online/offline) when networkVisible is
  // true. Everyone still connects (so their own DMs work), but invisible users
  // are never announced to others.
  const _isVisible = (id) => !!findUserById(id)?.networkVisible;
  // R-F2342 hardening — last-seen kept in memory (NOT users.json) so socket
  // churn never rewrites the credential store (avoids a lost-update race with
  // password/token writes); best-effort, resets on restart. Plus a tiny
  // per-socket send rate limit to stop a runaway/abusive send_message loop.
  const lastSeen = new Map();                    // userId -> ISO timestamp
  const _SEND_WINDOW_MS = 10000, _SEND_MAX = 25; // ≤25 msgs / 10s / socket

  // ── R-F2345 · ARIA-in-channel ──────────────────────────────────────────────
  // ARIA is a first-class member of the Network. DMs addressed to her route to
  // the brain (same async-poll path the web chat uses) and her reply is stored
  // + pushed back into the thread. She screens/enriches right where intel is
  // shared. anti-stack: one in-flight request per user; long analyses chunked.
  const ARIA_ID = 'aria';
  const ariaBusy = new Set();

  function _deliverToUser(id, event, data) {
    const s = onlineUsers.get(id);
    if (s) for (const sid of s) io.to(sid).emit(event, data);
  }
  notifyNetworkConversation = group => {
    for (const memberId of group.members) _deliverToUser(memberId, 'conversation_created', { conversationId: group.id });
  };
  function _ariaChunks(text, max = 1900) {
    const t = String(text || '');
    if (t.length <= max) return [t];
    const out = []; let buf = '';
    for (const para of t.split(/\n\n+/)) {
      if (para.length > max) {                        // giant paragraph → hard split
        if (buf) { out.push(buf); buf = ''; }
        for (let i = 0; i < para.length; i += max) out.push(para.slice(i, i + max));
      } else if ((buf ? buf.length + 2 : 0) + para.length > max) {
        if (buf) out.push(buf);
        buf = para;
      } else buf = buf ? buf + '\n\n' + para : para;
    }
    if (buf) out.push(buf);
    return out.length ? out : [t.slice(0, max)];
  }
  function _pushAria(uid, text) {
    const m = storeMessage(ARIA_ID, uid, text);
    _deliverToUser(uid, 'new_message', { ...m, fromUsername: 'ARIA', fromFullName: 'ARIA' });
  }
  async function _ariaChannelReply(uid, userText) {
    if (ariaBusy.has(uid)) {
      _pushAria(uid, 'One moment — I\'m still working on your last question. I\'ll reply here as soon as it\'s ready.');
      return;
    }
    ariaBusy.add(uid);
    _deliverToUser(uid, 'typing', { fromId: ARIA_ID, typing: true });
    let reply;
    // R-F3980 (C-69) §25 — this surface produced ARIA's answer for a user and
    // reported NOTHING on any path: a console.warn on failure, and a polite
    // non-answer on an empty result. §21b is explicit that logging is DARK, so
    // the brain could not tell a working Network DM from one apologising to
    // every user. Everything needed was already here and simply not called —
    // `reportOutcome` (server.mjs:3437) and the shared R-F1965 classifier
    // imported at line 49. Reusing that classifier matters: a DEGRADED brain
    // answer comes back HTTP 200 and reads like a success, which is the exact
    // trap R-F1965 was written for and which the web chat path already avoids.
    const _dmT0 = Date.now();
    const _dmReqId = `network_dm_${uid}_${_dmT0}`;
    try {
      const u = findUserById(uid);
      const personaUserId = conversationKeyForUser(u) || slugifyIdentity(u?.email || u?.username || uid);
      const result = await _ariaChatAsyncPoll(userText, `network_${personaUserId}`, personaUserId, '');
      const _answer = (result && String(result.response || result.answer || '').trim()) || '';
      reply = _answer || 'I could not produce a reply just now — try rephrasing?';
      // An EMPTY result is a produce-failure wearing a polite sentence. Classify
      // it as such rather than letting the fallback text read as an answer.
      reportOutcome(
        'network_dm', _dmReqId, 'chat_answer',
        _answer ? classifyDeliveryOutcome(result) : 'error',
        Date.now() - _dmT0,
        _answer ? degradedDetail(result) : 'empty_response',
      );
    } catch (e) {
      console.warn('[network] ARIA reply failed:', e?.message || e);
      reply = '⚠️ I could not reach my analysis engine just now. Give me a moment and try again.';
      reportOutcome('network_dm', _dmReqId, 'chat_answer', 'error',
                    Date.now() - _dmT0, String(e?.message || e).slice(0, 200));
    } finally {
      ariaBusy.delete(uid);
      _deliverToUser(uid, 'typing', { fromId: ARIA_ID, typing: false });
    }
    for (const chunk of _ariaChunks(reply)) _pushAria(uid, chunk);
  }

  io.on('connection', (socket) => {
    const uid = socket.userId;
    socket._sendTimes = [];

    // Track online (regardless of visibility — needed for the user's own DMs)
    const firstSocket = !onlineUsers.has(uid);
    if (firstSocket) onlineUsers.set(uid, new Set());
    onlineUsers.get(uid).add(socket.id);

    // Broadcast presence ONLY if this user has opted into the network.
    if (firstSocket && _isVisible(uid)) {
      io.emit('presence', { userId: uid, online: true });
    }
    // Send the connecting client the set of users who are online AND visible.
    // ONE user-store read (build a visible-id set) rather than one per user.
    const _visibleIds = new Set(listUsers().filter(u => u.networkVisible).map(u => u.id));
    socket.emit('online_users',
      Array.from(onlineUsers.keys()).filter(id => _visibleIds.has(id)));

    // Send message
    socket.on('send_message', ({ toId, conversationId, text, clientId } = {}, ack = () => {}) => {
      if ((!toId && !conversationId) || !text || typeof text !== 'string') return ack({ ok: false, error: 'Invalid message' });
      const safeText = text.trim().slice(0, 2000);
      if (!safeText) return ack({ ok: false, error: 'Message is empty' });
      // R-F2342 — per-socket send rate limit (abuse / runaway-loop guard).
      const _now = Date.now();
      socket._sendTimes = socket._sendTimes.filter(t => _now - t < _SEND_WINDOW_MS);
      if (socket._sendTimes.length >= _SEND_MAX) return ack({ ok: false, error: 'Rate limit exceeded' });
      socket._sendTimes.push(_now);
      if (!conversationId && toId !== ARIA_ID && findUserById(toId)?.status !== 'active') return ack({ ok: false, error: 'Recipient not found' });
      let msg, recipientIds;
      try {
        if (conversationId) {
          const conversation = getConversationById(conversationId, uid, 1);
          if (!conversation) return ack({ ok: false, error: 'Conversation not found' });
          msg = storeConversationMessage(conversationId, uid, safeText, clientId);
          recipientIds = conversation.members;
        } else {
          msg = storeMessage(uid, toId, safeText, clientId);
          recipientIds = [uid, toId];
        }
      } catch (error) {
        errorTracker.record('network', 'message_store_failed', error);
        return ack({ ok: false, error: 'Message could not be stored' });
      }
      const enrichFrom = findUserById(uid);
      const payload = {
        ...msg,
        fromUsername: enrichFrom?.username || 'Unknown',
        fromFullName: enrichFrom?.fullName || 'Unknown'
      };

      // Deliver to recipient (all their sockets)
      for (const recipientId of new Set(recipientIds)) {
        const sockets = onlineUsers.get(recipientId);
        if (sockets) for (const sid of sockets) io.to(sid).emit('new_message', payload);
      }
      ack({ ok: true, message: payload });

      // R-F2345 — if this DM is addressed to ARIA, route it to her brain and
      // push her reply back into the thread (fire-and-forget; emits when ready).
      // .catch so a rare store/emit throw can't become an unhandled rejection.
      // R-F3980 (C-69) §25 — the WORST of the three delivery paths: if
      // _ariaChannelReply itself rejects, the user receives NOTHING (no
      // apology, no message) and this used to console.warn only, so the brain
      // never learned the surface had gone silent.
      //
      // NOTE the comment sits ABOVE the guard deliberately. R-F2345's test
      // asserts `toId === ARIA_ID` and `_ariaChannelReply` within 60 characters
      // of each other, so a comment between them breaks a routing guarantee
      // check that is otherwise correct. Keeping the two adjacent preserves it
      // without weakening anyone else's assertion.
      const _dmOuterT0 = Date.now();
      if (!conversationId && toId === ARIA_ID) {
        _ariaChannelReply(uid, safeText).catch(e => {
          console.warn('[network] ARIA channel reply failed:', e?.message || e);
          reportOutcome('network_dm', `network_dm_outer_${uid}_${_dmOuterT0}`,
                        'chat_answer', 'error', Date.now() - _dmOuterT0,
                        `no_reply_delivered:${String(e?.message || e).slice(0, 160)}`);
        });
      }
    });

    // Typing indicator
    socket.on('typing', ({ toId, conversationId, typing } = {}) => {
      const recipients = conversationId ? getConversationById(conversationId, uid, 1)?.members : [toId];
      for (const recipientId of recipients || []) {
        if (!recipientId || recipientId === uid) continue;
        const sockets = onlineUsers.get(recipientId);
        if (sockets) for (const sid of sockets) io.to(sid).emit('typing', { fromId: uid, conversationId, typing: !!typing });
      }
    });

    // Mark read
    socket.on('mark_read', ({ fromId, conversationId } = {}) => {
      const id = conversationId || (fromId ? getConversationSummaries(uid).find(s => s.userId === fromId)?.conversationId : null);
      if (!id || !markConversationRead(uid, id)) return;
      const conversation = getConversationById(id, uid, 1);
      for (const memberId of conversation?.members || []) if (memberId !== uid) _deliverToUser(memberId, 'messages_read', { conversationId: id, userId: uid });
    });

    socket.on('disconnect', () => {
      const sockets = onlineUsers.get(uid);
      if (sockets) {
        sockets.delete(socket.id);
        if (sockets.size === 0) {
          onlineUsers.delete(uid);
          // R-F2342 — in-memory last-seen (no users.json write); announce
          // offline only if this user opted into the network.
          lastSeen.set(uid, new Date().toISOString());
          if (_isVisible(uid)) io.emit('presence', { userId: uid, online: false });
        }
      }
    });
  });

  // ── R-F2342 · ARIA Network API ────────────────────────────────────────────
  // Placed after the io block so onlineUsers + io are in scope. Express matches
  // by path at request time, so registration order doesn't shadow these.

  // GET /api/network/directory — the opt-in member roster + live online state.
  app.get('/api/network/directory', requireAuth, (req, res) => {
    // R-F2344 — requireAuth's same-process localhost bypass calls next() without
    // populating req.user; guard so such a call returns a clean 401, not a 500.
    if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });
    const meId = req.user.userId;
    const members = listUsers()
      .filter(u => u.status === 'active' && u.networkVisible && u.id !== meId)
      .map(u => ({
        id: u.id,
        username: u.username,
        fullName: u.fullName || u.username,
        role: u.role || 'viewer',
        sector: u.sector || '',
        jobTitle: u.jobTitle || '',
        companyName: u.companyName || '',
        online: onlineUsers.has(u.id),
        lastSeenAt: lastSeen.get(u.id) || u.lastSeenAt || null,
        avatarUrl: u.avatarUpdatedAt   // R-F2349 — same shared photo the profile uses
          ? `/api/profile/photo/${u.id}?v=${Date.parse(u.avatarUpdatedAt) || 0}` : null,
      }))
      .sort((a, b) => (Number(b.online) - Number(a.online)) ||
                      a.fullName.localeCompare(b.fullName));
    const me = findUserById(meId);
    res.json({
      visible: !!me?.networkVisible,
      memberCount: members.length,
      onlineCount: members.filter(m => m.online).length,
      members,
    });
  });

  // POST /api/network/visibility { visible } — opt in/out of the network.
  app.post('/api/network/visibility', requireAuth, (req, res) => {
    if (!req.user?.userId) return res.status(401).json({ error: 'Authentication required' });  // R-F2344
    const visible = !!req.body?.visible;
    const meId = req.user.userId;
    // networkVisible is a durable preference — persisted (low frequency, unlike
    // presence). Guarded so a deleted-mid-request user can't 500 the route.
    try { updateUser(meId, { networkVisible: visible }); }
    catch (e) { return res.status(500).json({ error: 'Could not update visibility' }); }
    // Real-time appear/disappear: if the user is currently connected, announce
    // their online presence (visible=true) or removal (visible=false), and tell
    // every client to refresh the roster membership.
    if (onlineUsers.has(meId) && visible) {
      io.emit('presence', { userId: meId, online: true });
    } else if (!visible) {
      io.emit('presence', { userId: meId, online: false });
    }
    io.emit('network_update', { userId: meId, visible });
    res.json({ visible });
  });

  // R-F16 2026-05-01: rebuild staged-module disk area from Redis before
  // accepting traffic. Container ephemeral filesystem otherwise loses
  // pending review work on every restart. Fire-and-forget — disk write
  // is fast and the autonomous loop runs after server boot anyway.
  (async () => {
    try {
      const { hydrateStagedFromRedis } = await import('./lib/self/code_generator.mjs');
      await hydrateStagedFromRedis();
    } catch (err) {
      console.warn('[Boot] Staged module hydrate failed:', err.message);
    }
  })();

  // R-F838 (2026-05-23): bind dual-stack IPv6 ('::') so the app accepts
  // connections on BOTH IPv4 and IPv6. Required for Fly's *.internal
  // private network (IPv6-only / 6PN). The old '0.0.0.0' bind broke
  // aria-intel → aria-web internal calls (e.g. email-state proxy at
  // routes/aria.py:5812) post-Seenode→Fly cutover.
  server.listen(port, '::');

  // R-F1797 (audit #18): graceful shutdown. Fly sends SIGTERM on every deploy;
  // without this, in-flight requests are killed mid-response (502s). Stop
  // accepting new connections, let in-flight requests finish (bounded by
  // SHUTDOWN_GRACE_MS, which must be < fly.web.toml kill_timeout), then exit.
  let _shuttingDown = false;
  const _gracefulShutdown = (signal) => {
    if (_shuttingDown) return;
    _shuttingDown = true;
    console.log(`[Crucix] ${signal} received — draining connections (graceful shutdown)…`);
    const graceMs = Number(process.env.SHUTDOWN_GRACE_MS || 25000);
    const forceTimer = setTimeout(() => {
      console.warn('[Crucix] drain timeout — forcing exit');
      process.exit(0);
    }, graceMs);
    forceTimer.unref?.();
    server.close((err) => {
      clearTimeout(forceTimer);
      if (err) { console.error('[Crucix] server.close error:', err.message); process.exit(1); }
      console.log('[Crucix] all connections drained — exiting cleanly');
      process.exit(0);
    });
  };
  process.on('SIGTERM', () => _gracefulShutdown('SIGTERM'));
  process.on('SIGINT', () => _gracefulShutdown('SIGINT'));

  server.on('error', (err) => {
    if (err.code === 'EADDRINUSE') {
      console.error(`\n[Crucix] FATAL: Port ${port} is already in use!`);
      console.error(`[Crucix] Fix:  taskkill /F /IM node.exe   (Windows)`);
      console.error(`[Crucix]       kill $(lsof -ti:${port})   (macOS/Linux)`);
    } else {
      console.error(`[Crucix] Server error:`, err.stack || err.message);
    }
    // R-F2605 — best-effort brain signal before we exit. errorTracker.record is
    // synchronous; its POST is fire-and-forget, which is fine on the exit path.
    try { errorTracker.record('boot', 'listen_error', err, null, { code: err.code, port }); } catch {}
    process.exit(1);
  });

  server.on('listening', async () => {
    console.log(`[Crucix] Server running on http://localhost:${port}`);
    console.log(`[Crucix] Build: ${CRUCIX_BUILD_REV}`);

    // Initialise dedup store — loads from Upstash Redis if configured, else file
    await initDedup();

    // Only auto-open browser on desktop environments (not headless Linux servers)
    if (process.platform !== 'linux' || process.env.DISPLAY) {
      const openCmd = process.platform === 'win32' ? 'cmd /c start ""' : 'open';
      exec(`${openCmd} "http://localhost:${port}"`, (err) => {
        if (err) console.log('[Crucix] Could not auto-open browser:', err.message);
      });
    }

    try {
      const existing = JSON.parse(readFileSync(join(RUNS_DIR, 'latest.json'), 'utf8'));
      const data = await synthesize(existing);
      currentData = data;
      console.log('[Crucix] Loaded existing data from runs/latest.json — dashboard ready instantly');
      broadcast({ type: 'update', data: currentData });
      // NOTE: do NOT call onSweepComplete here with stale latest.json data.
      // The initial runSweepCycle() below will fetch fresh data and trigger
      // alerts via onSweepComplete — preventing repeated sends of old signals.
    } catch {
      console.log('[Crucix] No existing data found — first sweep required');
    }

    console.log('[Crucix] Running initial sweep...');
    runSweepCycle().catch(err => {
      console.error('[Crucix] Initial sweep failed:', err.message || err);
    });

    setInterval(runSweepCycle, config.refreshIntervalMinutes * 60 * 1000);

    // Self-ping every 4 minutes — keeps the server awake on all hosting platforms.
    // Always enabled — prevents Seenode/Render/Railway from sleeping the process.
    const selfUrl = process.env.APP_URL || process.env.RENDER_EXTERNAL_URL || `http://localhost:${port}`;
    setInterval(async () => {
      try {
        await fetch(`${selfUrl}/api/health`, { signal: AbortSignal.timeout(10000) });
      } catch {}
    }, 4 * 60 * 1000);
    console.log('[Crucix] Self-ping enabled (every 4min) — server will stay awake 24/7');

    // R-F2180 — LIVENESS HEARTBEAT to the brain (proprioception). The self-ping
    // above keeps the HOST awake; this tells the BRAIN the web limb is ALIVE so
    // its per-limb liveness registry (R-F2178) can affirmatively answer "is the
    // web limb up?" instead of only learning from failures. Fire-and-forget — a
    // missed beat IS the signal (the brain marks the limb stale when beats stop).
    if (ARIA_SERVICE_URL) {
      const _webBeatMs = 3 * 60 * 1000;
      const _sendWebBeat = () => {
        fetch(`${ARIA_SERVICE_URL}/api/aria/liveness/beat`, {
          method: 'POST',
          headers: _ariaHeaders(),
          body: JSON.stringify({ limb: 'aria-web', status: 'alive', interval_s: Math.round(_webBeatMs / 1000) }),
          signal: AbortSignal.timeout(5000),
        }).catch(() => {});
      };
      _sendWebBeat();
      setInterval(_sendWebBeat, _webBeatMs);
      console.log('[Crucix] Brain liveness heartbeat enabled (every 3min)');
    }

    // R-F2860 — EXTERNAL liveness observer: aria-web WATCHES aria-intel. The
    // heartbeat above tells the brain "web is alive"; this watches the BRAIN. Because
    // it runs in a SEPARATE process, it can SEE and RECORD aria-intel dying or
    // crash-looping — which the in-process web_integrity_agent structurally cannot
    // (it dies with the process; "9 passed" is guaranteed whenever it logs at all).
    // Outages are recorded DURABLY on the /data volume (survives aria-intel death),
    // the operator is alerted (§19e — never let him discover an outage himself), and
    // on recovery the outage is reported to the brain (§25 — the death it could not
    // self-report). Confirmation is SUSTAINED/FLAPPING-gated so the legit ~10-min cold
    // boot and rolling deploys never cry wolf.
    if (ARIA_SERVICE_URL) {
      try {
        // Durable on the PERSISTENT volume (/data), not the ephemeral container fs
        // (/app/data) — so the outage ledger survives an aria-web redeploy too, not
        // only aria-intel's death. Falls back to cwd/data for local dev.
        const _outageDir = existsSync('/data') ? '/data' : join(process.cwd(), 'data');
        if (!existsSync(_outageDir)) mkdirSync(_outageDir, { recursive: true });
        const _outageStore = new PersistStore(
          'crucix:aria_intel_outages',
          join(_outageDir, 'aria_intel_outages.json'),
          () => [],
        );
        await _outageStore.init();
        // Probe the PUBLIC url (what users hit end-to-end, incl. fly-proxy — a dead
        // machine surfaces as a 502 there, which the observer treats as down). The
        // authed brain-report below uses the ARIA_SERVICE_URL API base.
        const _probeUrl = (process.env.ARIA_FLY_URL || 'https://aria-intel.fly.dev').replace(/\/$/, '');
        const _observer = createLivenessObserver({
          serviceUrl: _probeUrl,
          probeFn: probeFlyHealth,
          store: _outageStore,
          notifyFn: notifyAdmin,
          brainPostFn: async (sig) => {
            await fetch(`${ARIA_SERVICE_URL}/api/aria/brain/signal`, {
              method: 'POST',
              headers: { ..._ariaHeaders(), 'Content-Type': 'application/json' },
              body: JSON.stringify(sig),
              signal: AbortSignal.timeout(8000),
            });
          },
          logger: console,
        });
        _livenessObserverRef = _observer;         // expose to GET /api/health/aria-intel-observer
        _livenessOutageStoreRef = _outageStore;
        const _observerMs = (_observer._config.pollIntervalS || 30) * 1000;
        let _observerInFlight = false;
        const _observerTimer = setInterval(() => {
          if (_observerInFlight) return;             // non-overlapping (belt + the module's own guard)
          _observerInFlight = true;
          _observer.tick()
            .catch(err => console.error('[liveness_observer] tick crashed:', err?.message || err))
            .finally(() => { _observerInFlight = false; });
        }, _observerMs);
        if (typeof _observerTimer.unref === 'function') _observerTimer.unref();
        console.log(`[Crucix] External liveness observer enabled (aria-web → aria-intel, every ${_observerMs / 1000}s)`);
      } catch (e) {
        console.error('[liveness_observer] failed to start (non-fatal):', e?.message || e);
      }
    }

    cron.schedule('0 7 * * *', async () => {
      console.log('[Crucix] Sending morning digest...');
      if (TELEGRAM_GOLDEN_INTEL_ONLY) {
        console.log('[Digest] Telegram morning digest skipped — Golden Intel only');
      } else {
        try { await sendMorningDigest(telegramAlerter, currentData); }
        catch (e) { console.error('[Digest] Failed:', e.message); }
      }
      pushDigest('Morning Intelligence Brief', 'Your daily ARIA intelligence briefing is ready.', '/dashboard/brief').catch(e => console.warn('[Push] digest push failed:', e.message));
    }, { timezone: 'Europe/London' });

    // ── Channel Scheduler — delegated to channelServerHooks ────────────────
    // R-F2585 (operator: 2 Golden Intel drops/day, 3 max): TWO scheduled curated posts —
    // 07:00 (morning) + 17:00 (evening) Europe/London. Post dedup + the freshness gate mean
    // the evening slot only fires on a DIFFERENT fresh decision-grade signal (skips if nothing
    // new since morning — never re-posts the same item). CHANNEL_MAX_DAILY_POSTS=3 leaves
    // headroom for ONE real-time breaking item on top of the two scheduled slots.
    for (const _hour of [7, 17]) {
      cron.schedule(`0 ${_hour} * * *`, async () => {
        const bot = { botToken: config.telegram.botToken, chatId: config.telegram.chatId, channelId: config.telegram.channelId };
        // R-F2716 — pass the slot hour so the handler applies the A→B policy:
        // 07:00 = best Grade A else hold; 17:00 = Grade A else labelled Grade B.
        await channelHooks.handleMorningSignalCron(currentData, bot, { hour: _hour });
      }, { timezone: 'Europe/London' });
    }
    // R-F2723 — startup catch-up: if the process was down at 07:00/17:00 the slot
    // was silently lost. Shortly after boot, run any slot that was DUE today but
    // never executed (idempotent via content-dedup). Delayed so the app is ready.
    setTimeout(() => {
      const bot = { botToken: config.telegram.botToken, chatId: config.telegram.chatId, channelId: config.telegram.channelId };
      channelHooks.runStartupCatchUp(currentData, bot).catch(e => console.warn('[ChannelCron] startup catch-up failed:', e.message));
    }, 45000);

    // R-F2306 — DISABLED (operator: ONE relevant post/day, keep noise down).
    // The Case File / Know-Your-Rights / Country Read / Opportunity crons posted
    // HARD-CODED template content (e.g. the same "Mozambique LNG Contractor" case)
    // multiple times a day — repetitive, not live, not relevant. The single daily
    // channel post is now the LIVE, curated Morning Signal (07:00) which skips when
    // there is nothing material; genuine breaking items still escalate in real time.
    // Re-enable individually if a live (non-canned) generator is wired for them.

    // Weekly query evolution — Sunday 04:00 UTC
    // Genetic algorithm: queries that produced leads survive, useless ones die
    cron.schedule('0 4 * * 0', async () => {
      console.log('[Evolution] Running weekly query evolution...');
      try {
        const { evolveGeneration, getEvolutionStats } = await import('./lib/aria/query_evolution.mjs');
        evolveGeneration();
        const stats = getEvolutionStats();
        console.log(`[Evolution] Gen ${stats.generation} — ${stats.populationSize} queries, ${stats.totalHits} hits, ${stats.totalMisses} misses`);
        if (!TELEGRAM_GOLDEN_INTEL_ONLY && telegramAlerter?.isConfigured) {
          const top = stats.topQueries.slice(0, 3).map(q => `  "${q.query}" (fitness: ${q.fitness})`).join('\n');
          await telegramAlerter.sendMessage(`🧬 *QUERY EVOLUTION — Gen ${stats.generation}*\n${stats.populationSize} queries, ${stats.totalHits} total hits\n\nTop performers:\n${top}`);
        }
      } catch (e) { console.error('[Evolution] Failed:', e.message); }
    }, { timezone: 'Europe/London' });

    // Weekly pattern analysis — Sunday 03:00 UTC
    cron.schedule('0 3 * * 0', async () => {
      console.log('[Self] Running weekly pattern analysis...');
      try {
        const { patterns, runsAnalyzed } = await analyzePatterns(llmProvider);
        console.log(`[Self] Pattern analysis complete — ${patterns.length} patterns from ${runsAnalyzed} runs`);
        if (!TELEGRAM_GOLDEN_INTEL_ONLY && telegramAlerter?.isConfigured && patterns.length > 0) {
          const stored = getPatterns();
          await telegramAlerter.sendMessage(
            `🔍 *WEEKLY PATTERN UPDATE*\n${patterns.length} intelligence patterns detected from ${runsAnalyzed} historical runs.\n\n/patterns to view`
          );
        }
      } catch (e) { console.error('[Self] Pattern analysis failed:', e.message); }
    }, { timezone: 'Europe/London' });

    // Daily internet exploration — 06:00 UTC (morning sweep) + 14:00 UTC (afternoon sweep)
    const runDailyExploration = async () => {
      console.log('[Self] Running daily web exploration...');
      try {
        const findings = await runExploration(llmProvider);
        console.log(`[Self] Exploration complete — ${findings.insights?.length || 0} insights, ${findings.salesIdeas?.length || 0} ideas`);
        if (!TELEGRAM_GOLDEN_INTEL_ONLY && telegramAlerter?.isConfigured && (findings.insights?.length > 0 || findings.salesIdeas?.length > 0)) {
          const post = formatExplorerFindingsForTelegramIfTop(findings);
          if (post.shouldSend) {
            const sent = await telegramAlerter.sendMessage(post.text);
            if (sent?.ok !== false) {
              recordExplorerTelegramPost(post.keys);
            } else {
              console.warn('[Self] Exploration Telegram send failed — not recording dedup keys');
            }
          } else {
            console.log(`[Self] Exploration Telegram skipped — ${post.reason}`);
          }
        } else if (TELEGRAM_GOLDEN_INTEL_ONLY) {
          console.log('[Self] Exploration Telegram skipped — Golden Intel only');
        }
      } catch (e) { console.error('[Self] Web exploration failed:', e.message); }
    };
    // 4x daily exploration: 06:00, 10:00, 14:00, 18:00 London (was 2x — now 24/7 coverage)
    cron.schedule('0 6 * * *',  runDailyExploration, { timezone: 'Europe/London' });
    cron.schedule('0 10 * * *', runDailyExploration, { timezone: 'Europe/London' });
    cron.schedule('0 14 * * *', runDailyExploration, { timezone: 'Europe/London' });
    cron.schedule('0 18 * * *', runDailyExploration, { timezone: 'Europe/London' });

    // Daily autonomous maintenance — 02:00 London
    // 1) Auto-disables sources with ≥90% failure rate (≥20 sweeps of data)
    // 2) Auto-deploys staged modules that pass the briefing() test
    // 3) Alerts sources still degraded but not yet auto-disabled (need manual review or LLM fix)
    cron.schedule('0 2 * * *', async () => {
      console.log('[AutoMaint] Daily autonomous maintenance starting...');
      try {
        const { autoDisableDegradedSources, autoDeployStaged } = await import('./lib/self/updater.mjs');
        const ts = londonTs();

        // Step 1: auto-disable dead sources
        const disabled = autoDisableDegradedSources(0.90, 20);
        if (disabled.length > 0) {
          console.log(`[AutoMaint] Auto-disabled: ${disabled.map(d => d.name).join(', ')}`);
          if (telegramAlerter?.isConfigured) {
            await telegramAlerter.sendMessage(
              `⚙️ *AUTO-MAINTENANCE*\n_${ts} London_\n\n${disabled.map(d => `⛔ \`${d.name}\` disabled (${d.failRate}% fail rate)`).join('\n')}\n\n_Dead sources removed automatically. /sources for full report_`
            );
          }
        }

        // Step 2: auto-deploy staged modules that pass test
        const deployResults = await autoDeployStaged();
        const deployed = deployResults.filter(r => r.deployed);
        const skipped  = deployResults.filter(r => !r.deployed);
        if (deployed.length > 0) {
          console.log(`[AutoMaint] Auto-deployed: ${deployed.map(d => d.moduleName).join(', ')}`);
          if (telegramAlerter?.isConfigured) {
            await telegramAlerter.sendMessage(
              `🚀 *AUTO-DEPLOY*\n_${ts} London_\n\n${deployed.map(d => `✅ \`${d.moduleName}\` — ${d.testResult?.updates || 0} updates`).join('\n')}\n\n_New source modules deployed and tested automatically._`
            );
          }
        }
        if (skipped.length > 0) {
          console.log(`[AutoMaint] Deploy skipped (test failed): ${skipped.map(s => s.moduleName).join(', ')}`);
        }

        // Step 3: report remaining degraded sources that need LLM fix
        const toReview = getSourcesToReview().filter(s => s.status === 'critical' && (s.totalOk + s.totalFail) >= 48);
        const unfixed  = toReview.filter(s => !disabled.find(d => d.name === s.name));
        if (unfixed.length > 0) {
          console.log(`[AutoMaint] ${unfixed.length} source(s) still degraded — staging LLM fixes...`);
          for (const source of unfixed.slice(0, 3)) {
            try {
              const { generateSourceFix, stageModule } = await import('./lib/self/code_generator.mjs');
              const fix = await generateSourceFix(llmProvider, source.name, `Reliability ${source.reliability}% — consistently failing`);
              if (fix.success) {
                await stageModule(source.name, fix.code, { type: 'fix', description: `Auto-fix: ${source.name} was ${source.reliability}% reliable`, confidence: 0.75 });
                console.log(`[AutoMaint] LLM fix staged for: ${source.name}`);
              }
            } catch (err) {
              console.warn(`[AutoMaint] Fix staging failed for ${source.name}:`, err.message);
            }
          }
          if (telegramAlerter?.isConfigured) {
            const names = unfixed.map(s => `▸ \`${s.name}\` (${s.reliability}% reliable)`).join('\n');
            await telegramAlerter.sendMessage(
              `🔴 *SOURCE HEALTH ALERT*\n_${ts} London_\n\n${unfixed.length} source(s) degraded — LLM fixes staged:\n${names}\n\n_/sources for full report · fixes auto-deploy tomorrow if tests pass_`
            );
          }
        }

      } catch (e) { console.error('[AutoMaint] Daily maintenance failed:', e.message); }
    }, { timezone: 'Europe/London' });
  });
}

// ── Explorer auto-scheduler (curiosity → web exploration loop) ───────────────
if (BRAIN_URL) {
  startExplorerScheduler(app, redisAdapter, notifyAdmin);
  console.log('[Init] Explorer auto-scheduler started (curiosity thread resolution)');
}

// ── Zoom bot proxy — forward /api/zoom/* to the Python Zoom service ─────────
const ZOOM_BOT_URL = process.env.ZOOM_BOT_URL;
if (ZOOM_BOT_URL) {
  const zoomProxy = async (req, res) => {
    const path = req.url;  // e.g. /health, /join, /active
    try {
      const opts = { method: req.method, headers: { 'Content-Type': 'application/json' }, signal: AbortSignal.timeout(20000) }; // R-F2608 — bound the outbound fetch (mirror other proxies)
      if (req.method !== 'GET' && req.method !== 'HEAD') {
        opts.body = JSON.stringify(req.body || {});
      }
      // Forward ARIA internal token for auth
      if (req.headers.authorization) opts.headers['Authorization'] = req.headers.authorization;
      const upstream = await fetch(`${ZOOM_BOT_URL}${path}`, opts);
      const data = await upstream.json();
      res.status(upstream.status).json(data);
    } catch (e) {
      res.status(502).json({ error: 'Zoom service unreachable', detail: e.message });
    }
  };
  // R-F2101 (2026-06-28, ARIA web DD): gate the proxy with requireAuth. It's only
  // mounted when ZOOM_BOT_URL is set (unset today, so currently inactive), but as an
  // open proxy forwarding to an internal service it must not be reachable unauthed.
  app.use('/api/zoom', requireAuth, zoomProxy);
  console.log(`[Init] Zoom bot proxy → ${ZOOM_BOT_URL}`);
}

// ── WhatsApp Listener (Baileys) — runs inside this process ──────────────────
mountWAListener(app);

// ── Email Intelligence Reader (LinkedIn alerts, Google Alerts, tender notifications) ──
mountEmailReader(app);

// ── LinkedIn Intelligence (relationship maps, competitor tracking, appointments) ──
initLinkedInIntel().catch(e => console.warn('[LinkedIn Intel] Init failed:', e.message));
mountLinkedInRoutes(app);

// ── ARIA Proactive Operating Rhythm (daily/weekly/monthly autonomous outputs) ──
mountProactive(app);

// ── Deal Pipeline Tracker ──────────────────────────────────────────────────
initPipeline().catch(e => console.warn('[Pipeline] Init failed:', e.message));
mountPipelineRoutes(app);

// ── Cold Backup Export (admin-only) ────────────────────────────────────────
mountBackupRoutes(app);

// ── Express error handler — MUST be last middleware ──────────────────────────
app.use(errorTracker.expressMiddleware());

process.on('unhandledRejection', (err) => {
  console.error('[Crucix] Unhandled rejection:', err?.stack || err?.message || err);
  // R-F2182 — forward to the brain (was console-only = DARK §21a). A web-tier
  // crash is the worst failure and must reach the brain's signal sink so ARIA
  // knows the web limb is failing. errorTracker.record → /api/aria/brain/signal.
  try { errorTracker.record('web_process', 'unhandled_rejection', err); } catch { /* never loop on crash */ }
});
process.on('uncaughtException', (err) => {
  console.error('[Crucix] Uncaught exception:', err?.stack || err?.message || err);
  try { errorTracker.record('web_process', 'uncaught_exception', err); } catch { /* never loop on crash */ }
  // R-F2608 — deliberately NOT calling process.exit() here: changing prod
  // crash behaviour (crash-on-throw vs keep-running) is out of scope/risky and
  // deferred. The signal is recorded above; the process-lifecycle tradeoff is
  // left as-is intentionally.
});

start().catch(err => {
  console.error('[Crucix] FATAL — Server failed to start:', err?.stack || err?.message || err);
  process.exit(1);
});
