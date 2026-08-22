"""``final_answer`` tool. The agent loop should treat a successful call as stop."""

from __future__ import annotations

from typing import Callable

FINAL_ANSWER_TOOL_NAME = "final_answer"


def make_final_answer() -> Callable[..., str]:
    def final_answer(answer: str) -> str:
        return answer or ""

    return final_answer
