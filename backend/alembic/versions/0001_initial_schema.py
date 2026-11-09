"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-09-29 14:04:31.298338
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

revision: str = '0001'
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUM_NAMES = ['job_type', 'experience_level', 'ats_platform', 'application_status', 'email_direction', 'email_intent', 'interview_type']


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    postgresql.ENUM('full-time', 'part-time', 'internship', 'contract', 'freelance', name='job_type').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('entry', 'mid', 'senior', 'lead', 'executive', 'internship', name='experience_level').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('linkedin', 'indeed', 'glassdoor', 'wellfound', 'greenhouse', 'lever', 'workday', 'ashby', 'bamboohr', 'icims', 'taleo', 'smartrecruiters', 'jobvite', 'custom', 'unknown', name='ats_platform').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('discovered', 'matched', 'skipped', 'preparing', 'pending_approval', 'approved', 'applied', 'acknowledged', 'screening', 'interview', 'assessment', 'final_round', 'offer', 'accepted', 'rejected', 'withdrawn', 'failed', name='application_status').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('inbound', 'outbound', name='email_direction').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('acknowledgment', 'rejection', 'interview_invite', 'assessment', 'offer', 'follow_up', 'info_request', 'generic', 'unknown', name='email_intent').create(op.get_bind(), checkfirst=True)
    postgresql.ENUM('phone_screen', 'video_call', 'onsite', 'technical', 'behavioral', 'panel', 'take_home', 'pair_programming', 'other', name='interview_type').create(op.get_bind(), checkfirst=True)
    op.create_table('jobs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('company_name', sa.String(length=255), nullable=False),
    sa.Column('company_logo_url', sa.Text(), nullable=True),
    sa.Column('company_domain', sa.String(length=255), nullable=True),
    sa.Column('role_title', sa.String(length=255), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('requirements', sa.Text(), nullable=True),
    sa.Column('nice_to_haves', sa.Text(), nullable=True),
    sa.Column('job_type', postgresql.ENUM(name='job_type', create_type=False), nullable=True),
    sa.Column('experience_level', postgresql.ENUM(name='experience_level', create_type=False), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('is_remote', sa.Boolean(), nullable=False),
    sa.Column('salary_min', sa.Integer(), nullable=True),
    sa.Column('salary_max', sa.Integer(), nullable=True),
    sa.Column('salary_currency', sa.String(length=10), nullable=True),
    sa.Column('source_url', sa.Text(), nullable=False),
    sa.Column('source_platform', postgresql.ENUM(name='ats_platform', create_type=False), nullable=False),
    sa.Column('application_url', sa.Text(), nullable=True),
    sa.Column('external_id', sa.String(length=255), nullable=True),
    sa.Column('easy_apply', sa.Boolean(), nullable=False),
    sa.Column('dedupe_key', sa.String(length=512), nullable=True),
    sa.Column('description_embedding', Vector(1536), nullable=True),
    sa.Column('extracted_skills', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('extracted_requirements', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('raw_data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('posted_date', sa.Date(), nullable=True),
    sa.Column('deadline_date', sa.Date(), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('discovered_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_checked', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_url')
    )
    op.create_index('idx_jobs_company', 'jobs', ['company_name'], unique=False)
    op.create_index('idx_jobs_dedupe', 'jobs', ['dedupe_key'], unique=False)
    op.create_index('idx_jobs_platform', 'jobs', ['source_platform'], unique=False)
    op.create_index(op.f('ix_jobs_is_active'), 'jobs', ['is_active'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('full_name', sa.String(length=255), nullable=False),
    sa.Column('phone', sa.String(length=50), nullable=True),
    sa.Column('linkedin_url', sa.Text(), nullable=True),
    sa.Column('location', sa.String(length=255), nullable=True),
    sa.Column('hashed_password', sa.String(length=255), nullable=True),
    sa.Column('google_access_token', sa.Text(), nullable=True),
    sa.Column('google_refresh_token', sa.Text(), nullable=True),
    sa.Column('google_token_expiry', sa.DateTime(timezone=True), nullable=True),
    sa.Column('google_scopes', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('google_email', sa.String(length=255), nullable=True),
    sa.Column('gmail_history_id', sa.String(length=64), nullable=True),
    sa.Column('gmail_watch_expiration', sa.DateTime(timezone=True), nullable=True),
    sa.Column('gmail_last_polled_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('linkedin_session_cookie', sa.Text(), nullable=True),
    sa.Column('linkedin_cookie_updated_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('linkedin_session_valid', sa.Boolean(), nullable=False),
    sa.Column('linkedin_profile_snapshot', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ats_credentials', sa.Text(), nullable=True),
    sa.Column('preferences', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('consents', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_scan_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('agent_runs',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('run_type', sa.String(length=50), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('trigger', sa.String(length=20), nullable=True),
    sa.Column('jobs_discovered', sa.Integer(), nullable=False),
    sa.Column('jobs_matched', sa.Integer(), nullable=False),
    sa.Column('applications_prepared', sa.Integer(), nullable=False),
    sa.Column('applications_submitted', sa.Integer(), nullable=False),
    sa.Column('errors_count', sa.Integer(), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration_seconds', sa.Integer(), nullable=True),
    sa.Column('log', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agent_runs_user_id'), 'agent_runs', ['user_id'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('event_type', sa.String(length=64), nullable=False),
    sa.Column('title', sa.String(length=255), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('link', sa.Text(), nullable=True),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('is_read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_created_at'), 'notifications', ['created_at'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_table('resumes',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('label', sa.String(length=255), nullable=True),
    sa.Column('original_file_url', sa.Text(), nullable=True),
    sa.Column('original_filename', sa.String(length=255), nullable=True),
    sa.Column('raw_text', sa.Text(), nullable=True),
    sa.Column('parsed_content', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('skills_embedding', Vector(1536), nullable=True),
    sa.Column('is_master', sa.Boolean(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('parent_resume_id', sa.Uuid(), nullable=True),
    sa.Column('tailored_for_job_id', sa.Uuid(), nullable=True),
    sa.Column('changes_made', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('pdf_url', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['parent_resume_id'], ['resumes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tailored_for_job_id'], ['jobs.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_resumes_user_id'), 'resumes', ['user_id'], unique=False)
    op.create_table('user_field_mappings',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('field_name', sa.String(length=255), nullable=False),
    sa.Column('field_value', sa.Text(), nullable=False),
    sa.Column('field_type', sa.String(length=50), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'field_name', name='uq_user_field_mapping')
    )
    op.create_index(op.f('ix_user_field_mappings_user_id'), 'user_field_mappings', ['user_id'], unique=False)
    op.create_table('applications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('job_id', sa.Uuid(), nullable=False),
    sa.Column('status', postgresql.ENUM(name='application_status', create_type=False), nullable=False),
    sa.Column('match_score', sa.Integer(), nullable=True),
    sa.Column('match_reasoning', sa.Text(), nullable=True),
    sa.Column('match_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('similarity_score', sa.Double(), nullable=True),
    sa.Column('tailored_resume_id', sa.Uuid(), nullable=True),
    sa.Column('tailored_resume_pdf_url', sa.Text(), nullable=True),
    sa.Column('cover_letter', sa.Text(), nullable=True),
    sa.Column('custom_answers', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('ats_platform', postgresql.ENUM(name='ats_platform', create_type=False), nullable=True),
    sa.Column('form_fields', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('needs_manual_review', sa.Boolean(), nullable=False),
    sa.Column('manual_review_reason', sa.Text(), nullable=True),
    sa.Column('form_screenshot_url', sa.Text(), nullable=True),
    sa.Column('staged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('confirmation_screenshot_url', sa.Text(), nullable=True),
    sa.Column('confirmation_number', sa.String(length=255), nullable=True),
    sa.Column('first_response_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('offer_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('error_log', sa.Text(), nullable=True),
    sa.Column('retry_count', sa.Integer(), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ),
    sa.ForeignKeyConstraint(['tailored_resume_id'], ['resumes.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'job_id', name='uq_application_user_job')
    )
    op.create_index('idx_applications_user_status', 'applications', ['user_id', 'status'], unique=False)
    op.create_index(op.f('ix_applications_job_id'), 'applications', ['job_id'], unique=False)
    op.create_index(op.f('ix_applications_status'), 'applications', ['status'], unique=False)
    op.create_index(op.f('ix_applications_user_id'), 'applications', ['user_id'], unique=False)
    op.create_table('application_status_history',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('old_status', postgresql.ENUM(name='application_status', create_type=False), nullable=True),
    sa.Column('new_status', postgresql.ENUM(name='application_status', create_type=False), nullable=False),
    sa.Column('changed_by', sa.String(length=50), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_application_status_history_application_id'), 'application_status_history', ['application_id'], unique=False)
    op.create_table('communications',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('user_id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=True),
    sa.Column('gmail_message_id', sa.String(length=255), nullable=True),
    sa.Column('gmail_thread_id', sa.String(length=255), nullable=True),
    sa.Column('gmail_label_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('direction', postgresql.ENUM(name='email_direction', create_type=False), nullable=False),
    sa.Column('sender_email', sa.String(length=255), nullable=True),
    sa.Column('sender_name', sa.String(length=255), nullable=True),
    sa.Column('recipient_email', sa.String(length=255), nullable=True),
    sa.Column('subject', sa.Text(), nullable=True),
    sa.Column('body_text', sa.Text(), nullable=True),
    sa.Column('body_html', sa.Text(), nullable=True),
    sa.Column('attachments', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('detected_intent', postgresql.ENUM(name='email_intent', create_type=False), nullable=True),
    sa.Column('intent_confidence', sa.Float(), nullable=True),
    sa.Column('urgency', sa.String(length=16), nullable=True),
    sa.Column('extracted_details', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('suggested_reply', sa.Text(), nullable=True),
    sa.Column('gmail_draft_id', sa.String(length=255), nullable=True),
    sa.Column('is_action_required', sa.Boolean(), nullable=False),
    sa.Column('action_taken', sa.Boolean(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('gmail_message_id')
    )
    op.create_index('idx_comms_action', 'communications', ['is_action_required'], unique=False)
    op.create_index(op.f('ix_communications_application_id'), 'communications', ['application_id'], unique=False)
    op.create_index(op.f('ix_communications_gmail_thread_id'), 'communications', ['gmail_thread_id'], unique=False)
    op.create_index(op.f('ix_communications_user_id'), 'communications', ['user_id'], unique=False)
    op.create_table('interviews',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('application_id', sa.Uuid(), nullable=False),
    sa.Column('communication_id', sa.Uuid(), nullable=True),
    sa.Column('google_event_id', sa.String(length=255), nullable=True),
    sa.Column('google_event_link', sa.Text(), nullable=True),
    sa.Column('interview_type', postgresql.ENUM(name='interview_type', create_type=False), nullable=True),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('timezone', sa.String(length=50), nullable=False),
    sa.Column('meeting_link', sa.Text(), nullable=True),
    sa.Column('meeting_platform', sa.String(length=50), nullable=True),
    sa.Column('physical_location', sa.Text(), nullable=True),
    sa.Column('interviewer_names', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('interviewer_titles', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('interviewer_linkedin_urls', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('prep_notes', sa.Text(), nullable=True),
    sa.Column('company_research', sa.Text(), nullable=True),
    sa.Column('likely_questions', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('outcome', sa.String(length=50), nullable=True),
    sa.Column('feedback', sa.Text(), nullable=True),
    sa.Column('reminder_24h_sent', sa.Boolean(), nullable=False),
    sa.Column('reminder_1h_sent', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['applications.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['communication_id'], ['communications.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('google_event_id')
    )
    op.create_index(op.f('ix_interviews_application_id'), 'interviews', ['application_id'], unique=False)
    op.create_index(op.f('ix_interviews_scheduled_at'), 'interviews', ['scheduled_at'], unique=False)
    # PLAN.md §4 extras: vector similarity + partial / trigram indexes
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_embedding ON jobs USING ivfflat (description_embedding vector_cosine_ops) WITH (lists = 100)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_active ON jobs (is_active) WHERE is_active = true")
    op.execute("CREATE INDEX IF NOT EXISTS idx_jobs_title_trgm ON jobs USING gin (role_title gin_trgm_ops)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_comms_action_required ON communications (is_action_required) WHERE is_action_required = true")


def downgrade() -> None:
    op.drop_index(op.f('ix_interviews_scheduled_at'), table_name='interviews')
    op.drop_index(op.f('ix_interviews_application_id'), table_name='interviews')
    op.drop_table('interviews')
    op.drop_index(op.f('ix_communications_user_id'), table_name='communications')
    op.drop_index(op.f('ix_communications_gmail_thread_id'), table_name='communications')
    op.drop_index(op.f('ix_communications_application_id'), table_name='communications')
    op.drop_index('idx_comms_action', table_name='communications')
    op.drop_table('communications')
    op.drop_index(op.f('ix_application_status_history_application_id'), table_name='application_status_history')
    op.drop_table('application_status_history')
    op.drop_index(op.f('ix_applications_user_id'), table_name='applications')
    op.drop_index(op.f('ix_applications_status'), table_name='applications')
    op.drop_index(op.f('ix_applications_job_id'), table_name='applications')
    op.drop_index('idx_applications_user_status', table_name='applications')
    op.drop_table('applications')
    op.drop_index(op.f('ix_user_field_mappings_user_id'), table_name='user_field_mappings')
    op.drop_table('user_field_mappings')
    op.drop_index(op.f('ix_resumes_user_id'), table_name='resumes')
    op.drop_table('resumes')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_created_at'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_index(op.f('ix_agent_runs_user_id'), table_name='agent_runs')
    op.drop_table('agent_runs')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_jobs_is_active'), table_name='jobs')
    op.drop_index('idx_jobs_platform', table_name='jobs')
    op.drop_index('idx_jobs_dedupe', table_name='jobs')
    op.drop_index('idx_jobs_company', table_name='jobs')
    op.drop_table('jobs')
    for name in ENUM_NAMES:
        op.execute(f"DROP TYPE IF EXISTS {name}")
