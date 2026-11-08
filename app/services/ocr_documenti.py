"""
Motore OCR dei documenti d'identita', ripreso dal progetto ocr di Francesco Caligaris
(estrai_documenti.py) e adattato alla piattaforma: legge da bytes invece che da file, senza CLI.

Documenti: CIE 3.0 (fronte e retro), carta d'identita' cartacea, passaporto, tessera sanitaria
(TEAM). Accetta scansioni e foto da telefono: la carta viene ritagliata e raddrizzata.

Invece di fidarsi di una sola lettura:
  1. genera piu' varianti di ogni pagina (scala, binarizzazione, testo nero isolato);
  2. esegue Tesseract su ciascuna;
  3. valida i candidati (cifre di controllo MRZ ICAO 9303, carattere di controllo del codice
     fiscale, Luhn per il numero TEAM, coerenza delle date);
  4. sceglie per voto di maggioranza tra i candidati validi.
"""

from __future__ import annotations

import csv
import itertools
import os
import re
import sys
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

os.environ.setdefault("OMP_THREAD_LIMIT", "1")  # Tesseract multi-thread è più lento qui

import cv2
import numpy as np
import pytesseract

try:
    import pypdfium2 as pdfium
except ImportError:  # pragma: no cover
    pdfium = None

DPI = 300
TESS_LANG = "eng"  # 'ita' se installato non migliora molto: i campi sono maiuscoli


# =============================================================================
# 1. Caricamento pagine
# =============================================================================
def load_pages(path: Path) -> list[np.ndarray]:
    """Restituisce le pagine del file come immagini RGB (numpy)."""
    if path.suffix.lower() == ".pdf":
        if pdfium is None:
            raise RuntimeError("Installare pypdfium2 per leggere i PDF")
        pdf = pdfium.PdfDocument(str(path))
        return [np.array(p.render(scale=DPI / 72).to_pil().convert("RGB")) for p in pdf]
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError(f"Impossibile leggere {path}")
    return [prepare_photo(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))]


# ---- Foto da telefono -------------------------------------------------------
# Le scansioni sono già ritagliate sulla carta (formato ID-1, 85.6 x 54 mm) a
# 300 DPI: le foto vengono riportate alla stessa geometria, così il resto della
# pipeline (scale, soglie, distanze tra etichette) funziona senza modifiche.
CARD_W, CARD_H = round(85.6 / 25.4 * DPI), round(54 / 25.4 * DPI)


def _order_corners(pts: np.ndarray) -> np.ndarray:
    """Vertici in ordine: alto-sx, alto-dx, basso-dx, basso-sx."""
    pts = pts.reshape(4, 2).astype(np.float32)
    s, d = pts.sum(1), np.diff(pts, axis=1).ravel()
    return np.array(
        [pts[s.argmin()], pts[d.argmin()], pts[s.argmax()], pts[d.argmax()]], np.float32
    )


def find_card(rgb: np.ndarray) -> np.ndarray | None:
    """Cerca nella foto il quadrilatero della carta (rapporto lati ~1.58)."""
    f = 1000 / max(rgb.shape[:2])
    small = cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    area_img = small.shape[0] * small.shape[1]
    lum = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2LAB)[:, :, 0], (7, 7), 0)
    masks = [
        cv2.threshold(lum, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1],  # carta chiara
        cv2.dilate(cv2.Canny(lum, 30, 90), np.ones((5, 5), np.uint8)),
    ]  # bordi
    best = None
    for m in masks:
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((15, 15), np.uint8))
        for c in cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0]:
            a = cv2.contourArea(c)
            if not 0.08 * area_img < a < 0.97 * area_img:
                continue
            hull = cv2.convexHull(c)
            q = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
            if len(q) != 4:  # angoli arrotondati o in ombra: rettangolo minimo
                q = cv2.boxPoints(cv2.minAreaRect(hull))
            q = _order_corners(q)
            w, h = np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[3] - q[0])
            ratio = max(w, h) / max(1.0, min(w, h))
            fill = a / max(1.0, cv2.contourArea(q))
            if 1.35 < ratio < 1.85 and fill > 0.85 and (best is None or a * fill > best[0]):
                best = (a * fill, q / f)
    return None if best is None else best[1]


def prepare_photo(rgb: np.ndarray) -> np.ndarray:
    """Foto: ritaglia la carta e corregge la prospettiva. Scansioni: invariate."""
    h, w = rgb.shape[:2]
    if max(h, w) < 1.6 * CARD_W:  # già alla risoluzione di una scansione
        return rgb
    # pagina scansionata o fotocopia (sfondo quasi tutto bianco): non è la foto di una
    # carta appoggiata su un piano, quindi niente ritaglio
    bianco = (rgb.min(axis=2) > 190).mean()
    q = find_card(rgb) if bianco < 0.4 else None
    if q is None:  # nessuna carta da ritagliare: al massimo un A4 a 300 DPI
        if bianco >= 0.4 and max(h, w) <= 3600:
            return rgb
        f = (3508 if bianco >= 0.4 else 2 * CARD_W) / max(h, w)
        return cv2.resize(rgb, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
    if np.linalg.norm(q[3] - q[0]) > np.linalg.norm(q[1] - q[0]):  # carta in verticale
        q = np.roll(q, -1, axis=0)
    dst = np.array([[0, 0], [CARD_W, 0], [CARD_W, CARD_H], [0, CARD_H]], np.float32)
    M = cv2.getPerspectiveTransform(q, dst)
    return cv2.warpPerspective(rgb, M, (CARD_W, CARD_H), flags=cv2.INTER_AREA)


# =============================================================================
# 2. Pre-elaborazione e OCR
# =============================================================================
def make_variants(rgb: np.ndarray) -> list[tuple[str, np.ndarray, float]]:
    """Varianti dell'immagine da passare all'OCR: (nome, immagine, fattore scala)."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    # Il testo dei documenti è nero su sfondi chiari/colorati: il massimo dei
    # tre canali isola il nero meglio della scala di grigi.
    darkest = rgb.max(axis=2).astype(np.uint8)
    # carta ritagliata (scansione o foto raddrizzata): ingrandisci 3x; pagina intera
    # (es. fotocopia A4 di una carta cartacea): il testo è già abbastanza grande
    k = 3.0 if max(rgb.shape[:2]) < 1.6 * CARD_W else 1.3
    up = lambda im: cv2.resize(im, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    g = up(gray)
    otsu = cv2.threshold(
        cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
    )[1]
    d = up(darkest)
    dark120 = cv2.medianBlur(np.where(d < 120, 0, 255).astype(np.uint8), 3)
    dark90 = cv2.medianBlur(np.where(d < 90, 0, 255).astype(np.uint8), 3)
    variants = [("gray", g, k), ("otsu", otsu, k), ("dark120", dark120, k), ("dark90", dark90, k)]
    if k < 3:
        # pagina intera (fotocopie): soglia locale e via puntini/retini, cioè le componenti
        # più basse di un carattere; recupera il testo chiaro su sfondo tratteggiato
        b = cv2.adaptiveThreshold(
            cv2.GaussianBlur(g, (3, 3), 0),
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            51,
            20,
        )
        n, lab, st, _ = cv2.connectedComponentsWithStats(255 - b, 8)
        alte = [i for i in range(1, n) if 12 * k <= st[i][3] < 200 * k]
        variants.append(("pulita", np.where(np.isin(lab, alte), 0, 255).astype(np.uint8), k))
    return variants


@dataclass
class Word:
    text: str
    conf: float
    x0: float
    y0: float
    x1: float
    y1: float
    variant: str = ""

    @property
    def yc(self) -> float:
        return (self.y0 + self.y1) / 2

    @property
    def h(self) -> float:
        return self.y1 - self.y0


@dataclass
class PageOCR:
    """Risultati OCR di una pagina su tutte le varianti."""

    rgb: np.ndarray
    words: dict[str, list[Word]] = field(default_factory=dict)  # per variante
    texts: dict[str, str] = field(default_factory=dict)  # testo per variante

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    @property
    def height(self) -> int:
        return self.rgb.shape[0]

    def all_lines(self) -> list[str]:
        out = []
        for t in self.texts.values():
            out += [l.strip() for l in t.splitlines() if l.strip()]
        return out

    def full_text(self) -> str:
        return "\n".join(self.texts.values())


def ocr_words(img: np.ndarray, scale: float, variant: str, psm: int = 11) -> tuple[list[Word], str]:
    """Una sola chiamata a Tesseract: parole con coordinate + testo ricostruito per righe."""
    data = pytesseract.image_to_data(
        img, lang=TESS_LANG, config=f"--psm {psm}", output_type=pytesseract.Output.DICT
    )
    words, lines = [], {}
    for i, txt in enumerate(data["text"]):
        txt = txt.strip()
        conf = float(data["conf"][i])
        if not txt or conf < 0:
            continue
        x, y, w, h = (data[c][i] / scale for c in ("left", "top", "width", "height"))
        words.append(Word(txt, conf, x, y, x + w, y + h, variant))
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(txt)
    return words, "\n".join(" ".join(v) for v in lines.values())


def ocr_page(rgb: np.ndarray) -> PageOCR:
    page = PageOCR(rgb)
    for name, img, k in make_variants(rgb):
        page.words[name], page.texts[name] = ocr_words(img, k, name)
    return page


def _clip(rgb: np.ndarray, box) -> np.ndarray:
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = box
    x0, y0 = int(max(0, x0)), int(max(0, y0))
    x1, y1 = int(min(w, x1)), int(min(h, y1))
    return rgb[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else np.zeros((0, 0, 3), np.uint8)


def ocr_crop(
    rgb: np.ndarray,
    box,
    whitelist: str,
    psm: int = 6,
    scales: tuple[float, ...] = (1.0, 1.5, 2.0),
    psms: tuple[int, ...] | None = None,
) -> list[str]:
    """OCR mirato con alfabeto ristretto (es. MRZ, sole cifre) su più scale/binarizzazioni."""
    crop = _clip(rgb, box)
    if crop.size == 0:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    out = []
    for k in scales:
        g = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC) if k != 1 else gray
        o = cv2.threshold(
            cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU
        )[1]
        for im in (g, o):
            for p in psms or (psm,):
                cfg = f"--psm {p} -c tessedit_char_whitelist={whitelist}"
                out.append(pytesseract.image_to_string(im, lang=TESS_LANG, config=cfg))
    return out


def ocr_region(
    rgb: np.ndarray, box, thresholds=(60, 80, 100, 120), scales=(2.0, 3.0), psm: int = 7
) -> list[str]:
    """OCR di una singola riga isolando il testo nero (utile sopra ologrammi/sfondi)."""
    crop = _clip(rgb, box)
    if crop.size == 0:
        return []
    mx = crop.max(axis=2).astype(np.uint8)
    out = []
    for k in scales:
        g = cv2.resize(mx, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
        for t in thresholds:
            b = cv2.medianBlur(np.where(g < t, 0, 255).astype(np.uint8), 3)
            b = cv2.copyMakeBorder(b, 20, 20, 20, 20, cv2.BORDER_CONSTANT, value=255)
            out.append(
                pytesseract.image_to_string(b, lang=TESS_LANG, config=f"--psm {psm}").strip()
            )
    return out


def label_boxes(page: PageOCR, keyword: str, thr: float = 0.8) -> list[Word]:
    """Posizioni (deduplicate) di un'etichetta trovate nelle varie varianti OCR."""
    found: list[Word] = []
    for words in page.words.values():
        for w in find_labels(words, keyword, thr):
            if not any(abs(w.x0 - f.x0) < 10 and abs(w.y0 - f.y0) < 10 for f in found):
                found.append(w)
    return found


# =============================================================================
# 3. Utilità: voto, normalizzazione, ricerca posizionale
# =============================================================================
def vote(
    cands: Iterable[str | None], valid: Callable[[str], bool] | None = None
) -> tuple[str | None, bool]:
    """Restituisce (valore più frequente, validato?). Preferisce i candidati validi."""
    cands = [c for c in cands if c]
    if not cands:
        return None, False
    if valid:
        good = [c for c in cands if valid(c)]
        if good:
            return Counter(good).most_common(1)[0][0], True
    return Counter(cands).most_common(1)[0][0], valid is None


def clean_upper(s: str) -> str:
    s = s.upper().replace("’", "'").replace("`", "'")
    s = re.sub(r"[^A-ZÀ-Ý' \-]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" '-")


def fuzzy_eq(a: str, b: str, thr: float = 0.8) -> bool:
    return SequenceMatcher(None, a.upper(), b.upper()).ratio() >= thr


def find_labels(words: list[Word], keyword: str, thr: float = 0.8) -> list[Word]:
    kw = keyword.upper()
    return [
        w
        for w in words
        if len(w.text) >= len(kw) - 2 and fuzzy_eq(re.sub(r"[^A-Za-z]", "", w.text), kw, thr)
    ]


def _good(w: Word) -> bool:
    return w.conf >= 20 and re.search(r"[A-Za-z0-9]", w.text) is not None


def text_below(words: list[Word], label: Word, x_min: float, x_max: float, max_gap: float) -> str:
    """Prima riga di testo sotto l'etichetta, limitata alla colonna [x_min, x_max]."""
    cand = [
        w
        for w in words
        if _good(w)
        and w.y0 > label.y1 - 2
        and w.y0 - label.y1 < max_gap
        and x_min <= (w.x0 + w.x1) / 2 <= x_max
        and w.h > 0.6 * label.h
    ]
    if not cand:
        return ""
    first = min(cand, key=lambda w: w.y0)
    row = [w for w in cand if abs(w.yc - first.yc) < 0.6 * first.h]
    return " ".join(w.text for w in sorted(row, key=lambda w: w.x0))


# =============================================================================
# 4. Validatori
# =============================================================================
# ---- Codice fiscale ---------------------------------------------------------
_CF_ODD = dict(
    zip(
        "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ",
        [
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
    )
)
_CF_EVEN = {
    c: (int(c) if c.isdigit() else ord(c) - 65) for c in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
}
_OMOCODIA = dict(zip("LMNPQRSTUV", "0123456789"))
_MONTHS = "ABCDEHLMPRST"
_CF_DIGIT_POS = (6, 7, 9, 10, 12, 13, 14)
_TO_DIGIT = str.maketrans("OoDQIl|ZSB", "0000111258")
_TO_ALPHA = str.maketrans("0125861", "OIZSBGI")


def cf_check_char(cf15: str) -> str:
    s = sum(_CF_ODD[c] if i % 2 == 0 else _CF_EVEN[c] for i, c in enumerate(cf15))
    return chr(65 + s % 26)


_OMOCODIA_ORDINE = (14, 13, 12, 10, 9, 7, 6)  # ordine in cui l'omocodia sostituisce le cifre


def cf_valid(cf: str) -> bool:
    if not (
        re.fullmatch(
            r"[A-Z]{6}[0-9LMNPQRSTUV]{2}[A-Z][0-9LMNPQRSTUV]{2}[A-Z]"
            r"[0-9LMNPQRSTUV]{3}[A-Z]",
            cf,
        )
        and cf[8] in _MONTHS
        and cf_check_char(cf[:15]) == cf[15]
    ):
        return False
    # in un CF omocodico le lettere sostituiscono le cifre partendo da destra: scarta le
    # sequenze casuali che passano il controllo (es. "COMUNE DI MUNICIPALITY")
    sost = [cf[i].isalpha() for i in _OMOCODIA_ORDINE]
    if sost != sorted(sost, reverse=True) or sum(sost) > 3:
        return False
    # giorno di nascita (1-31, +40 per le donne) e data esistente
    d = "".join(_OMOCODIA.get(c, c) if i in _CF_DIGIT_POS else c for i, c in enumerate(cf))
    giorno = int(d[9:11]) - (40 if int(d[9:11]) > 40 else 0)
    try:
        date(2000, _MONTHS.index(d[8]) + 1, giorno)  # anno bisestile: accetta il 29/02
    except ValueError:
        return False
    return True


def cf_fix(raw: str) -> str | None:
    """Corregge gli errori OCR tipici (O/0, I/1, ...) in base alla posizione."""
    s = re.sub(r"[^A-Za-z0-9|]", "", raw).upper()
    if len(s) != 16:
        return None
    out = []
    for i, c in enumerate(s):
        if i in _CF_DIGIT_POS:
            out.append(c if c in _OMOCODIA else c.translate(_TO_DIGIT))
        else:
            out.append(c.translate(_TO_ALPHA))
    return "".join(out)


def cf_candidates(lines: Iterable[str]) -> list[str]:
    out = []
    for line in lines:
        compact = re.sub(r"[\s_\-.,:;\"“”'`|]", "", line.upper())
        for i in range(0, max(0, len(compact) - 15)):
            fixed = cf_fix(compact[i : i + 16])
            if fixed and cf_valid(fixed):
                out.append(fixed)
    return out


def cf_decode(cf: str) -> dict:
    """Ricava data di nascita, sesso e codice catastale del comune dal CF."""
    d = "".join(_OMOCODIA.get(c, c) if i in _CF_DIGIT_POS else c for i, c in enumerate(cf))
    yy, mm, dd = int(d[6:8]), _MONTHS.index(d[8]) + 1, int(d[9:11])
    sesso = "F" if dd > 40 else "M"
    dd = dd - 40 if dd > 40 else dd
    yyyy = 2000 + yy if 2000 + yy <= date.today().year else 1900 + yy
    return {
        "data_nascita": f"{dd:02d}/{mm:02d}/{yyyy}",
        "sesso": sesso,
        "codice_catastale": d[11:15],
    }


def _cf_cons_vow(s: str) -> tuple[str, str]:
    s = re.sub(r"[^A-Z]", "", s.upper().translate(str.maketrans("ÀÈÉÌÒÙ", "AEEIOU")))
    return "".join(c for c in s if c not in "AEIOU"), "".join(c for c in s if c in "AEIOU")


def cf_surname_code(s: str) -> str:
    c, v = _cf_cons_vow(s)
    return (c + v + "XXX")[:3]


def cf_name_code(s: str) -> str:
    c, v = _cf_cons_vow(s)
    if len(c) >= 4:
        return c[0] + c[2] + c[3]
    return (c + v + "XXX")[:3]


# ---- MRZ (ICAO 9303) --------------------------------------------------------
def mrz_check(s: str) -> str:
    val = lambda c: int(c) if c.isdigit() else (ord(c) - 55 if c.isalpha() else 0)
    return str(sum(val(c) * (7, 3, 1)[i % 3] for i, c in enumerate(s)) % 10)


# ---- Luhn (numero tessera TEAM) ---------------------------------------------
def luhn_valid(num: str) -> bool:
    if not num.isdigit():
        return False
    tot = 0
    for i, c in enumerate(reversed(num)):
        d = int(c) * (2 if i % 2 else 1)
        tot += d - 9 if d > 9 else d
    return tot % 10 == 0


_CONFUSABLE = {
    "3": "58",
    "5": "36",
    "8": "036",
    "6": "58",
    "9": "0",
    "0": "89",
    "1": "7",
    "7": "1",
    "2": "7",
}


def luhn_repair(num: str) -> list[str]:
    """Correzioni a una cifra tra cifre facilmente confondibili che rendono valido Luhn."""
    out = []
    for i, c in enumerate(num):
        for alt in _CONFUSABLE.get(c, ""):
            cand = num[:i] + alt + num[i + 1 :]
            if luhn_valid(cand):
                out.append(cand)
    return out


# ---- Date -------------------------------------------------------------------
def parse_date(s: str) -> date | None:
    m = re.fullmatch(r"(\d{2})[./](\d{2})[./](\d{4})", s)
    if not m:
        return None
    try:
        return date(int(m[3]), int(m[2]), int(m[1]))
    except ValueError:
        return None


def plausible_birth(s: str) -> bool:
    d = parse_date(s)
    return d is not None and d.year >= 1900 and d <= date.today()


def find_dates(text: str) -> list[str]:
    t = text.translate(str.maketrans("Oo", "00"))
    out = []
    for m in re.finditer(r"(\d{2})\s?([./])\s?(\d{2})\s?[./]\s?(\d{4})", t):
        s = f"{m[1]}/{m[3]}/{m[4]}"
        if parse_date(s):
            out.append(s)
    return out


# =============================================================================
# 5. Carta d'Identità Elettronica
# =============================================================================
_DIGITLIKE = set("0123456789OIDQSZB")


def _mrz_line1(s: str) -> list[str]:
    """Numero documento dalla riga 1 (tipo, ITA, numero[9], check), tollerando caratteri spuri."""
    out = []
    m = re.search(r"ITA", s)
    if not m:
        return out
    tail = re.split(r"<{2,}", s[m.end() :])[0][:13]
    for n in range(10, min(len(tail), 13) + 1):
        seg = tail[:n]
        for drop in itertools.combinations(range(n - 1), n - 10):
            cand = "".join(c for i, c in enumerate(seg) if i not in drop)
            num, chk = cand[:9], cand[9].translate(_TO_DIGIT)
            # formato CIE: 2 lettere, 5 cifre, 2 lettere
            if re.fullmatch(r"[A-Z0-9]{2}[0-9OIDQSZB]{5}[A-Z0-9]{2}", num):
                num = (
                    num[:2].translate(_TO_ALPHA)
                    + num[2:7].translate(_TO_DIGIT)
                    + num[7:].translate(_TO_ALPHA)
                )
            if chk.isdigit() and mrz_check(num) == chk:
                out.append(num)
    return out


def _mrz_line2(s: str) -> list[tuple]:
    """Nascita/sesso/scadenza/nazionalità: cerca la sottosequenza che soddisfa i check digit."""
    out = []
    for m in re.finditer(r"(?=(ITA|[A-Z]{3})<)", s):
        pre = s[: m.start()][-19:]
        if len(pre) < 15:
            continue
        for drop in itertools.combinations(range(len(pre)), len(pre) - 15):
            c = "".join(ch for i, ch in enumerate(pre) if i not in drop)
            if c[7] not in "MF<" or not all(ch in _DIGITLIKE for i, ch in enumerate(c) if i != 7):
                continue
            c = c[:7].translate(_TO_DIGIT) + c[7] + c[8:].translate(_TO_DIGIT)
            if mrz_check(c[:6]) == c[6] and mrz_check(c[8:14]) == c[14]:
                out.append((c[:6], c[7], c[8:14], m[1]))
        if out:
            break
    return list(set(out))


def _riempitivo(s: str) -> str:
    """Il riempitivo "<" letto come K tra altri "<" (es. MARIO<K<K<<) torna "<"."""
    return re.sub(r"(?<=<)K+(?=<)", lambda m: "<" * len(m[0]), s)


def parse_mrz(lines: list[str]) -> dict:
    """Raccoglie i voti per i campi MRZ (TD1, 3 righe x 30) da più letture OCR."""
    votes: dict[str, list] = {"num": [], "l2": [], "nomi": []}
    for l in lines:
        s = _riempitivo(re.sub(r"\s", "", l.upper()).replace("«", "<"))
        if len(s) < 20:
            continue
        if re.match(r"^[A-Z][<K]", s) and "ITA" in s[:8]:
            votes["num"] += _mrz_line1(s)
        elif re.search(r"[A-Z]{3}<", s) and sum(ch.isdigit() for ch in s) >= 8:
            votes["l2"] += _mrz_line2(s)
        m = re.fullmatch(r"([A-Z]+(?:<[A-Z]+)*)<<([A-Z]+(?:<[A-Z]+)*)[<K]*", s)
        if m and not re.search(r"\d", s):
            votes["nomi"].append((m[1].replace("<", " "), m[2].replace("<", " ")))
    res: dict = {}
    best = lambda v: Counter(v).most_common(1)[0][0] if v else None
    if votes["num"]:
        res["num"] = best(votes["num"])
    if votes["l2"]:
        b, sex, e, naz = best(votes["l2"])
        cur = date.today().year % 100
        res["nascita"] = f"{b[4:6]}/{b[2:4]}/{(2000 if int(b[:2]) <= cur else 1900) + int(b[:2])}"
        res["scadenza"] = f"{e[4:6]}/{e[2:4]}/{2000 + int(e[:2])}"
        res["sesso"], res["nazionalita"] = sex, naz
        for k in ("nascita", "scadenza"):  # check digit giusto ma data impossibile (es. 31/02)
            if not parse_date(res[k]):
                del res[k]
    if votes["nomi"]:
        res["cognome"], res["nome"] = best(votes["nomi"])
    return res


def snap_comune(nome: str | None, sigla: str | None, comuni: dict | None) -> str | None:
    """Con l'elenco ISTAT corregge la lettura del comune (es. TORING -> TORINO)."""
    if not nome or not comuni:
        return nome
    nomi = {n.upper() for n, sg in comuni.values() if not sigla or sg.upper() == sigla}
    if nome in nomi:
        return nome
    best = max(nomi, key=lambda n: SequenceMatcher(None, n, nome).ratio(), default=None)
    return best if best and SequenceMatcher(None, best, nome).ratio() >= 0.8 else nome


_CIE_LABELS = (
    "COGNOME",
    "SURNAME",
    "NOME",
    "NAME",
    "MUNICIPALITY",
    "SESSO",
    "SEX",
    "STATURA",
    "HEIGHT",
    "CITTADINANZA",
    "NATIONALITY",
    "EMISSIONE",
    "ISSUING",
    "SCADENZA",
    "EXPIRY",
    "LUOGO",
    "PLACE",
    "NASCITA",
    "BIRTH",
    "COMUNE",
    "FIRMA",
    "SIGNATURE",
)


def extract_cie(pages: list[PageOCR], comuni: dict | None = None) -> dict:
    out: dict = {"tipo_documento": "Carta d'Identità Elettronica"}
    notes: list[str] = []
    all_lines = [l for p in pages for l in p.all_lines()]

    # ---- MRZ: OCR generico + OCR mirato (alfabeto MRZ) sul fondo del retro ---
    mrz_texts = list(all_lines)
    for p in pages:
        if "<<" in p.full_text():
            for t in ocr_crop(
                p.rgb,
                (0, p.height * 0.62, p.width, p.height),
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
                psms=(6, 11),
            ):
                mrz_texts += [l for l in t.splitlines() if l.strip()]
    mrz = parse_mrz(mrz_texts)
    out["mrz_verificata"] = all(k in mrz for k in ("num", "nascita", "cognome"))

    # ---- Numero documento: MRZ (con check digit) e stampato sul fronte ------
    front_nums = [
        m.group(0).translate(str.maketrans("O", "0"))
        for l in all_lines
        for m in re.finditer(r"\b[A-Z]{2}[0-9O]{5}[A-Z]{2}\b", l.upper())
    ]
    if not front_nums and not mrz.get("num"):
        # numero stampato in alto a destra del fronte: rilettura mirata con alfabeto ristretto
        for p in pages[:1]:
            for t in ocr_crop(
                p.rgb,
                (p.width * 0.6, 0, p.width, p.height * 0.18),
                "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
                psms=(7, 11),
            ):
                front_nums += [
                    m.group(0).translate(str.maketrans("O", "0"))
                    for m in re.finditer(r"[A-Z]{2}[0-9O]{5}[A-Z]{2}", t.upper())
                ]
    num_front, _ = vote(front_nums)
    out["numero_documento"] = mrz.get("num") or num_front
    if mrz.get("num") and num_front and num_front != mrz["num"]:
        notes.append(f"Numero sul fronte letto come {num_front}, MRZ {mrz['num']} (usata MRZ)")

    # ---- Rilettura mirata della riga sotto un'etichetta -----------------------
    # Nelle foto la lettura a pagina intera varia molto con piccoli cambi di
    # inquadratura: rileggere la sola riga del valore, a più soglie, dà più voti.
    def rileggi(label: str, col_frac: float, thr: float = 0.8, alto: float = 2.2) -> list[str]:
        out = []
        for p in pages:
            for lab in label_boxes(p, label, thr)[:2]:
                h = lab.h
                for t in ocr_region(
                    p.rgb,
                    (
                        lab.x0 - 0.5 * h,
                        lab.y1 - 0.1 * h,
                        lab.x0 + p.width * col_frac,
                        lab.y1 + alto * h,
                    ),
                    psm=6,
                ):
                    # il riquadro può toccare l'etichetta successiva: tieni la prima riga
                    righe = [r for r in t.splitlines() if len(re.sub(r"\W", "", r)) >= 2]
                    if righe:
                        out.append(righe[0])
        return out

    def is_label(v: str) -> bool:
        """Testo composto solo da etichette (es. "NOME NAME"), non da un valore."""
        parti = [v.replace(" ", "")] + v.split()
        return any(fuzzy_eq(parti[0], e) for e in _CIE_LABELS) or all(
            any(fuzzy_eq(w, e) for e in _CIE_LABELS) for w in parti[1:] or [""]
        )

    # ---- Cognome / nome: preferisci la grafia del fronte se coerente con MRZ -
    def pick_name(mrz_val: str | None, label: str) -> str | None:
        cands = []
        for p in pages:
            for words in p.words.values():
                for lab in find_labels(words, label, 0.75):
                    v = clean_upper(
                        text_below(
                            words, lab, lab.x0 - 3 * lab.h, lab.x0 + p.width * 0.45, 4 * lab.h
                        )
                    )
                    if v:
                        cands.append(v)
        cands += [clean_upper(t) for t in rileggi(label, 0.45, 0.75)]
        cands = [c for c in cands if len(c.replace(" ", "")) >= 2 and not is_label(c)]
        key = lambda s: re.sub(r"[^A-Z]", "", s or "")
        if mrz_val:
            same = [c for c in cands if key(c) == key(mrz_val)]
            return vote(same)[0] or mrz_val
        return vote(cands)[0]

    out["cognome"] = pick_name(mrz.get("cognome"), "COGNOME")
    out["nome"] = pick_name(mrz.get("nome"), "NOME")

    # ---- Luogo e data di nascita (fronte) ----------------------------------
    luoghi, prov = [], []
    rx = re.compile(
        r"([A-Z][A-Z' .\-]{1,40}?)\s*[\({\[]\s*([A-Z]{2,})\s*[\)}\]]\s*"
        r"([0-9O]{2})[. ]*([0-9O]{2})[. ]*([0-9O]{4})"
    )
    nascite = []
    for l in all_lines + rileggi("PLACE", 0.45):
        m = rx.search(l.upper())
        if m:
            if len(clean_upper(m[1])) >= 3:
                luoghi.append(clean_upper(m[1]))
            prov.append(m[2])
            d = f"{m[3]}/{m[4]}/{m[5]}".replace("O", "0")
            if parse_date(d):
                nascite.append(d)
    out["luogo_nascita"], _ = vote(luoghi)
    out["provincia_nascita"], _ = vote(prov)
    out["luogo_nascita"] = snap_comune(out["luogo_nascita"], out["provincia_nascita"], comuni)

    front_dates = [d for p in pages[:1] for d in find_dates(p.full_text())]
    out["data_nascita"] = mrz.get("nascita") or vote(nascite, plausible_birth)[0]

    # ---- Campi posizionali: comune di rilascio, statura, residenza, atto ----
    def below_label(
        label: str, col_frac: float, gap: float, pat: str | None = None, thr: float = 0.8
    ) -> str | None:
        cands = []
        for p in pages:
            # etichetta trovata in una variante, valore cercato in tutte: nelle foto
            # capita che una variante legga solo l'etichetta e un'altra solo il valore
            for lab in label_boxes(p, label, thr):
                for words in p.words.values():
                    v = text_below(
                        words, lab, lab.x0 - 3 * lab.h, lab.x0 + p.width * col_frac, gap * lab.h
                    )
                    if pat:
                        m = re.search(pat, v.upper())
                        v = m.group(1) if m else ""
                    v = v.strip()
                    if v and not is_label(v):
                        cands.append(v)
        for t in rileggi(label, col_frac, thr):
            m = re.search(pat, t.upper()) if pat else None
            v = (m.group(1) if m else "") if pat else clean_upper(t)
            if v.strip() and not is_label(v.strip()):
                cands.append(v.strip())
        return vote(cands)[0]

    # ---- Sesso, cittadinanza, scadenza: MRZ (retro) o etichette del fronte --
    rx_date = r"(\d{2}\s?[./]\s?\d{2}\s?[./]\s?\d{4})"
    norm_date = lambda v: (find_dates(v) or [None])[0] if v else None
    out["sesso"] = mrz.get("sesso") or below_label("SEX", 0.12, 3, r"^\W*([MF])\b")
    out["cittadinanza"] = mrz.get("nazionalita") or below_label(
        "NATIONALITY", 0.15, 3, r"\b([A-Z]{3})\b"
    )
    out["data_scadenza"] = mrz.get("scadenza") or norm_date(below_label("EXPIRY", 0.2, 3, rx_date))
    if not out["data_scadenza"] and out["data_nascita"]:
        # la CIE scade il giorno del compleanno -> stessa gg/mm della nascita
        b = out["data_nascita"]
        out["data_scadenza"], _ = vote(
            d
            for d in front_dates
            if d[:5] == b[:5] and parse_date(b) and parse_date(d) > parse_date(b)
        )
    emis_front = norm_date(below_label("ISSUING", 0.2, 3, rx_date))

    # ---- Data di emissione: data del fronte diversa da nascita e scadenza ---
    emis = [d for d in front_dates if d not in (out["data_nascita"], out["data_scadenza"])]
    lo, hi = parse_date(out["data_nascita"] or ""), parse_date(out["data_scadenza"] or "")
    if lo and hi:
        emis = [d for d in emis if lo < parse_date(d) < hi and parse_date(d) <= date.today()]
    out["data_emissione"] = emis_front if emis_front in emis else vote(emis)[0]

    out["comune_rilascio"] = below_label("COMUNE", 0.35, 3, r"([A-Z][A-Z' \-]{2,})", thr=0.9)
    out["comune_rilascio"] = snap_comune(out["comune_rilascio"], None, comuni)
    out["statura_cm"] = below_label("HEIGHT", 0.15, 4, r"\b(1\d\d|2[0-3]\d)\b")

    # Residenza: riga del retro che termina con "(XX)" e non contiene date
    res_c = []
    for p in pages[1:] or pages:
        for l in p.all_lines():
            l2 = re.sub(r"[“”\"_|]", "", l.upper()).strip(" .,-")
            if (
                re.search(r"\([A-Z]{2}\)$", l2)
                and not re.search(r"\d{2}[./]\d{2}[./]\d{4}", l2)
                and len(l2) > 12
            ):
                res_c.append(re.sub(r"\s+", " ", l2))
    out["indirizzo_residenza"], _ = vote(res_c)

    atto = [
        re.sub(r"\bp[tiIl|]\b", "p1", re.sub(r"\s+", " ", m.group(0)))
        for l in all_lines
        for m in re.finditer(r"\b\d{1,5}\s+p\S{0,2}\s+\S{1,4}-\d{4}\s+\d{3,7}\b", l)
    ]
    out["estremi_atto_nascita"], _ = vote(atto)

    # ---- Codice fiscale (retro) --------------------------------------------
    out["codice_fiscale"], _ = vote(cf_candidates(all_lines))

    if not mrz:
        notes.append("MRZ non letta: dati anagrafici ricavati solo dal fronte")
    out["avvisi"] = notes
    return out


# =============================================================================
# 5b. Carta d'identità cartacea (libretto) e passaporto
# =============================================================================
_MESI = {
    "GEN": 1,
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAG": 5,
    "MAY": 5,
    "GIU": 6,
    "JUN": 6,
    "LUG": 7,
    "JUL": 7,
    "AGO": 8,
    "AUG": 8,
    "SET": 9,
    "SEP": 9,
    "OTT": 10,
    "OCT": 10,
    "NOV": 11,
    "DIC": 12,
    "DEC": 12,
}


def find_dates_any(text: str) -> list[str]:
    """Date nei formati 28-01-1958, 28.01.1958, 28/01/1958 e 03 NOV/NOV 1978."""
    t = text.upper()
    # cifre lette come lettere dentro una data (aB-01 1958 -> 8-01 1958)
    t = re.sub(
        r"[0-9OBSIZL]{1,2}\s?[-./]\s?[0-9OBSIZL]{2}\s?[-./ ]\s?[0-9OBSIZL]{4}",
        lambda m: m[0].translate(str.maketrans("OBSIZL", "085121")),
        t,
    )
    t = re.sub(r"(?<=\d{2}[-./]\d{2})\s(?=\d{4}\b)", "-", t)
    out = find_dates(re.sub(r"(?<=\d)\s?-\s?(?=\d)", "/", t))
    for m in re.finditer(r"\b([0-3O]?[0-9O])\s+([A-Z]{3})(?:\s*/\s*[A-Z]{3})?\s+([12]\d{3})\b", t):
        if m[2] in _MESI:
            d = f"{int(m[1].replace('O', '0')):02d}/{_MESI[m[2]]:02d}/{m[3]}"
            if parse_date(d):
                out.append(d)
    return out


def _split_glued(words: list[Word]) -> list[Word]:
    """'Cognome.......AICARDI' letto come una parola sola -> etichetta + valore."""
    out = []
    for w in words:
        m = re.fullmatch(r"([A-Za-z]{2,})[.,:;·]{2,}(.*)", w.text)
        if m and m[2]:
            cut = w.x0 + (w.x1 - w.x0) * len(m[1]) / len(w.text)
            out += [
                Word(m[1], w.conf, w.x0, w.y0, cut, w.y1, w.variant),
                Word(m[2], w.conf, cut, w.y0, w.x1, w.y1, w.variant),
            ]
        else:
            out.append(w)
    return out


def _value_tokens(text: str) -> str:
    """Tiene solo le parti 'valore' (maiuscole/cifre) togliendo i puntini di guida,
    che l'OCR legge come 'eeeee', '......', 'cecccs' ecc."""
    keep = []
    for tok in re.split(r"[.…·:;_]{2,}|\s+", text):
        tok = tok.strip(".,:;'`’_-|")
        letters = re.sub(r"[^A-Za-zÀ-Ýà-ý]", "", tok)
        if not tok or not re.search(r"[A-Z0-9]", tok):
            continue
        if letters and sum(c.isupper() for c in letters) / len(letters) < 0.75:
            continue
        keep.append(tok)
    return " ".join(keep)


def _senza_puntini(gray: np.ndarray, target: int = 90) -> list[np.ndarray]:
    """Riga ingrandita e binarizzata senza i puntini di guida (componenti basse):
    sulle fotocopie chiare sono loro a confondere l'OCR, non il testo."""
    k = target / gray.shape[0]
    g = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    out = []
    for b in (
        cv2.threshold(cv2.GaussianBlur(g, (3, 3), 0), 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[
            1
        ],
        cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 41, 12),
    ):
        inv = cv2.morphologyEx(255 - b, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        n, lab, st, _ = cv2.connectedComponentsWithStats(inv, 8)
        alte = [i for i in range(1, n) if st[i][3] >= 0.25 * g.shape[0] and st[i][4] >= 20]
        keep = np.isin(lab, alte)
        out.append(
            cv2.copyMakeBorder(
                np.where(keep, 0, 255).astype(np.uint8),
                20,
                20,
                20,
                20,
                cv2.BORDER_CONSTANT,
                value=255,
            )
        )
    return out


def _region_lines(rgb: np.ndarray, box, whitelist: str | None = None) -> list[str]:
    """OCR di una riga con soglie diverse (anche per fotocopie chiare)."""
    crop = _clip(rgb, box)
    if crop.size == 0 or min(crop.shape[:2]) < 5:
        return []
    gray = cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY)
    k = max(1.0, 48 / crop.shape[0])  # altezza riga ~50 px
    g = cv2.resize(gray, None, fx=k, fy=k, interpolation=cv2.INTER_CUBIC)
    ims = [np.where(g < t, 0, 255).astype(np.uint8) for t in (90, 140)]
    ims = [cv2.copyMakeBorder(im, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255) for im in ims]
    ims += _senza_puntini(gray)
    cfg = "--psm 7" + (f" -c tessedit_char_whitelist={whitelist}" if whitelist else "")
    return [pytesseract.image_to_string(im, lang=TESS_LANG, config=cfg).strip() for im in ims]


class _Campi:
    """Valori 'etichetta ....... VALORE' sulla stessa riga, da tutte le varianti OCR
    più una rilettura mirata della riga."""

    def __init__(self, pages: list[PageOCR]):
        self.pages = pages
        self.words = [(p, _split_glued(ws)) for p in pages for ws in p.words.values()]

    def labels(self, kw: str, thr: float = 0.8) -> list[tuple[PageOCR, Word]]:
        found = []
        for p, ws in self.words:
            for w in (
                find_labels(ws, kw, thr)
                if len(kw) > 3
                else [w for w in ws if re.sub(r"[^A-Za-z]", "", w.text).upper() == kw]
            ):
                if not any(
                    q is p and abs(w.x0 - f.x0) < w.h and abs(w.y0 - f.y0) < w.h for q, f in found
                ):
                    found.append((p, w))
        return found

    def raw(self, kw: str, width: float = 20, thr: float = 0.8, rileggi: bool = True) -> list[str]:
        """Testi a destra dell'etichetta (larghezza in multipli dell'altezza)."""
        out = []
        for p, lab in self.labels(kw, thr):
            h = lab.h
            x_max = lab.x0 + width * h
            for q, ws in self.words:
                if q is not p:
                    continue
                row = [
                    w
                    for w in ws
                    if w.x0 >= lab.x1 - 0.2 * h
                    and w.x0 < x_max
                    and abs(w.yc - lab.yc) < 0.6 * max(h, w.h)
                    and w is not lab
                ]
                if row:
                    out.append(" ".join(w.text for w in sorted(row, key=lambda w: w.x0)))
            if rileggi:
                out += _region_lines(p.rgb, (lab.x1, lab.y0 - 0.5 * h, x_max, lab.y1 + 0.5 * h))
        return out

    def testo(self, kw: str, pulisci: Callable[[str], str] = clean_upper, **kw_args) -> str | None:
        vals = [pulisci(_value_tokens(t)) for t in self.raw(kw, **kw_args)]
        return vote_completo([v for v in vals if len(v.replace(" ", "")) >= 2])

    def date(self, kw: str, width: float = 20, **kw_args) -> list[str]:
        testi = self.raw(kw, width=width, **kw_args)
        for p, lab in self.labels(kw):  # rilettura con sole cifre e separatori
            h = lab.h
            testi += _region_lines(
                p.rgb,
                (lab.x1, lab.y0 - 0.5 * h, lab.x0 + width * h, lab.y1 + 0.5 * h),
                whitelist="0123456789-./",
            )
        return [
            d for t in testi for d in find_dates_any(re.sub(r"(?<=\d{2})\s(?=\d{4}\b)", "-", t))
        ]


def vote_completo(cands: list[str]) -> str | None:
    """Voto di maggioranza, ma se una lettura più lunga contiene la vincente ed è stata
    letta almeno due volte vince lei ('RADIO' -> 'TECNICO RADIO TELEVISIVO')."""
    if not cands:
        return None
    cnt = Counter(cands)
    best = cnt.most_common(1)[0][0]
    longer = [c for c in cnt if c != best and best in c and cnt[c] >= 2]
    return max(longer, key=len) if longer else best


def clean_indirizzo(s: str) -> str:
    s = s.upper().replace("’", "'")
    s = re.sub(r"[^A-Z0-9À-Ý'.,/() \-]", " ", s)
    return re.sub(r"\s+", " ", s).strip(" .,-")


def _pick_birth_expiry(nascite: list[str], scadenze: list[str]) -> tuple[str | None, str | None]:
    """Le carte recenti scadono il giorno del compleanno: preferisci la coppia coerente."""
    nascite = [d for d in nascite if plausible_birth(d)]
    coppie = Counter(
        (n, s)
        for n in nascite
        for s in scadenze
        if n[:5] == s[:5] and parse_date(s) > parse_date(n)
    )
    if coppie:
        return coppie.most_common(1)[0][0]
    return vote(nascite)[0], vote(d for d in scadenze if parse_date(d))[0]


def extract_ci_cartacea(pages: list[PageOCR], comuni: dict | None = None) -> dict:
    out: dict = {"tipo_documento": "Carta d'Identità cartacea"}
    notes: list[str] = []
    c = _Campi(pages)
    all_lines = [l for p in pages for l in p.all_lines()]

    # ---- numero: 2 lettere + 7 cifre (stampato due volte sul libretto) --------
    nums = []
    for l in all_lines:
        for m in re.finditer(r"\b([A-Z]{2})\s?([0-9OSB$]{7})\b", l.upper().replace("N°", " ")):
            nums.append(m[1] + m[2].translate(str.maketrans("OSB$", "0585")))
    # rilettura mirata sotto "CARTA D'IDENTITA" (riga "N° AX 3755575")
    for p, lab in c.labels("IDENTITA", 0.75):
        h = lab.h
        for t in _region_lines(
            p.rgb,
            (lab.x0 - 3 * h, lab.y1, lab.x1 + 3 * h, lab.y1 + 2.6 * h),
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 ",
        ):
            for m in re.finditer(r"([A-Z]{2})\s?([0-9OSB]{7})\b", t.upper()):
                nums.append(m[1] + m[2].translate(str.maketrans("OSB", "058")))
    out["numero_documento"], _ = vote(n for n in nums if n[:2] not in ("SV", "IL", "NO"))

    out["cognome"] = c.testo("COGNOME")
    out["nome"] = c.testo("NOME")
    nascite = c.date("NATO")
    scadenze = c.date("SCADENZA", width=14)
    out["data_nascita"], out["data_scadenza"] = _pick_birth_expiry(nascite, scadenze)
    for campo, cands in (("data_nascita", nascite), ("data_scadenza", scadenze)):
        v = out[campo]
        if v and cands.count(v) < 0.6 * len(cands):  # letture discordanti
            notes.append(f"{campo} letta come {v} ma con letture discordanti: verificare a vista")

    # ---- luogo di nascita: riga "a ...... ALBENGA (SV)" sotto "(atto n. ...)" ---
    luoghi, prov = [], []
    rx = re.compile(r"([A-Z][A-Z' ]{2,}?)\W*[\(\[{]\s*([A-Z]{2})\s*[\)\]}]")
    for p, nato in c.labels("NATO"):
        citt = [w for q, w in c.labels("CITTADINANZA") if q is p and w.y0 > nato.y1]
        if not citt:
            continue
        y0, y1 = nato.y1, min(w.y0 for w in citt)
        h = nato.h
        testi = [
            " ".join(w.text for w in sorted(r, key=lambda w: w.x0))
            for q, ws in c.words
            if q is p
            for r in _rows(
                [w for w in ws if y0 < w.yc < y1 and nato.x0 - 3 * h < w.x0 < nato.x0 + 20 * h]
            )
        ]
        # la riga del luogo è l'ultima prima di "Cittadinanza"
        testi += _region_lines(
            p.rgb,
            (nato.x0 - 1.5 * h, y1 - 0.55 * (y1 - y0) - 0.3 * h, nato.x0 + 20 * h, y1 - 0.1 * h),
        )
        for t in testi:
            m = rx.search(_value_tokens(t).upper())
            if m and not re.search(r"\bATTO\b", m[1]):
                luoghi.append(clean_upper(m[1]))
                prov.append(m[2])
    out["provincia_nascita"], _ = vote(prov)
    out["luogo_nascita"], _ = vote(luoghi)
    out["luogo_nascita"] = snap_comune(out["luogo_nascita"], out["provincia_nascita"], comuni)

    cit = c.testo("CITTADINANZA")
    out["cittadinanza"] = "ITA" if cit and fuzzy_eq(cit.replace(" ", ""), "ITALIANA", 0.7) else cit
    res = c.testo("RESIDENZA", pulisci=clean_indirizzo)
    via = c.testo("VIA", pulisci=clean_indirizzo)
    out["indirizzo_residenza"] = ", ".join(x for x in (via, res) if x) or None
    out["stato_civile"] = c.testo("CIVILE")
    out["professione"] = c.testo("PROFESSIONE")
    stat = [
        m for t in c.raw("STATURA", width=10) for m in re.findall(r"\b([12])\s?[,.]\s?(\d{2})\b", t)
    ]
    out["statura_cm"], _ = vote(
        f"{int(a) * 100 + int(b)}" for a, b in stat if 120 <= int(a) * 100 + int(b) <= 230
    )
    out["capelli"] = c.testo("CAPELLI")
    out["occhi"] = c.testo("OCCHI")

    # ---- rilascio: "ALASSIO li 03-09-2015" vicino alla firma, "COMVNE DI ALASSIO" --
    comuni_r, emis = [], []
    for l in all_lines:
        m = re.search(r"([A-Z][A-Z' ]{2,}?)\W*\bL[I1l]\b\W*(.*)", l.upper())
        if m:
            ds = find_dates_any(m[2])
            if ds:
                emis += ds
                v = clean_upper(_value_tokens(m[1]))
                if len(v) >= 3:
                    comuni_r.append(v)
    for p in pages:
        for words in p.words.values():
            for lab in find_labels(words, "COMVNE", 0.8) + find_labels(words, "COMUNE", 0.8):
                v = clean_upper(
                    _value_tokens(
                        text_below(words, lab, lab.x0 - 4 * lab.h, lab.x1 + 6 * lab.h, 3 * lab.h)
                    )
                )
                if len(v) >= 3 and not fuzzy_eq(v.replace(" ", ""), "CARTADIDENTITA"):
                    comuni_r.append(v)
    comuni_r = [
        v
        for v in comuni_r
        if all(len(w) >= 2 for w in v.split())
        and not any(x in v for x in ("CARTA", "IDENT", "RTA D"))
    ]
    out["comune_rilascio"] = snap_comune(vote(comuni_r)[0], None, comuni)
    for p, ws in c.words:  # date stampate a mano/a macchina: rilettura con sole cifre
        for w in ws:
            if re.fullmatch(r"\W*\d{1,2}\W\d{1,2}\W\d{3,4}\W*", w.text):
                h = w.h
                for t in [w.text] + _region_lines(
                    p.rgb, (w.x0 - h, w.y0 - 0.4 * h, w.x1 + h, w.y1 + 0.4 * h), "0123456789-./"
                ):
                    emis += find_dates_any(t)
    emis += [d for l in all_lines for d in find_dates_any(l)]
    lo, hi = parse_date(out["data_nascita"] or ""), parse_date(out["data_scadenza"] or "")
    emis = [
        d
        for d in emis
        if d not in (out["data_nascita"], out["data_scadenza"])
        and plausible_birth(d)
        and (not lo or parse_date(d) > lo)
    ]
    if lo and hi:
        emis = [d for d in emis if lo < parse_date(d) < hi and parse_date(d) <= date.today()]
    out["data_emissione"], _ = vote(emis)
    out["sesso"] = None
    out["codice_fiscale"] = None
    if not (nascite and scadenze):
        notes.append("Data di nascita o di scadenza non lette: verificare a vista")
    out["avvisi"] = notes
    return out


def _rows(words: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: w.yc):
        if rows and abs(np.mean([x.yc for x in rows[-1]]) - w.yc) < 0.5 * w.h:
            rows[-1].append(w)
        else:
            rows.append([w])
    return rows


def parse_mrz_td3(lines: list[str]) -> dict:
    """MRZ del passaporto (TD3: 2 righe da 44 caratteri) con verifica dei check digit."""
    num, l2, nomi = [], [], []
    for l in lines:
        s = _riempitivo(re.sub(r"\s", "", l.upper()).replace("«", "<"))
        if len(s) < 25:
            continue
        m = re.match(r"^P[<K]([A-Z1]{3})(.+)$", s)
        if m and not re.search(r"\d", m[2]):
            # '<<' (separatore cognome/nome) letto spesso come '<K<' o 'K<'
            n = re.sub(r"<[<K]*<", "<<", m[2]).strip("<K")
            if "<<" in n:
                cog, nom = n.split("<<", 1)
                nom = re.split(r"<[<K]*<", nom)[0]
                nomi.append((cog.replace("<", " "), nom.replace("<", " ").strip()))
            continue
        for i in range(0, len(s) - 27):
            seg = s[i : i + 28]
            n9, c1 = seg[:9], seg[9].translate(_TO_DIGIT)
            naz = seg[10:13].translate(_TO_ALPHA)
            b, c2 = seg[13:19].translate(_TO_DIGIT), seg[19].translate(_TO_DIGIT)
            sex = seg[20]
            e, c3 = seg[21:27].translate(_TO_DIGIT), seg[27].translate(_TO_DIGIT)
            if not (
                b.isdigit()
                and e.isdigit()
                and c1.isdigit()
                and c2.isdigit()
                and c3.isdigit()
                and sex in "MF<"
                and naz.isalpha()
            ):
                continue
            if mrz_check(n9) == c1 and mrz_check(b) == c2 and mrz_check(e) == c3:
                num.append(n9.replace("<", ""))
                l2.append((b, sex, e, naz))
    res: dict = {}
    if num:
        res["num"] = Counter(num).most_common(1)[0][0]
    if l2:
        b, sex, e, naz = Counter(l2).most_common(1)[0][0]
        cur = date.today().year % 100
        res["nascita"] = f"{b[4:6]}/{b[2:4]}/{(2000 if int(b[:2]) <= cur else 1900) + int(b[:2])}"
        res["scadenza"] = f"{e[4:6]}/{e[2:4]}/{2000 + int(e[:2])}"
        res["sesso"], res["nazionalita"] = (sex if sex in "MF" else None), naz
        for k in ("nascita", "scadenza"):
            if not parse_date(res[k]):
                del res[k]
    if nomi:
        res["cognome"], res["nome"] = Counter(nomi).most_common(1)[0][0]
    return res


def extract_passaporto(pages: list[PageOCR], comuni: dict | None = None) -> dict:
    out: dict = {"tipo_documento": "Passaporto"}
    notes: list[str] = []
    all_lines = [l for p in pages for l in p.all_lines()]
    mrz_lines = list(all_lines)
    for p in pages:  # rilettura mirata della MRZ con l'alfabeto ristretto
        for words in list(p.words.values())[:1]:
            ys = [w.y0 for w in words if w.text.upper().startswith(("P<", "PK"))]
            for y in ys[:1]:
                for t in ocr_crop(
                    p.rgb,
                    (0, y - 40, p.width, y + 220),
                    "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
                    psms=(6,),
                    scales=(1.0, 1.5),
                ):
                    mrz_lines += [l for l in t.splitlines() if l.strip()]
    mrz = parse_mrz_td3(mrz_lines)
    out["mrz_verificata"] = all(k in mrz for k in ("num", "nascita", "cognome"))
    stampati = [m.group(0) for l in all_lines for m in re.finditer(r"\b[A-Z]{2}\d{7}\b", l.upper())]
    out["numero_documento"] = mrz.get("num") or vote(stampati)[0]

    def sotto(label: str, gap: float = 3.5, width: float = 18) -> list[str]:
        vals = []
        for p in pages:
            for words in p.words.values():
                for lab in find_labels(words, label, 0.8):
                    vals.append(
                        text_below(
                            words, lab, lab.x0 - 2 * lab.h, lab.x0 + width * lab.h, gap * lab.h
                        )
                    )
        return [v for v in vals if v]

    out["cognome"] = mrz.get("cognome") or vote(clean_upper(v) for v in sotto("COGNOME"))[0]
    out["nome"] = mrz.get("nome") or vote(clean_upper(v) for v in sotto("NOME"))[0]
    out["data_nascita"] = (
        mrz.get("nascita")
        or vote([d for v in sotto("NASCITA") for d in find_dates_any(v)], plausible_birth)[0]
    )
    out["sesso"] = mrz.get("sesso")
    out["cittadinanza"] = mrz.get("nazionalita")
    out["data_scadenza"] = (
        mrz.get("scadenza") or vote(d for v in sotto("SCADENZA") for d in find_dates_any(v))[0]
    )
    out["data_emissione"], _ = vote(d for v in sotto("RILASCIO") for d in find_dates_any(v))
    luoghi, prov = [], []
    for v in sotto("LUOGO", width=14):
        m = re.search(r"([A-Z][A-Z' ]{2,}?)\s*[\(C\[{]\s*([A-Z]{2,3})\s*[\)\]}]", v.upper())
        if m:
            luoghi.append(clean_upper(m[1]))
            prov.append(m[2])
    out["provincia_nascita"], _ = vote(prov)
    out["luogo_nascita"] = snap_comune(vote(luoghi)[0], out["provincia_nascita"], comuni)
    res = [clean_upper(v) for v in sotto("RESIDENZA", gap=2.5) if len(clean_upper(v)) >= 3]
    out["indirizzo_residenza"], _ = vote(r for r in res if "RESIDEN" not in r)
    out["statura_cm"], _ = vote(
        m for v in sotto("STATURA", gap=2.5) for m in re.findall(r"\b(1\d\d|2[0-3]\d)\b", v)
    )
    out["codice_fiscale"] = None
    if not mrz:
        notes.append("MRZ non letta: dati ricavati solo dalla parte stampata")
    out["avvisi"] = notes
    return out


# =============================================================================
# 6. Tessera Sanitaria / TEAM
# =============================================================================
_TS_STOPWORDS = {
    "REPUBBLICA",
    "ITALIANA",
    "TESSERA",
    "SANITARIA",
    "CARTA",
    "REGIONALE",
    "SERVIZI",
    "CODICE",
    "FISCALE",
    "SESSO",
    "COGNOME",
    "NOME",
    "LUOGO",
    "NASCITA",
    "PROVINCIA",
    "DATA",
    "SCADENZA",
    "EUROPEA",
    "ASSICURAZIONE",
    "MALATTIA",
    "NUMERO",
    "IDENTIFICAZIONE",
    "SALUTE",
    "SANITARI",
    "REGIONALI",
}


def extract_ts(pages: list[PageOCR], comuni: dict | None) -> dict:
    out: dict = {"tipo_documento": "Tessera Sanitaria / TEAM"}
    notes: list[str] = []
    all_lines = [l for p in pages for l in p.all_lines()]

    # ---- Codice fiscale -----------------------------------------------------
    cf, ok = vote(cf_candidates(all_lines))
    out["codice_fiscale"] = cf
    dec = cf_decode(cf) if cf else {}

    # ---- Cognome e nome: righe la cui codifica CF coincide con il CF --------
    sur_c, name_c = [], []
    for l in all_lines:
        v = clean_upper(l)
        if len(v) < 2 or any(w in _TS_STOPWORDS for w in v.split()):
            continue
        if cf and cf_surname_code(v) == cf[:3]:
            sur_c.append(v)
        if cf and cf_name_code(v) == cf[3:6]:
            name_c.append(v)
    # se una riga combacia con entrambi, privilegia il ruolo più frequente
    out["cognome"], _ = vote(sur_c)
    out["nome"], _ = vote([n for n in name_c if n != out["cognome"]])

    # ---- Date: nascita (coerente col CF) e scadenza --------------------------
    dates = [d for l in all_lines for d in find_dates(l)]
    birth = dec.get("data_nascita")
    out["data_nascita"] = birth if (birth and birth in dates) else (birth or vote(dates)[0])
    if birth and birth not in dates:
        notes.append("Data di nascita ricavata dal codice fiscale (non letta sulla tessera)")
    others = [d for d in dates if d != out["data_nascita"]]
    if out["data_nascita"]:
        b = parse_date(out["data_nascita"])
        others = [d for d in others if not b or parse_date(d) > b]
    out["data_scadenza"], _ = vote(others)
    out["sesso"] = dec.get("sesso")

    # ---- Luogo e provincia di nascita (fronte, a destra delle etichette) ----
    luogo_c, prov_c = [], []
    for p in pages:
        for lab in label_boxes(p, "LUOGO", 0.75)[:2]:
            h = lab.h
            for t in ocr_region(
                p.rgb,
                (lab.x1 + 0.3 * h, lab.y0 - 0.3 * h, lab.x1 + 0.45 * p.width, lab.y1 + 0.8 * h),
            ):
                v = " ".join(w for w in clean_upper(t).split() if len(w) > 1 or w in "DL")
                if len(v) >= 3:
                    luogo_c.append(v)
        for lab in label_boxes(p, "PROVINCIA", 0.75)[:2]:
            h = lab.h
            for t in ocr_region(
                p.rgb,
                (lab.x1 + 0.3 * h, lab.y0 - 0.6 * h, lab.x1 + 0.2 * p.width, lab.y1 + 0.8 * h),
            ):
                m = re.search(r"\b([A-Z]{2})\b", t.upper())
                if m:
                    prov_c.append(m[1])
    out["codice_catastale_nascita"] = dec.get("codice_catastale")
    if comuni and dec.get("codice_catastale") in comuni:
        nome_c, sigla = comuni[dec["codice_catastale"]]
        out["luogo_nascita"], out["provincia_nascita"] = nome_c.upper(), sigla
    else:
        out["luogo_nascita"], _ = vote(luogo_c)
        out["provincia_nascita"], _ = vote(prov_c)

    # ---- Numero identificativo tessera (TEAM, 20 cifre, Luhn) ---------------
    num_c, boxes = [], []
    for p in pages:
        for words in p.words.values():
            for w in words:
                d = re.sub(r"\D", "", w.text.translate(_TO_DIGIT))
                if len(d) >= 17 and d.startswith("80"):
                    num_c.append(d)
                    boxes.append((p, w))
    for p, w in boxes[:2]:  # OCR mirato con sole cifre sulla riga del numero
        pad = w.h
        for t in ocr_crop(p.rgb, (w.x0 - pad, w.y0 - pad, w.x1 + pad, w.y1 + pad), "0123456789", 7):
            d = re.sub(r"\D", "", t)
            if len(d) >= 17:
                num_c.append(d)
    num_c = [n for n in num_c if len(n) == 20]
    team_ok = lambda n: n.startswith("80380") and luhn_valid(n)  # 80 = sanità, 380 = Italia
    num_c = [n for n in num_c if n.startswith("80")]
    num, valid = vote(num_c, team_ok)
    if num and not valid:
        fixes = luhn_repair(num)
        # privilegia correzioni sulle ultime cifre (spesso sbiadite) e prefisso 80380
        fixes = [f for f in fixes if f.startswith("80380")]
        if fixes:
            fixes.sort(key=lambda f: -next(i for i in range(20) if f[i] != num[i]))
            notes.append(
                f"Numero tessera letto come {num} (Luhn non valido): "
                f"corretto in {fixes[0]} - verificare a vista"
            )
            num, valid = fixes[0], True
        else:
            notes.append("Numero tessera non verificato (controllo Luhn fallito)")
    out["numero_tessera"] = num
    out["numero_tessera_verificato"] = valid

    ist = [
        f"{m[1]}"
        for l in all_lines
        for m in re.finditer(r"SSN\s*-?\s*MIN\.?\s*SALUTE\s*[-=]?\s*(\d{6})", l.upper())
    ]
    out["codice_istituzione"], _ = vote(ist)

    out["avvisi"] = notes
    return out


# =============================================================================
# 7. Classificazione documento e orchestrazione
# =============================================================================
def classify(pages: list[PageOCR]) -> str | None:
    t = " ".join(p.full_text().upper() for p in pages)
    t_ns = re.sub(r"\s", "", t)
    # passaporto: MRZ a due righe che inizia con P< (o etichette PASSAPORTO/PASSPORT)
    pas = sum(k in t for k in ("PASSAPORTO", "PASSPORT", "PASSEPORT")) + 2 * bool(
        re.search(r"(^|\n)P[<K][A-Z]{3}", t.replace(" ", ""))
    )
    # carta d'identità cartacea (libretto): diciture del modello ministeriale
    cart = sum(
        k in t
        for k in (
            "CONNOTATI",
            "CONTRASSEGNI",
            "SEGNI PARTICOLARI",
            "STATO CIVILE",
            "PROFESSIONE",
            "CAPELLI",
            "IMPRONTA",
            "COMVNE",
            "TOTALE DIRITTI",
            "NATO IL",
        )
    )
    if pas >= 3:
        return "PAS"
    if cart >= 3:
        return "CIC"
    cie = sum(
        k in t
        for k in ("IDENTITY CARD", "CARTA DI IDENTIT", "MUNICIPALITY", "SURNAME", "RESIDENZA")
    ) + 2 * ("<ITA" in t_ns)
    ts = sum(
        k in t for k in ("TESSERA SANITARIA", "SANITARIA", "EUROPEA", "MALATTIA", "SSN")
    ) + 2 * bool(re.search(r"80380\d{10,}", t_ns))
    if max(cie, ts) == 0:
        return None
    return "CIE" if cie >= ts else "TS"


def _orientation_score(rgb: np.ndarray) -> int:
    gray = cv2.resize(
        cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
    )
    d = pytesseract.image_to_data(
        gray, lang=TESS_LANG, config="--psm 11", output_type=pytesseract.Output.DICT
    )
    return sum(
        1
        for t, c in zip(d["text"], d["conf"])
        if float(c) >= 80 and len(re.sub(r"[^A-Za-z0-9]", "", t)) >= 3
    )


def fix_orientation(rgb: np.ndarray, min_score: int = 12) -> np.ndarray:
    """Raddrizza la pagina provando le 4 rotazioni e tenendo quella con più parole sicure."""
    best, best_score = rgb, _orientation_score(rgb)
    if best_score >= min_score:
        return rgb
    for rot in (cv2.ROTATE_180, cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE):
        cand = cv2.rotate(rgb, rot)
        s = _orientation_score(cand)
        if s > best_score:
            best, best_score = cand, s
    return best


def load_pages_bytes(dati: bytes, pdf: bool, max_pagine: int = 4) -> list[np.ndarray]:
    """Come load_pages, a partire dal contenuto del file."""
    if pdf:
        if pdfium is None:
            raise RuntimeError("Installare pypdfium2 per leggere i PDF")
        doc = pdfium.PdfDocument(dati)
        try:
            return [
                np.array(doc[i].render(scale=DPI / 72).to_pil().convert("RGB"))
                for i in range(min(len(doc), max_pagine))
            ]
        finally:
            doc.close()
    img = cv2.imdecode(np.frombuffer(dati, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("Immagine illeggibile")
    return [prepare_photo(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))]


def process_bytes(dati: bytes, pdf: bool, comuni: dict | None = None) -> dict:
    """Riconosce il documento e ne estrae i dati."""
    return process_pages(load_pages_bytes(dati, pdf), comuni)


def process_file(path: Path, comuni: dict | None, verbose: bool = False) -> dict:
    data = process_pages(load_pages(path), comuni, verbose, path.name)
    data["file"] = path.name
    return data


def process_pages(
    rgbs: list[np.ndarray], comuni: dict | None, verbose: bool = False, nome: str = ""
) -> dict:
    pages = []
    for rgb in rgbs:
        rgb = fix_orientation(rgb)
        page = ocr_page(rgb)
        pages.append(page)
    kind = classify(pages)
    if verbose:
        for i, p in enumerate(pages):
            print(f"--- {nome} pag.{i + 1} ---\n{p.texts['gray']}", file=sys.stderr)
    if kind == "CIE":
        data = extract_cie(pages, comuni)
    elif kind == "CIC":
        data = extract_ci_cartacea(pages, comuni)
    elif kind == "PAS":
        data = extract_passaporto(pages, comuni)
    elif kind == "TS":
        data = extract_ts(pages, comuni)
    else:
        data = {"tipo_documento": "sconosciuto", "avvisi": ["Documento non riconosciuto"]}
    data["tipo"] = kind
    return data


def cross_checks(docs: list[dict]) -> tuple[dict, list[str]]:
    """Unisce i dati dei documenti in un'anagrafica unica e verifica la coerenza."""
    cie = next((d for d in docs if d["tipo_documento"].startswith(("Carta", "Passaporto"))), {})
    ts = next((d for d in docs if d["tipo_documento"].startswith("Tessera")), {})
    pick = lambda k: cie.get(k) or ts.get(k)
    ana = {
        k: pick(k)
        for k in (
            "cognome",
            "nome",
            "sesso",
            "data_nascita",
            "luogo_nascita",
            "provincia_nascita",
            "codice_fiscale",
            "cittadinanza",
            "indirizzo_residenza",
        )
    }
    checks = []
    cf = ana["codice_fiscale"]
    if cie.get("codice_fiscale") and ts.get("codice_fiscale"):
        checks.append(
            ("OK" if cie["codice_fiscale"] == ts["codice_fiscale"] else "ERRORE")
            + ": codice fiscale CIE vs tessera sanitaria"
        )
    if cf:
        dec = cf_decode(cf)
        if ana["cognome"]:
            checks.append(
                ("OK" if cf_surname_code(ana["cognome"]) == cf[:3] else "ERRORE")
                + ": cognome coerente con il codice fiscale"
            )
        if ana["nome"]:
            checks.append(
                ("OK" if cf_name_code(ana["nome"]) == cf[3:6] else "ERRORE")
                + ": nome coerente con il codice fiscale"
            )
        if ana["data_nascita"]:
            checks.append(
                ("OK" if dec["data_nascita"] == ana["data_nascita"] else "ERRORE")
                + ": data di nascita coerente con il codice fiscale"
            )
        if ana["sesso"]:
            checks.append(
                ("OK" if dec["sesso"] == ana["sesso"] else "ERRORE")
                + ": sesso coerente con il codice fiscale"
            )
    for d in docs:
        s = d.get("data_scadenza")
        if s and parse_date(s) and parse_date(s) < date.today():
            checks.append(f"ATTENZIONE: {d['tipo_documento']} scaduto il {s}")
    return ana, checks


def load_comuni(path: Path) -> dict:
    """Legge il CSV ISTAT 'Elenco-comuni-italiani.csv' -> {codice_catastale: (nome, sigla)}."""
    raw = path.read_bytes()
    text = raw.decode("utf-8") if raw[:3] != b"\xef\xbb\xbf" else raw[3:].decode("utf-8")
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    rows = list(csv.reader(text.splitlines(), delimiter=";" if ";" in text[:2000] else ","))
    head = [h.lower() for h in rows[0]]
    i_cod = next(i for i, h in enumerate(head) if "catastale" in h)
    i_nom = next(i for i, h in enumerate(head) if "denominazione" in h)
    i_sig = next((i for i, h in enumerate(head) if "sigla" in h), None)
    return {
        r[i_cod].strip().upper(): (r[i_nom].strip(), r[i_sig].strip() if i_sig is not None else "")
        for r in rows[1:]
        if len(r) > i_cod
    }
