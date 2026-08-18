"""add user_appeal table

Revision ID: 7f0271479978
Revises: c1107a7da929
Create Date: 2026-08-12 10:14:50.875475

FTS5 false-positive diff stripped per this chain's standing convention
(job_fts isn't ORM-tracked, so autogenerate always sees it as a phantom
"removed table"). Autogenerate also didn't see user_appeal itself as a
real diff to add -- same root cause documented in 4862a1c653b3 and
c1107a7da929: the running dev server's own db.create_all() (SKIP_DB_CREATE_ALL
only guards the `flask db migrate` invocation, not the already-running app
process) had already silently created the table on file-reload before this
command ran. Hand-written to match UserAppeal in app.py exactly.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7f0271479978'
down_revision = 'c1107a7da929'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_appeal',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('message', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('reviewed_at', sa.DateTime(), nullable=True),
        sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['reviewed_by_admin_id'], ['admin.id']),
        sa.ForeignKeyConstraint(['user_id'], ['user.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('user_appeal', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_appeal_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_appeal_user_id'), ['user_id'], unique=False)


def downgrade():
    op.drop_table('user_appeal')
