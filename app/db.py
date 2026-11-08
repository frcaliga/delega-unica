import json
import threading
from collections.abc import Callable, Iterator
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


class PasswordDaSegreto:
    """Password del database letta dal segreto che RDS gestisce e ruota da solo.

    Resta in memoria finche' una connessione nuova non viene rifiutata: allora si rilegge.
    """

    def __init__(self, client: Any, segreto: str, iniziale: str | None = None):
        self._client = client
        self._segreto = segreto
        self._password = iniziale
        self._lock = threading.Lock()

    def __call__(self, aggiorna: bool = False) -> str:
        with self._lock:
            if aggiorna or self._password is None:
                valore = self._client.get_secret_value(SecretId=self._segreto)["SecretString"]
                self._password = json.loads(valore)["password"]
            return self._password


def usa_password_ruotata(engine: Engine, password: Callable[..., str]) -> None:
    """Ogni connessione nuova usa la password corrente; se e' rifiutata, riprova una volta."""

    @event.listens_for(engine, "do_connect")
    def _connetti(dialect, conn_rec, cargs, cparams):
        cparams["password"] = password()
        try:
            return dialect.connect(*cargs, **cparams)
        except dialect.loaded_dbapi.OperationalError:
            cparams["password"] = password(aggiorna=True)
            return dialect.connect(*cargs, **cparams)


def _crea_engine() -> Engine:
    s = get_settings()
    engine = create_engine(s.database_url, pool_pre_ping=True)
    if s.db_password_secret_arn:
        import boto3

        client = boto3.client("secretsmanager", region_name=s.aws_region)
        usa_password_ruotata(
            engine, PasswordDaSegreto(client, s.db_password_secret_arn, s.db_password)
        )
    return engine


engine = _crea_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session
