from functools import lru_cache
from typing import Self

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Delega Unica AdE"
    database_url: str = "postgresql+psycopg://delega:delega@localhost:5432/delega"
    # In alternativa a DATABASE_URL, i singoli parametri (su AWS la password arriva dal segreto
    # che RDS gestisce e ruota da solo)
    db_host: str | None = None
    db_port: int = 5432
    db_name: str = "delega"
    db_user: str = "delega"
    db_password: str | None = None
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
    # Produzione: chiave in AWS KMS e certificato PEM passato come variabile d'ambiente
    fea_ca_kms_key_id: str | None = None
    fea_ca_cert_pem: str | None = None
    aws_region: str = "eu-south-1"
    # Invio dell'OTP: "console" scrive il codice nel log (solo sviluppo), "sns" usa AWS SNS
    sms_provider: str = "console"
    sms_mittente: str = "DelegaUnica"
    otp_minuti: int = 5
    otp_tentativi: int = 5

    @model_validator(mode="after")
    def _database_url(self) -> Self:
        if self.db_host:
            self.database_url = URL.create(
                "postgresql+psycopg",
                username=self.db_user,
                password=self.db_password,
                host=self.db_host,
                port=self.db_port,
                database=self.db_name,
                query={"sslmode": "require"},
            ).render_as_string(hide_password=False)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
