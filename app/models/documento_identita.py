"""Documenti d'identita' dei clienti persone fisiche, con la copia cifrata nell'applicazione.

Servono a compilare l'anagrafica e gli estremi del documento (deleghe, adesione FEA). A differenza
delle evidenze FEA, che si conservano vent'anni dentro l'adesione, queste copie si possono
cancellare quando non servono piu'.
"""

import uuid
from datetime import date
from typing import Any

from sqlalchemy import JSON, Date, ForeignKey, LargeBinary, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.domain.regole import TipoDocumento
from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin, str_enum
from app.services.cifratura import Busta


class DocumentoIdentita(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    __tablename__ = "documenti_identita"

    cliente_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("clienti.id"), index=True)
    tipo: Mapped[TipoDocumento] = mapped_column(str_enum(TipoDocumento, 1))
    numero: Mapped[str | None] = mapped_column(String(30))
    rilasciato_da: Mapped[str | None] = mapped_column(String(100))
    scadenza: Mapped[date | None] = mapped_column(Date)

    # Copia cifrata (AES-256-GCM) e chiave dati cifrata con la chiave principale
    copia_cifrata: Mapped[bytes] = mapped_column(LargeBinary)
    copia_chiave: Mapped[bytes] = mapped_column(LargeBinary)
    cifratura: Mapped[str] = mapped_column(String(10))
    copia_tipo: Mapped[str] = mapped_column(String(100))
    copia_nome: Mapped[str] = mapped_column(String(150))
    # Impronta della copia in chiaro
    copia_sha256: Mapped[str] = mapped_column(String(64))
    # Esito della lettura automatica, senza i dati personali (che stanno nei campi confermati)
    lettura: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Operatore che ha caricato il documento
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"))

    @property
    def busta(self) -> Busta:
        return Busta(self.cifratura, self.copia_chiave, self.copia_cifrata)

    def contesto(self) -> dict[str, str]:
        return {"tenant": str(self.tenant_id), "documento": str(self.id)}

    def scaduto(self, oggi: date) -> bool:
        return self.scadenza is not None and self.scadenza < oggi
