"""Public HybridDeepResearch tool contract.

These are the capability names and argument shapes an agent should expose.
They are not a production agent loop: wire them into your own framework and
search provider.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from .final_answer import FINAL_ANSWER_TOOL_NAME, make_final_answer
from .knowledge import make_knowledge_tools
from .run_sql import make_run_sql
from .web_fetch import make_web_fetch
from .web_search import make_web_search

SCHEMA_PATH = Path(__file__).with_name("schemas.json")


def load_schemas() -> list[dict[str, Any]]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def openai_tools() -> list[dict[str, Any]]:
    """Wrap :func:`load_schemas` as OpenAI Chat Completions ``tools``."""
    return [
        {
            "type": "function",
            "function": {
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"],
            },
        }
        for schema in load_schemas()
    ]


def build_toolset(
    *,
    sqlite_db_path: str | Path | None = None,
    sqlite_root: str | Path | None = None,
    db: str | None = None,
    search_fn=None,
    fetch_fn=None,
) -> dict[str, Callable[..., str]]:
    """Assemble the public tool callables for one task.

    Knowledge tools are included when both ``sqlite_root`` and ``db`` are set.
    """
    tools: dict[str, Callable[..., str]] = {
        "web_search": make_web_search(search_fn),
        "web_fetch": make_web_fetch(fetch_fn),
        "run_sql": make_run_sql(sqlite_db_path),
        FINAL_ANSWER_TOOL_NAME: make_final_answer(),
    }
    if sqlite_root is not None and db:
        tools.update(make_knowledge_tools(sqlite_root, db))
    return tools
