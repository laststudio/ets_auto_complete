import asyncio
import base64
import io
import json
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from ets_demo.audio import parse_sse_audio, synthesize_http
from ets_demo.config import HttpTtsConfig
from ets_demo.errors import AudioError


def test_parse_sse_audio_chunks() -> None:
    first = base64.b64encode(b"abc").decode()
    second = base64.b64encode(b"def").decode()
    payload = "\n".join(
        [
            f'data: {json.dumps({"chunk": first})}',
            f'data: {json.dumps({"chunk": second})}',
            "data: [DONE]",
        ]
    )
    assert parse_sse_audio(payload) == b"abcdef"


def test_parse_sse_audio_error() -> None:
    with pytest.raises(AudioError, match="stream failed"):
        parse_sse_audio('data: {"error":"provider error"}')


def test_http_tts_direct_audio_conversion(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    wav_bytes = buffer.getvalue()
    received: dict = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received.update(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav_bytes)))
            self.end_headers()
            self.wfile.write(wav_bytes)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    output = tmp_path / "http.mp3"
    try:
        asyncio.run(
            synthesize_http(
                "Hello",
                output,
                HttpTtsConfig(url=f"http://{host}:{port}/tts", style="openai"),
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received["input"] == "Hello"
    assert received["model"] == "kokoro"
    assert output.is_file() and output.stat().st_size > 0
