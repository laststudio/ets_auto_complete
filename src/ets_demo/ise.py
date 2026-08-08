from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import ssl
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets

from .audio import prepare_ise_pcm, to_pcm16k
from .config import IseConfig
from .errors import IseError


@dataclass(frozen=True, slots=True)
class IseResult:
    sid: str
    xml: str
    scores: dict[str, float]
    result_key: str = ""
    audio_key: str = ""


def build_auth_url(config: IseConfig, now: datetime | None = None) -> str:
    config.validate()
    parsed = urlparse(config.ws_url)
    now = now or datetime.now(UTC)
    date = format_datetime(now, usegmt=True)
    request_line = f"GET {parsed.path} HTTP/1.1"
    signing = f"host: {parsed.hostname}\ndate: {date}\n{request_line}"
    signature = base64.b64encode(
        hmac.new(config.api_secret.encode(), signing.encode(), hashlib.sha256).digest()
    ).decode()
    authorization = (
        f'hmac username="{config.api_key}", algorithm="hmac-sha256", '
        f'headers="host date request-line", signature="{signature}"'
    )
    query = dict(parse_qsl(parsed.query))
    query.update(
        {
            "authorization": base64.b64encode(authorization.encode()).decode(),
            "date": date,
            "host": parsed.hostname or "",
        }
    )
    return urlunparse(parsed._replace(query=urlencode(query)))


def parse_scores(xml_text: str) -> dict[str, float]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise IseError(f"ISE returned invalid XML: {exc}") from exc
    wanted = ("rec_paper", "read_chapter", "read_sentence", "read_word", "retell")
    node = next(
        (
            element
            for element in root.iter()
            if element.tag.split("}")[-1] in wanted and "total_score" in element.attrib
        ),
        None,
    )
    if node is None:
        raise IseError("ISE XML has no supported score node")
    return {
        key: float(node.attrib.get(key, 0) or 0)
        for key in (
            "total_score",
            "fluency_score",
            "accuracy_score",
            "integrity_score",
            "standard_score",
            "stress_score",
        )
    }


class IseEvaluator:
    def __init__(self, config: IseConfig) -> None:
        config.validate()
        self.config = config

    async def evaluate_file(
        self,
        audio_path: Path,
        text: str,
        *,
        category: str = "read_sentence",
        resource_id: str = "",
    ) -> IseResult:
        pcm = prepare_ise_pcm(to_pcm16k(audio_path), category)
        return await self.evaluate_pcm(pcm, text, category=category, resource_id=resource_id)

    async def evaluate_pcm(
        self,
        pcm: bytes,
        text: str,
        *,
        category: str = "read_sentence",
        resource_id: str = "",
    ) -> IseResult:
        if not text.strip():
            raise IseError("Reference text is empty")
        auth_url = build_auth_url(self.config)
        ssl_context = ssl.create_default_context() if self.config.verify_tls else ssl._create_unverified_context()
        init_text = "ets" if resource_id else text
        init = {
            "common": {"app_id": self.config.app_id},
            "business": {
                "category": category,
                "res_id": resource_id,
                "ent": "ets-gpu",
                "language": "en_us",
                "plev": "0",
                "ppr_from": "gts" if resource_id else "",
                "result_level": "complete",
                "audiostorage": "1",
                "resultstorage": "1",
                "tte": "utf-8",
                "cmd": "ssb",
                "ssm": "1",
                "auf": "audio/L16;rate=16000",
                "aue": "raw",
                "ssb_txt": init_text,
                "text": init_text,
            },
            "data": {"status": 0, "data": ""},
        }

        try:
            async with websockets.connect(
                auth_url,
                ssl=ssl_context,
                open_timeout=self.config.open_timeout,
                ping_interval=20,
                ping_timeout=20,
                max_size=4 * 1024 * 1024,
            ) as websocket:
                await websocket.send(json.dumps(init, ensure_ascii=False))
                initial = await self._receive_json(websocket)
                self._check_response(initial, "ISE initialization")
                sid = str(initial.get("sid", ""))

                for index, offset in enumerate(range(0, len(pcm), 10240)):
                    chunk = pcm[offset : offset + 10240]
                    frame = {
                        "business": {"cmd": "auw", "aus": 1 if index == 0 else 2, "aue": "raw"},
                        "data": {
                            "status": 1,
                            "data": base64.b64encode(chunk).decode(),
                            "data_type": 1,
                            "encoding": "raw",
                        },
                    }
                    await websocket.send(json.dumps(frame, separators=(",", ":")))

                await websocket.send(
                    json.dumps(
                        {
                            "business": {"cmd": "auw", "aus": 4, "aue": "raw"},
                            "data": {"status": 2, "data": "", "data_type": 1, "encoding": "raw"},
                        },
                        separators=(",", ":"),
                    )
                )

                while True:
                    message = await self._receive_json(websocket)
                    self._check_response(message, "ISE evaluation")
                    data = message.get("data") if isinstance(message.get("data"), dict) else {}
                    if data.get("status") != 2:
                        continue
                    xml_bytes = base64.b64decode(data.get("data", ""), validate=True)
                    xml_text = xml_bytes.decode("utf-8", errors="replace")
                    return IseResult(
                        sid=sid or str(message.get("sid", "")),
                        xml=xml_text,
                        scores=parse_scores(xml_text),
                        result_key=str(data.get("result_key", "")),
                        audio_key=str(data.get("meepo_key", "")),
                    )
        except (OSError, asyncio.TimeoutError, websockets.WebSocketException, ValueError) as exc:
            raise IseError(f"ISE websocket failed: {exc}") from exc

    async def _receive_json(self, websocket) -> dict:
        raw = await asyncio.wait_for(websocket.recv(), timeout=self.config.response_timeout)
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", errors="replace")
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise IseError("ISE returned a non-JSON websocket message") from exc
        if not isinstance(message, dict):
            raise IseError("ISE websocket message is not an object")
        return message

    @staticmethod
    def _check_response(message: dict, context: str) -> None:
        code = message.get("code", 0)
        if code not in (0, "0", None):
            detail = message.get("message", message.get("msg", "unknown error"))
            raise IseError(f"{context} failed ({code}): {detail}")
