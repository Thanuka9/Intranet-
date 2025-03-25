"""Updated StudyMaterial model with default minimum_level, corrected

Revision ID: ecc55de5f036
Revises: bcbc49cf99bf
Create Date: 2025-03-24 01:01:48.035057

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'ecc55de5f036'
down_revision = 'bcbc49cf99bf'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.alter_column(
            'files',
            existing_type=sa.TEXT(),
            type_=postgresql.ARRAY(sa.String()),
            existing_nullable=True,
            postgresql_using="CASE WHEN files = '' THEN '{}' ELSE files::varchar[] END"
        )

    with op.batch_alter_table('subtopics', schema=None) as batch_op:
        batch_op.alter_column(
            'file_id',
            existing_type=sa.VARCHAR(length=255),
            nullable=True
        )


def downgrade():
    with op.batch_alter_table('subtopics', schema=None) as batch_op:
        batch_op.alter_column(
            'file_id',
            existing_type=sa.VARCHAR(length=255),
            nullable=False
        )

    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.alter_column(
            'files',
            existing_type=postgresql.ARRAY(sa.String()),
            type_=sa.TEXT(),
            existing_nullable=True,
            postgresql_using="array_to_string(files, ',')"
        )

    # ### end Alembic commands ###
