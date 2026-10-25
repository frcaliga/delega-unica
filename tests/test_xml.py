from datetime import date

import pytest
from lxml import etree

from app.domain.regole import ModalitaFirma, Qualifica, TipoDocumento, TipoRichiesta
from app.models import Cliente, Delega, Tenant
from app.services.xml_delega import (
    NS_DELEGA,
    AllegatoFirmato,
    XmlNonValido,
    costruisci_delega,
    costruisci_fornitura,
)
from tests.helpers import LAURA, MARIO, SOCIETA, STUDIO

NS = {"d": NS_DELEGA}


def _tenant(cf=STUDIO) -> Tenant:
    return Tenant(
        name="Studio Bianchi & Associati", codice_fiscale=cf, cognome="Bianchi", nome="Ugo"
    )


def _delega(cliente: Cliente, **kw) -> Delega:
    valori = dict(
        cliente=cliente,
        tipo_richiesta=TipoRichiesta.CONFERIMENTO,
        servizi=["08", "01"],
        sottoscrittore_cf=cliente.codice_fiscale,
        sottoscrittore_cognome=cliente.cognome or "",
        sottoscrittore_nome=cliente.nome or "",
        qualifica=Qualifica.DELEGANTE,
        modalita_firma=ModalitaFirma.FIRMA_DIGITALE,
        data_firma=date(2026, 10, 20),
    )
    valori.update(kw)
    return Delega(**valori)


MARIO_CLIENTE = Cliente(codice_fiscale=MARIO, cognome="Rossi", nome="Mario Niccolò")


def test_conferimento_persona_fisica():
    xml = etree.fromstring(costruisci_delega(_delega(MARIO_CLIENTE), _tenant(), "SOFTWARE01"))
    assert xml.findtext("d:Intestazione/d:CodiceFornitura", namespaces=NS) == "DEL24"
    assert (
        xml.findtext("d:DatiDelega/d:SoggettoDelegante/d:PersoneFisiche/d:Nome", namespaces=NS)
        == "MARIO NICCOLÒ"
    )
    assert (
        xml.findtext(
            "d:DatiDelega/d:SoggettoDelegato/d:SoggettiDiversiDaPF/d:Denominazione", namespaces=NS
        )
        == "STUDIO BIANCHI & ASSOCIATI"
    )
    servizi = xml.findall(".//d:InserimentoDelega/d:Servizi/d:TipoServizio", namespaces=NS)
    assert [s.text for s in servizi] == ["1", "8"]
    assert xml.findtext(".//d:Firma/d:Data", namespaces=NS) == "20102026"
    assert xml.find(".//d:DatiDocumento", namespaces=NS) is None


def test_intermediario_persona_fisica():
    tenant = _tenant(cf=LAURA)
    xml = etree.fromstring(costruisci_delega(_delega(MARIO_CLIENTE), tenant))
    assert (
        xml.findtext(".//d:SoggettoDelegato/d:PersoneFisiche/d:Cognome", namespaces=NS) == "BIANCHI"
    )


def test_fea_non_qualificata_include_documento():
    delega = _delega(
        MARIO_CLIENTE,
        modalita_firma=ModalitaFirma.FEA_NON_QUALIFICATA,
        documento_tipo=TipoDocumento.ALTRO,
        documento_tipo_altro="Porto d'armi",
        documento_rilasciato_da="Questura di Roma",
        documento_numero="12345",
        documento_scadenza=date(2031, 2, 28),
    )
    xml = etree.fromstring(costruisci_delega(delega, _tenant()))
    doc = xml.find(".//d:DatiDocumento", namespaces=NS)
    assert doc.findtext("d:TipoDocumento", namespaces=NS) == "4"
    assert doc.findtext("d:DataScadenzaDocumento", namespaces=NS) == "28022031"
    assert doc.findtext("d:TipoAltroDocumento", namespaces=NS) == "PORTO D'ARMI"


def test_societa_con_rappresentante_e_revoca():
    societa = Cliente(codice_fiscale=SOCIETA, denominazione="Rossi Srl")
    delega = _delega(
        societa,
        tipo_richiesta=TipoRichiesta.REVOCA,
        servizi=[],
        sottoscrittore_cf=LAURA,
        sottoscrittore_cognome="Bruni",
        sottoscrittore_nome="Laura",
        qualifica=Qualifica.RAPPRESENTANTE,
    )
    xml = etree.fromstring(costruisci_delega(delega, _tenant()))
    assert xml.findtext(".//d:RevocaDelega/d:TipoRichiesta", namespaces=NS) == "2"
    assert xml.findtext(".//d:Sottoscrittore/d:Qualifica", namespaces=NS) == "4"


def test_rinnovo():
    xml = etree.fromstring(
        costruisci_delega(_delega(MARIO_CLIENTE, tipo_richiesta=TipoRichiesta.RINNOVO), _tenant())
    )
    assert xml.findtext(".//d:RinnovoDelega/d:TipoRichiesta", namespaces=NS) == "3"
    assert xml.find(".//d:Servizi", namespaces=NS) is None


def test_dati_non_validi_sono_rifiutati_dallo_schema():
    cliente = Cliente(codice_fiscale="XYZ", cognome="Rossi", nome="Mario")
    with pytest.raises(XmlNonValido):
        costruisci_delega(_delega(cliente), _tenant())


def test_fornitura_massiva():
    allegati = [
        AllegatoFirmato(MARIO, "delega_mario.xml.p7m", b"\x30\x80firmata"),
        AllegatoFirmato(LAURA, "delega_laura.xml.p7m", b"\x30\x80firmata2"),
    ]
    xml = etree.fromstring(costruisci_fornitura(_tenant(), allegati, email="studio@example.it"))
    ns = {
        "tm": "www.agenziaentrate.gov.it:specificheTecniche:telent:v1",
        "f": "urn:www.agenziaentrate.gov.it:specificheTecniche:sco:del:v1",
    }
    assert xml.find("tm:Contenuto", namespaces=ns).get("CodiceFornitura") == "DEL24"
    assert xml.findtext(".//f:NumeroDeleghe", namespaces=ns) == "2"
    progressivi = xml.findall(".//f:Delegante/f:ProgressivoDelega", namespaces=ns)
    assert [p.text for p in progressivi] == ["1", "2"]


def test_fornitura_oltre_300_deleghe():
    allegati = [AllegatoFirmato(MARIO, f"d{i}.p7m", b"x") for i in range(301)]
    with pytest.raises(XmlNonValido):
        costruisci_fornitura(_tenant(), allegati)
