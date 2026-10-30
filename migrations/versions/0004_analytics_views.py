"""Read-only analytics views and the role that text-to-SQL runs as.

The `analytics` schema holds the only objects generated SQL may read. The
`voc_analytics` role has USAGE on that schema and SELECT on its views, and
nothing else: no access to base tables. Views run with their owner's
privileges, so they can read the base tables the role cannot.

The application switches to the role per query with `SET LOCAL ROLE`, so the
migration grants it to the current user. Roles are cluster-wide, so creating
and dropping it is guarded.

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ROLE = "voc_analytics"

VIEWS = {
    "conversations": """
        SELECT
            c.external_id AS conversation_id,
            c.customer_id,
            c.industry,
            c.channel,
            c.started_at,
            i.contact_reason,
            i.sentiment,
            i.resolved,
            i.churn_risk,
            i.competitors,
            i.summary
        FROM public.conversations c
        LEFT JOIN public.insights i ON i.conversation_id = c.id
    """,
    "customers": """
        SELECT
            id AS customer_id,
            industry,
            plan_code,
            signup_at,
            churned_at,
            churn_reason,
            churned_at IS NOT NULL AS churned
        FROM public.customers
    """,
    "competitor_mentions": """
        SELECT
            c.external_id AS conversation_id,
            c.customer_id,
            c.industry,
            c.started_at,
            m.competitor
        FROM public.conversations c
        JOIN public.insights i ON i.conversation_id = c.id
        CROSS JOIN LATERAL unnest(i.competitors) AS m(competitor)
    """,
}


def upgrade() -> None:
    op.execute("CREATE SCHEMA analytics")
    for name, body in VIEWS.items():
        op.execute(f"CREATE VIEW analytics.{name} AS {body}")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{ROLE}') THEN
                CREATE ROLE {ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT USAGE ON SCHEMA analytics TO {ROLE}")
    op.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA analytics TO {ROLE}")
    op.execute(f"GRANT {ROLE} TO CURRENT_USER")


def downgrade() -> None:
    op.execute("DROP SCHEMA analytics CASCADE")
    op.execute(f"REVOKE {ROLE} FROM CURRENT_USER")
    op.execute(
        f"""
        DO $$
        BEGIN
            DROP ROLE IF EXISTS {ROLE};
        EXCEPTION WHEN dependent_objects_still_exist THEN
            NULL;  -- still granted in another database of this cluster
        END $$
        """
    )
