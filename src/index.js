// advisor-agent 宿主侧入口（node 侧）。
//
// 职责分两半：
//  1. 注册可持久化的配置 schema（enable 总开关 / 每技能开关 / 默认目标会话），
//     通过一个 /config 端点暴露给浏览器面板读写（参照官方 dsh-dafeiyu 的模式）。
//  2. 前端所有 UI（侧边栏入口 + 技能表单 + 投递）都在 lib/client.js，
//     本文件不做任何进程/端点外的重活，保持 payload 最小、可开源。
//
// 配置项：
//   enabled        总开关：关闭后侧边栏不出现「投研工具」入口
//   enabledSkills  启用的 skill id 列表（对应 lib/client.js 内联注册表 ADVISOR_SKILLS）
//   defaultTarget  点「运行」的默认投递目标：'new'（新开会话，默认）| 'current'（当前会话）
//                  表单内可临时覆盖。
//   tushareToken   tushare pro token：配置后由面板随工具指令以 --token 参数传给
//                  fetch_quotes.py（脚本侧最高优先级）；留空回退环境变量 TUSHARE_TOKEN
//                  / 工作区 .env。仅存于宿主设置，不入仓库。

import { createRequire } from 'node:module'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

const require = createRequire(import.meta.url)

export const name = 'advisor-agent'
export const inject = ['settings']
export const CONFIG_ENDPOINT = '/plugins/advisor-agent/config'
// workspace-init 脚本的静态下载端点：脚本随包分发（src/workspace-init/init_workspace.py 在 files 内），
// 供目标工作区里的 agent 在运行时下载后执行。纯插件安装（无仓库 .agents/skills）也能拿到脚本。
export const ASSET_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/init_workspace.py'
const ASSET_PATH = fileURLToPath(new URL('./workspace-init/init_workspace.py', import.meta.url))
// W底筛选自包含脚本的静态分发端点（与 workspace-init 同模式）：
// 纯标准库、零 quantify 依赖，目标工作区 agent 下载后直接 python 运行。
export const WB_SCREEN_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py'
const WB_SCREEN_PATH = fileURLToPath(new URL('./workspace-init/w_bottom_screen.py', import.meta.url))
// 中期动量排名自包含脚本的静态分发端点（与 w-bottom-screener 同模式）：
// 纯标准库、零 quantify 依赖，目标工作区 agent 下载后直接 python 运行，一次性出目标组合。
export const MOMENTUM_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/momentum_strategy.py'
const MOMENTUM_PATH = fileURLToPath(new URL('./workspace-init/momentum_strategy.py', import.meta.url))
// B站视频转录（自包含脚本）的静态分发端点：目标工作区 agent 下载后直接 python 运行，
// 不依赖 .agents/skills。脚本真源 src/workspace-init/transcribe_video.py。
export const TRANSCRIBE_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/transcribe_video.py'
const TRANSCRIBE_PATH = fileURLToPath(new URL('./workspace-init/transcribe_video.py', import.meta.url))
// 运行时环境准备（自包含脚本）的静态分发端点：在目标工作区建持久 .venv 并装
// yt-dlp/faster-whisper 等依赖。脚本真源 src/workspace-init/setup_runtime.py ——
// 由 workspace-init 职责承载，bili-video-summary 等依赖第三方库的技能统一走它。
export const SETUP_RUNTIME_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/setup_runtime.py'
const SETUP_RUNTIME_PATH = fileURLToPath(new URL('./workspace-init/setup_runtime.py', import.meta.url))
// 观察仓清单管理（自包含脚本）的静态分发端点（与 w-bottom-screener 同模式）：
// 纯标准库、零 quantify 依赖，目标工作区 agent 下载后直接 python 运行。
// watchlist-manager 技能的脚本真源，供 stock-valuation / copy-trade 等流程委托调用。
export const WATCHLIST_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/manage_watchlist.py'
const WATCHLIST_PATH = fileURLToPath(new URL('./workspace-init/manage_watchlist.py', import.meta.url))
// 统一行情取数 CLI（自包含脚本）的静态分发端点：直连 tushare pro REST API，
// 对照本地 CSV 行情库 output/quotes-store/ 增量刷库 / 出估值快照。
// w-bottom-screener / momentum-rotation 的取数步骤，以及估值、持仓复核、
// 论据核查等即取即用场景统一走它（fetch_quotes.py --snapshot）。
export const FETCH_QUOTES_ENDPOINT = '/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py'
const FETCH_QUOTES_PATH = fileURLToPath(new URL('./workspace-init/fetch_quotes.py', import.meta.url))
// 飞神交易逻辑（非脚本型方法论资产）的静态分发端点：目标工作区可能是空目录，
// 会话里没有 .agents/skills，因此不能靠 skill 调用；下载这三份 Markdown 后按其执行。
export const FEISHEN_SKILL_ENDPOINT = '/plugins/advisor-agent/assets/feishen-trading-logic/SKILL.md'
export const FEISHEN_RULEBOOK_ENDPOINT = '/plugins/advisor-agent/assets/feishen-trading-logic/feishen-rulebook.md'
export const FEISHEN_ALIASES_ENDPOINT = '/plugins/advisor-agent/assets/feishen-trading-logic/feishen-aliases.md'
const FEISHEN_SKILL_PATH = fileURLToPath(new URL('../.agents/skills/feishen-trading-logic/SKILL.md', import.meta.url))
const FEISHEN_RULEBOOK_PATH = fileURLToPath(new URL('../.agents/skills/feishen-trading-logic/references/feishen-rulebook.md', import.meta.url))
const FEISHEN_ALIASES_PATH = fileURLToPath(new URL('../.agents/skills/feishen-trading-logic/references/feishen-aliases.md', import.meta.url))
const FEISHEN_ASSETS = [
  [FEISHEN_SKILL_ENDPOINT, FEISHEN_SKILL_PATH, 'feishen-trading-logic/SKILL.md'],
  [FEISHEN_RULEBOOK_ENDPOINT, FEISHEN_RULEBOOK_PATH, 'feishen-trading-logic/feishen-rulebook.md'],
  [FEISHEN_ALIASES_ENDPOINT, FEISHEN_ALIASES_PATH, 'feishen-trading-logic/feishen-aliases.md'],
]

let Schema = null
try {
  Schema = require('@deepseek-ai/schemastery')
} catch (e) {
  // schemastery 未安装：跳过宿主配置 schema，仅保留 /config 端点（值取默认）。
}

// 默认启用的技能固定来自 lib/client.js 内联注册表 ADVISOR_SKILLS 的 id 集合；若丢失，用最小兜底。
export const DEFAULT_ENABLED_SKILLS = ['stock-valuation', 'copy-trade', 'workspace-init', 'w-bottom-screener', 'momentum-rotation', 'bili-video-summary', 'feishen-trading-logic']

const defaults = Object.freeze({
  enabled: true,
  enabledSkills: DEFAULT_ENABLED_SKILLS,
  defaultTarget: 'new',
  tushareToken: '',
})

function publicConfig(config = {}) {
  return {
    enabled: config.enabled !== false,
    // 升级兼容：把「新默认启用的技能」并入已保存列表，避免旧 settings 里只有旧默认，
    // 导致新增技能（如 copy-trade）被 enabledSkills 过滤掉、入口消失。
    enabledSkills: (() => {
      const saved = Array.isArray(config.enabledSkills) ? config.enabledSkills : defaults.enabledSkills.slice()
      const merged = new Set(saved)
      for (const id of defaults.enabledSkills) merged.add(id)
      return [...merged]
    })(),
    defaultTarget: config.defaultTarget === 'current' ? 'current' : 'new',
    tushareToken: typeof config.tushareToken === 'string' ? config.tushareToken : '',
  }
}

function localSettingsScope(value) {
  return {
    get: () => value,
    watch: () => () => {},
    update: async () => {},
  }
}

export const Config = Schema
  ? Schema.object({
      enabled: Schema.boolean().default(true).description('启用投研工具入口（关闭后侧边栏不显示）'),
      enabledSkills: Schema.array(Schema.string())
        .default(DEFAULT_ENABLED_SKILLS)
        .role('list')
        .description('在投研工具面板中启用的技能'),
      defaultTarget: Schema.union([
        Schema.const('new').description('新开会话'),
        Schema.const('current').description('当前会话'),
      ]).default('new').description('点击「运行」后默认把技能指令投递到哪里（表单内可临时切换）'),
      tushareToken: Schema.string().default('')
        .description('tushare pro token：配置后随工具指令以 --token 参数传给 fetch_quotes.py；'
          + '留空回退环境变量 TUSHARE_TOKEN / 工作区 .env'),
    }).description('投研工具：以表单化方式调用投顾技能（个股估值等）')
  : null

// ── /config 端点 ──────────────────────────────────────────────────────────────
// 前端只允许本地回环访问（与 dsh-dafeiyu 一致）：防跨源、防远程篡改配置。

function jsonResponse(res, status, body) {
  const payload = JSON.stringify(body)
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'cache-control': 'no-store',
    'content-length': Buffer.byteLength(payload),
  })
  res.end(payload)
}

function isLoopback(address) {
  return address === '127.0.0.1' || address === '::1' || address === '::ffff:127.0.0.1'
}

async function readPatch(req) {
  const chunks = []
  let bytes = 0
  for await (const chunk of req) {
    bytes += chunk.length
    if (bytes > 8192) throw new Error('request body is too large')
    chunks.push(chunk)
  }
  const value = JSON.parse(Buffer.concat(chunks).toString('utf8'))
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('patch must be an object')
  }
  const allowed = new Set(Object.keys(defaults))
  if (Object.keys(value).some((key) => !allowed.has(key))) {
    throw new Error('patch contains an unknown setting')
  }
  return value
}

export function createConfigHandler(settings) {
  return async (req, res) => {
    if (!isLoopback(req.socket?.remoteAddress)) {
      jsonResponse(res, 403, { error: 'local access only' })
      return
    }
    const origin = req.headers?.origin
    if (origin) {
      let originHost
      try { originHost = new URL(origin).host } catch {}
      if (!originHost || originHost !== req.headers.host) {
        jsonResponse(res, 403, { error: 'origin mismatch' })
        return
      }
    }
    if (req.method === 'GET') {
      jsonResponse(res, 200, settings.get())
      return
    }
    if (req.method !== 'PATCH') {
      jsonResponse(res, 405, { error: 'method not allowed' })
      return
    }
    try {
      await settings.update(await readPatch(req))
      jsonResponse(res, 200, settings.get())
    } catch (error) {
      jsonResponse(res, 400, { error: error instanceof Error ? error.message : String(error) })
    }
  }
}

// 静态资产下载端点（泛化）：仅回环、仅 GET，返回随包分发的脚本/方法论文本。
function makeAssetHandler(filePath, label, contentType = 'text/x-python; charset=utf-8') {
  const cached = (() => {
    try {
      return readFileSync(filePath, 'utf8')
    } catch (e) {
      return null
    }
  })()
  return async (req, res) => {
    if (!isLoopback(req.socket?.remoteAddress)) {
      jsonResponse(res, 403, { error: 'local access only' })
      return
    }
    if (req.method !== 'GET') {
      jsonResponse(res, 405, { error: 'method not allowed' })
      return
    }
    if (cached === null) {
      jsonResponse(res, 404, { error: `asset not found: ${label}` })
      return
    }
    res.writeHead(200, {
      'content-type': contentType,
      'cache-control': 'no-store',
      'content-length': Buffer.byteLength(cached),
    })
    res.end(cached)
  }
}

function mount(ctx, config = {}) {
  const logger = ctx.logger ?? console
  const base = publicConfig(config)
  const settings = ctx.settings?.register?.('advisor-agent', Config, {
    base,
    applies: 'live',
  }) ?? localSettingsScope(base)

  if (typeof ctx.inject === 'function') {
    // 在 webServer 语境下挂本地 /config 端点（进回环校验）。
    ctx.inject(['webServer'], (httpCtx) => {
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: CONFIG_ENDPOINT,
          handler: createConfigHandler(settings),
        }),
        'advisor-agent: local config endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: ASSET_ENDPOINT,
          handler: makeAssetHandler(ASSET_PATH, 'workspace-init/init_workspace.py'),
        }),
        'advisor-agent: workspace-init asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: WB_SCREEN_ENDPOINT,
          handler: makeAssetHandler(WB_SCREEN_PATH, 'workspace-init/w_bottom_screen.py'),
        }),
        'advisor-agent: w-bottom-screener asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: MOMENTUM_ENDPOINT,
          handler: makeAssetHandler(MOMENTUM_PATH, 'workspace-init/momentum_strategy.py'),
        }),
        'advisor-agent: momentum-rotation asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: TRANSCRIBE_ENDPOINT,
          handler: makeAssetHandler(TRANSCRIBE_PATH, 'workspace-init/transcribe_video.py'),
        }),
        'advisor-agent: bili-video-summary transcript asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: SETUP_RUNTIME_ENDPOINT,
          handler: makeAssetHandler(SETUP_RUNTIME_PATH, 'workspace-init/setup_runtime.py'),
        }),
        'advisor-agent: setup-runtime asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: WATCHLIST_ENDPOINT,
          handler: makeAssetHandler(WATCHLIST_PATH, 'workspace-init/manage_watchlist.py'),
        }),
        'advisor-agent: watchlist-manager asset endpoint',
      )
      httpCtx.effect(
        () => httpCtx.webServer.register({
          kind: 'exact',
          path: FETCH_QUOTES_ENDPOINT,
          handler: makeAssetHandler(FETCH_QUOTES_PATH, 'workspace-init/fetch_quotes.py'),
        }),
        'advisor-agent: fetch-quotes asset endpoint',
      )
      for (const [path, filePath, label] of FEISHEN_ASSETS) {
        httpCtx.effect(
          () => httpCtx.webServer.register({
            kind: 'exact',
            path,
            handler: makeAssetHandler(filePath, label, 'text/markdown; charset=utf-8'),
          }),
          `advisor-agent: ${label} asset endpoint`,
        )
      }
    })
  } else {
    logger.warn?.('advisor-agent: no ctx.inject, config endpoint not mounted')
  }

  ctx.effect(() => () => {})
}

export function apply(ctx, config = {}) {
  if (typeof ctx.inject === 'function') {
    ctx.inject(['settings'], (settingsCtx) => mount(settingsCtx, config))
    return
  }
  mount(ctx, config)
}