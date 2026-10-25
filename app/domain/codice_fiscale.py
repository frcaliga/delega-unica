"""Controllo formale dei codici fiscali (persone fisiche, anche omocodici, e soggetti diversi)."""

import re

_PF_RE = re.compile(
    r"^[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$"
)
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
