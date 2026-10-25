import enum
import uuid
from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.domain.regole import ModalitaFirma, Qualifica, TipoDocumento, TipoRichiesta
from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin, str_enum, utcnow
from app.models.cliente import Cliente


class StatoDelega(enum.StrEnum):
    BOZZA = "bozza"
    # File XML generato e consegnato al cliente per la firma
    IN_FIRMA = "in_firma"
    # Firma acquisita e delega annotata nel registro cronologico
    FIRMATA = "firmata"
    INVIATA = "inviata"
    # Conferimento o rinnovo accettato dall'AdE: la delega e' in vigore
    ATTIVA = "attiva"
    # Revoca accettata dall'AdE
    ACCETTATA = "accettata"
    SCARTATA = "scartata"
    # Sostituita da un nuovo conferimento o da un rinnovo
    SOSTITUITA = "sostituita"
    REVOCATA = "revocata"


class Delega(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    """Una comunicazione di conferimento, revoca o rinnovo per un cliente."""

    __tablename__ = "deleghe"

    cliente_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clienti.id"), index=True)
    tipo_richiesta: Mapped[TipoRichiesta] = mapped_column(str_enum(TipoRichiesta, 1))
    servizi: Mapped[list[str]] = mapped_column(JSON, default=list)
    # Per revoca e rinnovo: la delega attiva a cui si riferiscono
    delega_origine_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("deleghe.id"))

    sottoscrittore_cf: Mapped[str] = mapped_column(String(16))
    sottoscrittore_cognome: Mapped[str] = mapped_column(String(80))
    sottoscrittore_nome: Mapped[str] = mapped_column(String(80))
    qualifica: Mapped[Qualifica] = mapped_column(str_enum(Qualifica, 1))

    documento_tipo: Mapped[TipoDocumento | None] = mapped_column(str_enum(TipoDocumento, 1))
    documento_rilasciato_da: Mapped[str | None] = mapped_column(String(100))
    documento_numero: Mapped[str | None] = mapped_column(String(30))
    documento_scadenza: Mapped[date | None] = mapped_column(Date)
    documento_tipo_altro: Mapped[str | None] = mapped_column(String(100))

    modalita_firma: Mapped[ModalitaFirma] = mapped_column(str_enum(ModalitaFirma, 32))
    luogo_firma: Mapped[str | None] = mapped_column(String(100))
    data_firma: Mapped[date] = mapped_column(Date)

    stato: Mapped[StatoDelega] = mapped_column(
        str_enum(StatoDelega, 16), default=StatoDelega.BOZZA, index=True
    )
    data_conferimento: Mapped[date | None] = mapped_column(Date)
    data_scadenza: Mapped[date | None] = mapped_column(Date, index=True)
    esito_motivo: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    cliente: Mapped[Cliente] = relationship(lazy="joined")
    delega_origine: Mapped["Delega | None"] = relationship(remote_side="Delega.id")

    @property
    def ha_documento(self) -> bool:
        return self.documento_tipo is not None
