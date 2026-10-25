"""Adattamento dei testi al tipo DatoAN_Type dell'XSD dell'Agenzia delle Entrate."""

import re
import unicodedata

_ACCENTATE_AMMESSE = set("ÀÈÉÌÒÙÜ")
_AMMESSI = set("0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ&'-.,/°^()\"\\|+ ") | _ACCENTATE_AMMESSE
_PRIMO_AMMESSO = set('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ-"')
_SOSTITUZIONI = {"’": "'", "‘": "'", "`": "'", "´": "'", "“": '"', "”": '"', "–": "-", "—": "-"}

DATO_AN_RE = re.compile(
    r"^([0-9A-Z\-]|\")([0-9A-Z&]|'|\-|\.|,|\s|/|°|\^|\(|\)|À|È|É|Ì|Ò|Ù|Ü|\"|\\|\||\+)*$"
)


def normalizza_an(testo: str) -> str:
    """Maiuscolo, accenti non ammessi tolti, caratteri non ammessi sostituiti da spazio."""
    out = []
    for c in testo.strip().upper():
        c = _SOSTITUZIONI.get(c, c)
        if c not in _AMMESSI:
            base = unicodedata.normalize("NFD", c)[0]
            c = base if base in _AMMESSI else " "
        out.append(c)
    risultato = re.sub(r"\s+", " ", "".join(out)).strip()
    while risultato and risultato[0] not in _PRIMO_AMMESSO:
        risultato = risultato[1:].lstrip()
    return risultato


def is_dato_an(testo: str) -> bool:
    return bool(DATO_AN_RE.match(testo))
