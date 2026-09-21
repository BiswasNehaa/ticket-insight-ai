"""Rule-based anomaly detection over the tickets table.

Two rule types, per the assessment brief:
1. Abnormally long resolution times: resolved tickets whose resolution_time_hrs
   is more than 2 standard deviations above the mean for resolved tickets.
2. Unresolved high-priority tickets older than 24 hours: status is Open/Escalated,
   priority is High/Critical, and more than 24h have passed since created_at.
"""
import sqlite3
from datetime import datetime, timezone

import pandas as pd

LONG_RESOLUTION_STD_THRESHOLD = 2.0
STALE_HOURS_THRESHOLD = 24


def _rows_to_dicts(df: pd.DataFrame) -> list[dict]:
    return df.where(pd.notnull(df), None).to_dict(orient="records")


def find_long_resolution_anomalies(conn: sqlite3.Connection) -> list[dict]:
    df = pd.read_sql_query(
        "SELECT * FROM tickets WHERE status = 'Resolved' AND resolution_time_hrs IS NOT NULL", conn
    )
    if df.empty:
        return []

    mean = df["resolution_time_hrs"].mean()
    std = df["resolution_time_hrs"].std()
    threshold = mean + LONG_RESOLUTION_STD_THRESHOLD * std

    anomalies = df[df["resolution_time_hrs"] > threshold].copy()
    anomalies["anomaly_type"] = "long_resolution_time"
    anomalies["anomaly_reason"] = (
        f"resolution_time_hrs > mean({mean:.1f}) + {LONG_RESOLUTION_STD_THRESHOLD}*std({std:.1f}) = {threshold:.1f}h"
    )
    return _rows_to_dicts(anomalies)


def find_stale_high_priority_anomalies(conn: sqlite3.Connection) -> list[dict]:
    df = pd.read_sql_query(
        "SELECT * FROM tickets WHERE status != 'Resolved' AND priority IN ('High', 'Critical')", conn
    )
    if df.empty:
        return []

    df["created_at"] = pd.to_datetime(df["created_at"])
    now = pd.Timestamp(datetime.now(timezone.utc)).tz_localize(None)
    df["hours_open"] = (now - df["created_at"]).dt.total_seconds() / 3600

    anomalies = df[df["hours_open"] > STALE_HOURS_THRESHOLD].copy()
    anomalies["anomaly_type"] = "stale_high_priority"
    anomalies["anomaly_reason"] = anomalies["hours_open"].apply(
        lambda h: f"unresolved for {h:.1f}h (> {STALE_HOURS_THRESHOLD}h threshold), high/critical priority"
    )
    return _rows_to_dicts(anomalies)


def detect_anomalies(conn: sqlite3.Connection) -> dict:
    long_resolution = find_long_resolution_anomalies(conn)
    stale_high_priority = find_stale_high_priority_anomalies(conn)
    return {
        "long_resolution_time": long_resolution,
        "stale_high_priority": stale_high_priority,
        "total_anomalies": len(long_resolution) + len(stale_high_priority),
    }
