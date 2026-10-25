"""Firma e verifica di buste CAdES Baseline-B (CMS SignedData con contenuto incluso).

L'Allegato 2 richiede il formato CAdES Baseline-B senza ulteriore codifica base64. Con la FEA a
certificato non qualificato la busta del cliente e' a sua volta firmata dall'intermediario: la
busta esterna contiene come dati la busta interna.

La verifica qui e' un controllo preventivo (integrita', firmatario, date, validita' temporale del
certificato). La validazione completa della catena di fiducia la esegue l'Agenzia delle Entrate.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from asn1crypto import cms, core, tsp  # noqa: F401  (tsp registra signing_certificate_v2)
from asn1crypto import x509 as ax509
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

# QcStatement id-etsi-qcs-QcCompliance (0.4.0.1862.1.1), codificato DER
_QC_COMPLIANCE = bytes.fromhex("060604008e460101")
_QC_STATEMENTS_OID = "1.3.6.1.5.5.7.1.3"
_HASHES = {"sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}


class BustaNonValida(Exception):
    pass


# --- Firma ---------------------------------------------------------------------------------


def firma(
    contenuto: bytes,
    chiave: rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey,
    certificato: x509.Certificate,
    catena: list[x509.Certificate] | None = None,
    ora: datetime | None = None,
) -> bytes:
    """Busta CAdES-B (DER) con contenuto incluso, SHA-256 e attributo signing-certificate-v2."""
    cert = ax509.Certificate.load(certificato.public_bytes(serialization.Encoding.DER))
    ora = ora or datetime.now(UTC)
    attributi = cms.CMSAttributes(
        [
            cms.CMSAttribute({"type": "content_type", "values": ["data"]}),
            cms.CMSAttribute(
                {"type": "signing_time", "values": [cms.Time({"utc_time": core.UTCTime(ora)})]}
            ),
            cms.CMSAttribute(
                {"type": "message_digest", "values": [hashlib.sha256(contenuto).digest()]}
            ),
            cms.CMSAttribute(
                {
                    "type": "signing_certificate_v2",
                    "values": [
                        {
                            "certs": [
                                {
                                    "hash_algorithm": {"algorithm": "sha256"},
                                    "cert_hash": hashlib.sha256(cert.dump()).digest(),
                                    "issuer_serial": {
                                        "issuer": [
                                            ax509.GeneralName(
                                                name="directory_name", value=cert.issuer
                                            )
                                        ],
                                        "serial_number": cert.serial_number,
                                    },
                                }
                            ]
                        }
                    ],
                }
            ),
        ]
    )
    da_firmare = attributi.dump()
    if isinstance(chiave, rsa.RSAPrivateKey):
        firma_bytes = chiave.sign(da_firmare, padding.PKCS1v15(), hashes.SHA256())
        algoritmo = "sha256_rsa"
    else:
        firma_bytes = chiave.sign(da_firmare, ec.ECDSA(hashes.SHA256()))
        algoritmo = "sha256_ecdsa"

    signer_info = cms.SignerInfo(
        {
            "version": "v1",
            "sid": cms.SignerIdentifier(
                name="issuer_and_serial_number",
                value={"issuer": cert.issuer, "serial_number": cert.serial_number},
            ),
            "digest_algorithm": {"algorithm": "sha256"},
            "signed_attrs": attributi,
            "signature_algorithm": {"algorithm": algoritmo},
            "signature": firma_bytes,
        }
    )
    certificati = [cert] + [
        ax509.Certificate.load(c.public_bytes(serialization.Encoding.DER)) for c in catena or []
    ]
    signed_data = cms.SignedData(
        {
            "version": "v1",
            "digest_algorithms": [{"algorithm": "sha256"}],
            "encap_content_info": {"content_type": "data", "content": contenuto},
            "certificates": certificati,
            "signer_infos": [signer_info],
        }
    )
    return cms.ContentInfo({"content_type": "signed_data", "content": signed_data}).dump()


# --- Verifica ------------------------------------------------------------------------------


@dataclass
class Firmatario:
    codice_fiscale: str | None
    nome_comune: str | None
    emittente: str
    qualificato: bool
    ora_firma: datetime | None
    valido_dal: datetime
    valido_al: datetime


@dataclass
class Livello:
    """Una busta CMS; le buste annidate compaiono in ordine dall'esterno verso l'interno."""

    firmatari: list[Firmatario]


@dataclass
class EsitoVerifica:
    livelli: list[Livello] = field(default_factory=list)
    contenuto: bytes = b""


def _codice_fiscale(subject: ax509.Name) -> str | None:
    serial = subject.native.get("serial_number")
    if isinstance(serial, str) and serial.upper().startswith("TINIT-"):
        return serial[6:].upper()
    return None


def _qualificato(cert: ax509.Certificate) -> bool:
    for ext in cert["tbs_certificate"]["extensions"] or []:
        if ext["extn_id"].dotted == _QC_STATEMENTS_OID:
            return _QC_COMPLIANCE in ext["extn_value"].contents
    return False


def _trova_certificato(signed_data: cms.SignedData, sid: cms.SignerIdentifier) -> ax509.Certificate:
    for scelta in signed_data["certificates"] or []:
        if scelta.name != "certificate":
            continue
        cert = scelta.chosen
        if sid.name == "issuer_and_serial_number":
            if (
                cert.issuer == sid.chosen["issuer"]
                and cert.serial_number == sid.chosen["serial_number"].native
            ):
                return cert
        elif cert.key_identifier == sid.chosen.native:
            return cert
    raise BustaNonValida("Il certificato del firmatario non e' incluso nella busta")


def _verifica_firma(cert: ax509.Certificate, signer_info: cms.SignerInfo, dati: bytes) -> None:
    chiave = x509.load_der_x509_certificate(cert.dump()).public_key()
    digest = signer_info["digest_algorithm"]["algorithm"].native
    alg = _HASHES.get(digest)
    if alg is None:
        raise BustaNonValida(f"Algoritmo di hash non supportato: {digest}")
    firma_bytes = signer_info["signature"].native
    sig_alg = signer_info["signature_algorithm"]
    try:
        if isinstance(chiave, rsa.RSAPublicKey):
            if sig_alg.signature_algo == "rsassa_pss":
                params = sig_alg["parameters"]
                pad = padding.PSS(
                    mgf=padding.MGF1(
                        _HASHES[params["mask_gen_algorithm"]["parameters"]["algorithm"].native]()
                    ),
                    salt_length=params["salt_length"].native,
                )
                chiave.verify(firma_bytes, dati, pad, alg())
            else:
                chiave.verify(firma_bytes, dati, padding.PKCS1v15(), alg())
        elif isinstance(chiave, ec.EllipticCurvePublicKey):
            chiave.verify(firma_bytes, dati, ec.ECDSA(alg()))
        else:
            raise BustaNonValida("Tipo di chiave non supportato")
    except InvalidSignature as exc:
        raise BustaNonValida("La firma non corrisponde: il file e' stato modificato") from exc


def _verifica_signer(
    signed_data: cms.SignedData, si: cms.SignerInfo, contenuto: bytes
) -> Firmatario:
    cert = _trova_certificato(signed_data, si["sid"])
    attributi = si["signed_attrs"]
    if not attributi:
        raise BustaNonValida("Mancano gli attributi firmati richiesti dal formato CAdES")
    valori = {a["type"].native: a["values"] for a in attributi}
    if "signing_certificate_v2" not in valori and "signing_certificate" not in valori:
        raise BustaNonValida("Manca l'attributo signing-certificate richiesto da CAdES")

    digest = si["digest_algorithm"]["algorithm"].native
    atteso = hashlib.new(digest, contenuto).digest()
    if valori.get("message_digest") is None or valori["message_digest"][0].native != atteso:
        raise BustaNonValida(
            "L'impronta del contenuto non corrisponde: il file e' stato modificato"
        )

    # La firma copre gli attributi codificati come SET OF, non con il tag implicito [0]
    dati = b"\x31" + attributi.dump(force=False)[1:]
    _verifica_firma(cert, si, dati)

    ora = valori["signing_time"][0].native if "signing_time" in valori else None
    validita = cert["tbs_certificate"]["validity"]
    return Firmatario(
        codice_fiscale=_codice_fiscale(cert.subject),
        nome_comune=cert.subject.native.get("common_name"),
        emittente=cert.issuer.human_friendly,
        qualificato=_qualificato(cert),
        ora_firma=ora,
        valido_dal=validita["not_before"].native,
        valido_al=validita["not_after"].native,
    )


def verifica(busta: bytes, max_livelli: int = 3) -> EsitoVerifica:
    """Verifica le buste annidate e restituisce firmatari e contenuto finale (l'XML)."""
    if busta[:2] == b"MI" or busta.startswith(b"-----"):
        raise BustaNonValida("Il file e' codificato in base64: serve il .p7m binario (DER)")
    esito = EsitoVerifica()
    dati = busta
    for _ in range(max_livelli):
        try:
            info = cms.ContentInfo.load(dati)
            tipo = info["content_type"].native
        except Exception as exc:  # noqa: BLE001 - qualunque errore di parsing ASN.1
            if esito.livelli:
                break
            raise BustaNonValida("Il file non e' una busta CAdES (.p7m)") from exc
        if tipo != "signed_data":
            if esito.livelli:
                break
            raise BustaNonValida("Il file non e' una busta CAdES (.p7m)")
        signed_data = info["content"]
        contenuto = signed_data["encap_content_info"]["content"].native
        if contenuto is None:
            raise BustaNonValida("La firma e' staccata: il contenuto deve essere incluso")
        firmatari = [
            _verifica_signer(signed_data, si, contenuto) for si in signed_data["signer_infos"]
        ]
        esito.livelli.append(Livello(firmatari))
        dati = contenuto
    esito.contenuto = dati
    return esito
