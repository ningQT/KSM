"""API routes for model provider settings management."""

from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_db, require_admin_context, reset_llm_client
from app.api.models import (
    AvailableModelItem,
    AvailableModelsResponse,
    LLMSettingsRequest,
    LLMSettingsResetResponse,
    LLMSettingsResponse,
    LLMSettingsUpdateResponse,
    LLMTestRequest,
    LLMTestResponse,
    ModelConfigListResponse,
    ModelConfigSummary,
    ModelSettingsRequest,
    ModelSettingsResetResponse,
    ModelSettingsResponse,
    ModelSettingsUpdateResponse,
    ModelTestResponse,
)
from app.config import get_effective_settings, reload_effective_settings
from app.llm.client import LLMClient
from app.llm.embedding import EmbeddingService, known_embedding_dimension
from app.llm.prompts.settings import LLM_HEALTH_CHECK_SYSTEM_PROMPT
from app.llm.providers import (
    LLMNotConfiguredError,
    UnsupportedProviderCapabilityError,
    get_provider_registry,
)
from app.observability import log_event
from app.security.url_validation import validate_llm_base_url

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/v1/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin_context)],
)

_LLM_FIELDS = ["provider", "base_url", "api_key", "model", "temperature", "max_tokens"]


def _mask_api_key(key: str) -> str:
    """Mask API key for display: sk-****xxxx."""
    if not key:
        return ""
    if len(key) < 8:
        return "****"
    return f"{key[:3]}****{key[-4:]}"


def _provider_options() -> list[dict]:
    return [descriptor.public_dict() for descriptor in get_provider_registry().list_providers()]


def _build_settings_response(effective) -> LLMSettingsResponse:
    registry = get_provider_registry()
    provider_id = effective.llm_provider or ""
    provider_display_name: str | None = None
    if provider_id:
        try:
            provider_display_name = registry.get_provider(provider_id).descriptor.display_name
        except UnsupportedProviderCapabilityError:
            provider_display_name = None

    try:
        missing_fields = registry.missing_fields(effective)
    except UnsupportedProviderCapabilityError:
        missing_fields = ["provider"]

    return LLMSettingsResponse(
        provider=provider_id,
        provider_display_name=provider_display_name,
        base_url=effective.llm_base_url or "",
        api_key_masked=_mask_api_key(effective.llm_api_key),
        model=effective.llm_model or "",
        temperature=effective.llm_temperature,
        max_tokens=effective.llm_max_tokens,
        configured=not missing_fields,
        missing_fields=missing_fields,
        provider_options=_provider_options(),
    )


def _clean_update_value(field: str, value):
    if isinstance(value, str):
        value = value.strip()
    if field == "provider" and value:
        get_provider_registry().get_provider(value)
    if field == "base_url" and value:
        effective = get_effective_settings(get_db())
        value = validate_llm_base_url(
            value,
            require_https=effective.require_https_llm_base_url,
            ssrf_protection=effective.llm_ssrf_protection,
        )
    return value


@router.get("/llm", response_model=LLMSettingsResponse)
async def get_llm_settings():
    """Get current model provider configuration."""
    return _build_settings_response(get_effective_settings(get_db()))


@router.put("/llm", response_model=LLMSettingsUpdateResponse)
async def update_llm_settings(req: LLMSettingsRequest):
    """Update model provider configuration (persist to DB + hot-reload)."""
    started = time.perf_counter()
    updates = req.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="Request body must contain at least one field")
    log_event(
        logger,
        "settings.llm.update.start",
        updated_fields=sorted(updates),
    )

    unknown = sorted(set(updates) - set(_LLM_FIELDS))
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported LLM settings: {', '.join(unknown)}")

    db = get_db()
    for field, value in updates.items():
        try:
            cleaned = _clean_update_value(field, value)
        except (UnsupportedProviderCapabilityError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        db.set_setting(f"llm.{field}", json.dumps(cleaned))

    reload_effective_settings(db)
    reset_llm_client()
    settings_resp = _build_settings_response(get_effective_settings(db))
    log_event(
        logger,
        "settings.llm.update.done",
        updated_fields=sorted(updates),
        configured=settings_resp.configured,
        provider=settings_resp.provider,
        model=settings_resp.model,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return LLMSettingsUpdateResponse(updated=True, settings=settings_resp)


@router.delete("/llm", response_model=LLMSettingsResetResponse)
async def reset_llm_settings():
    """Clear console-managed model provider configuration."""
    started = time.perf_counter()
    log_event(logger, "settings.llm.reset.start")
    db = get_db()
    db.delete_settings("llm.")
    reload_effective_settings(db)
    reset_llm_client()
    log_event(
        logger,
        "settings.llm.reset.done",
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    return LLMSettingsResetResponse()


@router.post("/llm/test", response_model=LLMTestResponse)
async def test_llm_connection(req: LLMTestRequest | None = None):
    """Test model provider connectivity with current or provided config."""
    effective = get_effective_settings(get_db())
    updates = req.model_dump(exclude_none=True) if req else {}
    if "base_url" in updates and updates["base_url"]:
        try:
            updates["base_url"] = validate_llm_base_url(
                updates["base_url"],
                require_https=effective.require_https_llm_base_url,
                ssrf_protection=effective.llm_ssrf_protection,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    settings = effective.model_copy(update={f"llm_{field}": value for field, value in updates.items()})
    model_name = settings.llm_model or ""
    log_event(
        logger,
        "settings.llm.test.start",
        provider=settings.llm_provider,
        model=model_name,
        override_fields=sorted(updates),
    )

    start = time.monotonic()
    try:
        result = await asyncio.to_thread(
            LLMClient(settings).chat_completion,
            [
                {"role": "system", "content": LLM_HEALTH_CHECK_SYSTEM_PROMPT},
                {"role": "user", "content": "Hi"},
            ],
            max_tokens=min(settings.llm_max_tokens or 64, 64),
            call_name="settings.llm_test",
        )
        latency = int((time.monotonic() - start) * 1000)
        log_event(
            logger,
            "settings.llm.test.done",
            success=True,
            provider=settings.llm_provider,
            model=result.model or model_name,
            duration_ms=latency,
        )
        return LLMTestResponse(success=True, model=result.model or model_name, latency_ms=latency)
    except (LLMNotConfiguredError, UnsupportedProviderCapabilityError) as exc:
        latency = int((time.monotonic() - start) * 1000)
        log_event(
            logger,
            "settings.llm.test.done",
            level=logging.WARNING,
            success=False,
            provider=settings.llm_provider,
            model=model_name,
            duration_ms=latency,
            error_type=exc.__class__.__name__,
        )
        return LLMTestResponse(success=False, model=model_name, latency_ms=latency, error=str(exc))
    except Exception as exc:
        latency = int((time.monotonic() - start) * 1000)
        logger.warning("Unexpected error testing LLM connection: %s", exc)
        log_event(
            logger,
            "settings.llm.test.done",
            level=logging.WARNING,
            success=False,
            provider=settings.llm_provider,
            model=model_name,
            duration_ms=latency,
            error_type=exc.__class__.__name__,
        )
        return LLMTestResponse(
            success=False,
            model=model_name,
            latency_ms=latency,
            error=f"{type(exc).__name__}: {str(exc)[:500]}",
        )



def _fetch_openai_models(base_url: str, api_key: str) -> list[str]:
    """Fetch model IDs from an OpenAI-compatible endpoint."""
    if not base_url:
        return []
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        with httpx.Client(timeout=10.0) as client:
            response = client.get(f"{base_url.rstrip('/')}/models", headers=headers)
            response.raise_for_status()
            data = response.json()
    except Exception:
        return []
    return [
        str(item.get("id") or item.get("name"))
        for item in data.get("data", [])
        if isinstance(item, dict) and (item.get("id") or item.get("name"))
    ]


_MODEL_CATEGORIES = {"llm", "embedding"}
_EMBEDDING_MODEL_MARKERS = (
    "embed",
    "bge",
    "gte",
    "voyage",
    "jina",
    "text-embedding",
)
_CHAT_MODEL_MARKERS = (
    "chat",
    "instruct",
    "gpt",
    "claude",
    "gemini",
    "qwen",
    "deepseek",
    "llama",
    "step",
    "flash",
    "thinking",
)


def _model_matches_category(model_id: str, category: str) -> bool:
    """按模型 ID 启发式判断模型是否属于目标类别。"""
    lowered = model_id.casefold()
    is_embedding = any(marker in lowered for marker in _EMBEDDING_MODEL_MARKERS)
    if category == "embedding":
        return is_embedding
    return not is_embedding


def _model_capability_source(model_id: str, category: str) -> str:
    """返回模型能力来源，避免把未验证模型描述为确定能力。"""
    lowered = model_id.casefold()
    if category == "embedding":
        return "heuristic" if any(marker in lowered for marker in _EMBEDDING_MODEL_MARKERS) else "unverified"
    if any(marker in lowered for marker in _CHAT_MODEL_MARKERS):
        return "heuristic"
    return "unverified"


def _category_key(category: str, field: str) -> str:
    return f"{category}.{field}"


def _category_values(category: str, effective):
    if category == "llm":
        return {
            "provider": effective.llm_provider,
            "base_url": effective.llm_base_url,
            "api_key": effective.llm_api_key,
            "model": effective.llm_model,
            "temperature": effective.llm_temperature,
            "max_tokens": effective.llm_max_tokens,
        }
    return {
        "provider": effective.embedding_provider,
        "base_url": effective.effective_embedding_base_url,
        "api_key": effective.effective_embedding_api_key,
        "model": effective.embedding_model,
        "dimension": effective.embedding_dimension,
        "batch_size": effective.embedding_batch_size,
    }


def _missing_category_fields(category: str, values: dict) -> list[str]:
    required = ("provider", "base_url", "api_key", "model")
    return [name for name in required if not str(values.get(name) or "").strip()]


def _build_model_settings_response(category: str, effective) -> ModelSettingsResponse:
    values = _category_values(category, effective)
    provider_id = str(values.get("provider") or "")
    provider_display_name = None
    if provider_id:
        try:
            provider_display_name = get_provider_registry().get_provider(provider_id).descriptor.display_name
        except UnsupportedProviderCapabilityError:
            provider_display_name = None
    missing = _missing_category_fields(category, values)
    parameters = (
        {"temperature": values["temperature"], "max_tokens": values["max_tokens"]}
        if category == "llm"
        else {"dimension": values["dimension"], "batch_size": values["batch_size"]}
    )
    return ModelSettingsResponse(
        category=category,
        provider=provider_id,
        provider_display_name=provider_display_name,
        base_url=str(values.get("base_url") or ""),
        api_key_masked=_mask_api_key(str(values.get("api_key") or "")),
        model=str(values.get("model") or ""),
        configured=not missing,
        missing_fields=missing,
        parameters=parameters,
        provider_options=_provider_options(),
    )


@router.get("/models", response_model=ModelConfigListResponse)
async def list_model_configs():
    """List LLM and embedding configuration summaries."""
    effective = get_effective_settings(get_db())
    return ModelConfigListResponse(
        items=[
            ModelConfigSummary(
                category=category,
                provider=str(_category_values(category, effective).get("provider") or ""),
                model=str(_category_values(category, effective).get("model") or ""),
                base_url=str(_category_values(category, effective).get("base_url") or ""),
                api_key_masked=_mask_api_key(str(_category_values(category, effective).get("api_key") or "")),
                configured=not _missing_category_fields(category, _category_values(category, effective)),
                missing_fields=_missing_category_fields(category, _category_values(category, effective)),
            )
            for category in ("llm", "embedding")
        ],
        provider_options=_provider_options(),
    )


@router.get("/models/{category}", response_model=ModelSettingsResponse)
async def get_model_settings(category: str):
    """Get one model category configuration."""
    if category not in _MODEL_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be llm or embedding")
    return _build_model_settings_response(category, get_effective_settings(get_db()))


@router.put("/models/{category}", response_model=ModelSettingsUpdateResponse)
async def update_model_settings(category: str, req: ModelSettingsRequest):
    """Persist one model category configuration and refresh effective settings."""
    if category not in _MODEL_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be llm or embedding")
    db = get_db()
    values = req.model_dump(exclude_none=True)
    allowed = {"provider", "base_url", "api_key", "model", "temperature", "max_tokens"} if category == "llm" else {"provider", "base_url", "api_key", "model", "dimension", "batch_size"}
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unsupported {category} settings: {', '.join(unknown)}")
    for field, value in values.items():
        if isinstance(value, str):
            value = value.strip()
        if field == "provider" and value:
            get_provider_registry().get_provider(value)
        if field == "base_url" and value:
            effective = get_effective_settings(db)
            value = validate_llm_base_url(
                value,
                require_https=effective.require_https_llm_base_url,
                ssrf_protection=effective.llm_ssrf_protection,
            )
        db.set_setting(_category_key(category, field), json.dumps(value))
    reload_effective_settings(db)
    reset_llm_client()
    return ModelSettingsUpdateResponse(
        updated=True,
        settings=_build_model_settings_response(category, get_effective_settings(db)),
    )


@router.delete("/models/{category}", response_model=ModelSettingsResetResponse)
async def reset_model_settings(category: str):
    """Clear one model category configuration."""
    if category not in _MODEL_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be llm or embedding")
    db = get_db()
    db.delete_settings(f"{category}.")
    reload_effective_settings(db)
    reset_llm_client()
    return ModelSettingsResetResponse(reset=True, message=f"{category} configuration cleared.")


@router.post("/models/{category}/test", response_model=ModelTestResponse)
async def test_model_settings(category: str, req: ModelSettingsRequest | None = None):
    """Test current or draft model configuration without saving it."""
    if category not in _MODEL_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be llm or embedding")
    effective = get_effective_settings(get_db())
    updates = req.model_dump(exclude_none=True) if req else {}
    prefix = "llm_" if category == "llm" else "embedding_"
    settings = effective.model_copy(update={f"{prefix}{key}": value for key, value in updates.items()})
    if category == "llm":
        result = await test_llm_connection(LLMTestRequest(**updates))
        return ModelTestResponse(success=result.success, model=result.model, latency_ms=result.latency_ms, error=result.error)
    service = EmbeddingService(settings)
    start = time.monotonic()
    success, dimension, error = await asyncio.to_thread(service.test_connection)
    return ModelTestResponse(
        success=success,
        model=settings.embedding_model,
        latency_ms=int((time.monotonic() - start) * 1000),
        dimension=dimension,
        error=error,
    )


@router.post("/models/{category}/available", response_model=AvailableModelsResponse)
async def available_models(category: str, req: ModelSettingsRequest | None = None):
    """Return provider models with explicit capability-source metadata."""
    if category not in _MODEL_CATEGORIES:
        raise HTTPException(status_code=400, detail="category must be llm or embedding")
    effective = get_effective_settings(get_db())
    updates = req.model_dump(exclude_none=True) if req else {}
    prefix = "llm_" if category == "llm" else "embedding_"
    settings = effective.model_copy(update={f"{prefix}{key}": value for key, value in updates.items()})
    raw_models: list[str] = []
    source = "examples"
    warnings: list[str] = []
    if category == "embedding":
        raw_models = await asyncio.to_thread(EmbeddingService(settings).list_models)
    else:
        raw_models = await asyncio.to_thread(_fetch_openai_models, settings.llm_base_url, settings.llm_api_key)
        if not raw_models:
            try:
                provider = get_provider_registry().get_provider(settings.llm_provider)
                raw_models = list(provider.descriptor.model_examples)
            except UnsupportedProviderCapabilityError:
                warnings.append("UNSUPPORTED_PROVIDER")
    models = [
        model_id
        for model_id in raw_models
        if _model_matches_category(model_id, category)
    ]
    if raw_models:
        source = "provider" if category == "embedding" or settings.llm_base_url else "examples"
    items = []
    for model_id in models:
        capabilities = ["embedding"] if category == "embedding" else ["chat"]
        capability_source = _model_capability_source(model_id, category)
        items.append(
            AvailableModelItem(
                id=model_id,
                display_name=model_id,
                capabilities=capabilities,
                capability_source=capability_source,
                dimension=known_embedding_dimension(model_id) if category == "embedding" else None,
            )
        )
    if not models:
        if raw_models:
            warnings.append("NO_MATCHING_MODELS")
        else:
            warnings.append("NO_MODELS")
    return AvailableModelsResponse(category=category, models=items, source=source, warnings=warnings)
