"""control store: graph_registry budget_policies audit_log

Revision ID: 11eea4240e45
Revises: 
Create Date: 2026-08-03 16:52:09.932815

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '11eea4240e45'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    op.create_table(
        "graph_registry",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("graph_name", sa.Text(), nullable=False),
        sa.Column(
            "payload_capture_mode",
            sa.Text(),
            nullable=False,
            server_default="full",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "payload_capture_mode IN ('full', 'metadata_only')",
            name="ck_graph_registry_payload_capture_mode",
        ),
        sa.UniqueConstraint(
            "tenant_id", "graph_name", name="uq_graph_registry_tenant_graph"
        ),
    )

    op.create_table(
        "budget_policies",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("graph_name", sa.Text(), nullable=False),
        sa.Column("per_run_cap_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column(
            "active", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "per_run_cap_usd > 0", name="ck_budget_policies_cap_positive"
        ),
    )
    # Partial unique index: one active cap per tenant+graph. A deactivated
    # policy (active=false) can coexist as history without blocking a new
    # active one for the same tenant+graph.
    op.create_index(
        "uq_budget_policies_active_tenant_graph",
        "budget_policies",
        ["tenant_id", "graph_name"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "audit_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            primary_key=True,
        ),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        # = tokenlens_sdk's run_id, per Phase 2 §0 — same ID that correlates
        # telemetry spans and the LangGraph checkpoint being resumed.
        sa.Column("thread_id", sa.Text(), nullable=False),
        sa.Column("graph_name", sa.Text(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("spend_at_decision_usd", sa.Numeric(10, 4), nullable=False),
        sa.Column("new_cap_usd", sa.Numeric(10, 4), nullable=True),
        sa.Column("decided_by", sa.Text(), nullable=False),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'kill', 'approve_and_raise_cap')",
            name="ck_audit_log_decision",
        ),
    )
    op.create_index(
        "ix_audit_log_tenant_thread", "audit_log", ["tenant_id", "thread_id"]
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_log_tenant_thread", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index(
        "uq_budget_policies_active_tenant_graph", table_name="budget_policies"
    )
    op.drop_table("budget_policies")
    op.drop_table("graph_registry")
