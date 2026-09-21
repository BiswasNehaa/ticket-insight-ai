"""Turns a structured filter spec (produced by the LLM) into safe, parameterized SQL.

The LLM never writes SQL directly. It only ever emits values for a fixed set of
whitelisted columns/operators defined here, so nothing it outputs is executed as code.
"""
from dataclasses import dataclass, field
from typing import Any, Optional

TABLE = "tickets"

# Whitelisted columns the LLM is allowed to reference.
NUMERIC_COLUMNS = {"response_time_hrs", "resolution_time_hrs", "customer_rating"}
CATEGORICAL_COLUMNS = {"status", "priority", "category", "agent_id"}
ALLOWED_METRICS = {"count", "avg", "min", "max", "sum", "list"}
ALLOWED_GROUP_BY = CATEGORICAL_COLUMNS
ALLOWED_ORDER = {"asc", "desc"}


@dataclass
class QuerySpec:
    metric: str = "count"
    metric_column: Optional[str] = None
    group_by: Optional[str] = None
    order: Optional[str] = None
    limit: Optional[int] = None
    filters: dict[str, Any] = field(default_factory=dict)


class InvalidQuerySpec(ValueError):
    pass


def validate_spec(spec: QuerySpec) -> None:
    if spec.metric not in ALLOWED_METRICS:
        raise InvalidQuerySpec(f"Unsupported metric: {spec.metric}")
    if spec.metric in {"avg", "min", "max", "sum"} and spec.metric_column not in NUMERIC_COLUMNS:
        raise InvalidQuerySpec(f"metric '{spec.metric}' requires a numeric metric_column")
    if spec.group_by is not None and spec.group_by not in ALLOWED_GROUP_BY:
        raise InvalidQuerySpec(f"Unsupported group_by: {spec.group_by}")
    if spec.order is not None and spec.order not in ALLOWED_ORDER:
        raise InvalidQuerySpec(f"Unsupported order: {spec.order}")


def build_sql(spec: QuerySpec) -> tuple[str, list]:
    """Returns (sql, params) — always parameterized, never string-interpolated values."""
    validate_spec(spec)

    where_clauses: list[str] = []
    params: list = []
    f = spec.filters or {}

    for col in CATEGORICAL_COLUMNS:
        if f.get(col):
            where_clauses.append(f"{col} = ?")
            params.append(f[col])

    for col in NUMERIC_COLUMNS:
        gt_key, lt_key = f"{col}_gt", f"{col}_lt"
        if f.get(gt_key) is not None:
            where_clauses.append(f"{col} > ?")
            params.append(f[gt_key])
        if f.get(lt_key) is not None:
            where_clauses.append(f"{col} < ?")
            params.append(f[lt_key])

    if f.get("created_after"):
        where_clauses.append("created_at >= ?")
        params.append(f["created_after"])
    if f.get("created_before"):
        where_clauses.append("created_at <= ?")
        params.append(f["created_before"])

    if f.get("unresolved_older_than_hrs") is not None:
        where_clauses.append("status != 'Resolved'")
        where_clauses.append("(julianday('now') - julianday(created_at)) * 24 > ?")
        params.append(f["unresolved_older_than_hrs"])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    if spec.metric == "list":
        sql = f"SELECT * FROM {TABLE} {where_sql} ORDER BY created_at DESC"
        if spec.limit:
            sql += " LIMIT ?"
            params.append(int(spec.limit))
        return sql, params

    agg_expr = {
        "count": "COUNT(*)",
        "avg": f"AVG({spec.metric_column})",
        "min": f"MIN({spec.metric_column})",
        "max": f"MAX({spec.metric_column})",
        "sum": f"SUM({spec.metric_column})",
    }[spec.metric]

    if spec.group_by:
        sql = f"SELECT {spec.group_by}, {agg_expr} AS value FROM {TABLE} {where_sql} GROUP BY {spec.group_by}"
        if spec.order:
            sql += f" ORDER BY value {'ASC' if spec.order == 'asc' else 'DESC'}"
        if spec.limit:
            sql += " LIMIT ?"
            params.append(int(spec.limit))
    else:
        sql = f"SELECT {agg_expr} AS value FROM {TABLE} {where_sql}"

    return sql, params
