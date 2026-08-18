"""relax application cv_file nullable

Revision ID: 631f0683fc42
Revises: 8f432adfcf7b
Create Date: 2026-08-11 15:20:00.000000

Isolated in its own migration on purpose: unlike every other change in
this batch, downgrade() here is NOT unconditionally safe. Gig-job
applications (see Job.job_type) are allowed to have cv_file = NULL — if
any such row exists by the time this migration is rolled back, re-adding
NOT NULL will fail outright. Roll back only after confirming no
Application row has a null cv_file, or backfill/delete those rows first.
Route-level validation (apply()/portal_apply()) continues to require a CV
for every formal-job application regardless of this DB-level relaxation.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '631f0683fc42'
down_revision = '8f432adfcf7b'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('application', schema=None) as batch_op:
        batch_op.alter_column('cv_file', existing_type=sa.VARCHAR(length=300), nullable=True)


def downgrade():
    with op.batch_alter_table('application', schema=None) as batch_op:
        batch_op.alter_column('cv_file', existing_type=sa.VARCHAR(length=300), nullable=False)
