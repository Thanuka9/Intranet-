"""Add role and client_id columns to users table

Revision ID: 0ec1be2ecc6d
Revises: 17d116cf2e16
Create Date: 2025-04-01 09:52:26.011244
"""

from alembic import op
import sqlalchemy as sa


# Revision identifiers, used by Alembic.
revision = '0ec1be2ecc6d'
down_revision = '17d116cf2e16'
branch_labels = None
depends_on = None


def upgrade():
    # Add 'role' and 'client_id' columns to 'users' table
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('role', sa.String(length=50), nullable=True, server_default='member'))
        batch_op.add_column(sa.Column('client_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_users_client_id', 'clients', ['client_id'], ['id'])


def downgrade():
    # Drop 'role' and 'client_id' columns if rolled back
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('fk_users_client_id', type_='foreignkey')
        batch_op.drop_column('client_id')
        batch_op.drop_column('role')
