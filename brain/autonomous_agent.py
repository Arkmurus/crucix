"""
CRUCIX Autonomous Brain — Autonomous Agent Loop
The self-running intelligence engine. Executes full sweep cycles,
generates leads, monitors for anomalies, fires Telegram alerts,
and improves with every run without human intervention.
"""
import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import redis
import schedule
import requests

from .config import CONFIG
from .deepseek_client import DeepSeekClient
from .feedback_loop import FeedbackLoop
from .memory import CrucixMemory
from .ml_engine import CrucixMLEngine

logger = logging.getLogger("crucix.brain.agent")


class CrucixAutonomousAgent:
    """
    The main Crucix brain loop.

    Sweep cycle (every 6h by default):
      1. Pull latest intelligence signals from Redis/DB
      2. Recall past conclusions from vector memory
      3. Score each signal via ML engine
      4. Run DeepSeek analysis with memory context
      5. Generate BD leads with win probabilities
      6. Detect anomalies and trigger reactive queries
      7. Store conclusions to vector memory
      8. Fire Telegram alerts for HIGH urgency
      9. Self-evaluate quality of its own output
    """

    KEY_SIGNALS      = CONFIG.redis_brain_prefix + "incoming_signals"
    KEY_LEADS        = CONFIG.redis_brain_prefix + "generated_leads"
    KEY_LAST_RUN     = CONFIG.redis_brain_prefix + "last_run"
    KEY_RUN_HISTORY  = CONFIG.redis_brain_prefix + "run_history"
    KEY_REACTIVE_Q   = CONFIG.redis_brain_prefix + "reactive_queries"

    def __init__(self, telegram_token: str = None, telegram_chat_id: str = None):
        logger.info("Initialising Crucix Autonomous Brain...")

        try:
            self.redis = redis.from_url(CONFIG.redis_url, decode_responses=True)
            self.redis.ping()
        except Exception as e:
            logger.critical(f"Redis connection failed: {e}")
            raise

        # Pass redis client to memory and ml_engine for persistence
        self.memory    = CrucixMemory(redis_client=self.redis)
        self.ml_engine = CrucixMLEngine(redis_client=self.redis)
        self.deepseek  = DeepSeekClient()
        self.feedback  = FeedbackLoop(self.memory, self.ml_engine)

        self.telegram_token   = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.run_id: Optional[str] = None

        logger.info(f"Brain ready | memory={self.memory.memory_stats()} | ml={self.ml_engine.model_status()}")

    # ── Main Sweep ────────────────────────────────────────────────────────────

    def run_sweep(self) -> Dict:
        """Execute a full autonomous intelligence sweep. Returns sweep report."""
        self.run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        t_start     = time.time()
        logger.info(f"=== SWEEP START | {self.run_id} ===")

        report = {
            "run_id":             self.run_id,
            "started_at":         datetime.now(timezone.utc).isoformat(),
            "signals_processed":  0,
            "leads_generated":    0,
            "anomalies_detected": 0,
            "alerts_fired":       0,
            "markets_covered":    [],
            "top_leads":          [],
            "brain_conclusions":  [],
            "reactive_queries":   [],
            "self_evaluation":    {},
        }

        try:
            # ── 1. Pull signals ────────────────────────────────────────────────
            signals = self._pull_signals()
            report["signals_processed"] = len(signals)
            logger.info(f"Pulled {len(signals)} signals for processing")

            if not signals:
                logger.info("No signals to process — sweep complete (no-op)")
                return report

            # ── 2. Update anomaly baseline ─────────────────────────────────────
            self.ml_engine.update_anomaly_baseline(signals)

            # ── 3. Score + analyse each signal ────────────────────────────────
            leads, anomalies, conclusions = [], [], []
            markets_seen = set()

            for signal in signals:
                market = signal.get("market", "unknown")
                markets_seen.add(market)

                # ML scoring
                ml_score = self.ml_engine.score_opportunity({
                    **signal,
                    "has_anomaly_flag": False,
                    "signal_source_count": 1,
                })
                anomaly_info = ml_score["anomaly_assessment"]

                # Recall past conclusions for this market (memory injection)
                past = self.memory.recall_past_conclusions(
                    query=f"{market} procurement defence",
                    n=CONFIG.brain_memory_window,
                    market=market,
                )

                # DeepSeek analysis with memory context
                try:
                    analysis = self.deepseek.analyse_intelligence_signal(signal, past)
                except Exception as e:
                    logger.warning(f"DeepSeek analysis failed for signal: {e}")
                    analysis = {"procurement_opportunity": False, "confidence": 0, "reasoning": str(e)}

                # Blend ML win-probability with DeepSeek adjustment
                base_wp   = ml_score["win_probability_blended"]
                adj       = analysis.get("win_probability_adjustment", 0.0)
                final_wp  = round(min(max(base_wp + adj, 0.0), 1.0), 3)
                source_wt = self.feedback.get_source_weight(signal.get("source", "web_explorer"))
                region_wt = self.feedback.get_region_score(market)

                enriched_signal = {
                    **signal,
                    "run_id":            self.run_id,
                    "ml_score":          ml_score,
                    "deepseek_analysis": analysis,
                    "win_probability":   final_wp,
                    "source_weight":     source_wt,
                    "region_weight":     region_wt,
                    "weighted_score":    round(final_wp * source_wt * region_wt, 4),
                    "is_anomaly":        anomaly_info.get("is_anomaly", False),
                }

                # Store conclusion to vector memory
                self.memory.store_conclusion(
                    {"reasoning": analysis.get("reasoning", ""), **analysis, "win_probability": final_wp},
                    market=market,
                    run_id=self.run_id,
                )
                conclusions.append(analysis)

                if anomaly_info.get("is_anomaly"):
                    anomalies.append(enriched_signal)
                    report["anomalies_detected"] += 1
                    # Contextualise anomaly with DeepSeek
                    try:
                        anom_context = self.deepseek.explain_anomaly(anomaly_info)
                        enriched_signal["anomaly_context"] = anom_context
                        # Generate reactive web queries
                        reactive = self.deepseek.generate_reactive_queries(
                            trigger_event={**signal, "anomaly": anomaly_info},
                            base_queries=[]
                        )
                        report["reactive_queries"].extend(reactive)
                        self._push_reactive_queries(reactive)
                    except Exception as e:
                        logger.warning(f"Anomaly enrichment failed: {e}")

                # Build lead if opportunity detected
                if analysis.get("procurement_opportunity") and final_wp >= 0.25:
                    lead = self._build_lead(enriched_signal, analysis, final_wp)
                    leads.append(lead)
                    self.memory.store_lead(lead, self.run_id)
                    self._push_lead_to_redis(lead)

                    # Alert on HIGH urgency
                    if analysis.get("urgency") == "HIGH" and final_wp >= CONFIG.alert_threshold_risk:
                        self._fire_telegram_alert(lead)
                        report["alerts_fired"] += 1

            # ── 4. Generate BD Brief ───────────────────────────────────────────
            if leads:
                intel_summary = self._build_intel_summary(signals, anomalies, conclusions)
                market_snaps  = self._build_market_snapshots(list(markets_seen))
                try:
                    bd_brief = self.deepseek.generate_bd_brief(intel_summary, market_snaps)
                    self._store_bd_brief(bd_brief)
                    report["bd_brief_generated"] = True
                except Exception as e:
                    logger.error(f"BD brief generation failed: {e}")

            # ── 5. Brain self-evaluation ───────────────────────────────────────
            report["self_evaluation"] = self._self_evaluate(leads, conclusions)

            # ── 6. Store run history ───────────────────────────────────────────
            report["leads_generated"]  = len(leads)
            report["markets_covered"]  = list(markets_seen)
            report["top_leads"]        = sorted(
                leads, key=lambda x: x.get("win_probability", 0), reverse=True
            )[:5]
            report["brain_conclusions"] = [c.get("reasoning", "")[:200] for c in conclusions[:5]]
            report["duration_seconds"]  = round(time.time() - t_start, 1)

            self._store_run_report(report)
            logger.info(
                f"=== SWEEP COMPLETE | leads={len(leads)} "
                f"anomalies={report['anomalies_detected']} "
                f"time={report['duration_seconds']}s ==="
            )

        except Exception as e:
            logger.exception(f"Sweep failed: {e}")
            report["error"] = str(e)

        return report

    # ── Lead Construction ─────────────────────────────────────────────────────

    def _build_lead(self, signal: Dict, analysis: Dict, win_prob: float) -> Dict:
        return {
            "id":               str(uuid.uuid4()),
            "run_id":           self.run_id,
            "generated_at":     datetime.now(timezone.utc).isoformat(),
            "market":           signal.get("market", "unknown"),
            "urgency":          analysis.get("urgency", "MEDIUM"),
            "lead_action":      analysis.get("lead_action", "Review signal"),
            "oem_match_needed": analysis.get("oem_match_needed", []),
            "compliance_flags": analysis.get("compliance_flags", []),
            "win_probability":  win_prob,
            "weighted_score":   signal.get("weighted_score", 0),
            "confidence":       analysis.get("confidence", 50),
            "source":           signal.get("source", "unknown"),
            "source_url":       signal.get("url", ""),
            "signal_title":     signal.get("title", ""),
            "reasoning":        analysis.get("reasoning", ""),
            "status":           "OPEN",
            "outcome":          "PENDING",
            "user_rating":      None,
            "is_anomaly_driven": signal.get("is_anomaly", False),
        }

    # ── Signal Management ─────────────────────────────────────────────────────

    def _pull_signals(self) -> List[Dict]:
        """Pull intelligence signals from Redis queue (written by sweep engine)."""
        signals = []
        try:
            while True:
                raw = self.redis.lpop(self.KEY_SIGNALS)
                if not raw:
                    break
                try:
                    signals.append(json.loads(raw))
                except json.JSONDecodeError:
                    logger.warning(f"Malformed signal discarded: {raw[:100]}")
        except Exception as e:
            logger.error(f"Signal pull failed: {e}")
        return signals

    def push_signal(self, signal: Dict):
        """Called by sweep engine to feed a signal into the brain queue."""
        self.redis.rpush(self.KEY_SIGNALS, json.dumps(signal))

    def _push_lead_to_redis(self, lead: Dict):
        self.redis.lpush(self.KEY_LEADS, json.dumps(lead))
        self.redis.ltrim(self.KEY_LEADS, 0, 499)    # keep last 500 leads

    def _push_reactive_queries(self, queries: List[str]):
        for q in queries:
            self.redis.rpush(self.KEY_REACTIVE_Q, q)
        logger.info(f"Pushed {len(queries)} reactive queries for web explorer")

    # ── Summaries ─────────────────────────────────────────────────────────────

    def _build_intel_summary(self, signals, anomalies, conclusions) -> Dict:
        return {
            "total_signals":   len(signals),
            "anomalies":       len(anomalies),
            "markets":         list({s.get("market") for s in signals}),
            "top_sources":     list({s.get("source") for s in signals}),
            "key_conclusions": [c.get("reasoning", "")[:300] for c in conclusions[:5]],
            "anomaly_markets": [a.get("market") for a in anomalies],
        }

    def _build_market_snapshots(self, markets: List[str]) -> List[Dict]:
        snapshots = []
        for market in markets:
            open_leads = self.memory.get_open_leads_for_market(market)
            snapshots.append({
                "market":       market,
                "region_score": self.feedback.get_region_score(market),
                "open_leads":   len(open_leads),
                "stale_leads":  [l for l in open_leads
                                 if l.get("urgency") == "HIGH" and l.get("status") == "OPEN"],
            })
        return snapshots

    def _self_evaluate(self, leads: List[Dict], conclusions: List[Dict]) -> Dict:
        """Brain rates its own output quality this run."""
        if not leads:
            return {"quality_score": 0, "notes": "No leads generated"}

        avg_confidence = sum(l.get("confidence", 0) for l in leads) / len(leads)
        avg_win_prob   = sum(l.get("win_probability", 0) for l in leads) / len(leads)
        has_action     = sum(1 for l in leads if l.get("lead_action") and len(l["lead_action"]) > 20)
        has_compliance = sum(1 for l in leads if l.get("compliance_flags"))
        quality_score  = int(
            avg_confidence * 0.4 + avg_win_prob * 100 * 0.3 + (has_action / len(leads)) * 30
        )

        return {
            "quality_score":         quality_score,
            "avg_confidence":        round(avg_confidence, 1),
            "avg_win_probability":   round(avg_win_prob, 3),
            "leads_with_action":     has_action,
            "leads_with_compliance": has_compliance,
            "notes": "Good run" if quality_score > 65 else "Low confidence — review signal sources",
        }

    # ── Storage ───────────────────────────────────────────────────────────────

    def _store_bd_brief(self, brief: Dict):
        key = f"{CONFIG.redis_brain_prefix}bd_brief:latest"
        self.redis.set(key, json.dumps(brief), ex=7 * 86400)

    def _store_run_report(self, report: Dict):
        key = f"{CONFIG.redis_brain_prefix}run:{self.run_id}"
        self.redis.set(key, json.dumps(report, default=str), ex=30 * 86400)
        self.redis.lpush(self.KEY_RUN_HISTORY, self.run_id)
        self.redis.ltrim(self.KEY_RUN_HISTORY, 0, 99)   # keep last 100 run IDs
        self.redis.set(self.KEY_LAST_RUN, datetime.now(timezone.utc).isoformat())

    # ── Telegram Alerts ───────────────────────────────────────────────────────

    def _fire_telegram_alert(self, lead: Dict):
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            msg = (
                f"*CRUCIX HIGH PRIORITY LEAD*\n\n"
                f"*Market:* {lead['market']}\n"
                f"*Action:* {lead['lead_action']}\n"
                f"*Win Probability:* {lead['win_probability']:.0%}\n"
                f"*Confidence:* {lead['confidence']}/100\n"
                f"*Compliance:* {', '.join(lead['compliance_flags']) or 'None flagged'}\n\n"
                f"_{lead['reasoning'][:300]}_"
            )
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": msg, "parse_mode": "Markdown"},
                timeout=10,
            )
            logger.info(f"Telegram alert fired for lead {lead['id']}")
        except Exception as e:
            logger.warning(f"Telegram alert failed: {e}")

    # ── Scheduler ─────────────────────────────────────────────────────────────

    def start_scheduler(self):
        """Start autonomous sweep loop. Blocking — run in main thread or thread."""
        interval = CONFIG.sweep_interval_hours
        logger.info(f"Starting autonomous sweep scheduler (every {interval}h)")

        schedule.every(interval).hours.do(self.run_sweep)
        # Run immediately on startup
        self.run_sweep()

        while True:
            schedule.run_pending()
            time.sleep(60)

    def get_latest_leads(self, n: int = 20) -> List[Dict]:
        """Retrieve latest generated leads from Redis."""
        raw_leads = self.redis.lrange(self.KEY_LEADS, 0, n - 1)
        leads = []
        for r in raw_leads:
            try:
                leads.append(json.loads(r))
            except Exception:
                pass
        return leads

    def get_latest_bd_brief(self) -> Optional[Dict]:
        key = f"{CONFIG.redis_brain_prefix}bd_brief:latest"
        raw = self.redis.get(key)
        return json.loads(raw) if raw else None

    def get_run_history(self, n: int = 10) -> List[Dict]:
        run_ids = self.redis.lrange(self.KEY_RUN_HISTORY, 0, n - 1)
        reports = []
        for rid in run_ids:
            raw = self.redis.get(f"{CONFIG.redis_brain_prefix}run:{rid}")
            if raw:
                try:
                    reports.append(json.loads(raw))
                except Exception:
                    pass
        return reports
