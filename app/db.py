"""SQLite connection + CSV ingestion for the support tickets dataset."""
import sqlite3
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "data" / "support_tickets.csv"
DB_PATH = BASE_DIR / "tickets.db"

TABLE_NAME = "tickets"


def load_csv_to_db(csv_path: Path = CSV_PATH, db_path: Path = DB_PATH) -> None:
    """Ingest the CSV into a SQLite table, replacing any existing table."""
    df = pd.read_csv(csv_path)
    df["created_at"] = pd.to_datetime(df["created_at"])
    conn = sqlite3.connect(db_path)
    try:
        df.to_sql(TABLE_NAME, conn, if_exists="replace", index=False)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_status ON {TABLE_NAME}(status)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_priority ON {TABLE_NAME}(priority)")
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_agent ON {TABLE_NAME}(agent_id)")
        conn.commit()
    finally:
        conn.close()


def get_connection(db_path: Path = DB_PATH) -> sqlite3.Connection:
    if not db_path.exists():
        load_csv_to_db(CSV_PATH, db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_latest_ticket_date(conn: sqlite3.Connection) -> str:
    """Latest created_at date in the data, used as the reference 'today' for
    resolving relative phrases like 'this month' / 'this week' in NL questions.

    Anchoring to the data's own timeline (rather than the real wall-clock date)
    keeps relative-date questions meaningful on a static/historical dataset,
    and is a no-op difference on live data where the latest row is close to now.
    """
    row = conn.execute(f"SELECT MAX(created_at) FROM {TABLE_NAME}").fetchone()
    return row[0].split(" ")[0] if row and row[0] else None


if __name__ == "__main__":
    load_csv_to_db()
    print(f"Loaded {CSV_PATH} into {DB_PATH}")
