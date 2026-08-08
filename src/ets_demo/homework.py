from __future__ import annotations

import hashlib
import html
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any

from .errors import DemoError


def clean_html(text: str) -> str:
    text = re.sub(r"</?br\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"</?p\s*/?>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def derive_zip_password(data: bytes) -> str:
    if len(data) < 336:
        raise DemoError("ZIP is too small to contain the ETS footer")
    footer = data[-336:]
    seed = footer[16:144]
    first = hashlib.md5(seed, usedforsecurity=False).hexdigest().upper()
    second = hashlib.md5(first.encode(), usedforsecurity=False).hexdigest().upper()
    return first + second


def read_content_json(path: Path) -> dict[str, Any]:
    return parse_content_bytes(path.read_bytes())


def parse_content_bytes(data: bytes) -> dict[str, Any]:
    password = derive_zip_password(data).encode()
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [name for name in archive.namelist() if Path(name).name == "content.json"]
            if not names:
                raise DemoError("content.json was not found in the archive")
            with archive.open(names[0], pwd=password) as source:
                parsed = json.load(source)
    except (OSError, zipfile.BadZipFile, RuntimeError, json.JSONDecodeError) as exc:
        raise DemoError(f"Unable to read ETS ZIP: {exc}") from exc
    if not isinstance(parsed, dict):
        raise DemoError("content.json is not a JSON object")
    return parsed


def extract_questions(content: dict[str, Any]) -> list[dict[str, Any]]:
    structure_type = str(content.get("structure_type", ""))
    info = content.get("info") if isinstance(content.get("info"), dict) else {}
    questions: list[dict[str, Any]] = []

    def add(text: str, order: int, category: str = "read_sentence", **extra: Any) -> None:
        cleaned = clean_html(str(text or ""))
        if cleaned or category == "choose":
            questions.append(
                {
                    "entity_id": "",
                    "order": order,
                    "text": cleaned,
                    "category": category,
                    "structure_type": structure_type,
                    **extra,
                }
            )

    if structure_type in {"collector.read", "collector.word"}:
        add(info.get("value", ""), 0)
    elif structure_type == "collector.role":
        for index, item in enumerate(info.get("question", [])):
            std = item.get("std", []) if isinstance(item, dict) else []
            if std:
                add(std[0].get("value", ""), index)
    elif structure_type == "collector.choose":
        for index, item in enumerate(info.get("xtlist", [])):
            answer = str(item.get("answer", ""))
            answer_text = ""
            for option in item.get("xxlist", []):
                option_id = str(option.get("xx_xh", option.get("xxXh", "")))
                if option_id == answer:
                    answer_text = option.get("xx_nr", option.get("xxNr", ""))
                    break
            answer_text = answer_text or item.get("xt_value", item.get("xtValue", ""))
            answer_text = answer_text or item.get("xt_nr", item.get("xtNr", ""))
            add(answer_text, index, "choose", correct_option=answer)
    elif structure_type in {"collector.fill", "collector.3q5a", "collector.picture"}:
        std = info.get("std", [])
        add(std[0].get("value", "") if std else info.get("value", ""), 0)
    elif structure_type == "collector.repeat_essay":
        for index, item in enumerate(info.get("sublist", [])):
            add(item.get("text", ""), index)
        if not questions:
            add(info.get("value", ""), 0)
    return questions
