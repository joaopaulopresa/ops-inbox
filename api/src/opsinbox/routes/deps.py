"""Shared dependencies of the HTTP routes."""

from functools import lru_cache
from pathlib import Path

from opsinbox.llm import ModelClient
from opsinbox.settings import get_settings
from opsinbox.storage import ObjectStore, get_object_store
from opsinbox.workflow.service import model_client


def get_store() -> ObjectStore:
    """The object store."""
    return get_object_store()


@lru_cache
def get_model_client() -> ModelClient | None:
    """The cached model client (used for reply previews only)."""
    settings = get_settings()
    return model_client(settings, Path(settings.model_cache_dir))
