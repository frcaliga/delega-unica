"""FEA erogata dallo studio con la piattaforma (DPCM 22/2/2013, artt. 56 e 57).

Lo studio identifica il cliente di persona con un documento valido, ne raccoglie la dichiarazione
di accettazione delle condizioni del servizio e conserva per vent'anni copia del documento, la
dichiarazione e le evidenze di ogni firma.
"""

import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.regole import TipoDocumento
from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin, str_enum
from app.models.cliente import Cliente


class StatoAdesione(enum.StrEnum):
    # Dichiarazione generata, in attesa della conferma con OTP
    DA_CONFERMARE = "da_confermare"
    ATTIVA = "attiva"
    REVOCATA = "revocata"


class AdesioneFea(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "adesioni_fea"

    cliente_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clienti.id"), index=True)
    codice_fiscale: Mapped[str] = mapped_column(String(16), index=True)
    cognome: Mapped[str] = mapped_column(String(80))
    nome: Mapped[str] = mapped_column(String(80))

    documento_tipo: Mapped[TipoDocumento] = mapped_column(str_enum(TipoDocumento, 1))
    documento_numero: Mapped[str] = mapped_column(String(30))
    documento_rilasciato_da: Mapped[str] = mapped_column(String(100))
    documento_scadenza: Mapped[date] = mapped_column(Date)
    # Copia del documento esibito allo sportello
    copia_documento: Mapped[bytes] = mapped_column(LargeBinary)
    copia_documento_tipo: Mapped[str] = mapped_column(String(100))
    copia_documento_sha256: Mapped[str] = mapped_column(String(64))

    cellulare: Mapped[str] = mapped_column(String(20))
    versione_condizioni: Mapped[str] = mapped_column(String(20))
    dichiarazione: Mapped[bytes] = mapped_column(LargeBinary)
    dichiarazione_sha256: Mapped[str] = mapped_column(String(64))

    stato: Mapped[StatoAdesione] = mapped_column(
        str_enum(StatoAdesione, 16), default=StatoAdesione.DA_CONFERMARE
    )
    confermata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Operatore che ha identificato il cliente
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))

    cliente: Mapped[Cliente] = relationship(lazy="joined")


class ScopoOtp(enum.StrEnum):
    ADESIONE = "adesione"
    FIRMA = "firma"


class SessioneOtp(TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "sessioni_otp"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    scopo: Mapped[ScopoOtp] = mapped_column(str_enum(ScopoOtp, 16))
    # Adesione da confermare o delega da firmare
    riferimento_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    cellulare: Mapped[str] = mapped_column(String(20))
    codice_hmac: Mapped[str] = mapped_column(String(64))
    # Impronta del documento mostrato al cliente quando e' stato inviato il codice
    sha256_documento: Mapped[str] = mapped_column(String(64))
    scade_il: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    tentativi: Mapped[int] = mapped_column(Integer, default=0)
    usata_il: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    annullata: Mapped[bool] = mapped_column(Boolean, default=False)


class EvidenzaFirma(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    """Evidenze di una firma FEA apposta dal cliente con la piattaforma."""

    __tablename__ = "evidenze_firma"

    delega_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("deleghe.id"), index=True)
    adesione_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("adesioni_fea.id"))
    sessione_otp_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessioni_otp.id"))
    sha256_xml: Mapped[str] = mapped_column(String(64))
    sha256_busta: Mapped[str] = mapped_column(String(64))
    certificato: Mapped[bytes] = mapped_column(LargeBinary)
    seriale_certificato: Mapped[str] = mapped_column(String(64))
    ora_firma: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    # Operatore presente allo sportello
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
