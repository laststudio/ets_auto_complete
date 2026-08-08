from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .models import Homework


@dataclass(slots=True)
class MockState:
    homeworks: list[Homework]
    pid: str = "local-demo"
    secret: str = "local-demo"
    events: list[dict[str, Any]] = field(default_factory=list)
    next_file_id: int = 1


def _decode_request(payload: dict[str, Any], state: MockState) -> tuple[str, dict[str, Any]]:
    head = payload.get("head", {})
    body = str(payload.get("body", ""))
    timestamp = int(head.get("time", 0))
    expected = hashlib.md5(
        f"{state.pid}{timestamp}{body}{state.secret}".encode(), usedforsecurity=False
    ).hexdigest()
    if head.get("pid") != state.pid or head.get("sign") != expected:
        raise ValueError("invalid local signature")
    decoded = json.loads(base64.b64decode(body, validate=True))
    request = decoded[0]
    return str(request.get("r", "")), dict(request.get("params", {}))


def _response(body: Any, code: int = 0, msg: str = "ok") -> bytes:
    encoded = base64.b64encode(json.dumps(body, ensure_ascii=False).encode()).decode()
    return json.dumps([{"code": code, "msg": msg, "body": encoded}], ensure_ascii=False).encode()


def make_handler(state: MockState):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length))
                route, params = _decode_request(payload, state)
                if route != self.path.lstrip("/"):
                    raise ValueError("route and path do not match")
                body = self._dispatch(route, params)
                response = _response(body)
                self.send_response(200)
            except Exception as exc:
                response = _response({}, 400, str(exc))
                self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def _dispatch(self, route: str, params: dict[str, Any]) -> Any:
            state.events.append({"time": time.time(), "route": route, "params": params})
            if route == "g/homework/list":
                return {"data": [item.to_dict() for item in state.homeworks]}
            if route == "g/homework/struct":
                homework_id = str(params.get("id", ""))
                item = next((item for item in state.homeworks if item.id == homework_id), None)
                return item.to_dict() if item else {}
            if route == "g/homework/checkstart":
                return {"resource_id": "local-resource", "begin": int(time.time())}
            if route == "g/set/ppr-res":
                return {
                    "batch": [
                        {**item, "res_id": f"local-{item.get('entity_id', '')}", "engine_type": "local"}
                        for item in params.get("batch", [])
                    ]
                }
            if route == "m/file/pre-upload":
                file_id = state.next_file_id
                state.next_file_id += 1
                return {"id": str(file_id)}
            if route == "system/oss-config":
                return {"mode": "local", "dir": params.get("dir", ""), "name": params.get("name", "")}
            if route == "m/audio/sync-v2":
                return {"point": params.get("score", "0"), "total_complete": 100, "accepted": True}
            if route == "m/homework/set-use-time":
                return {"accepted": True, "use_time": params.get("use_time", 0)}
            if route == "g/homework/report-score":
                return {"accepted": True, "score_rate": params.get("homework_score_rate", 0)}
            if route == "g/homework/score-drop":
                return {"accepted": True}
            if route == "g/homework/pk-info":
                return {"serial_id": "local-pk-serial"}
            if route == "g/pk/start-word-pk":
                return {"serial_id": "local-pk-serial", "accepted": True}
            if route == "g/pk/submit-word":
                return {"accepted": True, "submitted": len(params.get("score_detail", []))}
            if route == "g/homework/score-sync":
                return {"accepted": True, "submitted": len(params.get("detail", []))}
            if route == "m/exam/sync":
                detail = params.get("detail", [])
                if isinstance(detail, str):
                    detail = json.loads(detail)
                return {"accepted": True, "submitted": len(detail)}
            if route == "m/exam/submit":
                return {"accepted": True, "exam_id": params.get("exam_id", "")}
            if route == "m/composition/save-draft":
                return {"accepted": True, "drafts": len(params.get("detail", []))}
            if route == "m/composition/mark":
                return {"accepted": True, "composition_id": params.get("composition_id", "")}
            if route == "m/homework/submit":
                return {"accepted": True, "homework_id": params.get("homework_id", "")}
            return {"accepted": True, "route": route}

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def serve(homeworks: list[Homework], host: str, port: int, pid: str, secret: str) -> None:
    state = MockState(homeworks=homeworks, pid=pid, secret=secret)
    server = ThreadingHTTPServer((host, port), make_handler(state))
    try:
        server.serve_forever()
    finally:
        server.server_close()
