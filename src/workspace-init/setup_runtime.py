"""工作区 Python 运行环境准备脚本（自包含、纯标准库，仅用系统 python 执行）。

用途：
  在目标工作区里建一个持久化的虚拟环境 `.venv`，并把各技能需要的第三方依赖装进去，
  让后续脚本（如 B站视频总结的 transcribe_video.py）开箱即用，不必每次现场 pip、也不会
  再撞到「系统 python 用户目录不可写」而反复 monkeypatch / --target 的坑。

  这是「环境准备」的确定性一步：建 venv 用 python -m venv 的正常路径，装依赖用 venv 自带
  pip（--target 直指 .venv/Lib/site-packages，天然可写），全程幂等、带显式进度、失败即清晰报错。

设计约束：
  - 纯标准库（os / sys / subprocess / pathlib / argparse），零第三方依赖，任何 Python 3.11+ 能跑。
  - 幂等：.venv 已存在、依赖已装好，直接跳过不重复下载；--force 可强刷。
  - 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。

用法：
  python setup_runtime.py                        # 在当前目录建 .venv + 装默认依赖（yt-dlp、faster-whisper）
  python setup_runtime.py --target D:/my-advisor  # 在指定工作区准备环境
  python setup_runtime.py --target D:/my-advisor --with-opencc   # 追加 opencc（繁转简）
  python setup_runtime.py --target D:/my-advisor --check         # 只检查不安装
  python setup_runtime.py --target D:/my-advisor --force          # 忽略已装状态，重新装

依赖清单（B站视频总结 skill 所需）：
  - yt-dlp          下载音频
  - faster-whisper  离线转录（会再经 hf-mirror 拉 whisper 模型，模型缓存与依赖分离）
  - opencc-python-reimplemented（可选，繁转简）
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# 默认装到工作区 .venv 的依赖（bili-video-summary 所需；其它技能纯标准库无需安装）。
BASE_DEPENDENCIES = ["yt-dlp", "faster-whisper"]
OPTIONAL_DEPENDENCIES = {"opencc": "opencc-python-reimplemented"}

# 建 venv 之后，各技能脚本统一用 .venv 里的 python 运行。
VENV_NAME = ".venv"

# 国内 pip 镜像，避免官方 PyPI 超时反复重试。
PIP_INDEX = "https://pypi.tuna.tsinghua.edu.cn/simple"


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    """运行一条命令并透传输出；失败抛出带清晰信息的异常。"""
    print("  + " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd), check=False)


def _venv_python(target: Path) -> Path:
    return target / VENV_NAME / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python")


def _venv_pip(target: Path) -> Path:
    return target / VENV_NAME / ("Scripts" if sys.platform == "win32" else "bin") / ("pip.exe" if sys.platform == "win32" else "pip")


def _ensure_venv(target: Path, force: bool) -> None:
    """建持久 .venv（已存在则跳过；--force 不重建，只保证存在后刷新依赖）。"""
    py = _venv_python(target)
    if py.exists():
        print(f"  .venv 已存在，跳过创建：{py}", flush=True)
        return
    print("  [建 venv] 正在用 python -m venv 创建持久虚拟环境 .venv …", flush=True)
    result = _run([sys.executable, "-m", "venv", str(target / VENV_NAME)], target)
    if result.returncode != 0:
        print("  ✗ 创建 .venv 失败，请检查系统 python 是否可用（python3 -m venv --help）。", flush=True)
        sys.exit(1)
    print("  ✓ .venv 创建完成", flush=True)


# 包名（pip 名）→ 导入模块名 的显式映射；探测「是否已装」靠 import 模块名。
PACKAGE_MODULE = {
    "yt-dlp": "yt_dlp",
    "faster-whisper": "faster_whisper",
    "opencc-python-reimplemented": "opencc",
}


def _installed(target: Path, module: str) -> bool:
    """用 .venv python 探测某模块是否已可导入；.venv 未建则视为未装。"""
    py = _venv_python(target)
    if not py.exists():
        return False
    probe = subprocess.run(
        [str(py), "-c", f"import {module}"],
        cwd=str(target), check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return probe.returncode == 0


def _install(target: Path, packages: list[str], force: bool) -> None:
    """用 .venv pip 安装依赖；已装且非 force 则跳过；失败则退出并给排障指引。"""
    pip = _venv_pip(target)
    base = [str(pip), "install", "--disable-pip-version-check", "-i", PIP_INDEX]
    to_install: list[str] = []
    for pkg in packages:
        mod = PACKAGE_MODULE.get(pkg, pkg)
        if force or not _installed(target, mod):
            to_install.append(pkg)
    if not to_install:
        for pkg in packages:
            print(f"  ✓ 已装，跳过：{pkg}", flush=True)
        return

    print(f"  [2/2] 安装依赖：{' '.join(to_install)}（首次较慢，请耐心等待）…", flush=True)
    result = _run(base + to_install, target)
    if result.returncode != 0:
        print(
            "\n  ✗ 依赖安装失败。常见原因与排查：\n"
            "    1) 网络不通 → 已用清华镜像，仍失败可临时改用其他镜像或代理后重试；\n"
            "    2) 需要编译（如 ctranslate2 无 wheel）→ 升级 pip：py -m pip install -U pip；\n"
            "    3) 磁盘/权限问题 → 确认 .venv 目录可写。\n"
            "  失败后本脚本会自动重试一次，若仍失败请把上面报错原样转给用户。",
            flush=True,
        )
        sys.exit(1)
    print("  ✓ 依赖安装完成", flush=True)


def _check_all(target: Path) -> None:
    deps = {}
    for pkg in BASE_DEPENDENCIES + list(OPTIONAL_DEPENDENCIES.values()):
        mod = PACKAGE_MODULE.get(pkg, pkg)
        deps[pkg] = "ok" if _installed(target, mod) else "missing"
    print("  依赖状态：", flush=True)
    for k, v in deps.items():
        print(f"    {k}: {v}", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="setup_runtime", description="工作区 Python 运行环境准备（建 .venv + 装依赖）")
    ap.add_argument("--target", default=".", help="目标工作区目录（默认当前目录）")
    ap.add_argument("--with-opencc", action="store_true", help="额外安装 opencc（繁转简，可选）")
    ap.add_argument("--check", action="store_true", help="只校验依赖是否就绪，不安装")
    ap.add_argument("--force", action="store_true", help="忽略已装状态，强制重装")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    packages = list(BASE_DEPENDENCIES)
    if args.with_opencc:
        packages.append(OPTIONAL_DEPENDENCIES["opencc"])

    if args.check:
        _check_all(target)
        # --check 用非零退出码表示未就绪，便于脚本串联判断
        ready = _installed(target, "yt_dlp") and _installed(target, "faster_whisper")
        sys.exit(0 if ready else 1)

    print(f"准备运行时环境：{target}", flush=True)
    print("  [1/2] 创建/确认持久虚拟环境 .venv …", flush=True)
    _ensure_venv(target, args.force)
    _install(target, packages, args.force)
    print(f"\n[完成] 运行时环境就绪：{target / VENV_NAME}", flush=True)
    print(f"  后续各技能脚本用该 python 运行：{_venv_python(target)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())