"""Ingest service - entry point for document ingestion."""

import json
import logging
from collections.abc import Callable
from pathlib import Path

from app.config import Settings
from app.exceptions import IngestFailedError, InstanceNotFoundError
from app.llm.client import LLMClient
from app.pipeline.write_pipeline import IngestResult, WritePipeline
from app.schema.validator import SchemaValidator
from app.storage.database import DatabaseBackend
from app.storage.filesystem import StorageBackend
from app.storage.indexer import Indexer
from app.storage.semantic_index import SemanticIndex

logger = logging.getLogger(__name__)


class IngestService:
    """Handles document ingestion into knowledge base.

    Reference: 详细设计文档 Section 6
    """

    def __init__(
        self,
        db: DatabaseBackend,
        storage: StorageBackend,
        llm: LLMClient,
        settings: Settings,
        indexer: Indexer | None = None,
        semantic_index: SemanticIndex | None = None,
    ):
        self.db = db
        self.storage = storage
        self.llm = llm
        self.settings = settings
        self.indexer = indexer or Indexer(db)
        self.validator = SchemaValidator()
        self.semantic_index = semantic_index

    def ingest_document(
        self,
        instance_id: str,
        file_path: str,
        filename: str | None = None,
        auto_map: bool | None = True,
        domain_hint: str | None = None,
        progress_callback: Callable[[int, str, dict | None], None] | None = None,
        cancel_check: Callable[[], bool] | None = None,
        job_id: str | None = None,
    ) -> IngestResult:
        """Ingest a markdown document into a knowledge base instance.

        1. Validate instance exists
        2. Read the document
        3. Run the write pipeline
        """
        # Validate instance
        instances = self.db.execute(
            "SELECT id, vault_path, auto_map, config_json FROM instances WHERE id = ?",
            (instance_id,),
        )
        if not instances:
            raise InstanceNotFoundError(instance_id)

        instance = instances[0]
        vault_path = instance["vault_path"]
        language = _instance_language(instance.get("config_json"))
        if auto_map is None:
            auto_map = bool(instance.get("auto_map", True))

        # Read document
        if not filename:
            filename = Path(file_path).name

        try:
            markdown = self.storage.read_file(file_path)
        except Exception:
            # Try as absolute path
            try:
                with open(file_path, encoding="utf-8") as f:
                    markdown = f.read()
            except Exception as e:
                raise IngestFailedError(f"Cannot read file: {e}")

        # Run pipeline
        pipeline = WritePipeline(
            db=self.db,
            storage=self.storage,
            llm=self.llm,
            indexer=self.indexer,
            validator=self.validator,
            settings=self.settings,
            semantic_index=self.semantic_index,
        )

        result = pipeline.execute(
            instance_id=instance_id,
            vault_path=vault_path,
            markdown=markdown,
            filename=filename,
            domain_hint=domain_hint,
            auto_map=auto_map,
            language=language,
            progress_callback=progress_callback,
            cancel_check=cancel_check,
            job_id=job_id,
        )

        if result.status == "failed":
            raise IngestFailedError("; ".join(result.warnings))

        return result


def _instance_language(config_json: object) -> str:
    if isinstance(config_json, str) and config_json.strip():
        try:
            config = json.loads(config_json)
        except json.JSONDecodeError:
            config = {}
    elif isinstance(config_json, dict):
        config = config_json
    else:
        config = {}
    language = str(config.get("language") or "zh").strip().lower()
    return language if language in {"zh", "en"} else "zh"

def build_ingest_preflight(settings: Settings) -> dict[str, object]:
    """Build model configuration preflight data for sync and async ingest.

    Args:
        settings: Effective runtime settings.

    Returns:
        A JSON-serializable preflight payload without secrets.
    """
    llm_missing = [
        name
        for name, value in (
            ("provider", settings.llm_provider),
            ("base_url", settings.llm_base_url),
            ("api_key", settings.llm_api_key),
            ("model", settings.llm_model),
        )
        if not str(value or "").strip()
    ]
    embedding_missing = [
        name
        for name, value in (
            ("provider", settings.embedding_provider),
            ("base_url", settings.effective_embedding_base_url),
            ("api_key", settings.effective_embedding_api_key),
            ("model", settings.embedding_model),
        )
        if not str(value or "").strip()
    ]
    embedding_configured = not embedding_missing
    dedup_mode = "semantic" if embedding_configured else "rule"
    if llm_missing:
        message = "LLM model configuration is required before writing."
    elif not embedding_configured:
        message = "Embedding model is not configured; rule deduplication may reduce quality."
    else:
        message = "Model configuration is ready."
    return {
        "llm_configured": not llm_missing,
        "embedding_configured": embedding_configured,
        "dedup_mode": dedup_mode,
        "requires_rule_dedup_confirmation": not embedding_configured,
        "missing_fields": sorted(set(llm_missing + embedding_missing)),
        "message": message,
        "applies_to": ["sync", "async"],
    }
