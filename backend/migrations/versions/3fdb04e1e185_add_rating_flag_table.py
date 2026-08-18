"""add rating flag table

Revision ID: 3fdb04e1e185
Revises: 110621e2ee84
Create Date: 2026-08-11 15:27:00.000000

Brand-new table, same reasoning as 7c59f4c89917's employer_appeal
migration. RatingFlag is the dispute path for Rating — exact shape of the
EmployerReport/EmployerAppeal "someone flags a record, an admin reviews
it" pattern, reused rather than reinvented; see RatingFlag's own
docstring in app.py.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '3fdb04e1e185'
down_revision = '110621e2ee84'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('rating_flag',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('rating_id', sa.Integer(), nullable=False),
    sa.Column('flagged_by_role', sa.String(length=10), nullable=False),
    sa.Column('flagged_by_user_id', sa.Integer(), nullable=True),
    sa.Column('flagged_by_employer_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('reviewed_at', sa.DateTime(), nullable=True),
    sa.Column('reviewed_by_admin_id', sa.Integer(), nullable=True),
    sa.ForeignKeyConstraint(['flagged_by_employer_id'], ['employer.id'], ),
    sa.ForeignKeyConstraint(['flagged_by_user_id'], ['user.id'], ),
    sa.ForeignKeyConstraint(['rating_id'], ['rating.id'], ),
    sa.ForeignKeyConstraint(['reviewed_by_admin_id'], ['admin.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('rating_flag', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_rating_flag_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_rating_flag_rating_id'), ['rating_id'], unique=False)


def downgrade():
    with op.batch_alter_table('rating_flag', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_rating_flag_rating_id'))
        batch_op.drop_index(batch_op.f('ix_rating_flag_created_at'))

    op.drop_table('rating_flag')
