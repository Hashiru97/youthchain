"""add scan_run.jobs_backfilled

Revision ID: d4f8b2e91c73
Revises: c3e7a9f14b02
Create Date: 2026-08-17 14:15:00.000000

Adds one new nullable column, scan_run.jobs_backfilled — how many jobs
got real description/salary/employment_type/location data from the
per-job detail-page backfill pass added to scanner/pipeline.py (see its
module docstring). Added because the admin scan-history panel
(admin_scanner.html) previously showed found/created/updated looking
perfectly healthy for a scan with no way to tell an admin that the jobs
underneath were still bodyless — this is the number that actually
answers "is the backfill pass working for this source".

No backfill of historical rows: every pre-existing scan_run predates the
backfill pass entirely and simply has no value here, not "backfilled 0"
(nullable, same convention as jobs_found/jobs_created/jobs_updated).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f8b2e91c73'
down_revision = 'c3e7a9f14b02'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('scan_run', schema=None) as batch_op:
        batch_op.add_column(sa.Column('jobs_backfilled', sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table('scan_run', schema=None) as batch_op:
        batch_op.drop_column('jobs_backfilled')
