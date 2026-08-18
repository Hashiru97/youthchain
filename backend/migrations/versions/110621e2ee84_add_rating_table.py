"""add rating table

Revision ID: 110621e2ee84
Revises: 631f0683fc42
Create Date: 2026-08-11 15:25:00.000000

Brand-new table (no server_default needed on its NOT NULL columns — no
pre-existing rows to backfill for a CREATE TABLE), same reasoning as
7c59f4c89917's employer_appeal migration. Rating is the trust mechanism
for the gig/informal-work lifecycle — see Job.job_type and the Rating
model's own docstring in app.py.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '110621e2ee84'
down_revision = '631f0683fc42'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('rating',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('application_id', sa.Integer(), nullable=False),
    sa.Column('direction', sa.String(length=20), nullable=False),
    sa.Column('employer_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('hidden', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['application_id'], ['application.id'], ),
    sa.ForeignKeyConstraint(['employer_id'], ['employer.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('application_id', 'direction', name='uq_rating_application_direction')
    )
    with op.batch_alter_table('rating', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rating_application_id'), ['application_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_employer_id'), ['employer_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_user_id'), ['user_id'], unique=False)


def downgrade():
    with op.batch_alter_table('rating', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rating_user_id'))
        batch_op.drop_index(batch_op.f('ix_rating_employer_id'))
        batch_op.drop_index(batch_op.f('ix_rating_created_at'))
        batch_op.drop_index(batch_op.f('ix_rating_application_id'))

    op.drop_table('rating')
