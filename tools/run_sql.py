"""Read-only ``run_sql`` tool over a per-task SQLite file."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from evaluation.sqlite_executor import execute_sqlite_query


def make_run_sql(
    sqlite_db_path: str | Path | None,
    *,
    max_rows: int = 500,
    timeout_s: float | None = 120.0,
) -> Callable[..., str]:
    """Return a ``run_sql(sql, description="")`` callable."""

    db_path = (
        Path(sqlite_db_path).expanduser().resolve()
        if sqlite_db_path is not None
        else None
    )

    def run_sql(sql: str, description: str = "") -> str:
        sql_text = (sql or "").strip()
        if not sql_text:
            return "Error: empty sql query."
        if db_path is None:
            return "Error: run_sql is not configured (no sqlite database for this run)."
        text, meta = execute_sqlite_query(
            db_path, sql_text, max_rows=max_rows, timeout_s=timeout_s
        )
        if not meta.get("ok"):
            return text
        header = f"{(description or '').strip()}\n\n" if (description or "").strip() else ""
        return f"{header}{text}"

    return run_sql
