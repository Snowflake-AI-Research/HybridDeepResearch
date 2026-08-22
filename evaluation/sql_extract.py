"""Extract the canonical SQL the agent produced for a ``search_to_sql`` trial.

Tries, in order:

1. The last fenced ``` ```sql ... ``` `` block in ``final_text``.
2. The last fenced generic ``` ``` ... ``` `` block in ``final_text``.
3. The ``sql`` argument of the last *successful* ``run_sql`` tool call
   in the native trajectory (skips calls whose paired tool result is
   missing or marked as error).

Two common message formats are supported:

- ``"openai"``: assistant ``tool_calls[*].function.{name,arguments}``
  paired with ``role="tool"`` results keyed by ``tool_call_id``.
- ``"anthropic"``: assistant content blocks of ``type="tool_use"``
  paired with ``role="user"`` content blocks of ``type="tool_result"``
  keyed by ``tool_use_id`` (carrying ``is_error``).
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterable

_RUN_SQL_TOOL_NAME = "run_sql"

# Source label vocabulary returned alongside the extracted SQL.
SOURCE_FENCED_SQL = "final_text_fenced_sql"
SOURCE_FENCED_GENERIC = "final_text_fenced_generic"
SOURCE_LAST_TOOL_CALL = "last_tool_call"
SOURCE_NONE = "none"


def _last_fenced(text: str, label_pattern: str) -> str | None:
    pattern = re.compile(rf"```(?:{label_pattern})\s*\n(.*?)```", re.DOTALL)
    matches = list(pattern.finditer(text or ""))
    if not matches:
        return None
    return (matches[-1].group(1).strip() or None)


def _openai_tool_results_by_id(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Build ``{tool_call_id: tool_message}`` from OpenAI-shaped messages."""
    by_id: dict[str, dict[str, Any]] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "tool":
            continue
        cid = str(m.get("tool_call_id") or "")
        if cid:
            by_id[cid] = m
    return by_id


def _openai_tool_result_is_error(tool_msg: dict[str, Any] | None) -> bool:
    """OpenAI doesn't mark errors structurally — we treat ``Error:`` /
    ``Rejected:`` content prefixes as failures, matching how the agent
    loop encodes :class:`ToolError`."""
    if not isinstance(tool_msg, dict):
        return True  # missing pair → treat as failed
    content = tool_msg.get("content")
    if isinstance(content, list):
        # Defensive — OpenAI tool messages are usually plain strings, but
        # be robust if a future loop version emits structured content.
        text = "".join(
            str(c.get("text") or c.get("content") or "")
            for c in content
            if isinstance(c, dict)
        )
    else:
        text = str(content or "")
    head = text.lstrip()
    return head.startswith("Error:") or head.startswith("Rejected:")


def _last_run_sql_openai(messages: list[dict[str, Any]]) -> str | None:
    by_id = _openai_tool_results_by_id(messages)
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        calls = m.get("tool_calls")
        if not isinstance(calls, list):
            continue
        for tc in reversed(calls):
            if not isinstance(tc, dict):
                continue
            fn = tc.get("function") if isinstance(tc.get("function"), dict) else None
            name = (fn or {}).get("name") or tc.get("name") or ""
            if name != _RUN_SQL_TOOL_NAME:
                continue
            raw_args = (fn or {}).get("arguments")
            if isinstance(raw_args, str):
                try:
                    args = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(raw_args, dict):
                args = raw_args
            else:
                args = {}
            sql = str((args or {}).get("sql") or "").strip()
            if not sql:
                continue
            cid = str(tc.get("id") or "")
            if cid and _openai_tool_result_is_error(by_id.get(cid)):
                continue
            return sql
    return None


def _anthropic_tool_results_by_id(messages: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "user":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                tid = str(block.get("tool_use_id") or "")
                if tid:
                    by_id[tid] = block
    return by_id


def _last_run_sql_anthropic(messages: list[dict[str, Any]]) -> str | None:
    by_id = _anthropic_tool_results_by_id(messages)
    for m in reversed(messages):
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        content = m.get("content")
        if not isinstance(content, list):
            continue
        for block in reversed(content):
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            if (block.get("name") or "") != _RUN_SQL_TOOL_NAME:
                continue
            inp = block.get("input")
            if not isinstance(inp, dict):
                continue
            sql = str(inp.get("sql") or "").strip()
            if not sql:
                continue
            tid = str(block.get("id") or "")
            paired = by_id.get(tid) if tid else None
            if paired is not None and bool(paired.get("is_error")):
                continue
            return sql
    return None


def _walk_messages_for_run_sql(
    messages: Iterable[dict[str, Any]] | None,
    *,
    message_format: str,
) -> str | None:
    if not messages:
        return None
    msgs = list(messages)
    if message_format == "anthropic":
        return _last_run_sql_anthropic(msgs)
    return _last_run_sql_openai(msgs)


def extract_agent_sql(
    *,
    final_text: str,
    messages: list[dict[str, Any]] | None,
    message_format: str = "openai",
) -> tuple[str, str]:
    """Return ``(sql, source)`` where source explains where the SQL came from.

    ``source`` is one of ``final_text_fenced_sql`` /
    ``final_text_fenced_generic`` / ``last_tool_call`` / ``none``.
    Empty SQL means nothing was extractable; the caller should report
    ``agent_sql_extraction_failed``.
    """
    text = final_text or ""
    fenced_sql = _last_fenced(text, "sql|SQL")
    if fenced_sql:
        return fenced_sql, SOURCE_FENCED_SQL
    fenced_generic = _last_fenced(text, "")
    if fenced_generic:
        return fenced_generic, SOURCE_FENCED_GENERIC
    last = _walk_messages_for_run_sql(messages, message_format=message_format)
    if last:
        return last, SOURCE_LAST_TOOL_CALL
    return "", SOURCE_NONE
