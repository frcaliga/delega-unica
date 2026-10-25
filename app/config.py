from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Delega Unica AdE"
    database_url: str = "postgresql+psycopg://delega:delega@localhost:5432/delega"
    secret_key: str = "dev-only-secret-key-change-me-0123456789"
    access_token_minutes: int = 30
    mfa_token_minutes: int = 5
    # Se attivo, gli operatori devono completare il secondo fattore (TOTP)
    # prima di poter usare le API applicative.
    require_mfa: bool = True
    totp_issuer: str = "Delega Unica"


@lru_cache
def get_settings() -> Settings:
    return Settings()
