from datetime import date

import pytest

from app.domain import codice_fiscale, regole
from app.domain.regole import (
    DatiDelegante,
    DatiDocumento,
    DatiSottoscrittore,
    ModalitaFirma,
    Qualifica,
    TipoDocumento,
    TipoRichiesta,
)
from app.domain.testo import is_dato_an, normalizza_an
from tests.helpers import LAURA, MARIO, SOCIETA


@pytest.mark.parametrize(
    "cf, valido",
    [
        ("RSSMRA80A01H501U", True),
        ("RSSMRA80A01H501A", False),
        ("RSSMRA80A01H50MU", False),  # omocodia con carattere di controllo sbagliato
        ("12345678903", True),
        ("12345678901", False),
        ("ABC", False),
    ],
)
def test_codice_fiscale(cf, valido):
    assert codice_fiscale.valida(cf) is valido


def test_codice_fiscale_omocodico():
    omocodico = "RSSMRA80A01H50M"  # l'ultima cifra 1 sostituita da M
    from tests.helpers import cf_pf

    assert codice_fiscale.valida(cf_pf(omocodico))


@pytest.mark.parametrize(
    "testo, atteso",
    [
        ("Società Rossi & C. s.r.l.", "SOCIETÀ ROSSI & C. S.R.L."),
        ("D’Angelo", "D'ANGELO"),
        ("Façade  Ñ", "FACADE N"),
        (" .inizio", "INIZIO"),
    ],
)
def test_normalizza_an(testo, atteso):
    assert normalizza_an(testo) == atteso
    assert is_dato_an(atteso)


def test_scadenza_quarto_anno_successivo():
    assert regole.scadenza(date(2026, 1, 15)) == date(2030, 12, 31)
    assert regole.scadenza(date(2026, 12, 31)) == date(2030, 12, 31)


def test_rinnovo_decorre_dall_anno_successivo():
    assert regole.scadenza_dopo_rinnovo(date(2030, 12, 31)) == date(2035, 12, 31)


def test_finestra_rinnovo_90_giorni():
    scad = date(2030, 12, 31)
    assert not regole.rinnovo_consentito(scad, date(2030, 10, 1))
    assert regole.rinnovo_consentito(scad, date(2030, 10, 2))
    assert regole.rinnovo_consentito(scad, scad)
    assert not regole.rinnovo_consentito(scad, date(2031, 1, 1))


def test_data_firma():
    oggi = date(2026, 10, 25)
    assert regole.errori_data_firma(date(2026, 10, 20), oggi) == []
    assert regole.errori_data_firma(date(2025, 12, 7), oggi)
    assert regole.errori_data_firma(date(2026, 10, 26), oggi)
    assert regole.errori_data_firma(date(2025, 12, 8), date(2029, 12, 9))


def test_servizi():
    assert regole.errori_servizi(TipoRichiesta.CONFERIMENTO, ["01", "08"]) == []
    assert regole.errori_servizi(TipoRichiesta.CONFERIMENTO, [])
    assert regole.errori_servizi(TipoRichiesta.CONFERIMENTO, ["09"])
    assert regole.errori_servizi(TipoRichiesta.CONFERIMENTO, ["01", "01"])
    assert regole.errori_servizi(TipoRichiesta.REVOCA, ["01"])
    assert regole.errori_servizi(TipoRichiesta.RINNOVO, []) == []


def test_sottoscrittore():
    pf = DatiDelegante(MARIO, persona_fisica=True, partita_iva_attiva=False)
    pnf = DatiDelegante(SOCIETA, persona_fisica=False, partita_iva_attiva=True)
    ok = regole.errori_sottoscrittore
    assert ok(pf, DatiSottoscrittore(MARIO, Qualifica.DELEGANTE)) == []
    assert ok(pf, DatiSottoscrittore(LAURA, Qualifica.DELEGANTE))
    assert ok(pf, DatiSottoscrittore(LAURA, Qualifica.TUTORE_GENITORE)) == []
    assert ok(pnf, DatiSottoscrittore(LAURA, Qualifica.RAPPRESENTANTE)) == []
    assert ok(pnf, DatiSottoscrittore(LAURA, Qualifica.TUTORE_GENITORE))
    assert ok(pf, DatiSottoscrittore(LAURA, Qualifica.CURATORE_FALLIMENTARE))


def test_firma_ammessa_per_tipo_di_soggetto():
    privato = DatiDelegante(MARIO, persona_fisica=True, partita_iva_attiva=False)
    con_iva = DatiDelegante(MARIO, persona_fisica=True, partita_iva_attiva=True)
    doc = DatiDocumento(
        TipoDocumento.CARTA_IDENTITA, "COMUNE DI ROMA", "CA00000AA", date(2030, 1, 1)
    )
    assert regole.errori_firma(privato, ModalitaFirma.FEA_NON_QUALIFICATA, doc) == []
    assert regole.errori_firma(privato, ModalitaFirma.FEA_NON_QUALIFICATA, None)
    assert regole.errori_firma(con_iva, ModalitaFirma.FEA_NON_QUALIFICATA, doc)
    assert regole.errori_firma(con_iva, ModalitaFirma.FEA_CIE, None) == []
