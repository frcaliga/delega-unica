"""Regole della delega unica (provv. 2 ottobre 2024 e Allegato 2 alle specifiche tecniche).

Le funzioni restituiscono l'elenco degli errori invece di sollevare eccezioni, cosi' l'operatore
li vede tutti insieme prima di generare il file XML.
"""

import calendar
import enum
from dataclasses import dataclass
from datetime import date, timedelta

from app.domain import codice_fiscale

# Prima data ammessa per firme e date contenute nel file XML
AVVIO_DELEGA_UNICA = date(2025, 12, 8)
MESI_VALIDITA_FIRMA = 48
GIORNI_FINESTRA_RINNOVO = 90
MAX_DELEGHE_FORNITURA = 300


class TipoRichiesta(enum.StrEnum):
    CONFERIMENTO = "1"
    REVOCA = "2"
    RINNOVO = "3"


class Servizio(enum.StrEnum):
    CASSETTO_FISCALE = "01"
    FATTURE_CONSULTAZIONE = "02"
    DATI_IVA = "03"
    INDIRIZZO_TELEMATICO = "04"
    FATTURAZIONE_CONSERVAZIONE = "05"
    DISPOSITIVI = "06"
    ISA_CPB = "07"
    AGENZIA_RISCOSSIONE = "08"


DESCRIZIONE_SERVIZI = {
    Servizio.CASSETTO_FISCALE: "Consultazione del cassetto fiscale delegato",
    Servizio.FATTURE_CONSULTAZIONE: "Consultazione e acquisizione delle fatture elettroniche "
    "o dei loro duplicati informatici",
    Servizio.DATI_IVA: "Consultazione dei dati rilevanti ai fini IVA",
    Servizio.INDIRIZZO_TELEMATICO: "Registrazione dell'indirizzo telematico",
    Servizio.FATTURAZIONE_CONSERVAZIONE: "Fatturazione elettronica e conservazione delle "
    "fatture elettroniche",
    Servizio.DISPOSITIVI: "Accreditamento e censimento dispositivi",
    Servizio.ISA_CPB: "Acquisizione dei dati ISA e dei dati per la proposta di concordato "
    "preventivo biennale",
    Servizio.AGENZIA_RISCOSSIONE: "Servizi online dell'area riservata dell'Agenzia delle "
    "entrate-Riscossione",
}


class Qualifica(enum.StrEnum):
    DELEGANTE = "1"
    TUTORE_GENITORE = "2"
    EREDE = "3"
    RAPPRESENTANTE = "4"
    CURATORE_FALLIMENTARE = "5"


class TipoDocumento(enum.StrEnum):
    CARTA_IDENTITA = "1"
    PASSAPORTO = "2"
    PATENTE = "3"
    ALTRO = "4"


class ModalitaFirma(enum.StrEnum):
    FIRMA_DIGITALE = "firma_digitale"
    FEA_CIE = "fea_cie"
    # FEA con certificato non qualificato: serve anche la firma digitale dell'intermediario
    FEA_NON_QUALIFICATA = "fea_non_qualificata"
    # Servizio web dell'intermediario in convenzione con l'AdE (Appendice C)
    CONVENZIONE_ADE = "convenzione_ade"


# Firme ammesse per titolari di partita IVA attiva e soggetti diversi dalle persone fisiche
FIRME_SOGGETTI_IVA = {ModalitaFirma.FIRMA_DIGITALE, ModalitaFirma.FEA_CIE}


def scadenza(data_conferimento: date) -> date:
    """La delega scade il 31 dicembre del quarto anno successivo a quello di conferimento."""
    return date(data_conferimento.year + 4, 12, 31)


def scadenza_dopo_rinnovo(scadenza_attuale: date) -> date:
    """Il rinnovo decorre dal 1 gennaio dell'anno successivo alla scadenza in corso."""
    return scadenza(date(scadenza_attuale.year + 1, 1, 1))


def inizio_finestra_rinnovo(scadenza_attuale: date) -> date:
    return scadenza_attuale - timedelta(days=GIORNI_FINESTRA_RINNOVO)


def rinnovo_consentito(scadenza_attuale: date, oggi: date) -> bool:
    return inizio_finestra_rinnovo(scadenza_attuale) <= oggi <= scadenza_attuale


def _sottrai_mesi(d: date, mesi: int) -> date:
    anno, mese0 = divmod(d.year * 12 + d.month - 1 - mesi, 12)
    giorno = min(d.day, calendar.monthrange(anno, mese0 + 1)[1])
    return date(anno, mese0 + 1, giorno)


def errori_data_firma(data_firma: date, oggi: date) -> list[str]:
    errori = []
    if data_firma < AVVIO_DELEGA_UNICA:
        errori.append("La data di firma non puo' essere anteriore all'8 dicembre 2025")
    if data_firma > oggi:
        errori.append("La data di firma non puo' essere futura")
    if data_firma < _sottrai_mesi(oggi, MESI_VALIDITA_FIRMA):
        errori.append("La data di firma non puo' essere anteriore di oltre 48 mesi")
    return errori


@dataclass(frozen=True)
class DatiDelegante:
    codice_fiscale: str
    persona_fisica: bool
    partita_iva_attiva: bool


@dataclass(frozen=True)
class DatiSottoscrittore:
    codice_fiscale: str
    qualifica: Qualifica


@dataclass(frozen=True)
class DatiDocumento:
    tipo: TipoDocumento
    rilasciato_da: str
    numero: str
    scadenza: date
    tipo_altro: str | None = None


def errori_servizi(tipo: TipoRichiesta, servizi: list[str]) -> list[str]:
    if tipo != TipoRichiesta.CONFERIMENTO:
        return ["Revoca e rinnovo non indicano servizi"] if servizi else []
    if not servizi:
        return ["Indicare almeno un servizio da delegare"]
    errori = []
    validi = {s.value for s in Servizio}
    if sconosciuti := sorted(set(servizi) - validi):
        errori.append(f"Servizi non previsti: {', '.join(sconosciuti)}")
    if len(set(servizi)) != len(servizi):
        errori.append("Servizi ripetuti")
    return errori


def errori_sottoscrittore(delegante: DatiDelegante, sott: DatiSottoscrittore) -> list[str]:
    errori = []
    if not codice_fiscale.valida(sott.codice_fiscale) or not codice_fiscale.is_persona_fisica(
        sott.codice_fiscale
    ):
        errori.append("Il codice fiscale del sottoscrittore deve essere di una persona fisica")
    if sott.qualifica == Qualifica.DELEGANTE:
        if not delegante.persona_fisica:
            errori.append("Per un soggetto diverso da persona fisica firma il rappresentante")
        elif sott.codice_fiscale != delegante.codice_fiscale:
            errori.append("Con qualifica 1 il sottoscrittore e' il delegante stesso")
    elif sott.codice_fiscale == delegante.codice_fiscale:
        errori.append("Il sottoscrittore coincide con il delegante: usare la qualifica 1")
    if not delegante.persona_fisica and sott.qualifica != Qualifica.RAPPRESENTANTE:
        errori.append("Per un soggetto diverso da persona fisica la qualifica e' 4")
    if sott.qualifica == Qualifica.CURATORE_FALLIMENTARE and not (
        delegante.persona_fisica and delegante.partita_iva_attiva
    ):
        errori.append(
            "Il curatore fallimentare firma solo per una ditta individuale con P.IVA attiva"
        )
    return errori


def errori_firma(
    delegante: DatiDelegante, modalita: ModalitaFirma, documento: DatiDocumento | None
) -> list[str]:
    errori = []
    soggetto_iva = delegante.partita_iva_attiva or not delegante.persona_fisica
    if soggetto_iva and modalita not in FIRME_SOGGETTI_IVA:
        errori.append(
            "Titolari di P.IVA attiva e soggetti diversi da persone fisiche firmano solo con "
            "firma digitale o FEA CIE"
        )
    if modalita == ModalitaFirma.FEA_NON_QUALIFICATA and documento is None:
        errori.append("Con la FEA non qualificata servono gli estremi del documento d'identita'")
    if documento and documento.tipo == TipoDocumento.ALTRO and not documento.tipo_altro:
        errori.append("Specificare il tipo di documento")
    return errori
