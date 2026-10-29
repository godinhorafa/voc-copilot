"""Static validation of model-generated SQL before it reaches the database.

This is one layer of three (see ADR 0007): the query also runs as a role that
can only read the analytics views, inside a READ ONLY transaction with a
statement timeout. The guard's job is to reject anything that isn't a plain
read of the allowed views, give the model a precise error to fix, and return
a normalized query with a row limit.

Checks:
* exactly one statement, and it is a SELECT (CTEs and set operations allowed)
* every table is an allowed view (CTE names excepted); unqualified view names
  are qualified with the analytics schema
* every function is on an allowlist; this is what stops `set_config()`,
  `pg_sleep()`, `pg_read_file()`, `dblink()`... which a SELECT can still call
* no SELECT ... INTO, no row-locking clauses
* a LIMIT no higher than `max_rows`

The SQL that runs is the one sqlglot generates from the checked tree, never the
original string, so comments and anything the parser skipped are gone.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.analytics.catalog import ALLOWED_TABLES, SCHEMA

DIALECT = "postgres"

ALLOWED_FUNCTIONS = frozenset(
    {
        # aggregates
        "COUNT", "SUM", "AVG", "MIN", "MAX", "ARRAY_AGG", "STRING_AGG", "BOOL_AND",
        "BOOL_OR", "PERCENTILE_CONT", "PERCENTILE_DISC", "MODE", "STDDEV", "VARIANCE",
        # window functions
        "ROW_NUMBER", "RANK", "DENSE_RANK", "LAG", "LEAD", "FIRST_VALUE", "LAST_VALUE",
        "NTILE", "PERCENT_RANK", "CUME_DIST",
        # dates and times
        "DATE_TRUNC", "EXTRACT", "DATE_PART", "NOW", "CURRENT_DATE", "CURRENT_TIMESTAMP",
        "AGE", "MAKE_DATE", "MAKE_INTERVAL", "TO_CHAR", "TIMESTAMP_TRUNC", "TIME_TO_STR",
        "DATE_ADD", "DATE_SUB", "TS_OR_DS_ADD", "DATE_DIFF", "TIMESTAMP_DIFF",
        # conditionals and casting
        "COALESCE", "NULLIF", "GREATEST", "LEAST", "CAST", "TRY_CAST", "IF", "CASE",
        # numbers
        "ROUND", "FLOOR", "CEIL", "ABS", "DIV", "MOD", "POWER", "SQRT", "SAFE_DIVIDE",
        # strings
        "LOWER", "UPPER", "LENGTH", "TRIM", "SUBSTRING", "CONCAT", "REPLACE", "SPLIT_PART",
        "LEFT", "RIGHT", "INITCAP", "POSITION", "STRPOS", "STR_POSITION",
        # arrays
        "UNNEST", "EXPLODE", "ARRAY_LENGTH", "CARDINALITY", "ARRAY_SIZE",
        "ARRAY_CONTAINS", "ARRAY_TO_STRING", "ARRAY_JOIN", "ARRAY",
        "ARRAY_ANY", "ANY", "ALL",
    }
)  # fmt: skip

# Anywhere in the tree, not just at the root: `WITH x AS (DELETE ... RETURNING *)
# SELECT * FROM x` is a SELECT at the top.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Into,
    exp.Lock,
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.TruncateTable,
    exp.Command,
    exp.Set,
)


class SQLGuardError(ValueError):
    """The query is not allowed; the message is meant to be shown to the model."""


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    tables: frozenset[str]


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).upper()
    return node.sql_name().upper()


def check(sql: str, *, max_rows: int = 200) -> GuardedQuery:
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except ParseError as exc:
        raise SQLGuardError(f"could not parse SQL: {exc.errors[0]['description']}") from exc
    if len(statements) != 1:
        raise SQLGuardError(f"expected exactly one statement, got {len(statements)}")
    tree = statements[0]
    if not isinstance(tree, exp.Query):
        raise SQLGuardError(f"only SELECT queries are allowed, got {tree.key.upper()}")

    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise SQLGuardError(f"{node.key.upper()} is not allowed in a read-only query")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if not name:
            continue  # e.g. a function in FROM, checked with the functions below
        if not table.db and not table.catalog and name in cte_names:
            continue
        if table.catalog:
            raise SQLGuardError(f"cross-database reference {table.sql()} is not allowed")
        schema = (table.db or SCHEMA).lower()
        qualified = f"{schema}.{name}"
        if qualified not in ALLOWED_TABLES:
            allowed = ", ".join(sorted(ALLOWED_TABLES))
            raise SQLGuardError(f"table {table.sql()} is not allowed; use only: {allowed}")
        table.set("db", exp.to_identifier(SCHEMA))
        tables.add(qualified)
    if not tables:
        raise SQLGuardError("the query must read at least one analytics view")

    for func in tree.find_all(exp.Func):
        if isinstance(func, exp.Connector):
            continue  # AND / OR / XOR are modeled as functions but are plain operators
        name = _function_name(func)
        if name not in ALLOWED_FUNCTIONS:
            raise SQLGuardError(f"function {name.lower()}() is not allowed")

    limit = tree.args.get("limit")
    current = None
    if isinstance(limit, exp.Limit) and isinstance(limit.expression, exp.Literal):
        current = int(limit.expression.this) if limit.expression.is_int else None
    if current is None or current > max_rows:
        tree = tree.limit(max_rows)

    return GuardedQuery(sql=tree.sql(dialect=DIALECT, comments=False), tables=frozenset(tables))
