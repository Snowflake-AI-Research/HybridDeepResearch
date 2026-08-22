#!/usr/bin/env python3
"""Build complete preview and release JSONL files from v1 plus sparse v2 patches."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


TASK_FILES = {
    "search_to_sql": Path(
        "type_1_search_to_sql/search_to_sql_examples.jsonl"
    ),
    "sql_to_search": Path(
        "type_1_sql_to_search/sql_to_search_examples.jsonl"
    ),
    "parallel": Path(
        "type_2_parallel/search_sql_parallel_examples.jsonl"
    ),
}
EXPECTED_PREFIXES = {
    "search_to_sql": "s2sql_",
    "sql_to_search": "sql2s_",
    "parallel": "parallel_",
}
REQUIRED_FIELDS = ("id", "final_question", "hint", "db", "final_answer")


def load_jsonl(path: Path, *, task_type: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            record_id = str(row.get("id") or "").strip()
            if not record_id:
                raise ValueError(f"{path}:{line_number}: missing id")
            if record_id in seen:
                raise ValueError(f"{path}:{line_number}: duplicate id {record_id!r}")
            expected_prefix = EXPECTED_PREFIXES[task_type]
            if not record_id.startswith(expected_prefix):
                raise ValueError(
                    f"{path}:{line_number}: id {record_id!r} does not match "
                    f"{task_type!r}"
                )
            seen.add(record_id)
            rows.append(row)
    return rows


def with_task_type(
    row: dict[str, Any],
    *,
    task_type: str,
) -> dict[str, Any]:
    missing = [field for field in REQUIRED_FIELDS if field not in row]
    if missing:
        raise ValueError(
            f"{row.get('id', '<unknown>')}: missing required fields "
            f"{', '.join(missing)}"
        )
    result = {"id": row["id"], "hybrid_type": task_type}
    result.update(
        (key, value) for key, value in row.items() if key != "id"
    )
    return result


def overlay_rows(
    base_rows: list[dict[str, Any]],
    patch_rows: list[dict[str, Any]],
    *,
    task_type: str,
) -> tuple[list[dict[str, Any]], int]:
    base_ids = [str(row["id"]) for row in base_rows]
    base_set = set(base_ids)
    base_by_id = {str(row["id"]): row for row in base_rows}
    patch_by_id = {str(row["id"]): row for row in patch_rows}
    unknown = sorted(set(patch_by_id) - base_set)
    if unknown:
        raise ValueError(
            f"{task_type}: v2 contains IDs absent from v1: {', '.join(unknown)}"
        )

    identical = [
        record_id
        for record_id, patch in patch_by_id.items()
        if base_by_id[record_id] == patch
    ]
    if identical:
        raise ValueError(
            f"{task_type}: v2 patch contains unchanged records: "
            f"{', '.join(sorted(identical))}"
        )

    merged = [
        with_task_type(
            patch_by_id.get(str(base["id"]), base),
            task_type=task_type,
        )
        for base in base_rows
    ]
    return merged, len(patch_by_id)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def build_releases(
    *,
    v1_root: Path,
    v2_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    preview_rows: list[dict[str, Any]] = []
    release_rows: list[dict[str, Any]] = []
    counts: dict[str, dict[str, int]] = {}

    for task_type, relative_path in TASK_FILES.items():
        v1_rows = load_jsonl(v1_root / relative_path, task_type=task_type)
        v2_rows = load_jsonl(v2_root / relative_path, task_type=task_type)
        merged_rows, patched = overlay_rows(
            v1_rows,
            v2_rows,
            task_type=task_type,
        )
        preview_rows.extend(
            with_task_type(row, task_type=task_type) for row in v1_rows
        )
        release_rows.extend(merged_rows)
        counts[task_type] = {
            "preview_records": len(v1_rows),
            "release_records": len(merged_rows),
            "v2_overrides": patched,
        }

    preview_path = (
        output_root
        / "hybrid_ds_preview"
        / "hybrid_ds_preview.jsonl"
    )
    release_path = (
        output_root
        / "hybrid_ds_release"
        / "hybrid_ds_release.jsonl"
    )
    write_jsonl(preview_path, preview_rows)
    write_jsonl(release_path, release_rows)

    preview_ids = [str(row["id"]) for row in preview_rows]
    release_ids = [str(row["id"]) for row in release_rows]
    if len(preview_ids) != len(set(preview_ids)):
        raise ValueError("preview output contains duplicate IDs")
    if len(release_ids) != len(set(release_ids)):
        raise ValueError("release output contains duplicate IDs")
    if preview_ids != release_ids:
        raise ValueError("preview and release ID/order mismatch")

    manifest = {
        "preview": {
            "source": "hybrid_ds_v1_full",
            "records": len(preview_rows),
            "path": str(preview_path.relative_to(output_root)),
            "sha256": sha256(preview_path),
        },
        "release": {
            "source": "hybrid_ds_v1_full overlaid by hybrid_ds_v2_full",
            "records": len(release_rows),
            "overrides": sum(
                item["v2_overrides"] for item in counts.values()
            ),
            "path": str(release_path.relative_to(output_root)),
            "sha256": sha256(release_path),
        },
        "by_task_type": counts,
    }
    manifest_path = output_root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--v1-root", required=True, type=Path)
    parser.add_argument("--v2-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_releases(
        v1_root=args.v1_root.expanduser().resolve(),
        v2_root=args.v2_root.expanduser().resolve(),
        output_root=args.output_root.expanduser().resolve(),
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
