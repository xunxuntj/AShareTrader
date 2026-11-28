"""Lightweight PBKDF2-SHA256 authentication and team user management service."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets

from ashare_trader.models import AccountState, UserAccount
from ashare_trader.storage.base import StorageBackend

PBKDF2_ITERATIONS = 120_000


def hash_password(password: str) -> str:
    """Hash a plaintext password using PBKDF2-HMAC-SHA256 with a random 16-byte salt."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a plaintext password against a stored PBKDF2 hash in constant time."""
    if not stored_hash or "$" not in stored_hash:
        return False
    parts = stored_hash.split("$")
    if len(parts) != 3 or parts[0] != "pbkdf2_sha256":
        return False
    _, salt, expected_hex = parts
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), PBKDF2_ITERATIONS)
    return hmac.compare_digest(dk.hex(), expected_hex)


class AuthService:
    """Manages team accounts (admin + members) backed by StorageBackend."""

    def __init__(self, storage: StorageBackend) -> None:
        self.storage = storage

    def ensure_default_admin(
        self,
        default_user_id: str = "admin",
        default_display_name: str = "管理员",
        default_password: str | None = None,
    ) -> UserAccount:
        """Create default admin account on first boot if app_users table is empty."""
        users = self.storage.load_users()
        if users:
            for u in users:
                if u.role == "admin":
                    return u
            return users[0]

        pwd = (default_password or os.getenv("APP_PASSWORD", "")).strip() or "admin123"
        admin = UserAccount(
            user_id=default_user_id,
            display_name=default_display_name,
            role="admin",
            password_hash=hash_password(pwd),
            is_active=True,
        )
        self.storage.upsert_user(admin)
        self.storage.save_account(AccountState(account_id=admin.user_id, initial_capital=100000.0, cash=100000.0))
        return admin

    def authenticate(self, user_id: str, password: str) -> UserAccount | None:
        """Authenticate an active user by user_id and password."""
        clean_id = user_id.strip().lower()
        if not clean_id or not password:
            return None
        user = self.storage.get_user(clean_id)
        if user is None or not user.is_active:
            return None
        if not verify_password(password, user.password_hash):
            return None
        return user

    def create_user(
        self,
        user_id: str,
        display_name: str,
        password: str,
        role: str = "user",
        initial_capital: float = 100000.0,
    ) -> UserAccount:
        """Create a new user account and initialize their personal ledger_account."""
        clean_id = user_id.strip().lower()
        if not clean_id or not clean_id.replace("_", "").replace("-", "").isalnum():
            raise ValueError("用户名仅支持字母、数字、下划线或短横线。")
        if len(password) < 4:
            raise ValueError("密码长度至少 4 位。")
        if self.storage.get_user(clean_id) is not None:
            raise ValueError(f"用户 '{clean_id}' 已存在。")

        user = UserAccount(
            user_id=clean_id,
            display_name=display_name.strip() or clean_id,
            role="admin" if role == "admin" else "user",
            password_hash=hash_password(password),
            is_active=True,
        )
        self.storage.upsert_user(user)
        cap = max(0.0, float(initial_capital))
        self.storage.save_account(AccountState(account_id=clean_id, initial_capital=cap, cash=cap))
        return user

    def reset_password(self, user_id: str, new_password: str) -> None:
        if len(new_password) < 4:
            raise ValueError("新密码长度至少 4 位。")
        user = self.storage.get_user(user_id.strip().lower())
        if user is None:
            raise ValueError(f"用户 '{user_id}' 不存在。")
        user.password_hash = hash_password(new_password)
        self.storage.upsert_user(user)

    def set_active(self, user_id: str, is_active: bool) -> None:
        user = self.storage.get_user(user_id.strip().lower())
        if user is None:
            raise ValueError(f"用户 '{user_id}' 不存在。")
        user.is_active = bool(is_active)
        self.storage.upsert_user(user)
