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

import { createRequire } from 'node:module'
import { createTushareMcpBridge } from './mcp-tushare.js'

const require = createRequire(import.meta.url)

export const name = 'advisor-agent'
export const inject = ['settings']
export const CONFIG_ENDPOINT = '/plugins/advisor-agent/config'

let Schema = null
try {
  Schema = require('@deepseek-ai/schemastery')
} catch (e) {
  // schemastery 未安装：跳过宿主配置 schema，仅保留 /config 端点（值取默认）。
}

// 默认启用的技能固定来自 lib/client.js 内联注册表 ADVISOR_SKILLS 的 id 集合；若丢失，用最小兜底。
export const DEFAULT_ENABLED_SKILLS = ['stock-valuation', 'copy-trade']

const defaults = Object.freeze({
  enabled: true,
  enabledSkills: DEFAULT_ENABLED_SKILLS,
  defaultTarget: 'new',
  tushareMcpUrl: '',
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
    tushareMcpUrl: typeof config.tushareMcpUrl === 'string' ? config.tushareMcpUrl : '',
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
      tushareMcpUrl: Schema.string().default('')
        .role('secret')
        .description('tushare MCP 完整 URL（含 token，形如 http://…/dingall?token=…）；保存后立即生效，留空则断开'),
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
    })
    // 在 tools 语境下建 tushare MCP 连接桥，并在 URL 变化时热切换（立即生效）。
    ctx.inject(['tools'], (toolsCtx) => {
      const bridge = createTushareMcpBridge(toolsCtx)
      toolsCtx.effect(() => () => bridge.dispose(), 'advisor-agent: tushare mcp bridge')

      // 首次挂载：用当前配置里的 URL 连接一次（若已填）。
      let lastUrl = settings.get().tushareMcpUrl
      if (typeof lastUrl === 'string' && lastUrl.trim() !== '') {
        void bridge.applyUrl(lastUrl)
      }

      // 订阅设置变化：URL 改变 → 立即重连（保存即生效）。
      const offWatch = settings.watch((next, prev) => {
        const nextUrl = next?.tushareMcpUrl
        const prevUrl = prev?.tushareMcpUrl
        if (nextUrl === prevUrl) return
        void bridge.applyUrl(typeof nextUrl === 'string' ? nextUrl : '')
      })
      toolsCtx.effect(() => offWatch, 'advisor-agent: tushare mcp url watch')
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