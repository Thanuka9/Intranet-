"""update taks

Revision ID: 67138a9b25ff
Revises: 0ec1be2ecc6d
Create Date: 2025-04-01 10:40:19.740747

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '67138a9b25ff'
down_revision = '0ec1be2ecc6d'
branch_labels = None
depends_on = None


def upgrade():
    # Instead of dropping tables, comment out the commands to drop them.
    # op.drop_table('user_clients')
    # op.drop_table('roles')
    # op.drop_table('user_roles')
    
    # If you still wish to modify the tasks table, keep those alterations.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.alter_column('priority',
               existing_type=sa.VARCHAR(length=20),
               nullable=False)
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=50),
               nullable=False)
        batch_op.alter_column('progress',
               existing_type=sa.INTEGER(),
               nullable=False)


def downgrade():
    # Revert tasks table changes.
    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.alter_column('progress',
               existing_type=sa.INTEGER(),
               nullable=True)
        batch_op.alter_column('status',
               existing_type=sa.VARCHAR(length=50),
               nullable=True)
        batch_op.alter_column('priority',
               existing_type=sa.VARCHAR(length=20),
               nullable=True)
    
    # Optionally, if you need to recreate the dropped tables in a downgrade (if they were dropped before):
    op.create_table('user_roles',
        sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('role_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id'], name='user_roles_role_id_fkey'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='user_roles_user_id_fkey'),
        sa.PrimaryKeyConstraint('user_id', 'role_id', name='user_roles_pkey')
    )
    op.create_table('roles',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('name', sa.VARCHAR(length=50), autoincrement=False, nullable=False),
        sa.PrimaryKeyConstraint('id', name='roles_pkey'),
        sa.UniqueConstraint('name', name='roles_name_key')
    )
    op.create_table('user_clients',
        sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('client_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.ForeignKeyConstraint(['client_id'], ['clients.id'], name='user_clients_client_id_fkey'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='user_clients_user_id_fkey'),
        sa.PrimaryKeyConstraint('user_id', 'client_id', name='user_clients_pkey')
    )
