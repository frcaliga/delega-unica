"""copia del documento conservata con la lettura, fino al salvataggio

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-08 23:30:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("letture_documenti", sa.Column("copia_cifrata", sa.LargeBinary(), nullable=True))
    op.add_column("letture_documenti", sa.Column("copia_chiave", sa.LargeBinary(), nullable=True))
    op.add_column("letture_documenti", sa.Column("copia_cifratura", sa.String(10), nullable=True))
    op.add_column("letture_documenti", sa.Column("copia_tipo", sa.String(100), nullable=True))
    op.add_column("letture_documenti", sa.Column("copia_nome", sa.String(150), nullable=True))
    op.add_column("letture_documenti", sa.Column("copia_impronta", sa.String(64), nullable=True))


def downgrade() -> None:
    for colonna in (
        "copia_impronta",
        "copia_nome",
        "copia_tipo",
        "copia_cifratura",
        "copia_chiave",
        "copia_cifrata",
    ):
        op.drop_column("letture_documenti", colonna)
