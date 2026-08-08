from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import ssl
import uuid
from email.utils import formatdate
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .errors import EtsError


def _ssl_context() -> Any:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


def _multipart_body(
    fields: list[tuple[str, str]],
    files: list[tuple[str, str, bytes, str]],
    boundary: str,
) -> bytes:
    lines: list[bytes] = []
    for name, value in fields:
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        lines.append(value.encode("utf-8"))
        lines.append(b"\r\n")
    for name, filename, content, content_type in files:
        lines.append(f"--{boundary}\r\n".encode())
        lines.append(
            f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'.encode()
        )
        lines.append(f"Content-Type: {content_type}\r\n\r\n".encode())
        lines.append(content)
        lines.append(b"\r\n")
    lines.append(f"--{boundary}--\r\n".encode())
    return b"".join(lines)


def upload_with_form(
    *,
    host: str,
    dir_name: str,
    file_name: str,
    access_id: str,
    policy: str,
    signature: str,
    callback: str,
    content: bytes,
    timeout: float = 30.0,
) -> str:
    """POST multipart/form-data using the ETS /system/oss-config response."""
    boundary = "------------------------" + uuid.uuid4().hex
    body = _multipart_body(
        [
            ("name", file_name),
            ("key", dir_name + file_name),
            ("OSSAccessKeyId", access_id),
            ("policy", policy),
            ("signature", signature),
            ("callback", callback),
        ],
        [("file", file_name, content, "application/octet-stream")],
        boundary,
    )
    request = Request(
        host,
        data=body,
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Content-Length": str(len(body)),
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            if response.status not in (200, 204):
                raise EtsError(f"OSS form upload failed: HTTP {response.status}")
    except (HTTPError, URLError, OSError) as exc:
        raise EtsError(f"OSS form upload failed: {exc}") from exc
    return host.rstrip("/") + "/" + dir_name + file_name


def _oss_string_to_sign(method: str, content_type: str, date: str, token: str, key: str) -> str:
    return (
        f"{method}\n"
        "\n"
        f"{content_type}\n"
        f"{date}\n"
        f"x-oss-security-token:{token}\n"
        f"/{key}"
    )


def upload_with_sts(
    *,
    access_key_id: str,
    access_key_secret: str,
    security_token: str,
    bucket: str,
    endpoint: str,
    object_key: str,
    content: bytes,
    content_type: str = "application/octet-stream",
    timeout: float = 30.0,
) -> str:
    """PUT an object to Aliyun OSS using temporary STS credentials."""
    date = formatdate(usegmt=True)
    string_to_sign = _oss_string_to_sign(
        "PUT", content_type, date, security_token, bucket + "/" + object_key
    )
    signature = base64.b64encode(
        hmac.new(access_key_secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    url = f"https://{bucket}.{endpoint}/{quote(object_key)}"
    request = Request(
        url,
        data=content,
        headers={
            "Host": f"{bucket}.{endpoint}",
            "Date": date,
            "Content-Type": content_type,
            "Authorization": f"OSS {access_key_id}:{signature}",
            "x-oss-security-token": security_token,
            "Content-Length": str(len(content)),
        },
        method="PUT",
    )
    try:
        with urlopen(request, timeout=timeout, context=_ssl_context()) as response:
            if response.status != 200:
                raise EtsError(f"OSS STS upload failed: HTTP {response.status}")
    except (HTTPError, URLError, OSError) as exc:
        raise EtsError(f"OSS STS upload failed: {exc}") from exc
    return object_key


class OssUploader:
    """Upload audio/XML files, preferring the ETS form config, falling back to STS PUT."""

    def __init__(self, client: Any, *, cdn_url: str = "https://cdn.subject.ets100.com") -> None:
        self._client = client
        self._cdn_url = cdn_url

    def _form_upload(self, file_path: Path, kind: str, tag: str) -> str:
        config = self._client.oss_config({"dir": kind, "tag": tag, "name": file_path.name})
        if not config.get("accessid"):
            raise EtsError(f"OSS config for {kind} returned no accessid")
        return upload_with_form(
            host=str(config.get("host", "")),
            dir_name=str(config.get("dir", "")),
            file_name=file_path.name,
            access_id=str(config.get("accessid", "")),
            policy=str(config.get("policy", "")),
            signature=str(config.get("signature", "")),
            callback=str(config.get("callback", "")),
            content=file_path.read_bytes(),
            timeout=float(self._client.config.timeout),
        )

    def upload(self, file_path: Path, kind: str, tag: str) -> str:
        if not file_path.is_file():
            raise EtsError(f"Upload source missing: {file_path}")
        try:
            return self._form_upload(file_path, kind, tag)
        except EtsError:
            sts = self._client.call(
                "/p1/student/config/config/ossEvaluateUploadStsToken",
                "p1/student/config/config/ossEvaluateUploadStsToken",
                self._client.common(),
            )
            access_key_id = str(sts.get("accessKeyId") or sts.get("access_key_id") or "")
            access_key_secret = str(sts.get("accessKeySecret") or sts.get("access_key_secret") or "")
            security_token = str(sts.get("securityToken") or sts.get("security_token") or "")
            if not (access_key_id and access_key_secret and security_token):
                raise EtsError("OSS STS config is incomplete")
            bucket = str(sts.get("bucket") or "ets60")
            endpoint = str(sts.get("endpoint") or "oss-cn-hangzhou.aliyuncs.com")
            prefix = "ugc/evaluate/mp3/middleApp/pc/" if kind == "mark_audio" else "ugc/evaluate/xml/middleApp/pc/"
            object_key = prefix + file_path.name
            upload_with_sts(
                access_key_id=access_key_id,
                access_key_secret=access_key_secret,
                security_token=security_token,
                bucket=bucket,
                endpoint=endpoint,
                object_key=object_key,
                content=file_path.read_bytes(),
            )
            return self._cdn_url.rstrip("/") + "/" + object_key
