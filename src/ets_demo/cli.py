from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .audio import synthesize_edge, synthesize_http
from .config import (
    CloneTtsConfig,
    DEFAULT_ETS_SECRET_KEY,
    EtsConfig,
    HttpTtsConfig,
    InterfaceConfig,
    IseConfig,
    save_local,
)
from .errors import DemoError, EtsError
from .ets import DEFAULT_SESSION_FILE, EtsClient, EtsSession, classify_homework, parse_homework_struct
from .homework import extract_questions, read_content_json
from .interface import InterfaceClient
from .ise import IseEvaluator
from .mock_server import serve
from .models import Homework, Question, load_homework
from .oss import OssUploader
from .scoring import ScoreProfile, generate_score_xml
from .workflow import StudyWorkflow, WorkflowOptions


def _json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _score_profile(args: argparse.Namespace) -> ScoreProfile:
    return ScoreProfile(
        total=args.score,
        accuracy=args.accuracy,
        fluency=args.fluency,
        integrity=args.integrity,
        stress=args.stress,
    ).clamped()


def _load_overrides(path: Path | None) -> dict[int, ScoreProfile]:
    if path is None:
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DemoError(f"Unable to read score overrides: {exc}") from exc
    if not isinstance(raw, dict):
        raise DemoError("score overrides must be a JSON object keyed by question index")
    result: dict[int, ScoreProfile] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            raise DemoError(f"score override {key} must be an object")
        result[int(key)] = ScoreProfile(
            total=float(value.get("total", value.get("ise_score", 5.0))),
            accuracy=float(value.get("accuracy", 4.95)),
            fluency=float(value.get("fluency", 4.95)),
            integrity=float(value.get("integrity", 4.95)),
            stress=float(value.get("stress", 4.90)),
        ).clamped()
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ets-demo",
        description="Local study workflow with preserved mock interface contracts",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor", help="Check modules, tools, and optional configuration")

    inspect = commands.add_parser("inspect", help="Inspect a local homework manifest or resource ZIP")
    inspect.add_argument("source", type=Path)

    parse_zip = commands.add_parser("parse-zip", help="Parse questions from a resource ZIP")
    parse_zip.add_argument("path", type=Path)

    tts = commands.add_parser("tts", help="Synthesize an MP3 with Edge TTS")
    tts.add_argument("text")
    tts.add_argument("--output", type=Path, default=Path("output.mp3"))
    tts.add_argument("--voice", default="en-US-JennyNeural")
    tts.add_argument("--rate", default="-10%")
    tts.add_argument("--pitch", default="+0Hz")

    http_tts = commands.add_parser("http-tts", help="Synthesize with a configured HTTP TTS provider")
    http_tts.add_argument("text")
    http_tts.add_argument("--output", type=Path, default=Path("output.mp3"))
    http_tts.add_argument("--voice")
    http_tts.add_argument("--speed", type=float)

    xml = commands.add_parser("score-xml", help="Generate local score XML")
    xml.add_argument("text")
    xml.add_argument("--output", type=Path, default=Path("score.xml"))
    _add_score_options(xml)
    xml.add_argument("--category", default="read_sentence")
    xml.add_argument("--seed", type=int, default=0)

    ise = commands.add_parser("ise", help="Evaluate an existing audio file with ISE")
    ise.add_argument("audio", type=Path)
    ise.add_argument("text")
    ise.add_argument("--category", default="read_sentence")
    ise.add_argument("--resource-id", default="")
    ise.add_argument("--xml-output", type=Path)

    pipeline = commands.add_parser("pipeline", help="Synthesize once, then evaluate the same audio")
    pipeline.add_argument("text")
    pipeline.add_argument("--audio-output", type=Path, default=Path("output.mp3"))
    pipeline.add_argument("--xml-output", type=Path)
    pipeline.add_argument("--voice", default="en-US-JennyNeural")
    pipeline.add_argument("--rate", default="-10%")
    pipeline.add_argument("--pitch", default="+0Hz")
    pipeline.add_argument("--category", default="read_sentence")
    pipeline.add_argument("--resource-id", default="")

    run = commands.add_parser("run", help="Run the complete local homework workflow")
    run.add_argument("source", type=Path)
    run.add_argument("--output-dir", type=Path, default=Path("output/run"))
    run.add_argument("--voice-mode", choices=("edge", "clone", "http"), default="edge")
    run.add_argument("--evaluator", choices=("local", "ise"), default="local")
    run.add_argument("--voice", default="en-US-JennyNeural")
    run.add_argument("--rate", default="-10%")
    run.add_argument("--pitch", default="+0Hz")
    run.add_argument("--clone-reference", type=Path)
    run.add_argument("--score-overrides", type=Path)
    run.add_argument("--target-total", type=float)
    run.add_argument("--send-interface", action="store_true")
    run.add_argument("--session-id", default="local-session")
    _add_score_options(run)

    mock = commands.add_parser("mock-server", help="Serve preserved interfaces")
    mock.add_argument("source", type=Path, nargs="+")
    mock.add_argument("--host", default="127.0.0.1")
    mock.add_argument("--port", type=int, default=8765)

    interface_list = commands.add_parser("interface-list", help="Call local homework-list interface")
    interface_list.add_argument("--session-id", default="local-session")
    interface_list.add_argument("--parent-id", default="")

    interface_struct = commands.add_parser("interface-struct", help="Call local homework-struct interface")
    interface_struct.add_argument("homework_id")
    interface_struct.add_argument("--session-id", default="local-session")
    interface_struct.add_argument("--resource-id", default="")

    interface_call = commands.add_parser("interface-call", help="Call any preserved local path/route")
    interface_call.add_argument("path", help="HTTP path, for example /g/homework/checkstart")
    interface_call.add_argument("route", help="Envelope route, for example g/homework/checkstart")
    interface_call.add_argument("--params", default="{}", help="JSON object or @path/to/file.json")

    ets_login = commands.add_parser("ets-login", help="Log in to the real ETS platform")
    ets_login.add_argument("phone")
    ets_login.add_argument("password")
    ets_login.add_argument("--token-file", type=Path, help="Session file to save (default ./session.json)")

    ets_setup = commands.add_parser(
        "ets-setup",
        help="Interactive wizard: configure credentials, log in, pick a homework, and complete it",
    )
    ets_setup.add_argument("--token-file", type=Path)

    ets_list = commands.add_parser("ets-list", help="List homework from the real ETS platform")
    ets_list.add_argument("--token-file", type=Path)
    ets_list.add_argument("--parent-id", default="")

    ets_parent = commands.add_parser("ets-parent", help="Resolve the parent_id for a logged-in account")
    ets_parent.add_argument("--token-file", type=Path)

    ets_struct = commands.add_parser("ets-struct", help="Fetch and parse a real ETS homework structure")
    ets_struct.add_argument("homework_id")
    ets_struct.add_argument("--token-file", type=Path)
    ets_struct.add_argument("--parent-id", default="")
    ets_struct.add_argument("--resource-id", default="")

    ets_score = commands.add_parser("ets-score", help="Read a real ETS homework score")
    ets_score.add_argument("homework_id")
    ets_score.add_argument("--token-file", type=Path)
    ets_score.add_argument("--parent-id", default="")
    ets_score.add_argument("--resource-id", default="")

    ets_do = commands.add_parser("ets-do", help="Complete a real ETS homework automatically")
    ets_do.add_argument("homework_id")
    ets_do.add_argument("--token-file", type=Path)
    ets_do.add_argument("--parent-id", default="")
    ets_do.add_argument("--resource-id", default="")
    ets_do.add_argument("--output-dir", type=Path, default=Path("output/ets"))
    ets_do.add_argument("--voice-mode", choices=("edge", "clone", "http"), default="edge")
    ets_do.add_argument("--evaluator", choices=("local", "ise"), default="local")
    ets_do.add_argument("--voice", default="en-US-JennyNeural")
    ets_do.add_argument("--rate", default="-10%")
    ets_do.add_argument("--pitch", default="+0Hz")
    ets_do.add_argument("--clone-reference", type=Path)
    ets_do.add_argument("--score-overrides", type=Path)
    ets_do.add_argument("--target-total", type=float)
    ets_do.add_argument("--no-upload", action="store_true", help="Do not upload files to OSS")
    _add_score_options(ets_do)
    return parser


def _add_score_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--score", type=float, default=5.0)
    parser.add_argument("--accuracy", type=float, default=4.95)
    parser.add_argument("--fluency", type=float, default=4.95)
    parser.add_argument("--integrity", type=float, default=4.95)
    parser.add_argument("--stress", type=float, default=4.90)
    parser.add_argument(
        "--use-time", type=int, default=None,
        help="Override total completion time in seconds (default: per-question recommended total)",
    )


def _ets_login(args: argparse.Namespace) -> int:
    def sms_code() -> str:
        return _prompt("短信验证码（新设备绑定，已发送至手机）", required=True)

    config = EtsConfig.from_env()
    with EtsClient(config) as client:
        client.login(args.phone, args.password, code_provider=sms_code)
        try:
            client.parent_id = client.get_parent_id()
        except EtsError:
            client.parent_id = ""
    session = EtsSession.load(args.token_file)
    session.token = client.token
    session.phone = client.session.phone
    session.password_enc = client.session.password_enc
    session.device_code = client.session.device_code
    session.parent_id = client.parent_id
    session.save(Path(args.token_file) if args.token_file else None)
    _json({"token": client.token, "parent_id": client.parent_id, "phone": client.session.phone})
    return 0


def _build_env_config(cli: EtsClient, overrides: dict[str, str], token_file: Path | None) -> None:
    """Persist interactive choices back into cli.config and the session file."""
    config = cli.config
    for key, value in overrides.items():
        if isinstance(config, EtsConfig):
            object.__setattr__(config, key, value)
    session = EtsSession.load(token_file)
    session.phone = cli.session.phone or session.phone
    session.password_enc = cli.session.password_enc or session.password_enc
    session.token = cli.token or session.token
    session.device_code = cli.session.device_code or session.device_code
    session.parent_id = cli.parent_id or session.parent_id
    session.save(Path(token_file) if token_file else None)


def _prompt(label: str, *, required: bool = True, secret: bool = False, default: str = "") -> str:
    import getpass
    import sys

    suffix = f" [{default}]" if default else ""
    prompt = f"{label}{suffix}: "
    while True:
        try:
            if secret and sys.stdin.isatty():
                value = getpass.getpass(prompt)
            else:
                value = input(prompt).strip()
        except EOFError:
            print()
            raise DemoError("Interactive input aborted")
        value = value or default
        if not value and required:
            print("  不能为空，请重新输入。", file=sys.stderr)
            continue
        return value


def _ets_setup(args: argparse.Namespace) -> int:
    import sys

    print("ETS 平台交互式引导", file=sys.stderr)
    print("将依次询问：API 地址、签名 key、手机号、密码，然后自动登录并列出作业。", file=sys.stderr)

    existing = EtsSession.load(args.token_file)
    print(file=sys.stderr)
    print(f"当前已保存会话: 手机号={existing.phone or '(无)'} token={'有' if existing.token else '无'}", file=sys.stderr)

    print("开始登录。请按提示逐项输入（默认值可用回车直接采用）：", file=sys.stderr)

    api_url = _prompt("ETS_API_URL", default="https://api.ets100.com")
    secret_key = _prompt("ETS_SECRET_KEY", default=DEFAULT_ETS_SECRET_KEY)
    phone = _prompt("手机号", default=existing.phone or "")
    password = _prompt("密码", required=True, secret=True, default="")
    token_file = str(Path(args.token_file).resolve()) if args.token_file else None

    config = EtsConfig.from_env()
    for key, value in (("api_url", api_url), ("secret_key", secret_key), ("phone", phone), ("password", password)):
        object.__setattr__(config, key, value)
    save_local(
        {
            "ETS_API_URL": api_url,
            "ETS_SECRET_KEY": secret_key,
            "ETS_PHONE": phone,
            "ETS_PASS": password,
        }
    )

    print(file=sys.stderr)

    def sms_code() -> str:
        return _prompt("短信验证码（新设备绑定，已发送至手机）", required=True)

    with EtsClient(config) as client:
        client.login(phone, password, code_provider=sms_code)
        try:
            client.parent_id = client.get_parent_id()
        except EtsError:
            client.parent_id = ""
        _build_env_config(client, overrides, args.token_file)
        print(file=sys.stderr)
        print(f"登录成功：{phone}  parent_id={client.parent_id or '(未解析)'}  token={client.token[:12]}...", file=sys.stderr)

        if not client.parent_id:
            print("未找到有效班级，请先在 ETS 官方客户端绑定/加入班级后重试。", file=sys.stderr)
            _json({"items": []})
            return 0

        items = client.get_homework_list(client.parent_id)
        pending = [i for i in items if str(i.get("status", "0")) not in ("1", "2")]
        if not pending:
            print("没有待完成（status=0/4）的作业。", file=sys.stderr)
            _json({"items": items})
            return 0
        print(file=sys.stderr)
        print("待完成作业：", file=sys.stderr)
        for index, item in enumerate(pending):
            print(f"  {index + 1}. {item.get('name', '')}  id={item.get('id', '')}  {classify_homework(item)}", file=sys.stderr)
        choice = _prompt(
            f"选择要自动完成的作业序号 (1-{len(pending)})，或输入 seek 跳过",
            required=True,
        )
        if choice.strip().lower() == "seek":
            print("已跳过自动完成。", file=sys.stderr)
            return 0
        try:
            homework_idx = int(choice) - 1
            homework_item = pending[homework_idx]
        except (ValueError, IndexError):
            raise DemoError(f"无效序号: {choice}")

        homework_id = str(homework_item.get("id", ""))
        resource_id = str(homework_item.get("resource_id", ""))
        print(file=sys.stderr)
        print(f"开始自动完成：{homework_item.get('name', '')} ({homework_id})", file=sys.stderr)
        completed_at = datetime.now().strftime("%Y%m%d%H%M%S")
        crate = subprocess.run(
            [sys.executable, "-m", "ets_demo", "ets-do",
             homework_id,
             "--token-file", token_file or str(DEFAULT_SESSION_FILE),
             "--parent-id", client.parent_id or "",
             "--resource-id", resource_id,
             "--output-dir", f"output/ets/{homework_id}-{completed_at}"],
        )
        if crate.returncode != 0:
            raise DemoError(f"自动完成失败，退出码 {crate.returncode}")
        return 0


def _parse_choice(value: str, upper: int) -> int:
    value = value.strip()
    if not value:
        raise DemoError(f"请输入 1-{upper} 的序号")
    try:
        idx = int(value)
    except ValueError:
        raise DemoError(f"请输入 1-{upper} 的序号")
    if idx < 1 or idx > upper:
        raise DemoError(f"序号必须在 1-{upper} 之间")
    return idx - 1


def _ets_session(args: argparse.Namespace) -> EtsSession:
    session = EtsSession.load(args.token_file)
    if not session.token:
        raise DemoError("No saved session. Run ets-login first or set ETS_PHONE/ETS_PASS.")
    return session


def _ets_parent(args: argparse.Namespace) -> int:
    config = EtsConfig.from_env()
    with EtsClient(config) as client:
        session = _ets_session(args)
        client.token = session.token
        parent_id = client.get_parent_id()
        client.parent_id = parent_id
        session.parent_id = parent_id
        session.save(Path(args.token_file) if args.token_file else None)
        _json({"parent_id": parent_id})
    return 0


def _ets_query(args: argparse.Namespace) -> int:
    config = EtsConfig.from_env()
    with EtsClient(config) as client:
        session = _ets_session(args)
        client.token = session.token
        parent_id = args.parent_id or session.parent_id
        if args.command == "ets-list":
            items = client.get_homework_list(parent_id)
            enriched = []
            for item in items:
                entry = dict(item)
                entry["type"] = classify_homework(item)
                enriched.append(entry)
            _json({"parent_id": parent_id, "count": len(enriched), "items": enriched})
        else:
            resource_id = args.resource_id or ""
            struct = client.get_homework_struct(args.homework_id, resource_id, parent_id=parent_id)
            questions = parse_homework_struct(struct, downloader=client._default_download)
            _json(
                {
                    "homework_id": args.homework_id,
                    "resource_id": resource_id,
                    "struct": struct,
                    "questions": questions,
                }
            )
    return 0


def _ets_score(args: argparse.Namespace) -> int:
    config = EtsConfig.from_env()
    with EtsClient(config) as client:
        session = _ets_session(args)
        client.token = session.token
        parent_id = args.parent_id or session.parent_id
        info = client.get_homework_info(args.homework_id, args.resource_id or "", parent_id)
        detail = {}
        try:
            detail = client.get_score_detail(
                args.homework_id, args.resource_id or "", parent_id
            )
        except DemoError:
            pass
    _json({"homework_info": info, "score_detail": detail})
    return 0


def _doctor() -> int:
    interface = InterfaceConfig.from_env()
    clone = CloneTtsConfig.from_env()
    http_tts = HttpTtsConfig.from_env()
    ise = IseConfig.from_env()
    ets = EtsConfig.from_env()
    checks = {
        "python": sys.version.split()[0],
        "ffmpeg": shutil.which("ffmpeg"),
        "edge_tts": False,
        "aiohttp": False,
        "websockets": False,
        "local_interface": interface.api_base_url or None,
        "clone_tts_configured": all((clone.submit_url, clone.results_url, clone.api_key)),
        "http_tts_configured": bool(http_tts.url),
        "ise_configured": all((ise.app_id, ise.api_key, ise.api_secret)),
        "ets_configured": bool(ets.secret_key) and ets.api_url.startswith("http"),
        "ets_saved_session": bool(EtsSession.load().token),
    }
    for module in ("edge_tts", "aiohttp", "websockets"):
        try:
            __import__(module)
            checks[module] = True
        except ImportError:
            pass
    _json(checks)
    required = checks["ffmpeg"] and checks["edge_tts"] and checks["aiohttp"] and checks["websockets"]
    return 0 if required else 1


def _progress(step: str, percent: int = 0, message: str = "") -> None:
    print(f"[{percent:3d}%] {message}", file=sys.stderr)


def _ets_progress() -> Callable[[str, int, str], None]:
    def inner(step: str, percent: int = 0, message: str = "") -> None:
        print(f"[{percent:3d}%] {message}", file=sys.stderr)

    return inner


async def fetch_real_homework(
    client: EtsClient,
    args: argparse.Namespace,
    parent_id: str,
    progress: Callable[[str, int, str], None],
) -> Homework:
    import asyncio

    loop = asyncio.get_event_loop()
    progress("list", 3, f"[0] loading homework {args.homework_id}")
    items = await loop.run_in_executor(None, client.get_homework_list, parent_id)
    item = next((entry for entry in items if str(entry.get("id", "")) == str(args.homework_id)), None)
    if item is None:
        raise DemoError(f"Homework {args.homework_id} was not found in the list")

    resource_id = args.resource_id or str(item.get("resource_id", ""))
    kind = classify_homework(item)
    name = str(item.get("name", args.homework_id))
    set_id = str(item.get("set_id", args.homework_id))

    progress("struct", message="fetching homework structure")
    metadata: dict = {}
    struct: dict = {}
    if kind in {"pk", "vocab"}:
        struct = await loop.run_in_executor(
            None, client.get_homework_struct, args.homework_id, resource_id, parent_id
        )
        questions = build_word_questions(struct)
        metadata = {"unit_id": 0, "book_id": 0}
    elif kind in {"mock", "normal"}:
        # 听说/模拟/考试作业：题目在列表项 struct/structs.contents 的加密 ZIP 里，
        # g/homework/struct 对已完成/过期作业可能返回空题或报错。
        # 先尝试列表内嵌结构，失败再走 API。
        struct_sources = [s for s in (item.get("structs") or []) if isinstance(s, dict)]
        if not struct_sources and isinstance(item.get("struct"), dict):
            struct_sources = [item["struct"]]
        contents = []
        for source in struct_sources:
            contents.extend(source.get("contents", []) or [])
            if not source.get("contents") and source.get("zip_url"):
                contents.append({"group_name": source.get("group_name", "默认"), "url": source["zip_url"]})
        questions = parse_homework_struct(
            {"contents": contents},
            downloader=client._default_download,
        )
        struct = {"total_point": float(item.get("total_point", 0) or 0)}
        if not contents and not questions:
            # 列表无 contents 时兜底：尝试 g/homework/struct
            struct = await loop.run_in_executor(
                None, client.get_homework_struct, args.homework_id, resource_id, parent_id
            )
            questions = parse_homework_struct(struct, downloader=client._default_download)
    else:
        questions = await run_question_download(client, args.homework_id, resource_id, parent_id)

    if kind == "composition":
        info = await loop.run_in_executor(
            None, client.get_homework_info, args.homework_id, resource_id, parent_id
        )
        compositions = info.get("compositions") or info.get("composition") or info.get("composition_data") or []
        if isinstance(compositions, list) and compositions:
            metadata["compositions"] = compositions
            if not questions:
                questions = [
                    {
                        "entity_id": str(c.get("composition_id") or c.get("id") or f"essay-{i + 1}"),
                        "order": i,
                        "text": "",
                        "category": "composition",
                        "structure_type": "composition",
                        "question_id": (c.get("composition_id") or c.get("id") or 0),
                    }
                    for i, c in enumerate(compositions)
                ]

    if kind == "mock":
        combo = ""
        if not questions:
            try:
                exam_body = await loop.run_in_executor(
                    None, client.exam_struct, set_id, resource_id, parent_id
                )
                if isinstance(exam_body, dict):
                    struct = exam_body
                    content = exam_body.get("content", []) if isinstance(exam_body, dict) else []
                    if isinstance(content, list):
                        for c in content:
                            if isinstance(c, dict) and c.get("combination_id"):
                                combo = str(c.get("combination_id", ""))
                                break
            except Exception:
                pass
        if combo:
            metadata["combination_id"] = combo

    total_point = float(struct.get("total_point", 0)) if isinstance(struct, dict) else 0
    if kind in {"pk", "vocab"}:
        total_point = 100.0
    if total_point <= 0:
        total_point = 30.0

    home = Homework(
        id=str(args.homework_id),
        name=name,
        questions=[Question.from_dict(q, index) for index, q in enumerate(questions)],
        total_point=total_point,
        resource_id=resource_id,
        set_id=set_id,
        status=int(item.get("status", 0)),
        kind=kind,
        metadata=metadata,
    )
    if not home.questions:
        raise DemoError("No usable questions were extracted for this homework")
    return home


def build_word_questions(struct: dict) -> list[dict]:
    questions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in struct.get("word_question", []):
        key = (str(item.get("question_id", "")), str(item.get("word_id", "")))
        if key in seen:
            continue
        seen.add(key)
        questions.append(
            {
                "entity_id": str(item.get("id") or item.get("entity_id") or f"wq-{len(questions)}"),
                "order": len(questions),
                "text": str(item.get("word", {}).get("content", "") if isinstance(item.get("word"), dict) else item.get("text", "")),
                "category": "choose",
                "structure_type": "word_question",
                "correct_option": str(item.get("answer", "")),
                "word_id": int(item.get("word_id", item.get("word", {}).get("id", 0)) if isinstance(item.get("word"), dict) else item.get("word_id", 0)),
                "question_id": int(item.get("question_id", item.get("id", 0)) if str(item.get("question_id", item.get("id", "0"))).isdigit() else 0),
                "question_type": int(item.get("question_type", 0)),
                "options": item.get("options", []),
                "answer": str(item.get("answer", "")),
            }
        )
    return questions


async def run_question_download(
    client: EtsClient, homework_id: str, resource_id: str, parent_id: str = ""
) -> list[dict]:
    import asyncio

    loop = asyncio.get_event_loop()
    struct = await loop.run_in_executor(None, client.get_homework_struct, homework_id, resource_id, parent_id)
    if not isinstance(struct, dict):
        return []
    return parse_homework_struct(struct, downloader=client._default_download)


async def _run_async(args: argparse.Namespace) -> int:
    if args.command == "tts":
        output = await synthesize_edge(
            args.text, args.output, voice=args.voice, rate=args.rate, pitch=args.pitch
        )
        _json({"output": str(output.resolve()), "bytes": output.stat().st_size})
        return 0

    if args.command == "http-tts":
        output = await synthesize_http(
            args.text,
            args.output,
            HttpTtsConfig.from_env(),
            voice=args.voice,
            speed=args.speed,
        )
        _json({"output": str(output.resolve()), "bytes": output.stat().st_size})
        return 0

    if args.command in {"ise", "pipeline"}:
        audio = args.audio if args.command == "ise" else args.audio_output
        if args.command == "pipeline":
            await synthesize_edge(
                args.text, audio, voice=args.voice, rate=args.rate, pitch=args.pitch
            )
        if not audio.is_file():
            raise DemoError(f"Audio file not found: {audio}")
        result = await IseEvaluator(IseConfig.from_env()).evaluate_file(
            audio, args.text, category=args.category, resource_id=args.resource_id
        )
        if args.xml_output:
            args.xml_output.parent.mkdir(parents=True, exist_ok=True)
            args.xml_output.write_text(result.xml, encoding="utf-8")
        _json(
            {
                "sid": result.sid,
                "scores": result.scores,
                "audio": str(audio.resolve()),
                "xml_output": str(args.xml_output.resolve()) if args.xml_output else None,
            }
        )
        return 0

    if args.command == "run":
        homework = load_homework(args.source)
        interface = None
        if args.send_interface:
            interface = InterfaceClient(InterfaceConfig.from_env())
        try:
            options = WorkflowOptions(
                output_dir=args.output_dir,
                voice_mode=args.voice_mode,
                evaluator=args.evaluator,
                voice=args.voice,
                rate=args.rate,
                pitch=args.pitch,
                clone_reference=args.clone_reference,
                clone_config=CloneTtsConfig.from_env() if args.voice_mode == "clone" else None,
                http_tts_config=HttpTtsConfig.from_env() if args.voice_mode == "http" else None,
                ise_config=IseConfig.from_env() if args.evaluator == "ise" else None,
                score_profile=_score_profile(args),
                per_question_scores=_load_overrides(args.score_overrides),
                target_total=args.target_total,
                session_id=args.session_id,
                send_interface=args.send_interface,
            )
            report = await StudyWorkflow(
                options, interface=interface, progress=_progress
            ).run(homework)
        finally:
            if interface is not None:
                interface.close()
        _json({"summary": report["summary"], "report_path": report["report_path"]})
        return 0

    if args.command == "ets-do":
        return await _ets_do(args)
    raise DemoError(f"Unsupported asynchronous command: {args.command}")


async def _ets_do(args: argparse.Namespace, config: EtsConfig | None = None) -> int:
    config = config or EtsConfig.from_env()
    with EtsClient(config) as client:
        session = EtsSession.load(args.token_file)
        if not session.token:
            raise DemoError("No saved session. Run ets-login first or set ETS_PHONE/ETS_PASS.")
        client.token = session.token
        parent_id = args.parent_id or client.resolve_parent_id()
        client.parent_id = parent_id

        g = _ets_progress()
        homework = await fetch_real_homework(client, args, parent_id, g)

        uploader = None
        upload_fn = None
        if not args.no_upload:
            uploader = OssUploader(client)
            upload_fn = lambda file_path, kind: uploader.upload(Path(file_path), kind, str(config.phone))

        options = WorkflowOptions(
            output_dir=args.output_dir,
            voice_mode=args.voice_mode,
            evaluator=args.evaluator,
            voice=args.voice,
            rate=args.rate,
            pitch=args.pitch,
            clone_reference=args.clone_reference,
            clone_config=CloneTtsConfig.from_env() if args.voice_mode == "clone" else None,
            http_tts_config=HttpTtsConfig.from_env() if args.voice_mode == "http" else None,
            ise_config=IseConfig.from_env() if args.evaluator == "ise" else None,
            score_profile=_score_profile(args),
            per_question_scores=_load_overrides(args.score_overrides),
            target_total=args.target_total,
            use_time=getattr(args, "use_time", None),
            ets_client=client,
            parent_account_id=parent_id,
            upload=upload_fn,
        )
        report = await StudyWorkflow(options, interface=None, progress=_progress).run(homework)
    _json({"summary": report["summary"], "report_path": report["report_path"]})
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "doctor":
            return _doctor()
        if args.command in {"tts", "http-tts", "ise", "pipeline", "run", "ets-do"}:
            return asyncio.run(_run_async(args))
        if args.command in {"ets-login", "ets-setup"}:
            if args.command == "ets-login":
                return _ets_login(args)
            return _ets_setup(args)
        if args.command == "ets-parent":
            return _ets_parent(args)
        if args.command in {"ets-list", "ets-struct"}:
            return _ets_query(args)
        if args.command == "ets-score":
            return _ets_score(args)
        if args.command == "parse-zip":
            _json(extract_questions(read_content_json(args.path)))
            return 0
        if args.command == "inspect":
            _json(load_homework(args.source).to_dict())
            return 0
        if args.command == "score-xml":
            xml = generate_score_xml(
                args.text, _score_profile(args), category=args.category, seed=args.seed
            )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(xml, encoding="utf-8")
            _json({"output": str(args.output.resolve()), "bytes": len(xml.encode())})
            return 0
        if args.command == "mock-server":
            homeworks = [load_homework(source) for source in args.source]
            config = InterfaceConfig.from_env()
            print(f"Local mock interface: http://{args.host}:{args.port}", file=sys.stderr)
            serve(homeworks, args.host, args.port, config.pid, config.secret_key)
            return 0
        if args.command in {"interface-list", "interface-struct", "interface-call"}:
            with InterfaceClient(InterfaceConfig.from_env()) as client:
                if args.command == "interface-list":
                    _json(client.homework_list(args.session_id, args.parent_id))
                elif args.command == "interface-struct":
                    _json(client.homework_struct(args.homework_id, args.session_id, args.resource_id))
                else:
                    raw = args.params
                    if raw.startswith("@"):
                        raw = Path(raw[1:]).read_text(encoding="utf-8")
                    params = json.loads(raw)
                    if not isinstance(params, dict):
                        raise DemoError("--params must decode to a JSON object")
                    _json(client.request(args.path, args.route, params))
            return 0
        raise DemoError(f"Unsupported command: {args.command}")
    except (DemoError, ValueError, KeyboardInterrupt) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
