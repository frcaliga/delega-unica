import uuid
from datetime import date, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.domain import codice_fiscale
from app.domain.regole import ModalitaFirma, Qualifica, Servizio, TipoDocumento, TipoRichiesta
from app.models import StatoDelega


def _cf(value: str) -> str:
    value = value.strip().upper()
    if not codice_fiscale.valida(value):
        raise ValueError("Codice fiscale non valido")
    return value


class ClienteBase(BaseModel):
    cognome: str | None = Field(None, max_length=80)
    nome: str | None = Field(None, max_length=80)
    denominazione: str | None = Field(None, max_length=150)
    partita_iva_attiva: bool = False
    email: EmailStr | None = None
    telefono: str | None = Field(None, max_length=30)


class ClienteIn(ClienteBase):
    codice_fiscale: str

    _valida_cf = field_validator("codice_fiscale")(_cf)

    @model_validator(mode="after")
    def _anagrafica(self) -> Self:
        if codice_fiscale.is_persona_fisica(self.codice_fiscale):
            if not (self.cognome and self.nome):
                raise ValueError("Per una persona fisica servono cognome e nome")
        elif not self.denominazione:
            raise ValueError("Per un soggetto diverso da persona fisica serve la denominazione")
        return self


class ClienteUpdate(ClienteBase):
    partita_iva_attiva: bool | None = None


class ClienteOut(ClienteBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    codice_fiscale: str
    persona_fisica: bool
    nominativo: str
    created_at: datetime


class SottoscrittoreIn(BaseModel):
    codice_fiscale: str
    cognome: str = Field(min_length=1, max_length=80)
    nome: str = Field(min_length=1, max_length=80)
    qualifica: Qualifica

    _valida_cf = field_validator("codice_fiscale")(_cf)


class DocumentoIn(BaseModel):
    tipo: TipoDocumento
    rilasciato_da: str = Field(min_length=1, max_length=100)
    numero: str = Field(min_length=1, max_length=30)
    scadenza: date
    tipo_altro: str | None = Field(None, max_length=100)


class DelegaIn(BaseModel):
    cliente_id: uuid.UUID
    tipo_richiesta: TipoRichiesta = TipoRichiesta.CONFERIMENTO
    servizi: list[Servizio] = []
    # Obbligatoria per revoca e rinnovo
    delega_origine_id: uuid.UUID | None = None
    # Se assente firma il cliente stesso (qualifica 1)
    sottoscrittore: SottoscrittoreIn | None = None
    documento: DocumentoIn | None = None
    modalita_firma: ModalitaFirma
    luogo_firma: str | None = Field(None, max_length=100)
    data_firma: date


class DelegaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cliente: ClienteOut
    tipo_richiesta: TipoRichiesta
    servizi: list[str]
    delega_origine_id: uuid.UUID | None
    sottoscrittore_cf: str
    sottoscrittore_cognome: str
    sottoscrittore_nome: str
    qualifica: Qualifica
    documento_tipo: TipoDocumento | None
    documento_rilasciato_da: str | None
    documento_numero: str | None
    documento_scadenza: date | None
    documento_tipo_altro: str | None
    modalita_firma: ModalitaFirma
    luogo_firma: str | None
    data_firma: date
    stato: StatoDelega
    data_conferimento: date | None
    data_scadenza: date | None
    esito_motivo: str | None
    created_at: datetime


class EsitoIn(BaseModel):
    esito: Literal["accettata", "scartata"]
    # Data della ricevuta AdE: per un conferimento accettato e' la data di conferimento
    data: date
    motivo: str | None = None

    @model_validator(mode="after")
    def _motivo(self) -> Self:
        if self.esito == "scartata" and not self.motivo:
            raise ValueError("Indicare il motivo dello scarto riportato nella ricevuta")
        return self


class VoceRegistroOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    numero: int
    data: date
    delega_id: uuid.UUID
    tipo_richiesta: TipoRichiesta
    delegante_cf: str
    delegante_nominativo: str
    sottoscrittore_cf: str
    sottoscrittore_nominativo: str
    documento_estremi: str | None
    servizi: list[str]
    hash: str
    created_at: datetime


class VerificaRegistroOut(BaseModel):
    integro: bool
    voci_controllate: int
    primo_errore: str | None


class TenantUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    cognome: str | None = Field(None, max_length=80)
    nome: str | None = Field(None, max_length=80)


class EsitoCaricamentoOut(BaseModel):
    numero_registro: int
    firmatari: list[dict]
    avvisi: list[str]


class FornituraIn(BaseModel):
    delega_ids: list[uuid.UUID] = Field(min_length=1, max_length=300)
    email: EmailStr | None = None
    cellulare: str | None = Field(None, pattern=r"^[0-9]{6,12}$")
