from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LLM via Hermes xAI OAuth proxy
    llm_base_url: str = "http://127.0.0.1:8645/v1"
    llm_api_key: str = "sol-right-local"
    # Dave conversation agent (reasoning)
    llm_model: str = "grok-4.20-0309-reasoning"
    # Solar Analyst parallel agent (same reasoning family; user-facing "Grok 4.2 Reasoning")
    solar_analyst_model: str = "grok-4.20-0309-reasoning"
    llm_timeout_s: float = 180.0

    # Google Maps Platform (Geocoding + Solar). Prefer GOOGLE_MAPS_API_KEY.
    google_maps_api_key: str = ""
    google_api_key: str = ""  # fallback alias

    # App
    host: str = "127.0.0.1"
    port: int = 8791
    db_path: str = str(ROOT / "data" / "leads.db")
    kb_path: str = str(ROOT / "app" / "kb" / "company.md")
    company_name: str = "SOL-RIGHT Solar"
    company_tagline: str = "Installed RIGHT!!"
    service_area: str = "Greater Chattanooga, TN and Greater Cleveland, TN"
    contact_phone: str = "(423) 555-0145"
    contact_email: str = "hello@sol-right.local"
    public_base_url: str = "http://107.221.94.155:8080"

    # Owner calendar / notifications
    owner_email: str = "shane.a.miller@live.com"
    calendar_owner_name: str = "Shane Miller"
    calendar_default_duration_minutes: int = 30
    calendar_timezone: str = "America/New_York"

    # Voice platform (outbound AI caller) — fire on explicit website consent
    voice_webhook_url: str = ""  # generic fallback webhook
    voice_webhook_secret: str = ""
    voice_from_number: str = ""
    # Retell AI (preferred)
    retell_api_key: str = ""
    retell_agent_id: str = ""  # e.g. agent_3f938b75a9e4c545737bff7db2
    retell_from_number: str = ""  # E.164 purchased/imported in Retell
    retell_api_base: str = "https://api.retellai.com"
    # Optional external DNC vendor endpoints (unused until set)
    national_dnc_api_url: str = ""
    tn_dnc_api_url: str = ""

    @property
    def maps_key(self) -> str:
        return (self.google_maps_api_key or self.google_api_key or "").strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()