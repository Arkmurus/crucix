"""ARIA continuous-learning layer.

This package contains the 24/7 learning loop modules:

  - training_export     : accumulates verified interactions as
                           fine-tune-ready JSONL training data
  - knowledge_spider    : auto-follows URLs + entity citations from
                           every new RAG chunk — graph-walks outward
                           so every insight grows the knowledge graph
  - metacognitive_journal: hourly structured reflection on successes,
                           failures, and novelties
  - research_engine     : continuously attacks ARIA's own weakest
                           mastery cells with web_search + web_crawl
                           + corpus_ingest, measuring mastery lift

All modules are LLM-free by design — they consume existing ARIA signals
(mistake_ledger, calibration_review, brain_hook, mastery heatmap, RAG
store, audit logs) and either emit structured records or dispatch
existing infrastructure. Zero marginal cost per run. Zero dependency
on specific LLM providers.

Scheduled by autonomous/tasks.yaml as four independent cron jobs:
  LEARNING-EXPORT-DAILY        — 03:30 UTC  (quiet-hour batch write)
  SPIDER-HOURLY                — every hour (opportunistic follow-up)
  METACOG-JOURNAL-HOURLY       — every hour (at :45 past)
  RESEARCH-WEAK-CELL-HOURLY    — every hour (at :30 past, dedupe with other ingests)
"""
from __future__ import annotations
