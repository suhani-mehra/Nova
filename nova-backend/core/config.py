"""
core/config.py
Centralised settings loaded from .env via pydantic-settings.
Usage anywhere in the app:
    from core.config import settings
    print(settings.database_url)
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

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str = "sqlite:///./nova.db"

    # ── Classmate Data ────────────────────────────────────────────────────────
    classmate_data_path: str = "./data/classmate"

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

    # ── CSV file names (relative to classmate_data_path) ─────────────────────
    @property
    def csv_files(self) -> dict:
        """
        Canonical mapping of table name → CSV filename.
        Used by data/loader.py to know what to ingest.
        """
        return {
            "dim_user":               "classmate_dim_classmate_user_in_.csv",
            "dim_employee_profile":   "classmate_dim_classmate_employee_profile_in_.csv",
            "dim_second_level_cat":   "classmate_dim_classmate_second_level_category_in_.csv",
            "dim_topic":              "classmate_dim_classmate_topic_in_.csv",
            "dim_content_mapping":    "classmate_dim_classmate_content_mapping_in_.csv",
            "dim_certificate":        "classmate_dim_classmate_certificate_in_.csv",
            "dim_training":           "classmate_dim_classmate_training_in_.csv",
            "vw_trainings":           "classmate_vw_classmate_trainings_in_.csv",
            "vw_certification":       "classmate_vw_classmate_certification_in_.csv",
            "fact_user_skill_status": "classmate_fact_classmate_user_skill_status_in_.csv",
            "fact_learning_credit":   "classmate_fact_classmate_learning_credit_in_.csv",
            "fact_self_study":        "classmate_fact_classmate_self_study_in_.csv",
            "fact_certification":     "classmate_fact_classmate_certification_in_.csv",
            "fact_training_nom":      "classmate_fact_classmate_training_nomination_in_.csv",
            "mv_quarterly_credits":   "classmate_mv_employee_year_quarter_credits_in_.csv",
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
