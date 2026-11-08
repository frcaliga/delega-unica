"""Lettura dei dati da una copia del documento d'identita', tutta in locale (Tesseract).

Dalla MRZ (retro della CIE, pagina dati del passaporto) si ricavano cognome, nome, data di
nascita, sesso, numero e scadenza, accettati solo se tornano le cifre di controllo. Dal testo
stampato si cerca anche il codice fiscale (retro della CIE, tessera sanitaria), accettato solo se
il carattere di controllo e' giusto. Le immagini non escono dalla piattaforma.

I dati letti sono una proposta: l'operatore li controlla sul documento originale.
"""

import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from itertools import product

from PIL import Image, ImageOps

from app.domain import codice_fiscale
from app.domain.mrz import DatiMrz, cerca

MAX_PAGINE = 4
LARGHEZZA_OCR = 2000
WHITELIST_MRZ = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<"
CF_NEL_TESTO = re.compile(codice_fiscale.MODELLO_PF)


class LetturaNonDisponibile(Exception):
    pass


@dataclass
class Lettura:
    mrz: DatiMrz | None = None
    codice_fiscale: str | None = None
    avvisi: list[str] = field(default_factory=list)

    @property
    def trovata(self) -> bool:
        return self.mrz is not None and self.mrz.valida


def immagini(dati: bytes, tipo: str) -> list[Image.Image]:
    if tipo == "application/pdf":
        import pypdfium2

        pdf = pypdfium2.PdfDocument(dati)
        try:
            return [
                pdf[i].render(scale=300 / 72).to_pil() for i in range(min(len(pdf), MAX_PAGINE))
            ]
        finally:
            pdf.close()
    immagine = Image.open(io.BytesIO(dati))
    return [ImageOps.exif_transpose(immagine)]


def _prepara(immagine: Image.Image) -> Image.Image:
    grigio = ImageOps.autocontrast(immagine.convert("L"))
    if grigio.width < LARGHEZZA_OCR:
        fattore = LARGHEZZA_OCR / grigio.width
        grigio = grigio.resize((LARGHEZZA_OCR, round(grigio.height * fattore)), Image.LANCZOS)
    return grigio


def _ocr(immagine: Image.Image, config: str) -> str:
    import pytesseract

    try:
        return pytesseract.image_to_string(immagine, lang="eng", config=config, timeout=30)
    except pytesseract.TesseractNotFoundError as exc:
        raise LetturaNonDisponibile("Tesseract non installato") from exc
    except RuntimeError:  # timeout
        return ""


def _cerca_mrz(immagine: Image.Image, oggi: date) -> DatiMrz | None:
    """Piu' letture della stessa immagine; vince il risultato piu' frequente tra i migliori.

    Le cifre di controllo coprono numero e date ma non il nominativo: per cognome e nome
    decide la maggioranza delle letture.
    """
    candidati: list[DatiMrz] = []
    # La MRZ sta in basso: la parte inferiore e l'immagine intera, anche capovolta
    for ruotata in (immagine, immagine.rotate(180)):
        basso = ruotata.crop((0, int(ruotata.height * 0.55), ruotata.width, ruotata.height))
        for parte, psm in product((basso, ruotata), (6, 11)):
            testo = _ocr(parte, f"--psm {psm} -c tessedit_char_whitelist={WHITELIST_MRZ}")
            if trovata := cerca(testo, oggi):
                candidati.append(trovata)
        if any(c.valida for c in candidati):
            break
    if not candidati:
        return None
    minimo = min(len(c.errori) for c in candidati)
    migliori = [c for c in candidati if len(c.errori) == minimo]

    def chiave(c: DatiMrz) -> tuple:
        return (c.numero_documento, c.data_nascita, c.data_scadenza, c.sesso)

    gruppo = Counter(chiave(c) for c in migliori).most_common(1)[0][0]
    scelti = [c for c in migliori if chiave(c) == gruppo]
    esito = scelti[0]
    esito.cognome = Counter(c.cognome for c in scelti).most_common(1)[0][0]
    esito.nome = Counter(c.nome for c in scelti).most_common(1)[0][0]
    return esito


def _cerca_codice_fiscale(immagine: Image.Image) -> str | None:
    testo = _ocr(immagine, "--psm 11").upper().replace(" ", "")
    for candidato in CF_NEL_TESTO.findall(testo):
        if codice_fiscale.valida(candidato):
            return candidato
    return None


def leggi(dati: bytes, tipo: str, oggi: date) -> Lettura:
    try:
        pagine = [_prepara(i) for i in immagini(dati, tipo)]
    except Exception:  # file illeggibile: la copia si conserva comunque
        return Lettura(avvisi=["Il file non e' un'immagine o un PDF leggibile"])
    esito = Lettura()
    for pagina in pagine:
        if not esito.trovata:
            trovata = _cerca_mrz(pagina, oggi)
            if trovata and (esito.mrz is None or len(trovata.errori) < len(esito.mrz.errori)):
                esito.mrz = trovata
        if esito.codice_fiscale is None:
            esito.codice_fiscale = _cerca_codice_fiscale(pagina)
        if esito.trovata and esito.codice_fiscale:
            break
    if esito.mrz is None:
        esito.avvisi.append(
            "Zona a lettura ottica non trovata: serve il retro della carta d'identita' "
            "elettronica o la pagina dati del passaporto, ben a fuoco"
        )
    elif not esito.mrz.valida:
        esito.avvisi.append(
            "Zona a lettura ottica letta in modo incerto (cifre di controllo errate): "
            "controllare i dati sul documento"
        )
    return esito
