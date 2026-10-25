from datetime import date, datetime

import pytest
from lxml import etree
from sqlalchemy import text

from app import clock
from app.clock import ROMA
from app.models import Role
from app.services import cades
from tests.helpers import LAURA, MARIO, SOCIETA, STUDIO

OGGI = date(2026, 10, 25)
DOCUMENTO = {
    "tipo": "1",
    "rilasciato_da": "Comune di Roma",
    "numero": "CA12345AB",
    "scadenza": "2031-05-10",
}


def _imposta_data(monkeypatch, giorno: date) -> None:
    monkeypatch.setattr(clock, "oggi", lambda: giorno)
    monkeypatch.setattr(
        clock, "adesso", lambda: datetime(giorno.year, giorno.month, giorno.day, 18, tzinfo=ROMA)
    )


@pytest.fixture(autouse=True)
def _oggi(monkeypatch):
    _imposta_data(monkeypatch, OGGI)


def firma_fea(pki, xml: bytes, giorno: date = OGGI) -> bytes:
    """Busta del cliente con FEA non qualificata, controfirmata dallo studio."""
    ora_cliente = datetime(giorno.year, giorno.month, giorno.day, 10, tzinfo=ROMA)
    ora_studio = datetime(giorno.year, giorno.month, giorno.day, 11, tzinfo=ROMA)
    interna = cades.firma(xml, *pki["mario_fea"], ora=ora_cliente)
    return cades.firma(interna, *pki["studio"], ora=ora_studio)


def carica(client, headers, delega_id, busta):
    return client.post(
        f"/deleghe/{delega_id}/firmato",
        headers=headers,
        files={"file": ("delega.xml.p7m", busta, "application/pkcs7-mime")},
    )


def firma_e_carica(client, headers, pki, delega_id, giorno: date = OGGI):
    xml = client.get(f"/deleghe/{delega_id}/xml", headers=headers)
    assert xml.status_code == 200, xml.text
    res = carica(client, headers, delega_id, firma_fea(pki, xml.content, giorno))
    assert res.status_code == 200, res.text
    return res.json()


@pytest.fixture
def headers(make_user, login):
    return login(make_user("admin@studio.it", tenant_cf=STUDIO, totp=True))


@pytest.fixture
def mario(client, headers):
    res = client.post(
        "/clienti",
        headers=headers,
        json={"codice_fiscale": MARIO.lower(), "cognome": "Rossi", "nome": "Mario"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _conferimento(cliente_id, **kw):
    body = {
        "cliente_id": cliente_id,
        "servizi": ["01", "02"],
        "modalita_firma": "fea_non_qualificata",
        "documento": DOCUMENTO,
        "luogo_firma": "Roma",
        "data_firma": "2026-10-24",
    }
    body.update(kw)
    return body


def _ciclo_completo(client, headers, pki, delega_id, data_esito="2026-10-25"):
    firma_e_carica(client, headers, pki, delega_id, date.fromisoformat(data_esito))
    assert client.post(f"/deleghe/{delega_id}/invio", headers=headers).status_code == 200
    res = client.post(
        f"/deleghe/{delega_id}/esito",
        headers=headers,
        json={"esito": "accettata", "data": data_esito},
    )
    assert res.status_code == 200, res.text
    return res.json()


def test_cliente_con_codice_fiscale_errato(client, headers):
    res = client.post(
        "/clienti",
        headers=headers,
        json={"codice_fiscale": "RSSMRA80A01H501A", "cognome": "Rossi", "nome": "Mario"},
    )
    assert res.status_code == 422


def test_cliente_duplicato(client, headers, mario):
    res = client.post(
        "/clienti",
        headers=headers,
        json={"codice_fiscale": MARIO, "cognome": "Rossi", "nome": "Mario"},
    )
    assert res.status_code == 409


def test_ciclo_di_vita_conferimento(client, headers, mario, pki):
    res = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"]))
    assert res.status_code == 201, res.text
    delega = res.json()
    assert delega["stato"] == "bozza"
    assert delega["qualifica"] == "1"

    xml = client.get(f"/deleghe/{delega['id']}/xml", headers=headers)
    assert xml.status_code == 200
    assert "DEL24_" in xml.headers["content-disposition"]
    root = etree.fromstring(xml.content)
    assert root.tag.endswith("Deleghe")

    esito = carica(client, headers, delega["id"], firma_fea(pki, xml.content)).json()
    assert esito["numero_registro"] == 1
    assert [liv["firmatari"][0]["codice_fiscale"] for liv in esito["firmatari"]] == [LAURA, MARIO]
    voce = client.get("/registro", headers=headers).json()[0]
    assert voce["documento_estremi"].startswith("Carta d'identita' n. CA12345AB")

    p7m = client.get(f"/deleghe/{delega['id']}/firmato", headers=headers)
    assert p7m.headers["content-type"] == "application/pkcs7-mime"

    client.post(f"/deleghe/{delega['id']}/invio", headers=headers)
    attiva = client.post(
        f"/deleghe/{delega['id']}/esito",
        headers=headers,
        json={"esito": "accettata", "data": "2026-10-25"},
    ).json()
    assert attiva["stato"] == "attiva"
    assert attiva["data_scadenza"] == "2030-12-31"


def test_regole_ade_bloccano_la_creazione(client, headers, mario):
    body = _conferimento(mario["id"], data_firma="2025-12-01", documento=None)
    res = client.post("/deleghe", headers=headers, json=body)
    assert res.status_code == 422
    errori = res.json()["detail"]["errori"]
    assert any("8 dicembre 2025" in e for e in errori)
    assert any("documento d'identita'" in e for e in errori)


def test_partita_iva_attiva_solo_firma_digitale_o_cie(client, headers):
    societa = client.post(
        "/clienti",
        headers=headers,
        json={"codice_fiscale": SOCIETA, "denominazione": "Rossi Srl", "partita_iva_attiva": True},
    ).json()
    rappresentante = {
        "codice_fiscale": LAURA,
        "cognome": "Bruni",
        "nome": "Laura",
        "qualifica": "4",
    }
    body = _conferimento(societa["id"], sottoscrittore=rappresentante)
    res = client.post("/deleghe", headers=headers, json=body)
    assert res.status_code == 422
    body["modalita_firma"] = "firma_digitale"
    assert client.post("/deleghe", headers=headers, json=body).status_code == 201


def test_nuovo_conferimento_sostituisce_il_precedente(client, headers, mario, pki):
    prima = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    _ciclo_completo(client, headers, pki, prima["id"])
    seconda = client.post(
        "/deleghe", headers=headers, json=_conferimento(mario["id"], servizi=["08"])
    ).json()
    _ciclo_completo(client, headers, pki, seconda["id"])
    assert client.get(f"/deleghe/{prima['id']}", headers=headers).json()["stato"] == "sostituita"


def test_revoca(client, headers, mario, pki):
    delega = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    _ciclo_completo(client, headers, pki, delega["id"])
    revoca = client.post(
        "/deleghe",
        headers=headers,
        json=_conferimento(
            mario["id"], tipo_richiesta="2", servizi=[], delega_origine_id=delega["id"]
        ),
    )
    assert revoca.status_code == 201, revoca.text
    esito = _ciclo_completo(client, headers, pki, revoca.json()["id"])
    assert esito["stato"] == "accettata"
    assert client.get(f"/deleghe/{delega['id']}", headers=headers).json()["stato"] == "revocata"


def test_rinnovo_solo_negli_ultimi_90_giorni(client, headers, mario, pki, monkeypatch):
    delega = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    _ciclo_completo(client, headers, pki, delega["id"])
    rinnovo = _conferimento(
        mario["id"], tipo_richiesta="3", servizi=[], delega_origine_id=delega["id"]
    )
    res = client.post("/deleghe", headers=headers, json=rinnovo)
    assert res.status_code == 422
    assert "rinnovo" in res.json()["detail"]["errori"][0]

    _imposta_data(monkeypatch, date(2030, 11, 15))
    rinnovo["data_firma"] = "2030-11-14"
    res = client.post("/deleghe", headers=headers, json=rinnovo)
    assert res.status_code == 201, res.text
    assert res.json()["servizi"] == ["01", "02"]
    esito = _ciclo_completo(client, headers, pki, res.json()["id"], data_esito="2030-11-15")
    assert esito["data_scadenza"] == "2035-12-31"

    scadenze = client.get("/deleghe/in-scadenza", headers=headers).json()
    assert scadenze == []


def test_stati_non_ammessi(client, headers, mario):
    delega = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    res = client.post(
        f"/deleghe/{delega['id']}/esito",
        headers=headers,
        json={"esito": "accettata", "data": "2026-10-25"},
    )
    assert res.status_code == 409


def test_scarto(client, headers, mario, pki):
    delega = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    firma_e_carica(client, headers, pki, delega["id"])
    client.post(f"/deleghe/{delega['id']}/invio", headers=headers)
    senza_motivo = client.post(
        f"/deleghe/{delega['id']}/esito",
        headers=headers,
        json={"esito": "scartata", "data": "2026-10-25"},
    )
    assert senza_motivo.status_code == 422
    res = client.post(
        f"/deleghe/{delega['id']}/esito",
        headers=headers,
        json={"esito": "scartata", "data": "2026-10-25", "motivo": "Delegante non trovato"},
    )
    assert res.json()["stato"] == "scartata"


def test_registro_numerazione_hash_ed_export(client, headers, mario, db, pki):
    for _ in range(3):
        d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
        firma_e_carica(client, headers, pki, d["id"])
    voci = client.get("/registro", headers=headers).json()
    assert [v["numero"] for v in voci] == [1, 2, 3]
    assert client.get("/registro/verifica", headers=headers).json()["integro"] is True

    csv = client.get("/registro/export.csv", headers=headers)
    assert csv.status_code == 200
    righe = csv.content.decode("utf-8-sig").splitlines()
    assert righe[0].startswith("Numero;Data;Operazione")
    assert len(righe) == 4

    # Un'alterazione fatta aggirando il trigger viene rilevata dalla verifica
    db.execute(text("ALTER TABLE registro_deleghe DISABLE TRIGGER registro_append_only"))
    db.execute(text("UPDATE registro_deleghe SET delegante_nominativo = 'X' WHERE numero = 2"))
    db.execute(text("ALTER TABLE registro_deleghe ENABLE TRIGGER registro_append_only"))
    db.commit()
    esito = client.get("/registro/verifica", headers=headers).json()
    assert esito["integro"] is False
    assert "numero 2" in esito["primo_errore"]


def test_registro_rifiuta_modifiche(make_user, db, client, headers, mario, pki):
    d = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    firma_e_carica(client, headers, pki, d["id"])
    with pytest.raises(Exception, match="sola aggiunta"):
        db.execute(text("DELETE FROM registro_deleghe"))
    db.rollback()


def test_isolamento_tra_studi(client, headers, mario, make_user, login):
    altro = login(make_user("altro@studio2.it", tenant_cf="11111111111", totp=True))
    assert client.get(f"/clienti/{mario['id']}", headers=altro).status_code == 404
    assert client.get("/clienti", headers=altro).json() == []
    delega = client.post("/deleghe", headers=headers, json=_conferimento(mario["id"])).json()
    assert client.get(f"/deleghe/{delega['id']}", headers=altro).status_code == 404
    res = client.post("/deleghe", headers=altro, json=_conferimento(mario["id"]))
    assert res.status_code == 404


def test_operatore_non_esporta_il_registro(client, make_user, login):
    op = login(make_user("op@studio.it", role=Role.OPERATORE, tenant_cf=STUDIO, totp=True))
    assert client.get("/registro/export.csv", headers=op).status_code == 403
