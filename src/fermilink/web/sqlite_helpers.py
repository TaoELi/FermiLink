from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy.engine import make_url


def _sqlite_path_from_url(url: str) -> Path | None:
    """Extract sqlite database path from a SQLAlchemy connection URL."""

    try:
        parsed = make_url(url)
    except Exception:
        return None
    if not parsed.drivername.startswith("sqlite"):
        return None
    if not parsed.database:
        return None
    path = Path(parsed.database)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path


def _ensure_sqlite_schema(db_path: Path, schema_sql: str) -> None:
    """Ensure sqlite database schema exists by executing DDL script."""

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema_sql)


def _ensure_sqlite_columns(db_path: Path, table: str, columns: dict[str, str]) -> None:
    """Backfill missing sqlite columns for an existing table."""

    with sqlite3.connect(db_path) as conn:
        conn.execute("PRAGMA foreign_keys = ON;")
        existing = {
            row[1] for row in conn.execute(f"PRAGMA table_info({table});").fetchall()
        }
        for name, col_type in columns.items():
            if name in existing:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {col_type};")
        conn.commit()
