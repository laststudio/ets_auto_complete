import asyncio
import io
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ets_demo.audio import synthesize_clone
from ets_demo.config import CloneTtsConfig


def test_clone_tts_direct_audio_response(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\x00\x00" * 16000)
    wav_bytes = buffer.getvalue()
    received = {"authorization": "", "content_type": ""}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received["authorization"] = self.headers.get("Authorization", "")
            received["content_type"] = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
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
    reference = tmp_path / "reference.wav"
    reference.write_bytes(wav_bytes)
    output = tmp_path / "clone.mp3"
    try:
        asyncio.run(
            synthesize_clone(
                "Hello",
                reference,
                output,
                CloneTtsConfig(
                    submit_url=f"http://{host}:{port}/clone",
                    results_url=f"http://{host}:{port}/results",
                    api_key="test-key",
                ),
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert received["authorization"] == "Bearer test-key"
    assert "multipart/form-data" in received["content_type"]
    assert output.is_file() and output.stat().st_size > 0
