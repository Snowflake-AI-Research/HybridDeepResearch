"""Read-only SQLite query execution with a write-statement guard.

Two layers:

- :func:`execute_readonly_rows` returns ``(rows, columns, meta)`` —
  used by judges that need the table values directly.
- :func:`execute_sqlite_query` wraps that in a pretty-printed text table
  for the agent's tool observation.

Connections are opened in URI ``mode=ro`` so even if the write-token
guard misses an exotic write path, SQLite refuses to mutate the file.
"""

from __future__ import annotations

import re
import sqlite3
import time
from pathlib import Path
from typing import Any

_READ_ONLY_SQL = re.compile(
    r"^\s*(WITH|SELECT|PRAGMA|EXPLAIN)\b",
    re.IGNORECASE | re.DOTALL,
)

_LEADING_LINE_COMMENT = re.compile(r"^\s*--[^\n]*\n?")
_LEADING_BLOCK_COMMENT = re.compile(r"^\s*/\*.*?\*/", re.DOTALL)


def _strip_leading_comments(sql: str) -> str:
    """Iteratively strip whitespace + leading SQL comments (``--`` / ``/* */``)."""
    s = sql or ""
    while True:
        s2 = s.lstrip()
        m = _LEADING_LINE_COMMENT.match(s2)
        if m:
            s = s2[m.end():]
            continue
        m = _LEADING_BLOCK_COMMENT.match(s2)
        if m:
            s = s2[m.end():]
            continue
        return s2


def _reject_write(sql: str) -> str | None:
    """Two-pass guard: token blacklist plus opener-keyword whitelist.

    Returns an error message when the SQL should be rejected, or ``None``
    when it's safe to execute. The token list catches the common write
    paths (``INSERT`` etc.) and the regex enforces the opening keyword
    after stripping any leading comments. ``mode=ro`` on the connection
    is the final guarantee.
    """
    s = sql.strip().upper()
    banned = (
        "INSERT ",
        "UPDATE ",
        "DELETE ",
        "DROP ",
        "ALTER ",
        "CREATE ",
        "ATTACH ",
        "DETACH ",
        "VACUUM",
        "REPLACE ",
    )
    for b in banned:
        if b in s:
            return f"Rejected: disallowed token in query ({b.strip()})."
    head = _strip_leading_comments(sql or "")
    if not _READ_ONLY_SQL.match(head):
        return "Rejected: only SELECT / WITH / PRAGMA / EXPLAIN queries are allowed."
    return None


def _install_query_timeout(
    conn: sqlite3.Connection,
    timeout_s: float | None,
) -> float | None:
    """Abort long-running queries via SQLite's progress handler."""
    if timeout_s is None or timeout_s <= 0:
        conn.set_progress_handler(None, 0)
        return None

    deadline = time.monotonic() + timeout_s

    def _handler() -> int:
        return 1 if time.monotonic() >= deadline else 0

    conn.set_progress_handler(_handler, 10_000)
    return deadline


def _clear_query_timeout(conn: sqlite3.Connection) -> None:
    conn.set_progress_handler(None, 0)


def execute_readonly_rows(
    db_path: Path,
    sql: str,
    *,
    max_rows: int = 500,
    timeout_s: float | None = None,
) -> tuple[list[list[Any]], list[str], dict[str, Any]]:
    """Run a read-only query; return ``(rows-as-lists, column-names, meta)``.

    ``meta`` carries ``ok`` (bool), ``error`` (when not ok), and
    ``truncated`` (bool — set when the result set exceeded *max_rows*).
    """
    err = _reject_write(sql)
    if err:
        return [], [], {"ok": False, "error": err}

    db_path = db_path.expanduser().resolve()
    if not db_path.is_file():
        msg = f"Database file not found: {db_path}"
        return [], [], {"ok": False, "error": msg}

    try:
        uri_path = db_path.resolve().as_posix()
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        return [], [], {"ok": False, "error": f"SQLite connect failed: {exc}"}

    deadline = _install_query_timeout(conn, timeout_s)
    try:
        cur = conn.execute(sql)
        # Fetch one extra row to detect truncation cleanly.
        fetched = cur.fetchmany(max_rows + 1)
    except sqlite3.Error as exc:
        if deadline is not None and "interrupted" in str(exc).lower():
            limit = int(timeout_s) if timeout_s is not None else 0
            return [], [], {
                "ok": False,
                "error": f"Query timed out after {limit}s.",
            }
        return [], [], {"ok": False, "error": f"SQL error: {exc}"}
    finally:
        try:
            _clear_query_timeout(conn)
            conn.close()
        except sqlite3.Error:
            pass

    truncated = len(fetched) > max_rows
    fetched = fetched[:max_rows]

    if not fetched:
        return [], [], {"ok": True, "truncated": truncated}

    cols = list(fetched[0].keys())
    rows = [[r[c] for c in cols] for r in fetched]
    return rows, cols, {"ok": True, "truncated": truncated}


def execute_sqlite_query(
    db_path: Path,
    sql: str,
    *,
    max_rows: int = 500,
    max_cell_chars: int = 2000,
    timeout_s: float | None = None,
) -> tuple[str, dict[str, Any]]:
    """Run a read-only query; return ``(formatted_text, metadata)``.

    Output is a fixed-width text table with a ``Rows: N`` header so the
    agent can quickly see how big the result set is. Cells over
    *max_cell_chars* are clipped with ``...``.
    """
    rows, cols, base_meta = execute_readonly_rows(
        db_path, sql, max_rows=max_rows, timeout_s=timeout_s
    )
    if not base_meta.get("ok"):
        return base_meta["error"], {"ok": False, "error": base_meta["error"]}

    truncated = bool(base_meta.get("truncated"))
    meta: dict[str, Any] = {
        "ok": True,
        "sql": sql,
        "db_path": str(db_path.expanduser().resolve()),
        "truncated": truncated,
    }

    if not rows:
        return "(empty result set)", {**meta, "row_count": 0}

    table_rows: list[list[str]] = []
    for r in rows:
        cells = []
        for v in r:
            s = "" if v is None else str(v)
            if len(s) > max_cell_chars:
                s = s[: max_cell_chars - 3] + "..."
            cells.append(s)
        table_rows.append(cells)
    colnames = cols

    widths = [len(c) for c in colnames]
    for tr in table_rows:
        for i, cell in enumerate(tr):
            widths[i] = min(60, max(widths[i], len(cell)))

    def fmt_row(vals: list[str]) -> str:
        return " | ".join(v.ljust(widths[i]) for i, v in enumerate(vals))

    header = fmt_row(colnames)
    sep = "-+-".join("-" * w for w in widths)
    body = "\n".join(fmt_row(tr) for tr in table_rows)
    summary = f"Rows: {len(table_rows)}"
    if truncated:
        summary += f" (truncated to first {max_rows}; more rows existed)"
    text = f"{summary}\n\n{header}\n{sep}\n{body}"
    return text, {**meta, "row_count": len(table_rows), "truncated": truncated}
