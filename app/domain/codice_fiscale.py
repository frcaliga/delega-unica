"""Controllo formale dei codici fiscali (persone fisiche, anche omocodici, e soggetti diversi)."""

import re
import unicodedata
from datetime import date

MODELLO_PF = r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]"
_PF_RE = re.compile(f"^{MODELLO_PF}$")
_PNF_RE = re.compile(r"^[0-9]{11}$")

# Valori dei caratteri in posizione dispari (1a, 3a, ...) per il carattere di controllo
_DISPARI = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [1, 0, 5, 7, 9, 13, 15, 17, 19, 21]
        + [
            1,
            0,
            5,
            7,
            9,
            13,
            15,
            17,
            19,
            21,
            2,
            4,
            18,
            20,
            11,
            3,
            6,
            8,
            12,
            14,
            16,
            10,
            22,
            25,
            24,
            23,
        ],
        strict=True,
    )
)


def _pari(c: str) -> int:
    return int(c) if c.isdigit() else ord(c) - ord("A")


def carattere_controllo_pf(primi15: str) -> str:
    totale = sum(_DISPARI[c] if i % 2 == 0 else _pari(c) for i, c in enumerate(primi15.upper()))
    return chr(ord("A") + totale % 26)


def cifra_controllo_pnf(prime10: str) -> str:
    totale = 0
    for i, c in enumerate(prime10):
        n = int(c)
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        totale += n
    return str((10 - totale % 10) % 10)


def is_persona_fisica(cf: str) -> bool:
    return len(cf) == 16


def valida(cf: str) -> bool:
    cf = cf.upper()
    if _PF_RE.match(cf):
        return carattere_controllo_pf(cf[:15]) == cf[15]
    if _PNF_RE.match(cf):
        return cifra_controllo_pnf(cf[:10]) == cf[10]
    return False


# Confronto tra il codice fiscale e i dati anagrafici letti da un documento

MESI = "ABCDEHLMPRST"
_OMOCODIA = dict(zip("LMNPQRSTUV", "0123456789", strict=True))
_POSIZIONI_OMOCODIA = (6, 7, 9, 10, 12, 13, 14)


def _lettere(testo: str) -> str:
    testo = unicodedata.normalize("NFD", testo.upper())
    return "".join(c for c in testo if "A" <= c <= "Z")


def _consonanti_vocali(testo: str) -> tuple[str, str]:
    lettere = _lettere(testo)
    vocali = "".join(c for c in lettere if c in "AEIOU")
    consonanti = "".join(c for c in lettere if c not in "AEIOU")
    return consonanti, vocali


def codice_cognome(cognome: str) -> str:
    consonanti, vocali = _consonanti_vocali(cognome)
    return (consonanti + vocali + "XXX")[:3]


def codice_nome(nome: str) -> str:
    consonanti, vocali = _consonanti_vocali(nome)
    if len(consonanti) >= 4:
        return consonanti[0] + consonanti[2] + consonanti[3]
    return (consonanti + vocali + "XXX")[:3]


def senza_omocodia(cf: str) -> str:
    cf = list(cf.upper())
    for i in _POSIZIONI_OMOCODIA:
        cf[i] = _OMOCODIA.get(cf[i], cf[i])
    return "".join(cf)


def discordanze(
    cf: str,
    *,
    cognome: str | None = None,
    nome: str | None = None,
    data_nascita: date | None = None,
    sesso: str | None = None,
) -> list[str]:
    """Dati anagrafici che non tornano con un codice fiscale di persona fisica.

    Il luogo di nascita non si controlla: il documento non lo riporta nella MRZ.
    """
    if not is_persona_fisica(cf):
        return []
    cf = senza_omocodia(cf)
    esito = []
    if cognome and codice_cognome(cognome) != cf[0:3]:
        esito.append("cognome")
    if nome and codice_nome(nome) != cf[3:6]:
        esito.append("nome")
    if data_nascita is not None:
        giorno = int(cf[9:11])
        if (
            cf[6:8] != f"{data_nascita.year % 100:02d}"
            or cf[8] != MESI[data_nascita.month - 1]
            or giorno % 40 != data_nascita.day
        ):
            esito.append("data di nascita")
        if sesso and (giorno > 40) != (sesso == "F"):
            esito.append("sesso")
    elif sesso:
        if (int(cf[9:11]) > 40) != (sesso == "F"):
            esito.append("sesso")
    return esito
