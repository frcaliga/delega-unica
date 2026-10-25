"""Invio degli SMS con i codici OTP. Il fornitore si sceglie con SMS_PROVIDER."""

import logging
from functools import lru_cache
from typing import Any, Protocol

from app.config import get_settings

log = logging.getLogger(__name__)


class InvioSms(Protocol):
    def invia(self, numero: str, testo: str) -> None: ...


class SmsConsole:
    """Solo sviluppo: scrive il messaggio nel log invece di inviarlo."""

    def invia(self, numero: str, testo: str) -> None:
        log.warning("SMS a %s: %s", numero, testo)


class SmsAws:
    """AWS SNS: invio diretto al numero, come SMS transazionale."""

    def __init__(self, client: Any, mittente: str):
        self.client = client
        self.mittente = mittente

    def invia(self, numero: str, testo: str) -> None:
        self.client.publish(
            PhoneNumber=e164(numero),
            Message=testo,
            MessageAttributes={
                "AWS.SNS.SMS.SMSType": {"DataType": "String", "StringValue": "Transactional"},
                "AWS.SNS.SMS.SenderID": {"DataType": "String", "StringValue": self.mittente},
            },
        )


@lru_cache
def get_sms() -> InvioSms:
    s = get_settings()
    if s.sms_provider == "console":
        return SmsConsole()
    if s.sms_provider == "sns":
        import boto3

        return SmsAws(boto3.client("sns", region_name=s.aws_region), s.sms_mittente)
    raise RuntimeError(f"Fornitore SMS non supportato: {s.sms_provider}")


def e164(numero: str) -> str:
    """Numero in formato internazionale: senza prefisso si assume l'Italia."""
    if numero.startswith("+"):
        return numero
    if numero.startswith("00"):
        return f"+{numero[2:]}"
    return f"+39{numero}"


def maschera(numero: str) -> str:
    return f"{numero[:3]}{'*' * (len(numero) - 6)}{numero[-3:]}"
