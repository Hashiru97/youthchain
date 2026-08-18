"""add saved_job table

Revision ID: e7a3c9f52b81
Revises: d4f8b2e91c73
Create Date: 2026-08-18 09:00:00.000000

Adds saved_job, the bookmark-toggle join table for the "Saved Jobs"
feature — one row per (user_id, job_id), same unique-pair convention
Application already established (see its own uq_user_job constraint).
Works uniformly for both Home (employer/gig) and Discover (scraped)
jobs since they already share one job table (see SavedJob's own
docstring in app.py) — no separate table per job source.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a3c9f52b81'
down_revision = 'd4f8b2e91c73'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'saved_job',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['job_id'], ['job.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'job_id', name='uq_saved_job_user_job'),
    )
    with op.batch_alter_table('saved_job', schema=None) as batch_op:
        # GET /api/saved_jobs filters on user_id and orders by created_at
        # on every request — worth its own index rather than a full
        # table scan as this grows.
        batch_op.create_index(batch_op.f('ix_saved_job_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('saved_job', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_job_user_id'))
    op.drop_table('saved_job')
