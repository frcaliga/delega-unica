"""Archivio dei documenti d'identita': controllo della copia, lettura, cifratura e proposta."""

import hashlib
import uuid
from dataclasses import dataclass
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


def proposta(copia: Copia, oggi: date, cf_cliente: str | None = None) -> PropostaOut:
    try:
        esito = lettura_documenti.leggi(copia.dati, copia.tipo, oggi)
    except lettura_documenti.LetturaNonDisponibile:
        return PropostaOut(letta=False, avvisi=["Lettura automatica non disponibile"])
    p = PropostaOut(letta=esito.trovata, avvisi=list(esito.avvisi))
    p.codice_fiscale = esito.codice_fiscale
    if m := esito.mrz:
        incerti = set(m.errori)
        p.campi_incerti = sorted(incerti)
        p.tipo = TipoDocumento.CARTA_IDENTITA if m.carta_identita else TipoDocumento.PASSAPORTO
        if "numero_documento" not in incerti and "composito" not in incerti:
            p.numero = m.numero_documento or None
        if "data_scadenza" not in incerti:
            p.scadenza = m.data_scadenza
        if "data_nascita" not in incerti:
            p.data_nascita = m.data_nascita
        p.cognome = m.cognome.title() or None
        p.nome = m.nome.title() or None
        p.sesso = m.sesso  # type: ignore[assignment]
        if p.scadenza and p.scadenza < oggi:
            p.avvisi.append("Il documento e' scaduto")
    cf = cf_cliente or p.codice_fiscale
    if cf and p.codice_fiscale and cf_cliente and p.codice_fiscale != cf_cliente:
        p.discordanze.append("codice fiscale")
    if cf:
        p.discordanze += codice_fiscale.discordanze(
            cf, cognome=p.cognome, nome=p.nome, data_nascita=p.data_nascita, sesso=p.sesso
        )
    return p


def riepilogo(p: PropostaOut) -> dict:
    """Esito della lettura da conservare: niente dati personali."""
    return {
        "letta": p.letta,
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
