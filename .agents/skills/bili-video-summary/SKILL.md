---
name: bili-video-summary
description: B站视频的「拉取→转录→分析」流水线。当用户发来 bilibili 视频链接（bilibili.com/video/BV…、b23.tv 短链，或直接给一个 BV 号），要求「分析/总结/转录/提炼这个视频」「帮我看看这个 UP 主讲了什么」「这个视频观点靠不靠谱/帮我核查里面的论据」，或拿到的是音频/视频内容需要听懂再评价时触发。覆盖 PC客户端/网页都没有字幕或 AI 总结的场景——本技能自己下载音频、离线 whisper 转录，不依赖 B站字幕。
---

# bili-video-summary：B站视频转录与分析

核心思路：B站多数视频**没有 CC 字幕**，AI 视频总结也只在网页/APP 且非全量覆盖。所以不要去抓字幕接口（多半是空的），直接走「yt-dlp 下音频 → faster-whisper 离线转录 → agent 读文字稿做分析」这条自己掌控的链路。

## 使用方式

用**系统 python** 运行脚本（不是项目 `.venv`——yt-dlp / faster-whisper 装在系统环境；这与 daily-update 的约定一致）：

```bash
python .agents/skills/bili-video-summary/scripts/transcribe_video.py "<视频URL或BV号>"
# 可选：--model small  --force（已有文字稿但想重跑） --prompt "自定义转录引导词"
```

产物在 `output/videos/<BV号>/`（`output/` 整目录已被 gitignore，不入库）：

| 文件 | 内容 |
|---|---|
| `transcript.txt` | 带时间戳的文字稿，**agent 分析读这个** |
| `transcript.json` | 结构化分段，供程序再加工 |
| `meta.json` | 标题 / UP主 / 时长 |
| `audio.*` | 原始音频（m4a；特意不转 mp3，免去装 ffmpeg） |

脚本已内置四处环境适配，无需用户手工处理：

1. **B站 412 反爬**：浏览器 UA + Referer + 匿名 `buvid3` cookie。仅 UA/Referer 已不够——B站近半年开始校验访客标识 `buvid3`，缺失时部分视频直接返回 412（连元数据都拿不到）；脚本在请求头里放一个格式合法的随机 buvid3 即可通过，无需用户真实 cookie。若再次 412，优先检查是不是 B站又收紧了校验（如要求真实登录态 cookie），再考虑 `yt-dlp --cookies-from-browser`。
2. **HuggingFace 国内不可达**：自动切 `hf-mirror.com` 镜像。
3. **Xet 协议绕过镜像**：自动禁用（Xet 会绕过镜像直连官方 CAS 服务器，同样会被拦）。

首次运行会下载 whisper 模型（small 约 460MB，之后有缓存）。

缺依赖时提示用户：`pip install yt-dlp faster-whisper`（可选 `opencc-python-reimplemented`，装了会自动把繁体转简）。

## 分析流程

拿起文字稿后，按下面的顺序产出结论（财经观点类视频是本项目主场景）：

1. **还原听错**。whisper 对普通话常出谐音错和繁体（脚本会用引导词 + opencc 缓解但不会根除）。遇到人名、机构、术语明显不通时，先按语境纠正再分析，例如：「臥石先生→沃什」「費城的暴跌→费城半导体指数暴跌」「幾方→己方」。
2. **内容摘要**：一段话讲清这个视频在说什么、结论是什么。
3. **核心论点链**：把论证拆成「前提 → 推理 → 结论」的链条，标注每一步是事实还是推测。
4. **论据核查表**：逐条列出可验证论据，打「属实 / 基本属实 / 夸大或错误 / 无法验证」。A股、港股、宏观数据用 tushare MCP 核（字段口径见 `stock-valuation/references/data-source.md`）；美股等接口常无权限，如实标注「无法验证」并说明用户可以怎么自查，不要硬编数据。
5. **方法论评价与立场提示**：指出论证硬伤（概率连乘当必然、不可证伪、样本偏差）、UP 主的立场背景（若有明显倾向）。
6. **免责收尾**：以上仅供研究参考，不构成投资建议。

## 与其它技能的分工

- 本技能只负责**拿到文字稿**；分析中需要个股估值、行业景气判断时，方法分别复用 `stock-valuation`、`prosperity-analysis`，数据走 tushare MCP，口径保持一致。
- 不属于 `daily-update` 管理的知识资产（它是一次性取数工具，无更新周期）；产物也不入库，无需登记。

已用 `BV1uEtH6mEY6`（李大霄《提醒11月3日对美股的风险》，4分20秒无字幕视频）实测跑通全流程；`BV19i4X6kEFx`（史诗级韭菜《本周经济数据分析》，约10分钟）验证了 buvid3 cookie 对 412 的修复。
