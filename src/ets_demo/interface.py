from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import InterfaceConfig
from .errors import InterfaceError

JsonObject = dict[str, Any]


def build_signed_payload(
    route: str,
    params: JsonObject,
    *,
    pid: str,
    secret_key: str,
    timestamp: int | None = None,
) -> JsonObject:
    timestamp = int(time.time()) if timestamp is None else timestamp
    raw = json.dumps(
        [{"r": route, "params": params}], ensure_ascii=False, separators=(",", ":")
    ).encode()
    body = base64.b64encode(raw).decode("ascii")
    signature = hashlib.md5(
        f"{pid}{timestamp}{body}{secret_key}".encode(), usedforsecurity=False
    ).hexdigest()
    return {
        "body": body,
        "head": {"version": "1.0", "sign": signature, "pid": pid, "time": timestamp},
    }


def decode_response_body(response: Any) -> JsonObject:
    if isinstance(response, list):
        response = response[0] if response else {}
    if not isinstance(response, dict):
        raise InterfaceError("Interface response is not a JSON object")
    result = dict(response)
    if isinstance(result.get("body"), str):
        try:
            result["body"] = json.loads(base64.b64decode(result["body"], validate=True))
        except (ValueError, json.JSONDecodeError):
            pass
    return result


class InterfaceClient:
    """Original endpoint contract pointed exclusively at a local mock/backend."""

    def __init__(
        self,
        config: InterfaceConfig,
        *,
        transport: Callable[[str, JsonObject], Any] | None = None,
        clock: Callable[[], int] | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._clock = clock or (lambda: int(time.time()))
        self._transport = transport

    def __enter__(self) -> "InterfaceClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        return

    def request(self, path: str, route: str, params: JsonObject) -> JsonObject:
        payload = build_signed_payload(
            route,
            params,
            pid=self.config.pid,
            secret_key=self.config.secret_key,
            timestamp=self._clock(),
        )
        try:
            if self._transport is not None:
                raw = self._transport(path, payload)
            else:
                request = Request(
                    self.config.api_base_url + path,
                    data=json.dumps(payload, ensure_ascii=False).encode(),
                    headers={
                        "Content-Type": "application/json",
                        "User-Agent": "study-workflow-demo/0.2",
                    },
                    method="POST",
                )
                with urlopen(request, timeout=self.config.timeout) as response:
                    raw = json.loads(response.read())
            result = decode_response_body(raw)
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
            raise InterfaceError(f"Local interface request failed: {exc}") from exc
        code = result.get("code", 0)
        if code not in (0, "0", 200, "200", None):
            raise InterfaceError(f"Interface error {code}: {result.get('msg', 'unknown error')}")
        return result

    def common(self, session_id: str = "") -> JsonObject:
        return {
            "sn": self.config.sn,
            "token": session_id,
            "version": self.config.version,
            "system": self.config.system,
            "global_client_version": self.config.client_version,
            "sign_response": 1,
        }

    def homework_list(self, session_id: str = "", parent_id: str = "") -> list[JsonObject]:
        merged: list[JsonObject] = []
        seen: set[str] = set()
        for status in ("0", "2", "3"):
            result = self.request(
                "/g/homework/list",
                "g/homework/list",
                {
                    **self.common(session_id),
                    "parent_account_id": parent_id,
                    "limit": "0",
                    "status": status,
                    "offset": "0",
                    "show_old_homework": 1,
                },
            )
            body = result.get("body", {})
            for item in body.get("data", []) if isinstance(body, dict) else []:
                item_id = str(item.get("id", item.get("homework_id", "")))
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    merged.append(item)
        return merged

    def homework_struct(self, homework_id: str, session_id: str = "", resource_id: str = "") -> JsonObject:
        params = {**self.common(session_id), "id": str(homework_id)}
        if resource_id:
            params["resource_id"] = resource_id
        result = self.request("/g/homework/struct", "g/homework/struct", params)
        return result.get("body", {}) if isinstance(result.get("body"), dict) else {}

    def check_start(self, homework_id: str, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/homework/checkstart",
            "g/homework/checkstart",
            {**self.common(session_id), "homework_id": str(homework_id)},
        )

    def ppr_resources(self, batch: list[JsonObject], session_id: str = "") -> JsonObject:
        return self.request(
            "/g/set/ppr-res", "g/set/ppr-res", {**self.common(session_id), "batch": batch}
        )

    def pre_upload(self, session_id: str = "") -> JsonObject:
        return self.request("/m/file/pre-upload", "m/file/pre-upload", self.common(session_id))

    def oss_config(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/system/oss-config", "system/oss-config", {**self.common(session_id), **params}
        )

    def audio_sync_v2(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/audio/sync-v2", "m/audio/sync-v2", {**self.common(session_id), **params}
        )

    def set_use_time(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/homework/set-use-time",
            "m/homework/set-use-time",
            {**self.common(session_id), **params},
        )

    def report_score(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/homework/report-score",
            "g/homework/report-score",
            {**self.common(session_id), **params},
        )

    def score_drop(self, homework_id: str, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/homework/score-drop",
            "g/homework/score-drop",
            {**self.common(session_id), "homework_id": str(homework_id), "resource_id": "", "seq_id": ""},
        )

    def pk_info(self, homework_id: str, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/homework/pk-info",
            "g/homework/pk-info",
            {**self.common(session_id), "homework_id": str(homework_id)},
        )

    def start_word_pk(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/pk/start-word-pk",
            "g/pk/start-word-pk",
            {**self.common(session_id), **params},
        )

    def submit_word_pk(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/pk/submit-word",
            "g/pk/submit-word",
            {**self.common(session_id), **params},
        )

    def score_sync(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/g/homework/score-sync",
            "g/homework/score-sync",
            {**self.common(session_id), **params},
        )

    def exam_sync(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/exam/sync", "m/exam/sync", {**self.common(session_id), **params}
        )

    def exam_submit(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/exam/submit", "m/exam/submit", {**self.common(session_id), **params}
        )

    def save_composition_draft(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/composition/save-draft",
            "m/composition/save-draft",
            {**self.common(session_id), **params},
        )

    def mark_composition(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/composition/mark",
            "m/composition/mark",
            {**self.common(session_id), **params},
        )

    def submit_homework(self, params: JsonObject, session_id: str = "") -> JsonObject:
        return self.request(
            "/m/homework/submit",
            "m/homework/submit",
            {**self.common(session_id), **params},
        )
