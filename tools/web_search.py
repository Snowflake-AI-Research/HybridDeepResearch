"""``web_search`` tool. Attach your own search backend; names and arguments are fixed."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from typing import Any, Callable

DEFAULT_COUNT = 10
MAX_COUNT = 20


def format_hits(hits: list[dict[str, str]], source: str) -> str:
    if not hits:
        return f"source={source}\n\n(no results)"
    lines = [f"source={source}", ""]
    for index, hit in enumerate(hits, 1):
        lines.append(
            f"{index}. {hit.get('title', '')}\n"
            f"   {hit.get('url', '')}\n"
            f"   {hit.get('description', '')}\n"
        )
    return "\n".join(lines)


def tavily_search(query: str, count: int) -> tuple[list[dict[str, str]], str]:
    """Optional Tavily backend. Requires ``TAVILY_API_KEY``."""
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("TAVILY_API_KEY is not set.")
    payload = json.dumps(
        {
            "api_key": api_key,
            "query": query,
            "max_results": count,
            "include_answer": False,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.tavily.com/search",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.loads(response.read().decode("utf-8"))
    hits = [
        {
            "title": str(item.get("title") or ""),
            "url": str(item.get("url") or ""),
            "description": str(item.get("content") or item.get("snippet") or ""),
        }
        for item in body.get("results") or []
    ]
    return hits, "tavily"


def make_web_search(
    search_fn: Callable[[str, int], tuple[list[dict[str, str]], str]] | None = None,
) -> Callable[..., str]:
    """Return ``web_search(query, count=10)``.

    ``search_fn`` should return ``(hits, source_label)``. Each hit needs
    ``title``, ``url``, and ``description``. If omitted, Tavily is used when
    ``TAVILY_API_KEY`` is present.
    """

    backend = search_fn or tavily_search

    def web_search(query: str, count: int = DEFAULT_COUNT) -> str:
        q = (query or "").strip()
        if not q:
            return "Error: empty query."
        n = max(1, min(MAX_COUNT, int(count)))
        try:
            hits, source = backend(q, n)
        except Exception as exc:
            return f"Error: web search failed: {exc}"
        return format_hits(hits, source)

    return web_search
