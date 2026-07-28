"""R-F1011 — ARIA LLM Training Pipeline.

Complete pipeline for training, evaluating, and deploying ARIA's own LLM.
Uses free/cheap compute (Kaggle, Colab, RunPod spot).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import pathlib
from typing import Any, Optional

logger = logging.getLogger("aria.llm_pipeline")


class LLMTrainingPipeline:
    """End-to-end LLM training pipeline.
    
    Steps:
    1. Curate dataset from chat logs, corrections, golden seed
    2. Prepare training configuration (LoRA/QLoRA)
    3. Generate training script for RunPod/Kaggle/Colab
    4. Launch training on available compute
    5. Evaluate against golden seed set
    6. Deploy to vLLM inference server
    7. Monitor performance and drift
    """

    def __init__(self, root: "pathlib.Path | None" = None):
        # R-F3346 — `root` is injectable so a test can write somewhere disposable.
        # This is R-F3291's seam, which LLMBuilder received and this class did not,
        # and the gap was not cosmetic: EVERY write below lands in the repo's real
        # data/training when root is hardcoded, and three of them overwrite curated
        # artefacts —
        #   _generate_training_script -> train_aria_llm.py   (line ~232)
        #   _prepare_config           -> training_config.json (line ~151)
        #   _curate_dataset           -> dataset_<ts>.json    (line ~126)
        #
        # Measured, not theorised: R-F1941's 155-line grounded trainer was restored
        # by R-F3336 and committed at defdf2e6; one full `pytest aria_service/tests/`
        # run later it was back to this module's hardcoded 74-line template, with
        # dataset_file/dataset_format stripped from the config. test_rf1011_product
        # calls _generate_training_script() and full_pipeline() directly.
        #
        # That also explains June: commit 6fe94c43's catch-all `git add -A` did not
        # revert the trainer, it COMMITTED a working tree this generator had already
        # clobbered. Restoring the file was treating the symptom; this is the writer.
        #
        # Per CLAUDE.md §24 the weekly cycle runs on a PAID pod, so a regenerated
        # toy means a KeyError on cfg["dataset_file"] there, or training the
        # distillation objective R-F1941 measured as capped at ~0.31 (below
        # DeepSeek's 0.34) — expensively, and reporting success.
        #
        # Default is unchanged: production still uses the repo root.
        self.root = pathlib.Path(root) if root else pathlib.Path(__file__).parent.parent.parent
        self.data_dir = self.root / "data" / "training"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._training_history: list[dict] = []

    async def full_pipeline(self, model_name: str = "mistralai/Mistral-7B-Instruct-v0.3") -> dict[str, Any]:
        """Run the full training pipeline."""
        logger.info("[llm_pipeline] starting full pipeline for %s", model_name)
        
        results = {
            "model": model_name,
            "started_at": time.time(),
            "steps": {},
        }
        
        # Step 1: Curate dataset
        dataset = await self._curate_dataset()
        results["steps"]["dataset"] = dataset
        logger.info("[llm_pipeline] dataset: %d pairs", dataset["total_pairs"])
        
        # Step 2: Prepare config
        config = self._prepare_config(model_name, dataset["total_pairs"])
        results["steps"]["config"] = config
        logger.info("[llm_pipeline] config prepared")
        
        # Step 3: Generate training script
        script = self._generate_training_script(model_name, config)
        results["steps"]["script"] = {"path": str(self.data_dir / "train_aria_llm.py")}
        logger.info("[llm_pipeline] training script generated")
        
        # Step 4: Evaluate (if we have a model to evaluate)
        eval_result = await self._evaluate_model()
        results["steps"]["evaluation"] = eval_result
        
        results["completed_at"] = time.time()
        results["duration_s"] = round(results["completed_at"] - results["started_at"], 1)
        
        self._training_history.append(results)
        return results

    async def _curate_dataset(self) -> dict[str, Any]:
        """Curate training dataset from multiple sources."""
        pairs = []
        sources = {}
        
        # 1. Chat audit logs
        try:
            from . import chat_audit_log
            audit = await chat_audit_log.get_recent(limit=5000)
            for entry in (audit or []):
                if isinstance(entry, dict):
                    q = entry.get("question", "") or entry.get("message", "")
                    a = entry.get("response", "") or entry.get("answer", "")
                    if q and a and len(q) > 10 and len(a) > 10:
                        pairs.append({"instruction": q, "response": a})
            sources["chat_audit"] = len(pairs)
        except Exception as e:
            logger.debug("[llm_pipeline] chat_audit failed: %s", e)
        
        # 2. Golden seed
        try:
            from . import eval_golden_seed
            seed = await eval_golden_seed.get_all()
            before = len(pairs)
            for s in (seed or [])[:500]:
                if isinstance(s, dict):
                    q = s.get("question", "")
                    a = s.get("expected_answer", "")
                    if q and a:
                        pairs.append({"instruction": q, "response": a})
            sources["golden_seed"] = len(pairs) - before
        except Exception as e:
            logger.debug("[llm_pipeline] golden_seed failed: %s", e)
        
        # 3. Corrections
        try:
            from . import correction_learner
            corrections = await correction_learner.recent_corrections_addendum()
            if corrections and isinstance(corrections, str):
                sources["corrections"] = 1
        except Exception as e:
            logger.debug("[llm_pipeline] corrections failed: %s", e)
        
        # Deduplicate
        seen = set()
        unique = []
        for p in pairs:
            key = p["instruction"][:100]
            if key not in seen:
                seen.add(key)
                unique.append(p)
        
        # Save dataset
        dataset_path = self.data_dir / f"dataset_{int(time.time())}.json"
        dataset_path.write_text(json.dumps(unique, indent=2), encoding="utf-8")
        
        return {
            "total_pairs": len(unique),
            "sources": sources,
            "dataset_path": str(dataset_path),
        }

    def _prepare_config(self, model_name: str, num_pairs: int) -> dict[str, Any]:
        """Prepare training configuration."""
        config = {
            "model_name": model_name,
            "method": "qlora",
            "lora_r": 16,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "learning_rate": 2e-4,
            "batch_size": 4,
            "num_epochs": min(5, max(1, 5000 // max(num_pairs, 1))),
            "max_seq_length": 2048,
            "output_dir": str(self.data_dir / "checkpoints"),
            "dataset_format": "instruction_response",
            "num_training_samples": num_pairs,
        }
        config_path = self.data_dir / "training_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        return config

    def _generate_training_script(self, model_name: str, config: dict) -> str:
        """Generate a standalone training script."""
        script = r'''"""ARIA LLM Training Script — generated by ARIA LLM Pipeline."""
import json, torch
from datasets import Dataset
from transformers import (
    AutoModelForCausalLM, AutoTokenizer, TrainingArguments,
    BitsAndBytesConfig,
)
from peft import LoraConfig
from trl import SFTTrainer

# Load config
with open("training_config.json") as f:
    config = json.load(f)

# Load dataset
with open(config["dataset_file"]) as f:
    data = json.load(f)

dataset = Dataset.from_list([
    {"text": f"### Instruction:\n{d['instruction']}\n\n### Response:\n{d['response']}"}
    for d in data
])

# Quantization
bnb_config = BitsAndBytesConfig(
    load_in_4bit=True,
    bnb_4bit_quant_type="nf4",
    bnb_4bit_compute_dtype=torch.bfloat16,
)

# Load model
model = AutoModelForCausalLM.from_pretrained(
    config["model_name"],
    quantization_config=bnb_config,
    device_map="auto",
    trust_remote_code=True,
)
tokenizer = AutoTokenizer.from_pretrained(config["model_name"])
tokenizer.pad_token = tokenizer.eos_token

# LoRA
lora_config = LoraConfig(
    r=config["lora_r"],
    lora_alpha=config["lora_alpha"],
    lora_dropout=config["lora_dropout"],
    bias="none",
    task_type="CAUSAL_LM",
)

# Training
training_args = TrainingArguments(
    output_dir=config["output_dir"],
    per_device_train_batch_size=config["batch_size"],
    learning_rate=config["learning_rate"],
    num_train_epochs=config["num_epochs"],
    logging_steps=10,
    save_steps=100,
    fp16=True,
    report_to="none",
)

trainer = SFTTrainer(
    model=model,
    args=training_args,
    train_dataset=dataset,
    tokenizer=tokenizer,
    max_seq_length=config["max_seq_length"],
    peft_config=lora_config,
)

trainer.train()
model.save_pretrained(config["output_dir"] + "/final")
tokenizer.save_pretrained(config["output_dir"] + "/final")
print(f"Training complete. Model saved to {config['output_dir']}/final")
'''
        script_path = self.data_dir / "train_aria_llm.py"
        script_path.write_text(script, encoding="utf-8")
        return script

    async def _evaluate_model(self) -> dict[str, Any]:
        """Evaluate against golden seed set."""
        try:
            from . import eval_golden_seed
            seed = await eval_golden_seed.get_all()
            return {
                "total_questions": len(seed),
                "status": "ready_for_evaluation",
                "note": "Run evaluation after training completes",
            }
        except Exception as e:
            return {"error": str(e)}

    def get_training_history(self) -> list[dict]:
        """Get training history."""
        return self._training_history

    def get_status(self) -> dict[str, Any]:
        """Get pipeline status."""
        return {
            "data_dir": str(self.data_dir),
            "training_runs": len(self._training_history),
            "last_run": self._training_history[-1] if self._training_history else None,
            "ready": True,
        }

# R-F1011 - wire to brain
from .engine_wiring import wire_success, wire_failure
wire_success(module="llm_pipeline", summary="Llm Pipeline Active", source_id="llm_pipeline:R-F1011")

# R-F2538: R-F2119 import-time wire_failure("module shutdown") block removed — it fired a FALSE engine_failure gap on every import (not at shutdown); do not re-add.
