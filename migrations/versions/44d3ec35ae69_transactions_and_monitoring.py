"""transactions and monitoring

Revision ID: 44d3ec35ae69
Revises: 09baa7900a93
Create Date: 2026-07-24 09:40:01.189886

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '44d3ec35ae69'
down_revision = '09baa7900a93'
branch_labels = None
depends_on = None


def upgrade():
    # Only the new `transaction` table. Alembic also emitted a spurious
    # NOT NULL alter on user.mfa_backup_codes (a diff artefact from the
    # non-transactional MFA migration); it is intentionally dropped here so
    # this migration does exactly one thing.
    op.create_table(
        'transaction',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('organization_id', sa.Integer(), nullable=False),
        sa.Column('customer_id', sa.Integer(), nullable=False),
        sa.Column('external_id', sa.String(length=120), nullable=True),
        sa.Column('direction', sa.String(length=10), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('currency', sa.String(length=3), nullable=False),
        sa.Column('amount_base', sa.Float(), nullable=False),
        sa.Column('method', sa.String(length=20), nullable=True),
        sa.Column('counterparty_name', sa.String(length=200), nullable=True),
        sa.Column('counterparty_country', sa.String(length=80), nullable=True),
        sa.Column('reference', sa.String(length=255), nullable=True),
        sa.Column('booked_at', sa.DateTime(), nullable=False),
        sa.Column('flags', sa.JSON(), nullable=False),
        sa.Column('flagged', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['customer_id'], ['customer.id'],
                                name=op.f('fk_transaction_customer_id_customer')),
        sa.ForeignKeyConstraint(['organization_id'], ['organization.id'],
                                name=op.f('fk_transaction_organization_id_organization')),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_transaction')),
    )


def downgrade():
    op.drop_table('transaction')
