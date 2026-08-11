from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .errors import ConfigError

LOCAL_CONFIG_FILE = Path(__file__).resolve().parent.parent.parent / "config.local.json"


def _load_local() -> dict[str, Any]:
    """读取本地配置文件（对应原 PHP 的 config.local.php，含真实密钥，不上 Git）。"""
    path = LOCAL_CONFIG_FILE
    override = os.getenv("ETS_LOCAL_CONFIG", "").strip()
    if override:
        path = Path(override).expanduser()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


_LOCAL_VALUES: dict[str, Any] = _load_local()


def save_local(values: dict[str, Any]) -> None:
    """把交互式配置持久化到本地配置文件。"""
    LOCAL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    merged = dict(_LOCAL_VALUES)
    merged.update({k: v for k, v in values.items() if v is not None and v != ""})
    LOCAL_CONFIG_FILE.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _env(name: str, default: str = "") -> str:
    """配置优先级：config.local.json > 环境变量 > 默认值。
    环境变量兜底仅为本工具自身路径类选项保留（如 ETS_LOCAL_CONFIG /
    ETS_SESSION_FILE），其余一律以 config.local.json 为准。"""
    if name in _LOCAL_VALUES and _LOCAL_VALUES[name] not in (None, ""):
        return str(_LOCAL_VALUES[name]).strip()
    value = os.getenv(name, "")
    return value.strip() if value else default


def _env_float(name: str, default: str) -> float:
    return float(_env(name, default))


def _env_bool(name: str, default: str = "1") -> bool:
    return _env(name, default).lower() not in {"", "0", "false", "no"}


@dataclass(frozen=True, slots=True)
class InterfaceConfig:
    api_base_url: str = ""
    pid: str = "local-demo"
    secret_key: str = "local-demo"
    sn: str = "t"
    system: str = "4"
    version: str = "3"
    client_version: str = "5.4.5"
    timeout: float = 30.0
    verify_tls: bool = True

    @classmethod
    def from_env(cls) -> "InterfaceConfig":
        return cls(
            api_base_url=_env("API_BASE_URL").rstrip("/"),
            pid="local-demo",
            secret_key="local-demo",
            sn="t",
            system="4",
            version="3",
            client_version="5.4.5",
            timeout=30.0,
        )

    def validate(self) -> None:
        if not self.api_base_url:
            raise ConfigError("API_BASE_URL is required for interface calls")
        parsed = urlparse(self.api_base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("API_BASE_URL must be an http(s) URL")

    def common_params(self) -> dict[str, object]:
        return {
            "sn": self.sn,
            "system": self.system,
            "global_client_version": self.client_version,
            "sign_response": 1,
        }


@dataclass(frozen=True, slots=True)
class IseConfig:
    app_id: str
    api_key: str
    api_secret: str
    ws_url: str = "wss://ise-api-dz-ets.xf-yun.com/v2/open-ise"
    open_timeout: float = 10.0
    response_timeout: float = 60.0
    verify_tls: bool = True

    @classmethod
    def from_env(cls) -> "IseConfig":
        return cls(
            app_id=_env("ISE_APP_ID"),
            api_key=_env("ISE_API_KEY"),
            api_secret=_env("ISE_API_SECRET"),
            ws_url="wss://ise-api-dz-ets.xf-yun.com/v2/open-ise",
            open_timeout=10.0,
            response_timeout=60.0,
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("ISE_APP_ID", self.app_id),
                ("ISE_API_KEY", self.api_key),
                ("ISE_API_SECRET", self.api_secret),
            )
            if not value
        ]
        if missing:
            raise ConfigError("Missing ISE configuration: " + ", ".join(missing))
        parsed = urlparse(self.ws_url)
        if parsed.scheme != "wss" or not parsed.hostname:
            raise ConfigError("ISE_WS_URL must be a valid wss:// URL")


@dataclass(frozen=True, slots=True)
class CloneTtsConfig:
    submit_url: str
    results_url: str
    api_key: str
    model: str = "pocket-tts"
    timeout: float = 30.0
    poll_interval: float = 2.0
    max_wait: float = 300.0

    @classmethod
    def from_env(cls) -> "CloneTtsConfig":
        return cls(
            submit_url=_env("CLONE_TTS_URL", "https://tts.ai/api/v1/voice-clone/"),
            results_url=_env("CLONE_TTS_RESULTS_URL", "https://api.tts.ai/v1/speech/results/"),
            api_key=_env("CLONE_TTS_API_KEY"),
            model=_env("CLONE_TTS_MODEL", "pocket-tts"),
            timeout=30.0,
            poll_interval=2.0,
            max_wait=300.0,
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("CLONE_TTS_URL", self.submit_url),
                ("CLONE_TTS_RESULTS_URL", self.results_url),
            )
            if not value
        ]
        if missing:
            raise ConfigError("Missing clone TTS configuration: " + ", ".join(missing))
        for name, value in (("CLONE_TTS_URL", self.submit_url), ("CLONE_TTS_RESULTS_URL", self.results_url)):
            parsed = urlparse(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ConfigError(f"{name} must be an http(s) URL")


@dataclass(frozen=True, slots=True)
class HttpTtsConfig:
    url: str
    api_key: str = ""
    style: str = "openai"
    results_url: str = ""
    model: str = "kokoro"
    voice: str = "af_bella"
    speed: float = 1.0
    timeout: float = 120.0
    poll_interval: float = 1.0
    max_wait: float = 120.0

    @classmethod
    def from_env(cls) -> "HttpTtsConfig":
        return cls(
            url=_env("HTTP_TTS_URL", "https://api.tts.ai/v1/tts/"),
            api_key=_env("HTTP_TTS_API_KEY"),
            style=_env("HTTP_TTS_STYLE", "ttsai"),
            results_url=_env("HTTP_TTS_RESULTS_URL", "https://api.tts.ai/v1/speech/results/"),
            model=_env("HTTP_TTS_MODEL", "kokoro"),
            voice=_env("HTTP_TTS_VOICE", "af_bella"),
            speed=1.0,
            timeout=120.0,
            poll_interval=1.0,
            max_wait=120.0,
        )

    def validate(self) -> None:
        if not self.url:
            raise ConfigError("HTTP_TTS_URL is required for HTTP TTS")
        if self.style not in {"openai", "gradium", "ttsai"}:
            raise ConfigError("HTTP_TTS_STYLE must be openai, gradium or ttsai")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("HTTP_TTS_URL must be an http(s) URL")
        if self.results_url:
            parsed_results = urlparse(self.results_url)
            if parsed_results.scheme not in {"http", "https"} or not parsed_results.hostname:
                raise ConfigError("HTTP_TTS_RESULTS_URL must be an http(s) URL")


DEFAULT_ETS_SECRET_KEY = "555ffbe95ccf4e9535a110170b445ab8"


@dataclass(frozen=True, slots=True)
class EtsConfig:
    api_url: str = "https://api.ets100.com"
    pid: str = "grlx"
    sn: str = "t"
    system: str = "4"
    version: str = "3"
    client_version: str = "5.4.5"
    secret_key: str = DEFAULT_ETS_SECRET_KEY
    device_code: str = ""
    phone: str = ""
    password: str = ""
    timeout: float = 30.0
    disable_ssl_verify: bool = True
    proxy: str = ""

    @classmethod
    def from_env(cls) -> "EtsConfig":
        return cls(
            api_url="https://api.ets100.com",
            pid="grlx",
            sn="t",
            system="4",
            version="3",
            client_version="5.4.5",
            secret_key=DEFAULT_ETS_SECRET_KEY,
            device_code=_env("ETS_DEVICE_CODE"),
            phone=_env("ETS_PHONE"),
            password=_env("ETS_PASS"),
            timeout=30.0,
            disable_ssl_verify=True,
            proxy=_env("HTTPS_PROXY"),
        )

    def validate(self) -> None:
        if not self.api_url:
            raise ConfigError("ETS_API_URL is required")
        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("ETS_API_URL must be an http(s) URL")
