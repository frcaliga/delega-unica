from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class Tenant(IdMixin, TimestampMixin, Base):
    """Uno studio o CAF che usa il SaaS: e' l'intermediario delegato."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200))
    # Codice fiscale dell'intermediario abilitato Entratel (11 o 16 caratteri)
    codice_fiscale: Mapped[str] = mapped_column(String(16), unique=True)
    # Obbligatori nel file XML quando l'intermediario e' una persona fisica (codice di 16 caratteri)
    cognome: Mapped[str | None] = mapped_column(String(80))
    nome: Mapped[str | None] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
