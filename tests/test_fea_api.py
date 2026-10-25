import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app import clock
from app.clock import ROMA
from app.main import app
from app.services import cades, fea, sms
from tests.helpers import MARIO
from tests.test_deleghe_api import (  # noqa: F401 - fixture riusate
    DOCUMENTO,
    OGGI,
    _conferimento,
    _oggi,
    carica,
    headers,
    mario,
)

CELLULARE = "+393331234567"


class SmsFinti:
    def __init__(self):
        self.inviati: list[tuple[str, str]] = []

    def invia(self, numero: str, testo: str) -> None:
        self.inviati.append((numero, testo))

    def ultimo_codice(self) -> str:
        return re.search(r"codice (\d{6})", self.inviati[-1][1]).group(1)


@pytest.fixture
def sms_finti(pki):
    finti = SmsFinti()
    app.dependency_overrides[sms.get_sms] = lambda: finti
    app.dependency_overrides[fea.get_autorita] = lambda: pki["ca"]
    yield finti
    app.dependency_overrides.clear()


def _adesione(client, headers, cliente_id, **kw):
    dati = {
        "documento_tipo": DOCUMENTO["tipo"],
        "documento_numero": DOCUMENTO["numero"],
        "documento_rilasciato_da": DOCUMENTO["rilasciato_da"],
        "documento_scadenza": DOCUMENTO["scadenza"],
        "cellulare": "333 123 4567",
    }
    dati.update(kw)
    return client.post(
        f"/clienti/{cliente_id}/adesioni-fea",
        headers=headers,
        data=dati,
        files={"copia_documento": ("ci.pdf", b"%PDF-1.4 copia", "application/pdf")},
    )


@pytest.fixture
def adesione(client, headers, mario, sms_finti):
    res = _adesione(client, headers, mario["id"], cellulare=CELLULARE)
    assert res.status_code == 201, res.text
    res = client.post(
        f"/adesioni-fea/{res.json()['id']}/conferma",
        headers=headers,
        json={"codice": sms_finti.ultimo_codice()},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _delega_in_firma(client, headers, mario):
    d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"]))
    assert d.status_code == 201, d.text
    xml = client.get(f"/deleghe/{d.json()['id']}/xml", headers=headers)
    assert xml.status_code == 200, xml.text
    return d.json()["id"], xml.content


def _firma_cliente(client, headers, sms_finti, delega_id):
    res = client.post(f"/deleghe/{delega_id}/fea/otp", headers=headers)
    assert res.status_code == 200, res.text
    res = client.post(
        f"/deleghe/{delega_id}/fea/firma",
        headers=headers,
        json={"codice": sms_finti.ultimo_codice()},
    )
    assert res.status_code == 200, res.text
    return res.json()


def _errori(res):
    assert res.status_code == 422, res.text
    return " ".join(res.json()["detail"]["errori"])


def test_adesione_con_conferma_otp(client, headers, mario, sms_finti):
    res = _adesione(client, headers, mario["id"])
    assert res.status_code == 201, res.text
    adesione = res.json()
    assert adesione["stato"] == "da_confermare"
    assert adesione["cellulare"] == "3331234567"
    assert sms_finti.inviati[-1][0] == "3331234567"

    testo = client.get(f"/adesioni-fea/{adesione['id']}/dichiarazione", headers=headers).text
    assert MARIO in testo and "CA12345AB" in testo and "vent'anni" in testo
    assert fea.sha256(testo.encode()) == adesione["dichiarazione_sha256"]

    url = f"/adesioni-fea/{adesione['id']}/conferma"
    sbagliato = "000000" if sms_finti.ultimo_codice() != "000000" else "111111"
    assert "errato" in _errori(client.post(url, headers=headers, json={"codice": sbagliato}))
    res = client.post(url, headers=headers, json={"codice": sms_finti.ultimo_codice()})
    assert res.status_code == 200, res.text
    assert res.json()["stato"] == "attiva"
    # Il codice e' monouso
    assert client.post(url, headers=headers, json={"codice": "123456"}).status_code == 409


def test_adesione_rifiutata_per_partita_iva_e_documento_scaduto(client, headers, sms_finti):
    cliente = client.post(
        "/clienti",
        headers=headers,
        json={
            "codice_fiscale": MARIO,
            "cognome": "Rossi",
            "nome": "Mario",
            "partita_iva_attiva": True,
        },
    ).json()
    errori = _errori(_adesione(client, headers, cliente["id"], documento_scadenza="2026-01-01"))
    assert "senza partita IVA" in errori
    assert "scaduto" in errori
    assert sms_finti.inviati == []


def test_otp_scaduto_e_troppi_tentativi(client, headers, mario, sms_finti, monkeypatch):
    adesione = _adesione(client, headers, mario["id"]).json()
    url = f"/adesioni-fea/{adesione['id']}/conferma"
    giusto = sms_finti.ultimo_codice()
    sbagliato = "000000" if giusto != "000000" else "111111"
    for _ in range(5):
        client.post(url, headers=headers, json={"codice": sbagliato})
    assert "Troppi tentativi" in _errori(client.post(url, headers=headers, json={"codice": giusto}))

    client.post(f"/adesioni-fea/{adesione['id']}/otp", headers=headers)
    dopo = datetime(OGGI.year, OGGI.month, OGGI.day, 18, tzinfo=ROMA) + timedelta(minutes=6)
    monkeypatch.setattr(clock, "adesso", lambda: dopo)
    res = client.post(url, headers=headers, json={"codice": sms_finti.ultimo_codice()})
    assert "scaduto" in _errori(res)


def test_firma_allo_sportello_e_controfirma(
    client, headers, mario, adesione, sms_finti, pki, monkeypatch
):
    delega_id, xml = _delega_in_firma(client, headers, mario)
    evidenza = _firma_cliente(client, headers, sms_finti, delega_id)
    assert "firmare la delega" in sms_finti.inviati[-1][1]

    busta = client.get(f"/deleghe/{delega_id}/fea/busta-cliente", headers=headers).content
    assert fea.sha256(busta) == evidenza["sha256_busta"]
    esito = cades.verifica(busta)
    firmatario = esito.livelli[0].firmatari[0]
    assert esito.contenuto == xml
    assert firmatario.codice_fiscale == MARIO
    assert not firmatario.qualificato

    # Lo studio controfirma piu' tardi con la propria firma digitale
    ora_studio = datetime(OGGI.year, OGGI.month, OGGI.day, 18, 30, tzinfo=ROMA)
    monkeypatch.setattr(clock, "adesso", lambda: ora_studio + timedelta(minutes=30))
    controfirmata = cades.firma(busta, *pki["studio"], ora=ora_studio)
    res = carica(client, headers, delega_id, controfirmata)
    assert res.status_code == 200, res.text
    assert res.json()["numero_registro"] == 1
    assert client.get(f"/deleghe/{delega_id}", headers=headers).json()["stato"] == "firmata"


def test_controfirma_di_una_busta_diversa(client, headers, mario, adesione, sms_finti, pki):
    delega_id, xml = _delega_in_firma(client, headers, mario)
    _firma_cliente(client, headers, sms_finti, delega_id)
    # Una busta valida ma prodotta fuori dalla piattaforma
    ora = datetime(OGGI.year, OGGI.month, OGGI.day, 17, tzinfo=ROMA)
    altra = cades.firma(xml, *pki["mario_fea"], ora=ora)
    controfirmata = cades.firma(altra, *pki["studio"], ora=ora + timedelta(minutes=5))
    assert "piattaforma" in _errori(carica(client, headers, delega_id, controfirmata))


def test_firma_senza_adesione_o_con_documento_diverso(client, headers, mario, sms_finti):
    delega_id, _ = _delega_in_firma(client, headers, mario)
    res = client.post(f"/deleghe/{delega_id}/fea/otp", headers=headers)
    assert "adesione attiva" in _errori(res)

    adesione = _adesione(client, headers, mario["id"], documento_numero="AX99999").json()
    client.post(
        f"/adesioni-fea/{adesione['id']}/conferma",
        headers=headers,
        json={"codice": sms_finti.ultimo_codice()},
    )
    res = client.post(f"/deleghe/{delega_id}/fea/otp", headers=headers)
    assert "non e' quello registrato" in _errori(res)


def test_xml_rigenerato_dopo_otp(client, headers, mario, adesione, sms_finti, monkeypatch):
    delega_id, _ = _delega_in_firma(client, headers, mario)
    client.post(f"/deleghe/{delega_id}/fea/otp", headers=headers)
    codice = sms_finti.ultimo_codice()
    # Un nuovo XML (ad esempio dopo aver cambiato i dati dello studio) invalida il codice
    client.patch("/tenant", headers=headers, json={"name": "Studio Bruni e associati"})
    client.get(f"/deleghe/{delega_id}/xml", headers=headers)
    res = client.post(f"/deleghe/{delega_id}/fea/firma", headers=headers, json={"codice": codice})
    assert "cambiato" in _errori(res)


def test_nuova_adesione_sostituisce_la_precedente(client, headers, mario, adesione, sms_finti):
    nuova = _adesione(client, headers, mario["id"], documento_numero="CA99999ZZ").json()
    client.post(
        f"/adesioni-fea/{nuova['id']}/conferma",
        headers=headers,
        json={"codice": sms_finti.ultimo_codice()},
    )
    stati = {
        a["id"]: a["stato"]
        for a in client.get(f"/clienti/{mario['id']}/adesioni-fea", headers=headers).json()
    }
    assert stati == {adesione["id"]: "revocata", nuova["id"]: "attiva"}


def test_evidenze_e_identificazione_non_modificabili(
    client, headers, mario, adesione, sms_finti, db
):
    delega_id, _ = _delega_in_firma(client, headers, mario)
    _firma_cliente(client, headers, sms_finti, delega_id)
    with pytest.raises(DBAPIError, match="sola aggiunta"):
        db.execute(text("UPDATE evidenze_firma SET ip = '1.2.3.4'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="non si modificano"):
        db.execute(text("UPDATE adesioni_fea SET documento_numero = 'XX'"))
    db.rollback()
    with pytest.raises(DBAPIError, match="revocate"):
        db.execute(text("DELETE FROM adesioni_fea"))
    db.rollback()


def test_certificato_monouso(client, headers, mario, adesione, sms_finti):
    seriali = set()
    for _ in range(2):
        delega_id, _ = _delega_in_firma(client, headers, mario)
        seriali.add(_firma_cliente(client, headers, sms_finti, delega_id)["seriale_certificato"])
        client.post(f"/deleghe/{delega_id}/invio", headers=headers)
    assert len(seriali) == 2


def test_autorita_salvata_e_ricaricata(tmp_path, pki):
    from app.services import ca_fea

    cert, chiave = tmp_path / "ca.pem", tmp_path / "ca.key"
    ca_fea.salva_autorita(pki["ca"], cert, chiave, "segreta")
    assert b"ENCRYPTED" in chiave.read_bytes()
    caricata = ca_fea.carica_autorita(cert, chiave, "segreta")
    assert caricata.certificato == pki["ca"].certificato
    with pytest.raises(ValueError):
        ca_fea.carica_autorita(cert, chiave, "sbagliata")
