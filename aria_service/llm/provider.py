"""
LLM Provider — Base class and factory.
All providers expose the same interface: complete(system, user, opts) → LLMResult.
Streaming: stream(system, user, opts) → AsyncGenerator[str] for token-by-token output.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
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

    async def stream(
        self,
        system_prompt: str,
        user_message: str,
        *,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        on_done: "Optional[callable]" = None,
    ) -> AsyncGenerator[str, None]:
        """Yield text chunks as the LLM generates them.

        Default implementation falls back to complete() and yields the
        full text as a single chunk — every provider works without
        modification. Override for true token-by-token streaming.

        on_done: optional callback(LLMResult) fired after the stream
        completes, carrying final token counts and model info.
        """
        result = await self.complete(
            system_prompt, user_message,
            max_tokens=max_tokens, timeout=timeout,
        )
        yield result.text
        if on_done:
            on_done(result)
