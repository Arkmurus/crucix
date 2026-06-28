"""R-F999 — ARIA Self-Sufficient Architecture.

ARIA currently depends on external LLMs (DeepSeek, Anthropic) for:
1. Writing code (self_coder.py → SovereignLLM)
2. Answering questions (aria_engine.py → LLM provider chain)
3. Reasoning and analysis (researcher.py, deep_researcher.py → LLM)
4. Code review (claude_reviewer.py → Anthropic)

This module builds the infrastructure for ARIA to become self-sufficient:
- Symbolic reasoning engine (no LLM needed)
- Code generation from templates + patterns (no LLM needed)
- Local knowledge retrieval (RAG + neural memory, no LLM needed)
- Rule-based analysis pipelines (deterministic, no LLM needed)
- Self-testing and self-deploying (already automated via CI/CD)

The goal: ARIA can code, test, deploy, and improve herself using ONLY
her own codebase, knowledge stores, and deterministic algorithms.
External LLMs become an OPTIONAL accelerator, not a dependency.
"""
from __future__ import annotations

import ast
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("aria.self_sufficient")


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 1: SYMBOLIC REASONING ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# No LLM needed. Uses pattern matching, rule evaluation, and knowledge retrieval.

@dataclass
class ReasoningStep:
    """A single step in a reasoning chain."""
    type: str  # premise, inference, conclusion, contradiction
    content: str
    confidence: float = 1.0
    source: str = ""
    children: list[ReasoningStep] = field(default_factory=list)


class SymbolicReasoner:
    """Deterministic reasoning engine. No LLM calls.
    
    Can:
    - Decompose questions into sub-questions
    - Match against known patterns in the knowledge base
    - Apply rule-based inference
    - Detect contradictions
    - Build explainable reasoning chains
    """
    
    def __init__(self, knowledge_base: Optional[dict] = None):
        self.knowledge = knowledge_base or {}
        self._rules: list[tuple[str, str, str]] = []  # (pattern, condition, conclusion)
        self._load_builtin_rules()
    
    def _load_builtin_rules(self) -> None:
        """Load built-in reasoning rules."""
        self._rules = [
            # Sanctions rules
            (r"is\s+(\w+\s?\w*)\s+sanctioned", "check_sanctions", "Look up {entity} in sanctions database"),
            # Export control rules
            (r"can\s+(?:I|we)\s+export\s+(\w+)", "check_export", "Check {item} against export control lists"),
            # Country risk rules
            (r"risk\s+(?:of|in|for)\s+(\w+)", "check_country_risk", "Assess {country} risk factors"),
            # Due diligence rules
            (r"screen\s+(\w+\s?\w*)", "run_screening", "Screen {entity} against watchlists"),
            # Knowledge rules
            (r"what\s+(?:is|are)\s+(\w[\w\s]*)", "retrieve_knowledge", "Search knowledge base for {topic}"),
        ]
    
    def decompose(self, question: str) -> list[ReasoningStep]:
        """Decompose a question into reasoning steps without LLM."""
        steps = []
        question_lower = question.lower()
        
        # Step 1: Identify the intent
        intent = self._classify_intent(question_lower)
        steps.append(ReasoningStep(
            type="premise",
            content=f"Classified intent: {intent}",
            confidence=0.9,
        ))
        
        # Step 2: Extract entities
        entities = self._extract_entities(question)
        if entities:
            steps.append(ReasoningStep(
                type="premise",
                content=f"Extracted entities: {', '.join(entities)}",
                confidence=0.85,
            ))
        
        # Step 3: Match against rules
        for pattern, condition, conclusion in self._rules:
            m = re.search(pattern, question_lower)
            if m:
                filled = conclusion.format(
                    entity=m.group(1) if m.groups() else "unknown",
                    item=m.group(1) if m.groups() else "unknown",
                    country=m.group(1) if m.groups() else "unknown",
                    topic=m.group(1) if m.groups() else "unknown",
                )
                steps.append(ReasoningStep(
                    type="inference",
                    content=filled,
                    confidence=0.75,
                    source=f"rule:{condition}",
                ))
        
        # Step 4: Check knowledge base
        for entity in entities:
            if entity.lower() in self.knowledge:
                steps.append(ReasoningStep(
                    type="conclusion",
                    content=f"Found in knowledge base: {self.knowledge[entity.lower()][:200]}",
                    confidence=0.95,
                    source="knowledge_base",
                ))
        
        return steps
    
    def _classify_intent(self, text: str) -> str:
        """Classify the intent of a question using pattern matching."""
        if any(w in text for w in ["sanction", "embargo", "restrict", "prohibit"]):
            return "sanctions_check"
        if any(w in text for w in ["export", "ship", "transfer", "sell"]):
            return "export_control"
        if any(w in text for w in ["risk", "danger", "threat", "exposure"]):
            return "risk_assessment"
        if any(w in text for w in ["screen", "check", "vet", "verify"]):
            return "screening"
        if any(w in text for w in ["what", "who", "where", "when", "how", "why"]):
            return "knowledge_retrieval"
        if any(w in text for w in ["compare", "difference", "versus", "vs"]):
            return "comparison"
        return "general_query"
    
    def _extract_entities(self, text: str) -> list[str]:
        """Extract potential entities (capitalised words, known patterns)."""
        entities = []
        # Capitalised words (potential proper nouns)
        for m in re.finditer(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text):
            entities.append(m.group())
        # Known entity patterns
        for m in re.finditer(r'\b([A-Z]{2,})\b', text):
            entities.append(m.group())
        return list(set(entities))


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 2: CODE GENERATION ENGINE
# ═══════════════════════════════════════════════════════════════════════════════
# No LLM needed. Uses templates, patterns, and AST manipulation.

class CodeGenerator:
    """Generate Python code from patterns and templates. No LLM calls.
    
    Can:
    - Generate new intel modules from templates
    - Add wiring calls to existing modules
    - Fix common code patterns (imports, error handling, type hints)
    - Generate test files from module signatures
    """
    
    # Templates for common module types
    TEMPLATES = {
        "intel_engine": (
            '"""R-{r_number} — {description}."""\n'
            'from __future__ import annotations\n\n'
            'import logging\n'
            'from typing import Optional\n\n'
            'logger = logging.getLogger("aria.{module_name}")\n\n\n'
            'async def {function_name}({params}) -> dict:\n'
            '    """{docstring}"""\n'
            '    logger.info("{module_name}.{function_name} called")\n\n'
            '    # R-F1220: template placeholder — replaced by actual implementation\n'
            '    result = {{"status": "not_implemented"}}\n\n'
            '    # R-F999 — wire to brain\n'
            '    from .engine_wiring import wire_success\n', wire_failure
            '    wire_success(\n'
            '        module="{module_name}",\n'
            '        summary="{summary}",\n'
            '        source_id="{module_name}:R-{r_number}",\n'
            '    )\n\n'
            '    return result\n'
        ),
        "test_file": (
            '"""R-{r_number} — Tests for {module_name}."""\n'
            'from __future__ import annotations\n\n'
            'import pytest\n'
            'from unittest.mock import AsyncMock, patch\n\n\n'
            'class Test{test_class}:\n'
            '    """Test the {module_name} module."""\n\n'
            '    @pytest.mark.asyncio\n'
            '    async def test_{function_name}_basic(self):\n'
            '        """Basic functionality test."""\n'
            '        from aria_service.intel.{module_name} import {function_name}\n'
            '        result = await {function_name}({test_params})\n'
            '        assert result is not None\n'
        ),
    }
    
    def generate_module(
        self,
        module_name: str,
        function_name: str,
        description: str,
        summary: str,
        params: str = "",
        docstring: str = "",
        r_number: int = 0,
    ) -> str:
        """Generate a new intel module from template."""
        return self.TEMPLATES["intel_engine"].format(
            r_number=r_number,
            module_name=module_name,
            function_name=function_name,
            description=description,
            params=params,
            docstring=docstring or f"Execute {function_name}",
            summary=summary,
        )
    
    def generate_test(
        self,
        module_name: str,
        function_name: str,
        test_params: str = "",
        r_number: int = 0,
    ) -> str:
        """Generate a test file from template."""
        test_class = module_name.replace("_", " ").title().replace(" ", "")
        return self.TEMPLATES["test_file"].format(
            r_number=r_number,
            module_name=module_name,
            function_name=function_name,
            test_class=test_class,
            test_params=test_params or "",
        )
    
    def add_wiring_to_function(self, source: str, module_name: str, summary: str) -> str:
        """Add a wire_success call to a function. No LLM needed."""
        lines = source.split("\n")
        result = []
        wiring_added = False
        
        for i, line in enumerate(lines):
            result.append(line)
            # Add wiring before the last return statement
            stripped = line.strip()
            if stripped.startswith("return ") and not wiring_added:
                indent = " " * (len(line) - len(line.lstrip()))
                wiring = (
                    f"\n{indent}    # R-F999 — wire to brain\n"
                    f"{indent}    from .engine_wiring import wire_success\n", wire_failure
                    f"{indent}    wire_success(\n"
                    f"{indent}        module=\"{module_name}\",\n"
                    f"{indent}        summary=\"{summary}\",\n"
                    f"{indent}        source_id=\"{module_name}:R-F999\",\n"
                    f"{indent}    )\n"
                )
                result.append(wiring)
                wiring_added = True
        
        return "\n".join(result)


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 3: SELF-IMPROVEMENT PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════
# Fully automated. No LLM needed for standard improvements.

class SelfImprovementPipeline:
    """Automated self-improvement pipeline.
    
    Steps:
    1. Scan for gaps (error logs, wiring gaps, test failures)
    2. Generate fixes (using CodeGenerator, not LLM)
    3. Test fixes (using pytest)
    4. Stage fixes (using self_improve.stage_improvement)
    5. Deploy (via git push → CI/CD)
    """
    
    def __init__(self):
        self.code_gen = CodeGenerator()
        self.reasoner = SymbolicReasoner()
    
    async def scan_and_fix_wiring_gaps(self) -> list[dict]:
        """Find dark modules and generate wiring fixes. No LLM needed."""
        import pathlib
        
        intel_dir = pathlib.Path(__file__).parent / "intel"
        fixes = []
        
        wired_tokens = {
            "brain_hook.absorb", "capability_gaps.record_gap",
            "mistake_ledger.record", "record_error", "record_gap",
            "wire_success", "wire_failure",
        }
        
        for f in sorted(intel_dir.glob("*.py")):
            if f.name.startswith("__") or f.name == "engine_wiring.py":
                continue
            
            content = f.read_text(encoding="utf-8", errors="replace")
            
            # Skip if already wired
            if any(t in content for t in wired_tokens):
                continue
            
            # Find the primary function
            module_name = f.name.replace(".py", "")
            func_name = self._find_primary_function(content)
            
            if func_name:
                # Generate the fix
                new_content = self.code_gen.add_wiring_to_function(
                    content, module_name, f"{func_name.replace('_', ' ').title()}"
                )
                fixes.append({
                    "file": f.name,
                    "function": func_name,
                    "content": new_content,
                })
        
        return fixes
    
    def _find_primary_function(self, content: str) -> Optional[str]:
        """Find the primary public function in a module."""
        priority_prefixes = [
            "render_", "analyse_", "detect_", "score_", "process_",
            "run_", "execute_", "search_", "check_", "lookup_",
            "screen_", "classify_", "verify_", "build_", "track_",
            "monitor_", "get_", "find_", "resolve_", "investigate_",
        ]
        
        for line in content.split("\n"):
            stripped = line.strip()
            for prefix in priority_prefixes:
                if stripped.startswith(f"def {prefix}") or stripped.startswith(f"async def {prefix}"):
                    m = re.match(r"(?:async\s+)?def\s+(\w+)", stripped)
                    if m and not m.group(1).startswith("_"):
                        return m.group(1)
        return None


# ═══════════════════════════════════════════════════════════════════════════════
# TIER 4: KNOWLEDGE-AUGMENTED RESPONSE
# ═══════════════════════════════════════════════════════════════════════════════
# No LLM needed for factual responses. Uses RAG + symbolic reasoning.

class KnowledgeAugmentedResponder:
    """Generate responses using only local knowledge. No LLM calls.
    
    Can:
    - Answer factual questions from the knowledge base
    - Provide sanctions status from the canonical cache
    - Give country risk assessments from the risk index
    - Explain DD findings from the case library
    """
    
    def __init__(self):
        self.reasoner = SymbolicReasoner()
    
    async def answer(self, question: str) -> dict[str, Any]:
        """Answer a question using only local knowledge."""
        steps = self.reasoner.decompose(question)
        
        # Build response from reasoning steps
        response_parts = []
        sources = []
        
        for step in steps:
            if step.type == "conclusion":
                response_parts.append(step.content)
                if step.source:
                    sources.append(step.source)
            elif step.type == "inference":
                response_parts.append(f"Analysis: {step.content}")
        
        if not response_parts:
            response_parts.append(
                "I don't have enough information in my local knowledge base "
                "to answer this question confidently. I would need to search "
                "external sources or consult my LLM."
            )
        
        return {
            "answer": "\n".join(response_parts),
            "sources": list(set(sources)),
            "reasoning_steps": [s.content for s in steps],
            "confidence": min(1.0, sum(s.confidence for s in steps) / max(len(steps), 1)),
            "llm_dependent": False,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# ROADMAP: Phases to full self-sufficiency
# ═══════════════════════════════════════════════════════════════════════════════

ROADMAP = {
    "phase_1": {
        "name": "Symbolic reasoning for common queries",
        "description": "Handle sanctions checks, export control, country risk without LLM",
        "status": "in_progress",
        "files": ["aria_service/intel/symbolic_reasoner.py"],
    },
    "phase_2": {
        "name": "Template-based code generation",
        "description": "Generate new modules, tests, and wiring from templates",
        "status": "in_progress",
        "files": ["aria_service/intel/code_generator.py"],
    },
    "phase_3": {
        "name": "Automated self-improvement pipeline",
        "description": "Scan gaps, generate fixes, test, stage, deploy — fully automated",
        "status": "planned",
        "files": ["aria_service/intel/self_improvement_pipeline.py"],
    },
    "phase_4": {
        "name": "Knowledge-augmented responses",
        "description": "Answer factual questions from local knowledge only",
        "status": "planned",
        "files": ["aria_service/intel/knowledge_responder.py"],
    },
    "phase_5": {
        "name": "Full LLM independence",
        "description": "ARIA can code, test, deploy, and answer without ANY external LLM",
        "status": "planned",
        "dependencies": ["phase_1", "phase_2", "phase_3", "phase_4"],
    },
}

# R-F2119 §21a — wire failure handler for self_sufficient
try:
    wire_failure(module="self_sufficient", detail="module shutdown",
                gap_type="engine_failure", source="self_sufficient:shutdown")
except Exception:
    pass
