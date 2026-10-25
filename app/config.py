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
    # Codice fiscale del produttore del software, riportato nel file XML se valorizzato
    identificativo_prod_software: str | None = None

    # Autorita' interna che emette i certificati monouso della FEA (python -m app.cli crea-ca-fea).
    # In produzione la chiave va custodita in un HSM o in un KMS.
    fea_ca_cert_path: str = "var/ca-fea/ca.pem"
    fea_ca_key_path: str = "var/ca-fea/ca.key"
    fea_ca_key_password: str | None = None
    # Invio dell'OTP: "console" scrive il codice nel log (solo sviluppo)
    sms_provider: str = "console"
    otp_minuti: int = 5
    otp_tentativi: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()
