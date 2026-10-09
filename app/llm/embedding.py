"""Embedding service for semantic similarity computation."""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

# Timeout for embedding API calls (seconds)
_EMBEDDING_TIMEOUT = 60.0

# 常见固定维度模型；未知模型继续沿用调用方显式配置的维度参数。
_KNOWN_EMBEDDING_DIMENSIONS = {
    "baai/bge-m3": 1024,
    "bge-m3": 1024,
    "bge-large-zh-v1.5": 1024,
    "bge-large-en-v1.5": 1024,
    "text-embedding-3-small": 1536,
    "text-embedding-3-large": 3072,
    "text-embedding-ada-002": 1536,
}
_VARIABLE_DIMENSION_MODEL_PREFIXES = (
    "text-embedding-3-",
)


def known_embedding_dimension(model_name: str) -> int | None:
    """返回已知 embedding 模型的默认维度。

    Args:
        model_name: 供应商返回或用户输入的模型名称。

    Returns:
        已知维度；无法确定时返回 ``None``。
    """
    return _KNOWN_EMBEDDING_DIMENSIONS.get((model_name or "").casefold())


def _is_fixed_dimension_model(model_name: str) -> bool:
    """判断模型是否使用固定向量维度，避免发送不支持的 dimensions 参数。"""
    normalized = (model_name or "").casefold()
    if any(normalized.startswith(prefix) for prefix in _VARIABLE_DIMENSION_MODEL_PREFIXES):
        return False
    return known_embedding_dimension(normalized) is not None


class EmbeddingService:
    """Service for converting text to semantic vectors using an OpenAI-compatible
    embedding API.

    Provides graceful degradation: when the API is unavailable or returns an error,
    methods return empty results rather than raising exceptions.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._last_error: str | None = None

    @property
    def _base_url(self) -> str:
        return self._settings.effective_embedding_base_url

    @property
    def _api_key(self) -> str:
        return self._settings.effective_embedding_api_key

    @property
    def _model(self) -> str:
        return self._settings.embedding_model

    @property
    def _dimension(self) -> int:
        return self._settings.embedding_dimension

    @property
    def _batch_size(self) -> int:
        return self._settings.embedding_batch_size

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def embed_text(self, text: str) -> list[float]:
        """Compute an embedding vector for a single text.

        Returns an empty list when the API call fails or the response is
        invalid, allowing callers to degrade gracefully.
        """
        if not text.strip():
            logger.warning("embed_text called with empty/whitespace text, returning []")
            return []
        results = self._call_api([text])
        if results:
            return results[0]
        return []

    def test_connection(self) -> tuple[bool, int | None, str | None]:
        """Test the embedding endpoint and verify the returned dimension.

        Returns:
            成功标志、实际维度和错误信息。
        """
        vector = self.embed_text("connection test")
        if not vector:
            return False, None, self._last_error or "Embedding request failed"
        if self._dimension and len(vector) != self._dimension:
            return False, len(vector), f"Expected dimension {self._dimension}, got {len(vector)}"
        return True, len(vector), None

    def list_models(self) -> list[str]:
        """Fetch model identifiers from an OpenAI-compatible models endpoint."""
        if not self._base_url:
            return []
        url = f"{self._base_url.rstrip('/')}/models"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        try:
            with httpx.Client(timeout=10.0) as client:
                response = client.get(url, headers=headers)
                response.raise_for_status()
                data = response.json()
        except Exception as exc:
            logger.warning("Failed to list embedding models: %s", exc)
            return []
        models = data.get("data", []) if isinstance(data, dict) else []
        return [str(item.get("id") or item.get("name")) for item in models if isinstance(item, dict) and (item.get("id") or item.get("name"))]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Compute embedding vectors for multiple texts.

        Texts are sent in chunks of ``embedding_batch_size`` to stay within
        API limits.  Each chunk that fails produces empty-list entries so that
        the output length always matches the input length.
        """
        if not texts:
            return []

        all_embeddings: list[list[float]] = [None] * len(texts)  # type: ignore[list-item]

        for offset in range(0, len(texts), self._batch_size):
            chunk = texts[offset : offset + self._batch_size]
            # Filter out empty strings but track their positions
            non_empty_indices: list[int] = []
            non_empty_texts: list[str] = []
            for i, t in enumerate(chunk):
                if t.strip():
                    non_empty_indices.append(offset + i)
                    non_empty_texts.append(t)
                else:
                    all_embeddings[offset + i] = []

            if non_empty_texts:
                results = self._call_api(non_empty_texts)
                for idx_in_chunk, global_idx in enumerate(non_empty_indices):
                    if idx_in_chunk < len(results):
                        all_embeddings[global_idx] = results[idx_in_chunk]
                    else:
                        all_embeddings[global_idx] = []

        # Safety: fill any remaining None (shouldn't happen, but be defensive)
        return [e if e is not None else [] for e in all_embeddings]

    @staticmethod
    def cosine_similarity(a: list[float], b: list[float]) -> float:
        """Compute cosine similarity between two vectors.

        Returns 0.0 for zero vectors or vectors of different dimensions.
        """
        if not a or not b or len(a) != len(b):
            return 0.0

        dot = 0.0
        norm_a = 0.0
        norm_b = 0.0
        for ai, bi in zip(a, b, strict=True):
            dot += ai * bi
            norm_a += ai * ai
            norm_b += bi * bi

        denom = math.sqrt(norm_a) * math.sqrt(norm_b)
        if denom == 0.0:
            return 0.0

        return dot / denom

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _call_api(self, texts: list[str]) -> list[list[float]]:
        """POST to the embeddings endpoint and return parsed vectors.

        On any failure, logs a warning and returns an empty list.
        """
        self._last_error = None
        if not self._base_url:
            self._last_error = "Embedding base URL is not configured"
            logger.warning(
                "Embedding base URL is not configured; cannot call embedding API"
            )
            return []

        url = f"{self._base_url.rstrip('/')}/embeddings"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        payload: dict[str, Any] = {
            "input": texts if len(texts) > 1 else texts[0],
            "model": self._model,
        }
        # Request the configured dimension when supported by the provider
        if self._dimension and not _is_fixed_dimension_model(self._model):
            payload["dimensions"] = self._dimension

        try:
            with httpx.Client(timeout=_EMBEDDING_TIMEOUT) as client:
                response = client.post(url, json=payload, headers=headers)
                response.raise_for_status()
                data = response.json()
        except httpx.HTTPStatusError as exc:
            self._last_error = self._sanitize_error(
                f"HTTP {exc.response.status_code}: {exc.response.text[:500]}"
            )
            logger.warning(
                "Embedding API returned HTTP %s: %s",
                exc.response.status_code,
                exc.response.text[:500],
            )
            return []
        except httpx.RequestError as exc:
            self._last_error = self._sanitize_error(f"Request failed: {exc}")
            logger.warning("Embedding API request failed: %s", exc)
            return []
        except Exception as exc:
            self._last_error = self._sanitize_error(f"Unexpected error: {exc}")
            logger.warning("Unexpected error calling embedding API: %s", exc)
            return []

        vectors = _parse_embedding_response(data, len(texts))
        if not vectors:
            self._last_error = "Embedding response was invalid or incomplete"
        return vectors

    def _sanitize_error(self, message: str) -> str:
        """移除错误信息中可能出现的完整 API Key。"""
        if self._api_key:
            return message.replace(self._api_key, "***")
        return message


def _parse_embedding_response(data: dict[str, Any], expected_count: int) -> list[list[float]]:
    """Extract embedding vectors from an OpenAI-compatible response.

    Returns a list of vectors sorted by their ``index`` field.  If parsing
    fails or the response shape is unexpected, returns an empty list.
    """
    try:
        raw_data = data.get("data", [])
        if not raw_data:
            logger.warning("Embedding response contained no data entries")
            return []

        # Sort by index to maintain order
        sorted_entries = sorted(raw_data, key=lambda e: e.get("index", 0))
        embeddings = [entry["embedding"] for entry in sorted_entries]

        if len(embeddings) != expected_count:
            logger.warning(
                "Expected %d embeddings, got %d",
                expected_count,
                len(embeddings),
            )
            return []

        return embeddings
    except (KeyError, TypeError, IndexError) as exc:
        logger.warning("Failed to parse embedding response: %s", exc)
        return []
