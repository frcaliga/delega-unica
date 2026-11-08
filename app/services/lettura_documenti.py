"""Lettura dei dati da una copia del documento d'identita', tutta in locale (Tesseract + OpenCV).

Il riconoscimento e l'estrazione sono quelli del motore OCR in `ocr_documenti` (CIE fronte e
retro, carta cartacea, passaporto, tessera sanitaria; foto da telefono raddrizzate). Qui i
risultati diventano dati tipizzati, con l'indicazione di cosa e' stato verificato: numero e date
della MRZ hanno le cifre di controllo, il codice fiscale il carattere di controllo; il resto e'
letto dal testo stampato e va controllato sull'originale.

Le immagini non escono dalla piattaforma.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.domain.regole import TipoDocumento

TIPI = {
    "CIE": TipoDocumento.CARTA_IDENTITA,
    "CIC": TipoDocumento.CARTA_IDENTITA,
    "PAS": TipoDocumento.PASSAPORTO,
    "TS": TipoDocumento.ALTRO,
}
NOMI = {
    "CIE": "carta d'identita' elettronica",
    "CIC": "carta d'identita' cartacea",
    "PAS": "passaporto",
    "TS": "tessera sanitaria",
}


class LetturaNonDisponibile(Exception):
    pass


@dataclass
class Lettura:
    riconosciuto: str | None = None  # CIE, CIC, PAS, TS
    tipo: TipoDocumento | None = None
    numero: str | None = None
    scadenza: date | None = None
    cognome: str | None = None
    nome: str | None = None
    data_nascita: date | None = None
    sesso: str | None = None
    luogo_nascita: str | None = None
    provincia_nascita: str | None = None
    rilasciato_da: str | None = None
    codice_fiscale: str | None = None
    # Numero e date confermati dalle cifre di controllo della MRZ
    mrz_verificata: bool = False
    avvisi: list[str] = field(default_factory=list)

    @property
    def trovata(self) -> bool:
        return self.mrz_verificata or (self.riconosciuto == "TS" and bool(self.codice_fiscale))


COMUNI_INCLUSI = Path(__file__).resolve().parent.parent / "dati" / "comuni.csv"


@lru_cache
def comuni() -> dict | None:
    """Elenco ISTAT dei comuni (quello configurato o quello incluso): corregge i nomi letti male."""
    percorso = Path(get_settings().comuni_csv_path or COMUNI_INCLUSI)
    if not percorso.is_file():
        return None
    from app.services import ocr_documenti

    return ocr_documenti.load_comuni(percorso)


def _data(valore: str | None) -> date | None:
    if not valore:
        return None
    try:
        return datetime.strptime(valore, "%d/%m/%Y").date()
    except ValueError:
        return None


def _titolo(valore: str | None) -> str | None:
    return valore.strip().title() if valore and valore.strip() else None


def da_estrazione(d: dict) -> Lettura:
    kind = d.get("tipo")
    lettura = Lettura(
        riconosciuto=kind,
        tipo=TIPI.get(kind or ""),
        numero=(d.get("numero_documento") or None) if kind != "TS" else d.get("numero_tessera"),
        scadenza=_data(d.get("data_scadenza")),
        cognome=_titolo(d.get("cognome")),
        nome=_titolo(d.get("nome")),
        data_nascita=_data(d.get("data_nascita")),
        sesso=d.get("sesso") if d.get("sesso") in ("M", "F") else None,
        luogo_nascita=_titolo(d.get("luogo_nascita")),
        provincia_nascita=(d.get("provincia_nascita") or None),
        codice_fiscale=d.get("codice_fiscale") or None,
        mrz_verificata=bool(d.get("mrz_verificata")),
        avvisi=list(d.get("avvisi") or []),
    )
    if comune := _titolo(d.get("comune_rilascio")):
        lettura.rilasciato_da = f"Comune di {comune}"
    if kind is None:
        lettura.avvisi = [
            "Documento non riconosciuto: servono la carta d'identita' (fronte e retro), "
            "il passaporto o la tessera sanitaria, ben a fuoco"
        ]
    return lettura


def leggi(
    dati: bytes, tipo: str, oggi: date | None = None, foto: list[bytes] | None = None
) -> Lettura:
    """Legge la copia; `foto`, se date, sono le immagini originali di fronte e retro (la copia
    e' il PDF che le unisce) e si leggono al posto del PDF, ritagliate sulla carta."""
    try:
        from app.services import ocr_documenti
    except ImportError as exc:  # OpenCV o numpy assenti
        raise LetturaNonDisponibile(str(exc)) from exc
    import pytesseract

    try:
        if foto:
            estratti = ocr_documenti.process_images(foto, comuni())
        else:
            estratti = ocr_documenti.process_bytes(dati, tipo == "application/pdf", comuni())
    except pytesseract.TesseractNotFoundError as exc:
        raise LetturaNonDisponibile("Tesseract non installato") from exc
    except Exception:  # file illeggibile: la copia si conserva comunque
        return Lettura(avvisi=["Il file non e' un'immagine o un PDF leggibile"])
    return da_estrazione(estratti)
