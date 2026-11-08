"""Zona a lettura ottica (MRZ) dei documenti di viaggio, ICAO 9303.

- TD1, tre righe da 30 caratteri: carta d'identita' elettronica (CIE) e carte d'identita' UE
- TD3, due righe da 44 caratteri: passaporto

Ogni campo ha una cifra di controllo: una lettura OCR viene accettata solo se tornano tutte.
Gli errori tipici dell'OCR (O al posto di 0, I al posto di 1...) si correggono in base al tipo
del campo prima del controllo.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from itertools import product

PESI = (7, 3, 1)
CARATTERI = re.compile(r"^[A-Z0-9<]+$")

# Confusioni piu' frequenti dell'OCR, nei due sensi
A_CIFRA = {"O": "0", "Q": "0", "D": "0", "U": "0", "I": "1", "L": "1", "Z": "2", "S": "5",
           "G": "6", "B": "8", "T": "7"}  # fmt: skip
A_LETTERA = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B", "7": "T"}


def valore(c: str) -> int:
    if c.isdigit():
        return int(c)
    if c == "<":
        return 0
    return ord(c) - ord("A") + 10


def cifra_controllo(dati: str) -> str:
    return str(sum(valore(c) * PESI[i % 3] for i, c in enumerate(dati)) % 10)


def _numerico(s: str) -> str:
    return "".join(A_CIFRA.get(c, c) for c in s)


def _alfabetico(s: str) -> str:
    return "".join(A_LETTERA.get(c, c) for c in s)


def _controllo(c: str) -> str:
    # La cifra di controllo puo' valere "<" quando il campo e' vuoto
    return c if c == "<" else _numerico(c)


def _correggi_numero(numero: str, controllo: str) -> str:
    """Numero del documento: alfanumerico, quindi si provano le varianti ambigue."""
    if cifra_controllo(numero) == controllo:
        return numero
    ambigui = [i for i, c in enumerate(numero) if c in A_CIFRA or c in A_LETTERA]
    if len(ambigui) > 6:
        return numero
    for scelta in product((False, True), repeat=len(ambigui)):
        lista = list(numero)
        for i, cambia in zip(ambigui, scelta, strict=True):
            if cambia:
                c = lista[i]
                lista[i] = A_CIFRA.get(c) or A_LETTERA.get(c) or c
        candidato = "".join(lista)
        if cifra_controllo(candidato) == controllo:
            return candidato
    return numero


def _data(aammgg: str, futura: bool, oggi: date) -> date | None:
    if not aammgg.isdigit():
        return None
    aa, mm, gg = int(aammgg[:2]), int(aammgg[2:4]), int(aammgg[4:])
    secolo = 2000 if futura or aa <= oggi.year % 100 else 1900
    try:
        return date(secolo + aa, mm, gg)
    except ValueError:
        return None


def _lettere_nome(parte: str) -> str:
    # Nei nomi non ci sono cifre: sono lettere lette male (0 per O, 1 per I...)
    return re.sub(r"[0-9]", lambda m: A_LETTERA.get(m[0], "<"), parte)


def _nominativo(campo: str) -> tuple[str, str]:
    campo = _lettere_nome(campo).rstrip("<")
    cognome, _, nome = campo.partition("<<")
    pulisci = lambda s: re.sub(r"<+", " ", s).strip()  # noqa: E731
    return pulisci(cognome), pulisci(nome)


# Formato del numero dei documenti italiani: L = lettera, N = cifra
FORMATI_NUMERO = {
    ("C", "ITA"): "LLNNNNNLL",  # CIE: CA00000AA
    ("P", "ITA"): "LLNNNNNNN",  # passaporto: YA0000000
}


def _numero(numero: str, formato: str | None, controllo: str) -> str:
    """Con un formato noto ogni posizione ha il suo tipo; altrimenti si provano le varianti."""
    if not formato:
        return _correggi_numero(numero, controllo)
    return "".join(
        (A_CIFRA.get(c, c) if f == "N" else A_LETTERA.get(c, c))
        for c, f in zip(numero, formato, strict=True)
    )


@dataclass
class DatiMrz:
    formato: str  # "TD1" o "TD3"
    tipo_documento: str  # "C..." carta d'identita', "P..." passaporto
    stato_emittente: str
    numero_documento: str
    cognome: str
    nome: str
    data_nascita: date | None
    sesso: str | None  # "M", "F" o None
    data_scadenza: date | None
    nazionalita: str
    righe: list[str]
    # Campi la cui cifra di controllo non torna
    errori: list[str] = field(default_factory=list)

    @property
    def valida(self) -> bool:
        return not self.errori and bool(self.cognome)

    @property
    def carta_identita(self) -> bool:
        return self.tipo_documento.startswith(("C", "I", "A"))


def _verifica(errori: list[str], nome: str, dati: str, controllo: str) -> None:
    if controllo == "<" and set(dati) <= {"<"}:
        return
    if cifra_controllo(dati) != controllo:
        errori.append(nome)


def leggi_td1(righe: list[str], oggi: date) -> DatiMrz:
    r1, r2, r3 = righe
    errori: list[str] = []
    controllo_numero = _controllo(r1[14])
    formato = FORMATI_NUMERO.get((r1[0], _alfabetico(r1[2:5])))
    numero = _numero(r1[5:14], formato, controllo_numero)
    _verifica(errori, "numero_documento", numero, controllo_numero)
    nascita, c_nascita = _numerico(r2[0:6]), _controllo(r2[6])
    scadenza, c_scadenza = _numerico(r2[8:14]), _controllo(r2[14])
    _verifica(errori, "data_nascita", nascita, c_nascita)
    _verifica(errori, "data_scadenza", scadenza, c_scadenza)
    composito = (
        numero + r1[14] + r1[15:30] + nascita + c_nascita + scadenza + c_scadenza + r2[18:29]
    )
    if not errori:
        _verifica(errori, "composito", composito, _controllo(r2[29]))
    cognome, nome = _nominativo(r3)
    return DatiMrz(
        formato="TD1",
        tipo_documento=_alfabetico(r1[0:2]).rstrip("<"),
        stato_emittente=_alfabetico(r1[2:5]),
        numero_documento=numero.rstrip("<"),
        cognome=cognome,
        nome=nome,
        data_nascita=_data(nascita, False, oggi),
        sesso=r2[7] if r2[7] in "MF" else None,
        data_scadenza=_data(scadenza, True, oggi),
        nazionalita=_alfabetico(r2[15:18]),
        righe=list(righe),
        errori=errori,
    )


def leggi_td3(righe: list[str], oggi: date) -> DatiMrz:
    r1, r2 = righe
    errori: list[str] = []
    controllo_numero = _controllo(r2[9])
    formato = FORMATI_NUMERO.get((r1[0], _alfabetico(r1[2:5])))
    numero = _numero(r2[0:9], formato, controllo_numero)
    _verifica(errori, "numero_documento", numero, controllo_numero)
    nascita, c_nascita = _numerico(r2[13:19]), _controllo(r2[19])
    scadenza, c_scadenza = _numerico(r2[21:27]), _controllo(r2[27])
    _verifica(errori, "data_nascita", nascita, c_nascita)
    _verifica(errori, "data_scadenza", scadenza, c_scadenza)
    composito = numero + r2[9] + nascita + c_nascita + scadenza + c_scadenza + r2[28:42] + r2[42]
    if not errori:
        _verifica(errori, "composito", composito, _controllo(r2[43]))
    cognome, nome = _nominativo(r1[5:44])
    return DatiMrz(
        formato="TD3",
        tipo_documento=_alfabetico(r1[0:2]).rstrip("<"),
        stato_emittente=_alfabetico(r1[2:5]),
        numero_documento=numero.rstrip("<"),
        cognome=cognome,
        nome=nome,
        data_nascita=_data(nascita, False, oggi),
        sesso=r2[20] if r2[20] in "MF" else None,
        data_scadenza=_data(scadenza, True, oggi),
        nazionalita=_alfabetico(r2[10:13]),
        righe=list(righe),
        errori=errori,
    )


def _normalizza(riga: str) -> str:
    riga = riga.upper().replace(" ", "").replace("«", "<").replace("‹", "<")
    riga = re.sub(r"[^A-Z0-9<]", "", riga)
    # Il riempitivo "<" viene spesso letto come K
    return re.sub(r"(?<=<)K+(?=<)", lambda m: "<" * len(m[0]), riga)


def _adatta(riga: str, lunghezza: int) -> str:
    return riga[:lunghezza].ljust(lunghezza, "<")


def cerca(testo: str, oggi: date) -> DatiMrz | None:
    """Trova la MRZ in un testo OCR. Preferisce la lettura con tutte le cifre di controllo."""
    righe = [_normalizza(r) for r in testo.splitlines()]
    righe = [r for r in righe if len(r) >= 10 and "<" in r]
    candidati: list[DatiMrz] = []
    for i in range(len(righe)):
        terna = righe[i : i + 3]
        # La riga del nominativo finisce col riempitivo, che l'OCR a volte accorcia
        if (
            len(terna) == 3
            and all(27 <= len(r) <= 33 for r in terna[:2])
            and 10 <= len(terna[2]) <= 33
        ):
            candidati.append(leggi_td1([_adatta(r, 30) for r in terna], oggi))
        coppia = righe[i : i + 2]
        if len(coppia) == 2 and 20 <= len(coppia[0]) <= 47 and 40 <= len(coppia[1]) <= 47:
            candidati.append(leggi_td3([_adatta(r, 44) for r in coppia], oggi))
    if not candidati:
        return None
    return min(candidati, key=lambda d: len(d.errori))
