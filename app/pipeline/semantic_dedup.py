"""Semantic deduplication decision maker for the KSM write pipeline.

Uses SemanticIndex for cosine-similarity search to detect duplicate notes
and decide whether to merge or create new cards.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

from app.config import Settings, get_settings
from app.storage.semantic_index import SemanticIndex

logger = logging.getLogger(__name__)


class SemanticDeduplicator:
    """Makes deduplication decisions by querying the semantic index.

    Provides three core operations:
    - find_duplicate_source: strict check for duplicate source notes (0.92)
    - find_duplicate_cards: batch check for duplicate cards (0.88)
    - should_merge_or_create: decision logic for card merge vs create (0.90)
    """

    def __init__(
        self,
        semantic_index: SemanticIndex,
        settings: Settings | None = None,
    ) -> None:
        """Initialize the deduplicator.

        Args:
            semantic_index: The semantic index for similarity searches.
            settings: Application settings. If None, loads from get_settings().
        """
        self._index = semantic_index
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def find_duplicate_source(
        self,
        instance_id: str,
        new_title: str,
        new_summary: str,
        new_concepts: list[str],
    ) -> str | None:
        """Search for an existing source note that is a duplicate.

        Uses a strict threshold (0.92) to avoid false positives.

        Args:
            instance_id: The vault instance ID.
            new_title: Title of the new source note.
            new_summary: Summary of the new source note.
            new_concepts: List of concept tags.

        Returns:
            The file_path of the duplicate source note if found, else None.
        """
        query_text = self._build_query_text(new_title, new_summary, new_concepts)
        threshold = self._settings.dedup_source_threshold

        results = self._index.find_similar(
            instance_id,
            query_text,
            threshold=threshold,
            top_k=5,
        )

        # Filter to source notes only
        for result in results:
            if result.get("type") == "source":
                logger.info(
                    "Found duplicate source: %s (score=%.4f)",
                    result["file_path"],
                    result["score"],
                )
                return result["file_path"]

        return None

    def find_duplicate_cards(
        self,
        instance_id: str,
        candidate_cards: list[dict[str, Any]],
    ) -> dict[str, str]:
        """Batch search for duplicate cards among candidates.

        For each candidate card, searches for similar existing cards
        using a relaxed threshold (0.88).

        Args:
            instance_id: The vault instance ID.
            candidate_cards: List of dicts with keys:
                - title: Card title
                - summary: Card summary
                - concepts: List of concept tags (optional)

        Returns:
            Dict mapping card_title -> existing_file_path for duplicates.
            Cards without duplicates are not included.
        """
        threshold = self._settings.dedup_card_threshold
        duplicates: dict[str, str] = {}

        for card in candidate_cards:
            title = card.get("title", "")
            summary = card.get("summary", "")
            concepts = card.get("concepts", [])

            query_text = self._build_query_text(title, summary, concepts)

            results = self._index.find_similar(
                instance_id,
                query_text,
                threshold=threshold,
                top_k=5,
            )

            # Filter to card notes only
            for result in results:
                if result.get("type") == "card":
                    logger.info(
                        "Found duplicate card '%s': %s (score=%.4f)",
                        title,
                        result["file_path"],
                        result["score"],
                    )
                    duplicates[title] = result["file_path"]
                    break  # Take the best match for this card

        return duplicates

    def should_merge_or_create(
        self,
        instance_id: str,
        new_card_title: str,
        new_card_summary: str,
    ) -> tuple[str, str]:
        """Decide whether to merge with an existing card or create a new one.

        Uses a medium threshold (0.90) for the merge decision.

        Args:
            instance_id: The vault instance ID.
            new_card_title: Title of the new card.
            new_card_summary: Summary of the new card.

        Returns:
            A tuple of (action, file_path) where:
            - action is "merge" or "create"
            - file_path is the path to merge with (if action="merge"), else ""
        """
        query_text = self._build_query_text(new_card_title, new_card_summary, [])
        threshold = self._settings.dedup_merge_threshold

        results = self._index.find_similar(
            instance_id,
            query_text,
            threshold=threshold,
            top_k=5,
        )

        # Filter to card notes only and return the best match
        for result in results:
            if result.get("type") == "card":
                logger.info(
                    "Merge decision for '%s': merge with %s (score=%.4f)",
                    new_card_title,
                    result["file_path"],
                    result["score"],
                )
                return ("merge", result["file_path"])

        logger.info(
            "Merge decision for '%s': create new card",
            new_card_title,
        )
        return ("create", "")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_query_text(title: str, summary: str, concepts: list[str]) -> str:
        """Build query text from note metadata for embedding.

        Uses the same format as SemanticIndex._build_embed_text for consistency.
        """
        concept_str = ", ".join(concepts) if concepts else ""
        parts = [title, summary, concept_str]
        return "\n".join(parts)

@dataclass(frozen=True)
class RuleDedupDecision:
    """规则去重决定。"""

    action: str
    title_similarity: float
    concept_jaccard: float
    summary_overlap: float
    combined_score: float
    strong_signal: bool
    reason: str


def _normalize_terms(value: str) -> set[str]:
    """将中文、英文和数字片段提取为可比较词项。"""
    return {item.casefold() for item in re.findall(r"[\w\u4e00-\u9fff]+", value or "") if item}


def _jaccard(left: set[str], right: set[str]) -> float:
    """计算两个集合的交并比。"""
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


def _text_similarity(left: str, right: str) -> float:
    """计算标题或别名的归一化相似度。"""
    return SequenceMatcher(None, (left or "").strip().casefold(), (right or "").strip().casefold()).ratio()


def score_rule_dedup(
    *,
    title: str,
    aliases: list[str],
    concepts: list[str],
    summary: str,
    other_title: str,
    other_aliases: list[str] | None = None,
    other_concepts: list[str] | None = None,
    other_summary: str = "",
    settings: Settings | None = None,
) -> RuleDedupDecision:
    """按标题、别名、概念和摘要计算规则去重分数。

    Args:
        title: 新卡片标题。
        aliases: 新卡片别名。
        concepts: 新卡片概念。
        summary: 新卡片摘要。
        other_title: 候选卡片标题。
        other_aliases: 候选卡片别名。
        other_concepts: 候选卡片概念。
        other_summary: 候选卡片摘要。
        settings: 阈值配置。

    Returns:
        可解释的规则去重决定。
    """
    effective = settings or get_settings()
    left_titles = [title, *aliases]
    right_titles = [other_title, *(other_aliases or [])]
    title_similarity = max((_text_similarity(a, b) for a in left_titles for b in right_titles), default=0.0)
    concept_jaccard = _jaccard(_normalize_terms(" ".join(concepts)), _normalize_terms(" ".join(other_concepts or [])))
    summary_overlap = _jaccard(_normalize_terms(summary), _normalize_terms(other_summary))
    combined = 0.45 * title_similarity + 0.35 * concept_jaccard + 0.20 * summary_overlap
    exact_alias = any(a.strip().casefold() == b.strip().casefold() for a in left_titles for b in right_titles if a and b)
    strong_signal = exact_alias or (len(_normalize_terms(" ".join(concepts)) & _normalize_terms(" ".join(other_concepts or []))) >= 3 and concept_jaccard >= 0.6)
    duplicate = strong_signal or (
        combined >= effective.rule_dedup_combined_threshold
        and concept_jaccard >= effective.rule_dedup_concept_threshold
    )
    reason = "exact alias match" if exact_alias else "strong concept evidence" if strong_signal else "combined threshold" if duplicate else "below threshold"
    return RuleDedupDecision(
        action="reject" if duplicate else "create",
        title_similarity=round(title_similarity, 6),
        concept_jaccard=round(concept_jaccard, 6),
        summary_overlap=round(summary_overlap, 6),
        combined_score=round(combined, 6),
        strong_signal=strong_signal,
        reason=reason,
    )


class RuleDeduplicator:
    """不依赖 embedding 的笔记去重器。"""

    def __init__(self, db: Any, settings: Settings | None = None) -> None:
        """保存数据库和配置。

        Args:
            db: 数据库后端。
            settings: 应用配置。
        """
        self._db = db
        self._settings = settings or get_settings()

    def should_merge_or_create(
        self,
        instance_id: str,
        *,
        title: str,
        summary: str,
        concepts: list[str],
    ) -> tuple[str, str, RuleDedupDecision]:
        """返回规则去重的 merge/create 决定。"""
        rows = self._db.execute(
            "SELECT file_path, frontmatter FROM notes WHERE instance_id = ? AND graph_layer = 2",
            (instance_id,),
        )
        best_path = ""
        best_decision = score_rule_dedup(
            title=title,
            aliases=[],
            concepts=concepts,
            summary=summary,
            other_title="",
            settings=self._settings,
        )
        best_score = -1.0
        for row in rows:
            try:
                frontmatter = json.loads(row.get("frontmatter") or "{}")
            except (json.JSONDecodeError, TypeError):
                frontmatter = {}
            decision = score_rule_dedup(
                title=title,
                aliases=[],
                concepts=concepts,
                summary=summary,
                other_title=str(frontmatter.get("title") or ""),
                other_aliases=[str(v) for v in frontmatter.get("aliases", [])],
                other_concepts=[str(v) for v in frontmatter.get("concepts", [])],
                other_summary=str(frontmatter.get("summary") or ""),
                settings=self._settings,
            )
            if decision.combined_score > best_score:
                best_score = decision.combined_score
                best_path = str(row["file_path"])
                best_decision = decision
        if best_decision.action in {"merge", "reject"} and best_path:
            return ("merge", best_path, best_decision)
        return ("create", "", best_decision)
