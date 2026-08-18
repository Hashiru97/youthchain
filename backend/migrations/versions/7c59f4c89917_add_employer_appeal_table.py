"""add employer appeal table

Revision ID: 7c59f4c89917
Revises: 831a9bb41403
Create Date: 2026-08-07 12:33:19.604368

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. employer_appeal
is a brand-new table (no server_default needed on its NOT NULL `status`
column — there are no pre-existing rows to backfill for a CREATE TABLE).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '7c59f4c89917'
down_revision = '831a9bb41403'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('employer_appeal',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('employer_id', sa.Integer(), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['employer_id'], ['employer.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by_admin_id'], ['admin.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('employer_appeal', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_employer_appeal_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_employer_appeal_employer_id'), ['employer_id'], unique=False)


def downgrade():
    with op.batch_alter_table('employer_appeal', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_employer_appeal_employer_id'))
        batch_op.drop_index(batch_op.f('ix_employer_appeal_created_at'))

    op.drop_table('employer_appeal')
