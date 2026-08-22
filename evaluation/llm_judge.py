#!/usr/bin/env python3
"""Run the public text-answer judge over a JSONL file."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

from openai import OpenAI


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "answer_judge.txt"
PROMPT = PROMPT_PATH.read_text(encoding="utf-8")


def parse_verdict(raw: str) -> dict[str, Any]:
    """Parse the auditable fields emitted by the judge prompt."""
    text = raw.replace("**", "")
    correct_match = re.search(r"correct:\s*(yes|no)", text, re.IGNORECASE)
    reason_match = re.search(
        r"reasoning:\s*(.+?)(?=\ncorrect:|\nconfidence:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    answer_match = re.search(
        r"extracted_final_answer:\s*(.+?)(?=\n\[correct_answer\]|\nreasoning:|$)",
        text,
        re.DOTALL | re.IGNORECASE,
    )
    confidence_match = re.search(
        r"confidence:\s*(\d+(?:\.\d+)?)%?",
        text,
        re.IGNORECASE,
    )
    return {
        "correct": bool(
            correct_match and correct_match.group(1).lower() == "yes"
        ),
        "parse_ok": correct_match is not None,
        "extracted_final_answer": (
            answer_match.group(1).strip() if answer_match else None
        ),
        "reasoning": reason_match.group(1).strip() if reason_match else raw.strip(),
        "confidence": (
            float(confidence_match.group(1)) if confidence_match else None
        ),
        "raw_judge_output": raw,
    }


def iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            yield value


def judge_one(
    *,
    client: OpenAI,
    model: str,
    record: dict[str, Any],
) -> dict[str, Any]:
    question = str(record.get("question") or "").strip()
    answer = str(record.get("response") or "").strip()
    expected = str(record.get("correct_answer") or "").strip()
    if not question or not answer or not expected:
        raise ValueError("each record needs question, response, and correct_answer")

    prompt = PROMPT.format(
        question=question,
        answer=answer,
        expected_answer=expected,
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=2048,
    )
    raw = (response.choices[0].message.content or "").strip()
    return {**record, **parse_verdict(raw)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--model", default=os.environ.get("JUDGE_MODEL", ""))
    parser.add_argument(
        "--base-url",
        default=os.environ.get("JUDGE_BASE_URL", ""),
        help="Optional OpenAI-compatible API base URL.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    model = args.model.strip()
    if not model:
        raise SystemExit("Set JUDGE_MODEL or pass --model.")

    client_kwargs: dict[str, Any] = {
        "api_key": os.environ.get("JUDGE_API_KEY", "EMPTY"),
    }
    if args.base_url.strip():
        client_kwargs["base_url"] = args.base_url.strip()
    client = OpenAI(**client_kwargs)

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    correct = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in iter_jsonl(args.input.expanduser().resolve()):
            judged = judge_one(client=client, model=model, record=record)
            total += 1
            correct += int(judged["correct"])
            handle.write(json.dumps(judged, ensure_ascii=False) + "\n")

    accuracy = correct / total if total else 0.0
    print(f"Judged {total} records | correct={correct} | accuracy={accuracy:.3f}")
    print(f"Wrote {output}")


if __name__ == "__main__":
    main()
