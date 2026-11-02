"""them cot facilities vao messages

Revision ID: 5d2a8c4e1b70
Revises: 3b7c1e9a2f40
Create Date: 2026-11-01 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '5d2a8c4e1b70'
down_revision: Union[str, Sequence[str], None] = '3b7c1e9a2f40'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('messages', sa.Column('facilities', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'facilities')
