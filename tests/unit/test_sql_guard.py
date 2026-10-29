import pytest

from app.analytics.catalog import ALLOWED_TABLES, VIEWS, describe
from app.analytics.sql_guard import SQLGuardError, check

ALLOWED = [
    "SELECT count(*) FROM conversations",
    "SELECT date_trunc('month', started_at) AS m, count(*) FROM analytics.conversations "
    "WHERE contact_reason = 'cancellation' GROUP BY 1 ORDER BY 1",
    "WITH churned AS (SELECT customer_id FROM customers WHERE churned) "
    "SELECT count(*) FROM churned",
    "SELECT competitor, count(*) AS n FROM competitor_mentions m "
    "JOIN customers c ON c.customer_id = m.customer_id WHERE c.churned "
    "GROUP BY 1 ORDER BY n DESC LIMIT 5",
    "SELECT * FROM conversations WHERE 'NovaTel' = ANY(competitors)",
    "SELECT count(*) FILTER (WHERE sentiment = 'negative') FROM conversations",
    "SELECT round(avg((NOT resolved)::int), 3) FROM conversations",
    "SELECT conversation_id FROM conversations UNION SELECT conversation_id "
    "FROM competitor_mentions",
    "SELECT rank() OVER (ORDER BY count(*) DESC), contact_reason FROM conversations GROUP BY 2",
    'SELECT * FROM "analytics"."conversations";',
    "SELECT * FROM conversations WHERE summary LIKE '%a:b%'",
    "SELECT count(*) FROM conversations WHERE industry = 'telecom' AND (resolved OR "
    "sentiment <> 'negative') AND NOT churn_risk = 'high'",
]

DENIED = {
    "base table": ("SELECT * FROM public.conversations", "not allowed"),
    "unqualified base table": ("SELECT * FROM turns", "not allowed"),
    "base table in subquery": (
        "SELECT (SELECT count(*) FROM jobs) FROM conversations",
        "table jobs",
    ),
    "base table in lateral": (
        "SELECT * FROM conversations c, LATERAL (SELECT * FROM public.jobs) j",
        "public.jobs",
    ),
    "catalog": ("SELECT * FROM pg_catalog.pg_roles", "not allowed"),
    "information schema": ("SELECT * FROM information_schema.tables", "not allowed"),
    "similar view name": ("SELECT * FROM analytics.conversations_raw", "not allowed"),
    "cross database": ("SELECT * FROM other.analytics.conversations", "cross-database"),
    "no view": ("SELECT 1", "at least one analytics view"),
    "table function": ("SELECT * FROM generate_series(1, 10)", "at least one"),
    "two statements": ("SELECT 1; DROP TABLE conversations", "exactly one statement"),
    "delete": ("DELETE FROM conversations", "only SELECT"),
    "update": ("UPDATE conversations SET summary = ''", "only SELECT"),
    "dml in cte": (
        "WITH x AS (DELETE FROM conversations RETURNING *) SELECT * FROM x",
        "DELETE is not allowed",
    ),
    "insert in cte": (
        "WITH x AS (INSERT INTO customers (customer_id) VALUES ('x') RETURNING *) SELECT * FROM x",
        "INSERT is not allowed",
    ),
    "select into": ("SELECT * INTO stolen FROM conversations", "INTO is not allowed"),
    "row lock": ("SELECT * FROM conversations FOR UPDATE", "LOCK is not allowed"),
    "set role": ("SET ROLE postgres", "only SELECT"),
    "copy": ("COPY conversations TO '/tmp/x'", "only SELECT"),
    "set_config escape": (
        "SELECT set_config('role', 'postgres', true) FROM conversations",
        "set_config",
    ),
    "sleep": ("SELECT pg_sleep(10) FROM conversations", "pg_sleep"),
    "read file": ("SELECT pg_read_file('/etc/passwd') FROM conversations", "pg_read_file"),
    "settings": ("SELECT current_setting('role') FROM conversations", "current_setting"),
    "regex operator": ("SELECT * FROM conversations WHERE summary ~ 'x'", "not allowed"),
    "dblink": ("SELECT * FROM conversations, dblink('host=x', 'SELECT 1')", "dblink"),
    "unparseable": ("SELEC * FROM conversations", "could not parse"),
}


@pytest.mark.parametrize("sql", ALLOWED)
def test_allowed(sql: str) -> None:
    guarded = check(sql)
    assert guarded.tables <= ALLOWED_TABLES
    assert "analytics." in guarded.sql


@pytest.mark.parametrize(("sql", "message"), DENIED.values(), ids=DENIED.keys())
def test_denied(sql: str, message: str) -> None:
    with pytest.raises(SQLGuardError, match=message):
        check(sql)


def test_qualifies_view_names_and_reports_tables() -> None:
    guarded = check(
        "SELECT m.competitor FROM competitor_mentions m JOIN customers c USING (customer_id)"
    )
    assert "analytics.competitor_mentions" in guarded.sql
    assert guarded.tables == {"analytics.competitor_mentions", "analytics.customers"}


def test_cte_names_are_not_treated_as_tables() -> None:
    guarded = check("WITH conversations AS (SELECT * FROM customers) SELECT * FROM conversations")
    assert guarded.tables == {"analytics.customers"}


@pytest.mark.parametrize(
    ("sql", "expected_limit"),
    [
        ("SELECT * FROM conversations", "LIMIT 50"),
        ("SELECT * FROM conversations LIMIT 10", "LIMIT 10"),
        ("SELECT * FROM conversations LIMIT 100000", "LIMIT 50"),
        ("SELECT * FROM conversations LIMIT ALL", "LIMIT 50"),
        ("SELECT * FROM conversations LIMIT (SELECT 1)", "LIMIT 50"),
    ],
)
def test_limit_is_enforced(sql: str, expected_limit: str) -> None:
    assert check(sql, max_rows=50).sql.endswith(expected_limit)


def test_comments_are_dropped() -> None:
    assert "DROP" not in check("SELECT * FROM conversations -- ; DROP TABLE x").sql


def test_output_is_stable() -> None:
    once = check("select count(*) from conversations where channel='voice'").sql
    assert check(once).sql == once


def test_catalog_description_lists_every_column() -> None:
    text = describe()
    for view in VIEWS:
        assert view.qualified in text
        for column in view.columns:
            assert f"- {column.name} (" in text
