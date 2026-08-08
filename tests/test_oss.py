import base64
import hashlib
import hmac

from ets_demo.oss import (
    _multipart_body,
    _oss_string_to_sign,
    upload_with_sts,
)


def test_multipart_body_contains_fields():
    boundary = "BOUNDARY"
    body = _multipart_body(
        [("name", "a.mp3"), ("key", "dir/a.mp3")],
        [("file", "a.mp3", b"audio", "application/octet-stream")],
        boundary,
    )
    text = body.decode()
    assert f"--{boundary}\r\n" in text
    assert 'name="name"' in text
    assert 'name="file"; filename="a.mp3"' in text
    assert b"audio" in body
    assert text.endswith(f"--{boundary}--\r\n")


def test_oss_string_to_signature_format():
    date = "Fri, 01 Jan 2026 00:00:00 GMT"
    sign = _oss_string_to_sign(
        "PUT", "application/octet-stream", date, "tok", "ets60/ugc/a.mp3"
    )
    lines = sign.split("\n")
    assert lines[0] == "PUT"
    assert lines[1] == ""
    assert lines[2] == "application/octet-stream"
    assert lines[3] == date
    assert lines[4] == "x-oss-security-token:tok"
    assert lines[5] == "/ets60/ugc/a.mp3"


def test_upload_with_sts_builds_signed_request(monkeypatch):
    captured = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout=None, context=None):
        captured["headers"] = dict(request.header_items())
        captured["data"] = request.data
        return FakeResponse()

    monkeypatch.setattr("ets_demo.oss.urlopen", fake_urlopen)

    uploaded = upload_with_sts(
        access_key_id="AK",
        access_key_secret="SK_SECRET",
        security_token="TOK",
        bucket="ets60",
        endpoint="oss-cn-hangzhou.aliyuncs.com",
        object_key="ugc/e2e/a.mp3",
        content=b"audio",
    )
    assert uploaded == "ugc/e2e/a.mp3"
    headers_lower = {k.lower(): v for k, v in captured["headers"].items()}
    date = headers_lower["date"]
    string_to_sign = (
        "PUT\n\napplication/octet-stream\n"
        f"{date}\n"
        "x-oss-security-token:TOK\n"
        "/ets60/ugc/e2e/a.mp3"
    )
    expected = base64.b64encode(
        hmac.new(b"SK_SECRET", string_to_sign.encode(), hashlib.sha1).digest()
    ).decode()
    assert headers_lower.get("authorization") == f"OSS AK:{expected}"
    assert headers_lower.get("x-oss-security-token") == "TOK"
    assert captured["data"] == b"audio"