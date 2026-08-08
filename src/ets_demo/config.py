from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from .errors import ConfigError


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


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
            pid=_env("LOCAL_API_PID", "local-demo"),
            secret_key=_env("LOCAL_API_SECRET", "local-demo"),
            sn=_env("API_SN", "t"),
            system=_env("API_SYSTEM", "4"),
            version=_env("API_VERSION", "3"),
            client_version=_env("API_CLIENT_VERSION", "5.4.5"),
            timeout=float(_env("API_TIMEOUT", "30")),
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
            ws_url=_env("ISE_WS_URL", "wss://ise-api-dz-ets.xf-yun.com/v2/open-ise"),
            open_timeout=float(_env("ISE_OPEN_TIMEOUT", "10")),
            response_timeout=float(_env("ISE_RESPONSE_TIMEOUT", "60")),
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
    timeout: float = 30.0
    poll_interval: float = 2.0
    max_wait: float = 300.0

    @classmethod
    def from_env(cls) -> "CloneTtsConfig":
        return cls(
            submit_url=_env("CLONE_TTS_URL"),
            results_url=_env("CLONE_TTS_RESULTS_URL"),
            api_key=_env("CLONE_TTS_API_KEY"),
            timeout=float(_env("CLONE_TTS_TIMEOUT", "30")),
            poll_interval=float(_env("CLONE_TTS_POLL_INTERVAL", "2")),
            max_wait=float(_env("CLONE_TTS_MAX_WAIT", "300")),
        )

    def validate(self) -> None:
        missing = [
            name
            for name, value in (
                ("CLONE_TTS_URL", self.submit_url),
                ("CLONE_TTS_RESULTS_URL", self.results_url),
                ("CLONE_TTS_API_KEY", self.api_key),
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
            url=_env("HTTP_TTS_URL"),
            api_key=_env("HTTP_TTS_API_KEY"),
            style=_env("HTTP_TTS_STYLE", "openai").lower(),
            results_url=_env("HTTP_TTS_RESULTS_URL"),
            model=_env("HTTP_TTS_MODEL", "kokoro"),
            voice=_env("HTTP_TTS_VOICE", "af_bella"),
            speed=float(_env("HTTP_TTS_SPEED", "1.0")),
            timeout=float(_env("HTTP_TTS_TIMEOUT", "120")),
            poll_interval=float(_env("HTTP_TTS_POLL_INTERVAL", "1")),
            max_wait=float(_env("HTTP_TTS_MAX_WAIT", "120")),
        )

    def validate(self) -> None:
        if not self.url:
            raise ConfigError("HTTP_TTS_URL is required for HTTP TTS")
        if self.style not in {"openai", "gradium"}:
            raise ConfigError("HTTP_TTS_STYLE must be openai or gradium")
        parsed = urlparse(self.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("HTTP_TTS_URL must be an http(s) URL")
        if self.results_url:
            parsed_results = urlparse(self.results_url)
            if parsed_results.scheme not in {"http", "https"} or not parsed_results.hostname:
                raise ConfigError("HTTP_TTS_RESULTS_URL must be an http(s) URL")


@dataclass(frozen=True, slots=True)
class EtsConfig:
    api_url: str = "https://api.ets100.com"
    pid: str = "grlx"
    sn: str = "t"
    system: str = "4"
    version: str = "3"
    client_version: str = "5.4.5"
    secret_key: str = ""
    device_code: str = ""
    phone: str = ""
    password: str = ""
    timeout: float = 30.0
    disable_ssl_verify: bool = True
    proxy: str = ""

    @classmethod
    def from_env(cls) -> "EtsConfig":
        return cls(
            api_url=_env("ETS_API_URL", "https://api.ets100.com").rstrip("/"),
            pid=_env("ETS_PID", "grlx"),
            sn=_env("ETS_SN", "t"),
            system=_env("ETS_SYSTEM", "4"),
            version=_env("ETS_VERSION", "3"),
            client_version=_env("ETS_CLIENT_VERSION", "5.4.5"),
            secret_key=_env("ETS_SECRET_KEY"),
            device_code=_env("ETS_DEVICE_CODE"),
            phone=_env("ETS_PHONE"),
            password=_env("ETS_PASS"),
            timeout=float(_env("ETS_TIMEOUT", "30")),
            disable_ssl_verify=_env("ETS_DISABLE_SSL_VERIFY", "1").lower()
            not in {"", "0", "false", "no"},
            proxy=_env("HTTPS_PROXY", ""),
        )

    def validate(self) -> None:
        if not self.api_url:
            raise ConfigError("ETS_API_URL is required")
        if not self.secret_key:
            raise ConfigError("ETS_SECRET_KEY is required for real ETS calls")
        parsed = urlparse(self.api_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ConfigError("ETS_API_URL must be an http(s) URL")
