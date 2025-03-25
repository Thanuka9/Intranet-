"""Remove StudyMaterial and StudyMaterialProgress tables

Revision ID: d067097ef36e
Revises: 89f45cbc6ad1
Create Date: 2025-01-12 00:23:49.944054

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = 'd067097ef36e'
down_revision = '89f45cbc6ad1'
branch_labels = None
depends_on = None


def upgrade():
    # Drop the dependent table first
    op.drop_table('study_material_progress')  # Drop the dependent table
    op.drop_table('study_materials')  # Drop the parent table


def downgrade():
    # Recreate the tables in reverse order
    op.create_table('study_materials',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('title', sa.VARCHAR(length=100), autoincrement=False, nullable=False),
        sa.Column('description', sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column('category', sa.VARCHAR(length=50), autoincrement=False, nullable=True),
        sa.Column('standard_time', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('created_at', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.Column('max_time', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('video_chunk_ids', sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column('transcript_chunk_ids', sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column('video_id', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column('transcript_id', sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.PrimaryKeyConstraint('id', name='study_materials_pkey')
    )

    op.create_table('study_material_progress',
        sa.Column('id', sa.INTEGER(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('material_id', sa.INTEGER(), autoincrement=False, nullable=False),
        sa.Column('time_spent', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('completion_percentage', sa.INTEGER(), autoincrement=False, nullable=True),
        sa.Column('start_time', postgresql.TIMESTAMP(), autoincrement=False, nullable=True),
        sa.ForeignKeyConstraint(['material_id'], ['study_materials.id'], name='study_material_progress_material_id_fkey'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], name='study_material_progress_user_id_fkey'),
        sa.PrimaryKeyConstraint('id', name='study_material_progress_pkey')
    )
