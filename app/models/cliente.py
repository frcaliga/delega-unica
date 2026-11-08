from datetime import date

from sqlalchemy import Boolean, Date, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TenantScopedMixin, TimestampMixin


class Cliente(IdMixin, TimestampMixin, TenantScopedMixin, Base):
    """Contribuente che conferisce la delega allo studio (soggetto delegante)."""

    __tablename__ = "clienti"
    __table_args__ = (UniqueConstraint("tenant_id", "codice_fiscale", name="uq_clienti_cf"),)

    codice_fiscale: Mapped[str] = mapped_column(String(16))
    # Persona fisica: cognome e nome. Soggetto diverso: denominazione.
    cognome: Mapped[str | None] = mapped_column(String(80))
    nome: Mapped[str | None] = mapped_column(String(80))
    denominazione: Mapped[str | None] = mapped_column(String(150))
    partita_iva_attiva: Mapped[bool] = mapped_column(Boolean, default=False)
    email: Mapped[str | None] = mapped_column(String(320))
    telefono: Mapped[str | None] = mapped_column(String(30))
    # Persona fisica: letti dal documento d'identita' o inseriti a mano
    data_nascita: Mapped[date | None] = mapped_column(Date)
    sesso: Mapped[str | None] = mapped_column(String(1))
    # Comune (e provincia) o Stato estero di nascita
    luogo_nascita: Mapped[str | None] = mapped_column(String(100))

    @property
    def persona_fisica(self) -> bool:
        return len(self.codice_fiscale) == 16

    @property
    def nominativo(self) -> str:
        if self.persona_fisica:
            return f"{self.cognome} {self.nome}"
        return self.denominazione or ""
