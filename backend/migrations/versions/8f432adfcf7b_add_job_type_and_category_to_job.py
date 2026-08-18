"""add job_type and category to job

Revision ID: 8f432adfcf7b
Revises: b3cb501ba547
Create Date: 2026-08-11 15:12:11.952311

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database (job_fts and its shadow
tables) — stripped. server_default='formal' on the new NOT NULL job_type
column so backfilling it onto any database with pre-existing Job rows
doesn't fail, and so every job created before this migration (and every
mobile client that doesn't yet send job_type) keeps behaving exactly like
today's CV-required formal job. Split out from the application.cv_file
and rating-table changes so each is independently reviewable/droppable —
see 631f0683fc42, 110621e2ee84, 3fdb04e1e185.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '8f432adfcf7b'
down_revision = 'b3cb501ba547'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('job_type', sa.String(length=20), nullable=False, server_default='formal'))
        batch_op.add_column(sa.Column('category', sa.String(length=40), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('category')
        batch_op.drop_column('job_type')
