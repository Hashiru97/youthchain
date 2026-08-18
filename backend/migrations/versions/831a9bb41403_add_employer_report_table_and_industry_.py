"""add employer report table and industry fields

Revision ID: 831a9bb41403
Revises: c6a9484f6134
Create Date: 2026-08-06 01:26:10.070250

Hand-edited after autogeneration: same recurring FTS5 false-positive as
every prior migration touching this database — stripped. employer_report
is a brand-new table (no server_default needed on its NOT NULL `status`
column — there are no pre-existing rows to backfill for a CREATE TABLE,
unlike an ALTER TABLE ADD COLUMN on an existing table). Both industry
columns are nullable, so no backfill concern there either.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '831a9bb41403'
down_revision = 'c6a9484f6134'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('employer_report',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reporter_user_id', sa.Integer(), nullable=False),
    sa.Column('employer_id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.Integer(), nullable=True),
    sa.Column('message_id', sa.Integer(), nullable=True),
    sa.Column('category', sa.String(length=30), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['employer_id'], ['employer.id'], ),
    sa.ForeignKeyConstraint(['job_id'], ['job.id'], ),
    sa.ForeignKeyConstraint(['message_id'], ['message.id'], ),
    sa.ForeignKeyConstraint(['reporter_user_id'], ['user.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by_admin_id'], ['admin.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('employer_report', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_employer_report_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_employer_report_employer_id'), ['employer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_employer_report_reporter_user_id'), ['reporter_user_id'], unique=False)

    with op.batch_alter_table('candidate', schema=None) as batch_op:
        batch_op.add_column(sa.Column('preferred_industries', sa.Text(), nullable=True))

    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.add_column(sa.Column('industry', sa.String(length=50), nullable=True))


def downgrade():
    with op.batch_alter_table('employer', schema=None) as batch_op:
        batch_op.drop_column('industry')

    with op.batch_alter_table('candidate', schema=None) as batch_op:
        batch_op.drop_column('preferred_industries')

    with op.batch_alter_table('employer_report', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_employer_report_reporter_user_id'))
        batch_op.drop_index(batch_op.f('ix_employer_report_employer_id'))
        batch_op.drop_index(batch_op.f('ix_employer_report_created_at'))

    op.drop_table('employer_report')
