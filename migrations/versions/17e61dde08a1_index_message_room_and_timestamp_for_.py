"""index message room and timestamp for chat history

Revision ID: 17e61dde08a1
Revises: 116625023967
Create Date: 2026-08-10 18:03:39.910526

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = '17e61dde08a1'
down_revision = '116625023967'
branch_labels = None
depends_on = None


def upgrade():
    # T25. Every chat read filters by room and orders by time, so without this
    # a join is a full scan plus a sort over every room's history rather than
    # one room's. The column order matters: room first (equality) then
    # timestamp (range/sort) is what lets the index satisfy both halves.
    #
    # batch_alter_table is Alembic's SQLite compatibility path and is a plain
    # CREATE INDEX on Postgres. Verified against live Neon before shipping.
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.create_index('ix_message_room_timestamp', ['room', 'timestamp'], unique=False)


def downgrade():
    with op.batch_alter_table('message', schema=None) as batch_op:
        batch_op.drop_index('ix_message_room_timestamp')
