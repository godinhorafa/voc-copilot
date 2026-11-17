"""Tenants, per-tenant API keys and row-level security.

* `tenants` and `api_keys` (hashes only). A `default` tenant with a fixed id
  owns all existing data and the bootstrap API_KEY.
* Every table with customer data gets `tenant_id`, defaulting to the
  transaction's `app.tenant_id` (an error when it is not set).
* Row-level security, FORCEd so the table owner is subject to it too, limits
  each table to the rows of the tenant in `app.tenant_id`; with none set, a
  session sees nothing and can write nothing. Superusers bypass RLS, so the
  application switches to the `voc_app` role (no superuser, not the owner)
  for every tenant-scoped transaction.
* The analytics views filter on `app.tenant_id` themselves: views run with
  their owner's privileges, which in development is a superuser.

Downgrading keeps only the default tenant's data.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_TENANT = "00000000-0000-0000-0000-000000000001"
APP_ROLE = "voc_app"
TENANT_TABLES = ("customers", "conversations", "turns", "chunks", "insights", "jobs")
CURRENT_TENANT = "NULLIF(current_setting('app.tenant_id', true), '')::uuid"

ROLE_ENUM = postgresql.ENUM("admin", "analyst", "ingest", name="api_key_role", create_type=False)

# Same columns as 0004, plus the tenant filter.
VIEWS = {
    "conversations": f"""
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
        WHERE c.tenant_id = {CURRENT_TENANT}
    """,
    "customers": f"""
        SELECT
            id AS customer_id,
            industry,
            plan_code,
            signup_at,
            churned_at,
            churn_reason,
            churned_at IS NOT NULL AS churned
        FROM public.customers
        WHERE tenant_id = {CURRENT_TENANT}
    """,
    "competitor_mentions": f"""
        SELECT
            c.external_id AS conversation_id,
            c.customer_id,
            c.industry,
            c.started_at,
            m.competitor
        FROM public.conversations c
        JOIN public.insights i ON i.conversation_id = c.id
        CROSS JOIN LATERAL unnest(i.competitors) AS m(competitor)
        WHERE c.tenant_id = {CURRENT_TENANT}
    """,
}


def upgrade() -> None:
    op.create_table(
        "tenants",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
        sa.UniqueConstraint("slug", name=op.f("uq_tenants_slug")),
    )
    op.execute(
        f"INSERT INTO tenants (id, slug, name) VALUES ('{DEFAULT_TENANT}', 'default', 'Default')"
    )

    ROLE_ENUM.create(op.get_bind())
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("tenant_id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("role", ROLE_ENUM, nullable=False),
        sa.Column("prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name=op.f("fk_api_keys_tenant_id_tenants"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_index(op.f("ix_api_keys_tenant_id"), "api_keys", ["tenant_id"])

    for table in TENANT_TABLES:
        op.add_column(table, sa.Column("tenant_id", sa.UUID(), nullable=True))
        op.execute(f"UPDATE {table} SET tenant_id = '{DEFAULT_TENANT}'")
        op.alter_column(
            table,
            "tenant_id",
            nullable=False,
            server_default=sa.text("current_setting('app.tenant_id')::uuid"),
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_tenant_id_tenants"),
            table,
            "tenants",
            ["tenant_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index(op.f(f"ix_{table}_tenant_id"), table, ["tenant_id"])

    # Customer ids and external conversation ids are unique per tenant.
    op.drop_constraint("pk_customers", "customers", type_="primary")
    op.create_primary_key("pk_customers", "customers", ["tenant_id", "id"])
    op.drop_constraint("uq_conversations_external_id", "conversations", type_="unique")
    op.create_unique_constraint(
        "uq_conversations_tenant_external_id", "conversations", ["tenant_id", "external_id"]
    )

    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
                CREATE ROLE {APP_ROLE} NOLOGIN;
            END IF;
        END $$
        """
    )
    op.execute(f"GRANT {APP_ROLE} TO CURRENT_USER")
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    for table in TENANT_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {table} "
            f"USING (tenant_id = {CURRENT_TENANT}) WITH CHECK (tenant_id = {CURRENT_TENANT})"
        )

    for name, body in VIEWS.items():
        op.execute(f"CREATE OR REPLACE VIEW analytics.{name} AS {body}")


def downgrade() -> None:
    for name, body in VIEWS.items():
        unfiltered = body.rsplit("WHERE", 1)[0]
        op.execute(f"CREATE OR REPLACE VIEW analytics.{name} AS {unfiltered}")

    for table in TENANT_TABLES:
        op.execute(f"DROP POLICY tenant_isolation ON {table}")
        op.execute(f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
        op.execute(f"REVOKE ALL ON {table} FROM {APP_ROLE}")
    op.execute(f"REVOKE USAGE ON SCHEMA public FROM {APP_ROLE}")
    op.execute(f"REVOKE {APP_ROLE} FROM CURRENT_USER")
    op.execute(
        f"""
        DO $$
        BEGIN
            DROP ROLE IF EXISTS {APP_ROLE};
        EXCEPTION WHEN dependent_objects_still_exist THEN
            NULL;  -- still granted in another database of this cluster
        END $$
        """
    )

    # Without tenants, ids must be unique again: keep the default tenant's rows.
    op.execute(f"DELETE FROM tenants WHERE id <> '{DEFAULT_TENANT}'")  # cascades
    op.drop_constraint("uq_conversations_tenant_external_id", "conversations", type_="unique")
    op.create_unique_constraint("uq_conversations_external_id", "conversations", ["external_id"])
    op.drop_constraint("pk_customers", "customers", type_="primary")
    op.create_primary_key("pk_customers", "customers", ["id"])

    for table in TENANT_TABLES:
        op.drop_index(op.f(f"ix_{table}_tenant_id"), table_name=table)
        op.drop_constraint(op.f(f"fk_{table}_tenant_id_tenants"), table, type_="foreignkey")
        op.drop_column(table, "tenant_id")

    op.drop_index(op.f("ix_api_keys_tenant_id"), table_name="api_keys")
    op.drop_table("api_keys")
    ROLE_ENUM.drop(op.get_bind())
    op.drop_table("tenants")
