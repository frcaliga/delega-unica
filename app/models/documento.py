import uuid
from typing import Any

from sqlalchemy import JSON, ForeignKey, LargeBinary, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin


class DocumentoFirmato(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    """Busta CAdES (.p7m) della delega firmata, verificata al caricamento."""

    __tablename__ = "documenti_firmati"
    __table_args__ = (UniqueConstraint("delega_id", name="uq_documenti_firmati_delega"),)

    delega_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("deleghe.id"))
    nome_file: Mapped[str] = mapped_column(String(100))
    contenuto: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    # Riepilogo dei firmatari per livello di busta, dall'esterno verso l'interno
    firmatari: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
