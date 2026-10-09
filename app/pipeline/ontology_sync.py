"""Write-side ontology candidate extraction.

Best-effort, non-blocking sync that extracts ontology candidates
from card frontmatter after a document passes through the write pipeline.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.core.ontology_service import OntologyDuplicateError, OntologyService, _norm_key
from app.schema.parser import parse_frontmatter
from app.storage.database import DatabaseBackend

logger = logging.getLogger(__name__)

# Pipeline relation_type → ontology relation_type
_RELATION_TYPE_MAP: dict[str, str] = {
    "dependency": "related_to",
    "comparison": "related_to",
    "composition": "part_of",
    "extension": "is_a",
}

_AUTO_SOURCE = "auto"
_CONCEPT_CONFIDENCE = 0.7
_RELATION_ENTITY_CONFIDENCE = 0.55
_RELATION_CONFIDENCE = 0.6
_MAX_RELATION_ENTITY_SUPPLEMENTS_PER_CARD = 3
_MAX_CONCEPT_RELATIONS_PER_CARD = 4
_MAX_ENTITY_NAME_CHARS = 32
_GENERIC_TERMS = {
    "概念",
    "方法",
    "知识",
    "背景",
    "事件",
    "人物",
    "关系",
    "来源",
    "summary",
    "overview",
    "concept",
    "method",
    "knowledge",
}


def sync_ontology_candidates(
    db: DatabaseBackend,
    instance_id: str,
    card_paths: list[str],
    card_contents: list[str],
    classification: Any = None,
) -> dict[str, int]:
    """Extract ontology candidates and return task metrics."""
    try:
        return _do_sync(db, instance_id, card_paths, card_contents, classification)
    except Exception:
        logger.warning("Ontology sync failed for instance %s (non-blocking)", instance_id, exc_info=True)
        return {"entity_candidate_count": 0, "relation_count": 0}


def _do_sync(
    db: DatabaseBackend,
    instance_id: str,
    card_paths: list[str],
    card_contents: list[str],
    classification: Any,
) -> dict[str, int]:
    from app.pipeline.query_dictionary import invalidate_query_caches
    svc = OntologyService(db, on_change=invalidate_query_caches)

    # Track normalized entity name → entity_id for relation resolution.
    entity_map: dict[str, str] = {}
    card_concept_keys: dict[str, set[str]] = {}
    card_entity_ids_by_path: dict[str, set[str]] = {}
    card_title_keys = _collect_card_title_keys(card_paths, card_contents)
    relation_count = 0
    created_entity_count = 0
    settings = get_settings()
    per_card_entity_limit = settings.entity_candidate_max_per_card
    per_document_entity_limit = settings.entity_candidate_max_per_document

    # 1. Extract entities from card concepts. Card titles are not entities by default.
    for card_path, card_content in zip(card_paths, card_contents):
        fm, _ = parse_frontmatter(card_content)
        title = fm.get("title") or fm.get("name") or ""
        concepts = _extract_concepts(fm)
        concept_keys: set[str] = set()
        concept_names: list[str] = []
        card_entity_ids: set[str] = set()
        for concept in concepts:
            if len(card_entity_ids) >= per_card_entity_limit:
                break
            if not _is_valid_entity_name(concept):
                continue
            concept_key = _norm_key(concept)
            if concept_key in concept_keys:
                continue
            concept_keys.add(concept_key)
            concept_names.append(concept)
            entity, created = _get_or_create_entity(
                svc,
                instance_id,
                concept,
                card_path,
                confidence=_CONCEPT_CONFIDENCE,
                allow_create=created_entity_count < per_document_entity_limit,
            )
            if entity is None:
                continue
            if created:
                created_entity_count += 1
            card_entity_ids.add(entity["id"])
            entity_map[concept_key] = entity["id"]
            _safe_add_entity_link(
                svc,
                instance_id,
                entity["id"],
                card_path,
                title or concept,
                link_type=_link_type_for_concept(title, concept, concepts),
                confidence=_CONCEPT_CONFIDENCE,
            )
        title_text = str(title or "").strip()
        title_key = _norm_key(title_text)
        if (
            _is_valid_title_entity(title_text, concept_keys)
            and title_key not in entity_map
            and len(card_entity_ids) < per_card_entity_limit
        ):
            title_entity, created = _get_or_create_entity(
                svc,
                instance_id,
                title_text,
                card_path,
                confidence=_CONCEPT_CONFIDENCE,
                allow_create=created_entity_count < per_document_entity_limit,
            )
            if title_entity is not None:
                if created:
                    created_entity_count += 1
                card_entity_ids.add(title_entity["id"])
                entity_map.setdefault(title_key, title_entity["id"])
                _safe_add_entity_link(
                    svc,
                    instance_id,
                    title_entity["id"],
                    card_path,
                    title_text,
                    link_type="definition",
                    confidence=_CONCEPT_CONFIDENCE,
                )
        card_concept_keys[card_path] = concept_keys
        card_entity_ids_by_path[card_path] = card_entity_ids
        relation_count += _create_concept_relations_for_card(
            svc,
            instance_id,
            card_path,
            title,
            concept_names,
            entity_map,
        )

    # 2. Extract relations from relation_descriptions, with capped supplemental entities.
    for card_path, card_content in zip(card_paths, card_contents):
        fm, _ = parse_frontmatter(card_content)
        rel_descs = fm.get("relation_descriptions") or []
        if not isinstance(rel_descs, list):
            continue
        supplemental_count = 0
        supplement_limit = max(
            0,
            per_card_entity_limit - len(card_entity_ids_by_path.get(card_path, set())),
        )
        for rel_desc in rel_descs:
            if not isinstance(rel_desc, dict):
                continue
            supplemental_count, created_delta = _process_relation(
                svc,
                instance_id,
                rel_desc,
                entity_map,
                card_path,
                card_concept_keys.get(card_path, set()),
                card_title_keys,
                supplemental_count,
                supplement_limit,
                created_entity_count,
                per_document_entity_limit,
            )
            created_entity_count += created_delta

    # 3. Extract domain as entity type from classification
    if classification is not None:
        domain = getattr(classification, "domain", None)
        if domain and isinstance(domain, str) and domain.strip():
            type_data = _safe_create_type(svc, instance_id, domain.strip())
            if type_data is not None:
                _assign_type_to_entities(svc, instance_id, entity_map, type_data["id"])

    return {
        "entity_candidate_count": min(created_entity_count, per_document_entity_limit),
        "relation_count": relation_count,
    }


def _get_or_create_entity(
    svc: OntologyService,
    instance_id: str,
    name: str,
    card_path: str,
    confidence: float,
    *,
    allow_create: bool = True,
) -> tuple[dict[str, Any] | None, bool]:
    """获取或创建实体，并返回本次是否实际创建了实体。"""
    existing = _find_entity_by_name(svc, instance_id, name)
    if existing is not None:
        return existing, False
    if not allow_create:
        return None, False
    try:
        created = svc.create_entity(
            instance_id,
            name=name,
            description=f"Auto-extracted from {card_path}",
            source=_AUTO_SOURCE,
            confidence=confidence,
            status="candidate",
        )
        return created, True
    except OntologyDuplicateError:
        # Find existing entity by name
        existing = _find_entity_by_name(svc, instance_id, name)
        return existing, False
    except Exception:
        logger.debug("Failed to create entity '%s'", name, exc_info=True)
        return None, False


def _find_entity_by_name(
    svc: OntologyService,
    instance_id: str,
    name: str,
) -> dict[str, Any] | None:
    """Find an entity by normalized name."""
    norm = _norm_key(name)
    entities = svc.list_entities(instance_id)
    for ent in entities:
        if _norm_key(ent["name"]) == norm:
            return ent
    return None


def _extract_concepts(frontmatter: dict[str, Any]) -> list[str]:
    concepts = frontmatter.get("concepts") or []
    if isinstance(concepts, str):
        concepts = [concepts]
    if not isinstance(concepts, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for value in concepts:
        if not isinstance(value, str):
            continue
        text = value.strip()
        key = _norm_key(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _collect_card_title_keys(card_paths: list[str], card_contents: list[str]) -> set[str]:
    keys: set[str] = set()
    for card_path, card_content in zip(card_paths, card_contents):
        path_stem = Path(card_path).stem.strip()
        if path_stem:
            keys.add(_norm_key(path_stem))
        fm, _ = parse_frontmatter(card_content)
        title = str(fm.get("title") or fm.get("name") or "").strip()
        if title:
            keys.add(_norm_key(title))
    return keys


def _is_valid_entity_name(value: str) -> bool:
    text = value.strip()
    if len(text) < 2 or len(text) > _MAX_ENTITY_NAME_CHARS:
        return False
    if _norm_key(text) in _GENERIC_TERMS:
        return False
    if any(ch in text for ch in "\n\r。！？!?；;：:，,、"):
        return False
    return True


def _is_valid_title_entity(title: str, concept_keys: set[str]) -> bool:
    title = str(title or "").strip()
    if not title or not concept_keys or _norm_key(title) in concept_keys:
        return False
    if len(title) < 2 or len(title) > _MAX_ENTITY_NAME_CHARS:
        return False
    if _looks_like_composite_title(title):
        return False
    return _is_valid_entity_name(title)


def _looks_like_composite_title(title: str) -> bool:
    composite_markers = (
        "与",
        "和",
        "及",
        "以及",
        "中的",
        "之间",
        "关于",
        "建立",
        "事件",
    )
    return any(marker in title for marker in composite_markers)


def _link_type_for_concept(title: str, concept: str, concepts: list[str]) -> str:
    title = str(title or "").strip()
    if not title:
        return "mention"
    if _norm_key(title) == _norm_key(concept):
        return "definition"
    if len(concepts) == 1 and _norm_key(concepts[0]) == _norm_key(concept):
        return "definition"
    return "mention"


def _safe_add_entity_link(
    svc: OntologyService,
    instance_id: str,
    entity_id: str,
    card_path: str,
    title: str,
    *,
    link_type: str,
    confidence: float,
) -> None:
    try:
        svc.add_entity_link(
            instance_id,
            entity_id,
            card_path,
            link_type=link_type,
            snippet=f"Auto-linked from {title}",
            confidence=confidence,
        )
    except OntologyDuplicateError:
        pass
    except Exception:
        logger.debug("Failed to add entity link for '%s'", title, exc_info=True)


def _create_concept_relations_for_card(
    svc: OntologyService,
    instance_id: str,
    card_path: str,
    title: str,
    concepts: list[str],
    entity_map: dict[str, str],
) -> int:
    if len(concepts) < 2:
        return 0
    hub = _select_relation_hub(title, concepts)
    if hub is None:
        return 0
    hub_id = entity_map.get(_norm_key(hub))
    if hub_id is None:
        return 0

    created = 0
    for concept in concepts:
        if _norm_key(concept) == _norm_key(hub):
            continue
        target_id = entity_map.get(_norm_key(concept))
        if target_id is None:
            continue
        try:
            svc.create_relation(
                instance_id,
                source_entity_id=hub_id,
                target_entity_id=target_id,
                relation_type="related_to",
                description=f"共同出现在知识卡片《{title or Path(card_path).stem}》的概念中。",
                source=_AUTO_SOURCE,
                confidence=_RELATION_CONFIDENCE,
                status="candidate",
            )
            created += 1
        except OntologyDuplicateError:
            pass
        except Exception:
            logger.debug("Failed to create concept relation %s→%s", hub, concept, exc_info=True)
        if created >= min(_MAX_CONCEPT_RELATIONS_PER_CARD, get_settings().ontology_relation_max_per_card):
            return created
    return created


def _select_relation_hub(title: str, concepts: list[str]) -> str | None:
    title_key = _norm_key(title)
    if title_key:
        for concept in concepts:
            if _norm_key(concept) == title_key:
                return concept
        for concept in concepts:
            if concept and concept in title:
                return concept
    return concepts[0] if concepts else None


def _resolve_or_supplement_entity(
    svc: OntologyService,
    instance_id: str,
    name: str,
    card_path: str,
    entity_map: dict[str, str],
    concept_keys: set[str],
    card_title_keys: set[str],
    existing_id: str | None,
    supplemental_count: int,
    supplement_limit: int,
    created_entity_count: int,
    per_document_entity_limit: int,
) -> tuple[str | None, int, int]:
    if existing_id is not None:
        return existing_id, supplemental_count, 0

    key = _norm_key(name)
    if key in card_title_keys:
        return None, supplemental_count, 0
    if key in concept_keys:
        return entity_map.get(key), supplemental_count, 0
    if supplemental_count >= min(
        _MAX_RELATION_ENTITY_SUPPLEMENTS_PER_CARD,
        supplement_limit,
    ):
        return None, supplemental_count, 0
    if not _is_valid_entity_name(name):
        return None, supplemental_count, 0

    entity, created = _get_or_create_entity(
        svc,
        instance_id,
        name,
        card_path,
        confidence=_RELATION_ENTITY_CONFIDENCE,
        allow_create=created_entity_count < per_document_entity_limit,
    )
    if entity is None:
        return None, supplemental_count, 0

    entity_map[key] = entity["id"]
    _safe_add_entity_link(
        svc,
        instance_id,
        entity["id"],
        card_path,
        name,
        link_type="mention",
        confidence=_RELATION_ENTITY_CONFIDENCE,
    )
    return entity["id"], supplemental_count + 1, 1 if created else 0


def _process_relation(
    svc: OntologyService,
    instance_id: str,
    rel_desc: dict[str, Any],
    entity_map: dict[str, str],
    card_path: str,
    concept_keys: set[str],
    card_title_keys: set[str],
    supplemental_count: int,
    supplement_limit: int,
    created_entity_count: int,
    per_document_entity_limit: int,
) -> tuple[int, int]:
    """Process a single relation_description entry."""
    source_name = str(rel_desc.get("source", "") or "").strip()
    target_name = str(rel_desc.get("target", "") or "").strip()
    rel_type = rel_desc.get("relation_type", "")
    description = rel_desc.get("description", "")

    if not source_name or not target_name:
        return supplemental_count, 0

    # Map relation type
    ontology_rel_type = _RELATION_TYPE_MAP.get(rel_type, "related_to")

    # Resolve entity IDs
    source_id = entity_map.get(_norm_key(source_name))
    target_id = entity_map.get(_norm_key(target_name))

    source_id, supplemental_count, source_created = _resolve_or_supplement_entity(
        svc,
        instance_id,
        source_name,
        card_path,
        entity_map,
        concept_keys,
        card_title_keys,
        source_id,
        supplemental_count,
        supplement_limit,
        created_entity_count,
        per_document_entity_limit,
    )
    target_id, supplemental_count, target_created = _resolve_or_supplement_entity(
        svc,
        instance_id,
        target_name,
        card_path,
        entity_map,
        concept_keys,
        card_title_keys,
        target_id,
        supplemental_count,
        supplement_limit,
        created_entity_count + source_created,
        per_document_entity_limit,
    )
    created_delta = source_created + target_created

    if source_id is None or target_id is None:
        return supplemental_count, created_delta

    try:
        svc.create_relation(
            instance_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            relation_type=ontology_rel_type,
            description=description,
            source=_AUTO_SOURCE,
            confidence=_RELATION_CONFIDENCE,
            status="candidate",
        )
    except OntologyDuplicateError:
        pass
    except Exception:
        logger.debug("Failed to create relation %s→%s", source_name, target_name, exc_info=True)
    return supplemental_count, created_delta


def _safe_create_type(
    svc: OntologyService,
    instance_id: str,
    name: str,
) -> dict[str, Any] | None:
    """Create an entity type, silently skipping duplicates."""
    try:
        return svc.create_type(
            instance_id,
            name=name,
            description="Auto-extracted domain type",
            source=_AUTO_SOURCE,
            confidence=_RELATION_CONFIDENCE,
            status="candidate",
        )
    except OntologyDuplicateError:
        return _find_type_by_name(svc, instance_id, name)
    except Exception:
        logger.debug("Failed to create type '%s'", name, exc_info=True)
        return None


def _find_type_by_name(
    svc: OntologyService,
    instance_id: str,
    name: str,
) -> dict[str, Any] | None:
    norm = _norm_key(name)
    types = svc.list_types(instance_id)
    for item in types:
        if _norm_key(item["name"]) == norm:
            return item
    return None


def _assign_type_to_entities(
    svc: OntologyService,
    instance_id: str,
    entity_map: dict[str, str],
    type_id: str,
) -> None:
    for entity_id in entity_map.values():
        try:
            entity = svc.get_entity(instance_id, entity_id)
            if entity.get("entity_type_id"):
                continue
            svc.update_entity(instance_id, entity_id, {"entity_type_id": type_id})
        except Exception:
            logger.debug("Failed to assign type '%s' to entity '%s'", type_id, entity_id, exc_info=True)
