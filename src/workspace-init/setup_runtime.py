"""工作区 Python 运行环境准备脚本（自包含、纯标准库，仅用于「建 .venv + 装依赖」）。

定位与边界（重要）：
  本脚本只负责「工作区 .venv + 第三方依赖」这一层，是**插件可控的自动化路径**。
  它**不负责安装 Python 解释器本身**——Python 是否可用是用户机器的环境前提，
  属于「非插件问题」。本脚本检测到系统无可用 Python 时，不尝试任何自动安装/兜底，
  而是明确停下、报告 `python_missing` 并提示用户手动装 Python（这是用户职责）。

三分法状态（供 agent / 脚本串联判断，避免「装没装」的模糊兜底）：
  - python_missing  ：系统无可用 Python（python / python3 都调不起来）→ 需用户装，脚本退出码 2，不再自动帮忙。
  - deps_missing    ：Python 可用，但工作区 .venv 或依赖缺失 → 本脚本自动建 venv 装依赖（可选），装成功后 ready。
  - ready           ：工作区 .venv 就绪，yt-dlp / faster-whisper 可导入。

退出码约定（与其他技能脚本、调用方对齐）：
  0  ready（--check 时依赖就绪）
  1  依赖安装失败（网络/编译/磁盘等；属环境问题，需用户处置，脚本只报错不硬来）
  2  python_missing（系统无可用 Python，需用户手动安装）
  3  needs_escalation（安装撞权限拒绝：Errno 13 / WinError 5。典型是带文件沙箱的
     会话里，受限令牌连 Python 建的受限临时目录都进不去，venv/ensurepip/pip 因此
     失败。唯一处置：用会话的提权机制重跑同一条命令、交用户审批；--target /
     monkeypatch / 换路径等替代方案会同样失败，一律禁止）

设计约束：
  - 纯标准库（os / sys / subprocess / shutil / pathlib / argparse），零第三方依赖，任何 Python 3.11+ 能跑。
  - 幂等：.venv 已存在、依赖已装好则跳过不重复下载；--force 可强刷。
  - 不依赖本仓库 quantify 包，可拷贝到任意工作区单独运行。
  - 权限与提权：.venv 与 pip 安装的写入全部落在目标工作区内；但 venv/ensurepip/pip
    运行时要建「仅本用户」的受限临时目录，在带文件沙箱的会话里可能连自己都进不去
    （Errno 13 / WinError 5）。此时以退出码 3 停下并打印提权指引，除此之外不提供
    任何替代路径。

用法：
  python setup_runtime.py --check                              # 只判定状态，不安装（退出码见上）
  python setup_runtime.py --target D:/my-advisor               # 建 .venv + 装默认依赖
  python setup_runtime.py --target D:/my-advisor --with-opencc # 追加 opencc（繁转简）
  python setup_runtime.py --target D:/my-advisor --force        # 忽略已装状态，重装

依赖清单（B站视频总结 skill 所需）：
  - yt-dlp          下载音频
  - faster-whisper  离线转录（会再经 hf-mirror 拉 whisper 模型，模型缓存与依赖分离）
  - opencc-python-reimplemented（可选，繁转简）
"""
from __future__ import annotations

import argparse
import shutil
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

# 包名（pip 名）→ 导入模块名 的显式映射；探测「是否已装」靠 import 模块名。
PACKAGE_MODULE = {
    "yt-dlp": "yt_dlp",
    "faster-whisper": "faster_whisper",
    "opencc-python-reimplemented": "opencc",
}

# ── 退出码约定 ──
EXIT_READY = 0
EXIT_INSTALL_FAILED = 1
EXIT_PYTHON_MISSING = 2
EXIT_NEEDS_ESCALATION = 3

# 权限拒绝的特征串（跨中英文环境）；命中即判定「需提权」而非普通安装失败。
PERMISSION_MARKERS = (
    "errno 13", "winerror 5", "permission denied", "access is denied", "拒绝访问",
)


def _venv_python(target: Path) -> Path:
    return target / VENV_NAME / ("Scripts" if sys.platform == "win32" else "bin") / (
        "python.exe" if sys.platform == "win32" else "python"
    )


def _venv_pip(target: Path) -> Path:
    return target / VENV_NAME / ("Scripts" if sys.platform == "win32" else "bin") / (
        "pip.exe" if sys.platform == "win32" else "pip"
    )


def _run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess:
    print("  + " + " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=str(cwd), check=False)


def _run_capture(cmd: list[str], cwd: Path, echo: bool = False) -> tuple[int, str]:
    """运行子进程并捕获合并输出；echo=True 时边回显边捕获（保持进度可见）。

    捕获的全文用于失败后归类原因：权限拒绝 → 需提权（退出码 3），其它 → 环境问题。
    """
    if echo:
        print("  + " + " ".join(cmd), flush=True)
    buf: list[str] = []
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, errors="replace",
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        buf.append(line)
        if echo:
            print("  | " + line.rstrip(), flush=True)
    rc = proc.wait()
    return rc, "".join(buf)


def _is_permission_error(text: str) -> bool:
    """判断子进程输出是否属于权限拒绝（Errno 13 / WinError 5 等）。"""
    lowered = (text or "").lower()
    return any(m in lowered for m in PERMISSION_MARKERS)


def _tail_detail(text: str, lines: int = 6) -> str:
    """把捕获的底层报错压缩成「末尾 N 行」附在提示后，帮助定位真实原因。"""
    if not text or not text.strip():
        return ""
    tail = [ln for ln in text.splitlines() if ln.strip()][-lines:]
    return "\n  底层报错（末尾）：\n    " + "\n    ".join(tail)


def _print_escalation_hint() -> None:
    """权限拒绝时的确定性指引：提权重跑同一命令，交用户审批；禁止一切替代路径。"""
    print(
        "\n  ✗ 本次失败是「权限拒绝」（Errno 13 / WinError 5 / Access denied）。典型场景：\n"
        "    会话文件沙箱的受限令牌与 Python 在 Windows 上建受限临时目录的行为冲突，\n"
        "    venv / ensurepip / pip 都会因此失败（这不是 Python 缺组件，也不是网络问题）。\n"
        "    唯一正确处置（替代路径会同样失败，一律禁止：--target / monkeypatch / 换目录 / 反复重试）：\n"
        "    1) 用当前会话的提权机制（如 sandbox_permissions / danger-full-access）重跑\n"
        "       同一条命令，附一句理由，交用户审批；\n"
        "    2) 用户批准 → 继续走完流程；用户拒绝 → 停下如实转告，等用户指示。\n"
        "    若提权后仍失败，再按「环境问题（磁盘/权限/Python 异常）」转告用户处置。",
        flush=True,
    )


def probe_system_python() -> str | None:
    """探测系统里可用的 Python 解释器（不创建、不安装），返回可执行名或 None。

    按常见优先级依次探测：优先当前解释器，再 python3 / python。这是「是否需用户
    处置」的判定依据——无可用解释器即 python_missing，脚本直接上报，不做自动安装。
    """
    candidates: list[str] = [sys.executable]
    if sys.platform == "win32":
        candidates += ["py", "python", "python3"]
    else:
        candidates += ["python3", "python"]

    seen: set[str] = set()
    for c in candidates:
        if not c or c in seen:
            continue
        seen.add(c)
        # shutil.which 只在 PATH 里找；sys.executable 是绝对路径，直接视为可用。
        resolved = shutil.which(c) if (c not in ("py",) and not Path(c).is_file()) else c
        if not resolved:
            continue
        probe = subprocess.run(
            [resolved, "-c", "import sys"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
        )
        if probe.returncode == 0:
            return resolved
    return None


def _ensure_venv(target: Path, python_exe: str) -> None:
    """确保 .venv 就绪（有解释器且有 pip）；半成品（缺 pip，常见于上次被中断）自动补装。

    venv 模块失败时会吞掉 ensurepip 的真实报错（只给退出码 1），所以失败后用
    venv 解释器重跑 ensurepip 采集完整输出，把「权限拒绝（需提权）」和
    「Python 环境异常」区分开，各自走确定性的处置路径。
    """
    py = _venv_python(target)
    pip = _venv_pip(target)
    if py.exists() and pip.exists():
        print(f"  .venv 已就绪，跳过创建：{py}", flush=True)
        return
    if py.exists():
        print("  ! .venv 是半成品（有解释器、缺 pip，通常是上次安装被中断），重新补装…", flush=True)
    else:
        print(f"  [1/2] 用 {python_exe} -m venv 创建持久虚拟环境 .venv …", flush=True)
    _run([python_exe, "-m", "venv", str(target / VENV_NAME)], target)
    if pip.exists():
        print("  ✓ .venv 创建完成", flush=True)
        return

    # venv 没能把 pip 装进去：重跑 ensurepip 采集真实原因并归类。
    detail = ""
    if py.exists():
        _, detail = _run_capture([str(py), "-m", "ensurepip", "--default-pip"], target)
        if pip.exists():
            print("  ✓ .venv 补装完成", flush=True)
            return
        if _is_permission_error(detail):
            _print_escalation_hint()
            if detail.strip():
                print(_tail_detail(detail), flush=True)
            sys.exit(EXIT_NEEDS_ESCALATION)
    print(
        "  ✗ 创建 .venv 失败（pip 未装上）。这通常不是插件问题，而是当前 Python 环境异常：\n"
        "    1) 可能是精简版/商店版 Python（缺少 ensurepip 或 venv 模块）；\n"
        "    2) 或目标磁盘不可写。\n"
        "    请用户改用完整安装的 CPython 3.11+ 后重试，脚本不再自动兜底。"
        + _tail_detail(detail),
        flush=True,
    )
    sys.exit(EXIT_INSTALL_FAILED)


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
    """用 .venv pip 安装依赖；已装且非 force 则跳过。

    失败按原因分流：权限拒绝 → 退出码 3（提权重跑同一命令，交用户审批）；
    网络/编译/磁盘等 → 退出码 1（环境问题，转告用户，脚本不硬来）。
    """
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
    rc, out = _run_capture(base + to_install, target, echo=True)
    if rc != 0:
        if _is_permission_error(out):
            _print_escalation_hint()
            sys.exit(EXIT_NEEDS_ESCALATION)
        print(
            "\n  ✗ 依赖安装失败（原因见上方 pip 输出）。这是「环境/网络」问题，不是插件问题，需用户处置：\n"
            "    1) 网络不通 → 已用清华镜像，仍失败请自行配置代理/换镜像后重试；\n"
            "    2) 需要编译（如 ctranslate2 无 wheel）→ 确认 Python 版本有对应轮子或升级 pip；\n"
            "    3) 磁盘/权限 → 确认 .venv 目录可写。\n"
            "  脚本只报错、不自动兜底；请把以上原因与报错转告用户，待用户确认后再重试。",
            flush=True,
        )
        sys.exit(EXIT_INSTALL_FAILED)
    print("  ✓ 依赖安装完成", flush=True)


def _status(target: Path) -> dict:
    """无副作用地判定当前状态（三分法），返回结构化结果。"""
    py = probe_system_python()
    if py is None:
        return {"status": "python_missing", "deps": {}, "python": None}

    deps = {"yt-dlp": "ok" if _installed(target, "yt_dlp") else "missing",
            "faster-whisper": "ok" if _installed(target, "faster_whisper") else "missing"}
    ready = deps["yt-dlp"] == "ok" and deps["faster-whisper"] == "ok"
    return {"status": "ready" if ready else "deps_missing", "python": py, "deps": deps}


def _print_action(status: str, target: Path) -> None:
    """按状态输出「下一步该谁做」的确定性指引（agent / 用户）。"""
    if status == "python_missing":
        print(
            "# python_missing：未检测到可用的 Python 解释器（既不是插件能解决的环境问题）。\n"
            "# 需要用户处置：请安装完整版 CPython 3.11+（勾选“Add to PATH”），装好后重试。\n"
            "# 脚本不会自动安装 Python，也不会用其它方式兜底。",
            flush=True,
        )
    elif status == "deps_missing":
        print(
            f"# deps_missing：Python 可用，但工作区 {target / VENV_NAME} 缺 yt-dlp / faster-whisper。\n"
            "# 这是插件可自动修复的步骤：继续运行 setup_runtime.py（不带 --check）即可建 .venv 并装依赖。",
            flush=True,
        )
    else:
        print(f"# ready：工作区 {target / VENV_NAME} 依赖就绪，可直接运行 transcribe_video.py。", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="setup_runtime", description="工作区 Python 运行环境准备（建 .venv + 装依赖）")
    ap.add_argument("--target", default=".", help="目标工作区目录（默认当前目录）")
    ap.add_argument("--with-opencc", action="store_true", help="额外安装 opencc（繁转简，可选）")
    ap.add_argument("--check", action="store_true", help="只判定状态（python_missing/deps_missing/ready），不安装")
    ap.add_argument("--force", action="store_true", help="忽略已装状态，强制重装")
    args = ap.parse_args(argv)

    target = Path(args.target).resolve()
    target.mkdir(parents=True, exist_ok=True)

    packages = list(BASE_DEPENDENCIES)
    if args.with_opencc:
        packages.append(OPTIONAL_DEPENDENCIES["opencc"])

    if args.check:
        st = _status(target)
        _print_action(st["status"], target)
        # 退出码：python_missing=2（需用户）、deps_missing=1（可自动修）、ready=0。
        return {
            "ready": EXIT_READY,
            "deps_missing": EXIT_INSTALL_FAILED,
            "python_missing": EXIT_PYTHON_MISSING,
        }[st["status"]]

    # 非 --check：正式准备环境。先做 python_missing 门禁，缺 Python 直接停下（不自动装）。
    python_exe = probe_system_python()
    if python_exe is None:
        _print_action("python_missing", target)
        return EXIT_PYTHON_MISSING

    print(f"准备运行时环境：{target}", flush=True)
    _ensure_venv(target, python_exe)
    _install(target, packages, args.force)
    print(f"\n[完成] 运行时环境就绪：{target / VENV_NAME}", flush=True)
    print(f"  后续各技能脚本用该 python 运行：{_venv_python(target)}", flush=True)
    return EXIT_READY


if __name__ == "__main__":
    raise SystemExit(main())