#!/usr/bin/env python3
"""Validate the shape and IDs of a HybridDeepResearch predictions file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def validate_answer(value: Any, *, location: str) -> None:
    if isinstance(value, str):
        if not value.strip():
            raise ValueError(f"{location}: final_answer must not be empty")
        return
    raise ValueError(f"{location}: final_answer must be a string")


def validate(path: Path) -> int:
    seen: set[str] = set()
    count = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            location = f"{path}:{line_number}"
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError(f"{location}: expected a JSON object")
            extra = set(record) - {"instance_id", "final_answer"}
            if extra:
                raise ValueError(
                    f"{location}: unexpected fields: {', '.join(sorted(extra))}"
                )
            instance_id = record.get("instance_id")
            if not isinstance(instance_id, str) or not instance_id.strip():
                raise ValueError(f"{location}: instance_id must be a non-empty string")
            if instance_id in seen:
                raise ValueError(f"{location}: duplicate instance_id {instance_id!r}")
            if "final_answer" not in record:
                raise ValueError(f"{location}: missing final_answer")
            validate_answer(record["final_answer"], location=location)
            seen.add(instance_id)
            count += 1
    if not count:
        raise ValueError(f"{path}: no prediction records found")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("predictions", type=Path)
    args = parser.parse_args()
    path = args.predictions.expanduser().resolve()
    count = validate(path)
    print(f"OK: {count} unique prediction records in {path}")


if __name__ == "__main__":
    main()
