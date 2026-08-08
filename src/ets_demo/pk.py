from __future__ import annotations

import json
from typing import Any

from .models import Question


def normalize_options(options: list[dict[str, Any]]) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for option in options:
        name = str(option.get("option_name", option.get("name", option.get("id", ""))))
        value = str(option.get("option_value", option.get("value", option.get("text", ""))))
        if name or value:
            result.append({"option_name": name, "option_value": value})
    return result


def answer_text(question: Question) -> str:
    answer = question.answer or question.correct_option
    if question.question_type == 19:
        return question.translate or answer
    if question.question_type in {16, 17, 18, 20}:
        return question.text or answer
    return answer or question.text


def wrong_answer(question: Question) -> str:
    answer = answer_text(question)
    options = normalize_options(question.options)
    answer_is_name = any(item["option_name"] == answer for item in options)
    answer_is_value = any(item["option_value"] == answer for item in options)
    for item in options:
        candidate = item["option_name"] if answer_is_name else item["option_value"]
        if candidate and candidate != answer:
            return candidate
    if answer_is_value:
        return ""
    return "X"


def build_pk_detail(question: Question, correct: bool) -> dict[str, Any]:
    answer = answer_text(question) if correct else wrong_answer(question)
    return {
        "point": 1 if correct else 0,
        "answer": answer,
        "word_id": question.word_id,
        "question_id": question.question_id or question.order + 1,
    }


def build_vocab_detail(
    question: Question, correct: bool, client_time: str, version: str = "local"
) -> dict[str, Any]:
    detail = build_pk_detail(question, correct)
    return {
        "word_id": detail["word_id"],
        "question_id": detail["question_id"],
        "score_rate": detail["point"],
        "score_detail": json.dumps(
            {"choose": str(detail["answer"]), "version": version},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        "client_time": client_time,
        "answer": detail["answer"],
        "point": float(detail["point"]),
    }
