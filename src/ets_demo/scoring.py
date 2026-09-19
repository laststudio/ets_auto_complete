from __future__ import annotations

import json
import math
import random
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ScoreProfile:
    total: float = 5.0
    accuracy: float = 4.95
    fluency: float = 4.95
    integrity: float = 4.95
    stress: float = 4.90

    def clamped(self) -> "ScoreProfile":
        clamp = lambda value: max(0.0, min(5.0, float(value)))
        return ScoreProfile(
            total=clamp(self.total),
            accuracy=clamp(self.accuracy),
            fluency=clamp(self.fluency),
            integrity=clamp(self.integrity),
            stress=clamp(self.stress),
        )


def convert_score(
    score: float, maximum: float, graduation: float = 0.125, *, cap: bool = True
) -> float:
    if maximum <= 0:
        return 0.0
    value = score * maximum / 5.0
    if graduation > 0:
        value = math.ceil((value - 1e-12) / graduation) * graduation
    value = round(value, 2)
    return min(maximum, value) if cap else max(0.0, value)


def build_score_detail(
    profile: ScoreProfile,
    *,
    category: str,
    choose: str = "",
    repeat_mode: bool = True,
    graduation: float = 0.125,
    clamp: bool = True,
) -> str:
    if clamp:
        profile = profile.clamped()
    return json.dumps(
        {
            "total_score": f"{profile.total:.2f}",
            "choose": choose,
            "fluency_score": f"{profile.fluency:.2f}",
            "accuracy_score": f"{profile.accuracy:.2f}",
            "integrity_score": f"{profile.integrity:.2f}",
            "standard_score": f"{profile.stress:.2f}",
            "category": category,
            "repeat_mode": "0" if repeat_mode else "1",
            "version": "3",
            "rate_scale": 1 if graduation == 0.125 else 0,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _phonemes(word: str) -> list[str]:
    word = re.sub(r"[^a-zA-Z]", "", word).lower()
    if not word:
        return ["*"]
    pairs = {
        "th": "θ", "sh": "ʃ", "ch": "tʃ", "wh": "w", "ph": "f", "ck": "k",
        "ng": "ŋ", "oo": "uː", "ee": "iː", "ea": "iː", "ou": "aʊ",
        "ow": "aʊ", "ai": "eɪ", "ay": "eɪ", "oi": "ɔɪ", "oy": "ɔɪ",
    }
    result: list[str] = []
    index = 0
    while index < len(word):
        pair = word[index : index + 2]
        if pair in pairs:
            result.append(pairs[pair])
            index += 2
        else:
            result.append(word[index])
            index += 1
    return result


def generate_score_xml(
    text: str,
    profile: ScoreProfile,
    *,
    category: str = "read_sentence",
    seed: int | None = None,
    clamp: bool = True,
) -> str:
    if clamp:
        profile = profile.clamped()
    randomizer = random.Random(seed)
    root = ET.Element("xml_result")
    category_name = category if category in {"read_sentence", "read_chapter", "talk_recite", "talk_free"} else "read_chapter"
    category_node = ET.SubElement(root, category_name)

    def _bounded(value: float) -> float:
        return max(0.0, min(5.0, value)) if clamp else max(0.0, value)

    standard = _bounded(profile.total + randomizer.uniform(-0.1, 0.1))
    paper = ET.SubElement(
        category_node,
        "rec_paper",
        {
            "total_score": f"{profile.total:.2f}",
            "accuracy_score": f"{profile.accuracy:.2f}",
            "fluency_score": f"{profile.fluency:.2f}",
            "integrity_score": f"{profile.integrity:.2f}",
            "standard_score": f"{standard:.2f}",
            "stress_score": f"{profile.stress:.2f}",
            "is_rejected": "false",
            "except_info": "",
            "content": text,
        },
    )
    sentences = [part.strip() for part in re.split(r"[.!?]+", text) if part.strip()] or [text]
    for sentence_text in sentences:
        sentence_score = _bounded(profile.total + randomizer.uniform(-0.15, 0.15))
        sentence = ET.SubElement(
            paper,
            "sentence",
            {"content": sentence_text, "total_score": f"{sentence_score:.2f}"},
        )
        for word_text in sentence_text.split():
            word_score = _bounded(sentence_score + randomizer.uniform(-0.3, 0.3))
            word = ET.SubElement(
                sentence,
                "word",
                {"content": word_text, "total_score": f"{word_score:.2f}", "dp_message": "0"},
            )
            for phoneme in _phonemes(word_text):
                ET.SubElement(word, "phoneme", {"content": phoneme})
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")
