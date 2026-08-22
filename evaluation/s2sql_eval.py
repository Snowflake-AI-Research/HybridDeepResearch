#!/usr/bin/env python3
"""Evaluate S2SQL predictions by read-only SQL execution match."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

from execution_match import (
    S2SQL_NUMBER_OF_DIGITS_CONSIDERED,
    compare_execution_results,
    is_sql_ordered,
)
from sql_extract import SOURCE_NONE, extract_agent_sql
from sqlite_executor import execute_readonly_rows


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield value


def rows_to_columnar(
    rows: list[list[Any]],
    columns: list[str],
) -> dict[str, list[Any]]:
    result = {column: [] for column in columns}
    for row in rows:
        for index, column in enumerate(columns):
            result[column].append(row[index] if index < len(row) else None)
    return result


def resolve_database(
    record: dict[str, Any],
    *,
    db_root: Path | None,
    db_template: str,
) -> Path:
    explicit = str(record.get("db_path") or "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    db_name = str(record.get("db") or "").strip()
    if not db_name or db_root is None:
        raise ValueError("record needs db_path, or db together with --db-root")
    return db_root / db_template.format(db=db_name)


def evaluate_record(
    record: dict[str, Any],
    *,
    db_root: Path | None,
    db_template: str,
    max_rows: int,
    timeout: float | None,
    strict: bool,
) -> dict[str, Any]:
    final_text = str(
        record.get("response") or record.get("final_answer") or ""
    ).strip()
    gold_sql = str(record.get("gold_sql") or record.get("sql") or "").strip()
    messages = record.get("messages")
    if not isinstance(messages, list):
        messages = None
    message_format = str(record.get("message_format") or "openai")

    verdict: dict[str, Any] = {
        "instance_id": record.get("instance_id") or record.get("id"),
        "judge_kind": "s2sql_exec",
        "match": False,
        "score": 0.0,
        "reason": "",
        "agent_sql": "",
        "agent_sql_source": SOURCE_NONE,
        "column_precision": 0.0,
        "column_recall": 0.0,
        "order_sensitive": False,
        "agent_exec_error": None,
        "gold_exec_error": None,
        "agent_table": None,
        "gold_table": None,
    }
    if not gold_sql:
        verdict["reason"] = "missing_gold_sql"
        return verdict

    agent_sql, source = extract_agent_sql(
        final_text=final_text,
        messages=messages,
        message_format=message_format,
    )
    verdict["agent_sql"] = agent_sql
    verdict["agent_sql_source"] = source
    if not agent_sql:
        verdict["reason"] = "agent_sql_extraction_failed"
        return verdict

    try:
        db_path = resolve_database(
            record,
            db_root=db_root,
            db_template=db_template,
        )
    except ValueError as exc:
        verdict["reason"] = str(exc)
        return verdict
    if not db_path.is_file():
        verdict["reason"] = f"sqlite_db_not_found: {db_path}"
        return verdict

    gold_rows, gold_columns, gold_meta = execute_readonly_rows(
        db_path,
        gold_sql,
        max_rows=max_rows,
        timeout_s=timeout,
    )
    if not gold_meta.get("ok"):
        verdict["gold_exec_error"] = gold_meta.get("error")
        verdict["reason"] = f"gold_sql_exec_error: {gold_meta.get('error')}"
        return verdict

    agent_rows, agent_columns, agent_meta = execute_readonly_rows(
        db_path,
        agent_sql,
        max_rows=max_rows,
        timeout_s=timeout,
    )
    if not agent_meta.get("ok"):
        verdict["agent_exec_error"] = agent_meta.get("error")
        verdict["reason"] = f"agent_sql_exec_error: {agent_meta.get('error')}"
        return verdict

    gold_table = rows_to_columnar(gold_rows, gold_columns)
    agent_table = rows_to_columnar(agent_rows, agent_columns)
    order_sensitive = is_sql_ordered(gold_sql)
    score, precision, recall = compare_execution_results(
        agent_table,
        gold_table,
        order_matters=order_sensitive,
        strict=strict,
        ignore_extra_columns_for_recall=True,
        float_digits=S2SQL_NUMBER_OF_DIGITS_CONSIDERED,
    )
    matched = score >= 1.0
    verdict.update(
        {
            "match": matched,
            "score": 1.0 if matched else 0.0,
            "reason": (
                "exec_match"
                if matched
                else (
                    f"exec_mismatch: raw_score={score:.3f} "
                    f"col_precision={precision:.3f} col_recall={recall:.3f}"
                )
            ),
            "column_precision": float(precision),
            "column_recall": float(recall),
            "order_sensitive": order_sensitive,
            "agent_table": {"columns": agent_columns, "rows": agent_rows},
            "gold_table": {"columns": gold_columns, "rows": gold_rows},
        }
    )
    return verdict


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--db-root", type=Path)
    parser.add_argument(
        "--db-template",
        default="{db}/{db}_template.sqlite",
        help="Path below --db-root; {db} is replaced by the record's db field.",
    )
    parser.add_argument("--max-rows", type=int, default=10_000)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--strict", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    db_root = args.db_root.expanduser().resolve() if args.db_root else None
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    total = 0
    matched = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in iter_jsonl(args.input.expanduser().resolve()):
            verdict = evaluate_record(
                record,
                db_root=db_root,
                db_template=args.db_template,
                max_rows=max(1, args.max_rows),
                timeout=args.timeout,
                strict=args.strict,
            )
            total += 1
            matched += int(verdict["match"])
            handle.write(json.dumps(verdict, ensure_ascii=False) + "\n")

    accuracy = matched / total if total else 0.0
    print(f"Evaluated {total} S2SQL records | matched={matched} | accuracy={accuracy:.3f}")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
