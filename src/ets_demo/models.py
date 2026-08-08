from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .errors import DemoError
from .homework import extract_questions, read_content_json


@dataclass(slots=True)
class Question:
    entity_id: str
    order: int
    text: str
    category: str = "read_sentence"
    structure_type: str = "collector.read"
    correct_option: str = ""
    options: list[dict[str, Any]] = field(default_factory=list)
    word_id: int = 0
    question_id: int = 0
    question_type: int = 0
    answer: str = ""
    translate: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any], index: int) -> "Question":
        return cls(
            entity_id=str(value.get("entity_id") or value.get("id") or f"local-{index + 1}"),
            order=int(value.get("order", index)),
            text=str(value.get("text", "")),
            category=str(value.get("category", "read_sentence")),
            structure_type=str(value.get("structure_type", "collector.read")),
            correct_option=str(value.get("correct_option", "")),
            options=list(value.get("options", [])),
            word_id=int(value.get("word_id", 0)),
            question_id=int(value.get("question_id", value.get("id", 0))) if str(value.get("question_id", value.get("id", "0"))).isdigit() else 0,
            question_type=int(value.get("question_type", 0)),
            answer=str(value.get("answer", value.get("correct_option", ""))),
            translate=str(value.get("translate", "")),
            metadata=dict(value.get("metadata", {})),
        )


@dataclass(slots=True)
class Homework:
    id: str
    name: str
    questions: list[Question]
    total_point: float = 100.0
    resource_id: str = "local-resource"
    set_id: str = "local-set"
    status: int = 0
    kind: str = "normal"
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "Homework":
        raw_questions = value.get("questions", [])
        if not isinstance(raw_questions, list):
            raise DemoError("homework.questions must be a list")
        questions = [Question.from_dict(item, index) for index, item in enumerate(raw_questions)]
        if not questions:
            raise DemoError("homework has no questions")
        return cls(
            id=str(value.get("id", "local-homework")),
            name=str(value.get("name", "Local homework")),
            questions=questions,
            total_point=float(value.get("total_point", 100.0)),
            resource_id=str(value.get("resource_id", "local-resource")),
            set_id=str(value.get("set_id", "local-set")),
            status=int(value.get("status", 0)),
            kind=str(value.get("kind", "normal")),
            metadata=dict(value.get("metadata", {})),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_homework(path: Path) -> Homework:
    suffix = path.suffix.lower()
    if suffix == ".zip":
        content = read_content_json(path)
        questions = extract_questions(content)
        value = {
            "id": path.stem,
            "name": path.stem,
            "questions": questions,
            "metadata": {"source": str(path), "structure_type": content.get("structure_type", "")},
        }
        return Homework.from_dict(value)

    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"Unable to load homework manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise DemoError("homework manifest must be a JSON object")
    if "questions" not in value and "structure_type" in value:
        value = {
            "id": path.stem,
            "name": path.stem,
            "questions": extract_questions(value),
            "metadata": {"source": str(path), "structure_type": value.get("structure_type", "")},
        }
    return Homework.from_dict(value)
