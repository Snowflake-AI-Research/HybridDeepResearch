"""External-knowledge tools backed by ``<sqlite_root>/<db>/<db>_kb.jsonl``."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

_MAX_FULL_CATALOG_CHARS = 12_000


def _kb_path(sqlite_root: Path, db: str) -> Path:
    name = (db or "").strip().lower()
    if not name.replace("_", "").isalnum():
        raise ValueError(f"invalid database name: {db!r}")
    return sqlite_root.expanduser().resolve() / name / f"{name}_kb.jsonl"


@lru_cache(maxsize=64)
def _load_entries(sqlite_root_str: str, db: str) -> tuple[dict[str, Any], ...]:
    path = _kb_path(Path(sqlite_root_str), db)
    if not path.is_file():
        raise FileNotFoundError(f"Knowledge file not found: {path}")
    entries: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                entries.append(json.loads(line))
    return tuple(entries)


def _numeric_id(key: str, db: str) -> int | None:
    token = (key or "").strip()
    if token.isdigit():
        return int(token)
    prefix = f"{db.lower()}_"
    if token.lower().startswith(prefix):
        suffix = token[len(prefix) :]
        if suffix.isdigit():
            return int(suffix)
    return None


def _matches(entry: dict[str, Any], key: str, db: str) -> bool:
    token = (key or "").strip()
    if not token:
        return False
    eid = entry.get("id")
    if eid is not None and str(eid) == token:
        return True
    num = _numeric_id(token, db)
    if num is not None and eid == num:
        return True
    if eid is not None and token.lower() == f"{db.lower()}_{eid}".lower():
        return True
    title = str(entry.get("knowledge", "")).strip()
    return bool(title) and title.lower() == token.lower()


def make_knowledge_tools(
    sqlite_root: str | Path,
    db: str,
) -> dict[str, Callable[..., str]]:
    """Return the three knowledge-catalog callables for one database."""

    root = Path(sqlite_root).expanduser().resolve()
    entries = _load_entries(str(root), db)

    def get_all_external_knowledge_names() -> str:
        names = [
            str(entry.get("knowledge", "")).strip()
            for entry in entries
            if entry.get("knowledge")
        ]
        return json.dumps(names, ensure_ascii=False)

    def get_knowledge_definition(knowledge_name: str) -> str:
        key = (knowledge_name or "").strip()
        if not key:
            return "[ERROR]: knowledge_name is required."
        for entry in entries:
            if _matches(entry, key, db):
                return json.dumps(entry, ensure_ascii=False, indent=2)
        return f"Knowledge not found for {key!r} in database {db!r}."

    def get_all_knowledge_definitions() -> str:
        full = json.dumps(list(entries), ensure_ascii=False, indent=2)
        if len(full) <= _MAX_FULL_CATALOG_CHARS:
            return full
        names = [
            str(entry.get("knowledge", "")).strip()
            for entry in entries
            if entry.get("knowledge")
        ]
        return json.dumps(
            {
                "_notice": (
                    f"Full catalog is {len(full)} chars; returning titles only. "
                    "Call get_knowledge_definition for each metric you need."
                ),
                "database": db,
                "entry_count": len(entries),
                "knowledge_names": names,
            },
            ensure_ascii=False,
            indent=2,
        )

    return {
        "get_all_external_knowledge_names": get_all_external_knowledge_names,
        "get_knowledge_definition": get_knowledge_definition,
        "get_all_knowledge_definitions": get_all_knowledge_definitions,
    }
