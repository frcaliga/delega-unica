"""Generazione dei file XML della delega unica secondo l'Allegato 2 alle specifiche tecniche.

- delega singola (Appendice A, codice fornitura DEL24): il file che il contribuente firma in CAdES;
- fornitura per l'invio massivo (Appendice B): fino a 300 deleghe firmate, allegate in base64.
"""

import base64
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path

from lxml import etree

from app.domain.regole import MAX_DELEGHE_FORNITURA, ModalitaFirma, TipoRichiesta
from app.domain.testo import normalizza_an
from app.models import Delega, Tenant

SPECIFICHE = Path(__file__).resolve().parent.parent / "specifiche"
NS_DELEGA = "urn:www.agenziaentrate.gov.it:specificheTecniche:sfe:del:v1"
NS_FORNITURA = "urn:www.agenziaentrate.gov.it:specificheTecniche:sco:del:v1"
NS_TELEMATICO = "www.agenziaentrate.gov.it:specificheTecniche:telent:v1"
CODICE_FORNITURA = "DEL24"


class XmlNonValido(Exception):
    def __init__(self, errori: list[str]):
        super().__init__("; ".join(errori))
        self.errori = errori


@cache
def _schema(nome: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(SPECIFICHE / nome)))


def _valida(root: etree._Element, schema: str) -> None:
    xsd = _schema(schema)
    if not xsd.validate(root):
        raise XmlNonValido([f"riga {e.line}: {e.message}" for e in xsd.error_log])


def data_ggmmaaaa(d: date) -> str:
    return d.strftime("%d%m%Y")


def _sub(parent: etree._Element, ns: str, tag: str, text: str | None = None) -> etree._Element:
    el = etree.SubElement(parent, f"{{{ns}}}{tag}")
    if text is not None:
        el.text = text
    return el


def _anagrafica(
    parent: etree._Element,
    ns: str,
    codice_fiscale: str,
    cognome: str | None,
    nome: str | None,
    denominazione: str | None,
) -> None:
    _sub(parent, ns, "CodiceFiscale", codice_fiscale)
    if len(codice_fiscale) == 16:
        pf = _sub(parent, ns, "PersoneFisiche")
        _sub(pf, ns, "Cognome", normalizza_an(cognome or ""))
        _sub(pf, ns, "Nome", normalizza_an(nome or ""))
    else:
        pnf = _sub(parent, ns, "SoggettiDiversiDaPF")
        _sub(pnf, ns, "Denominazione", normalizza_an(denominazione or ""))


def costruisci_delega(
    delega: Delega, tenant: Tenant, identificativo_software: str | None = None
) -> bytes:
    """XML della singola delega, validato sull'XSD ufficiale (delega_v1_1.xsd)."""
    ns = NS_DELEGA
    root = etree.Element(f"{{{ns}}}Deleghe", nsmap={"del": ns})
    intestazione = _sub(root, ns, "Intestazione")
    _sub(intestazione, ns, "CodiceFornitura", CODICE_FORNITURA)
    if identificativo_software:
        _sub(intestazione, ns, "IdentificativoProdSoftware", identificativo_software)

    dati = _sub(root, ns, "DatiDelega")
    cliente = delega.cliente
    _anagrafica(
        _sub(dati, ns, "SoggettoDelegante"),
        ns,
        cliente.codice_fiscale,
        cliente.cognome,
        cliente.nome,
        cliente.denominazione,
    )
    _anagrafica(
        _sub(dati, ns, "SoggettoDelegato"),
        ns,
        tenant.codice_fiscale,
        tenant.cognome,
        tenant.nome,
        tenant.name,
    )

    sott = _sub(dati, ns, "Sottoscrittore")
    _sub(sott, ns, "CodiceFiscale", delega.sottoscrittore_cf)
    _sub(sott, ns, "Cognome", normalizza_an(delega.sottoscrittore_cognome))
    _sub(sott, ns, "Nome", normalizza_an(delega.sottoscrittore_nome))
    _sub(sott, ns, "Qualifica", delega.qualifica.value)
    # Gli estremi del documento vanno nel file solo con la FEA non qualificata
    if delega.modalita_firma == ModalitaFirma.FEA_NON_QUALIFICATA and delega.ha_documento:
        doc = _sub(sott, ns, "DatiDocumento")
        _sub(doc, ns, "TipoDocumento", delega.documento_tipo.value)
        _sub(doc, ns, "RilasciatoDa", normalizza_an(delega.documento_rilasciato_da or ""))
        _sub(doc, ns, "NumeroDocumento", normalizza_an(delega.documento_numero or ""))
        _sub(doc, ns, "DataScadenzaDocumento", data_ggmmaaaa(delega.documento_scadenza))
        if delega.documento_tipo_altro:
            _sub(doc, ns, "TipoAltroDocumento", normalizza_an(delega.documento_tipo_altro))

    richiesta = _sub(dati, ns, "Richiesta")
    match delega.tipo_richiesta:
        case TipoRichiesta.CONFERIMENTO:
            ins = _sub(richiesta, ns, "InserimentoDelega")
            _sub(ins, ns, "TipoRichiesta", TipoRichiesta.CONFERIMENTO.value)
            # Le specifiche elencano i servizi come 01..08, ma il tipo DatoNU_Type dell'XSD
            # non ammette zeri iniziali: nel file vanno scritti come 1..8.
            for codice in sorted(delega.servizi):
                _sub(_sub(ins, ns, "Servizi"), ns, "TipoServizio", str(int(codice)))
        case TipoRichiesta.REVOCA:
            _sub(_sub(richiesta, ns, "RevocaDelega"), ns, "TipoRichiesta", "2")
        case TipoRichiesta.RINNOVO:
            _sub(_sub(richiesta, ns, "RinnovoDelega"), ns, "TipoRichiesta", "3")

    firma = _sub(dati, ns, "Firma")
    _sub(firma, ns, "Data", data_ggmmaaaa(delega.data_firma))
    _sub(firma, ns, "Firma", "1")

    _valida(root, "delega_v1_1.xsd")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)


@dataclass(frozen=True)
class AllegatoFirmato:
    codice_fiscale_delegante: str
    nome_file: str
    contenuto_p7m: bytes


def costruisci_fornitura(
    tenant: Tenant,
    allegati: list[AllegatoFirmato],
    *,
    codice_fiscale_fornitore: str | None = None,
    identificativo_software: str | None = None,
    email: str | None = None,
    cellulare: str | None = None,
) -> bytes:
    """File per l'invio massivo (fornitureDEL24_v1.xsd), da controllare e inviare con
    Desktop Telematico o Entratel Multifile. Il richiedente e' l'intermediario delegato."""
    if not 1 <= len(allegati) <= MAX_DELEGHE_FORNITURA:
        raise XmlNonValido([f"Una fornitura contiene da 1 a {MAX_DELEGHE_FORNITURA} deleghe"])

    tm, ns = NS_TELEMATICO, NS_FORNITURA
    root = etree.Element(f"{{{tm}}}Messaggio", nsmap={"tm": tm, "del": ns})
    intestazione = _sub(root, tm, "Intestazione")
    _sub(
        intestazione,
        tm,
        "CodiceFiscaleFornitore",
        codice_fiscale_fornitore or tenant.codice_fiscale,
    )
    contenuto = _sub(root, tm, "Contenuto")
    contenuto.set("CodiceFornitura", CODICE_FORNITURA)

    fornitura = _sub(contenuto, ns, "Fornitura")
    _sub(_sub(fornitura, ns, "Intestazione"), ns, "TipoFornitore", "10")
    documento = _sub(fornitura, ns, "Documento")
    documento.set("identificativo", "00001")

    frontespizio = _sub(documento, ns, "Frontespizio")
    if identificativo_software:
        _sub(frontespizio, ns, "IdentificativoProdSoftware", identificativo_software)
    _sub(frontespizio, ns, "DichiarazioneAssunzioneResponsabilita", "1")
    richiedente = _sub(frontespizio, ns, "Richiedente")
    _anagrafica(richiedente, ns, tenant.codice_fiscale, tenant.cognome, tenant.nome, tenant.name)
    if cellulare:
        _sub(richiedente, ns, "Cellulare", cellulare)
    if email:
        _sub(richiedente, ns, "Email", email)
    _sub(frontespizio, ns, "NumeroDeleghe", str(len(allegati)))

    deleghe = _sub(documento, ns, "Deleghe")
    for progressivo, allegato in enumerate(allegati, start=1):
        delegante = _sub(deleghe, ns, "Delegante")
        _sub(delegante, ns, "ProgressivoDelega", str(progressivo))
        _sub(delegante, ns, "CodiceFiscaleDelegante", allegato.codice_fiscale_delegante)
        file_el = _sub(delegante, ns, "Allegato")
        _sub(file_el, ns, "FileType", "application/pkcs7-mime")
        _sub(file_el, ns, "FileName", allegato.nome_file)
        _sub(file_el, ns, "ImageData", base64.b64encode(allegato.contenuto_p7m).decode())

    _valida(root, "fornituraDEL24_v1.xsd")
    return etree.tostring(root, xml_declaration=True, encoding="UTF-8", pretty_print=True)
