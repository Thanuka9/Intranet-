"""Cascade Delete update

Revision ID: 15e950798d5b
Revises: 21efa8a72730
Create Date: 2025-06-01 19:24:22.873946

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '15e950798d5b'
down_revision = '21efa8a72730'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('exam_access_requests', schema=None) as batch_op:
        batch_op.drop_constraint('exam_access_requests_user_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'users', ['user_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_constraint('questions_category_id_fkey', type_='foreignkey')
        batch_op.drop_constraint('questions_exam_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'categories', ['category_id'], ['id'], ondelete='CASCADE')
        batch_op.create_foreign_key(None, 'exams', ['exam_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint('tasks_client_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'clients', ['client_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('user_clients', schema=None) as batch_op:
        batch_op.drop_constraint('user_clients_client_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'clients', ['client_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('user_roles', schema=None) as batch_op:
        batch_op.drop_constraint('user_roles_role_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'roles', ['role_id'], ['id'], ondelete='CASCADE')

    with op.batch_alter_table('user_task_association', schema=None) as batch_op:
        batch_op.drop_constraint('user_task_association_task_id_fkey', type_='foreignkey')
        batch_op.create_foreign_key(None, 'tasks', ['task_id'], ['id'], ondelete='CASCADE')

    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    with op.batch_alter_table('user_task_association', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('user_task_association_task_id_fkey', 'tasks', ['task_id'], ['id'])

    with op.batch_alter_table('user_roles', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('user_roles_role_id_fkey', 'roles', ['role_id'], ['id'])

    with op.batch_alter_table('user_clients', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('user_clients_client_id_fkey', 'clients', ['client_id'], ['id'])

    with op.batch_alter_table('tasks', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('tasks_client_id_fkey', 'clients', ['client_id'], ['id'])

    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('questions_exam_id_fkey', 'exams', ['exam_id'], ['id'])
        batch_op.create_foreign_key('questions_category_id_fkey', 'categories', ['category_id'], ['id'])

    with op.batch_alter_table('exam_access_requests', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.create_foreign_key('exam_access_requests_user_id_fkey', 'users', ['user_id'], ['id'])

    # ### end Alembic commands ###
