"""``web_fetch`` tool. Reference fetch; swap in a renderer if you need one."""

from __future__ import annotations

import html
import re
import urllib.request
from typing import Callable

_TAG = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)
_WS = re.compile(r"\n{3,}")
MAX_CHARS = 20_000


def fetch_url(url: str) -> str:
    address = (url or "").strip()
    if not address:
        return "Error: empty url."
    request = urllib.request.Request(
        address,
        headers={"User-Agent": "HybridDeepResearch-public-fetch/1.0"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    text = raw.decode("utf-8", errors="replace")
    if "html" in content_type.lower() or "<html" in text[:400].lower():
        text = html.unescape(_TAG.sub(" ", text))
    text = _WS.sub("\n\n", text).strip()
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 3] + "..."
    return text or "(empty page)"


def make_web_fetch(
    fetch_fn: Callable[[str], str] | None = None,
) -> Callable[..., str]:
    backend = fetch_fn or fetch_url

    def web_fetch(url: str) -> str:
        try:
            return backend(url)
        except Exception as exc:
            return f"Error: web_fetch failed: {exc}"

    return web_fetch
