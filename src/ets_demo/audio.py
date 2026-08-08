from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin

import aiohttp

from .config import CloneTtsConfig, HttpTtsConfig
from .errors import AudioError


async def synthesize_edge(
    text: str,
    output: Path,
    *,
    voice: str = "en-US-JennyNeural",
    rate: str = "-10%",
    pitch: str = "+0Hz",
) -> Path:
    if not text.strip():
        raise AudioError("TTS text is empty")
    try:
        import edge_tts
    except ImportError as exc:
        raise AudioError("edge-tts is not installed; run pip install -e .") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        communicate = edge_tts.Communicate(text, voice=voice, rate=rate, pitch=pitch)
        await communicate.save(str(output))
    except Exception as exc:
        raise AudioError(f"Edge TTS failed: {exc}") from exc
    if not output.is_file() or output.stat().st_size == 0:
        raise AudioError("Edge TTS produced no audio")
    return output


def _clone_speed(text: str) -> float:
    words = len(text.split())
    if words > 80:
        return 1.3
    if words > 30:
        return 1.5
    return 2.0


async def synthesize_clone(
    text: str,
    reference_audio: Path,
    output: Path,
    config: CloneTtsConfig,
    *,
    model: str = "chatterbox",
    speed: float | None = None,
) -> Path:
    config.validate()
    if not text.strip():
        raise AudioError("TTS text is empty")
    if not reference_audio.is_file():
        raise AudioError(f"Clone reference audio not found: {reference_audio}")
    headers = {"Authorization": f"Bearer {config.api_key}"}
    try:
        timeout = aiohttp.ClientTimeout(total=config.timeout)
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as client:
            with reference_audio.open("rb") as source:
                form = aiohttp.FormData()
                form.add_field(
                    "reference_audio",
                    source,
                    filename=reference_audio.name,
                    content_type="application/octet-stream",
                )
                form.add_field("text", text)
                form.add_field("model", model)
                form.add_field("format", "mp3")
                form.add_field("speed", str(speed if speed is not None else _clone_speed(text)))
                response = await client.post(
                    config.submit_url,
                    data=form,
                    allow_redirects=True,
                )
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if content_type.startswith("audio/"):
                    audio = await response.read()
                else:
                    result = await response.json()
                    uuid = result.get("uuid", "") if isinstance(result, dict) else ""
                    if not uuid:
                        raise AudioError("Clone TTS response has no audio or uuid")
                    deadline = time.monotonic() + config.max_wait
                    audio = b""
                    while time.monotonic() < deadline:
                        await asyncio.sleep(config.poll_interval)
                        async with client.get(config.results_url, params={"uuid": uuid}) as poll:
                            poll.raise_for_status()
                            poll_type = poll.headers.get("content-type", "").lower()
                            if poll_type.startswith("audio/") or "octet-stream" in poll_type:
                                audio = await poll.read()
                                break
                            status = await poll.json()
                        if not isinstance(status, dict):
                            continue
                        if status.get("status") == "failed":
                            raise AudioError(f"Clone TTS failed: {status.get('error', 'unknown error')}")
                        if status.get("status") == "completed" and status.get("result_url"):
                            download_url = urljoin(config.results_url, str(status["result_url"]))
                            async with client.get(download_url) as downloaded:
                                downloaded.raise_for_status()
                                audio = await downloaded.read()
                            break
                    if not audio:
                        raise AudioError("Clone TTS polling timed out")
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
        raise AudioError(f"Clone TTS request failed: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(audio)
    normalize_mp3(output)
    return output


def parse_sse_audio(payload: str) -> bytes:
    audio = bytearray()
    for line in payload.splitlines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("error"):
            raise AudioError(f"HTTP TTS stream failed: {event['error']}")
        chunk = event.get("chunk", "") if isinstance(event, dict) else ""
        if chunk:
            try:
                audio.extend(base64.b64decode(chunk, validate=True))
            except ValueError as exc:
                raise AudioError("HTTP TTS stream contains invalid base64 audio") from exc
    if not audio:
        raise AudioError("HTTP TTS stream returned no audio")
    return bytes(audio)


async def synthesize_http(
    text: str,
    output: Path,
    config: HttpTtsConfig,
    *,
    voice: str | None = None,
    speed: float | None = None,
) -> Path:
    config.validate()
    if not text.strip():
        raise AudioError("TTS text is empty")
    headers: dict[str, str] = {}
    if config.api_key:
        if config.style == "gradium":
            headers["x-api-key"] = config.api_key
        else:
            headers["Authorization"] = f"Bearer {config.api_key}"
    if config.style == "gradium":
        payload = {
            "text": text,
            "voice_id": voice or config.voice,
            "output_format": "wav",
            "only_audio": True,
            "json_config": json.dumps({"padding_bonus": 1.0, "temp": 0.0}),
        }
    else:
        payload = {
            "model": config.model,
            "input": text,
            "voice": voice or config.voice,
            "response_format": "mp3",
            "speed": speed if speed is not None else config.speed,
        }

    timeout = aiohttp.ClientTimeout(total=config.timeout)
    try:
        async with aiohttp.ClientSession(timeout=timeout, headers=headers) as client:
            async with client.post(config.url, json=payload) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" in content_type:
                    audio = parse_sse_audio(await response.text())
                elif content_type.startswith("audio/") or "octet-stream" in content_type:
                    audio = await response.read()
                else:
                    result = await response.json()
                    if not isinstance(result, dict):
                        raise AudioError("HTTP TTS JSON response is not an object")
                    if result.get("error"):
                        raise AudioError(f"HTTP TTS failed: {result['error']}")
                    direct = result.get("audio") or result.get("audio_base64")
                    if direct:
                        audio = base64.b64decode(str(direct), validate=True)
                    else:
                        uuid = str(result.get("uuid", ""))
                        if not uuid or not config.results_url:
                            raise AudioError("HTTP TTS response has no audio, uuid, or results URL")
                        audio = await _poll_http_tts(client, config, uuid)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, json.JSONDecodeError) as exc:
        raise AudioError(f"HTTP TTS request failed: {exc}") from exc

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="http_tts_") as temp_dir:
        source = Path(temp_dir) / "source.audio"
        source.write_bytes(audio)
        convert_to_mp3(source, output)
    return output


async def _poll_http_tts(
    client: aiohttp.ClientSession, config: HttpTtsConfig, uuid: str
) -> bytes:
    deadline = time.monotonic() + config.max_wait
    while time.monotonic() < deadline:
        await asyncio.sleep(config.poll_interval)
        async with client.get(config.results_url, params={"uuid": uuid}) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").lower()
            if content_type.startswith("audio/") or "octet-stream" in content_type:
                return await response.read()
            status = await response.json()
        if not isinstance(status, dict):
            continue
        if status.get("status") == "failed":
            raise AudioError(f"HTTP TTS failed: {status.get('error', 'unknown error')}")
        if status.get("status") == "completed" and status.get("result_url"):
            url = urljoin(config.results_url, str(status["result_url"]))
            async with client.get(url) as download:
                download.raise_for_status()
                return await download.read()
    raise AudioError("HTTP TTS polling timed out")


def convert_to_mp3(source: Path, output: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("ffmpeg is required but was not found")
    command = [
        ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(source),
        "-ar", "24000", "-ac", "1", "-b:a", "128k", str(output),
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, timeout=60)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        detail = getattr(exc, "stderr", b"")
        message = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
        raise AudioError(f"HTTP TTS audio conversion failed: {message.strip()}") from exc


def normalize_mp3(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("ffmpeg is required but was not found")
    with tempfile.TemporaryDirectory(prefix="clone_norm_") as temp_dir:
        normalized = Path(temp_dir) / "normalized.mp3"
        command = [
            ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path),
            "-af", "loudnorm=I=-16:LRA=11:TP=-1.5", "-ar", "24000", "-ac", "1",
            "-b:a", "128k", str(normalized),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
            fallback = [
                ffmpeg, "-nostdin", "-v", "error", "-y", "-i", str(path),
                "-ar", "24000", "-ac", "1", "-b:a", "128k", str(normalized),
            ]
            try:
                subprocess.run(fallback, check=True, capture_output=True, timeout=60)
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                detail = getattr(exc, "stderr", b"")
                message = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
                raise AudioError(f"Clone audio conversion failed: {message.strip()}") from exc
        path.write_bytes(normalized.read_bytes())


def to_pcm16k(audio_path: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("ffmpeg is required but was not found")

    with tempfile.TemporaryDirectory(prefix="ets_audio_") as temp_dir:
        pcm_path = Path(temp_dir) / "audio.pcm"
        command = [
            ffmpeg,
            "-nostdin",
            "-v",
            "error",
            "-y",
            "-i",
            str(audio_path),
            "-ar",
            "16000",
            "-ac",
            "1",
            "-f",
            "s16le",
            str(pcm_path),
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, timeout=60)
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            detail = getattr(exc, "stderr", b"")
            message = detail.decode(errors="replace") if isinstance(detail, bytes) else str(detail)
            raise AudioError(f"ffmpeg conversion failed: {message.strip()}") from exc
        return pcm_path.read_bytes()


def prepare_ise_pcm(pcm: bytes, category: str) -> bytes:
    bytes_per_second = 16000 * 2
    pcm = (b"\x00" * (bytes_per_second // 2)) + pcm
    minimum = bytes_per_second * 5
    maximum = bytes_per_second * (30 if category == "read_chapter" else 15)
    if len(pcm) < minimum:
        pcm += b"\x00" * (minimum - len(pcm))
    return pcm[:maximum]


def run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    raise AudioError("run_async cannot be called from an active event loop")
