"""
core/config.py
Centralised settings loaded from .env via pydantic-settings.
Usage anywhere in the app:
    from core.config import settings
    print(settings.api_endpoint_url)
"""

from functools import lru_cache
from typing import List
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",   # tolerate stale/unknown keys in .env
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

    # ── Azure OpenAI ──────────────────────────────────────────────────────────
    openai_api_key: str
    openai_model: str = "gpt-4o-mini"          # Azure deployment name
    openai_api_version: str = "2024-08-01-preview"
    azure_openai_endpoint: str                  # e.g. https://your-resource.openai.azure.com/
    openai_recommendation_cache_hours: int = 24

    # ── Classmate table-dump API (replaces the old Fabric SQL connection) ─────
    # POST {tableName, pageNumber, pageSize} → whole-table pages.
    # Auth: APIM subscription key header + OAuth client-credentials Bearer token.
    api_endpoint_url: str = ""
    api_subscription_key: str = ""
    api_tenant_id: str = ""
    api_client_id: str = ""
    api_client_secret: str = ""
    api_scope: str = ""
    api_page_size: int = 50000

    # ── Local warehouse (synced copy of the API tables) ──────────────────────
    # Relative paths resolve against nova_backend/ (the uvicorn cwd).
    warehouse_db_path: str = "nova_warehouse.db"

    # ── Nova App ──────────────────────────────────────────────────────────────
    nova_env: str = "development"
    nova_secret_key: str
    nova_cors_origins: List[str] = ["http://localhost:5500"]
    nova_dev_bypass: bool = False  # set NOVA_DEV_BYPASS=true in .env to skip JWT auth

    @property
    def is_dev(self) -> bool:
        return self.nova_env == "development"

    @property
    def azure_configured(self) -> bool:
        """
        True when real Azure AD JWT validation should run.
        Set NOVA_DEV_BYPASS=true in .env to use the Fabric dev user
        even when a real tenant ID is present (e.g. needed for Fabric).
        """
        if self.nova_dev_bypass:
            return False
        return self.azure_tenant_id.lower() != "placeholder"

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
    ai_proficiency_min_score: int = 30       # out of 100
    monthly_credit_target: float = 100.0     # month-to-date credits that map credits_score to 100

    # ── AI proficiency levels (Overview "by region" bar chart) ───────────────
    # Cumulative ("at least") thresholds on the normalised 0–100 AI axis score.
    # "professional" == ai_proficiency_min_score (the same definition used for the
    # company-wide AI-proficient metric everywhere else). Someone at a higher
    # level is also counted in every lower level. Edit here to retune.
    ai_proficiency_levels: dict = {
        "professional": 30,
        "specialist":   45,
        "expert":       55,
        "champion":     65,
    }
    # Per-level coverage goal = % of the workforce the company wants at each level.
    # Rendered as the dashed goal line above each bar. Edit here to retune.
    ai_proficiency_level_goals: dict = {
        "professional": 80,
        "specialist":   50,
        "expert":       35,
        "champion":     20,
    }


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
