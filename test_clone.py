#!/usr/bin/env python3
"""测试 tts.ai 免费克隆模型的效果。

用法：
  python test_clone.py --reference /path/to/ref.wav [--text "英语文本"] [--model pocket-tts] [--output out.mp3]

要求：
  - 参考音频 ≥5 秒的英语人声（建议 10-30 秒、单说话人、无噪音），wav/mp3 均可
  - 可选的 API Key 放在 config.local.json 的 CLONE_TTS_API_KEY（不填用匿名配额）
"""
from __future__ import annotations

import argparse
import asyncio
import random
import sys
from pathlib import Path

from ets_demo.audio import synthesize_clone
from ets_demo.config import CloneTtsConfig

DEFAULT_TEXT = (
    "Good morning everyone. Today we are going to talk about the weather in spring. "
    "In the north of China, spring is usually dry and windy, but the flowers still bloom. "
    "This is a short reading test for the cloned voice."
)


def _random_ip() -> str:
    return f"{random.randint(2, 223)}.{random.randint(0, 255)}.{random.randint(0, 255)}.{random.randint(1, 254)}"


async def _main(args: argparse.Namespace) -> int:
    reference = Path(args.reference)
    if not reference.is_file():
        print(f"错误：参考音频不存在：{reference}", file=sys.stderr)
        return 2
    config = CloneTtsConfig.from_env()
    if args.model:
        config = CloneTtsConfig(
            submit_url=config.submit_url,
            results_url=config.results_url,
            api_key=config.api_key,
            model=args.model,
            timeout=config.timeout,
            poll_interval=config.poll_interval,
            max_wait=config.max_wait,
        )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    print(f"参考音频 : {reference} ({reference.stat().st_size} bytes)")
    print(f"克隆模型 : {config.model}")
    print(f"API Key  : {'已配置' if config.api_key else '未配置（匿名使用）'}")
    print(f"朗读文本 : {args.text!r}")
    print("正在克隆并合成，可能需要 1-2 分钟...")
    attempts = 1 if args.forwarded_for else 10
    used_ip = args.forwarded_for
    result = None
    for attempt in range(attempts):
        try:
            result = await synthesize_clone(
                args.text,
                reference,
                output,
                config,
                speed=args.speed,
                forwarded_for=used_ip,
                chinese_tone=args.chinese_tone,
                noise=args.noise,
            )
            break
        except Exception as exc:
            if attempt >= attempts - 1 or ("429" not in str(exc) and "Processing failed" not in str(exc)):
                print(f"失败：{exc}", file=sys.stderr)
                return 1
            used_ip = _random_ip()
            print(f"触发限流/服务端失败，换 IP 重试（第 {attempt + 2}/{attempts} 次，新 IP={used_ip}）...")
            await asyncio.sleep(2)
    if result is None:
        print("失败：多次重试后仍被限流", file=sys.stderr)
        return 1
    print(f"成功：{result} ({result.stat().st_size} bytes)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="测试 tts.ai 免费克隆模型")
    parser.add_argument("--reference", required=True, help="≥5 秒英语参考音频路径")
    parser.add_argument("--text", default=DEFAULT_TEXT, help="要朗读的英语文本")
    parser.add_argument("--model", default=None, help="克隆模型，默认读 config 的 CLONE_TTS_MODEL")
    parser.add_argument("--output", default="clone_test.mp3", help="输出 MP3 路径")
    parser.add_argument("--speed", type=float, default=None, help="语速倍率")
    parser.add_argument("--forwarded-for", default=None, help="X-Forwarded-For 头（绕过本机 IP 限流）")
    parser.add_argument("--chinese-tone", action="store_true", help="中式英语语调（放慢语速+轻微降调，非发音替换）")
    parser.add_argument("--noise", type=float, default=0.0, help="噪点强度 0-1（0.1 轻微底噪 / 0.3 明显 / 0.6 较强，默认 0=无）")
    return asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
