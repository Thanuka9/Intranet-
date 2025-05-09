"""Faillogin and Lock usr

Revision ID: 988646e3948d
Revises: 348518716fd0
Create Date: 2025-05-07 11:50:17.984571
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '988646e3948d'
down_revision = '348518716fd0'
branch_labels = None
depends_on = None


def upgrade():
    # 1) Add the new audit columns to failed_logins (timestamp non-nullable via server_default)
    with op.batch_alter_table('failed_logins') as batch:
        batch.add_column(sa.Column(
            'ip_address',
            sa.String(length=45),
            nullable=True
        ))
        batch.add_column(sa.Column(
            'user_agent',
            sa.String(length=256),
            nullable=True
        ))
        batch.add_column(sa.Column(
            'timestamp',
            sa.DateTime(),
            nullable=False,
            server_default=sa.text('NOW()')
        ))

    # 2) Back-fill timestamp from the old attempt_time
    op.execute(
        "UPDATE failed_logins "
        "SET timestamp = attempt_time"
    )

    # 3) Remove the server_default so future inserts use the Python default
    with op.batch_alter_table('failed_logins') as batch:
        batch.alter_column(
            'timestamp',
            existing_type=sa.DateTime(),
            server_default=None
        )
        # 4) Drop the old column
        batch.drop_column('attempt_time')

    # 5) Add lockout fields to users with safe defaults
    with op.batch_alter_table('users') as batch:
        batch.add_column(sa.Column(
            'failed_login_count',
            sa.Integer(),
            nullable=False,
            server_default='0'
        ))
        batch.add_column(sa.Column(
            'is_locked',
            sa.Boolean(),
            nullable=False,
            server_default='false'
        ))
        batch.add_column(sa.Column(
            'locked_at',
            sa.DateTime(),
            nullable=True
        ))

    # 6) Remove the server_default settings so future inserts omit them
    with op.batch_alter_table('users') as batch:
        batch.alter_column('failed_login_count', server_default=None)
        batch.alter_column('is_locked', server_default=None)


def downgrade():
    # 1) Remove lockout fields from users
    with op.batch_alter_table('users') as batch:
        batch.drop_column('locked_at')
        batch.drop_column('is_locked')
        batch.drop_column('failed_login_count')

    # 2) Re-add attempt_time, then drop new audit columns
    with op.batch_alter_table('failed_logins') as batch:
        batch.add_column(sa.Column(
            'attempt_time',
            postgresql.TIMESTAMP(),
            nullable=True
        ))
        batch.drop_column('timestamp')
        batch.drop_column('user_agent')
        batch.drop_column('ip_address')
