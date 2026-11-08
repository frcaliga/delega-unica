"""Archivio dei documenti d'identita': controllo della copia, lettura, cifratura e proposta."""

import hashlib
import io
import uuid
from dataclasses import dataclass, field
from datetime import date

from app.domain import codice_fiscale
from app.domain.regole import TipoDocumento
from app.models import Cliente, DocumentoIdentita, User
from app.schemas.documenti_identita import PropostaOut
from app.services import lettura_documenti
from app.services.cifratura import Portachiavi

MAX_COPIA = 10 * 1024 * 1024
TIPI_COPIA = {"application/pdf", "image/jpeg", "image/png"}
FIRME = {
    "application/pdf": (b"%PDF",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/png": (b"\x89PNG\r\n\x1a\n",),
}


@dataclass
class Copia:
    dati: bytes
    tipo: str
    nome: str
    # Per fronte e retro uniti in un PDF: le foto originali, che si leggono ciascuna
    # ritagliata sulla carta (il PDF ne perde la geometria)
    foto: list[bytes] = field(default_factory=list)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.dati).hexdigest()


def controlla_copia(dati: bytes, tipo: str | None, nome: str | None) -> tuple[Copia | None, str]:
    """La copia accettata, oppure il motivo del rifiuto."""
    if tipo not in TIPI_COPIA:
        return None, "La copia del documento deve essere PDF, JPEG o PNG"
    if not dati or len(dati) > MAX_COPIA:
        return None, "La copia del documento deve essere tra 1 byte e 10 MB"
    if not dati.startswith(FIRME[tipo]):
        return None, "Il contenuto del file non corrisponde al tipo dichiarato"
    nome = (nome or "documento").rsplit("/", 1)[-1].rsplit("\\", 1)[-1][:150]
    return Copia(dati, tipo, nome), ""


MAX_FILE = 2


def unisci(copie: list[Copia]) -> tuple[Copia | None, str]:
    """Fronte e retro fotografati a parte diventano un unico PDF, una pagina per lato."""
    if len(copie) == 1:
        return copie[0], ""
    if len(copie) > MAX_FILE:
        return None, "Al massimo due file: fronte e retro"
    if any(c.tipo == "application/pdf" for c in copie):
        return None, "Un PDF va caricato da solo: deve gia' contenere fronte e retro"
    from PIL import Image, ImageOps

    try:
        pagine = [
            ImageOps.exif_transpose(Image.open(io.BytesIO(c.dati))).convert("RGB") for c in copie
        ]
    except Exception:
        return None, "Immagine illeggibile"
    buf = io.BytesIO()
    pagine[0].save(buf, "PDF", save_all=True, append_images=pagine[1:], resolution=300)
    nome = copie[0].nome.rsplit(".", 1)[0][:140] + ".pdf"
    unita = Copia(buf.getvalue(), "application/pdf", nome, foto=[c.dati for c in copie])
    if len(unita.dati) > MAX_COPIA:
        return None, "Fronte e retro insieme superano 10 MB"
    return unita, ""


def pagine(dati: bytes, tipo: str, lato: int = 1000, max_pagine: int = 4) -> list:
    """Le pagine della copia come immagini PIL ridotte (lato lungo <= `lato`), per l'anteprima."""
    from PIL import Image, ImageOps

    if tipo == "application/pdf":
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(dati)
        try:
            risultato = []
            for i in range(min(len(doc), max_pagine)):
                w, h = doc[i].get_size()
                img = doc[i].render(scale=min(4.0, lato / max(w, h))).to_pil()
                risultato.append(img.convert("RGB"))
            return risultato
        finally:
            doc.close()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(dati))).convert("RGB")
    img.thumbnail((lato, lato))
    return [img]


def anteprima(dati: bytes, tipo: str, lato: int = 1000) -> bytes:
    """Fronte e retro affiancati in un unico JPEG, per vederli nella pagina."""
    from PIL import Image

    imgs = pagine(dati, tipo, lato)
    margine = 16
    larghezza = sum(i.width for i in imgs) + margine * (len(imgs) - 1)
    foglio = Image.new("RGB", (larghezza, max(i.height for i in imgs)), "white")
    x = 0
    for i in imgs:
        foglio.paste(i, (x, 0))
        x += i.width + margine
    buf = io.BytesIO()
    foglio.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def proposta(copia: Copia, oggi: date, cf_cliente: str | None = None) -> PropostaOut:
    try:
        esito = lettura_documenti.leggi(copia.dati, copia.tipo, oggi, foto=copia.foto)
    except lettura_documenti.LetturaNonDisponibile:
        return PropostaOut(letta=False, avvisi=["Lettura automatica non disponibile"])
    p = PropostaOut(
        letta=esito.trovata,
        riconosciuto=lettura_documenti.NOMI.get(esito.riconosciuto or ""),
        tipo=esito.tipo,
        numero=esito.numero,
        scadenza=esito.scadenza,
        rilasciato_da=esito.rilasciato_da,
        cognome=esito.cognome,
        nome=esito.nome,
        data_nascita=esito.data_nascita,
        sesso=esito.sesso,  # type: ignore[arg-type]
        luogo_nascita=esito.luogo_nascita,
        provincia_nascita=esito.provincia_nascita,
        codice_fiscale=esito.codice_fiscale,
        avvisi=list(esito.avvisi),
    )
    if not esito.mrz_verificata:
        # Letti dal testo stampato: nessuna cifra di controllo a confermarli
        letti = {"numero_documento": p.numero, "data_nascita": p.data_nascita}
        letti["data_scadenza"] = p.scadenza
        p.campi_incerti = [k for k, v in letti.items() if v]
    if p.scadenza and p.scadenza < oggi:
        p.avvisi.append("Il documento e' scaduto")
    if p.codice_fiscale and cf_cliente and p.codice_fiscale != cf_cliente:
        p.discordanze.append("codice fiscale")
    if cf := cf_cliente or p.codice_fiscale:
        p.discordanze += codice_fiscale.discordanze(
            cf, cognome=p.cognome, nome=p.nome, data_nascita=p.data_nascita, sesso=p.sesso
        )
    return p


def riepilogo(p: PropostaOut) -> dict:
    """Esito della lettura da conservare: niente dati personali."""
    return {
        "letta": p.letta,
        "documento": p.riconosciuto,
        "campi_incerti": p.campi_incerti,
        "discordanze": p.discordanze,
        "codice_fiscale_trovato": p.codice_fiscale is not None,
    }


def nuovo(
    portachiavi: Portachiavi,
    *,
    cliente: Cliente,
    user: User,
    copia: Copia,
    tipo: TipoDocumento,
    numero: str | None,
    rilasciato_da: str | None,
    scadenza: date | None,
    lettura: dict,
) -> DocumentoIdentita:
    doc = DocumentoIdentita(
        id=uuid.uuid4(),
        tenant_id=cliente.tenant_id,
        cliente_id=cliente.id,
        tipo=tipo,
        numero=numero.strip().upper() if numero else None,
        rilasciato_da=rilasciato_da.strip() if rilasciato_da else None,
        scadenza=scadenza,
        copia_tipo=copia.tipo,
        copia_nome=copia.nome,
        copia_sha256=copia.sha256,
        lettura=lettura,
        user_id=user.id,
    )
    busta = portachiavi.cifra(copia.dati, doc.contesto())
    doc.cifratura, doc.copia_chiave, doc.copia_cifrata = (
        busta.schema,
        busta.chiave_cifrata,
        busta.dati,
    )
    return doc


def apri(portachiavi: Portachiavi, doc: DocumentoIdentita) -> bytes:
    dati = portachiavi.decifra(doc.busta, doc.contesto())
    if hashlib.sha256(dati).hexdigest() != doc.copia_sha256:
        raise ValueError("Impronta della copia non corrispondente")
    return dati
