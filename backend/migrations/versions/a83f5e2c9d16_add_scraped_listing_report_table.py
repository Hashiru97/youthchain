"""add scraped_listing_report table

Revision ID: a83f5e2c9d16
Revises: f291d6c84a37
Create Date: 2026-08-18 10:15:00.000000

Adds scraped_listing_report, the "report this listing" table for
Discover (scraped) jobs — the counterpart to the existing employer_report
table, but keyed to job_id only (no employer_id: a scraped listing has
no real Employer account to report against, see ScrapedListingReport's
own docstring in app.py for why this is a separate table rather than a
nullable employer_id on the existing one).

job_id is nullable, unlike saved_job.job_id — a report must be able to
outlive the Job it was filed against (scanner.reaper deletes scraped
Jobs on a grace-period timer, and an admin can delete one directly via
"remove listing"), and both of those null this column out first rather
than deleting the report, so the moderation record survives.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a83f5e2c9d16'
down_revision = 'f291d6c84a37'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'scraped_listing_report',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('reporter_user_id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.Integer(), nullable=True),
        sa.Column('category', sa.String(length=30), nullable=False),
        sa.Column('details', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['reporter_user_id'], ['user.id']),
        sa.ForeignKeyConstraint(['job_id'], ['job.id']),
        sa.ForeignKeyConstraint(['reviewed_by_admin_id'], ['admin.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scraped_listing_report', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_scraped_listing_report_reporter_user_id'), ['reporter_user_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_scraped_listing_report_job_id'), ['job_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_scraped_listing_report_created_at'), ['created_at'], unique=False)


def downgrade():
    with op.batch_alter_table('scraped_listing_report', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scraped_listing_report_created_at'))
        batch_op.drop_index(batch_op.f('ix_scraped_listing_report_job_id'))
        batch_op.drop_index(batch_op.f('ix_scraped_listing_report_reporter_user_id'))
    op.drop_table('scraped_listing_report')
