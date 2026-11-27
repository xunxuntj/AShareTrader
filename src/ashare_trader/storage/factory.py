"""Storage backend factory selecting Supabase or local fallback automatically."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from ashare_trader.storage.base import StorageBackend
from ashare_trader.storage.local_store import LocalStorage
from ashare_trader.storage.supabase_store import SupabaseStorage


def create_storage(
    data_dir: str | Path = "data",
    force_local: bool = False,
) -> StorageBackend:
    """Return SupabaseStorage when credentials are present, else LocalStorage."""
    load_dotenv()
    if not force_local:
        url = os.getenv("SUPABASE_URL", "").strip()
        key = os.getenv("SUPABASE_KEY", "").strip()
        if url and key:
            return SupabaseStorage(url=url, key=key)
    return LocalStorage(data_dir=data_dir)
