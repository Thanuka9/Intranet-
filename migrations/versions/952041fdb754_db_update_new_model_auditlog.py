"""DB update new model auditlog

Revision ID: 952041fdb754
Revises: b6f3f2726c23
Create Date: 2025-06-07 20:10:43.782146

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '952041fdb754'
down_revision = 'b6f3f2726c23'
branch_labels = None
depends_on = None


def upgrade():
    # Create audit_log table
    op.create_table(
        'audit_log',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('event_type', sa.String(length=100), nullable=False),
        sa.Column('actor_user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('ip_address', sa.String(length=45), nullable=True),
        sa.Column('target_table', sa.String(length=100), nullable=True),
        sa.Column('target_id', sa.Integer(), nullable=True),
        sa.Column('description', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('created_at', postgresql.TIMESTAMP(timezone=True), server_default=sa.text('now()'), nullable=False),
    )

    # Indexes for performance
    op.create_index('ix_audit_log_created_at', 'audit_log', ['created_at'], unique=False)
    op.create_index('ix_audit_log_ip_address', 'audit_log', ['ip_address'], unique=False)
    op.create_index('ix_audit_log_actor_user_id', 'audit_log', ['actor_user_id'], unique=False)
    op.create_index('ix_audit_event_user', 'audit_log', ['event_type', 'actor_user_id'], unique=False)
    op.create_index('ix_audit_target', 'audit_log', ['target_table', 'target_id'], unique=False)


def downgrade():
    # Drop indexes and table
    op.drop_index('ix_audit_target', table_name='audit_log')
    op.drop_index('ix_audit_event_user', table_name='audit_log')
    op.drop_index('ix_audit_log_actor_user_id', table_name='audit_log')
    op.drop_index('ix_audit_log_ip_address', table_name='audit_log')
    op.drop_index('ix_audit_log_created_at', table_name='audit_log')
    op.drop_table('audit_log')
