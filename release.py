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
    """优先读取 config.local.json 里的 ETS_PHONE/ETS_PASS，其次 session.json。"""
    from ets_demo.config import _env
    from ets_demo.ets import EtsSession, decode_password

    phone = _env("ETS_PHONE", "").strip()
    password = _env("ETS_PASS", "").strip()
    if phone and password:
        return {"phone": phone, "password": password, "source": "config"}
    try:
        session = EtsSession.load()
        password = decode_password(session.password_enc)
        if session.phone and password:
            return {"phone": session.phone, "password": password, "source": "session"}
    except Exception:
        pass
    return {}


def _gather_credentials(saved: dict) -> tuple[str, str]:
    import getpass

    if saved.get("source") == "config":
        print(f"使用配置账号 {saved['phone']}（config.local.json）")
        return saved["phone"], saved["password"]
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
            print("提示：CLONE_TTS_URL 默认指向 tts.ai（https://tts.ai/api/v1/voice-clone/），")
            print("      注册 https://tts.ai 后把 API Key 填入 config.local.json 的 CLONE_TTS_API_KEY（不填也可匿名使用），")
            print("      并用 ETS_CLONE_REFERENCE 指定参考音频路径后重试。")
            print("回退到 edge 语音。")
            return "edge", None
        if not reference:
            print("克隆模式需要参考音频：在 config.local.json 中设置 ETS_CLONE_REFERENCE=/path/to/reference.wav")
            print("回退到 edge 语音。")
            return "edge", None
    return mode, reference or None


def _score_values() -> dict:
    """从 config.local.json 读取自定义分数（默认满分 5.0，各维度接近满分）。"""
    from ets_demo.config import _env

    def f(key: str, default: float) -> float:
        try:
            return float(_env(key, str(default)))
        except ValueError:
            return default

    def i(key: str, default: int) -> int:
        try:
            return int(float(_env(key, str(default))))
        except ValueError:
            return default

    return {
        "score": f("ETS_SCORE", 5.0),
        "accuracy": f("ETS_SCORE_ACCURACY", 4.95),
        "fluency": f("ETS_SCORE_FLUENCY", 4.95),
        "integrity": f("ETS_SCORE_INTEGRITY", 4.95),
        "stress": f("ETS_SCORE_STRESS", 4.90),
        "target_total": f("ETS_TARGET_TOTAL", -1.0),
        "use_time": i("ETS_USE_TIME", 0),
    }


_CATEGORY_LABELS = {
    "read_sentence": "朗读",
    "read_chapter": "篇章朗读",
    "choose": "选择",
    "role": "角色扮演",
    "repeat": "跟读",
    "fill": "填空",
    "picture": "看图说话",
    "composition": "作文",
    "word_question": "单词",
}


def _category_label(category: str) -> str:
    return _CATEGORY_LABELS.get(category, category or "题目")


def _print_homework_preview(homework) -> None:
    """打印作业总分、题型分布和题目列表。"""
    from collections import Counter

    labels = Counter(_category_label(q.category) for q in homework.questions)
    print()
    print("─" * 56)
    print(f"作业：{homework.name}")
    print(f"总分：{homework.total_point:.2f}    题目数：{len(homework.questions)}")
    print("题型：" + "  ".join(f"{name}×{count}" for name, count in labels.items()))
    print("─" * 56)
    print("题目列表：")
    for index, question in enumerate(homework.questions, start=1):
        label = _category_label(question.category)
        text = (question.text or "").strip().replace("\n", " ")
        if len(text) > 48:
            text = text[:48] + "..."
        if question.category == "choose" and text:
            text = f"答案：{text}"
        elif not text:
            text = "（无文本，音频题）"
        extra = f"  选项×{len(question.options)}" if question.options else ""
        print(f"  {index:2d}. [{label}] {text}{extra}")
    print("─" * 56)


MAX_FIELD_SCORE = 5.0


def _prompt_float(label: str, default: float, *, maximum: float | None = None) -> float:
    """读取浮点输入；超过 maximum 时仅提示，不强制限制。"""
    while True:
        raw = input(f"  {label} [{default:g}]: ").strip()
        if not raw:
            value = default
        else:
            try:
                value = float(raw)
            except ValueError:
                print("    请输入数字。")
                continue
        if value < 0:
            print("    分数不能为负，请重新输入。")
            continue
        if maximum is not None and value > maximum:
            print(
                f"    ⚠ 当前分数 {value:g} 超过单项满分 {maximum:g}，"
                "将按原样提交，可能被平台拒绝。"
            )
        return value


def _prompt_int(label: str, default: int) -> int:
    while True:
        raw = input(f"  {label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return max(0, int(float(raw)))
        except ValueError:
            print("    请输入整数。")


def _profile_from(value: float, base: dict) -> dict:
    """以统一维度为基础，用 value 作为总分维度，构造逐题配置。"""
    return {
        "score": value,
        "accuracy": base["accuracy"],
        "fluency": base["fluency"],
        "integrity": base["integrity"],
        "stress": base["stress"],
    }


def _short_text(question, limit: int = 34) -> str:
    text = (question.text or "").strip().replace("\n", " ")
    if question.category == "choose" and text:
        text = f"答案 {text}"
    if not text:
        text = "（音频题）"
    return text[:limit] + ("…" if len(text) > limit else "")


def _configure_scoring(homework, defaults: dict) -> dict:
    """展示评分菜单：统一分 / 满分 / 逐题，并可覆盖最终总分。"""
    base = dict(defaults)
    print()
    print("─" * 56)
    print("  评分设置")
    print("─" * 56)
    print(f"  1. 配置文件统一分数（总分={base['score']:.2f}，各维度≈4.95）")
    print("  2. 全部满分（总分=5.0）")
    if homework is not None and homework.questions:
        print(f"  3. 逐题设置分数（共 {len(homework.questions)} 题，0-5，可超限）")
    else:
        print("  3. 逐题设置分数（无题目信息，不可用）")
    print("  回车 = 1")
    choice = input("请选择 [1]: ").strip() or "1"

    per_question: dict[int, dict] = {}
    uniform = base

    if choice == "2":
        uniform = {
            "score": MAX_FIELD_SCORE,
            "accuracy": MAX_FIELD_SCORE,
            "fluency": MAX_FIELD_SCORE,
            "integrity": MAX_FIELD_SCORE,
            "stress": MAX_FIELD_SCORE,
        }
        print(f"  已选择全部满分：总分={MAX_FIELD_SCORE:.2f}")
    elif choice == "3" and homework is not None and homework.questions:
        maximum_per_question = homework.total_point / len(homework.questions)
        print()
        print("  逐题评分：直接回车=沿用统一分，可输入 0-5 或更大值。")
        print(f"  本题满分≈{maximum_per_question:.2f} 分（仅作参考，可超出）")
        for index, question in enumerate(homework.questions):
            label = _category_label(question.category)
            print(f"  {index + 1:2d}. [{label}] {_short_text(question)}")
            value = _prompt_float("      本题分数 (0-5)", base["score"], maximum=MAX_FIELD_SCORE)
            per_question[index] = _profile_from(value, base)
        uniform = base
    else:
        print(f"  使用配置统一分数：总分={base['score']:.2f}")

    print()
    print("─" * 56)
    print("  最终总分（云端）")
    print("─" * 56)
    print("  可覆盖各题汇总，允许超过作业满分。例：输入 10000 尝试提交 10000 分。")
    default_total = base["target_total"] if base["target_total"] > 0 else 0.0
    total = _prompt_float("总分（0=按各题汇总）", default_total)
    target_total = total if total > 0 else None
    allow_over_max = total > 0 or any(
        p["score"] > MAX_FIELD_SCORE for p in per_question.values()
    ) or uniform["score"] > MAX_FIELD_SCORE
    if target_total is not None and homework is not None and target_total > homework.total_point:
        print(
            f"    ⚠ 总分 {target_total:.2f} 超过作业满分 {homework.total_point:.2f}，"
            "将按原样提交，可能被平台拒绝。"
        )

    use_time = _prompt_int("完成用时秒（0=推荐用时）", base["use_time"])

    return {
        "profile": uniform,
        "per_question": per_question,
        "target_total": target_total,
        "use_time": use_time,
        "allow_over_max": allow_over_max,
    }


async def _fetch_homework_preview(client, homework_id: str, resource_id: str, parent_id: str):
    """拉取作业结构用于预览，失败返回 None。"""
    import argparse
    from ets_demo.cli import fetch_real_homework, _ets_progress

    stub = argparse.Namespace(homework_id=homework_id, resource_id=resource_id)
    return await fetch_real_homework(client, stub, parent_id, _ets_progress())


def _view_score(client, item: dict, parent_id: str = "") -> None:
    """查询并格式化打印一份作业的分数/进度。"""
    import json as _json_mod

    homework_id = str(item.get("id", ""))
    resource_id = str(item.get("resource_id", ""))
    print()
    print(f"作业：{item.get('name', '')} ({homework_id})")
    try:
        info = client.get_homework_info(homework_id, resource_id, parent_id)
    except Exception as exc:
        print(f"查询失败: {exc}")
        return
    if not isinstance(info, dict):
        print("服务器无返回。")
        return

    total_point = float(item.get("total_point", 0) or 0)
    point = float(info.get("point", 0) or 0)
    status = str(item.get("status", "0"))
    status_text = {"0": "未完成", "1": "已完成", "2": "已过期", "4": "待重交", "5": "历史"}.get(status, status)
    print(f"状态：{status_text}   得分：{point:.2f}/{total_point:.2f}")
    complete = info.get("complete")
    if complete in (None, ""):
        complete = item.get("complete")
    if complete not in (None, ""):
        print(f"完成度：{complete}%")
    if total_point > 0 and point > 0:
        print(f"得分率：{point / total_point * 100:.1f}%")
    else:
        avg = item.get("avg_point") if item.get("avg_point") not in (None, "") else info.get("avg_point")
        if avg not in (None, ""):
            try:
                avg_value = float(avg)
                avg_pct = avg_value / 100.0 if avg_value > 100 else avg_value
                print(f"得分率：{avg_pct:.1f}%")
            except (TypeError, ValueError):
                pass

    scores = info.get("score") or []
    if isinstance(scores, list) and scores:
        print(f"答题明细（{len(scores)} 题）：")
        for i, s in enumerate(scores, start=1):
            real = s.get("real_score")
            detail_raw = s.get("detail") or ""
            dims = ""
            if detail_raw:
                try:
                    d = _json_mod.loads(detail_raw)
                    dims = "  ".join(
                        f"{k}={v}" for k, v in d.items()
                        if k in ("accuracy_score", "fluency_score", "integrity_score", "standard_score")
                    )
                except ValueError:
                    pass
            audio = s.get("audio") or ""
            audio_mark = " 有音频" if audio else ""
            print(f"  {i:2d}. 得分={real}{audio_mark}  {dims}")
    elif "last_complete" in info and isinstance(info["last_complete"], dict):
        last = info["last_complete"]
        submit_time = last.get("submit_time") or ""
        last_point = last.get("point") or 0
        last_complete = last.get("complete") or 0
        print(f"最近一次：提交于 {submit_time or '未知'}  得分={last_point}  完成度={last_complete}%")
    else:
        print("暂无答题明细（可能尚未提交）。")


def _pick_action(item: dict) -> str:
    """针对单份作业的操作菜单，返回 do / score / skip。"""
    name = str(item.get("name", ""))
    hid = str(item.get("id", ""))
    while True:
        print()
        print(f"{name} ({hid})")
        print("  1. 自动完成")
        print("  2. 查看分数/进度")
        print("  回车 = 自动完成   s = 跳过")
        choice = input("请选择: ").strip().lower()
        if choice == "2":
            return "score"
        if choice in ("", "1"):
            return "do"
        if choice == "s":
            return "skip"
        print("无效输入")


def _do_homework(config, client, item: dict, parent_id: str = "") -> None:
    import asyncio
    import argparse
    from ets_demo.cli import _ets_do
    from ets_demo.config import _env

    voice_mode, clone_reference = _voice_choice(config)
    defaults = _score_values()
    homework_id = str(item.get("id", ""))
    resource_id = str(item.get("resource_id", ""))

    print()
    print(f"正在读取作业内容：{item.get('name', '')} ({homework_id}) ...")
    homework = None
    try:
        homework = asyncio.run(
            _fetch_homework_preview(client, homework_id, resource_id, parent_id)
        )
    except Exception as exc:  # noqa: BLE001
        print(f"无法预览题目（{exc}），将直接开始。")
    if homework is not None:
        _print_homework_preview(homework)
    else:
        total_point = float(item.get("total_point", 0) or 0)
        if total_point > 0:
            print(f"总分：{total_point:.2f}")

    scoring = _configure_scoring(homework, defaults)
    scores = scoring["profile"]
    from ets_demo.scoring import ScoreProfile

    per_question = {
        index: ScoreProfile(
            total=profile["score"],
            accuracy=profile["accuracy"],
            fluency=profile["fluency"],
            integrity=profile["integrity"],
            stress=profile["stress"],
        )
        for index, profile in scoring["per_question"].items()
    }

    print()
    print("─" * 56)
    print("  开始自动完成")
    print("─" * 56)
    print(f"  作业：{item.get('name', '')} ({homework_id})")
    print(f"  语音：{voice_mode}" + (f"  参考={clone_reference}" if clone_reference else ""))
    score_note = (
        f"逐题设置（{len(per_question)} 题）"
        if per_question
        else f"统一总分={scores['score']:.2f}"
    )
    print(f"  评分：{score_note}（accuracy={scores['accuracy']:.2f} "
          f"fluency={scores['fluency']:.2f} integrity={scores['integrity']:.2f} "
          f"stress={scores['stress']:.2f}）")
    if scoring["target_total"] is not None:
        print(f"  最终总分：{scoring['target_total']:.2f}"
              + ("  ⚠ 超出作业满分" if scoring["allow_over_max"] else ""))
    else:
        print("  最终总分：按各题汇总")
    print(f"  用时：{'自定义 ' + str(scores['use_time']) + ' 秒' if scoring['use_time'] > 0 else '按推荐用时（未设置）'}")
    if voice_mode == "clone":
        tone = _env("ETS_CHINESE_TONE", "").lower() in {"1", "true", "yes"}
        noise = float(_env("ETS_NOISE", "0") or 0)
        print(f"  克隆音色：{'中式语调（慢速+降调）' if tone else '原声'}"
              + (f"，噪点强度={noise}" if noise > 0 else ""))
    print("─" * 56)
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
        target_total=scoring["target_total"],
        use_time=scoring["use_time"] if scoring["use_time"] > 0 else None,
        no_upload=False,
        score=scores["score"],
        accuracy=scores["accuracy"],
        fluency=scores["fluency"],
        integrity=scores["integrity"],
        stress=scores["stress"],
    )
    asyncio.run(
        _ets_do(
            run_args,
            config=config,
            homework=homework,
            per_question_scores=per_question or None,
            allow_over_max=scoring["allow_over_max"],
        )
    )


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
        done = 0
        for item in selected:
            action = _pick_action(item)
            if action == "score":
                _view_score(client, item, parent_id=parent_id)
                again = input("  查看下一操作？(y/n) [y]: ").strip().lower()
                if again in ("n", "no"):
                    continue
            elif action == "skip":
                print(f"跳过 {item.get('name', '')}")
                continue
            _do_homework(config, client, item, parent_id=parent_id)
            done += 1
        if done:
            print()
            print(f"完成，共处理 {done} 份作业。输出目录见 output/ets/")
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
