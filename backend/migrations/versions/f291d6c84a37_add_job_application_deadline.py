"""add job application_deadline

Revision ID: f291d6c84a37
Revises: e7a3c9f52b81
Create Date: 2026-08-18 10:00:00.000000

Adds one new nullable column, job.application_deadline — a real Date
parsed by scanner.pipeline from a scraped listing's own stated deadline
(see scanner/claude_extractor.py's new "deadline" field), or null when
the source never stated one or it couldn't be parsed. Employer-posted
jobs never set this (post_job() has no deadline field) — always null
there, which _search_jobs()'s expired_only filter treats as "never
expires", so this column is a pure no-op for the Home feed.

No backfill needed: every pre-existing row (employer-posted or already
scraped before this column existed) simply has no deadline until the
scanner extracts one on its next scan, same convention as
employment_type (c3e7a9f14b02) and every other nullable scraped-only
column before it.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f291d6c84a37'
down_revision = 'e7a3c9f52b81'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('application_deadline', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('application_deadline')
