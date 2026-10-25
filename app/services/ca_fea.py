"""Emissione dei certificati non qualificati per la FEA (Allegato 2, paragrafo 2.b.2).

Il certificato segue il profilo ETSI EN 319 412-1: il campo SERIALNUMBER del soggetto vale
"TINIT-" seguito dal codice fiscale del firmatario. Per ogni firma si genera una chiave nuova,
usata una sola volta e poi scartata: cosi' il firmatario ne mantiene il controllo esclusivo.

La chiave dell'autorita' puo' stare in un file cifrato (sviluppo) oppure in AWS KMS (produzione),
dove non esce mai dal modulo hardware: la piattaforma invia l'impronta e riceve la firma.
"""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import cache, cached_property
from pathlib import Path
from typing import Any, Protocol

from asn1crypto import x509 as ax509
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DURATA_CERTIFICATO = timedelta(days=60)


class ChiaveAutorita(Protocol):
    def chiave_pubblica(self) -> rsa.RSAPublicKey: ...

    def firma_sha256(self, impronta: bytes) -> bytes:
        """Firma RSA PKCS#1 v1.5 di un'impronta SHA-256."""
        ...


@dataclass(frozen=True)
class ChiaveLocale:
    privata: rsa.RSAPrivateKey

    def chiave_pubblica(self) -> rsa.RSAPublicKey:
        return self.privata.public_key()

    def firma_sha256(self, impronta: bytes) -> bytes:
        return self.privata.sign(impronta, padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))


class ChiaveKms:
    """Chiave asimmetrica AWS KMS (RSA_4096, SIGN_VERIFY)."""

    def __init__(self, client: Any, key_id: str):
        self.client = client
        self.key_id = key_id

    @cached_property
    def _pubblica(self) -> rsa.RSAPublicKey:
        der = self.client.get_public_key(KeyId=self.key_id)["PublicKey"]
        chiave = serialization.load_der_public_key(der)
        if not isinstance(chiave, rsa.RSAPublicKey):
            raise ValueError("La chiave KMS dell'autorita' FEA deve essere RSA")
        return chiave

    def chiave_pubblica(self) -> rsa.RSAPublicKey:
        return self._pubblica

    def firma_sha256(self, impronta: bytes) -> bytes:
        risposta = self.client.sign(
            KeyId=self.key_id,
            Message=impronta,
            MessageType="DIGEST",
            SigningAlgorithm="RSASSA_PKCS1_V1_5_SHA_256",
        )
        return risposta["Signature"]


@dataclass(frozen=True)
class Autorita:
    chiave: ChiaveAutorita
    certificato: x509.Certificate


@cache
def _chiave_provvisoria() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _firma(builder: x509.CertificateBuilder, chiave: ChiaveAutorita) -> x509.Certificate:
    """La libreria firma solo con chiavi in memoria: si costruisce il certificato con una chiave
    provvisoria, che fissa l'algoritmo sha256WithRSAEncryption, e se ne sostituisce la firma con
    quella della chiave dell'autorita'."""
    tbs = builder.sign(_chiave_provvisoria(), hashes.SHA256()).tbs_certificate_bytes
    firma = chiave.firma_sha256(hashlib.sha256(tbs).digest())
    chiave.chiave_pubblica().verify(firma, tbs, padding.PKCS1v15(), hashes.SHA256())
    certificato = ax509.Certificate(
        {
            "tbs_certificate": ax509.TbsCertificate.load(tbs),
            "signature_algorithm": {"algorithm": "sha256_rsa"},
            "signature_value": firma,
        }
    )
    return x509.load_der_x509_certificate(certificato.dump())


def crea_autorita(
    denominazione: str,
    anni: int = 10,
    dal: datetime | None = None,
    chiave: ChiaveAutorita | None = None,
) -> Autorita:
    """Autorita' di certificazione interna (non qualificata) per i certificati FEA. Senza chiave
    esterna ne genera una locale."""
    if chiave is None:
        chiave = ChiaveLocale(rsa.generate_private_key(public_exponent=65537, key_size=4096))
    nome = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, denominazione),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{denominazione} CA FEA"),
        ]
    )
    ora = dal or datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.chiave_pubblica())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ora - timedelta(minutes=5))
        .not_valid_after(ora + timedelta(days=365 * anni))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    )
    return Autorita(chiave, _firma(cert, chiave))


def emetti_certificato(
    autorita: Autorita,
    codice_fiscale: str,
    cognome: str,
    nome: str,
    durata: timedelta = DURATA_CERTIFICATO,
    estensioni: list[x509.ExtensionType] | None = None,
    dal: datetime | None = None,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """Nuova chiave e certificato del firmatario. La durata deve coprire l'invio all'AdE:
    i certificati devono essere validi alla data di ricezione del file."""
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    soggetto = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            x509.NameAttribute(NameOID.SURNAME, cognome.upper()),
            x509.NameAttribute(NameOID.GIVEN_NAME, nome.upper()),
            x509.NameAttribute(NameOID.SERIAL_NUMBER, f"TINIT-{codice_fiscale.upper()}"),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{cognome} {nome}".upper()),
        ]
    )
    ora = dal or datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(soggetto)
        .issuer_name(autorita.certificato.subject)
        .public_key(chiave.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(ora - timedelta(minutes=5))
        .not_valid_after(ora + durata)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=True,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.EMAIL_PROTECTION]), critical=False
        )
    )
    for estensione in estensioni or []:
        builder = builder.add_extension(estensione, critical=False)
    return chiave, _firma(builder, autorita.chiave)


def salva_autorita(autorita: Autorita, percorso_cert: Path, percorso_chiave: Path, password: str):
    """Scrive certificato (PEM) e chiave privata (PKCS#8 cifrata) di un'autorita' locale."""
    if not isinstance(autorita.chiave, ChiaveLocale):
        raise ValueError("Solo la chiave di un'autorita' locale si puo' salvare su file")
    percorso_cert.parent.mkdir(parents=True, exist_ok=True)
    percorso_chiave.parent.mkdir(parents=True, exist_ok=True)
    percorso_cert.write_bytes(autorita.certificato.public_bytes(serialization.Encoding.PEM))
    chiave = autorita.chiave.privata.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password.encode()),
    )
    percorso_chiave.touch(mode=0o600)
    percorso_chiave.write_bytes(chiave)


def carica_autorita(percorso_cert: Path, percorso_chiave: Path, password: str | None) -> Autorita:
    certificato = x509.load_pem_x509_certificate(percorso_cert.read_bytes())
    chiave = serialization.load_pem_private_key(
        percorso_chiave.read_bytes(), password.encode() if password else None
    )
    if not isinstance(chiave, rsa.RSAPrivateKey):
        raise ValueError("La chiave dell'autorita' FEA deve essere RSA")
    return _verifica_coppia(ChiaveLocale(chiave), certificato)


def carica_autorita_kms(certificato_pem: bytes, client: Any, key_id: str) -> Autorita:
    return _verifica_coppia(
        ChiaveKms(client, key_id), x509.load_pem_x509_certificate(certificato_pem)
    )


def _verifica_coppia(chiave: ChiaveAutorita, certificato: x509.Certificate) -> Autorita:
    pubblica = certificato.public_key()
    if (
        not isinstance(pubblica, rsa.RSAPublicKey)
        or chiave.chiave_pubblica().public_numbers() != pubblica.public_numbers()
    ):
        raise ValueError("La chiave non corrisponde al certificato dell'autorita' FEA")
    return Autorita(chiave, certificato)
