"""Change files column to LargeBinary

Revision ID: 9e4b6e53ba6a
Revises: 6792e5070c22
Create Date: 2025-02-24 16:32:07.206570

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = '9e4b6e53ba6a'
down_revision = '6792e5070c22'
branch_labels = None
depends_on = None


def upgrade():
    # ----------------------------
    # User Level Progress Table
    # ----------------------------
    op.create_table('user_level_progress',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('level_id', sa.Integer(), nullable=False),
        sa.Column('area_id', sa.Integer(), nullable=False),
        sa.Column('passed', sa.Boolean(), nullable=True),
        sa.ForeignKeyConstraint(['area_id'], ['areas.id']),
        sa.ForeignKeyConstraint(['level_id'], ['levels.id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.id']),
        sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('user_level_progress', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_user_level_progress_area_id'), ['area_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_level_progress_level_id'), ['level_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_level_progress_user_id'), ['user_id'], unique=False)

    # ----------------------------
    # Levels Table
    # ----------------------------
    with op.batch_alter_table('levels', schema=None) as batch_op:
        batch_op.add_column(sa.Column('title', sa.String(length=255), nullable=False))

    # ----------------------------
    # Study Materials Table
    # ----------------------------
    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.add_column(sa.Column('level_id', sa.Integer(), nullable=True))
        
        # Drop the existing 'files' column
        batch_op.drop_column('files')

        # Re-add the column with the new data type
        batch_op.add_column(sa.Column('files', sa.LargeBinary(), nullable=True))
        
        batch_op.create_foreign_key(None, 'levels', ['level_id'], ['id'])

    # ----------------------------
    # User Progress Table
    # ----------------------------
    with op.batch_alter_table('user_progress', schema=None) as batch_op:
        batch_op.add_column(sa.Column('completed', sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column('level_id', sa.Integer(), nullable=True))
        batch_op.create_index(batch_op.f('ix_user_progress_level_id'), ['level_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_progress_study_material_id'), ['study_material_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_user_progress_user_id'), ['user_id'], unique=False)
        batch_op.create_foreign_key(None, 'levels', ['level_id'], ['id'])


def downgrade():
    # ----------------------------
    # User Progress Table
    # ----------------------------
    with op.batch_alter_table('user_progress', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_user_progress_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_progress_study_material_id'))
        batch_op.drop_index(batch_op.f('ix_user_progress_level_id'))
        batch_op.drop_column('level_id')
        batch_op.drop_column('completed')

    # ----------------------------
    # Study Materials Table
    # ----------------------------
    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.drop_constraint(None, type_='foreignkey')
        
        # Drop the LargeBinary column
        batch_op.drop_column('files')

        # Re-add the column as ARRAY(TEXT)
        batch_op.add_column(sa.Column('files', postgresql.ARRAY(sa.TEXT()), nullable=True))
        
        batch_op.drop_column('level_id')

    # ----------------------------
    # Levels Table
    # ----------------------------
    with op.batch_alter_table('levels', schema=None) as batch_op:
        batch_op.drop_column('title')

    # ----------------------------
    # User Level Progress Table
    # ----------------------------
    with op.batch_alter_table('user_level_progress', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_user_level_progress_user_id'))
        batch_op.drop_index(batch_op.f('ix_user_level_progress_level_id'))
        batch_op.drop_index(batch_op.f('ix_user_level_progress_area_id'))

    op.drop_table('user_level_progress')
