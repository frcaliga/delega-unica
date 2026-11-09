"""Letture dei documenti d'identita' in corso o appena concluse.

La lettura impegna la CPU per decine di secondi: gira in background e la pagina ne chiede l'esito.
L'esito (dati letti e anteprima del documento) e' cifrato come le copie. Esito e copia caricata
restano, cifrati, finche' non si salva il cliente o il documento, e comunque non oltre un'ora.
"""

import uuid

from sqlalchemy import ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin
from app.services.cifratura import Busta


class LetturaDocumento(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "letture_documenti"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))
    # in_corso, pronta, errore; consegnata o chiusa (errore) quando la pagina ha avuto l'esito
    stato: Mapped[str] = mapped_column(String(10), default="in_corso")
    esito_cifrato: Mapped[bytes | None] = mapped_column(LargeBinary)
    esito_chiave: Mapped[bytes | None] = mapped_column(LargeBinary)
    cifratura: Mapped[str | None] = mapped_column(String(10))
    # Copia caricata, cifrata: si salva con il cliente senza ricaricarla
    copia_cifrata: Mapped[bytes | None] = mapped_column(LargeBinary)
    copia_chiave: Mapped[bytes | None] = mapped_column(LargeBinary)
    copia_cifratura: Mapped[str | None] = mapped_column(String(10))
    copia_tipo: Mapped[str | None] = mapped_column(String(100))
    copia_nome: Mapped[str | None] = mapped_column(String(150))
    copia_impronta: Mapped[str | None] = mapped_column(String(64))

    @property
    def busta(self) -> Busta:
        return Busta(self.cifratura or "", self.esito_chiave or b"", self.esito_cifrato or b"")

    @property
    def busta_copia(self) -> Busta:
        return Busta(
            self.copia_cifratura or "", self.copia_chiave or b"", self.copia_cifrata or b""
        )

    def contesto(self) -> dict[str, str]:
        return {"tenant": str(self.tenant_id), "lettura": str(self.id)}

    def contesto_copia(self) -> dict[str, str]:
        return {**self.contesto(), "parte": "copia"}
