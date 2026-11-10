"""career copilot chat tables, and composite indexes for the queries the dashboard runs most

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-09 12:00:00.000000

New tables
    chat_sessions, chat_messages - conversations with the career copilot.

New B-tree indexes (each one matches a query that used to scan or sort a user's whole table)
    applications (user_id, match_score)      Swipe Review deck order, best matches
    applications (user_id, reviewed_at)      undo stack, "kept today"
    application_status_history (application_id, created_at)   an application's timeline, in order
    jobs (discovered_at)                     "newest first" lists, search index build
    notifications (user_id, is_read, created_at)              unread count in the bell
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0008'
down_revision: str | None = '0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('title', sa.String(length=120), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_chat_sessions_user_updated', 'chat_sessions', ['user_id', 'updated_at'], unique=False)
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('session_id', sa.Uuid(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('meta', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['chat_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_chat_messages_session_created', 'chat_messages', ['session_id', 'created_at'], unique=False)

    op.create_index('idx_applications_user_score', 'applications', ['user_id', 'match_score'], unique=False)
    op.create_index('idx_applications_user_reviewed', 'applications', ['user_id', 'reviewed_at'], unique=False)
    op.create_index('idx_status_history_app_created', 'application_status_history', ['application_id', 'created_at'],
                    unique=False)
    op.create_index('idx_jobs_discovered_at', 'jobs', ['discovered_at'], unique=False)
    op.create_index('idx_notifications_user_unread', 'notifications', ['user_id', 'is_read', 'created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_notifications_user_unread', table_name='notifications')
    op.drop_index('idx_jobs_discovered_at', table_name='jobs')
    op.drop_index('idx_status_history_app_created', table_name='application_status_history')
    op.drop_index('idx_applications_user_reviewed', table_name='applications')
    op.drop_index('idx_applications_user_score', table_name='applications')
    op.drop_index('idx_chat_messages_session_created', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('idx_chat_sessions_user_updated', table_name='chat_sessions')
    op.drop_table('chat_sessions')
