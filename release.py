#!/usr/bin/env python3
"""
ETS 作业自动完成 - 开箱即用版

直接运行本脚本即可进入交互流程：
    欢迎横幅 -> 检测已保存账号 -> 输入手机号/密码 -> 登录 ->
    获取父账号 -> 列出待完成作业 -> 选择 -> 自动完成并回传。

若当前 Python 环境缺少依赖，脚本会自动创建 venv、安装依赖后重启自身。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _bootstrap() -> None:
    """确保 ets_demo 及其依赖可用，否则在 venv 中安装并以之重启。"""
    sys.path.insert(0, str(ROOT / "src"))
    try:
        from ets_demo.cli import main  # noqa: F401
        import aiohttp  # noqa: F401
        import edge_tts  # noqa: F401
        import websockets  # noqa: F401
        return
    except ImportError:
        pass

    import subprocess
    import venv

    venv_dir = ROOT / "venv" / "cli"
    python = venv_dir / "bin" / "python"
    if not python.exists():
        print("首次运行：正在准备运行环境 ...", file=sys.stderr)
        venv.EnvBuilder(with_pip=True).create(str(venv_dir))
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", "--upgrade", "pip"], check=True)
    subprocess.run([str(python), "-m", "pip", "install", "--quiet", str(ROOT)], check=True)
    print("运行环境就绪，重新启动 ...", file=sys.stderr)
    os.execv(str(python), [str(python), str(ROOT / "release.py"), *sys.argv[1:]])


def _saved_session() -> dict:
    try:
        from ets_demo.ets import EtsSession, decode_password

        session = EtsSession.load()
        password = decode_password(session.password_enc)
        if session.phone and password:
            return {"phone": session.phone, "password": password}
    except Exception:
        pass
    return {}


def _gather_credentials(saved: dict) -> tuple[str, str]:
    import getpass

    if saved:
        answer = input(f"检测到已保存账号 {saved['phone']}，直接使用？(y/n) [y]: ").strip().lower()
        if answer in ("", "y", "yes"):
            print(f"使用已保存账号 {saved['phone']}")
            return saved["phone"], saved["password"]
    print()
    phone = input("请输入手机号: ").strip()
    while not phone:
        phone = input("手机号不能为空，请重新输入: ").strip()
    password = getpass.getpass("请输入密码（输入不显示）: ")
    while not password:
        password = getpass.getpass("密码不能为空，请重新输入: ")
    if not saved:
        save = input("是否保存密码，下次自动登录？(y/n) [y]: ").strip().lower()
        if save in ("", "y", "yes"):
            saved["phone"], saved["password"] = phone, password
    return phone, password


def _login(config, phone: str, password: str) -> dict:
    from ets_demo.ets import EtsClient, EtsError, EtsSession

    def sms_code() -> str:
        return input("请输入短信验证码（新设备需绑定，已发送至手机）: ").strip()

    client = EtsClient(config)
    try:
        client.login(phone, password, code_provider=sms_code)
        try:
            client.parent_id = client.get_parent_id()
        except EtsError:
            client.parent_id = ""
        session = EtsSession.load()
        session.token = client.token
        session.phone = client.session.phone
        session.password_enc = client.session.password_enc
        session.device_code = client.session.device_code
        session.parent_id = client.parent_id
        session.save()
        return {"client": client, "parent_id": client.parent_id, "token": client.token}
    except EtsError as exc:
        raise SystemExit(f"登录失败: {exc}")
    finally:
        client.close()

def _homework_bucket(item) -> str:
    """ETS 真实 item.status: 0=未完成 1=已完成 2=已过期 4=待重交 5=历史。
    归并：待完成(0,4) / 已完成(1,5) / 已过期(2)。"""
    status = str(item.get("status", "0"))
    if status in ("0", "4"):
        return "0"
    if status in ("1", "5"):
        return "1"
    return "2"


def _pick_homework(client, parent_id: str) -> list[dict]:
    from ets_demo.ets import EtsError, classify_homework

    try:
        items = client.get_homework_list(parent_id)
    except EtsError as exc:
        if "80004" in str(exc) or "没有找到有效班级" in str(exc):
            print(f"错误: {exc}")
            print("提示：该账号未加入任何班级，或需要先在 ETS 官方客户端绑定班级。")
            return []
        raise
    if not items:
        print("没有获取到任何作业。")
        return []

    categories = [
        ("0", "待完成"),
        ("1", "已完成"),
        ("2", "已过期"),
    ]
    labels = {key: label for key, label in categories}
    grouped = {key: [item for item in items if _homework_bucket(item) == key] for key, _ in categories}

    while True:
        print()
        print("请选择作业分类：")
        for index, (key, label) in enumerate(categories):
            count = len(grouped[key])
            print(f"  {index + 1}. {label}（{count} 份）")
        print("  回车=退出")
        category_choice = input("分类序号: ").strip()
        if not category_choice:
            return []
        try:
            key = categories[int(category_choice) - 1][0]
        except (ValueError, IndexError):
            print(f"无效分类: {category_choice}")
            continue

        bucket = grouped[key]
        if not bucket:
            print(f"没有{labels[key]}的作业。")
            continue
        print()
        print(f"{labels[key]}作业：")
        for index, item in enumerate(bucket):
            name = item.get("name", "")
            hid = item.get("id", "")
            kind = classify_homework(item)
            print(f"  {index + 1}. {name}  id={hid}  类型={kind}")
        print()
        choice = input("请选择要处理的作业序号（多个用逗号分隔，0=全部，回车=返回分类菜单）: ").strip()
        if not choice:
            continue
        try:
            if choice == "0":
                return bucket
            indices = [int(part) - 1 for part in choice.replace("，", ",").split(",") if part.strip()]
            return [bucket[i] for i in indices]
        except (ValueError, IndexError):
            print(f"无效序号: {choice}")


def _voice_choice(config) -> tuple[str, str | None]:
    """根据本地配置决定 voice_mode 和 clone_reference。
    返回 (voice_mode, clone_reference)。"""
    from ets_demo.config import _env

    mode = _env("ETS_VOICE_MODE", "edge").strip().lower()
    reference = _env("ETS_CLONE_REFERENCE", "").strip()
    if mode not in ("edge", "clone", "http"):
        mode = "edge"
    if mode == "clone":
        try:
            from ets_demo.config import CloneTtsConfig

            CloneTtsConfig.from_env().validate()
        except Exception as exc:
            print(f"克隆 TTS 未配置：{exc}")
            print("提示：在 ~/.ets_demo/config.local.json 中配置 CLONE_TTS_URL / CLONE_TTS_RESULTS_URL / CLONE_TTS_API_KEY，")
            print("      并用 ETS_CLONE_REFERENCE 指定参考音频路径后重试。")
            print("回退到 edge 语音。")
            return "edge", None
        if not reference:
            print("克隆模式需要参考音频：在 config.local.json 中设置 ETS_CLONE_REFERENCE=/path/to/reference.wav")
            print("回退到 edge 语音。")
            return "edge", None
    return mode, reference or None


def _do_homework(config, item: dict, parent_id: str = "") -> None:
    import asyncio
    from ets_demo.cli import _ets_do
    from ets_demo.ets import EtsSession
    import argparse

    voice_mode, clone_reference = _voice_choice(config)
    homework_id = str(item.get("id", ""))
    resource_id = str(item.get("resource_id", ""))
    print()
    print(f"开始自动完成：{item.get('name', '')} ({homework_id})  语音={voice_mode}"
          + (f" 参考={clone_reference}" if clone_reference else ""))
    run_args = argparse.Namespace(
        command="ets-do",
        homework_id=homework_id,
        token_file=None,
        parent_id=parent_id,
        resource_id=resource_id,
        output_dir=Path("output/ets"),
        voice_mode=voice_mode,
        evaluator="local",
        voice="en-US-JennyNeural",
        rate="-10%",
        pitch="+0Hz",
        clone_reference=Path(clone_reference) if clone_reference else None,
        score_overrides=None,
        target_total=None,
        no_upload=False,
        score=5.0,
        accuracy=4.95,
        fluency=4.95,
        integrity=4.95,
        stress=4.90,
    )
    asyncio.run(_ets_do(run_args, config=config))


def main() -> None:
    from ets_demo.config import EtsConfig

    print()
    print("=" * 56)
    print("  ETS 作业自动完成 · 开箱即用")
    print("=" * 56)
    print()

    config = EtsConfig.from_env()
    saved = _saved_session()
    phone, password = _gather_credentials(saved)
    print()
    print("正在登录 ...")
    logged = _login(config, phone, password)
    parent_id = logged["parent_id"]
    if parent_id:
        print(f"登录成功！parent_id={parent_id}  token={logged['token'][:12]}...")
    else:
        print(f"登录成功！token={logged['token'][:12]}...")
        print("注意：该账号未找到有效班级（m/ecard/list 无 parent_id）。")
        print("      请先在 ETS 官方客户端绑定/加入班级后重试，否则作业无法列出。")
        return

    client = logged["client"]
    try:
        selected = _pick_homework(client, parent_id)
        for item in selected:
            _do_homework(config, item, parent_id=parent_id)
        if selected:
            print()
            print(f"完成，共处理 {len(selected)} 份作业。输出目录见 output/ets/")
    finally:
        client.close()


if __name__ == "__main__":
    _bootstrap()
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        raise SystemExit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(1)
