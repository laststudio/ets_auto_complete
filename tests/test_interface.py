import base64
import hashlib
import json

from ets_demo.config import InterfaceConfig
from ets_demo.interface import InterfaceClient, build_signed_payload, decode_response_body


def test_signed_payload_matches_preserved_protocol() -> None:
    payload = build_signed_payload(
        "g/homework/list",
        {"token": "local-session"},
        pid="local-demo",
        secret_key="secret",
        timestamp=123,
    )
    decoded = json.loads(base64.b64decode(payload["body"]))
    assert decoded == [{"r": "g/homework/list", "params": {"token": "local-session"}}]
    expected = hashlib.md5(
        f"local-demo123{payload['body']}secret".encode(), usedforsecurity=False
    ).hexdigest()
    assert payload["head"]["sign"] == expected


def test_decode_base64_response_body() -> None:
    body = base64.b64encode(json.dumps({"accepted": True}).encode()).decode()
    assert decode_response_body([{"code": 0, "body": body}])["body"] == {"accepted": True}


def test_homework_list_merges_local_interface_results() -> None:
    requests = 0

    def handler(path: str, payload: dict) -> list[dict]:
        nonlocal requests
        requests += 1
        assert path == "/g/homework/list"
        assert payload["head"]["pid"] == "local-demo"
        items = [{"id": "shared"}, {"id": f"item-{requests}"}]
        encoded = base64.b64encode(json.dumps({"data": items}).encode()).decode()
        return [{"code": 0, "body": encoded}]

    config = InterfaceConfig(
        api_base_url="http://127.0.0.1:8765", pid="local-demo", secret_key="secret"
    )
    with InterfaceClient(
        config, transport=handler, clock=lambda: 1
    ) as client:
        result = client.homework_list("local-session")
    assert requests == 3
    assert [item["id"] for item in result] == ["shared", "item-1", "item-2", "item-3"]
