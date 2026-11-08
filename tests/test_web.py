"""Interfaccia web dello studio: accesso con TOTP, CSRF e flussi principali dalle pagine."""

import re
from datetime import datetime

import pyotp
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import clock
from app.clock import ROMA
from app.main import app
from app.models import Delega, Role, StatoDelega, User
from app.services import cades, fea, sms
from tests.conftest import PASSWORD
from tests.helpers import MARIO, SOCIETA, STUDIO
from tests.test_deleghe_api import OGGI, _oggi  # noqa: F401 - data simulata
from tests.test_fea_api import SmsFinti

ORA_FIRMA = datetime(2026, 10, 24, 10, tzinfo=ROMA)


class Browser:
    """Client che si comporta come il browser con HTMX: cookie e token CSRF."""

    def __init__(self) -> None:
        self.client = TestClient(app, base_url="https://testserver", follow_redirects=False)

    def get(self, url: str, **kw):
        return self.client.get(url, **kw)

    def post(self, url: str, data=None, files=None, htmx: str | None = None, csrf: bool = True):
        if "du_csrf" not in self.client.cookies:
            self.get("/studio/accesso")
        headers = {}
        if csrf:
            headers["X-CSRF-Token"] = self.client.cookies["du_csrf"]
        if htmx:
            headers.update({"HX-Request": "true", "HX-Target": htmx})
        return self.client.post(url, data=data or {}, files=files, headers=headers)

    def accedi(self, user: User) -> None:
        res = self.post("/studio/accesso", {"email": user.email, "password": PASSWORD})
        assert res.status_code == 303, res.text
        assert res.headers["location"] == "/studio/codice"
        res = self.post("/studio/codice", {"code": pyotp.TOTP(user.totp_secret).now()})
        assert res.status_code == 303, res.text


def _id(res, tipo: str) -> str:
    return re.search(rf"/studio/{tipo}/([0-9a-f-]{{36}})", res.headers["location"]).group(1)


@pytest.fixture
def admin(make_user):
    return make_user("admin@studio.it", tenant_cf=STUDIO, totp=True)


@pytest.fixture
def browser(admin) -> Browser:
    b = Browser()
    b.accedi(admin)
    return b


@pytest.fixture
def sms_finti(pki):
    finti = SmsFinti()
    app.dependency_overrides[sms.get_sms] = lambda: finti
    app.dependency_overrides[fea.get_autorita] = lambda: pki["ca"]
    yield finti
    app.dependency_overrides.clear()


def _crea_cliente(browser: Browser, **kw) -> str:
    dati = {"codice_fiscale": MARIO.lower(), "cognome": "Rossi", "nome": "Mario"}
    dati.update(kw)
    res = browser.post("/studio/clienti/nuovo", dati)
    assert res.status_code == 303, res.text
    return _id(res, "clienti")


DOCUMENTO = {
    "doc_tipo": "1",
    "doc_numero": "CA12345AB",
    "doc_rilasciato_da": "Comune di Roma",
    "doc_scadenza": "2031-05-10",
}


def _crea_delega(browser: Browser, cliente_id: str, **kw) -> str:
    dati = {
        "tipo_richiesta": "1",
        "servizi": ["01", "02"],
        "modalita_firma": "firma_digitale",
        "data_firma": "2026-10-24",
        "luogo_firma": "Roma",
        **DOCUMENTO,
    }
    dati.update(kw)
    res = browser.post(f"/studio/clienti/{cliente_id}/deleghe/nuova", dati)
    assert res.status_code == 303, res.text
    return _id(res, "deleghe")


def _genera_xml(browser: Browser, delega_id: str) -> bytes:
    res = browser.post(f"/studio/deleghe/{delega_id}/xml")
    assert res.status_code == 303, res.text
    xml = browser.get(f"/studio/deleghe/{delega_id}/xml")
    assert xml.status_code == 200
    return xml.content


def _carica(browser: Browser, delega_id: str, busta: bytes):
    return browser.post(
        f"/studio/deleghe/{delega_id}/firmato",
        files={"file": ("delega.xml.p7m", busta, "application/pkcs7-mime")},
    )


def test_pagine_senza_sessione_rimandano_all_accesso():
    b = Browser()
    res = b.get("/studio/clienti")
    assert res.status_code == 303
    assert res.headers["location"] == "/studio/accesso"
    # Con HTMX il rinvio passa dall'intestazione HX-Redirect
    res = b.get("/studio/clienti", headers={"HX-Request": "true"})
    assert res.headers["HX-Redirect"] == "/studio/accesso"
    assert b.get("/").headers["location"] == "/studio"
    # Le API restano in JSON
    assert b.get("/clienti").json()["detail"] == "Credenziali non valide"


def test_accesso_con_codice_totp(admin):
    b = Browser()
    pagina = b.get("/studio/accesso")
    assert pagina.status_code == 200
    assert "default-src 'self'" in pagina.headers["content-security-policy"]
    assert b.post("/studio/accesso", {"email": admin.email, "password": "x"}).status_code == 401

    res = b.post("/studio/accesso", {"email": admin.email, "password": PASSWORD})
    assert res.headers["location"] == "/studio/codice"
    # Con la sola password non si entra
    assert b.get("/studio").status_code == 303
    assert b.post("/studio/codice", {"code": "000000"}).status_code == 401

    res = b.post("/studio/codice", {"code": pyotp.TOTP(admin.totp_secret).now()})
    assert res.headers["location"] == "/studio"
    cookie = res.headers["set-cookie"]
    assert "du_sessione=" in cookie and "HttpOnly" in cookie and "Secure" in cookie
    cruscotto = b.get("/studio")
    assert cruscotto.status_code == 200
    assert "Cruscotto" in cruscotto.text

    b.post("/studio/esci")
    assert b.get("/studio").status_code == 303


def test_primo_accesso_configura_il_totp(make_user, db):
    user = make_user("nuovo@studio.it", tenant_cf=STUDIO)
    b = Browser()
    res = b.post("/studio/accesso", {"email": user.email, "password": PASSWORD})
    assert res.headers["location"] == "/studio/configura-codice"
    pagina = b.get("/studio/configura-codice")
    assert "<svg" in pagina.text
    db.refresh(user)
    assert user.totp_secret in pagina.text and not user.totp_enabled

    assert b.post("/studio/configura-codice", {"code": "000000"}).status_code == 422
    res = b.post("/studio/configura-codice", {"code": pyotp.TOTP(user.totp_secret).now()})
    assert res.headers["location"] == "/studio"
    db.refresh(user)
    assert user.totp_enabled
    assert b.get("/studio").status_code == 200


def test_csrf_obbligatorio(browser):
    res = browser.post("/studio/clienti/nuovo", {"codice_fiscale": MARIO}, csrf=False)
    assert res.status_code == 403
    assert "ricaricare la pagina" in res.text
    # Il token nel modulo vale come l'intestazione
    res = browser.client.post(
        "/studio/clienti/nuovo",
        data={
            "csrf": browser.client.cookies["du_csrf"],
            "codice_fiscale": MARIO,
            "cognome": "Rossi",
            "nome": "Mario",
        },
    )
    assert res.status_code == 303


def test_clienti_ricerca_e_errori(browser):
    res = browser.post(
        "/studio/clienti/nuovo",
        {"codice_fiscale": "RSSMRA80A01H501A", "cognome": "Rossi", "nome": "Mario"},
    )
    assert res.status_code == 422
    assert "Codice fiscale non valido" in res.text
    cliente_id = _crea_cliente(browser)
    assert browser.post("/studio/clienti/nuovo", {"codice_fiscale": MARIO}).status_code == 422
    _crea_cliente(browser, codice_fiscale=SOCIETA, cognome="", nome="", denominazione="Alfa Srl")

    elenco = browser.get("/studio/clienti")
    assert "Rossi Mario" in elenco.text and "Alfa Srl" in elenco.text
    # La ricerca con HTMX restituisce solo le righe della tabella
    righe = browser.get(
        "/studio/clienti?q=alfa", headers={"HX-Request": "true", "HX-Target": "elenco-clienti"}
    )
    assert "Alfa Srl" in righe.text and "Rossi Mario" not in righe.text
    assert "<html" not in righe.text

    res = browser.post(
        f"/studio/clienti/{cliente_id}",
        {"cognome": "Rossi", "nome": "Mario", "telefono": "3331234567"},
    )
    assert res.status_code == 303
    assert "3331234567" in browser.get(f"/studio/clienti/{cliente_id}").text


def test_altro_studio_non_vede_il_cliente(browser, make_user):
    cliente_id = _crea_cliente(browser)
    altro = Browser()
    altro.accedi(make_user("altro@studio.it", tenant_cf="09876543210", totp=True))
    res = altro.get(f"/studio/clienti/{cliente_id}")
    assert res.status_code == 404
    assert "Cliente non trovato" in res.text


def test_ciclo_firma_digitale(browser, pki, db):
    cliente_id = _crea_cliente(browser)
    res = browser.post(
        f"/studio/clienti/{cliente_id}/deleghe/nuova",
        {"tipo_richiesta": "1", "modalita_firma": "firma_digitale", "data_firma": "2026-10-24"},
    )
    assert res.status_code == 422
    assert "Indicare almeno un servizio" in res.text

    delega_id = _crea_delega(browser, cliente_id)
    scheda = browser.get(f"/studio/deleghe/{delega_id}")
    assert "Genera il file XML" in scheda.text
    xml = _genera_xml(browser, delega_id)

    errore = _carica(browser, delega_id, b"non e' un p7m")
    assert errore.status_code == 422
    busta = cades.firma(xml, *pki["mario_qualificato"], ora=ORA_FIRMA)
    res = _carica(browser, delega_id, busta)
    assert res.status_code == 200, res.text
    assert "annotata nel registro al numero <strong>1</strong>" in res.text

    registro = browser.get("/studio/registro")
    assert "Rossi Mario" in registro.text and "CA12345AB" in registro.text
    verifica = browser.post("/studio/registro/verifica", htmx="verifica")
    assert "Registro integro: 1 voci" in verifica.text
    csv = browser.get("/studio/registro/export.csv")
    assert csv.headers["content-type"].startswith("text/csv")

    assert browser.post(f"/studio/deleghe/{delega_id}/invio").status_code == 303
    res = browser.post(
        f"/studio/deleghe/{delega_id}/esito", {"esito": "scartata", "data": "2026-10-25"}
    )
    assert res.status_code == 422
    assert "motivo" in res.text
    res = browser.post(
        f"/studio/deleghe/{delega_id}/esito", {"esito": "accettata", "data": "2026-10-25"}
    )
    assert res.status_code == 303
    delega = db.get(Delega, delega_id)
    assert delega.stato == StatoDelega.ATTIVA
    assert "31/12/2030" in browser.get(f"/studio/deleghe/{delega_id}").text


def test_fea_allo_sportello(browser, pki, sms_finti, db, monkeypatch):
    cliente_id = _crea_cliente(browser, telefono="3331234567")
    documento = {
        "documento_tipo": "1",
        "documento_numero": "CA12345AB",
        "documento_rilasciato_da": "Comune di Roma",
        "documento_scadenza": "2031-05-10",
    }
    copia = {"copia_documento": ("ci.pdf", b"%PDF-1.4 copia", "application/pdf")}
    senza_copia = browser.post(f"/studio/clienti/{cliente_id}/adesioni", documento)
    assert senza_copia.status_code == 422
    assert "copia del documento" in senza_copia.text

    res = browser.post(f"/studio/clienti/{cliente_id}/adesioni", documento, files=copia)
    assert res.status_code == 303, res.text
    adesione_url = res.headers["location"]
    pagina = browser.get(adesione_url)
    assert "Dichiarazione di accettazione" in pagina.text and MARIO in pagina.text
    assert browser.post(f"{adesione_url}/conferma", {"codice": "12"}).status_code == 422
    res = browser.post(f"{adesione_url}/conferma", {"codice": sms_finti.ultimo_codice()})
    assert res.status_code == 303

    # La nuova delega propone la FEA e il documento dell'adesione
    modulo = browser.get(f"/studio/clienti/{cliente_id}/deleghe/nuova")
    assert 'value="CA12345AB"' in modulo.text
    delega_id = _crea_delega(browser, cliente_id, modalita_firma="fea_non_qualificata")
    _genera_xml(browser, delega_id)

    pannello = browser.post(f"/studio/deleghe/{delega_id}/fea/otp", htmx="fea-firma")
    assert pannello.status_code == 200
    assert 'id="fea-firma"' in pannello.text and "<html" not in pannello.text
    assert "Codice inviato a 333****567" in pannello.text
    sbagliato = browser.post(
        f"/studio/deleghe/{delega_id}/fea/firma", {"codice": "000000"}, htmx="fea-firma"
    )
    if sms_finti.ultimo_codice() != "000000":
        assert sbagliato.status_code == 422
    res = browser.post(
        f"/studio/deleghe/{delega_id}/fea/firma",
        {"codice": sms_finti.ultimo_codice()},
        htmx="fea-firma",
    )
    assert res.status_code == 204
    assert res.headers["HX-Location"] == f"/studio/deleghe/{delega_id}?ok=fea"
    scheda = browser.get(f"/studio/deleghe/{delega_id}")
    assert "Scarica la busta firmata dal cliente" in scheda.text
    assert "Rigenera il file XML" not in scheda.text

    busta_cliente = browser.get(f"/studio/deleghe/{delega_id}/fea/busta-cliente").content
    controfirmata = cades.firma(
        busta_cliente, *pki["studio"], ora=datetime(2026, 10, 25, 19, tzinfo=ROMA)
    )
    # Lo studio controfirma dopo il cliente (che firma alle 18 della data simulata)
    monkeypatch.setattr(clock, "adesso", lambda: datetime(2026, 10, 25, 20, tzinfo=ROMA))
    res = _carica(browser, delega_id, controfirmata)
    assert res.status_code == 200, res.text
    assert db.scalar(select(Delega.stato).where(Delega.id == delega_id)) == StatoDelega.FIRMATA

    # Fornitura massiva dalle deleghe firmate
    elenco = browser.get("/studio/deleghe?stato=firmata")
    assert "Scarica il file della fornitura" in elenco.text
    fornitura = browser.post("/studio/forniture", {"delega_ids": [delega_id]})
    assert fornitura.headers["content-type"] == "application/xml"


def test_operatore_non_esporta_il_registro(browser, make_user):
    operatore = Browser()
    operatore.accedi(make_user("op@studio.it", role=Role.OPERATORE, tenant_cf=STUDIO, totp=True))
    assert operatore.get("/studio/registro/export.csv").status_code == 403
    assert operatore.post("/studio/registro/verifica").status_code == 403
    assert "Esporta CSV" not in operatore.get("/studio/registro").text
    assert operatore.post("/studio/studio", {"name": "Nuovo nome"}).status_code == 403


def test_dati_dello_studio_e_nuovo_utente(browser, db):
    assert browser.post("/studio/studio", {"name": "Studio Bianchi"}).status_code == 303
    assert "Studio Bianchi" in browser.get("/studio/studio").text
    res = browser.post(
        "/studio/studio/utenti",
        {
            "full_name": "Anna Verdi",
            "email": "anna@studio.it",
            "role": "operatore",
            "password": "corta",
        },
    )
    assert res.status_code == 422
    res = browser.post(
        "/studio/studio/utenti",
        {
            "full_name": "Anna Verdi",
            "email": "anna@studio.it",
            "role": "operatore",
            "password": "password-lunga-123",
        },
    )
    assert res.status_code == 303
    assert db.scalar(select(User).where(User.email == "anna@studio.it")).role == Role.OPERATORE
