from datetime import datetime

from lxml import etree

from app.clock import ROMA
from app.services import cades
from tests.helpers import LAURA, MARIO
from tests.test_deleghe_api import (  # noqa: F401 - fixture riusate
    _conferimento,
    _oggi,
    carica,
    firma_e_carica,
    firma_fea,
    headers,
    mario,
)

ORA = datetime(2026, 10, 25, 10, tzinfo=ROMA)


def _nuova(client, headers, mario, **kw):
    d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"], **kw))
    assert d.status_code == 201, d.text
    xml = client.get(f"/deleghe/{d.json()['id']}/xml", headers=headers).content
    return d.json()["id"], xml


def _errori(res):
    assert res.status_code == 422, res.text
    return " ".join(res.json()["detail"]["errori"])


def test_fea_senza_firma_dello_studio(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario)
    busta = cades.firma(xml, *pki["mario_fea"], ora=ORA)
    assert "firmata anche dallo studio" in _errori(carica(client, headers, delega_id, busta))


def test_certificato_di_un_altro_codice_fiscale(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario)
    interna = cades.firma(xml, *pki["studio"], ora=ORA)
    busta = cades.firma(interna, *pki["studio"], ora=ORA)
    assert "altro codice fiscale" in _errori(carica(client, headers, delega_id, busta))


def test_firma_esterna_non_qualificata(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario)
    interna = cades.firma(xml, *pki["mario_fea"], ora=ORA)
    busta = cades.firma(interna, *pki["mario_fea"], ora=ORA)
    assert "firma digitale" in _errori(carica(client, headers, delega_id, busta))


def test_contenuto_diverso_dalla_delega(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario)
    alterato = xml.replace(b"ROSSI", b"VERDI")
    errori = _errori(carica(client, headers, delega_id, firma_fea(pki, alterato)))
    assert "non contiene l'XML generato" in errori


def test_firma_precedente_alla_data_della_delega(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario, data_firma="2026-10-25")
    ieri = datetime(2026, 10, 24, 10, tzinfo=ROMA)
    interna = cades.firma(xml, *pki["mario_fea"], ora=ieri)
    busta = cades.firma(interna, *pki["studio"], ora=ORA)
    assert "successiva alla firma del cliente" in _errori(carica(client, headers, delega_id, busta))


def test_firma_digitale_del_cliente(client, headers, mario, pki):
    delega_id, xml = _nuova(client, headers, mario, modalita_firma="firma_digitale")
    non_qualificata = cades.firma(xml, *pki["mario_fea"], ora=ORA)
    assert "non e' qualificato" in _errori(carica(client, headers, delega_id, non_qualificata))
    busta = cades.firma(xml, *pki["mario_qualificato"], ora=ORA)
    res = carica(client, headers, delega_id, busta)
    assert res.status_code == 200, res.text
    assert res.json()["firmatari"][0]["firmatari"][0]["codice_fiscale"] == MARIO


def test_file_corrotto(client, headers, mario):
    delega_id, _ = _nuova(client, headers, mario)
    assert "CAdES" in _errori(carica(client, headers, delega_id, b"non e' un p7m"))


def test_fornitura_massiva(client, headers, mario, pki):
    ids = []
    for _ in range(2):
        d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
        firma_e_carica(client, headers, pki, d["id"])
        ids.append(d["id"])
    res = client.post("/forniture", headers=headers, json={"delega_ids": ids})
    assert res.status_code == 200, res.text
    root = etree.fromstring(res.content)
    ns = {"f": "urn:www.agenziaentrate.gov.it:specificheTecniche:sco:del:v1"}
    assert root.findtext(".//f:NumeroDeleghe", namespaces=ns) == "2"
    assert root.findtext(".//f:Richiedente/f:CodiceFiscale", namespaces=ns)
    assert LAURA  # firmatario esterno di test


def test_fornitura_con_delega_non_firmata(client, headers, mario):
    d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    res = client.post("/forniture", headers=headers, json={"delega_ids": [d["id"]]})
    assert "senza file firmato" in _errori(res)
