import argparse
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _run(cmd, **kwargs):
    print("+", " ".join(str(p) for p in cmd), flush=True)
    subprocess.run([str(c) for c in cmd], cwd=ROOT, check=True, **kwargs)


def _build_dist() -> Path:
    try:
        import build  # noqa: F401

        has_build = True
    except ImportError:
        has_build = False
    if not has_build:
        _run([sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", "pip"])
        _run([sys.executable, "-m", "pip", "install", "--quiet", "build"])
    _run([sys.executable, "-m", "build"])
    wheels = sorted((ROOT / "dist").glob("*.whl"))
    if not wheels:
        raise SystemExit("build produced no wheel in dist/")
    return wheels[-1]


def mirror_venv(target: Path) -> None:
    """克隆当前解释器到目标 venv."""
    if not (target / "bin" / "python").exists():
        venv.EnvBuilder(with_pip=True, clear=True).create(str(target))
    pip = target / "bin" / "pip"
    _run([pip, "install", "--quiet", "--upgrade", "pip"])
    _run([pip, "install", "--quiet", os.getcwd()])


def find_command(names: list[str]) -> Path:
    for name in names:
        path = Path(name)
        if path.exists() and path.is_file():
            return path
    raise RuntimeError(f"未找到可执行命令: {names}")


def main() -> None:
    parser = argparse.ArgumentParser(description="release: build wheel, install CLI, then guide login")
    parser.add_argument("--venv", default=None, help="已存在的虚拟环境路径（将往其中安装）")
    parser.add_argument("--python", default=None, help="用于创建全新 venv 的解释器")
    parser.add_argument("--no-build", action="store_true", help="跳过构建只安装")
    args = parser.parse_args()

    wheel = None if args.no_build else _build_dist()

    if args.venv:
        python = Path(args.venv) / "bin" / "python"
        if not python.exists():
            _run([
                sys.executable, "-m", "venv",
                str(Path(args.venv).resolve()),
            ])
            python = Path(args.venv) / "bin" / "python"
        print(f"使用 venv: {args.venv}", flush=True)
    else:
        parent = ROOT / "venv"
        parent.mkdir(exist_ok=True)
        python = Path(sys.executable) if args.python is None else Path(args.python)
        print(f"创建 venv: {parent / 'cli'}", flush=True)
        _run([python, "-m", "venv", parent / "cli"])
        python = parent / "cli" / "bin" / "python"

    pip = python.with_name("pip")
    source = wheel if wheel is not None else str(ROOT)
    _run([pip, "install", "--quiet", "--upgrade", "pip"])
    _run([pip, "install", "--quiet", source])

    cli = find_command([str(python.with_name("ets-demo")), str(python.with_name("ets-demo.exe"))])
    _run([cli, "doctor"])

    print()
    print("=" * 60)
    print("安装完成。接下来引导登录真实 ETS 平台。")
    print("=" * 60)
    print()
    print("请先设置 ETS_SECRET_KEY（可选账号用于自动重登：ETS_PHONE/ETS_PASS）：")
    print()
    print("  set -x ETS_SECRET_KEY '你的 secret key'")
    print(f"  set -x PATH {python.parent} $PATH")
    print()
    print("然后登录：")
    print()
    print(f"  {cli} ets-login 手机号 密码")
    print()
    print("登录后查询作业与自动完成：")
    print()
    print(f"  {cli} ets-list --parent-id ''")
    print(f"  {cli} ets-do <homework_id> --output-dir output/ets")
    print()
    print("提示：ets-do 会自动下载题目、合成音频、评分并回传结果。")
    print("提示：登录凭据保存在 ~/.ets_demo/session.json（权限 600）。")
    print("提示：若未显式指定父账号，可先运行 ets-parent 获取 parent_id。")
    print()
    print(f"CLI 安装路径: {cli}")


if __name__ == "__main__":
    main()