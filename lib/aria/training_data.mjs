// lib/aria/training_data.mjs
// Training Data Collector — builds fine-tuning dataset for future proprietary LLM
//
// Collects from every ARIA interaction, deal outcome, brain assessment, and
// knowledge extraction. Outputs JSONL (JSON Lines) format compatible with:
// - Llama 3 fine-tuning (Meta format)
// - Mistral fine-tuning (conversation format)
// - Qwen fine-tuning (ChatML format)
// - OpenAI fine-tuning API format
//
// When ready to transition to own LLM:
// 1. Export dataset: GET /api/aria/training-data/export
// 2. Fine-tune with Python: python train.py --data training_data.jsonl --model llama3
// 3. Deploy to Ollama: ollama create aria-v1 -f Modelfile
// 4. Switch provider: LLM_PROVIDER=ollama LLM_MODEL=aria-v1

import { readFileSync, writeFileSync, appendFileSync, existsSync, mkdirSync, statSync } from 'fs';
import { join, dirname } from 'path';
import { redisGet, redisSet } from '../persist/store.mjs';

const TRAINING_DIR = join(process.cwd(), 'runs', 'training');
const CONVERSATIONS_FILE = join(TRAINING_DIR, 'conversations.jsonl');
const OUTCOMES_FILE = join(TRAINING_DIR, 'outcomes.jsonl');
const BRAIN_FILE = join(TRAINING_DIR, 'brain_assessments.jsonl');
const KNOWLEDGE_FILE = join(TRAINING_DIR, 'knowledge.jsonl');
const META_FILE = join(TRAINING_DIR, 'meta.json');
const TRAINING_REDIS_KEY = 'crucix:training:meta';

function ensureDir() {
  if (!existsSync(TRAINING_DIR)) mkdirSync(TRAINING_DIR, { recursive: true });
}

function appendLine(file, data) {
  ensureDir();
  try { appendFileSync(file, JSON.stringify(data) + '\n', 'utf8'); }
  catch (e) { console.warn('[Training] Write failed:', e.message); }
}

function loadMeta() {
  try {
    if (existsSync(META_FILE)) return JSON.parse(readFileSync(META_FILE, 'utf8'));
  } catch {}
  return { conversations: 0, outcomes: 0, brainAssessments: 0, knowledge: 0, createdAt: new Date().toISOString() };
}

function saveMeta(meta) {
  ensureDir();
  try { writeFileSync(META_FILE, JSON.stringify(meta, null, 2), 'utf8'); } catch {}
  redisSet(TRAINING_REDIS_KEY, meta).catch(() => {});
}

// ── Record ARIA Conversation ─────────────────────────────────────────────────
// Every ARIA chat becomes a training example: system prompt + user message → ARIA response
// This is the PRIMARY training data source — teaches the model how ARIA reasons

export function recordConversation(systemPrompt, userMessage, ariaResponse, metadata = {}) {
  if (!userMessage || !ariaResponse || ariaResponse.length < 20) return;

  const example = {
    // OpenAI / Llama chat format
    messages: [
      { role: 'system', content: systemPrompt.substring(0, 2000) },
      { role: 'user', content: userMessage.substring(0, 1000) },
      { role: 'assistant', content: ariaResponse.substring(0, 3000) },
    ],
    // Metadata for filtering/weighting during training
    meta: {
      ts: new Date().toISOString(),
      market: metadata.market || '',
      topic: metadata.topic || '',
      hadIntelContext: !!metadata.hadIntelContext,
      responseQuality: metadata.responseQuality || null, // set later via feedback
    },
  };

  appendLine(CONVERSATIONS_FILE, example);
  const meta = loadMeta();
  meta.conversations++;
  meta.lastConversation = new Date().toISOString();
  saveMeta(meta);
}

// ── Record Deal Outcome ──────────────────────────────────────────────────────
// Teaches the model which recommendations lead to wins vs losses
// Critical for calibrating confidence and recommendation quality

export function recordOutcomeTraining(market, product, outcome, brainRecommendation, approach, winProb) {
  const example = {
    messages: [
      { role: 'system', content: 'You are ARIA, a defence procurement intelligence agent. Learn from this deal outcome to improve future recommendations.' },
      { role: 'user', content: `Deal outcome for ${market} (${product}): ${outcome}. Brain recommended: ${brainRecommendation ? 'YES' : 'NO'}. Win probability was ${winProb}%. Approach used: ${approach || 'standard'}.` },
      { role: 'assistant', content: outcome === 'WON'
        ? `This approach worked for ${market}. Key success factors to remember: ${product} demand in ${market}, win probability ${winProb}% was ${winProb > 50 ? 'reasonably calibrated' : 'under-estimated — increase future scores for similar opportunities'}.`
        : `This approach failed for ${market}. Analysis: win probability ${winProb}% was ${winProb > 50 ? 'over-optimistic — reduce scores for similar deals' : 'correctly pessimistic but we attempted anyway'}. Investigate: wrong OEM? Wrong timing? Competitor won?`
      },
    ],
    meta: {
      ts: new Date().toISOString(),
      type: 'outcome',
      market,
      product,
      outcome,
      winProb,
    },
  };

  appendLine(OUTCOMES_FILE, example);
  const meta = loadMeta();
  meta.outcomes++;
  meta.lastOutcome = new Date().toISOString();
  saveMeta(meta);
}

// ── Record Brain Assessment ──────────────────────────────────────────────────
// Teaches the model how to produce structured BD intelligence

export function recordBrainAssessment(context, brainOutput) {
  if (!brainOutput) return;

  const example = {
    messages: [
      { role: 'system', content: 'You are CRUCIX, the autonomous BD intelligence brain. Produce structured strategic assessments.' },
      { role: 'user', content: 'Analyze the current intelligence and produce your autonomous BD assessment.' },
      { role: 'assistant', content: typeof brainOutput === 'string' ? brainOutput : JSON.stringify(brainOutput).substring(0, 4000) },
    ],
    meta: {
      ts: new Date().toISOString(),
      type: 'brain_assessment',
      tenderCount: context?.tenderCount || 0,
      ideaCount: context?.ideaCount || 0,
      hasWeeklyPriority: !!brainOutput?.weeklyPriority,
      salesLeadCount: brainOutput?.salesLeads?.length || 0,
      confidence: brainOutput?.confidence || null,
    },
  };

  appendLine(BRAIN_FILE, example);
  const meta = loadMeta();
  meta.brainAssessments++;
  meta.lastBrain = new Date().toISOString();
  saveMeta(meta);
}

// ── Record Knowledge Fact ────────────────────────────────────────────────────
// Teaches the model verified domain facts

export function recordKnowledgeFact(topic, content, confidence) {
  const example = {
    messages: [
      { role: 'system', content: 'You are ARIA, a defence procurement expert. State verified facts.' },
      { role: 'user', content: `What do you know about: ${topic}?` },
      { role: 'assistant', content: `[${confidence}] ${content}` },
    ],
    meta: { ts: new Date().toISOString(), type: 'knowledge', topic, confidence },
  };

  appendLine(KNOWLEDGE_FILE, example);
  const meta = loadMeta();
  meta.knowledge++;
  saveMeta(meta);
}

// ── Export Combined Dataset ──────────────────────────────────────────────────
// Merges all training files into one JSONL, ready for fine-tuning

export function exportTrainingData() {
  ensureDir();
  const files = [CONVERSATIONS_FILE, OUTCOMES_FILE, BRAIN_FILE, KNOWLEDGE_FILE];
  const lines = [];

  for (const file of files) {
    try {
      if (!existsSync(file)) continue;
      const content = readFileSync(file, 'utf8').trim();
      if (!content) continue;
      for (const line of content.split('\n')) {
        try { lines.push(JSON.parse(line)); } catch {}
      }
    } catch {}
  }

  return {
    format: 'jsonl',
    compatible_with: ['llama3', 'mistral', 'qwen', 'openai'],
    total_examples: lines.length,
    breakdown: loadMeta(),
    data: lines,
    export_date: new Date().toISOString(),
    instructions: {
      llama3: 'python train.py --model meta-llama/Llama-3-8B --data training_data.jsonl --epochs 3',
      mistral: 'python train.py --model mistralai/Mistral-7B-v0.3 --data training_data.jsonl --epochs 3',
      ollama: 'ollama create aria-v1 -f Modelfile  # then set LLM_PROVIDER=ollama LLM_MODEL=aria-v1',
    },
  };
}

// ── Stats ────────────────────────────────────────────────────────────────────

export function getTrainingStats() {
  const meta = loadMeta();
  const sizes = {};
  for (const [name, file] of [['conversations', CONVERSATIONS_FILE], ['outcomes', OUTCOMES_FILE], ['brain', BRAIN_FILE], ['knowledge', KNOWLEDGE_FILE]]) {
    try { sizes[name] = existsSync(file) ? statSync(file).size : 0; } catch { sizes[name] = 0; }
  }
  return { ...meta, fileSizes: sizes, totalSizeBytes: Object.values(sizes).reduce((a, b) => a + b, 0) };
}
