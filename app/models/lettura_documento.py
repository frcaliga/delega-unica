"""Letture dei documenti d'identita' in corso o appena concluse.

La lettura impegna la CPU per decine di secondi: gira in background e la pagina ne chiede l'esito.
L'esito (dati letti e anteprima del documento) e' cifrato come le copie e la riga si cancella
appena consegnato, o dopo un'ora.
"""

import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin
from app.services.cifratura import Busta


class LetturaDocumento(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "letture_documenti"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    # in_corso, pronta, errore
    stato: Mapped[str] = mapped_column(String(10), default="in_corso")
    esito_cifrato: Mapped[bytes | None] = mapped_column(LargeBinary)
    esito_chiave: Mapped[bytes | None] = mapped_column(LargeBinary)
    cifratura: Mapped[str | None] = mapped_column(String(10))

    @property
    def busta(self) -> Busta:
        return Busta(self.cifratura or "", self.esito_chiave or b"", self.esito_cifrato or b"")

    def contesto(self) -> dict[str, str]:
        return {"tenant": str(self.tenant_id), "lettura": str(self.id)}
