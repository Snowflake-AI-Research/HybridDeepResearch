#!/usr/bin/env python3
"""Create public task records by removing construction and grading fields."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator


PUBLIC_FIELDS = ("id", "hybrid_type", "final_question", "hint", "db")
REQUIRED_FIELDS = ("id", "hybrid_type", "final_question", "db")
VALID_TYPES = {"sql_to_search", "search_to_sql", "parallel"}


def iter_jsonl(path: Path) -> Iterator[tuple[int, dict[str, Any]]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield line_number, value


def prepare_record(record: dict[str, Any], *, location: str) -> dict[str, Any]:
    missing = [key for key in REQUIRED_FIELDS if not record.get(key)]
    if missing:
        raise ValueError(f"{location}: missing {', '.join(missing)}")
    if record["hybrid_type"] not in VALID_TYPES:
        raise ValueError(
            f"{location}: invalid hybrid_type {record['hybrid_type']!r}"
        )
    return {key: record[key] for key in PUBLIC_FIELDS if key in record}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source = args.input.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for line_number, record in iter_jsonl(source):
            public = prepare_record(record, location=f"{source}:{line_number}")
            record_id = str(public["id"])
            if record_id in seen:
                raise ValueError(f"{source}:{line_number}: duplicate id {record_id!r}")
            seen.add(record_id)
            handle.write(json.dumps(public, ensure_ascii=False) + "\n")
            count += 1

    print(f"Wrote {count} public task records to {output}")


if __name__ == "__main__":
    main()
