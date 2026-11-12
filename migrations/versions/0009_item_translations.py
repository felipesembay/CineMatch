"""item translations (pt-BR display text)

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-12 10:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "item_translations",
        sa.Column("item_id", sa.BigInteger(), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column("source_language", sa.String(length=8), nullable=False),
        sa.Column("raw_uri", sa.Text(), nullable=True),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["item_id"],
            ["catalog_items.id"],
            name=op.f("fk_item_translations_item_id_catalog_items"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("item_id", "language", name=op.f("pk_item_translations")),
    )


def downgrade() -> None:
    op.drop_table("item_translations")
