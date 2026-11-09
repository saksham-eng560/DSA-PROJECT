"""internshala session + review-queue field overrides

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-01 09:00:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table('users') as batch_op:
        batch_op.add_column(sa.Column('internshala_session', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('internshala_session_updated_at', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('internshala_session_valid', sa.Boolean(), server_default=sa.false(), nullable=False))
    with op.batch_alter_table('applications') as batch_op:
        batch_op.add_column(sa.Column('field_overrides', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'),
                                      nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('applications') as batch_op:
        batch_op.drop_column('field_overrides')
    with op.batch_alter_table('users') as batch_op:
        batch_op.drop_column('internshala_session_valid')
        batch_op.drop_column('internshala_session_updated_at')
        batch_op.drop_column('internshala_session')
