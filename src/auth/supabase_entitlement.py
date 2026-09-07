from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from typing import Dict, Optional

import requests
from loguru import logger


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except Exception:
        return default


def extract_bearer_token(auth_header: Optional[str]) -> str:
    if not auth_header:
        return ""
    parts = str(auth_header).strip().split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return ""


@dataclass
class SupabaseIdentity:
    user_id: str
    email: str
    created_at: Optional[str] = None


class SupabaseEntitlementService:
    """Supabase-backed identity validation.

    The subscription/payment/points stack has been removed; this service
    only validates an access token and returns the user identity.
    """

    def __init__(self):
        self.enabled = _env_bool("POLYWEATHER_AUTH_ENABLED", False)
        self.supabase_url = str(os.getenv("SUPABASE_URL") or "").strip().rstrip("/")
        self.anon_key = str(os.getenv("SUPABASE_ANON_KEY") or "").strip()
        self.timeout_sec = max(3, _env_int("SUPABASE_HTTP_TIMEOUT_SEC", 8))
        self.cache_ttl_sec = max(5, _env_int("SUPABASE_AUTH_CACHE_TTL_SEC", 30))
        self.cache_max_entries = max(16, _env_int("SUPABASE_AUTH_CACHE_MAX_ENTRIES", 1024))
        self._identity_cache: Dict[str, Dict[str, object]] = {}
        self._identity_cache_lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.anon_key)

    def _user_endpoint(self) -> str:
        return f"{self.supabase_url}/auth/v1/user"

    def _request_headers_for_user(self, access_token: str) -> Dict[str, str]:
        return {
            "apikey": self.anon_key,
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        }

    def _cache_identity(self, access_token: str, identity: Optional[SupabaseIdentity], ts: float) -> None:
        with self._identity_cache_lock:
            self._identity_cache[access_token] = {"identity": identity, "ts": ts}
            if len(self._identity_cache) <= self.cache_max_entries:
                return
            # Evict expired entries first, then the oldest, to keep the
            # cache bounded even under token-spam (each miss would
            # otherwise pin raw bearer material in memory forever).
            now_ts = time.time()
            expired = [
                key
                for key, value in self._identity_cache.items()
                if now_ts - float(value.get("ts") or 0) >= self.cache_ttl_sec
            ]
            for key in expired:
                del self._identity_cache[key]
            while len(self._identity_cache) > self.cache_max_entries:
                oldest_key = min(
                    self._identity_cache,
                    key=lambda key: float(self._identity_cache[key].get("ts") or 0),
                )
                del self._identity_cache[oldest_key]

    def get_identity(self, access_token: str) -> Optional[SupabaseIdentity]:
        if not access_token:
            return None

        now_ts = time.time()
        with self._identity_cache_lock:
            cached = self._identity_cache.get(access_token)
            if cached and now_ts - float(cached.get("ts") or 0) < self.cache_ttl_sec:
                identity = cached.get("identity")
                return identity if isinstance(identity, SupabaseIdentity) else None

        if not self.configured:
            return None

        try:
            response = requests.get(
                self._user_endpoint(),
                headers=self._request_headers_for_user(access_token),
                timeout=self.timeout_sec,
            )
            if response.status_code != 200:
                if response.status_code in {401, 403}:
                    self._cache_identity(access_token, None, now_ts)
                return None
            data = response.json() if response.content else {}
            user_id = str(data.get("id") or "").strip()
            if not user_id:
                self._cache_identity(access_token, None, now_ts)
                return None

            identity = SupabaseIdentity(
                user_id=user_id,
                email=str(data.get("email") or "").strip(),
                created_at=str(data.get("created_at") or "").strip() or None,
            )
            self._cache_identity(access_token, identity, now_ts)
            return identity
        except Exception as exc:
            logger.warning(f"supabase auth user check failed: {exc}")
            return None


SUPABASE_ENTITLEMENT = SupabaseEntitlementService()
