"""Servizio FEA erogato dallo studio: dichiarazione di accettazione e firma della delega."""

import hashlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from cryptography import x509

from app.config import get_settings
from app.models import AdesioneFea, Delega, Tenant
from app.services import ca_fea, cades

VERSIONE_CONDIZIONI = "1.0"

# Testo da far validare dal legale dello studio prima dell'uso (DPCM 22/2/2013, art. 57).
CONDIZIONI = """\
DICHIARAZIONE DI ACCETTAZIONE DEL SERVIZIO DI FIRMA ELETTRONICA AVANZATA
Versione condizioni: {versione}

Il/la sottoscritto/a {cognome} {nome}, codice fiscale {codice_fiscale},
identificato/a di persona da {operatore} presso {studio} (codice fiscale {studio_cf})
mediante {documento} n. {documento_numero} rilasciato da {documento_rilasciato_da},
con scadenza {documento_scadenza},

DICHIARA

1. di aver ricevuto e letto le condizioni del servizio di firma elettronica avanzata (FEA)
   offerto da {studio} ai sensi degli articoli 55-57 del DPCM 22 febbraio 2013;
2. di sapere che la FEA vale solo nei rapporti con {studio} e per i documenti relativi alla
   delega unica ai servizi online dell'Agenzia delle Entrate e dell'Agenzia delle
   Entrate-Riscossione;
3. che per ogni firma riceve un codice monouso via SMS al numero {cellulare}, di cui ha l'uso
   esclusivo, e che con quel codice viene generato un certificato intestato al proprio codice
   fiscale, usato per una sola firma;
4. di sapere che {studio} conserva per almeno vent'anni copia del documento d'identita',
   la presente dichiarazione e le evidenze di ogni firma, e che puo' ottenerne copia gratuita
   su richiesta;
5. di poter revocare in qualsiasi momento l'adesione al servizio.

Data: {data}
Conferma con codice OTP inviato al numero {cellulare}.
"""

NOMI_DOCUMENTO = {"1": "carta d'identita'", "2": "passaporto", "3": "patente", "4": "documento"}


def dichiarazione(adesione: AdesioneFea, studio: Tenant, operatore: str, ora: datetime) -> bytes:
    return CONDIZIONI.format(
        versione=VERSIONE_CONDIZIONI,
        cognome=adesione.cognome,
        nome=adesione.nome,
        codice_fiscale=adesione.codice_fiscale,
        operatore=operatore,
        studio=studio.name,
        studio_cf=studio.codice_fiscale,
        documento=NOMI_DOCUMENTO[adesione.documento_tipo.value],
        documento_numero=adesione.documento_numero,
        documento_rilasciato_da=adesione.documento_rilasciato_da,
        documento_scadenza=f"{adesione.documento_scadenza:%d/%m/%Y}",
        cellulare=adesione.cellulare,
        data=f"{ora:%d/%m/%Y %H:%M}",
    ).encode()


def sha256(dati: bytes) -> str:
    return hashlib.sha256(dati).hexdigest()


@lru_cache
def get_autorita() -> ca_fea.Autorita:
    s = get_settings()
    if s.fea_ca_kms_key_id:
        import boto3

        pem = (
            s.fea_ca_cert_pem.encode()
            if s.fea_ca_cert_pem
            else Path(s.fea_ca_cert_path).read_bytes()
        )
        client = boto3.client("kms", region_name=s.aws_region)
        return ca_fea.carica_autorita_kms(pem, client, s.fea_ca_kms_key_id)
    return ca_fea.carica_autorita(
        Path(s.fea_ca_cert_path), Path(s.fea_ca_key_path), s.fea_ca_key_password
    )


def firma_delega(
    autorita: ca_fea.Autorita, delega: Delega, ora: datetime
) -> tuple[bytes, x509.Certificate]:
    """Busta CAdES del cliente. La chiave privata vive solo per questa firma."""
    chiave, certificato = ca_fea.emetti_certificato(
        autorita,
        delega.sottoscrittore_cf,
        delega.sottoscrittore_cognome,
        delega.sottoscrittore_nome,
        dal=ora,
    )
    assert delega.xml_generato is not None
    busta = cades.firma(delega.xml_generato, chiave, certificato, [autorita.certificato], ora)
    return busta, certificato
