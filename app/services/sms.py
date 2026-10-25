"""Invio degli SMS con i codici OTP. Il fornitore si sceglie con SMS_PROVIDER."""

import logging
from functools import lru_cache
from typing import Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class InvioSms(Protocol):
    def invia(self, numero: str, testo: str) -> None: ...


class SmsConsole:
    """Solo sviluppo: scrive il messaggio nel log invece di inviarlo."""

    def invia(self, numero: str, testo: str) -> None:
        log.warning("SMS a %s: %s", numero, testo)


@lru_cache
def get_sms() -> InvioSms:
    provider = get_settings().sms_provider
    if provider == "console":
        return SmsConsole()
    raise RuntimeError(f"Fornitore SMS non supportato: {provider}")


def maschera(numero: str) -> str:
    return f"{numero[:3]}{'*' * (len(numero) - 6)}{numero[-3:]}"
