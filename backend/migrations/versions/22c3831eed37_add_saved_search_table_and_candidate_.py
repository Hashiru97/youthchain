"""add saved_search table and candidate job_alerts_enabled

Revision ID: 22c3831eed37
Revises: a83f5e2c9d16
Create Date: 2026-08-18 19:06:10.754107

Autogenerate missed saved_search itself: this dev DB's own db.create_all()
(app.py, runs on every import including `flask db migrate`) had already
created it locally, so the diff only showed as a genuinely new column on
candidate. saved_search is hand-written below, same shape/index convention
as saved_job (see e7a3c9f52b81) which this feature deliberately mirrors.

FTS5 false-positive diff and the unrelated ix_job_source/ix_saved_job_user_id/
ix_scan_run_*/ix_scraped_company_name "removed index" noise stripped per
this chain's standing convention (see c1107a7da929's own docstring) — none
of that reflects a real change made here.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '22c3831eed37'
down_revision = 'a83f5e2c9d16'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'saved_search',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('q', sa.String(length=200), nullable=True),
        sa.Column('location', sa.String(length=120), nullable=True),
        sa.Column('skill', sa.String(length=120), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('saved_search', schema=None) as batch_op:
        # _dispatch_job_alerts_for_scan loads every saved_search on each
        # scan (SavedSearch.query.all()) rather than filtering by user, so
        # this index isn't for that path — it's for GET/POST
        # /api/saved_searches, which do filter by user_id on every call.
        batch_op.create_index(batch_op.f('ix_saved_search_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('candidate', schema=None) as batch_op:
        batch_op.add_column(sa.Column('job_alerts_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('candidate', schema=None) as batch_op:
        batch_op.drop_column('job_alerts_enabled')

    with op.batch_alter_table('saved_search', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_saved_search_user_id'))
    op.drop_table('saved_search')
