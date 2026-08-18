"""add job scanner tables and columns

Revision ID: a1f92c7de034
Revises: 8b93699c7f37
Create Date: 2026-08-17 09:20:00.000000

Adds the schema for the job scanner feature: three new tables
(job_source, scan_run, scraped_company) and seven new nullable/defaulted
columns on job (source, source_id, company_name, description, salary,
apply_url, external_id, scraped_at). server_default='employer' on the new
NOT NULL job.source column, since every row that exists before this
migration runs was necessarily created by post_job() (the only place a
Job was ever created before the scanner) — same backfill-safety pattern
this chain already uses for job.job_type (see 8f432adfcf7b).

Split from the pg_cron-enabling migration (a1f92c7de034 -> next) so the
scheduling change — which is Postgres-only and has real operational
prerequisites — can be reviewed/dropped independently of this one, which
is plain DDL identical on SQLite and Postgres. Same reasoning 8f432adfcf7b
itself gives for splitting job_type/category from unrelated changes.

FTS5 false-positive diff stripped per this chain's standing convention
(job_fts isn't ORM-tracked, so autogenerate always sees it as a phantom
removed table).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1f92c7de034'
down_revision = '8b93699c7f37'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'job_source',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('base_url', sa.String(length=500), nullable=False),
        sa.Column('active', sa.Boolean(), nullable=False),
        sa.Column('scan_frequency_minutes', sa.Integer(), nullable=False),
        sa.Column('last_scan_started_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_admin_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['created_by_admin_id'], ['admin.id']),
        sa.PrimaryKeyConstraint('id'),
    )

    op.create_table(
        'scan_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('source_id', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False),
        sa.Column('triggered_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('queued_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('jobs_found', sa.Integer(), nullable=True),
        sa.Column('jobs_created', sa.Integer(), nullable=True),
        sa.Column('jobs_updated', sa.Integer(), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('claimed_by', sa.String(length=100), nullable=True),
        sa.ForeignKeyConstraint(['source_id'], ['job_source.id']),
        sa.ForeignKeyConstraint(['triggered_by_admin_id'], ['admin.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scan_run', schema=None) as batch_op:
        # The claim query (scanner.pipeline) filters status='queued' and
        # orders by id under FOR UPDATE SKIP LOCKED — this index is what
        # keeps that claim cheap as scan_run grows.
        batch_op.create_index(batch_op.f('ix_scan_run_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_scan_run_source_id'), ['source_id'], unique=False)

    op.create_table(
        'scraped_company',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=200), nullable=False),
        sa.Column('domain', sa.String(length=200), nullable=True),
        sa.Column('verified', sa.Boolean(), nullable=False),
        sa.Column('verified_at', sa.DateTime(), nullable=True),
        sa.Column('verified_by_admin_id', sa.Integer(), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['verified_by_admin_id'], ['admin.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('scraped_company', schema=None) as batch_op:
        # Company matching is by case-insensitive exact name (see
        # ScrapedCompany's own docstring) — this index is what keeps that
        # lookup cheap on every scanned job.
        batch_op.create_index(batch_op.f('ix_scraped_company_name'), ['name'], unique=False)

    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.add_column(sa.Column('source', sa.String(length=20), nullable=False, server_default='employer'))
        batch_op.add_column(sa.Column('source_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('company_name', sa.String(length=200), nullable=True))
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))
        batch_op.add_column(sa.Column('salary', sa.String(length=120), nullable=True))
        batch_op.add_column(sa.Column('apply_url', sa.String(length=500), nullable=True))
        batch_op.add_column(sa.Column('external_id', sa.String(length=300), nullable=True))
        batch_op.add_column(sa.Column('scraped_at', sa.DateTime(), nullable=True))
        batch_op.create_foreign_key('fk_job_source_id', 'job_source', ['source_id'], ['id'])
        # Not unique — de-dup on re-scan is an app-level get-or-create
        # (source_id + external_id) in scanner.pipeline, not a DB
        # constraint (same convention as every other validated-in-Python
        # field on this model). Plain index for lookup speed only.
        batch_op.create_index('ix_job_source_external_id', ['source_id', 'external_id'], unique=False)
        # GET /api/discover_jobs and the Home-feed-leak guard both filter
        # on this column on every request — worth its own index rather
        # than relying on the composite one above.
        batch_op.create_index(batch_op.f('ix_job_source'), ['source'], unique=False)


def downgrade():
    with op.batch_alter_table('job', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_job_source'))
        batch_op.drop_index('ix_job_source_external_id')
        batch_op.drop_constraint('fk_job_source_id', type_='foreignkey')
        batch_op.drop_column('scraped_at')
        batch_op.drop_column('external_id')
        batch_op.drop_column('apply_url')
        batch_op.drop_column('salary')
        batch_op.drop_column('description')
        batch_op.drop_column('company_name')
        batch_op.drop_column('source_id')
        batch_op.drop_column('source')

    with op.batch_alter_table('scraped_company', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scraped_company_name'))
    op.drop_table('scraped_company')

    with op.batch_alter_table('scan_run', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_scan_run_source_id'))
        batch_op.drop_index(batch_op.f('ix_scan_run_status'))
    op.drop_table('scan_run')

    op.drop_table('job_source')
