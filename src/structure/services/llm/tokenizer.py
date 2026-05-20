"""Offline token counting helpers for LLM request budgeting.

The service prefers real local tokenizers when they are available and falls
back to a conservative heuristic when they are not.  It never downloads model
files at runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import logging
import os
from typing import Any

from cachetools import TTLCache

from structure.frameworks.tool_calling.models import ChatMessage

logger = logging.getLogger(__name__)

_CACHE_MAXSIZE = int(os.getenv("LLM_TOKENIZER_CACHE_SIZE", "10000"))
_CACHE_TTL = int(os.getenv("LLM_TOKENIZER_CACHE_TTL", "3600"))


@dataclass(frozen=True)
class TokenCountResult:
    """Token count with cache/backend metadata."""

    tokens: int
    cache_hit: bool
    backend: str


def stable_json_dumps(value: Any) -> str:
    """Serialize JSON-like values deterministically for hashing/counting."""
    return json.dumps(
        _normalize_for_json(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_hash(value: Any) -> str:
    """Return a stable SHA-256 hash for JSON-like values or plain text."""
    text = value if isinstance(value, str) else stable_json_dumps(value)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize_for_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    elif hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "__dict__") and not isinstance(value, type):
        value = vars(value)

    if isinstance(value, dict):
        return {str(k): _normalize_for_json(v) for k, v in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_normalize_for_json(v) for v in value]
    return value


class TokenizerService:
    """Count request tokens using local tokenizer backends with cached results."""

    def __init__(
        self,
        *,
        cache_enabled: bool | None = None,
        cache_maxsize: int = _CACHE_MAXSIZE,
        cache_ttl: int = _CACHE_TTL,
    ) -> None:
        if cache_enabled is None:
            cache_enabled = os.getenv("LLM_TOKENIZER_CACHE_ENABLED", "true").lower() in {
                "1",
                "true",
                "yes",
            }
        self.cache_enabled = cache_enabled
        self._cache: TTLCache[tuple[str, str, str], tuple[int, str]] = TTLCache(
            maxsize=cache_maxsize,
            ttl=cache_ttl,
        )
        self._transformers_tokenizers: dict[str, Any | None] = {}

    def count_text(self, model: str, text: str) -> TokenCountResult:
        """Count tokens for a plain text segment."""
        model_key = model or "unknown"
        text = text or ""
        cache_key = ("text", model_key, stable_hash(text))
        if self.cache_enabled and cache_key in self._cache:
            tokens, backend = self._cache[cache_key]
            return TokenCountResult(tokens=tokens, cache_hit=True, backend=backend)

        tokens, backend = self._count_text_uncached(model_key, text)
        if self.cache_enabled:
            self._cache[cache_key] = (tokens, backend)
        return TokenCountResult(tokens=tokens, cache_hit=False, backend=backend)

    def count_message(self, model: str, message: ChatMessage) -> TokenCountResult:
        """Count one chat message, including role/tool-call framing overhead."""
        payload = message.to_openai_dict()
        content = stable_json_dumps(payload)
        result = self.count_text(model, content)
        # Chat-completion framing overhead varies by tokenizer/provider.  The
        # small fixed surcharge keeps estimates conservative without pretending
        # to be provider-exact for every OpenAI-compatible backend.
        return TokenCountResult(
            tokens=result.tokens + 4,
            cache_hit=result.cache_hit,
            backend=result.backend,
        )

    def count_tools(self, model: str, tools_info: Any) -> TokenCountResult:
        """Count a tool schema block or prompt-calling tool text."""
        if not tools_info:
            return TokenCountResult(tokens=0, cache_hit=True, backend="none")
        text = tools_info if isinstance(tools_info, str) else stable_json_dumps(tools_info)
        result = self.count_text(model, text)
        return TokenCountResult(
            tokens=result.tokens + 8,
            cache_hit=result.cache_hit,
            backend=result.backend,
        )

    def count_request(
        self,
        model: str,
        messages: list[ChatMessage],
        tools_info: Any = None,
    ) -> TokenCountResult:
        """Count a complete chat-completion request."""
        parts = [self.count_message(model, message) for message in messages]
        parts.append(self.count_tools(model, tools_info))
        tokens = sum(part.tokens for part in parts) + 3
        cache_hit = all(part.cache_hit for part in parts)
        backend = ",".join(sorted({part.backend for part in parts if part.backend}))
        return TokenCountResult(tokens=tokens, cache_hit=cache_hit, backend=backend)

    def _count_text_uncached(self, model: str, text: str) -> tuple[int, str]:
        tiktoken_count = self._count_with_tiktoken(model, text)
        if tiktoken_count is not None:
            return tiktoken_count, "tiktoken"

        transformers_count = self._count_with_transformers(model, text)
        if transformers_count is not None:
            return transformers_count, "transformers"

        return self._fallback_count(text), "fallback"

    @staticmethod
    def _count_with_tiktoken(model: str, text: str) -> int | None:
        try:
            import tiktoken  # type: ignore[import-not-found]
        except Exception:
            return None

        try:
            try:
                encoding = tiktoken.encoding_for_model(model)
            except Exception:
                lower = model.lower()
                if lower.startswith(("gpt-", "o1", "o3", "o4", "text-")):
                    encoding = tiktoken.get_encoding("cl100k_base")
                else:
                    return None
            return len(encoding.encode(text))
        except Exception:
            logger.debug("tiktoken count failed for model=%s", model, exc_info=True)
            return None

    def _count_with_transformers(self, model: str, text: str) -> int | None:
        tokenizer = self._get_transformers_tokenizer(model)
        if tokenizer is None:
            return None
        try:
            return len(tokenizer.encode(text, add_special_tokens=False))
        except Exception:
            logger.debug(
                "transformers tokenizer count failed for model=%s",
                model,
                exc_info=True,
            )
            return None

    def _get_transformers_tokenizer(self, model: str) -> Any | None:
        if model in self._transformers_tokenizers:
            return self._transformers_tokenizers[model]

        try:
            from transformers import AutoTokenizer  # type: ignore[import-not-found]
        except Exception:
            self._transformers_tokenizers[model] = None
            return None

        try:
            tokenizer = AutoTokenizer.from_pretrained(
                model,
                local_files_only=True,
                trust_remote_code=True,
            )
        except Exception:
            tokenizer = None

        self._transformers_tokenizers[model] = tokenizer
        return tokenizer

    @staticmethod
    def _fallback_count(text: str) -> int:
        """Conservative multilingual fallback when no offline tokenizer exists."""
        if not text:
            return 0

        ascii_chars = 0
        non_ascii_chars = 0
        for char in text:
            if ord(char) < 128:
                ascii_chars += 1
            else:
                non_ascii_chars += 1

        # English-like text averages roughly 3-4 chars/token; CJK text is often
        # closer to 1 char/token.  Use ceiling math to avoid undercounting.
        ascii_tokens = (ascii_chars + 2) // 3
        return ascii_tokens + non_ascii_chars
