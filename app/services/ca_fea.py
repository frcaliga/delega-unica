"""Emissione dei certificati non qualificati per la FEA (Allegato 2, paragrafo 2.b.2).

Il certificato segue il profilo ETSI EN 319 412-1: il campo SERIALNUMBER del soggetto vale
"TINIT-" seguito dal codice fiscale del firmatario. Per ogni firma si genera una chiave nuova,
usata una sola volta e poi scartata: cosi' il firmatario ne mantiene il controllo esclusivo.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

DURATA_CERTIFICATO = timedelta(days=60)


@dataclass(frozen=True)
class Autorita:
    chiave: rsa.RSAPrivateKey
    certificato: x509.Certificate


def crea_autorita(denominazione: str, anni: int = 10) -> Autorita:
    """Autorita' di certificazione interna (non qualificata) per i certificati FEA."""
    chiave = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    nome = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "IT"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, denominazione),
            x509.NameAttribute(NameOID.COMMON_NAME, f"{denominazione} CA FEA"),
        ]
    )
    ora = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(nome)
        .issuer_name(nome)
        .public_key(chiave.public_key())
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
        .sign(chiave, hashes.SHA256())
    )
    return Autorita(chiave, cert)


def emetti_certificato(
    autorita: Autorita,
    codice_fiscale: str,
    cognome: str,
    nome: str,
    durata: timedelta = DURATA_CERTIFICATO,
    estensioni: list[x509.ExtensionType] | None = None,
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
    ora = datetime.now(UTC)
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
    return chiave, builder.sign(autorita.chiave, hashes.SHA256())
