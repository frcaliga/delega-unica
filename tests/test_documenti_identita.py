"""Documenti d'identita': MRZ, confronto col codice fiscale, cifratura, lettura e archivio."""

import io
import shutil
from datetime import date

import pytest
from PIL import Image
from sqlalchemy import select, text

from app.domain import codice_fiscale
from app.models import AuditLog, DocumentoIdentita
from app.services import cifratura, lettura_documenti, ocr_documenti
from tests.helpers import LAURA, MARIO, SOCIETA, cf_pf
from tests.immagini_documenti import retro_cie, righe_cie
from tests.test_deleghe_api import _oggi, headers, mario  # noqa: F401 - fixture riusate
from tests.test_web import admin, browser, sms_finti  # noqa: F401 - fixture riusate

OGGI = date(2026, 10, 24)
tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="Tesseract assente")

# MRZ (motore OCR)


def test_mrz_cie_con_errori_tipici_dell_ocr():
    righe = righe_cie(numero="CB12345XY", cognome="DE LUCA", nome="GIAN PAOLO")
    # O per 0 e I per 1, un carattere spurio inserito, riempitivo letto come K
    sporche = [
        righe[0].replace("12345", "I2345").replace("ITA", "ITA."),
        righe[1].replace("800101", "8OO1O1"),
        righe[2].replace("<<<<<", "<K<K<"),
    ]
    dati = ocr_documenti.parse_mrz(["REPUBBLICA ITALIANA", *sporche])
    assert dati["num"] == "CB12345XY"
    assert (dati["cognome"], dati["nome"]) == ("DE LUCA", "GIAN PAOLO")
    assert (dati["nascita"], dati["scadenza"], dati["sesso"]) == ("01/01/1980", "01/01/2031", "M")


def test_mrz_cifre_di_controllo_errate():
    righe = righe_cie()
    righe[1] = "800102" + righe[1][6:]  # data di nascita alterata
    dati = ocr_documenti.parse_mrz(righe)
    assert "nascita" not in dati and dati["num"] == "CA00000AA"


def test_da_estrazione():
    lettura = lettura_documenti.da_estrazione(
        {
            "tipo": "CIE",
            "numero_documento": "CA00000AA",
            "cognome": "DE LUCA",
            "nome": "GIAN PAOLO",
            "data_nascita": "31/12/1999",
            "data_scadenza": "31/02/2031",
            "sesso": "M",
            "comune_rilascio": "ROMA",
            "luogo_nascita": "TORINO",
            "provincia_nascita": "TO",
            "mrz_verificata": True,
        }
    )
    assert lettura.trovata and lettura.tipo.value == "1"
    assert (lettura.cognome, lettura.nome) == ("De Luca", "Gian Paolo")
    assert lettura.data_nascita == date(1999, 12, 31) and lettura.scadenza is None
    assert lettura.rilasciato_da == "Comune di Roma" and lettura.luogo_nascita == "Torino"
    assert not lettura_documenti.da_estrazione({"tipo": None}).trovata


# Codice fiscale


def test_discordanze_con_il_codice_fiscale():
    assert (
        codice_fiscale.discordanze(
            MARIO, cognome="Rossi", nome="Mario", data_nascita=date(1980, 1, 1), sesso="M"
        )
        == []
    )
    assert (
        codice_fiscale.discordanze(
            LAURA, cognome="Bianchi", nome="Laura", data_nascita=date(1985, 8, 1), sesso="F"
        )
        == []
    )
    assert codice_fiscale.discordanze(
        MARIO, cognome="Verdi", nome="Mario", data_nascita=date(1980, 1, 2), sesso="F"
    ) == ["cognome", "data di nascita", "sesso"]
    # Omocodia: le cifre sostituite da lettere valgono lo stesso
    omocodico = cf_pf("RSSMRAULALMVHQRS"[:6] + "UL" + "A" + "LM" + "H" + "5Q1")
    assert codice_fiscale.discordanze(omocodico, data_nascita=date(1980, 1, 1)) == []
    assert codice_fiscale.codice_nome("Gianfranco") == "GFR"
    assert codice_fiscale.codice_cognome("Fo") == "FOX"
    assert codice_fiscale.discordanze(SOCIETA, cognome="Rossi") == []


# Cifratura


class KmsFinto:
    def __init__(self):
        self.locale = cifratura.ChiaveLocale(b"k" * 32)

    def generate_data_key(self, KeyId, KeySpec, EncryptionContext):
        chiave, cifrata = self.locale.nuova_chiave(EncryptionContext)
        return {"Plaintext": chiave, "CiphertextBlob": cifrata}

    def decrypt(self, KeyId, CiphertextBlob, EncryptionContext):
        return {"Plaintext": self.locale.apri_chiave(CiphertextBlob, EncryptionContext)}


def test_cifratura_a_busta():
    locale = cifratura.ChiaveLocale(b"\x01" * 32)
    contesto = {"tenant": "t1", "documento": "d1"}
    busta = cifratura.cifra(locale, b"copia del documento", contesto)
    assert b"copia" not in busta.dati and busta.schema == "locale"
    assert cifratura.decifra(locale, busta, contesto) == b"copia del documento"
    # Contesto diverso (altra riga o altro studio), dati alterati, chiave diversa
    with pytest.raises(cifratura.ErroreCifratura):
        cifratura.decifra(locale, busta, {"tenant": "t2", "documento": "d1"})
    alterata = cifratura.Busta(busta.schema, busta.chiave_cifrata, busta.dati[:-1] + b"\x00")
    with pytest.raises(cifratura.ErroreCifratura):
        cifratura.decifra(locale, alterata, contesto)
    with pytest.raises(cifratura.ErroreCifratura):
        cifratura.decifra(cifratura.ChiaveLocale(b"\x02" * 32), busta, contesto)


def test_passaggio_da_chiave_locale_a_kms():
    locale = cifratura.ChiaveLocale(b"\x01" * 32)
    contesto = {"tenant": "t1", "documento": "d1"}
    vecchia = cifratura.Portachiavi(locale).cifra(b"prima", contesto)
    portachiavi = cifratura.Portachiavi(cifratura.ChiaveKms(KmsFinto(), "alias/x"), locale)
    nuova = portachiavi.cifra(b"dopo", contesto)
    assert nuova.schema == "kms"
    assert portachiavi.decifra(vecchia, contesto) == b"prima"
    assert portachiavi.decifra(nuova, contesto) == b"dopo"


# Lettura delle immagini


@tesseract
def test_lettura_retro_cie_e_pdf():
    righe = righe_cie(
        numero="CB12345XY", cognome="BIANCHI", nome="LAURA", nascita=date(1985, 8, 1), sesso="F"
    )
    png = retro_cie(righe, LAURA)
    esito = lettura_documenti.leggi(png, "image/png", OGGI)
    assert esito.trovata, esito
    assert esito.riconosciuto == "CIE" and esito.codice_fiscale == LAURA
    assert (esito.cognome, esito.nome, esito.sesso, esito.data_nascita) == (
        "Bianchi",
        "Laura",
        "F",
        date(1985, 8, 1),
    )
    assert esito.numero == "CB12345XY" and esito.scadenza == date(2031, 1, 1)

    # PDF di scansione con il fronte (senza MRZ) e il retro
    fronte = Image.new("RGB", (1020, 640), "white")
    buf = io.BytesIO()
    retro = Image.open(io.BytesIO(png))
    fronte.save(buf, "PDF", save_all=True, append_images=[retro], resolution=300)
    esito = lettura_documenti.leggi(buf.getvalue(), "application/pdf", OGGI)
    assert esito.trovata and esito.cognome == "Bianchi"


@tesseract
def test_lettura_senza_mrz():
    buf = io.BytesIO()
    Image.new("RGB", (800, 500), "white").save(buf, "PNG")
    esito = lettura_documenti.leggi(buf.getvalue(), "image/png", OGGI)
    assert not esito.trovata and "Documento non riconosciuto" in esito.avvisi[0]
    rotto = lettura_documenti.leggi(b"\x89PNG\r\n\x1a\nrotto", "image/png", OGGI)
    assert not rotto.trovata and rotto.avvisi


# API


def _copia_mario(formato="JPEG"):
    tipo = {"JPEG": "image/jpeg", "PNG": "image/png"}[formato]
    return {"copia": ("retro.jpg", retro_cie(righe_cie(), MARIO, formato), tipo)}


@tesseract
def test_lettura_senza_salvare(client, headers, db):
    res = client.post(
        "/documenti-identita/lettura",
        headers=headers,
        files=_copia_mario(),
        data={"codice_fiscale": MARIO},
    )
    assert res.status_code == 200, res.text
    p = res.json()
    assert p["letta"] and p["cognome"] == "Rossi" and p["nome"] == "Mario"
    assert p["data_nascita"] == "1980-01-01" and p["sesso"] == "M"
    assert p["codice_fiscale"] == MARIO and p["discordanze"] == []
    # Un numero letto e' sempre quello giusto: se le cifre di controllo non tornano, resta vuoto
    assert p["numero"] in ("CA00000AA", None)
    assert db.scalar(select(DocumentoIdentita.id)) is None


def test_copia_non_valida(client, headers, mario):
    for file in (
        ("ci.txt", b"testo", "text/plain"),
        ("ci.pdf", b"non e' un pdf", "application/pdf"),
        ("ci.pdf", b"", "application/pdf"),
    ):
        res = client.post(
            f"/clienti/{mario['id']}/documenti-identita", headers=headers, files={"copia": file}
        )
        assert res.status_code == 422, res.text


@tesseract
def test_archivio_cifrato(client, headers, mario, db, make_user, login):
    copia = _copia_mario()
    res = client.post(
        f"/clienti/{mario['id']}/documenti-identita",
        headers=headers,
        files=copia,
        data={"rilasciato_da": "Comune di Roma"},
    )
    assert res.status_code == 201, res.text
    doc = res.json()
    assert doc["proposta"]["letta"] and doc["scadenza"] == "2031-01-01"
    assert doc["tipo"] == "1" and doc["rilasciato_da"] == "Comune di Roma"
    # Nell'esito conservato non ci sono dati personali
    assert doc["lettura"] == {
        "letta": True,
        "documento": "carta d'identita' elettronica",
        "campi_incerti": doc["proposta"]["campi_incerti"],
        "discordanze": [],
        "codice_fiscale_trovato": True,
    }

    # Nel database c'e' solo il testo cifrato
    grezzo = db.execute(
        text("SELECT copia_cifrata, cifratura FROM documenti_identita WHERE id = :id"),
        {"id": doc["id"]},
    ).one()
    originale = copia["copia"][1]
    assert grezzo.cifratura == "locale" and originale[100:140] not in grezzo.copia_cifrata

    scaricata = client.get(f"/documenti-identita/{doc['id']}/copia", headers=headers)
    assert scaricata.status_code == 200 and scaricata.content == originale
    assert scaricata.headers["cache-control"] == "no-store"
    elenco = client.get(f"/clienti/{mario['id']}/documenti-identita", headers=headers).json()
    assert [d["id"] for d in elenco] == [doc["id"]]

    # Un altro studio non lo vede
    altro = login(make_user("altro@studio.it", tenant_cf="11111111111", totp=True))
    assert client.get(f"/documenti-identita/{doc['id']}/copia", headers=altro).status_code == 404
    assert client.delete(f"/documenti-identita/{doc['id']}", headers=altro).status_code == 404

    assert client.delete(f"/documenti-identita/{doc['id']}", headers=headers).status_code == 204
    assert client.get(f"/documenti-identita/{doc['id']}/copia", headers=headers).status_code == 404
    azioni = set(db.scalars(select(AuditLog.action)))
    assert {
        "documento_identita_caricato",
        "documento_identita_aperto",
        "documento_identita_eliminato",
    } <= azioni


def test_solo_persone_fisiche(client, headers):
    societa = client.post(
        "/clienti",
        headers=headers,
        json={"codice_fiscale": SOCIETA, "denominazione": "Societa' Srl"},
    ).json()
    res = client.post(
        f"/clienti/{societa['id']}/documenti-identita",
        headers=headers,
        files={"copia": ("ci.pdf", b"%PDF-1.4 copia", "application/pdf")},
    )
    assert res.status_code == 422 and "persone fisiche" in res.text


def test_anagrafica_con_data_di_nascita_e_sesso(client, headers, mario):
    res = client.patch(
        f"/clienti/{mario['id']}",
        headers=headers,
        json={"data_nascita": "1980-01-01", "sesso": "M"},
    )
    assert res.status_code == 200 and res.json()["data_nascita"] == "1980-01-01"
    res = client.patch(f"/clienti/{mario['id']}", headers=headers, json={"sesso": "X"})
    assert res.status_code == 422


# Pagine


@tesseract
def test_pagine_nuovo_cliente_dal_documento_e_adesione_fea(browser, sms_finti, db, _oggi):
    copia = _copia_mario()
    letti = browser.post("/studio/clienti/nuovo/leggi", {}, files=copia, htmx="campi-nuovo-cliente")
    assert letti.status_code == 200, letti.text
    assert 'id="campi-nuovo-cliente"' in letti.text and "<html" not in letti.text
    for valore in ("Rossi", "Mario", "1980-01-01", "2031-01-01"):
        assert f'value="{valore}"' in letti.text, valore
    assert MARIO in letti.text

    dati = {
        "codice_fiscale": MARIO,
        "cognome": "Rossi",
        "nome": "Mario",
        "data_nascita": "1980-01-01",
        "sesso": "M",
        "documento_tipo": "1",
        "documento_numero": "CA00000AA",
        "documento_rilasciato_da": "Comune di Roma",
        "documento_scadenza": "2031-01-01",
    }
    res = browser.post("/studio/clienti/nuovo", dati, files=copia)
    assert res.status_code == 303, res.text
    scheda_url = res.headers["location"].split("?")[0]
    doc = db.scalars(select(DocumentoIdentita)).one()
    assert (doc.numero, doc.rilasciato_da) == ("CA00000AA", "Comune di Roma")

    scheda = browser.get(scheda_url)
    assert "Documenti d&#39;identita&#39;" in scheda.text or "Documenti d'identita'" in scheda.text
    assert f"/studio/documenti/{doc.id}/copia" in scheda.text
    # L'adesione FEA propone il documento archiviato e ne riusa la copia
    assert 'value="CA00000AA"' in scheda.text and f'value="{doc.id}"' in scheda.text
    scaricata = browser.get(f"/studio/documenti/{doc.id}/copia")
    assert scaricata.content == copia["copia"][1]

    adesione = {
        "documento_tipo": "1",
        "documento_numero": "CA00000AA",
        "documento_rilasciato_da": "Comune di Roma",
        "documento_scadenza": "2031-01-01",
        "cellulare": "+393331234567",
        "documento_identita_id": str(doc.id),
    }
    res = browser.post(f"{scheda_url}/adesioni", adesione)
    assert res.status_code == 303, res.text
    from app.models import AdesioneFea

    salvata = db.scalars(select(AdesioneFea)).one()
    assert salvata.copia_documento == copia["copia"][1]
    assert salvata.copia_documento_tipo == "image/jpeg"

    # Eliminare la copia archiviata non tocca l'evidenza dell'adesione
    res = browser.post(f"/studio/documenti/{doc.id}/elimina", {})
    assert res.status_code == 303
    db.expire_all()
    assert db.scalar(select(DocumentoIdentita.id)) is None
    assert db.scalars(select(AdesioneFea)).one().copia_documento == copia["copia"][1]


@tesseract
def test_pagina_scheda_carica_documento_e_aggiorna_anagrafica(browser, db):
    from app.models import Cliente
    from tests.test_web import _crea_cliente

    cliente_id = _crea_cliente(browser)
    copia = _copia_mario()
    letti = browser.post(
        f"/studio/clienti/{cliente_id}/documenti/leggi", {}, files=copia, htmx="campi-documento"
    )
    assert letti.status_code == 200 and 'value="1980-01-01"' in letti.text
    senza_file = browser.post(f"/studio/clienti/{cliente_id}/documenti", {"documento_tipo": "1"})
    assert senza_file.status_code == 422 and "copia del documento" in senza_file.text
    res = browser.post(
        f"/studio/clienti/{cliente_id}/documenti",
        {
            "documento_tipo": "1",
            "documento_numero": "CA00000AA",
            "documento_scadenza": "2031-01-01",
            "cognome": "Rossi",
            "nome": "Mario",
            "data_nascita": "1980-01-01",
            "sesso": "M",
            "aggiorna_anagrafica": "on",
        },
        files=copia,
    )
    assert res.status_code == 303, res.text
    cliente = db.get(Cliente, cliente_id)
    assert (cliente.data_nascita, cliente.sesso) == (date(1980, 1, 1), "M")


def test_comando_elimina_documenti_scaduti(client, headers, mario, db, monkeypatch):
    from typer.testing import CliRunner

    from app.cli import cli

    monkeypatch.setattr(lettura_documenti, "leggi", lambda *a, **k: lettura_documenti.Lettura())
    for scadenza in ("2024-01-01", "2026-01-01"):
        res = client.post(
            f"/clienti/{mario['id']}/documenti-identita",
            headers=headers,
            files={"copia": ("ci.pdf", b"%PDF-1.4 copia", "application/pdf")},
            data={"scadenza": scadenza, "numero": "CA00000AA"},
        )
        assert res.status_code == 201, res.text
    esito = CliRunner().invoke(cli, ["elimina-documenti-scaduti", "--mesi", "12"])
    assert esito.exit_code == 0 and "Eliminati: 1" in esito.output, esito.output
    db.expire_all()
    assert [d.scadenza for d in db.scalars(select(DocumentoIdentita))] == [date(2026, 1, 1)]


@tesseract
def test_fronte_e_retro_in_due_foto(client, headers, mario):
    buf = io.BytesIO()
    Image.new("RGB", (1020, 640), "white").save(buf, "JPEG")
    retro = retro_cie(righe_cie(), MARIO, "JPEG")
    files = [
        ("copia", ("fronte.jpg", buf.getvalue(), "image/jpeg")),
        ("copia", ("retro.jpg", retro, "image/jpeg")),
    ]
    res = client.post(f"/clienti/{mario['id']}/documenti-identita", headers=headers, files=files)
    assert res.status_code == 201, res.text
    doc = res.json()
    assert doc["copia_tipo"] == "application/pdf" and doc["copia_nome"] == "fronte.pdf"
    assert doc["proposta"]["riconosciuto"] == "carta d'identita' elettronica"
    assert doc["proposta"]["codice_fiscale"] == MARIO
    copia = client.get(f"/documenti-identita/{doc['id']}/copia", headers=headers).content
    assert copia.startswith(b"%PDF")

    pdf_e_foto = [
        ("copia", ("ci.pdf", b"%PDF-1.4 copia", "application/pdf")),
        ("copia", ("retro.jpg", retro, "image/jpeg")),
    ]
    res = client.post(
        f"/clienti/{mario['id']}/documenti-identita", headers=headers, files=pdf_e_foto
    )
    assert res.status_code == 422 and "PDF va caricato da solo" in res.text


def test_elenco_comuni_istat_in_windows_1252(tmp_path):
    righe = (
        "Codice Comune;Denominazione (Italiana e straniera);Denominazione in italiano;"
        "Sigla automobilistica;Codice Catastale del comune\n"
        "001;Forlì;Forlì;FC;D704\n"
        "002;Bolzano/Bozen;Bolzano;BZ;A952\n"
    )
    for codifica in ("cp1252", "utf-8-sig"):
        f = tmp_path / f"comuni-{codifica}.csv"
        f.write_bytes(righe.encode(codifica))
        elenco = ocr_documenti.load_comuni(f)
        assert elenco["D704"] == ("Forlì", "FC")
        assert elenco["A952"] == ("Bolzano", "BZ")


@tesseract
def test_nuovo_cliente_altro_documento_svuota_i_campi_letti_prima(browser):
    bianca = io.BytesIO()
    Image.new("RGB", (800, 500), "white").save(bianca, "PNG")
    precedenti = {
        "campi_letti": "codice_fiscale,cognome,nome",
        "codice_fiscale": MARIO,
        "cognome": "Rossi",
        "nome": "Mario",
        "telefono": "+393331234567",
    }
    res = browser.post(
        "/studio/clienti/nuovo/leggi",
        precedenti,
        files={"copia": ("altro.png", bianca.getvalue(), "image/png")},
        htmx="campi-nuovo-cliente",
    )
    assert res.status_code == 200, res.text
    assert MARIO not in res.text and 'value="Rossi"' not in res.text
    assert 'value="+393331234567"' in res.text  # scritto a mano: resta


@tesseract
def test_anteprima_di_fronte_e_retro(browser, db):
    copia = _copia_mario()
    letti = browser.post("/studio/clienti/nuovo/leggi", {}, files=copia, htmx="campi-nuovo-cliente")
    assert 'src="data:image/jpeg;base64,' in letti.text

    dati = {"codice_fiscale": MARIO, "cognome": "Rossi", "nome": "Mario", "documento_tipo": "1"}
    res = browser.post("/studio/clienti/nuovo", dati, files=copia)
    assert res.status_code == 303, res.text
    doc = db.scalars(select(DocumentoIdentita)).one()
    scheda = browser.get(res.headers["location"].split("?")[0])
    assert f"/studio/documenti/{doc.id}/anteprima.jpg" in scheda.text
    img = browser.get(f"/studio/documenti/{doc.id}/anteprima.jpg")
    assert img.status_code == 200 and img.headers["content-type"] == "image/jpeg"
    assert img.headers["cache-control"] == "no-store"
    assert Image.open(io.BytesIO(img.content)).width <= 1000
    motivi = [a.details.get("motivo") for a in db.scalars(select(AuditLog))]
    assert "anteprima" in motivi
