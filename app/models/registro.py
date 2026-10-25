import uuid
from datetime import date

from sqlalchemy import JSON, Date, ForeignKey, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.regole import TipoRichiesta
from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin, str_enum


class VoceRegistro(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    """Voce del registro cronologico delle deleghe (punto 9 del provvedimento).

    Solo inserimenti: il database rifiuta modifiche e cancellazioni. Ogni voce contiene l'hash
    della precedente dello stesso studio, cosi' un'alterazione fuori dall'applicazione si vede.
    I dati sono copiati e non collegati, perche' il registro deve restare com'era al momento.
    """

    __tablename__ = "registro_deleghe"
    __table_args__ = (UniqueConstraint("tenant_id", "numero", name="uq_registro_numero"),)

    numero: Mapped[int] = mapped_column(Integer)
    data: Mapped[date] = mapped_column(Date)
    delega_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("deleghe.id"))
    tipo_richiesta: Mapped[TipoRichiesta] = mapped_column(str_enum(TipoRichiesta, 1))
    delegante_cf: Mapped[str] = mapped_column(String(16))
    delegante_nominativo: Mapped[str] = mapped_column(String(161))
    sottoscrittore_cf: Mapped[str] = mapped_column(String(16))
    sottoscrittore_nominativo: Mapped[str] = mapped_column(String(161))
    documento_estremi: Mapped[str | None] = mapped_column(String(300))
    servizi: Mapped[list[str]] = mapped_column(JSON, default=list)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    hash_precedente: Mapped[str | None] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
