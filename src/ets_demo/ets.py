from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import EtsConfig
from .errors import EtsError
from .homework import clean_html, parse_content_bytes
from .interface import build_signed_payload, decode_response_body

JsonObject = dict[str, Any]

DEFAULT_SESSION_FILE = Path.home() / ".ets_demo" / "session.json"
ZIP_CDN_PREFIX = "https://cdn.subject.ets100.com/made/"


def session_file() -> Path:
    override = os.getenv("ETS_SESSION_FILE", "").strip()
    return Path(override).expanduser() if override else DEFAULT_SESSION_FILE


def _http_context(config: EtsConfig) -> Any:
    if config.disable_ssl_verify:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context
    return None


@dataclass(slots=True)
class EtsSession:
    phone: str = ""
    password_enc: str = ""
    token: str = ""
    parent_id: str = ""
    device_code: str = ""
    updated_at: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> "EtsSession":
        path = path or session_file()
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        return cls(
            phone=str(raw.get("phone", "")),
            password_enc=str(raw.get("password_enc", "")),
            token=str(raw.get("token", "")),
            parent_id=str(raw.get("parent_id", "")),
            device_code=str(raw.get("device_code", "")),
            updated_at=int(raw.get("updated_at", 0)),
        )

    def save(self, path: Path | None = None) -> None:
        path = path or session_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.updated_at = int(time.time())
        path.write_text(
            json.dumps(
                {
                    "phone": self.phone,
                    "password_enc": self.password_enc,
                    "token": self.token,
                    "parent_id": self.parent_id,
                    "device_code": self.device_code,
                    "updated_at": self.updated_at,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass

    def update(self, other: "EtsSession") -> None:
        for field in ("phone", "password_enc", "token", "parent_id", "device_code"):
            value = getattr(other, field)
            if value:
                setattr(self, field, value)


def encode_password(password: str) -> str:
    return base64.b64encode(password.encode()).decode()


def decode_password(password_enc: str) -> str:
    try:
        return base64.b64decode(password_enc.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return ""


def generate_device_code() -> str:
    def part() -> str:
        return hashlib.md5(os.urandom(16), usedforsecurity=False).hexdigest().upper()[:16]

    return f"{part()}|{part()}"


def _resolved_device_code(config: EtsConfig, session: EtsSession | None = None) -> str:
    if config.device_code:
        return config.device_code
    if session is not None and session.device_code:
        return session.device_code
    return generate_device_code()


def classify_homework(item: JsonObject) -> str:
    name = str(item.get("name", ""))
    if str(item.get("pk", "0")) == "1" or "PK" in name.upper() or "单词对战" in name or "对战" in name:
        return "pk"
    if str(item.get("set_type", "")) == "6" or any(k in name for k in ("词汇", "单词", "默写")):
        return "vocab"
    if any(k in name for k in ("作文", "写作", "writing")):
        return "composition"
    if any(k in name for k in ("模拟", "测试", "考试", "期中", "期末", "月考")):
        return "mock"
    if any(k in name for k in ("配音", "dub", "视频")):
        return "dubbing"
    return "normal"


def _merge_questions(primary: list[JsonObject], *extras: list[JsonObject]) -> list[JsonObject]:
    seen: set[str] = set()
    merged: list[JsonObject] = []
    for group in (primary, *extras):
        for item in group:
            key = str(
                (item.get("entity_id") or "") + "|"
                + str(item.get("order", "")) + "|"
                + str(item.get("category", "")) + "|"
                + str(item.get("text", ""))[:20]
            )
            if key not in seen:
                seen.add(key)
                merged.append(item)
    return merged


def parse_homework_struct(struct: JsonObject, *, downloader: Callable[[str], bytes] | None = None) -> list[JsonObject]:
    questions: list[JsonObject] = []
    for item in struct.get("lvread_question", []):
        entity_id = str(item.get("entity_id") or item.get("id") or "")
        text = str(item.get("text") or item.get("en_text") or item.get("content") or "")
        if entity_id and text:
            questions.append(
                {
                    "entity_id": entity_id,
                    "order": int(item.get("order", 0)),
                    "text": clean_html(text),
                    "category": "read_sentence",
                    "structure_type": "lvread",
                }
            )
    for item in struct.get("word_question", []):
        entity_id = str(item.get("entity_id") or item.get("id") or "")
        if entity_id:
            questions.append(
                {
                    "entity_id": entity_id,
                    "order": int(item.get("order", 0)),
                    "text": "",
                    "category": "choose",
                    "correct_option": str(item.get("answer") or item.get("correct") or ""),
                    "structure_type": "word_question",
                }
            )
    for index, content in enumerate(struct.get("contents", [])):
        entity_id = str(content.get("real_id") or content.get("entity_id") or "")
        order = int(content.get("order", index))
        group_name = str(content.get("group_name") or f"题型{index + 1}")
        zip_url = str(content.get("url") or content.get("zip_url") or "")
        if zip_url and downloader is not None:
            full_url = zip_url if zip_url.startswith("http") else ZIP_CDN_PREFIX + zip_url
            try:
                parsed = parse_content_bytes(downloader(full_url))
                extracted = extract_content_questions(parsed)
            except Exception:
                extracted = []
            for question in extracted:
                if not question.get("entity_id"):
                    question["entity_id"] = entity_id
                if question.get("order") is None:
                    question["order"] = order
                question["group_name"] = group_name
            questions.extend(extracted)
        elif entity_id:
            text = str(content.get("text") or content.get("en_text") or "")
            if text:
                questions.append(
                    {
                        "entity_id": entity_id,
                        "order": order,
                        "text": clean_html(text),
                        "category": "read_sentence",
                        "group_name": group_name,
                    }
                )
    return questions


def extract_content_questions(content: JsonObject) -> list[JsonObject]:
    from .homework import extract_questions

    return extract_questions(content)


class EtsClient:
    """Real ETS platform client with the preserved signature protocol."""

    def __init__(
        self,
        config: EtsConfig,
        *,
        transport: Callable[[str, JsonObject], Any] | None = None,
        clock: Callable[[], int] | None = None,
        downloader: Callable[[str], bytes] | None = None,
        session: EtsSession | None = None,
    ) -> None:
        config.validate()
        self.config = config
        self._clock = clock or (lambda: int(time.time()))
        self._transport = transport
        self._downloader = downloader or self._default_download
        self.session = session or EtsSession.load()
        self.token = self.session.token
        self.parent_id = self.session.parent_id

    def __enter__(self) -> "EtsClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        return

    def common(self, token: str | None = None) -> JsonObject:
        return {
            "sn": self.config.sn,
            "token": token or self.token,
            "version": self.config.version,
            "system": self.config.system,
            "global_client_version": self.config.client_version,
            "sign_response": 1,
        }

    def _default_download(self, url: str) -> bytes:
        request = Request(
            url,
            headers={"User-Agent": "libcurl-agent/1.0"},
            method="GET",
        )
        with urlopen(request, timeout=self.config.timeout, context=_http_context(self.config)) as response:
            return response.read()

    def request(self, path: str, route: str, params: JsonObject) -> JsonObject:
        max_attempts = 4
        for attempt in range(max_attempts):
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
                    proxy_handler = None
                    handlers: list[Any] = []
                    if self.config.proxy:
                        from urllib.request import ProxyHandler

                        handlers.append(ProxyHandler({"http": self.config.proxy, "https": self.config.proxy}))
                    opener = urlopen
                    if handlers:
                        import urllib.request

                        opener = urllib.request.build_opener(*handlers).open
                    request = Request(
                        self.config.api_url + path,
                        data=json.dumps(payload, ensure_ascii=False).encode(),
                        headers={
                            "Content-Type": "application/json",
                            "User-Agent": "libcurl-agent/1.0",
                        },
                        method="POST",
                    )
                    with opener(request, timeout=self.config.timeout, context=_http_context(self.config)) as response:
                        raw = json.loads(response.read())
                result = decode_response_body(raw)
            except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError) as exc:
                if attempt == max_attempts - 1:
                    raise EtsError(f"ETS request failed: {exc}") from exc
                continue
            code = result.get("code")
            if code in (9001, 20004, "9001", "20004"):
                if self._try_relogin() and attempt < max_attempts - 1:
                    params = dict(params)
                    params["token"] = self.token
                    continue
            return result
        raise EtsError("ETS request failed after retries")

    def call(self, path: str, route: str, params: JsonObject) -> JsonObject:
        result = self.request(path, route, params)
        code = result.get("code")
        if code not in (0, "0", 200, "200", None):
            raise EtsError(f"ETS error {code}: {result.get('msg', 'unknown error')}")
        body = result.get("body", {})
        return body if isinstance(body, dict) else {}

    def _try_relogin(self) -> bool:
        phone = self.config.phone or self.session.phone
        password = self.config.password or decode_password(self.session.password_enc)
        if not phone or not password:
            return False
        try:
            self.login(phone, password)
            return True
        except EtsError:
            return False

    def login(self, phone: str, password: str) -> JsonObject:
        device_code = _resolved_device_code(self.config, self.session)
        params = {
            "sn": self.config.sn,
            "phone": phone,
            "password": password,
            "device_code": device_code,
            "device_name": "DESKTOP",
            "version": "3",
            "local_ip": "127.0.0.1",
            "system": self.config.system,
            "global_client_version": self.config.client_version,
            "sign_response": 1,
        }
        result = self.request("/user/login", "user/login", params)
        code = result.get("code")
        if code in (30014, 30008, "30014", "30008"):
            body = self.rebind(phone, password, device_code)
        elif result.get("code") not in (0, "0", 200, "200", None):
            raise EtsError(f"ETS login failed: {result.get('msg', result.get('code'))}")
        else:
            rebind_body = result.get("body", {})
            body = {"token": rebind_body.get("token", "")} if isinstance(rebind_body, dict) else {"token": ""}
        token = str(body.get("token", ""))
        if not token:
            raise EtsError("ETS login returned no token")
        self.token = token
        self.session.phone = phone
        self.session.password_enc = encode_password(password)
        self.session.device_code = device_code
        self.session.save()
        return body

    def rebind(self, phone: str, password: str, device_code: str) -> JsonObject:
        params = {
            "sn": self.config.sn,
            "phone": phone,
            "email": "",
            "password": password,
            "code": "0",
            "version": "2",
            "device_name": "1337",
            "device_code": device_code,
            "local_ip": "127.0.0.1",
            "system": self.config.system,
            "global_client_version": self.config.client_version,
            "sign_response": 1,
        }
        result = self.request("/user/rebind-code", "user/rebind-code", params)
        if result.get("code") not in (0, "0", 200, "200", None):
            raise EtsError(f"ETS device rebind failed: {result.get('msg', result.get('code'))}")
        body = result.get("body", {})
        token = str(body.get("token", "")) if isinstance(body, dict) else ""
        if not token:
            raise EtsError("ETS device rebind returned no token")
        self.token = token
        return body

    def get_parent_id(self, token: str | None = None) -> str:
        params = {
            "sn": self.config.sn,
            "system": self.config.system,
            "local_ip": "127.0.0.1",
            "global_client_version": self.config.client_version,
            "sign_response": 1,
            "token": token or self.token,
            "version": "3",
        }
        body = self.call("/m/ecard/list", "m/ecard/list", params)
        accounts = body.get("data", []) if isinstance(body, dict) else []
        if not isinstance(accounts, list):
            accounts = []
        for account in accounts:
            if (
                str(account.get("status", "")) == "0"
                and str(account.get("out_of_date", "")) == "0"
                and str(account.get("mobile_status", "")) == "1"
            ):
                parent_id = str(account.get("parent_id", ""))
                if parent_id:
                    return parent_id
        for account in accounts:
            parent_id = str(account.get("parent_id", ""))
            if parent_id:
                return parent_id
        raise EtsError("Unable to find a parent_id from m/ecard/list")

    def get_homework_list(self, parent_id: str = "", token: str | None = None) -> list[JsonObject]:
        token = token or self.token
        merged: list[JsonObject] = []
        seen: set[str] = set()
        for status in ("0", "2", "3"):
            params = {
                "sn": self.config.sn,
                "token": token,
                "parent_account_id": parent_id,
                "limit": "0",
                "status": status,
                "offset": "0",
                "max_end_time": "",
                "max_homework_id": "",
                "min_end_time": "",
                "min_homework_id": "",
                "get_to_do_count": 1,
                "show_old_homework": 1,
                "parent_homework_id": "",
                "check_pass": 1,
                "get_to_overtime_count": 1,
                "version": self.config.version,
                "system": self.config.system,
                "global_client_version": self.config.client_version,
                "sign_response": 1,
            }
            body = self.call("/g/homework/list", "g/homework/list", params)
            for item in body.get("data", []) if isinstance(body, dict) else []:
                item_id = str(item.get("id", item.get("homework_id", "")))
                if item_id and item_id not in seen:
                    seen.add(item_id)
                    merged.append(item)
        return merged

    def get_homework_struct(self, homework_id: str, resource_id: str = "", token: str | None = None) -> JsonObject:
        params = {
            **self.common(token),
            "id": str(homework_id),
        }
        if resource_id:
            params["resource_id"] = resource_id
        body = self.call("/g/homework/struct", "g/homework/struct", params)
        if not body.get("lvread_question") and not body.get("word_question") and not body.get("contents"):
            retry = dict(params)
            retry["resource_id"] = ""
            body = self.call("/g/homework/struct", "g/homework/struct", retry)
        return body

    def get_homework_info(self, homework_id: str, resource_id: str = "", parent_id: str = "", token: str | None = None) -> JsonObject:
        params = {
            **self.common(token),
            "homework_id": str(homework_id),
            "resource_id": str(resource_id),
            "version": "2",
        }
        if parent_id:
            params["parent_account_id"] = parent_id
        return self.call("/m/homework/info", "m/homework/info", params)

    def get_score_detail(self, homework_id: str, resource_id: str = "", parent_id: str = "", token: str | None = None) -> JsonObject:
        params = {
            **self.common(token),
            "homework_id": int(homework_id),
            "resource_id": str(resource_id),
            "parent_account_id": parent_id,
        }
        return self.call("/g/homework/score-detail", "g/homework/score-detail", params)

    def check_start(self, homework_id: str, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/homework/checkstart",
            "g/homework/checkstart",
            {**self.common(token), "homework_id": int(homework_id)},
        )

    def ppr_resources(self, batch: list[JsonObject], token: str | None = None) -> JsonObject:
        body = self.call("/g/set/ppr-res", "g/set/ppr-res", {**self.common(token), "batch": batch})
        return body

    def pre_upload(self, token: str | None = None) -> JsonObject:
        return self.call("/m/file/pre-upload", "m/file/pre-upload", self.common(token))

    def oss_config(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/system/oss-config",
            "system/oss-config",
            {**self.common(token), **params},
        )

    def audio_sync_v2(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call("/m/audio/sync-v2", "m/audio/sync-v2", {**self.common(token), **params})

    def set_use_time(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/m/homework/set-use-time",
            "m/homework/set-use-time",
            {**self.common(token), **params},
        )

    def report_score(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/homework/report-score",
            "g/homework/report-score",
            {**self.common(token), **params},
        )

    def score_drop(self, homework_id: str, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/homework/score-drop",
            "g/homework/score-drop",
            {**self.common(token), "homework_id": int(homework_id), "resource_id": "", "seq_id": ""},
        )

    def pk_info(self, homework_id: str, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/homework/pk-info",
            "g/homework/pk-info",
            {**self.common(token), "homework_id": int(homework_id)},
        )

    def start_word_pk(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/pk/start-word-pk",
            "g/pk/start-word-pk",
            {**self.common(token), **params},
        )

    def submit_word_pk(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/pk/submit-word",
            "g/pk/submit-word",
            {**self.common(token), **params},
        )

    def score_sync(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/g/homework/score-sync",
            "g/homework/score-sync",
            {**self.common(token), **params},
        )

    def exam_struct(self, exam_id: str, resource_id: str = "", token: str | None = None) -> JsonObject:
        return self.call(
            "/m/exam/struct",
            "m/exam/struct",
            {
                **self.common(token),
                "exam_id": str(exam_id),
                "resource_id": str(resource_id),
                "scence_type": 0,
            },
        )

    def exam_sync(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call("/m/exam/sync", "m/exam/sync", {**self.common(token), **params})

    def exam_submit(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call("/m/exam/submit", "m/exam/submit", {**self.common(token), **params})

    def save_composition_draft(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/m/composition/save-draft",
            "m/composition/save-draft",
            {**self.common(token), **params},
        )

    def mark_composition(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/m/composition/mark",
            "m/composition/mark",
            {**self.common(token), **params},
        )

    def submit_homework(self, params: JsonObject, token: str | None = None) -> JsonObject:
        return self.call(
            "/m/homework/submit",
            "m/homework/submit",
            {**self.common(token), **params},
        )

    def get_ise_config(self, token: str | None = None) -> JsonObject:
        routes = (
            "/p1/student/config/config/iseInfo",
            "/p1/student/config/config/iseConfig",
            "/g/config/ise",
            "/p1/student/config/config/xfIseConfig",
        )
        for path in routes:
            route = path.lstrip("/")
            try:
                body = self.call(path, route, self.common(token))
            except EtsError:
                continue
            config = self._extract_ise_config(body)
            if config:
                return config
        return {}

    @staticmethod
    def _extract_ise_config(body: JsonObject) -> JsonObject:
        candidates = [body, body.get("ise") if isinstance(body.get("ise"), dict) else {},
                      body.get("xfyun") if isinstance(body.get("xfyun"), dict) else {}]
        keys = {
            "app_id": ("app_id", "appId", "ise_app_id"),
            "api_key": ("api_key", "apiKey", "ise_api_key"),
            "api_secret": ("api_secret", "apiSecret", "ise_api_secret"),
        }
        result: JsonObject = {}
        for source in candidates:
            if not isinstance(source, dict):
                continue
            for field, names in keys.items():
                if field in result:
                    continue
                for name in names:
                    value = source.get(name)
                    if value:
                        result[field] = str(value)
                        break
        return result

    def resolve_parent_id(self, token: str | None = None) -> str:
        if self.parent_id:
            return self.parent_id
        self.parent_id = self.get_parent_id(token)
        return self.parent_id
