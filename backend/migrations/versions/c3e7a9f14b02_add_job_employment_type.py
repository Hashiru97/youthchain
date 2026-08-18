"""add job employment_type

Revision ID: c3e7a9f14b02
Revises: b6d3e81f5a97
Create Date: 2026-08-17 12:30:00.000000

Adds one new nullable column, job.employment_type — a free-text work
arrangement ("Full-time", "Part-time", "Contract", "Internship",
"Temporary", or similar) as stated on a scraped listing's source page,
or null when the source doesn't state one. Purely informational and
scraped-job-specific, same as company_name/salary/description/apply_url
added by a1f92c7de034 — deliberately NOT a rename or repurposing of the
existing job.job_type column, which is an unrelated YouthChain-specific
"formal"/"gig" concept the mobile apply flow depends on.

No backfill needed: every pre-existing row (employer-posted or already
scraped) simply has no employment_type until the scanner extracts one on
its next scan, same as the other nullable scraped-only columns.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e7a9f14b02'
down_revision = 'b6d3e81f5a97'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('employment_type', sa.String(length=60), nullable=True))


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_column('employment_type')
