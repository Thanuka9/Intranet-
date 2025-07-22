# migrations/versions/0516901f940c_add_privacy_agreed_columns_to_user.py

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import expression

# revision identifiers, used by Alembic.
revision = '0516901f940c'
down_revision = '952041fdb754'
branch_labels = None
depends_on = None

def upgrade():
    # 1) Add as nullable with a server default of FALSE
    op.add_column('users',
        sa.Column(
            'privacy_agreed',
            sa.Boolean(),
            nullable=True,
            server_default=expression.false()
        )
    )
    # 2) Add the timestamp column (nullable)
    op.add_column('users',
        sa.Column('privacy_agreed_at',
                  sa.DateTime(timezone=True),
                  nullable=True)
    )
    # 3) Backfill existing rows (should all get FALSE)
    op.execute("UPDATE users SET privacy_agreed = FALSE WHERE privacy_agreed IS NULL")
    # 4) Now make privacy_agreed truly NOT NULL and drop the default
    op.alter_column('users', 'privacy_agreed',
        nullable=False,
        server_default=None
    )

def downgrade():
    op.drop_column('users', 'privacy_agreed_at')
    op.drop_column('users', 'privacy_agreed')
