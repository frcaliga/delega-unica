"""documenti d'identita' dei clienti

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-08 18:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("clienti", sa.Column("data_nascita", sa.Date(), nullable=True))
    op.add_column("clienti", sa.Column("sesso", sa.String(length=1), nullable=True))
    op.create_table(
        "documenti_identita",
        sa.Column("cliente_id", sa.Uuid(), nullable=False),
        sa.Column(
            "tipo",
            sa.Enum(
                "1",
                "2",
                "3",
                "4",
                name="tipodocumento",
                native_enum=False,
                create_constraint=False,
                length=1,
            ),
            nullable=False,
        ),
        sa.Column("numero", sa.String(length=30), nullable=True),
        sa.Column("rilasciato_da", sa.String(length=100), nullable=True),
        sa.Column("scadenza", sa.Date(), nullable=True),
        sa.Column("copia_cifrata", sa.LargeBinary(), nullable=False),
        sa.Column("copia_chiave", sa.LargeBinary(), nullable=False),
        sa.Column("cifratura", sa.String(length=10), nullable=False),
        sa.Column("copia_tipo", sa.String(length=100), nullable=False),
        sa.Column("copia_nome", sa.String(length=150), nullable=False),
        sa.Column("copia_sha256", sa.String(length=64), nullable=False),
        sa.Column("lettura", sa.JSON(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["cliente_id"], ["clienti.id"]),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_documenti_identita_cliente_id", "documenti_identita", ["cliente_id"])
    op.create_index("ix_documenti_identita_tenant_id", "documenti_identita", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_documenti_identita_tenant_id", table_name="documenti_identita")
    op.drop_index("ix_documenti_identita_cliente_id", table_name="documenti_identita")
    op.drop_table("documenti_identita")
    op.drop_column("clienti", "sesso")
    op.drop_column("clienti", "data_nascita")
