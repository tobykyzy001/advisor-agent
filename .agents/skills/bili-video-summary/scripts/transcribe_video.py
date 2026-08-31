"""B站视频下载音频并转录为文字稿的工具脚本。

用法：
    python transcribe_video.py <B站视频URL或BV号> [--model small] [--force]

依赖（装在系统 python 即可，项目 .venv 不需要）：
    pip install yt-dlp faster-whisper
    # 可选：装了 opencc 会把转录结果再转一版简体
    pip install opencc-python-reimplemented

产物（默认写到 output/videos/<BV号>/，该目录已被 gitignore）：
    audio.*           原始音频（m4a，无需 ffmpeg 转 mp3）
    transcript.txt    带时间戳的文字稿
    transcript.json   结构化分段（start/text），供程序再加工
    meta.json         标题、UP主、时长等元数据
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


def transcribe(audio_path: Path, model_size: str, prompt: str) -> list[dict]:
    """faster-whisper 转录；首次运行会走镜像下载模型（small 约 460MB）。"""
    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
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
    parser = argparse.ArgumentParser(description="B站视频音频下载 + whisper 转录")
    parser.add_argument("video", help="B站视频 URL、b23.tv 短链或 BV 号")
    parser.add_argument("--model", default="small", help="whisper 模型，默认 small（首次下载约460MB）")
    parser.add_argument("--out", default="output/videos", help="输出根目录，默认 output/videos")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="转录引导词（影响简体/领域词识别）")
    parser.add_argument("--force", action="store_true", help="忽略已有音频/文字稿，重新跑全流程")
    args = parser.parse_args()

    out_root = Path(args.out)
    url = normalize_url(args.video)
    audio_path, meta = download_audio(url, out_root)
    video_dir = audio_path.parent
    transcript_txt = video_dir / "transcript.txt"

    (video_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.force or not transcript_txt.exists():
        print(f"转录中：{meta['title']}（模型 {args.model}，首次需下载模型）", flush=True)
        segments = transcribe(audio_path, args.model, args.prompt)
        (video_dir / "transcript.json").write_text(
            json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        lines = [f"[{s['start']:6.1f}s] {to_simplified(s['text'])}" for s in segments]
        transcript_txt.write_text("\n".join(lines) + "\n", encoding="utf-8")
    else:
        print("已有文字稿，跳过转录（--force 可重跑）")

    print(f"\n完成：{meta['title']} | UP主：{meta['uploader']} | 时长：{meta['duration']}s")
    print(f"文字稿：{transcript_txt}")


if __name__ == "__main__":
    main()
