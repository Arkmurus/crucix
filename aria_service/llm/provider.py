"""
LLM Provider — Base class and factory.
All providers expose the same interface: complete(system, user, opts) → LLMResult.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("aria.llm")


@dataclass
class LLMResult:
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    routed_via: str = ""  # hybrid only


class LLMProvider(ABC):
    name: str = "base"

    @property
    @abstractmethod
    def is_configured(self) -> bool: ...

    @abstractmethod
    async def complete(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 60.0,
    ) -> LLMResult: ...
