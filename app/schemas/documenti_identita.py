import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.domain.regole import TipoDocumento


class PropostaOut(BaseModel):
    """Dati letti dalla copia del documento, da controllare sull'originale."""

    # Dati confermati dai controlli (MRZ, oppure codice fiscale della tessera sanitaria)
    letta: bool
    # Documento riconosciuto: carta d'identita' elettronica o cartacea, passaporto, tessera
    riconosciuto: str | None = None
    # Campi letti dal testo stampato, senza cifre di controllo a confermarli
    campi_incerti: list[str] = []
    tipo: TipoDocumento | None = None
    numero: str | None = None
    scadenza: date | None = None
    rilasciato_da: str | None = None
    cognome: str | None = None
    nome: str | None = None
    data_nascita: date | None = None
    sesso: Literal["M", "F"] | None = None
    luogo_nascita: str | None = None
    provincia_nascita: str | None = None
    codice_fiscale: str | None = None
    # Dati che non tornano con il codice fiscale del cliente
    discordanze: list[str] = []
    avvisi: list[str] = []


class DocumentoIdentitaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cliente_id: uuid.UUID
    tipo: TipoDocumento
    numero: str | None
    rilasciato_da: str | None
    scadenza: date | None
    copia_tipo: str
    copia_nome: str
    copia_sha256: str
    lettura: dict[str, Any]
    created_at: datetime


class DocumentoCaricatoOut(DocumentoIdentitaOut):
    proposta: PropostaOut
