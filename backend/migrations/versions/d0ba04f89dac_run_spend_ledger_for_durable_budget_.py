"""run_spend ledger for durable budget gating across resume

The in-process gating counter (TokenLensCallbackHandler.cumulative_cost_usd)
resets to zero on every .invoke()/.stream() call — including every resume
after an interrupt, since Command(resume=...) is itself a fresh invocation.
Verified by direct testing (Phase 2 §2) that this let a resumed run bypass
budget enforcement regardless of the actual approval decision. This table
is the fix: one row per thread_id (= run_id), incremented synchronously on
every real LLM call, read by the node wrapper's pre-check instead of the
in-process counter — durable across any number of interrupts/resumes and
process restarts.

Revision ID: d0ba04f89dac
Revises: 11eea4240e45
Create Date: 2026-08-03 18:23:04.251466

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0ba04f89dac'
down_revision: Union[str, Sequence[str], None] = '11eea4240e45'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "run_spend",
        sa.Column("thread_id", sa.Text(), primary_key=True),
        sa.Column(
            "cost_usd",
            sa.Numeric(12, 6),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "cost_usd >= 0", name="ck_run_spend_cost_non_negative"
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("run_spend")
