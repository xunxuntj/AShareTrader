"""Storage backends (Supabase PostgreSQL + local SQLite/Parquet fallback)."""

from ashare_trader.storage.base import StorageBackend
from ashare_trader.storage.factory import create_storage

__all__ = ["StorageBackend", "create_storage"]
