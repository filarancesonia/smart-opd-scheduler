"""Application settings, loaded from environment / .env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_name: str = "Smart OPD Scheduler"
    environment: str = "development"
    debug: bool = True
    api_prefix: str = "/api/v1"

    database_url: str = "sqlite:///./opd.db"

    # Instants are stored in UTC; rosters are wall-clock. This bridges them.
    hospital_timezone: str = "Asia/Kolkata"

    # Room 10 - Security & Privacy
    secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7
    field_encryption_key: str = "change-me-too"
    # scrypt cost as a power of two. 14 is the OWASP baseline; the test suite
    # lowers it so hashing does not dominate the run.
    scrypt_cost_log2: int = 14

    # Room 1 - device gateway
    device_api_key: str = "dev-device-key"
    # A presence signal older than this is no longer trusted as "live".
    presence_ttl_seconds: int = 300

    # Room 4 - where trained model artifacts live
    model_dir: str = "models"

    # Room 6 - notification providers
    sms_provider: str = "console"
    whatsapp_provider: str = "console"
    voice_provider: str = "console"

    # Room 9 - government integrations
    abha_base_url: str = "https://sandbox.abdm.gov.in"
    abha_client_id: str = ""
    abha_client_secret: str = ""
    ors_base_url: str = "https://sandbox.ors.gov.in"
    ors_api_key: str = ""
    hmis_fhir_base_url: str = ""
    hmis_fhir_token: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
