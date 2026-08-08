import base64
import hashlib
import io
import json
import zipfile

import pytest

from ets_demo.config import EtsConfig
from ets_demo.ets import (
    EtsClient,
    EtsError,
    EtsSession,
    decode_password,
    encode_password,
    parse_homework_struct,
)


SECRET = "test-secret"


def env(body, code=0, msg="ok"):
    encoded = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
    return [{"code": code, "msg": msg, "body": encoded}]


def make_config(**overrides):
    data = {
        "api_url": "https://api.ets100.com",
        "pid": "grlx",
        "sn": "t",
        "system": "4",
        "version": "3",
        "client_version": "5.4.5",
        "secret_key": SECRET,
        "timeout": 30,
    }
    data.update(overrides)
    allowed = {
        "api_url", "pid", "sn", "system", "version", "client_version",
        "secret_key", "timeout", "phone", "password", "device_code",
    }
    return EtsConfig(**{k: v for k, v in data.items() if k in allowed})


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, path, payload):
        self.calls.append({"path": path, "payload": payload})
        return self.responses.pop(0)


def _decode_payload(payload):
    raw = base64.b64decode(payload["body"])
    return json.loads(raw)[0]


def test_build_signed_payload_uses_md5():
    transport = FakeTransport([env({"data": []}) for _ in range(3)])
    config = make_config()
    client = EtsClient(config, transport=transport, clock=lambda: 123)
    client.get_homework_list("")
    call = transport.calls[0]
    head = call["payload"]["head"]
    inner = _decode_payload(call["payload"])
    expected = hashlib.md5(
        f"{config.pid}123{call['payload']['body']}{SECRET}".encode(), usedforsecurity=False
    ).hexdigest()
    assert head["sign"] == expected
    assert head["pid"] == config.pid
    assert inner["r"] == "g/homework/list"


def test_login_passes_expected_params():
    transport = FakeTransport([env({"token": "abc"})])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    result = client.login("13800000000", "password")
    assert result == {"token": "abc"}
    call = transport.calls[0]
    params = _decode_payload(call["payload"])["params"]
    assert call["path"] == "/user/login"
    assert params["phone"] == "13800000000"
    assert params["device_name"] == "DESKTOP"
    assert params["sign_response"] == 1


def test_login_rebinds_on_30014():
    transport = FakeTransport(
        [env({"msg": "need rebind"}, code=30014), env({"token": "rebound"})]
    )
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.login("13800000000", "password")
    assert client.token == "rebound"
    assert transport.calls[1]["path"] == "/user/rebind-code"


def test_login_30008_sends_sms_and_uses_code_provider():
    transport = FakeTransport(
        [
            env({"msg": "new device"}, code=30008),  # login
            env({}),                                 # send sms
            env({"token": "bound"}),                 # rebind with code
        ]
    )
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    seen = []
    client.login("13800000000", "password", code_provider=lambda: seen.append("code") or "1234")
    assert client.token == "bound"
    assert seen == ["code"]
    assert transport.calls[1]["path"] == "/code/send-user-exist"
    params = _decode_payload(transport.calls[2]["payload"])["params"]
    assert params["code"] == "1234"


def test_login_30008_without_code_provider_uses_zero():
    transport = FakeTransport(
        [env({"msg": "new device"}, code=30008), env({"token": "bound"})]
    )
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.login("13800000000", "password")
    assert client.token == "bound"
    params = _decode_payload(transport.calls[1]["payload"])["params"]
    assert params["code"] == "0"


def test_auto_relogin_on_token_expiry():
    transport = FakeTransport(
        [
            env({"msg": "expired"}, code=20004),  # status 0: token expired
            env({"token": "second"}),             # relogin via saved account
            env({"data": []}),                    # status 0 retry
            env({"data": []}),                    # status 2
            env({"data": []}),                    # status 3
        ]
    )
    client = EtsClient(
        make_config(phone="13800000000", password="secret"),
        transport=transport,
        clock=lambda: 2,
    )
    client.token = "stale"
    items = client.get_homework_list("")
    assert items == []
    assert client.token == "second"


def test_homework_list_merges_statuses():
    def page(*ids):
        return env({"data": [{"id": str(x)} for x in ids]})

    transport = FakeTransport([page(1, 2), page(2, 3), page(1, 4)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    items = client.get_homework_list("")
    assert [item["id"] for item in items] == ["1", "2", "3", "4"]
    statuses = [_decode_payload(c["payload"])["params"]["status"] for c in transport.calls]
    assert statuses == ["0", "2", "3"]


def _zip_with_footer(content: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
    data = buffer.getvalue()
    seed = b"S" * 128
    mid = b"\x00" * (144 - 16 - 128)
    tail = b"\x00" * (336 - 8 - 8 - 128 - 5)
    footer = b"MSTCHINA" + b"\x00" * 8 + seed + mid + b"EPLAT" + b"\x00" * 0 + tail
    assert len(footer) == 336
    return data + footer


def test_parse_homework_struct_extracts_lvread_and_zip():
    struct = {
        "total_point": 30,
        "lvread_question": [{"entity_id": "r1", "text": "<p>Hello world.</p>", "order": 0}],
        "contents": [{"real_id": "ct1", "order": 1, "url": "https://cdn.example.com/x.zip"}],
    }

    def downloader(url):
        return _zip_with_footer(
            {
                "structure_type": "collector.choose",
                "info": {
                    "xtlist": [
                        {
                            "answer": "B",
                            "xxlist": [
                                {"xx_xh": "A", "xx_nr": "apple"},
                                {"xx_xh": "B", "xx_nr": "orange"},
                            ],
                        }
                    ]
                },
            }
        )

    questions = parse_homework_struct(struct, downloader=downloader)
    assert questions[0]["text"] == "Hello world."
    assert questions[-1]["category"] == "choose"
    assert questions[-1]["group_name"] == "题型1"


def test_get_parent_id_parses_indexed_ecard_body():
    body = {
        "0": {"parent_id": "66", "status": "0", "out_of_date": "0", "mobile_status": "1"},
        "1": {"parent_id": "77", "status": "0", "out_of_date": "0", "mobile_status": "1"},
    }
    transport = FakeTransport([env(body)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.token = "tok"
    assert client.get_parent_id() == "66"
    assert transport.calls[0]["path"] == "/m/ecard/list"


def test_get_parent_id_falls_back_to_any_parent_id():
    body = {"0": {"parent_id": "9", "status": "1", "out_of_date": "0", "mobile_status": "0"}}
    transport = FakeTransport([env(body)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.token = "tok"
    assert client.get_parent_id() == "9"


def test_get_parent_id_uses_class_id_when_no_parent_id():
    body = {"0": {"class_id": "88", "status": "0", "out_of_date": "0", "mobile_status": "1"}}
    transport = FakeTransport([env(body)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.token = "tok"
    assert client.get_parent_id() == "88"


def test_get_parent_id_returns_empty_on_80004():
    transport = FakeTransport([env({"msg": "没有找到有效班级"}, code=80004)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.token = "tok"
    assert client.get_parent_id() == ""


def test_get_parent_id_raises_on_other_errors():
    transport = FakeTransport([env({"msg": "boom"}, code=9999)])
    client = EtsClient(make_config(), transport=transport, clock=lambda: 1)
    client.token = "tok"
    with pytest.raises(EtsError):
        client.get_parent_id()


def test_session_roundtrip(tmp_path):
    path = tmp_path / "session.json"
    session = EtsSession(phone="13800000000", token="tok", parent_id="pid")
    session.save(path)
    loaded = EtsSession.load(path)
    assert loaded.phone == "13800000000"
    assert loaded.token == "tok"
    assert loaded.parent_id == "pid"


def test_password_encode_roundtrip():
    assert decode_password(encode_password("s3cret")) == "s3cret"
    assert decode_password("not-valid") == ""