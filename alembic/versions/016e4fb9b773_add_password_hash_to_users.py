"""add password_hash to users

Revision ID: 016e4fb9b773
Revises: aaf67e8df282
Create Date: 2026-04-12 11:17:47.137822

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '016e4fb9b773'
down_revision: Union[str, Sequence[str], None] = 'aaf67e8df282'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('users', sa.Column('password_hash', sa.String(length=255), nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    # checkpoint 테이블은 AIOMySQLSaver.setup()이 관리하므로 Alembic에서 다루지 않음
    op.drop_column('users', 'password_hash')
