"""Immagini sintetiche di documenti d'identita' per i test della lettura (nessun dato reale)."""

import io
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from app.services.ocr_documenti import mrz_check as cifra_controllo

FONT_MONO = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"


def righe_cie(
    numero: str = "CA00000AA",
    cognome: str = "ROSSI",
    nome: str = "MARIO",
    nascita: date = date(1980, 1, 1),
    sesso: str = "M",
    scadenza: date = date(2031, 1, 1),
) -> list[str]:
    n = f"{nascita:%y%m%d}"
    s = f"{scadenza:%y%m%d}"
    r1 = ("C<" + "ITA" + numero + cifra_controllo(numero)).ljust(30, "<")
    r2 = n + cifra_controllo(n) + sesso + s + cifra_controllo(s) + "ITA" + "<" * 11
    composito = r1[5:30] + r2[0:7] + r2[8:15] + r2[18:29]
    r2 += cifra_controllo(composito)
    r3 = (cognome.replace(" ", "<") + "<<" + nome.replace(" ", "<")).ljust(30, "<")[:30]
    return [r1, r2, r3]


def _font(dimensione: int):
    try:
        return ImageFont.truetype(FONT_MONO, dimensione)
    except OSError:
        return ImageFont.load_default(dimensione)


def retro_cie(righe: list[str], codice_fiscale: str | None = None, formato: str = "PNG") -> bytes:
    img = Image.new("RGB", (1020, 640), (226, 232, 240))
    disegno = ImageDraw.Draw(img)
    testo = _font(26)
    disegno.text((40, 40), "REPUBBLICA ITALIANA", font=testo, fill=(40, 40, 40))
    disegno.text((520, 40), "CARTA DI IDENTITA", font=testo, fill=(40, 40, 40))
    if codice_fiscale:
        disegno.text((40, 110), "CODICE FISCALE / FISCAL CODE", font=_font(20), fill=(70, 70, 70))
        disegno.text((40, 140), codice_fiscale, font=testo, fill=(20, 20, 20))
    disegno.rectangle((0, 430, 1020, 640), fill=(250, 250, 250))
    mono = _font(30)
    for i, riga in enumerate(righe):
        disegno.text((40, 455 + i * 55), riga, font=mono, fill=(0, 0, 0))
    img = img.filter(ImageFilter.GaussianBlur(0.6))
    buf = io.BytesIO()
    img.save(buf, formato)
    return buf.getvalue()
