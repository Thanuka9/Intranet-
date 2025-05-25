"""Remove modules, update special_exam_records, add attempted_url, and create audit tables

Revision ID: a83338cb876f
Revises: e7fbf7fdce71
Create Date: 2025-05-09 13:36:07.717109

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a83338cb876f'
down_revision = 'e7fbf7fdce71'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Drop obsolete module tables
    op.drop_table('module_roles')
    op.drop_table('modules')

    # 2) Update special_exam_records
    with op.batch_alter_table('special_exam_records', schema=None) as batch_op:
        batch_op.drop_column('paper2_attempts')
        batch_op.drop_column('paper1_attempts')

    # 3) Add attempted_url to failed_logins
    op.add_column(
        'failed_logins',
        sa.Column('attempted_url', sa.String(length=255), nullable=True)
    )

    # 4) Create successful_logins table
    op.create_table(
        'successful_logins',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=False),
        sa.Column('user_agent', sa.String(length=256), nullable=True),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now(), nullable=False)
    )

    # 5) Create page_views table
    op.create_table(
        'page_views',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('ip_address', sa.String(length=45), nullable=False),
        sa.Column('user_agent', sa.String(length=256), nullable=True),
        sa.Column('url', sa.String(length=255), nullable=False),
        sa.Column('duration', sa.Integer(), server_default='0', nullable=False),
        sa.Column('timestamp', sa.DateTime(), server_default=sa.func.now(), nullable=False)
    )


def downgrade():
    # 1) Drop page_views and successful_logins tables
    op.drop_table('page_views')
    op.drop_table('successful_logins')

    # 2) Remove attempted_url
    op.drop_column('failed_logins', 'attempted_url')

    # 3) Restore special_exam_records
    with op.batch_alter_table('special_exam_records', schema=None) as batch_op:
        batch_op.add_column(sa.Column('paper1_attempts', sa.INTEGER(), nullable=True))
        batch_op.add_column(sa.Column('paper2_attempts', sa.INTEGER(), nullable=True))

    # 4) Recreate modules and module_roles tables
    op.create_table(
        'modules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['parent_id'], ['modules.id']),
        sa.UniqueConstraint('name')
    )
    op.create_table(
        'module_roles',
        sa.Column('module_id', sa.Integer(), nullable=False),
        sa.Column('role_id', sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(['module_id'], ['modules.id']),
        sa.ForeignKeyConstraint(['role_id'], ['roles.id']),
        sa.PrimaryKeyConstraint('module_id', 'role_id')
    )