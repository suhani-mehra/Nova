"""
core/config.py
Centralised settings loaded from .env via pydantic-settings.
Usage anywhere in the app:
    from core.config import settings
    print(settings.fabric_server)
"""

from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Azure AD ──────────────────────────────────────────────────────────────
    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str
    azure_authority: str

    @property
    def azure_jwks_uri(self) -> str:
        return (
            f"https://login.microsoftonline.com/"
            f"{self.azure_tenant_id}/discovery/v2.0/keys"
        )

    @property
    def azure_issuer(self) -> str:
        return (
            f"https://login.microsoftonline.com/"
            f"{self.azure_tenant_id}/v2.0"
        )

    # ── OpenAI ────────────────────────────────────────────────────────────────
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"
    openai_recommendation_cache_hours: int = 24

    # ── Microsoft Fabric ──────────────────────────────────────────────────────
    fabric_server: str
    fabric_database: str
    fabric_driver: str = "/opt/homebrew/lib/libmsodbcsql.18.dylib"

    # ── Nova App ──────────────────────────────────────────────────────────────
    nova_env: str = "development"
    nova_secret_key: str
    nova_cors_origins: List[str] = ["http://localhost:5500"]

    @property
    def is_dev(self) -> bool:
        return self.nova_env == "development"

    # ── Tier Thresholds ───────────────────────────────────────────────────────
    tier_platinum_pct: int = 3
    tier_diamond_pct: int = 10
    tier_gold_pct: int = 20
    tier_silver_pct: int = 40
    tier_bronze_pct: int = 60

    @property
    def tier_thresholds(self) -> dict:
        """
        Returns ordered dict from most exclusive to least.
        Used by tier_service to classify a user's percentile rank.
        """
        return {
            "platinum": self.tier_platinum_pct,
            "diamond":  self.tier_diamond_pct,
            "gold":     self.tier_gold_pct,
            "silver":   self.tier_silver_pct,
            "bronze":   self.tier_bronze_pct,
            # below bronze → starter (handled as fallback)
        }

    # ── Streak & Scoring ─────────────────────────────────────────────────────
    streak_min_seconds_per_day: int = 1800   # 30 minutes
    ai_proficiency_min_score: int = 60       # out of 100


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """
    Cached singleton — import this everywhere instead of
    instantiating Settings() directly. The cache means .env
    is only read once per process.
    """
    return Settings()


# Convenience alias used throughout the app:
#   from core.config import settings
settings = get_settings()
