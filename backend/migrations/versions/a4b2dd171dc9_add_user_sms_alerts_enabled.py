"""add user sms_alerts_enabled

Revision ID: a4b2dd171dc9
Revises: 22c3831eed37
Create Date: 2026-08-18 22:22:54.107214

FTS5 false-positive diff and the unrelated ix_job_source/ix_saved_job_user_id/
ix_scan_run_*/ix_scraped_company_name "removed index" noise stripped per
this chain's standing convention (see c1107a7da929's own docstring) — none
of that reflects a real change made here.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4b2dd171dc9'
down_revision = '22c3831eed37'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('sms_alerts_enabled', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('sms_alerts_enabled')
