// advisor-agent 的 tushare MCP 连接桥（node 侧）。
//
// 背景：DSH 自带的 @deepseek-ai/dsh-mcp-client 把「连接 + 工具注册」绑定在插件激活的
// 一次性 effect 上，配置只能写死在 cordis.yml，改 URL 要靠 HMR（dispose 旧实例再建新实例）
// 才重连，无法做到「用户在设置界面改 URL → 立即生效」。
//
// 本模块在 advisor-agent 内自建一个精简的 MCP Streamable HTTP 连接监督器：
//   - 复用 @modelcontextprotocol/sdk 的 Client + StreamableHTTPClientTransport（与 mcp-client 同款）；
//   - 连接成功后 listTools() 一趟，把每个 MCP 工具以公开名 `mcp__<serverName>__<rawName>`
//     注册到 ctx.tools（公开名规范化规则与 mcp-client 一致，见下）；
//   - applyUrl(url) 热切换：收到新 URL 时先卸旧代、再连新 URL、重新 sync 工具。工具公开名
//     不变（serverName 固定），所以对 agent 无感、不会重复注册。
//
// 工具名规则（与 mcp-client 对齐，见其 README「Tool naming」）：
//   - 公开名 = `mcp__<serverName>__<rawName>`；
//   - 若替换非法字符或截断到 64 字符改变了名字，追加 12 位 SHA-256(serverName\0rawName)，
//     保证不同 MCP 工具绝不折叠成同名。此处用 createHash('sha256') 复刻。
//
// 本模块只做「连接 + 工具桥」，不含任何 tushare 业务逻辑；token 存在用户填的完整 URL 里，
// 不落盘、不入库、不打印。

import { createHash } from 'node:crypto'
import { createRequire } from 'node:module'

const require = createRequire(import.meta.url)

// 与 mcp-client 相同的 serverName：工具公开名固定为 mcp__tushareMcp__<rawName>，
// 与 .agents/skills/stock-valuation/references/data-source.md 及 AGENTS.md 记录一致。
const SERVER_NAME = 'tushareMcp'
const MAX_PUBLIC_NAME_LENGTH = 64
const INVALID_NAME_CHARS = /[^A-Za-z0-9_-]/g
const HASH_LENGTH = 12
const DEFAULT_TOOL_CALL_TIMEOUT_MS = 60_000

// 惰性加载 MCP SDK（ESM-only；require 走 CJS 导出）。未安装时返回 null。
function loadSdk() {
  try {
    const { Client } = require('@modelcontextprotocol/sdk/client/index.js')
    const { StreamableHTTPClientTransport } = require('@modelcontextprotocol/sdk/client/streamableHttp.js')
    return { Client, StreamableHTTPClientTransport }
  } catch (e) {
    return null
  }
}

// 复刻 mcp-client 的 publicToolName：稳定、纯函数。
function publicToolName(rawName) {
  const joined = `mcp__${SERVER_NAME}__${rawName}`
  const normalized = joined.replace(INVALID_NAME_CHARS, '_')
  if (normalized === joined && normalized.length <= MAX_PUBLIC_NAME_LENGTH) return normalized
  const hash = createHash('sha256').update(`${SERVER_NAME}\0${rawName}`).digest('hex').slice(0, HASH_LENGTH)
  return `${normalized.slice(0, MAX_PUBLIC_NAME_LENGTH - HASH_LENGTH - 1)}_${hash}`
}

// 从 MCP 调用结果里按顺序抽文本（对齐 mcp-client 的 extractText）。
function extractText(content) {
  if (!Array.isArray(content)) return content != null ? JSON.stringify(content) : '(no output)'
  return content
    .map((block) => {
      if (block && typeof block === 'object') {
        if (block.type === 'text' && typeof block.text === 'string') return block.text
        if (block.type === 'image') return '[image omitted]'
        if (block.type === 'resource_link') return `Resource link: ${block.name ?? ''} (${block.uri ?? ''})`
        return '[unsupported MCP block]'
      }
      return String(block)
    })
    .join('\n')
}

export function createTushareMcpBridge(ctx) {
  const sdk = loadSdk()
  const logger = ctx.logger ?? console
  const tools = ctx.tools ?? null

  // 可用性判定：未装 SDK 或没有 tools 服务时，桥接静默禁用（不影响插件其余功能）。
  if (!sdk || !tools || typeof tools.register !== 'function') {
    if (!sdk) logger.warn?.('advisor-agent: @modelcontextprotocol/sdk 未安装，tushare MCP 桥接已禁用')
    if (!tools) logger.warn?.('advisor-agent: 无 ctx.tools 服务，tushare MCP 桥接已禁用')
    return { applyUrl: async () => {}, dispose: async () => {} }
  }

  const { Client, StreamableHTTPClientTransport } = sdk

  let disposed = false
  let client = null // 当前 Client 实例
  let disposers = new Map() // 已注册工具名 -> unregister 函数
  let sequence = 0 // 连接代数，防止迟到的旧连接覆盖新连接
  let chain = Promise.resolve() // 串行化 applyUrl，避免并发热切竞争

  // 关闭当前一代（幂等）。
  async function teardownCurrent() {
    const current = client
    client = null
    const currentDisposers = disposers
    disposers = new Map()
    for (const dispose of currentDisposers.values()) {
      try { await dispose() } catch (e) {}
    }
    if (current) {
      try { await current.close() } catch (e) {}
    }
  }

  // 执行一次 MCP 工具调用（被某个工具 definition 的 execute 绑定）。
  async function execTool(clientInstance, rawName, args, exec) {
    const signal = exec?.signal
    const controller = new AbortController()
    const onAbort = () => controller.abort(signal?.reason)
    if (signal) signal.addEventListener('abort', onAbort, { once: true })
    const timer = setTimeout(() => controller.abort(new Error('tushare MCP 调用超时')), DEFAULT_TOOL_CALL_TIMEOUT_MS)
    timer.unref?.()
    try {
      const result = await clientInstance.callTool(
        { name: rawName, arguments: args && typeof args === 'object' ? args : {} },
        undefined,
        { signal: controller.signal },
      )
      if (result.isError === true) throw new Error(extractText(result.content))
      const text = extractText(result.content)
      return {
        content: [{ type: 'text', text: text || `(${rawName} 无文本输出)` }],
      }
    } finally {
      clearTimeout(timer)
      if (signal) signal.removeEventListener('abort', onAbort)
    }
  }

  // 用给定 URL 建新连接并注册工具（假定旧代已被调用方 teardown）。
  // seq 用于竞态保护：连接完成后若已不是当前代，则回滚本代。
  async function connect(url, seq) {
    const generation = new Client({ name: 'advisor-agent', version: '0.1.0' }, { capabilities: {} })
    const transport = new StreamableHTTPClientTransport(new URL(url), { requestInit: { headers: {} } })
    await generation.connect(transport)
    if (disposed || seq !== sequence) { await generation.close().catch(() => {}); return 0 }

    // listTools 一趟（tushare 不靠 pagination，但按 SDK 语义遍历 cursor 兜底）。
    const definitions = new Map()
    let cursor
    do {
      const list = cursor === undefined ? await generation.listTools() : await generation.listTools({ cursor })
      for (const tool of list.tools ?? []) {
        const publicName = publicToolName(tool.name)
        if (definitions.has(publicName)) throw new Error(`tushare MCP 工具列表含重复名称 "${tool.name}"`)
        definitions.set(publicName, tool)
      }
      cursor = list.nextCursor
    } while (cursor)

    const nextDisposers = new Map()
    for (const [publicName, tool] of definitions) {
      const definition = {
        name: publicName,
        description: tool.description ?? '',
        parameters: tool.inputSchema ?? { type: 'object', properties: {} },
        output: {
          schema: {
            type: 'object',
            properties: { content: { type: 'array', items: {} } },
            required: ['content'],
            additionalProperties: false,
          },
          render: (_args, value) => [{ type: 'text', text: extractText(value && value.content) }],
        },
        execute: (args, exec) => execTool(generation, tool.name, args, exec),
      }
      try {
        const dispose = tools.register(definition)
        nextDisposers.set(publicName, dispose)
      } catch (error) {
        for (const d of nextDisposers.values()) { try { await d() } catch (e) {} }
        await generation.close().catch(() => {})
        throw error
      }
    }

    if (disposed || seq !== sequence) {
      for (const d of nextDisposers.values()) { try { await d() } catch (e) {} }
      await generation.close().catch(() => {})
      return 0
    }
    client = generation
    disposers = nextDisposers
    return nextDisposers.size
  }

  // 热切换入口：清空 URL 则断开并注销全部工具；否则断旧连新。
  async function applyUrl(url) {
    if (disposed) return
    const run = chain.then(async () => {
      if (disposed) return
      const trimmed = typeof url === 'string' ? url.trim() : ''
      if (trimmed === '') {
        await teardownCurrent()
        logger.info?.('tushare MCP：URL 已清空，已断开并注销全部工具')
        return
      }
      const seq = ++sequence
      await teardownCurrent()
      if (disposed || seq !== sequence) return
      try {
        const count = await connect(trimmed, seq)
        if (count > 0) logger.info?.(`tushare MCP：已连接并注册 ${count} 个工具（serverName=${SERVER_NAME}）`)
      } catch (error) {
        // 新代失败：旧代已 teardown，本次留空；不影响插件其余功能。
        logger.warn?.(`tushare MCP：连接失败，未注册工具：${error instanceof Error ? error.message : error}`)
      }
    })
    chain = run.catch(() => {})
    return run
  }

  async function dispose() {
    disposed = true
    await chain
    await teardownCurrent()
  }

  return { applyUrl, dispose }
}