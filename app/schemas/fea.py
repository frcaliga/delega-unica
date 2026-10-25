import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.regole import TipoDocumento
from app.models import StatoAdesione


class AdesioneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    cliente_id: uuid.UUID
    codice_fiscale: str
    documento_tipo: TipoDocumento
    documento_numero: str
    documento_scadenza: date
    cellulare: str
    versione_condizioni: str
    dichiarazione_sha256: str
    stato: StatoAdesione
    confermata_il: datetime | None
    revocata_il: datetime | None
    created_at: datetime


class CodiceIn(BaseModel):
    codice: str = Field(pattern=r"^\s*\d{6}\s*$")


class OtpInviatoOut(BaseModel):
    cellulare: str
    scade_il: datetime


class FirmaFeaOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seriale_certificato: str
    ora_firma: datetime
    sha256_busta: str
