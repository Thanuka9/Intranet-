"""Cascade Departments

Revision ID: 0570dfce92e0
Revises: 15e950798d5b
Create Date: 2025-06-02 12:44:26.395980

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '0570dfce92e0'
down_revision = '15e950798d5b'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.create_table('user_departments',
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('department_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'department_id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint('users_department_id_fkey', type_='foreignkey')
        batch_op.drop_column('department_id')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('department_id', sa.INTEGER(), autoincrement=False, nullable=True))
        batch_op.create_foreign_key('users_department_id_fkey', 'departments', ['department_id'], ['id'])

    op.drop_table('user_departments')
    # ### end Alembic commands ###
