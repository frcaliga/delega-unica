"""Cifratura delle copie dei documenti d'identita' (cifratura a busta, AES-256-GCM).

Ogni copia ha una propria chiave dati casuale. La chiave dati e' salvata cifrata con la chiave
principale:

- produzione: AWS KMS (`DOCUMENTI_KMS_KEY_ID`), che non esporta mai la chiave principale;
- altrove: una chiave AES di 32 byte in base64 (`DOCUMENTI_CHIAVE`), oppure, solo in sviluppo,
  derivata da `SECRET_KEY`.

Il contesto (studio e id del documento) e' legato al testo cifrato: una copia spostata su un'altra
riga o su un altro studio non si decifra.
"""

import base64
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Protocol

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings


class ErroreCifratura(Exception):
    pass


def _aad(contesto: dict[str, str]) -> bytes:
    return "&".join(f"{k}={contesto[k]}" for k in sorted(contesto)).encode()


@dataclass
class Busta:
    schema: str  # "kms" o "locale"
    chiave_cifrata: bytes
    dati: bytes  # nonce (12 byte) + testo cifrato con il tag GCM


class ChiavePrincipale(Protocol):
    schema: str

    def nuova_chiave(self, contesto: dict[str, str]) -> tuple[bytes, bytes]:
        """Restituisce (chiave dati in chiaro, chiave dati cifrata)."""
        ...

    def apri_chiave(self, cifrata: bytes, contesto: dict[str, str]) -> bytes: ...


class ChiaveLocale:
    schema = "locale"

    def __init__(self, chiave: bytes):
        if len(chiave) != 32:
            raise ErroreCifratura("La chiave dei documenti deve essere di 32 byte")
        self._aes = AESGCM(chiave)

    def nuova_chiave(self, contesto: dict[str, str]) -> tuple[bytes, bytes]:
        chiave = AESGCM.generate_key(bit_length=256)
        nonce = os.urandom(12)
        return chiave, nonce + self._aes.encrypt(nonce, chiave, _aad(contesto))

    def apri_chiave(self, cifrata: bytes, contesto: dict[str, str]) -> bytes:
        try:
            return self._aes.decrypt(cifrata[:12], cifrata[12:], _aad(contesto))
        except Exception as exc:
            raise ErroreCifratura("Chiave del documento non decifrabile") from exc


class ChiaveKms:
    schema = "kms"

    def __init__(self, client, key_id: str):
        self._client = client
        self._key_id = key_id

    def nuova_chiave(self, contesto: dict[str, str]) -> tuple[bytes, bytes]:
        r = self._client.generate_data_key(
            KeyId=self._key_id, KeySpec="AES_256", EncryptionContext=contesto
        )
        return r["Plaintext"], r["CiphertextBlob"]

    def apri_chiave(self, cifrata: bytes, contesto: dict[str, str]) -> bytes:
        try:
            r = self._client.decrypt(
                KeyId=self._key_id, CiphertextBlob=cifrata, EncryptionContext=contesto
            )
        except Exception as exc:
            raise ErroreCifratura("Chiave del documento non decifrabile") from exc
        return r["Plaintext"]


def cifra(principale: ChiavePrincipale, dati: bytes, contesto: dict[str, str]) -> Busta:
    chiave, cifrata = principale.nuova_chiave(contesto)
    nonce = os.urandom(12)
    testo = AESGCM(chiave).encrypt(nonce, dati, _aad(contesto))
    return Busta(principale.schema, cifrata, nonce + testo)


class Portachiavi:
    """La chiave con cui cifrare le nuove copie e quelle per leggere le copie gia' salvate.

    Passando da una chiave locale a KMS le copie esistenti restano leggibili.
    """

    def __init__(self, *chiavi: ChiavePrincipale):
        self.attiva = chiavi[0]
        self._per_schema = {c.schema: c for c in reversed(chiavi)}

    def cifra(self, dati: bytes, contesto: dict[str, str]) -> Busta:
        return cifra(self.attiva, dati, contesto)

    def decifra(self, busta: Busta, contesto: dict[str, str]) -> bytes:
        principale = self._per_schema.get(busta.schema)
        if principale is None:
            raise ErroreCifratura(f"Chiave {busta.schema} non configurata")
        return decifra(principale, busta, contesto)


def decifra(principale: ChiavePrincipale, busta: Busta, contesto: dict[str, str]) -> bytes:
    if busta.schema != principale.schema:
        raise ErroreCifratura(
            f"Documento cifrato con la chiave {busta.schema}, configurata {principale.schema}"
        )
    chiave = principale.apri_chiave(busta.chiave_cifrata, contesto)
    try:
        return AESGCM(chiave).decrypt(busta.dati[:12], busta.dati[12:], _aad(contesto))
    except Exception as exc:
        raise ErroreCifratura("Documento alterato o chiave errata") from exc


def _chiave_locale() -> ChiaveLocale:
    s = get_settings()
    if s.documenti_chiave:
        return ChiaveLocale(base64.b64decode(s.documenti_chiave))
    # Solo sviluppo: chiave derivata dal segreto dell'applicazione
    derivata = HKDF(
        algorithm=hashes.SHA256(), length=32, salt=None, info=b"documenti-identita"
    ).derive(s.secret_key.encode())
    return ChiaveLocale(derivata)


@lru_cache
def get_portachiavi() -> Portachiavi:
    s = get_settings()
    if s.documenti_kms_key_id:
        import boto3

        client = boto3.client("kms", region_name=s.aws_region)
        return Portachiavi(ChiaveKms(client, s.documenti_kms_key_id), _chiave_locale())
    return Portachiavi(_chiave_locale())
