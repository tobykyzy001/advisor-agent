"""B站视频下载音频并转录为文字稿的**自包含单文件工具脚本**。

这是「B站视频总结」的唯一可执行真源，随插件包分发，由宿主静态端点
`/plugins/advisor-agent/assets/workspace-init/transcribe_video.py` 提供给目标
工作区里的 agent 下载执行（与 workspace-init/init_workspace.py、w-bottom-screener
同一分发模式）。**不依赖 `.agents/skills`**，目标工作区无需任何 skill 文件。

用法：
    python transcribe_video.py "<B站视频URL/BV号/b23短链>" [--model small] [--force] \
        [--out output/videos] [--models output/videos/models]
    python transcribe_video.py --selfcheck    # 只检查依赖是否就绪，不下载不转录

依赖（装在工作区持久 `.venv`，用 `.venv/Scripts/python.exe` 运行本脚本）：
    # 一键建 .venv 并装依赖（由 workspace-init 的 setup_runtime.py 负责，先跑它）：
    python setup_runtime.py --target <工作区目录>
    # 或手工：
    python -m venv .venv && .venv/Scripts/pip install yt-dlp faster-whisper
    # 可选：装了 opencc 会把转录结果再转一版简体
    .venv/Scripts/pip install opencc-python-reimplemented

> 注意：本脚本**不装依赖、不建环境**。缺依赖时请先跑 workspace-init 的 setup_runtime.py
> 准备 `.venv`，再用 `.venv/Scripts/python.exe` 运行本脚本；不要临时 `pip install --target`
> 或 monkeypatch tempfile 去绕过系统 python 不可写——那会反复失败。

产物（默认写到 output/videos/<BV号>/，该目录已被 gitignore）：
    audio.*           原始音频（m4a，无需 ffmpeg 转 mp3）
    transcript.txt    带时间戳的文字稿（agent 分析读这个）
    transcript.json   结构化分段（start/text），供程序再加工
    meta.json         标题、UP主、时长等元数据
    ../models/         whisper 模型缓存目录（HF_HOME 指向此处，收拢到工作区）

除标准库外，脚本本身只依赖 yt-dlp 与 faster-whisper（导入时机在各自函数内，便于
`--selfcheck` 不装依赖也能跑）。所有网络适配（B站 412 反爬 / HF 镜像 / 关闭 Xet）已内置。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# faster-whisper 首次运行要从 HuggingFace 下载模型，国内直连常被掐断；
# 必须在导入 faster_whisper 之前切到 hf-mirror 镜像，并禁用 Xet 存储协议
#（Xet 会绕过镜像直连官方 CAS 服务器，同样会被拦）。
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")

# B站对非浏览器 UA 的下载请求直接返回 412，必须用浏览器头伪装；
# 只给 UA/Referer 触发较频繁，尽量补齐整套浏览器请求头。
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Referer": "https://www.bilibili.com",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    # B站近半年开始校验 buvid3（访客标识），缺失时部分视频直接 412；
    # buvid3 只是匿名访客 ID，放一个格式合法的随机值即可绕过。
    "Cookie": "buvid3=2A1B0A23-0000-4000-8000-2A1B0A23F77F11111infoc",
}

# whisper 听普通话容易自由发挥成繁体；给它一段简体中文开场白 + 领域词能明显拉回简体。
DEFAULT_PROMPT = "以下是简体中文的口述内容，涉及财经、股市、估值、美股、A股等专业话题。"


def normalize_url(raw: str) -> str:
    """允许直接传 BV 号或 b23.tv 短链，统一补全为完整 URL。"""
    raw = raw.strip()
    if raw.upper().startswith("BV"):
        return f"https://www.bilibili.com/video/{raw}"
    return raw


def check_dependencies() -> dict[str, str]:
    """检查第三方依赖是否可用，返回 {模块: 状态}。不抛异常。"""
    deps: dict[str, str] = {}
    for mod in ("yt_dlp", "faster_whisper"):
        try:
            __import__(mod)
            deps[mod] = "ok"
        except ImportError:
            deps[mod] = "missing"
    try:
        import opencc  # noqa: F401

        deps["opencc"] = "ok"
    except ImportError:
        deps["opencc"] = "missing"
    return deps


def download_audio(url: str, out_root: Path) -> tuple[Path, dict]:
    """下载音频与元数据。先探测拿到 BV 号，再按 BV 号建目录下载。"""
    import yt_dlp

    base_opts = {"http_headers": BROWSER_HEADERS, "noplaylist": True, "quiet": True}
    with yt_dlp.YoutubeDL(base_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    video_dir = out_root / info["id"]
    video_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "bvid": info.get("id"),
        "title": info.get("title"),
        "uploader": info.get("uploader") or "",
        "duration": info.get("duration"),
        "webpage_url": info.get("webpage_url"),
    }

    existing = sorted(video_dir.glob("audio.*"))
    if existing:
        return existing[0], meta

    opts = {**base_opts, "format": "bestaudio/best", "outtmpl": str(video_dir / "audio.%(ext)s")}
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(meta["webpage_url"] or url, download=True)

    matches = sorted(video_dir.glob("audio.*"))
    if not matches:
        sys.exit(f"下载失败：{video_dir} 下没有音频文件")
    return matches[0], meta


# 各档模型体积与速度预期（cpu int8，普通话，粗略估值），用于打印时长提示。
MODEL_COSTS = {
    "tiny": {"size_mb": 75, "speed": "约 1~2 倍音频时长"},
    "base": {"size_mb": 145, "speed": "约 0.6~1.2 倍音频时长"},
    "small": {"size_mb": 460, "speed": "约 0.4~0.8 倍音频时长"},
    "medium": {"size_mb": 1500, "speed": "约 0.2~0.4 倍音频时长"},
}


def transcribe(audio_path: Path, model_size: str, prompt: str) -> list[dict]:
    """faster-whisper 转录；首次运行会走镜像下载模型（small 约 460MB）。"""
    from faster_whisper import WhisperModel

    print(f"  [2/4] 加载 whisper 模型 {model_size}（cpu int8）…", flush=True)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    print(f"  [3/4] 转录中（VAD 过滤；长视频请耐心等待）…", flush=True)
    segments, _ = model.transcribe(
        str(audio_path), language="zh", initial_prompt=prompt, vad_filter=True
    )
    return [{"start": round(s.start, 1), "text": s.text.strip()} for s in segments]


def to_simplified(text: str) -> str:
    """装了 opencc 就繁转简，没装原样返回（不影响主流程）。"""
    try:
        from opencc import OpenCC

        return OpenCC("t2s").convert(text)
    except ImportError:
        return text


def main() -> None:
    parser = argparse.ArgumentParser(description="B站视频音频下载 + whisper 转录（自包含）")
    parser.add_argument("video", nargs="?", help="B站视频 URL、b23.tv 短链或 BV 号")
    parser.add_argument(
        "--model",
        default="small",
        help="whisper 模型：tiny(最快)/base/small(默认,更准)/medium；越小越快、语音越清晰可越小模型",
    )
    parser.add_argument("--out", default="output/videos", help="输出根目录，默认 output/videos")
    parser.add_argument(
        "--models",
        default="output/videos/models",
        help="whisper 模型缓存目录（设 HF_HOME 指向此处，默认 output/videos/models）",
    )
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="转录引导词（影响简体/领域词识别）")
    parser.add_argument("--force", action="store_true", help="忽略已有音频/文字稿，重新跑全流程")
    parser.add_argument("--selfcheck", action="store_true", help="只检查依赖与环境，不下载不转录")
    args = parser.parse_args()

    # 模型缓存固定在工作区（收拢模型）：必须在导入 faster_whisper 前设置 HF_HOME。
    # 用户显式设过 HF_HOME 则尊重，不覆盖。
    os.environ.setdefault("HF_HOME", str(Path(args.models).resolve()))

    if args.selfcheck:
        deps = check_dependencies()
        ok = all(v == "ok" for v in [deps["yt_dlp"], deps["faster_whisper"]])
        hint = ""
        if not ok:
            hint = (
                "依赖未就绪：请先用 workspace-init 的 setup_runtime.py 建工作区 .venv 并装依赖"
                "（python setup_runtime.py --target <工作区>），再用 .venv/Scripts/python.exe 运行本脚本。"
            )
        print(json.dumps({
            "deps": deps,
            "hf_endpoint": os.environ.get("HF_ENDPOINT"),
            "hf_home": os.environ.get("HF_HOME"),
            "models_dir_exists": Path(args.models).resolve().is_dir(),
            "ready": ok,
            "hint": hint,
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if ok else 1)

    if not args.video:
        parser.error("缺少视频参数：请传 B站视频 URL / BV 号 / b23.tv 短链")

    out_root = Path(args.out)
    url = normalize_url(args.video)
    print(f"  [1/4] 解析并下载音频（B站反爬已内置）…", flush=True)
    audio_path, meta = download_audio(url, out_root)
    video_dir = audio_path.parent
    transcript_txt = video_dir / "transcript.txt"

    (video_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.force or not transcript_txt.exists():
        cost = MODEL_COSTS.get(args.model)
        size_txt = f"约 {cost['size_mb']}MB" if cost else "体积未知"
        print(
            f"准备转录：{meta['title']}（模型 {args.model}，{size_txt}）\n"
            f"  首次运行将从镜像下载模型（联网下载，耗时视带宽而定）；"
            f"之后复用本地缓存不再下载。\n"
            f"  转录时长约 {cost['speed'] if cost else '与音频时长相当'}，请耐心等待。",
            flush=True,
        )
        segments = transcribe(audio_path, args.model, args.prompt)
        (video_dir / "transcript.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [f"[{s['start']:6.1f}s] {to_simplified(s['text'])}" for s in segments]
        transcript_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"  [4/4] 转录完成，共 {len(lines)} 段", flush=True)
    else:
        print("  已有文字稿，跳过转录（--force 可重跑）", flush=True)

    print(f"\n完成：{meta['title']} | UP主：{meta['uploader']} | 时长：{meta['duration']}s")
    print(f"文字稿：{transcript_txt}")


if __name__ == "__main__":
    main()