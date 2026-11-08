"""letture dei documenti in background

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-08 23:00:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "letture_documenti",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stato", sa.String(length=10), nullable=False),
        sa.Column("esito_cifrato", sa.LargeBinary(), nullable=True),
        sa.Column("esito_chiave", sa.LargeBinary(), nullable=True),
        sa.Column("cifratura", sa.String(length=10), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_letture_documenti_tenant_id", "letture_documenti", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_letture_documenti_tenant_id", table_name="letture_documenti")
    op.drop_table("letture_documenti")
