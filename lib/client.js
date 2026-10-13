window.__ModuleLoader__.load({ id: 'advisor-agent', factory: (require) => {
  const module = { exports: {} }
  const exports = module.exports
  const React = require('react')
  const { useEffect, useMemo, useRef, useState } = React

  const CONFIG_ENDPOINT = '/plugins/advisor-agent/config'
  // 预置端点：面板投递指令前，把技能所需脚本/文档直接写进目标工作区 scripts/。
  // 桌面原生外壳下会话 shell 不认识 dsh-app:// 协议、又被宿主门禁拦在 HTTP 之外，
  // 「让 agent 自己下载」恒不可达；预置走渲染器内 fetch（与 /config 同通道），恒可达。
  const PROVISION_ENDPOINT = '/plugins/advisor-agent/provision'
  const NAMESPACE = 'advisor-agent'

  // ─────────────────────────────────────────────────────────────────────────────
  // 投研技能注册表（单一数据源）。
  //
  // 注意：/plugins/<id>/ 只服务 client.js 这一个 bundle（DSH client-modules 的
  // serveBundle 只认 /client.js 与 /client.js.map），插件包内其它静态文件不会
  // 被静态伺服。因此技能注册表必须内联在本文件，不能走运行时 fetch。
  //
  // 每新增技能，只需往下面数组加一条对象（id/label/description/params），
  // 渲染、校验、指令拼装全部由通用逻辑驱动，无需改任何其它代码。
  // 参数字段：type∈{string|number|select}, label, required, placeholder, hint, default, options
  // ─────────────────────────────────────────────────────────────────────────────
  const ADVISOR_SKILLS = [
    {
      id: 'stock-valuation',
      label: '个股估值',
      description: '对一只 A股/港股 做估值判断：贵不贵 / 值多少钱 / 目标价 / 何时重估',
      params: {
        symbol: {
          type: 'string',
          label: '股票名或代码',
          required: true,
          placeholder: '如 贵州茅台 / 600519 / 00700',
          hint: '支持股票名称（如 贵州茅台、宁德时代）或代码（A股 6 位、港股 5 位）',
        },
        market: {
          type: 'select',
          label: '市场',
          required: false,
          default: 'A',
          options: [
            { value: 'A', label: 'A股' },
            { value: 'HK', label: '港股' },
          ],
        },
      },
    },
    {
      id: 'report-reading',
      label: '研报阅读',
      description: '读一份卖方研报：抽出主张 → 批判性核查（有证据才证伪）→ 采信账本 → 前瞻PE/PEG/合理价与安全边际',
      params: {
        entry: {
          type: 'string',
          label: '研报入口（文件路径或链接）',
          required: true,
          placeholder: '如 output/research/report.md 或 https://…/rpt.html',
          hint: '本地 .txt/.md/.html 文件路径，或 http(s) 链接；PDF 请先转成文本再喂进来（脚本不解析 PDF，会如实报缺口）。'
            + '入口只有这一个参数：脚本自己抽正文与关键字段，抽不到的会列成缺口，再由会话读正文补齐后重跑',
        },
      },
    },
    {
      id: 'feishen-trading-logic',
      label: '飞神交易逻辑',
      description: '按「市场环境→板块阶段→个股角色→量价触发→周期/成本」给出条件动作、失效条件与观察边界',
      params: {
        question: {
          type: 'string',
          label: '问题/任务',
          required: true,
          placeholder: '如：结合当前市场按飞神逻辑整理观察池；或我比较萝卜、裤裤、哈韩',
          hint: '一句话写清想判断什么；支持飞神别名（如萝卜/裤裤/哈韩/汉子），技能会先解析',
        },
        mode: {
          type: 'select',
          label: '分析模式',
          required: false,
          default: 'auto',
          options: [
            { value: 'auto', label: '自动判断' },
            { value: 'pool', label: '候选池 / 观察池' },
            { value: 'compare', label: '对比（多只/多方向）' },
            { value: 'stock', label: '单股分析' },
            { value: 'portfolio', label: '持仓 / 成本处理' },
            { value: 'review', label: '交易复盘' },
          ],
        },
        symbols: {
          type: 'string',
          label: '标的（可选）',
          required: false,
          placeholder: '如 600519, 罗博特科, 光库科技',
          hint: '逗号或中文顿号分隔；1–3 只会走个股层级，缺少实时证据时不硬编结论',
        },
        theme: {
          type: 'string',
          label: '板块 / 题材（可选）',
          required: false,
          placeholder: '如 人形机器人 / AI 算力 / 证券',
          hint: '候选池和板块阶段判断时使用',
        },
        horizon: {
          type: 'select',
          label: '交易周期',
          required: false,
          default: 'auto',
          options: [
            { value: 'auto', label: '自动判断' },
            { value: 'short', label: '短线' },
            { value: 'swing', label: '波段' },
            { value: 'medium', label: '中期趋势' },
            { value: 'long', label: '长期跟踪' },
          ],
        },
        position_context: {
          type: 'string',
          label: '持仓/成本说明（可选）',
          required: false,
          placeholder: '如：成本 12.5，现有 30%，想判断持有/做T/减仓',
          hint: '只填会实质影响动作的信息；不填则保持观察态',
        },
      },
    },
    {
      id: 'copy-trade',
      label: '抄作业分析',
      description: '抓取群作业链接，还原大佬持仓/换仓路线，轻量回测，「值不值得抄」',
      params: {
        url: {
          type: 'string',
          label: '作业链接',
          required: true,
          placeholder: '如 http://121.41.9.82:4380/dingall?a=…',
          hint: '钉钉/QQ群里「群主贴持仓/买卖信号」的消息流链接（dingall 等）；留空可用本地 HTML 文件',
        },
        html: {
          type: 'string',
          label: '本地 HTML 路径（可选）',
          required: false,
          placeholder: '如 output/copy-trade/local_raw.html',
          hint: '不填则用上面的链接抓取；填了则以本地文件为准（离线分析）',
        },
      },
    },
    {
      id: 'workspace-init',
      label: '初始化工作区',
      description: '在指定目录一键生成三大技能（抄作业 / 个股分析 / W底搜索）的运行目录骨架与清单模板',
      params: {
        target: {
          type: 'string',
          label: '目标目录（绝对路径）',
          required: true,
          placeholder: '如 D:/my-advisor',
          hint: '在此目录生成 output/（作业回测·持仓·观察仓·W底报告）与 knowledge/（个股知识库）骨架；已存在的文件不会被覆盖',
        },
      },
    },
    {
      id: 'w-bottom-screener',
      label: '观察仓 W底筛选',
      description: '筛观察仓里近5日形成 W底（双底）+ 放量确认的标的，出命中清单与形态细节',
      params: {
        lookback: {
          type: 'string',
          label: '回看交易日数（可选）',
          required: false,
          placeholder: '默认 30',
          hint: '在近 N 个交易日内识别双底；留空用默认 30',
        },
        trough_tol: {
          type: 'string',
          label: '双底偏差阈值（可选）',
          required: false,
          placeholder: '默认 3%（填 0.03）',
          hint: '左右两底低点偏差上限（|B1-A|/A）；留空用默认 3%',
        },
      },
    },
    {
      id: 'momentum-rotation',
      label: '中期动量排名',
      description: '对观察仓所有标的算 mom20/mom120/mom60 双榜动量排名 + 三道过滤，出等权目标组合（8 只）+ 老仓缓冲',
      params: {
        max_positions: {
          type: 'string',
          label: '最大持仓数（可选）',
          required: false,
          placeholder: '默认 8',
          hint: '组合最多等权持有几只；留空用默认 8（快4+慢4）',
        },
        top_n: {
          type: 'string',
          label: '排名展示靠前名次（可选）',
          required: false,
          placeholder: '默认 16',
          hint: 'buffer16 缓冲名次；留空用默认 16',
        },
        market_guard: {
          type: 'select',
          label: '大盘状态关（可选）',
          required: false,
          default: 'on',
          options: [
            { value: 'on', label: '开（默认）· 大盘下行冻结新增' },
            { value: 'off', label: '关 · 下行也照常选股' },
          ],
          hint: '关闭后大盘状态关不再冻结新增；大盘状态仍照常计算并展示在报告里，风险请人工把控',
        },
      },
    },
    {
      id: 'etf-rotation',
      label: 'ETF动量轮动',
      description: '两段式：先按景气跟踪状态从 ETF 目录映射构建当期池（常驻层+在跟板块映射），再对当期池算 24 日加权回归斜率×R² 动量排名，出前 K 等权目标组合（默认 K=4，缓冲 1.5K）+ 空槽切货币ETF',
      params: {
        action: {
          type: 'select',
          label: '操作',
          required: true,
          default: 'run',
          options: [
            { value: 'run', label: 'run · 对当期 ETF 池跑轮动排名出持仓' },
            { value: 'pool-build', label: 'pool-build · 按景气研究重建当期 ETF 池' },
          ],
          hint: '平时用 run；池成分一般按季度/景气变化用 pool-build 重建（先刷库不存在的槽位会自动补）',
        },
        k: {
          type: 'string',
          label: '持仓数 K（可选）',
          required: false,
          placeholder: '默认 4',
          hint: '等权持有合格榜前 K 名（老仓缓冲 ceil(1.5K)）；低波端点 10；留空用默认 4',
        },
      },
    },
    {
      id: 'prosperity-picking',
      label: '景气板块选股',
      description: '维护景气板块跟踪（无则初始化、有则复核景气点/风险点并剔除/新增），再从每个在跟板块选出 8–12 支（默认 10）高相关标的，按景气投资口径估值后经确认写入观察仓；范围可收窄为仅扫当前新热点或仅指定板块，不必每次全量重跑',
      params: {
        action: {
          type: 'select',
          label: '操作',
          required: true,
          default: 'screen',
          options: [
            { value: 'screen', label: 'screen · 筛选/复核景气板块' },
            { value: 'pick', label: 'pick · 选出景气标的' },
          ],
        },
        scope: {
          type: 'select',
          label: '范围',
          required: false,
          default: 'all',
          options: [
            { value: 'all', label: 'all · 全部在跟板块（全量复核+扫新增 / 全量选股）' },
            { value: 'new', label: 'new · 仅扫描当前新热点板块（screen 用，不动现有板块，最快）' },
            { value: 'only', label: 'only · 仅指定板块（重筛/新增指定板块，或只为指定板块选股）' },
          ],
          hint: '默认 all 全量：screen 复核每个在跟板块+扫新热点、pick 重选每个在跟板块，板块多了会慢；日常跟踪建议 new / only，定期再跑 all',
        },
        sectors: {
          type: 'string',
          label: '指定板块（范围=only 时必填）',
          required: false,
          placeholder: '如 ai-compute,robotics；新增可写中文名（如 固态电池）',
          hint: '多个用逗号分隔；pick 只支持已在跟的板块 id，screen 还可写想研究纳入的中文板块名',
        },
        focus: {
          type: 'string',
          label: '关注点/补充要求（可选）',
          required: false,
          placeholder: '如 侧重AI板块 / 排除港股 / 重点核查某风险',
          hint: '写给本轮分析的要求；与流程硬约束冲突时以硬约束为准',
        },
        count: {
          type: 'string',
          label: '每板块目标支数（可选，pick 用）',
          required: false,
          placeholder: '默认 10（建议每板块 8–12 支）',
          hint: '「选出景气标的」按板块定额：每板块支数默认 10（建议 8–12）；板块内合格候选不足时按实际数量如实说明',
        },
      },
    },
    {
      id: 'watchlist-manager',
      label: '观察仓管理',
      description: '维护观察仓标的池（W底筛选与动量轮动共用）：加入/移出/列出/查改观察标的',
      params: {
        action: {
          type: 'select',
          label: '操作',
          required: true,
          default: 'add',
          options: [
            { value: 'add', label: 'add · 加入观察仓' },
            { value: 'list', label: 'list · 列出观察仓' },
            { value: 'set', label: 'set · 写参数（如 BS/MR）' },
            { value: 'rm', label: 'rm · 移出观察仓' },
            { value: 'check', label: 'check · 查询是否在仓' },
          ],
        },
        code: {
          type: 'string',
          label: '股票代码',
          required: false,
          placeholder: '如 600519 / 600519.SH / 00700',
          hint: 'add/set/rm/check 必填，list 不用填；自动规范化（600519→600519.SH、700→00700.HK）',
        },
        name: {
          type: 'string',
          label: '名称（可选，add 用）',
          required: false,
          placeholder: '如 贵州茅台',
          hint: '便于报告展示',
        },
        note: {
          type: 'string',
          label: '跟踪理由/备注（可选，add 用）',
          required: false,
          placeholder: '如 等回调到 1500',
          hint: '纯展示，如「等什么买点/目标价」',
        },
        set_params: {
          type: 'string',
          label: 'param 透传（可选，set 用）',
          required: false,
          placeholder: '如 --BS B --BS_DATE 2025-06-03 或 --MR 3',
          hint: '成对 --key value；只写命中的键，不动其他字段；键只能英文/数字/下划线',
        },
        search: {
          type: 'string',
          label: '搜索/筛选（可选，list 用）',
          required: false,
          placeholder: '如 茅台 / 600519，或 --where PS=ai-compute',
          hint: 'list 的搜索条件：关键字（不区分大小写，包含匹配 代码/名称/备注/来源/param 值，含空格需加引号）或 --where KEY=VALUE 按 param 精确筛选（单写 KEY 表示键存在，可多对）；两者可组合',
        },
      },
    },
    {
      id: 'bili-video-summary',
      label: 'B站视频总结',
      description: '下载 B站视频音频 → 离线 whisper 转录成文字稿 → 还原听错 → 摘要/论点链/论据核查/立场提示',
      params: {
        video: {
          type: 'string',
          label: '视频链接或 BV 号',
          required: true,
          placeholder: '如 https://www.bilibili.com/video/BV1xx… 或 BV1xx…',
          hint: '支持完整链接、b23.tv 短链、或直接 BV 号；转录产物写到工作区 output/videos/<BV号>/（已有文字稿会跳过转录、直接分析）',
        },
        model: {
          type: 'select',
          label: '识别模型（速度/精度权衡）',
          required: false,
          default: 'small',
          options: [
            { value: 'tiny', label: 'tiny · 最快（约 0.5~1 倍音频时长）' },
            { value: 'base', label: 'base · 较快（约 0.6~1.2 倍）' },
            { value: 'small', label: 'small · 默认（更准，约 0.4~0.8 倍）' },
          ],
          hint: '长视频或想快出结果选 base/tiny；首次运行需下载对应模型（tiny≈75MB / base≈145MB / small≈460MB），之后复用缓存不再下载',
        },
      },
    },
  ]

  // ── 主题样式（对齐 DSH CSS 变量，缺失时用中性回退色） ────────────────────────
  const cssVars = {
    card: '1px solid var(--dsw-alias-border-l1, #d8d8d8)',
    surface: 'var(--dsw-alias-bg-base, transparent)',
    inputBg: 'var(--dsw-alias-input-bg, #fff)',
    inputFg: 'var(--dsw-alias-label-primary, #111)',
    accent: 'var(--dsw-alias-accent, #3b82f6)',
    err: 'var(--dsw-alias-state-error-primary, #d33)',
    ok: 'var(--dsw-alias-state-success-primary, #187)',
  }

  const overlayStyle = {
    position: 'fixed', inset: 0, zIndex: 1000, display: 'flex',
    alignItems: 'center', justifyContent: 'center',
    background: 'rgba(0,0,0,0.35)', padding: 16,
  }
  const dialogStyle = {
    background: cssVars.surface, border: cssVars.card, borderRadius: 12,
    padding: 20, width: 'min(440px, 92vw)', maxHeight: '86vh',
    overflow: 'auto', display: 'grid', gap: 14,
    boxShadow: '0 12px 40px rgba(0,0,0,0.25)',
  }
  const fieldStyle = { display: 'grid', gap: 6 }
  const labelStyle = { display: 'block', fontWeight: 600, fontSize: 13 }
  const hintStyle = { display: 'block', opacity: 0.6, fontSize: 12, marginTop: 2 }
  const inputStyle = {
    padding: '8px 10px', borderRadius: 8, fontSize: 13, minWidth: 0,
    border: '1px solid var(--dsw-alias-border-l2, #ccc)',
    background: cssVars.inputBg, color: cssVars.inputFg,
  }
  const buttonStyle = {
    padding: '9px 14px', borderRadius: 8, border: 'none', fontSize: 13, fontWeight: 600,
    background: cssVars.accent, color: '#fff', cursor: 'pointer', justifySelf: 'start',
  }

  // ── 运行时把 ctx 服务存到闭包（apply 注入，面板消费） ─────────────────────────
  const runtime = {
    sessions: null, // ctx.get('sessions')
    workspaces: null, // ctx.get('workspaces')
    uiWorkspace: null, // ctx.get('uiWorkspace')：官方「新开会话」组合服务（含 startSession/connectWorkspace）
    layout: null, // ctx.get('layout')：主面板切换（selectPanel）
  }

  // ── 配置读写（/config 端点，宿主侧提供） ─────────────────────────────────────
  const DEFAULT_CONFIG = { enabled: true, enabledSkills: ['stock-valuation', 'copy-trade', 'workspace-init', 'w-bottom-screener', 'bili-video-summary', 'feishen-trading-logic', 'prosperity-picking'], defaultTarget: 'new', tushareToken: '' }

  // fallback：读取失败时的兜底配置——默认 DEFAULT_CONFIG；提交时传已加载的快照，
  // 避免瞬时失败把已读到的 token 冲掉退回默认值。
  async function readConfig(fallback) {
    const base = fallback !== undefined ? fallback : Object.assign({}, DEFAULT_CONFIG)
    try {
      const res = await fetch(CONFIG_ENDPOINT, { cache: 'no-store' })
      if (!res.ok) throw new Error(`config read failed: ${res.status}`)
      const cfg = Object.assign({}, DEFAULT_CONFIG, await res.json())
      // 升级兼容：把默认启用的技能并入已保存列表，避免旧配置让新技能入口消失。
      const set = new Set(Array.isArray(cfg.enabledSkills) ? cfg.enabledSkills : [])
      for (const id of DEFAULT_CONFIG.enabledSkills) set.add(id)
      cfg.enabledSkills = [...set]
      return cfg
    } catch (e) {
      console.error('[advisor-agent] readConfig failed, using fallback:', e)
      return base
    }
  }

  async function writeConfig(patch) {
    try {
      const res = await fetch(CONFIG_ENDPOINT, {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(patch),
      })
      if (!res.ok) throw new Error(`config write failed: ${res.status}`)
      return Object.assign({}, DEFAULT_CONFIG, await res.json())
    } catch (e) {
      console.error('[advisor-agent] writeConfig failed:', e)
      throw e
    }
  }

  // ── 解析目标工作区（当前会话所属 → recent → 第一个），返回 workspace view ──
  function resolveTargetWorkspace() {
    const ws = runtime.workspaces
    if (!ws || !ws.list) return undefined
    const snap = (ws.list && ws.list.getSnapshot) ? ws.list.getSnapshot() : {}
    const items = Array.isArray(snap.items) ? snap.items : []
    if (items.length === 0) return undefined
    // 1) 当前会话所属的工作区（反查 sessionIds）
    const sessions = runtime.sessions
    const curId = sessions && sessions.list && sessions.list.getSnapshot
      ? sessions.list.getSnapshot().current
      : undefined
    if (curId) {
      const owner = items.find((w) => Array.isArray(w.sessionIds) && w.sessionIds.indexOf(curId) !== -1)
      if (owner) return owner
    }
    // 2) 最近活动工作区
    if (snap.recentWorkspaceId) {
      const recent = items.find((w) => w.workspaceId === snap.recentWorkspaceId)
      if (recent) return recent
    }
    // 3) 第一个工作区
    return items[0]
  }

  // 当前工作区的磁盘路径（供 workspace-init 做默认目标目录）
  function resolveCurrentWorkspacePath() {
    const view = resolveTargetWorkspace()
    return view && typeof view.path === 'string' ? view.path : ''
  }

  // ── 资产预置（投递前由面板完成，指令里不再出现任何下载 URL） ────────────────
  // 返回该技能本次运行所需的预置资产键列表（与宿主侧 /provision 白名单键一致）。
  // 键为空数组的技能（走 skill 方法论的 copy-trade / portfolio-tracker 等）不预置。
  function provisionAssetsFor(skill, values) {
    switch (skill.id) {
      case 'stock-valuation':
        return [
          'scripts/stock-valuation/SKILL.md',
          'scripts/stock-valuation/data-source.md',
          'scripts/fetch_quotes.py',
          'scripts/fetch_forecast.py',
          'scripts/fetch_snapshot.py',
        ]
      case 'report-reading':
        return ['scripts/report_reader.py', 'scripts/fetch_quotes.py', 'scripts/fetch_forecast.py']
      case 'workspace-init':
        return ['scripts/init_workspace.py', 'scripts/setup_runtime.py']
      case 'w-bottom-screener':
        return ['scripts/fetch_quotes.py', 'scripts/w_bottom_screen.py']
      case 'momentum-rotation':
        return ['scripts/fetch_quotes.py', 'scripts/momentum_strategy.py']
      case 'prosperity-picking': {
        // screen 只需状态与观察仓脚本；pick 追加行情/一致预期取数与估值方法论文档。
        const base = ['scripts/prosperity_state.py', 'scripts/manage_watchlist.py']
        if (String(values.action || 'screen').trim() === 'pick') {
          return base.concat([
            'scripts/fetch_quotes.py',
            'scripts/fetch_forecast.py',
            'scripts/stock-valuation/SKILL.md',
            'scripts/stock-valuation/data-source.md',
          ])
        }
        return base
      }
      case 'watchlist-manager':
        return ['scripts/manage_watchlist.py']
      case 'feishen-trading-logic':
        return [
          'scripts/feishen-trading-logic/SKILL.md',
          'scripts/feishen-trading-logic/feishen-rulebook.md',
          'scripts/feishen-trading-logic/feishen-aliases.md',
          'scripts/fetch_quotes.py',
        ]
      case 'bili-video-summary':
        return ['scripts/transcribe_video.py', 'scripts/setup_runtime.py', 'scripts/fetch_quotes.py']
      default:
        return []
    }
  }

  // 调用宿主 /provision 端点把资产写进目标工作区；失败抛错（由调用方阻止投递）。
  async function provisionAssets(workspaceRoot, assets) {
    let res
    try {
      res = await fetch(PROVISION_ENDPOINT, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ workspaceRoot, assets }),
      })
    } catch (e) {
      throw new Error(`脚本预置请求失败（${e && e.message ? e.message : e}），指令未投递`)
    }
    if (!res.ok) {
      let detail = `HTTP ${res.status}`
      try {
        const body = await res.json()
        if (body && body.error) detail = body.error
      } catch (e) {}
      throw new Error(`脚本预置失败（${detail}），指令未投递`)
    }
    return res.json()
  }

  // 反查某个会话所属工作区的磁盘路径（预置目标）。查不到返回空串，由调用方兜底。
  function workspacePathForSession(sessionId) {
    const ws = runtime.workspaces
    if (!ws || !ws.list || !ws.list.getSnapshot || !sessionId) return ''
    const items = Array.isArray(ws.list.getSnapshot().items) ? ws.list.getSnapshot().items : []
    const owner = items.find((w) => Array.isArray(w.sessionIds) && w.sessionIds.indexOf(sessionId) !== -1)
    return owner && typeof owner.path === 'string' ? owner.path : ''
  }

  // 等待会话注册到其所属工作区：workspaces.list 的 sessionIds 可能稍晚于 selection
  // 更新（新开会话场景存在传播时差），短轮询兜住；超时返回空串由调用方报错。
  async function waitWorkspacePathForSession(sessionId, timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      const path = workspacePathForSession(sessionId)
      if (path) return path
      if (Date.now() >= deadline) return ''
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
  }

  // 「新开会话」的目标工作区 id：当前会话所属 → 最近活动 → 第一个。
  // 与官方 startSession 的解析顺序一致，只是这里要拿 workspaceId 自己 create 会话。
  function resolveNewSessionWorkspaceId() {
    const ws = runtime.workspaces
    if (!ws || !ws.list || typeof ws.list.getSnapshot !== 'function') return ''
    const snap = ws.list.getSnapshot() || {}
    const items = Array.isArray(snap.items) ? snap.items : []
    if (items.length === 0) return ''
    const curId = readCurrentSessionId()
    if (curId) {
      const owner = items.find((w) => Array.isArray(w.sessionIds) && w.sessionIds.indexOf(curId) !== -1)
      if (owner && typeof owner.workspaceId === 'string') return owner.workspaceId
    }
    if (snap.recentWorkspaceId) {
      const recent = items.find((w) => w.workspaceId === snap.recentWorkspaceId)
      if (recent) return recent.workspaceId
    }
    return typeof items[0].workspaceId === 'string' ? items[0].workspaceId : ''
  }

  // 等待新会话可投递：binding 需要宿主先 retain 该会话（openSession 内部完成），
  // 且 open() 是异步的，短轮询兜住极短时差；超时返回 undefined 交给调用方报错。
  async function waitSessionBinding(sessionId, timeoutMs = 3000) {
    const deadline = Date.now() + timeoutMs
    for (;;) {
      const binding = runtime.sessions && typeof runtime.sessions.binding === 'function'
        ? runtime.sessions.binding(sessionId)
        : undefined
      if (binding && binding.session) return binding
      if (Date.now() >= deadline) return undefined
      await new Promise((resolve) => setTimeout(resolve, 100))
    }
  }

  // ── 指令拼装：把表单值转成一条投递给 agent 的指令 ───────────────────────────
  function buildInstruction(skill, values, config) {
    // 面板配置的 tushare token：随指令以 --token 参数传给 fetch_quotes.py（脚本侧最高优先级）。
    const token = config && typeof config.tushareToken === 'string' ? config.tushareToken.trim() : ''
    const tokenArg = token ? ` --token ${token}` : ''
    const tokenSourceText = token
      ? 'token 已随命令以 --token 参数带上（来自投研面板配置）'
      : 'token 用 --token 参数 / 环境变量 TUSHARE_TOKEN / 工作区 .env'
    const parts = []
    for (const [k, spec] of Object.entries(skill.params)) {
      let v = values[k]
      // workspace-init 的目标目录：表单未填时，用「当前工作区的磁盘路径」预填，
      // 让生成目录跟随用户正在查看的工作区（而非 agent 仓库根目录）。
      if (skill.id === 'workspace-init' && k === 'target' && (!v || String(v).trim() === '')) {
        const p = resolveCurrentWorkspacePath()
        if (p) v = p
      }
      if (v !== undefined && v !== null && String(v).trim() !== '') {
        parts.push(`${spec.label || k}: ${v}`)
      }
    }
    const paramsText = parts.length > 0 ? `（${parts.join('；')}）` : ''
    // report-reading：读研报工具（面板只给一个入口参数，同时支持本地文件与 http(s) 链接）。
    // 脚本 report_reader.py 自包含纯标准库、随插件分发，由面板在投递前预置到工作区 scripts/；
    // 核心原则写死在指令里：批判性核查只找证据——有证据证伪才替换/作废，
    // 拿不出证据一律默认采信研报估算；行情与一致预期证据经文件喂给脚本，不手工转抄数字。
    if (skill.id === 'report-reading') {
      const entry = String(values.entry || '').trim()
      const dir = 'output/research'
      return [
        `执行「研报阅读」流程：把这份研报读成可核查的主张，做一轮只找证据的批判性核查，再按采信结果给估值判断。`,
        `研报入口：${entry}`,
        `第一步：所需三个脚本（report_reader.py、fetch_quotes.py、fetch_forecast.py）已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取、无需检查文件是否存在，直接从第二步开始执行。若 scripts/ 下缺失对应脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `三个脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码，直接执行即可。`,
        `第二步（抽正文与主张）：python scripts/report_reader.py --input "${entry}" --text-out ${dir}/body.txt --no-report`,
        `脚本自动抽取标的/机构/日期/评级/目标价/自述估值锚（如「给予 2027 年 25 倍 PE」）与分年度预测表（区分 A 列实际值、E 列预测）；抽不到的字段会列成「数据缺口」。若退出码 2（入口不可用：文件不存在、链接抓取失败、PDF 未转文本），如实转告用户并请其提供文本或可访问链接，不要编造研报内容。`,
        `第三步（有缺口才做）：用 read 工具读 ${dir}/body.txt 正文，按脚本头部 CLAIMS_SCHEMA_DOC 的格式把缺口字段补成 ${dir}/claims.json（单位：净利/营收百万元、EPS 与价格元、总市值亿元）。正文里确实没有的字段宁可留空、保持缺口，**不要猜数**。`,
        `第四步（取证据，务必重定向到文件，数字不要经过会话转抄）：`,
        `  python scripts/fetch_quotes.py --snapshot <标的代码>${tokenArg} > ${dir}/snapshot.txt`,
        `  python scripts/fetch_forecast.py <标的代码> --mcap <总市值亿元> > ${dir}/consensus.txt`,
        `  总市值亿元 = 快照输出里的「总市值（万元）÷ 1e4」。第二个命令仅 A 股可用（港股无同花顺 F10 盈利预测页）：跳过时在汇报里说明「缺一致预期」，不要编造一致预期数据。`,
        `第五步（终跑）：python scripts/report_reader.py --input "${entry}" --snapshot-file ${dir}/snapshot.txt --consensus-file ${dir}/consensus.txt`,
        `  若第三步生成了 claims.json，就在命令里追加 --claims ${dir}/claims.json；若用户在会话里明确给了估值倍数口径（如「按 25 倍 PE 看」），追加 --pe-band <低>,<高> 并说明。`,
        `  脚本产出完整报告 ${dir}/report_<标的>_<时间戳>.md 与同名 .json，标准输出给摘要（研报要素 / 有证据证伪与存疑条目 / 采信账本 / 估值桥 / 缺口）。`,
        `第六步（汇报）：按报告结构原样转达——研报要素 → 批判性核查发现（把「有证据证伪」与「存疑但没证伪」分开列）→ 采信账本（哪些数字采信了、依据是什么、有没有被证据替换或作废）→ 估值桥（前瞻PE/PEG/逐年PE/目标价空间/合理价区间/相对保守价的溢折价/现价隐含要求）→ 方向结论 → 与机构一致预期的差异 → 缺口与风险 → 关键节点（等财报验证/等销售月报/等价格库存拐点）。同时告知报告文件位置。`,
        `硬约束：`,
        `1. 采信原则（本工具的灵魂）：核查只找证据。只有拿到证据（报告内部算术矛盾、行文增速与表格绝对值打架、EPS 与净利/股本矛盾、与已披露实际值冲突、评级与目标价方向矛盾）才允许替换或作废主张；**拿不出证据的一律默认采信研报估算**。存疑项绝不能说成「研报错了/不可信」，「证伪」与「存疑」的措辞必须分清。`,
        `2. 不得自行给研报数字打折（砍增速、乘系数、凭感觉下调）；没有证据的「我觉得偏高」不是信息。`,
        `3. 行情与一致预期数字一律经文件传给脚本（--snapshot-file / --consensus-file），不要在会话里手算 PE/PEG 或转抄数字。`,
        `4. 缺口如实报告：PDF 未转文本、链接不可用、无机构覆盖、取数失败都直接说明，不编造研报内容或预测。`,
        `5. 估值方向结论必须建立在「采信后的预测」之上，并写明所用的合理PE 假设（默认 8–25 倍只是通用观察区间，未做质量溢价/行业β 校准）与前瞻年（跟随研报自述锚年）。`,
        `6. 用户若要持续跟踪该标的，委托 watchlist-manager 写入观察仓（python scripts/manage_watchlist.py add <代码> --note "<等什么/目标价>" --source report-reading），不要直接改清单文件。`,
        `7. 输出仅供研究参考，不构成投资建议。`,
      ].join('\n')
    }
    // workspace-init：产出确定性的执行指令。
    // 脚本随插件分发（src/workspace-init/，files 清单内），由面板预置到目标工作区
    // scripts/ 后执行——这是唯一可靠来源：目标工作区 cwd 下不存在仓库本地的
    // .agents/skills 路径，不能拿它当回退。
    if (skill.id === 'workspace-init') {
      const target = (values.target && String(values.target).trim()) || resolveCurrentWorkspacePath() || '.'
      return [
        `在目录 ${target} 初始化投资顾问工作区。`,
        `第一步：所需两个脚本（init_workspace.py、setup_runtime.py）已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失对应脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `第二步：python scripts/init_workspace.py --target ${target}`,
        `两个脚本自身均为纯标准库（setup_runtime.py 负责装第三方依赖，它自己不需要依赖）；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `该脚本会创建：output/copy-trade/（抄作业 copy-trade 回测产物）、output/reports/（个股分析 stock-valuation 研报）、output/w-bottom/ 与 output/watchlist/（W底搜索 w-bottom-screener 报告与观察仓标的池）、output/quotes-store/（共享本地行情库，fetch_quotes.py 刷库写回）、output/momentum/（动量轮动报告与持仓状态）、output/prosperity/（景气板块选股：景气跟踪状态与筛选/选股报告）、output/portfolio/（持仓清单）、output/sectors/（景气快照）、output/skill-state/（知识更新状态）、output/videos/（B站视频转录）、knowledge/（个股知识库，含 stocks/ 清单索引与模板）。`,
        `并写入 README.md、.gitignore、holdings.yaml、watchlist.yaml、update-manifest.yaml、alias-map.override.yaml、knowledge/index.md、knowledge/stocks/_template.md 等模板文件（仅当文件不存在时）。`,
        `第三步（可选，供后续 B站视频总结工具直接复用）：若用户要用 B站视频总结，运行 \`python scripts/setup_runtime.py --target ${target}\` 建工作区持久 .venv 并装 yt-dlp、faster-whisper（可选 --with-opencc）。此步幂等、首次较慢，非必须时不主动跑；B站工具跑时会按需自行调用。若该脚本报 python_missing（退出码 2，系统无可用 Python）或安装失败（退出码 1），这是「非插件问题」：如实把原因转告用户、请用户确认后处理，不要自动安装 Python、不要换路径兜底。若报退出码 3（需提权：安装撞权限拒绝 Errno 13 / WinError 5），唯一动作是用会话提权机制重跑同一条命令、交用户审批，不换路径。`,
        `执行完成后，用一句话汇报生成了哪些目录与模板文件、哪些已存在被跳过。`,
      ].join('\n')
    }
    // w-bottom-screener：两步确定性执行（fetch_quotes.py 刷库 → w_bottom_screen.py 读库判定）。
    // 两个脚本均自包含（纯标准库）随插件分发，由面板预置到工作区 scripts/；无仓库 .agents/skills 也能跑。
    // 行情由脚本直连 tushare REST API 拉取并写本地 CSV 行情库，全程不经过任何 LLM 上下文。
    if (skill.id === 'w-bottom-screener') {
      const watchlist = 'output/watchlist/watchlist.yaml'
      const extras = []
      if (values.lookback && String(values.lookback).trim() !== '') extras.push(`--lookback ${values.lookback}`)
      if (values.trough_tol && String(values.trough_tol).trim() !== '') extras.push(`--trough-tol ${values.trough_tol}`)
      const extraText = extras.length > 0 ? ` ${extras.join(' ')}` : ''
      return [
        `执行「观察仓 W底放量筛选」流程。`,
        `第一步：所需两个脚本（fetch_quotes.py、w_bottom_screen.py）已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失对应脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `两个脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `命令与参数均已完整给出，按步骤顺序直接执行即可：无需预读 watchlist、无需预检环境或校验脚本文件内容。`,
        `第二步（刷库）：python scripts/fetch_quotes.py --watchlist ${watchlist} --min-bars 30 --full-days 90${tokenArg}`,
        `脚本直连 tushare REST API，对照本地行情库 output/quotes-store/ 把每只增量补到最新交易日（新票或库内不足 30 根的自动全量取近 90 自然日），幂等合并写回；输出为汇总式（成功/失败、有新数据的只数与根数、异常标的逐只列出），K 线与逐票明细不会出现；用一两句话转达刷库结果即可，不要逐只复述。`,
        `第二步失败分流（退出码见脚本输出）：退出码 2（缺 token）→ 如实转告用户按提示配置 TUSHARE_TOKEN（--token 参数 / 环境变量 / 工作区 .env 写 TUSHARE_TOKEN=…）后重跑第二步；退出码 1 或 3（网络/接口失败、部分标的失败）→ 原样转告失败清单与原因，不要编造行情、不要跳过刷库直接判定。`,
        `第三步（判定）：python scripts/w_bottom_screen.py --watchlist ${watchlist}${extraText}`,
        `脚本直接读行情库判定 W底+放量形态（库内数据缺失或过期超过 10 自然日会 fail-closed 并提示先刷库），输出命中报告 output/w-bottom/screen_*.md；把报告内容与命中清单原样汇报给用户。`,
      ].join('\n')
    }
    // momentum-rotation：对观察仓所有标的做「中期动量排名 → 目标组合」。
    // 取数与计算分离：fetch_quotes.py 直连 tushare 刷库（行情不经过任何 LLM 上下文），
    // momentum_strategy.py 直接读本地行情库计算，两步出结果。
    if (skill.id === 'momentum-rotation') {
      const watchlist = 'output/watchlist/watchlist.yaml'
      const stateFile = 'output/momentum/state.json'
      const extras = []
      if (values.max_positions && String(values.max_positions).trim() !== '') extras.push(`--max-positions ${String(values.max_positions).trim()}`)
      if (values.top_n && String(values.top_n).trim() !== '') extras.push(`--buffer-rank ${String(values.top_n).trim()}`)
      // 大盘状态关：表单选「关」时透传 --market-guard false，下行也照常选股出组合
      const marketGuardOff = String(values.market_guard || 'on') === 'off'
      if (marketGuardOff) extras.push('--market-guard false')
      const extraText = extras.length > 0 ? ` ${extras.join(' ')}` : ''
      return [
        `用「中期动量排名」工具，对观察仓（${watchlist}）所有标的做一遍中期动量排名并给出等权目标组合。`,
        `严格按以下步骤执行，不要跳步：`,
        `第一步：所需两个脚本（fetch_quotes.py、momentum_strategy.py）已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失对应脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `两个脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码，直接执行即可。`,
        `命令与参数均已完整给出，按步骤顺序直接执行即可：无需预读 watchlist、无需预检环境或校验脚本文件内容。`,
        `第二步（刷库）：python scripts/fetch_quotes.py --watchlist ${watchlist} --min-bars 121 --full-days 420${tokenArg}`,
        `脚本直连 tushare REST API，对照本地行情库 output/quotes-store/ 把每只增量补到最新交易日（新票或库内不足 121 根的自动全量取近 420 自然日），幂等合并写回；输出为汇总式（成功/失败、有新数据的只数与根数、异常标的逐只列出），K 线与逐票明细不会出现；用一两句话转达刷库结果即可，不要逐只复述。`,
        `第二步失败分流（退出码见脚本输出）：退出码 2（缺 token）→ 如实转告用户按提示配置 TUSHARE_TOKEN（--token 参数 / 环境变量 / 工作区 .env 写 TUSHARE_TOKEN=…）后重跑第二步；退出码 1 或 3（网络/接口失败、部分标的失败）→ 原样转告失败清单与原因，不要编造行情、不要跳过刷库直接计算。`,
        `第三步（计算）：python scripts/momentum_strategy.py --watchlist ${watchlist} --state ${stateFile}${extraText}`,
        `脚本直接读行情库计算 mom20/mom120/mom60 三条动量 + 三道过滤 + 快慢双榜（buffer16 粘性、快4慢4补满）算出目标持仓（库内数据缺失或过期超过 10 自然日会 fail-closed 并提示先刷库），标准输出只打印摘要（大盘状态、目标持仓表、调仓变动——老仓被剔除的标的及原因、新增汇总）——「逐只动量与过滤」全池明细不打印、只写入完整报告 output/momentum/plan_<时间戳>.md，并回写持仓状态 ${stateFile}。`,
        `把标准输出中的摘要、目标持仓与调仓变动（被剔除的老仓及原因、新增）原样汇报给用户，不要自行改写或省略数字；「逐只动量与过滤」明细不要读入或整表复述，向用户提示报告 md 文件位置即可（需要逐只明细时让用户直接打开该文件）。`,
        ...(marketGuardOff ? [`注意：本次按表单选择关闭了大盘状态关（--market-guard false），即使大盘状态为下行脚本也会照常选股出目标组合；报告中仍照常展示大盘状态（regime），请人工确认大盘风险敞口。`] : []),
      ].join('\n')
    }
    // etf-rotation：两段式——pool-build（按景气状态从 ETF 目录映射构建当期池）/ run（对当期池跑轮动）。
    if (skill.id === 'etf-rotation') {
      const action = String(values.action || 'run').trim()
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const fetchUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py`
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/etf_rotation_strategy.py`
      const mapAssetUrl = `${origin}/plugins/advisor-agent/assets/etf-rotation/etf-map.yaml`
      const stateUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/prosperity_state.py`
      const mapFile = 'output/etf-rotation/etf-map.yaml'
      const pool = 'output/etf-rotation/pool.yaml'
      const stateFile = 'output/etf-rotation/state.json'
      const prosperityFile = 'output/prosperity/state.yaml'
      const kArg = action === 'run' && values.k && String(values.k).trim() !== '' ? ` --k ${String(values.k).trim()}` : ''
      const prep = [
        `第一步：把文件准备到当前工作区（不存在目录先创建）：从 ${fetchUrl} 下载 fetch_quotes.py、从 ${assetUrl} 下载 etf_rotation_strategy.py（均保存到 scripts/，用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8，每次覆盖下载保证版本与插件一致）；若 ${mapFile} 不存在，从 ${mapAssetUrl} 下载 ETF 目录保存为 ${mapFile}（已存在则跳过，不覆盖用户已补录过的目录）。任一下载失败（404/网络错误）直接如实报告"脚本资产端点不可用"，不要跳过。`,
        `脚本均为纯标准库自包含、清单为纯文本，无需安装依赖、无需阅读源码，按步骤顺序执行即可。`,
      ]
      if (action === 'pool-build') {
        return [
          `用「ETF动量轮动」工具的 pool-build 流程，按当前景气跟踪状态重建当期 ETF 池（${pool}）。`,
          `严格按以下步骤执行，不要跳步：`,
          ...prep,
          `第二步（读取在跟板块）：读取 ${prosperityFile}（prosperity_state.py 维护的景气板块跟踪状态，唯一写入口是脚本 ${stateUrl}）。若无该文件或无在跟板块，直接如实告知用户"还没有在跟的景气板块，请先跑 prosperity-picking 的 screen 动作初始化"，本次只按 ETF 目录的常驻层（layer: base，全收）生成最小池并在报告中注明「未含景气映射」，不臆造板块。`,
          `第三步（映射建池）：对 ${prosperityFile} 里每个在跟板块，从 ${mapFile} 的 layer: sector 菜单按 sector 关键词匹配选 1–2 只代表 ETF（优先上市 ≥1 年、你判断流动性与代表性更好的；上市未满 45 交易日或标注 2025 上市的一律排除并在报告注明）；常驻层（layer: base）全收；layer: extend 仅在板块/主题关键词明确指向时收编（默认不收）。菜单中没有覆盖的新板块：可联网按板块名选 1 只已上市主流行业 ETF 补入，并在报告标注「菜单外补录」。`,
          `第四步（写池）：把当期池写入 ${pool}（极简 YAML：ts_code/name/note，note 附「入选：<板块名>｜复核：今天日期」；跨境品种 note 须含 QDII 标记）。同时把新增品种的补录建议追加写到 ${mapFile} 对应层（layer 与 sector 字段照抄既有格式）——自动步进对目录只追加、不删除；夕阳/死仓品种（用户拍板或你给我明确说明）才可做下架修剪：从 sector 层移到「下架休眠区」并注明原因与日期，不许物理删行。`,
          `第五步（汇报）：列表汇报当期池构成（常驻 N 只 + 各板块映射明细 + 排除项及原因），并把刷库诊断一并给了：python scripts/etf_rotation_strategy.py --pool ${pool} --plan（只打印库内数据状态，不计算）。提示用户跑 run 动作即可出轮动持仓。`,
        ].join('\n')
      }
      return [
        `用「ETF动量轮动」工具，对当期 ETF 池（${pool}）做一遍 24 日加权回归斜率×R² 动量排名并给出前 K 等权目标组合。`,
        `严格按以下步骤执行，不要跳步：`,
        ...prep,
        `第二步（刷库）：若 ${pool} 不存在，直接如实告知"ETF 池尚未构建，请先跑 pool-build（按景气研究重建当期池）"，不要退回自建名单触碰运气；存在则执行：python scripts/fetch_quotes.py --watchlist ${pool} --min-bars 45 --full-days 120${tokenArg}`,
        `脚本直连 tushare REST API，对照本地行情库 output/quotes-store/ 把每只 ETF 增量补到最新交易日（新票或库内不足 45 根的自动全量取近 120 自然日），幂等合并写回；输出为汇总式，K 线与逐票明细不会出现；用一两句话转达刷库结果即可。`,
        `第二步失败分流（退出码见脚本输出）：退出码 2（缺 token）→ 如实转告用户按提示配置 TUSHARE_TOKEN（--token 参数 / 环境变量 / 工作区 .env 写 TUSHARE_TOKEN=…）后重跑第二步；退出码 1 或 3 → 原样转告失败清单与原因，不要编造行情、不要跳过刷库直接计算。`,
        `第三步（计算）：python scripts/etf_rotation_strategy.py --pool ${pool} --state ${stateFile}${kArg}`,
        `脚本直接读行情库算 24 日加权回归斜率×R² + 三道过滤（R²≥0.4、近3日单日跌幅≤3.5%、得分∈(0,5)），合格榜降序、老仓缓冲 ceil(1.5K)、出前 K 等权目标持仓 + 空槽切货币ETF511880 防御（库内数据缺失或过期超过 10 自然日会 fail-closed 并提示先刷库），标准输出只打印摘要（目标持仓表、调仓变动——含被剔除老仓及原因、防御槽、QDII 溢价人工核提示），「逐只动量与过滤」全池明细不打印、只写入完整报告 output/etf-rotation/plan_<时间戳>.md，并回写持仓状态 ${stateFile}。`,
        `把标准输出中的摘要、目标持仓与调仓变动（被剔除的老仓及原因、新增、防御槽、QDII 溢价提示）原样汇报给用户，不要自行改写或省略数字；「逐只动量与过滤」明细不要读入或整表复述，向用户提示报告 md 文件位置即可。`,
        `池复核提醒（本次只跑排名，不动池）：若池复核日期距今超过一个季度或景气状态 ${prosperityFile} 有更新，建议用户跑 pool-build 重建当期池。`,
      ].join('\n')
    }
    // prosperity-picking：景气板块选股组合工具（筛选景气板块 screen / 选出景气标的 pick）。
    // 状态脚本 prosperity_state.py 与 manage_watchlist.py / fetch_quotes.py /
    // fetch_forecast.py（同花顺机构一致预期，前瞻PE/PEG 的分母）均随插件分发、
    // 由面板预置到工作区 scripts/；个股估值方法论（stock-valuation 的 SKILL.md +
    // data-source.md）同样由面板预置到 scripts/stock-valuation/ 供各板块 subagent 通读
    // （不依赖工作区装有该技能）；pick 的选标的/取行情/估值按板块各起一个后台 subagent
    // 并行执行（并发上限 3、完成一个补一个，无 subagent 工具时串行退化），主流程只做
    // 门槛/汇总/经用户确认后写入；板块复核用 prosperity-analysis 框架，观察仓写入一律
    // 委托 watchlist-manager（add + set --PS 板块 key，供板块日后剔除时快速筛选删除）。
    if (skill.id === 'prosperity-picking') {
      const action = String(values.action || 'screen').trim()
      const focus = String(values.focus || '').trim()
      const count = String(values.count || '').trim()
      // 范围参数：避免「每次都全量重跑」。screen 支持 all（全量复核+扫新增）/
      // new（只扫当前新热点，不动现有板块）/ only（只处理指定板块——在跟的重筛、
      // 未在跟的研究后新增）；pick 支持 all（每个在跟板块都选）/ only（只为指定
      // 在跟板块选股）。pick+new 与 only 未填板块时提示词直接让用户修正、不执行。
      const scopeRaw = String(values.scope || 'all').trim()
      const scope = scopeRaw === 'new' || scopeRaw === 'only' ? scopeRaw : 'all'
      // 指定板块：逗号/顿号/分号/空格分隔；板块 id 或中文板块名均可
      // （id 用于复核/选股，中文名用于 screen 研究新增，新板块的 id 由流程按约定生成）
      const onlySectors = scope === 'only'
        ? String(values.sectors || '').split(/[,，、;；\s]+/).filter(Boolean)
        : []
      const onlyText = onlySectors.join('、')
      const stateFile = 'output/prosperity/state.yaml'
      const watchlist = 'output/watchlist/watchlist.yaml'
      const isPick = action === 'pick'
      // pick 的数量参数是「每板块目标支数」（建议 8–12、默认 10），硬边界 1–20：
      // 表单值超出硬边界时就近取边界，建议区间外的值视为用户的轻量/加量指定、照常执行。
      const perSectorRaw = parseInt(count, 10)
      const perSector = Number.isFinite(perSectorRaw) ? Math.min(20, Math.max(1, perSectorRaw)) : 10
      const lines = [
        `执行「景气板块选股」工具${isPick ? '的「选出景气标的」' : '的「筛选/复核景气板块」'}流程（景气投资跟踪状态：${stateFile}）。`,
        `本轮范围：${scope === 'all' ? '全部在跟板块（全量）' : scope === 'new' ? '仅扫描当前新热点板块（不动现有板块）' : `仅指定板块：${onlyText || '（未填写）'}`}——严格按范围执行，不越界处理范围外的板块。`,
        `第一步：所需脚本已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）：prosperity_state.py、manage_watchlist.py${isPick ? '、fetch_quotes.py、fetch_forecast.py（同花顺机构一致预期取数，无需 token），估值方法论文档已预置到 scripts/stock-valuation/（SKILL.md、data-source.md）' : ''}；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失对应脚本/文档，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `第二步（读状态）：python scripts/prosperity_state.py`,
      ]
      if (isPick) {
        if (scope === 'new') {
          lines.push(
            `范围「仅扫描新热点」对选股动作无意义：pick 是从已跟踪的景气板块里选股，不涉及新增板块扫描。直接向用户说明，请其改用「全部在跟板块」或「仅指定板块」后重跑，本轮不执行任何选股、不起 subagent。`,
          )
        } else if (scope === 'only' && onlySectors.length === 0) {
          lines.push(
            `选了「仅指定板块」却未填写指定板块：直接向用户说明并请其在「指定板块」栏填入板块 id 后重跑，本轮不执行任何选股、不起 subagent。`,
          )
        } else {
          lines.push(
          `门槛（硬拒绝）：若输出含「当前没有跟踪中的景气板块」（或状态文件不存在），拒绝本次请求并终止流程：向用户说明——景气标的必须先有景气板块名单，请先运行本工具的「筛选/复核景气板块」动作完成初始化或复核；不要凭印象选股、不要绕过该门槛。`,
          `第三步（分板块并行${scope === 'only' ? `，本轮范围：仅指定板块 ${onlyText}` : ''}）：对${scope === 'only' ? '指定板块各' : '每个在跟板块'}起一个后台 subagent，由它完成该板块的「选标的 → 取行情 → 景气估值」全流程${scope === 'only' ? '；先对照第二步在跟列表核对指定板块：在跟的才执行，不在跟的如实报告并略过、全部为不在跟则终止并请用户核对板块 id（pick 不支持凭指定名称新增板块，想新增板块请先跑 screen）；其余在跟板块本轮不动、不重选' : ''}；并发上限 3——先同时起前 3 个板块，之后每收到一个 subagent 完成通知就补起下一个未开始的板块，直到全部目标板块完成（目标板块 ≤3 个时一次性全起；会话没有 subagent 工具时退化为串行逐板块自行完成下述任务，流程与产出不变）。`,
          `每个 subagent 的提示词必须自包含（subagent 看不到当前会话），按下面整段模板填入该板块实际信息后发出：`,
          `「你是景气板块选股的板块执行单元，只负责下面这一个板块，不写观察仓、不改任何状态文件、只返回结构化结果不写文件。当前工作区 scripts/ 下已有 fetch_quotes.py 与 fetch_forecast.py、scripts/stock-valuation/ 下已有估值方法论文档（SKILL.md 与 data-source.md）。`,
          `板块信息：id=<板块id>，名称=<板块名>，入选原因=<reason>，最近复核结论=<note>（均取自状态输出）。`,
          `任务：1) 联网研究该板块候选，选出至多 ${perSector} 支（建议区间 8–12 支、默认 10${count && String(perSector) !== count ? `，表单值 ${count} 已按边界 1–20 就近修正` : ''}）与板块景气度相关性高的核心受益标的——候选范围仅 A 股（沪深主板/创业板/科创板/北交所，北交所标的可选；不选港股，其行情与一致预期取数链不全）——标准：主营业务与板块景气直接挂钩、营收/利润对板块量价敏感、业务纯度高不蹭概念、行业地位领先；板块内按纯度+龙头地位排序取舍，合格候选不足定额时按实际数量如实说明、不硬凑。2) 对全部候选跑 python scripts/fetch_quotes.py --snapshot <代码1,代码2,...>${tokenArg} 取最新收盘/PE/PB/市值等，再逐只跑 python scripts/fetch_forecast.py <代码> --mcap <该股总市值亿元> 取同花顺机构一致预期（净利润/EPS 年度汇总+机构明细+详细指标历史值；--mcap 用快照里的总市值÷10000 换算成亿元；缺 token 或任一取数失败：按脚本退出码与输出如实报告，不编造行情与预测）。3) 先完整阅读 scripts/stock-valuation/SKILL.md 与 scripts/stock-valuation/data-source.md（含口径局限：tushare 快照只有静态/TTM 两档 PE、港股缺估值字段与一致预期页、财务同比不在快照内），再按其框架逐只估值——识别生意属性 → 选估值口径 → 质量溢价/行业β横切校准 → 收敛方向性结论；景气标的按「景气成长/制造」主口径执行：前瞻/动态 PE + PEG + 周期中枢盈利（类 CAPE）+ 逆向 DCF 隐含增长率 + 相对估值分位，叠加质量溢价与拥挤度/交易结构；明确禁止用孤立静态 PE/PB 下「贵/便宜」结论（周期股 PE 悖论：盈利顶点静态 PE 最低恰是风险），输出前逐一过 SKILL.md 的「常见陷阱自检」清单；前瞻PE/PEG 的分母用第 2) 步 fetch_forecast.py 的净利润一致预期均值，结论注明覆盖机构数与预测年度，无覆盖/取数失败时明确标注数据缺口与替代口径（历史增速外推等），不编造；自己的估值结论与机构一致预期方向相悖或差距显著时，必须做差异分析——预期透支（现价隐含增速已超一致预期增速）/ 机构预测滞后（报告日期偏旧、景气拐点后未更新）/ 覆盖不足（机构数少且卖方偏乐观）/ 年内进度不符（最新披露业绩明显落后或超前全年预期）/ 口径差异（一次性收益等）/ 独立判断（自己判断的景气拐点早于卖方）——并明确采信立场，不回避分歧、不盲抄机构数字。4) 返回结构化结果：每只标的的 代码/名称/相关逻辑一句话/估值结论（口径+数值）/与机构一致预期的差异（无显著分歧或差距原因一句话）/风险点（全部标注数据时点），外加板块候选不足定额时的说明。」`,
          `第四步（汇总核查）：全部目标板块的 subagent 完成后合并结果：同一标的被多个板块选中时只保留一条，PS 归属相关逻辑更强的主板块、并在相关逻辑里注明另一板块；核查${scope === 'only' ? `指定板块 ${onlyText} ` : '每个在跟板块'}均已覆盖、各板块数量定额执行情况（含不足说明）如实转达；某板块 subagent 失败或超时 → 如实报告该板块缺口、可单独重跑该板块，不编造结果。`,
          `第五步（报告）：出一张总表（代码/名称/所属板块 PS id/相关逻辑一句话/估值结论（口径+数值）/与机构一致预期的差异/风险点），写入选股报告 output/prosperity/report_<时间戳>.md（含全部证据时点与各板块结论来源），并把表格完整呈现给用户。`,
          `第六步（先问后写）：询问用户「是否将上述标的加入观察仓？」，得到明确同意后逐只写入（幂等，已在仓的只补 param）：`,
          `  python scripts/manage_watchlist.py add <代码> --name "<名称>" --note "<景气逻辑一句话>" --source prosperity-picking`,
          `  python scripts/manage_watchlist.py set <代码> --PS <板块id> --PS_DATE <今日日期>`,
          `--PS（板块 id）与 --PS_DATE（打标日期）即板块 key，供板块日后剔除时按 PS=<板块id> 快速筛选删除该板块全部标的。用户不同意 → 只保留报告、不写观察仓，如实说明。`,
          `第七步：汇报写入结果（成功清单/已存在清单）与报告文件位置。`,
          )
        }
      } else {
        lines.push(
          `按第二步输出分流：`,
          `【分支 A：输出含「当前没有跟踪中的景气板块」（或状态文件不存在）→ 初始化】`,
          `第三步：按景气投资方法论（自上而下宏观定位→中观行业景气→业绩兑现确认；核心信号：行业盈利增速方向 + 分析师一致预期上修/下修的二阶导）联网检索当前 A股（沪深北，含北交所）处于盈利景气上行通道的板块（港股公司可作板块景气证据参考，但选标的仅 A 股；web_search 取证，标注数据时点，不编造）。`,
          `第四步：选出 2–5 个当前最景气的板块（宁缺毋滥，上行早段优先于情绪饱满的中后段），逐个给出：景气位置/景气信号（具体证据+时点）/盈利方向（一阶导+二阶导）/估值口径（前瞻PE/分位）/风险点。`,
          `第五步：逐个登记（板块 id 用小写英文短代号，如 ai-compute、robotics——它是观察仓 PS param 的值）：python scripts/prosperity_state.py add <板块id> --name "<板块名>" --reason "<入选原因与核心证据>"`,
          `第六步：把初始化报告写入 output/prosperity/report_<时间戳>.md（含每个板块的上述字段与证据时点），向用户完整汇报每个板块的入选原因。初始化不选个股、不写观察仓——那是「选出景气标的」动作的事。`,
        )
        // 分支 B（已存在在跟板块）按范围分流：all 全量复核+扫新增、new 只扫新热点、
        // only 只处理指定板块（在跟的重筛、未在跟的研究后新增），避免每次全量重跑。
        if (scope === 'only' && onlySectors.length === 0) {
          lines.push(
            `【范围说明】本轮选了「仅指定板块」却未填写指定板块：直接向用户说明并请其在「指定板块」栏填入板块 id 或中文板块名后重跑，本轮不做任何复核/新增。`,
          )
        } else if (scope === 'new') {
          lines.push(
            `【分支 B（已存在在跟板块；本轮范围：仅扫描当前新热点板块，不动任何现有板块）】`,
            `第三步：联网扫描当前市场上是否有新的景气板块值得新增（同分支 A 研究口径，标注数据时点；先与第二步输出的在跟板块比对去重——已在跟方向不重复登记）。本轮不对任何在跟板块重新取证、不改其复核结论。`,
            `第四步：筛出真正值得新增的板块（可为 0 个，宁缺毋滥；上行早段优先于情绪饱满的中后段），逐个登记：python scripts/prosperity_state.py add <板块id> --name "<板块名>" --reason "<入选原因与核心证据>"。没有值得新增的也要如实说明。`,
            `第五步：把本轮扫描报告写入 output/prosperity/report_<时间戳>.md（扫描口径/候选与取舍/新增结果，含证据时点），向用户完整汇报。`,
          )
        } else if (scope === 'only') {
          lines.push(
            `【分支 B（已存在在跟板块；本轮范围：仅处理指定板块 ${onlyText}，不碰其他板块、不做全市场新热点扫描）】`,
            `第三步：对照第二步在跟列表逐个核对指定板块——在跟的：联网取证复核（标注时点：景气点——盈利/订单/价格/预期上修等正向证据，与风险点——预期下修/拥挤度/估值分位过高/景气与股价脱钩等负向证据，分别列出）；不在跟的（用户指名要新增的方向，可以给中文名）：按分支 A 研究口径联网取证，确认处于盈利景气上行通道才纳入。`,
            `第四步：结论分三类处理：复核后仍景气 → python scripts/prosperity_state.py review <板块id> --note "<景气点/风险点一句话结论>"；值得新增（含用户指名方向）→ python scripts/prosperity_state.py add <板块id> --name "<板块名>" --reason "<入选原因>"（板块 id 用小写英文短代号，如 ai-compute、robotics——它是观察仓 PS param 的值）；复核后不再景气要剔除 → 必须先走第五步的用户确认，确认后才 python scripts/prosperity_state.py rm <板块id> --reason "<不再景气原因>"。未被指定的在跟板块本轮一律不动。`,
            `第五步（剔除板块必须先问）：对每个拟剔除板块，先询问用户「是否把观察仓中该板块的标的（PS=<板块id>）一并踢出观察仓？」：用户同意 → 先 python scripts/manage_watchlist.py list --where PS=<板块id> 精确筛出该板块全部观察仓标的，逐个 python scripts/manage_watchlist.py rm <代码>，踢完再 rm 板块；用户不同意或暂不处理 → 只剔除板块跟踪状态（rm 板块），观察仓条目原样保留。`,
            `第六步：把复核报告写入 output/prosperity/report_<时间戳>.md（本轮仅覆盖指定板块：各板块景气点/风险点/结论、剔除与新增汇总、观察仓联动结果，含证据时点），向用户完整汇报。`,
          )
        } else {
          lines.push(
            `【分支 B（已存在在跟板块；本轮范围：全量复核）】`,
            `第三步：对每个在跟板块重新联网取证（标注时点）：当前是否仍然景气——景气点（盈利/订单/价格/预期上修等正向证据）与风险点（预期下修/拥挤度/估值分位过高/景气与股价脱钩等负向证据）分别列出。`,
            `第四步：再扫描一次当前市场上是否有新的景气板块值得新增（同分支 A 研究口径）。`,
            `第五步：结论分三类处理：保留且仍景气 → python scripts/prosperity_state.py review <板块id> --note "<景气点/风险点一句话结论>"；新增 → python scripts/prosperity_state.py add <板块id> --name "<板块名>" --reason "<入选原因>"；剔除（不再景气）→ 必须先走第六步的用户确认，确认后才 python scripts/prosperity_state.py rm <板块id> --reason "<不再景气原因>"。`,
            `第六步（剔除板块必须先问）：对每个拟剔除板块，先询问用户「是否把观察仓中该板块的标的（PS=<板块id>）一并踢出观察仓？」：用户同意 → 先 python scripts/manage_watchlist.py list --where PS=<板块id> 精确筛出该板块全部观察仓标的，逐个 python scripts/manage_watchlist.py rm <代码>，踢完再 rm 板块；用户不同意或暂不处理 → 只剔除板块跟踪状态（rm 板块），观察仓条目原样保留。`,
            `第七步：把复核报告写入 output/prosperity/report_<时间戳>.md（每个板块的景气点/风险点/结论、剔除与新增汇总、观察仓联动结果），向用户完整汇报。`,
          )
        }
      }
      lines.push(
        `硬约束：`,
        `1. 所有景气/估值判断必须联网取证并标注数据时点，取不到证据的方向不下结论。`,
        `2. 板块 id 只用小写字母/数字/连字符（它是观察仓 PS param 的值，供快速筛选删除）。`,
        `3. 观察仓的增删一律走 manage_watchlist.py（add/set/rm），不直接改 ${watchlist}。`,
        `4. 输出仅供研究参考，不构成投资建议。`,
        `5. pick 的分板块 subagent 只做研究、取行情与估值并返回结构化结果，不写观察仓、不改状态文件；一切写入由主流程经用户确认后统一执行。`,
        `6. pick 候选标的仅 A 股（沪深主板/创业板/科创板/北交所），不选港股。`,
        `7. 估值结论与机构一致预期方向相悖或差距显著时，必须给出差异分析（预期透支/预测滞后/覆盖不足/年内进度不符/口径差异/独立判断）与采信立场，不回避分歧。`,
        `8. 严格遵守本轮范围：new 不改动任何现有板块，only 不处理指定以外的板块，pick 无 new 范围；范围与流程冲突时以范围校验提示为准、不越界执行。`,
      )
      if (focus) lines.push(`用户补充要求：${focus}（与上述流程或硬约束冲突时，以流程硬约束为准）。`)
      return lines.join('\n')
    }
    // watchlist-manager：观察仓清单的增删改查 + 其他工具 param 的透传写入口。
    // 与 w-bottom-screener 同一分发模式：脚本真源 src/workspace-init/manage_watchlist.py
    // 随插件包分发，由面板预置到工作区 scripts/ 后执行；纯标准库，无需联网/无需 MCP。
    if (skill.id === 'watchlist-manager') {
      const action = String(values.action || 'add').trim()
      const code = String(values.code || '').trim()
      const name = String(values.name || '').trim()
      const note = String(values.note || '').trim()
      const setParams = String(values.set_params || '').trim()
      const search = String(values.search || '').trim()
      if (action !== 'list' && !code) {
        return `「观察仓管理」${action} 操作需要股票代码：请在表单里填写「股票代码」（如 600519 / 600519.SH / 00700）后重新运行。`
      }
      if (action === 'set' && !setParams) {
        return `「观察仓管理」set 操作需要至少一对 param：请在表单「param 透传」里填写，如 --BS B --BS_DATE 2025-06-03，然后重新运行。`
      }
      const watchlist = 'output/watchlist/watchlist.yaml'
      let cmd = `python scripts/manage_watchlist.py --watchlist ${watchlist}`
      if (action === 'list') {
        cmd += ' list' // 显式 list 子命令（无搜索条件时等价于全量列出）
        if (search) cmd += ` ${search}`
      } else if (action === 'set') {
        cmd += ` set ${code} ${setParams}`
      } else if (action === 'rm' || action === 'check') {
        cmd += ` ${action} ${code}`
      } else {
        const extras = []
        if (name) extras.push(`--name "${name}"`)
        if (note) extras.push(`--note "${note}"`)
        cmd += ` add ${code}${extras.length > 0 ? ' ' + extras.join(' ') : ''}`
      }
      return [
        `用「观察仓管理」工具对观察仓标的池（${watchlist}）执行 ${action} 操作。`,
        `第一步：所需脚本 manage_watchlist.py 已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失该脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `该脚本为纯标准库自包含脚本，无需联网、无需 MCP、无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `第二步：${cmd}`,
        `行为说明：add 幂等（已在仓则只更新 name/note、保留首加日期）；清单不存在时 add 自动创建、其他操作会报错并提示；rm 对不在仓的标的返回退出码 1；check 退出码 0=在仓、1=不在（这是查询结果而非命令失败，如实转达）；set 只更新已在仓的条目（不在仓会报错，此时提示用户改用 add）；list 支持搜索——后接关键字（不区分大小写，包含匹配 代码/名称/备注/来源/param 值）或 --where KEY=VALUE 按 param 精确筛选（单写 KEY 表示键存在；可多对、多条件 AND；如 --where PS=ai-compute 筛某景气板块全部标的），两者可组合；无命中时输出「命中 0 只」且退出码 0（这是查询结果而非命令失败）。`,
        `第三步：把脚本的标准输出原样汇报给用户（list 会列出（或按搜索条件筛出）观察标的、各自备注与 param），不要省略条目。`,
      ].join('\n')
    }
    // feishen-trading-logic：决策框架型工具。
    // 目标工作区可能是空目录，会话里没有本仓库的 .agents/skills；因此不调用 skill，
    // 而是用面板预置到 scripts/feishen-trading-logic/ 的三份 Markdown 资产按其执行，
    // 个股行情证据由 fetch_quotes.py 直连补齐（同由面板预置）。
    if (skill.id === 'feishen-trading-logic') {
      const question = String(values.question || '').trim()
      if (!question) {
        return '「飞神交易逻辑」需要填写「问题/任务」后运行。'
      }
      const modeLabels = {
        auto: '自动判断',
        pool: '候选池 / 观察池',
        compare: '对比（多只/多方向）',
        stock: '单股分析',
        portfolio: '持仓 / 成本处理',
        review: '交易复盘',
      }
      const horizonLabels = {
        auto: '自动判断',
        short: '短线',
        swing: '波段',
        medium: '中期趋势',
        long: '长期跟踪',
      }
      const mode = modeLabels[values.mode] ? values.mode : 'auto'
      const horizon = horizonLabels[values.horizon] ? values.horizon : 'auto'
      const symbols = String(values.symbols || '').trim()
      const theme = String(values.theme || '').trim()
      const positionContext = String(values.position_context || '').trim()
      const assetDir = 'scripts/feishen-trading-logic'
      const lines = [
        `执行「飞神交易逻辑」工具。目标工作区可能不含本仓库的 .agents/skills：不要调用 skill，也不要寻找本地技能目录；该工具的方法论与参考文档以三份 Markdown 资产随插件分发。`,
        `第一步：四份资产（SKILL.md、feishen-rulebook.md、feishen-aliases.md、fetch_quotes.py）已由投研工具面板预置到当前工作区（前三份在 ${assetDir}/、脚本在 scripts/，均与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若对应目录下缺失资产，直接如实报告「面板资产预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `第二步：完整阅读 ${assetDir}/SKILL.md，然后阅读 ${assetDir}/feishen-rulebook.md 与 ${assetDir}/feishen-aliases.md；只允许用这三份本地资产实施该框架，不调用同名 skill。`,
        `任务：${question}`,
        `表单指定的分析模式：${modeLabels[mode]}；若与任务本身语义冲突，以任务语义而定，但不要降低证据要求。`,
        `表单指定的交易周期：${horizonLabels[horizon]}；若未明确，先由市场/板块/个股证据推断并说明。`,
      ]
      if (symbols) lines.push(`标的线索：${symbols}。如含昵称/谐音，先按预置的 feishen-aliases.md 解析；别名未确认且影响结论时必须留确认状态。`)
      if (theme) lines.push(`板块/题材线索：${theme}。先判断行业/题材阶段，再判断个股角色。`)
      if (positionContext) lines.push(`持仓/成本上下文：${positionContext}。按战略仓/战术仓、做T边界和失效条件处理。`)
      return lines.concat([
        `硬约束：`,
        `1. 严格按「市场环境→板块阶段→个股角色→量价触发→交易周期→成本仓位→条件动作」顺序推理，不允许从股票名直接跳到买卖结论。`,
        `2. 个股价格/量能证据优先用本地脚本校验：直接跑 python scripts/fetch_quotes.py --snapshot <代码>${tokenArg} 取最新收盘/涨跌幅/换手/PE/PB/市值快照（脚本已随面板预置到工作区 scripts/，纯标准库，${tokenSourceText}）；板块/市场级证据取不到时明确输出证据缺口，并只停留在候选观察/待验证，不编造价格、成交量、板块强弱或新闻。`,
        `3. 候选池模式只给方向级触发与失效条件；进入 1–3 只标的或问到买点、卖点、当前价格、剔除条件时，明确切换为个股/持仓模式，并给触发、持有、做T、减仓/退出、失效、证据与置信度。`,
        `4. 动作只使用条件式标签：核心观察、候选观察、持有观察、分层参与、做T观察、减仓/回避；不输出「必买」「必卖」。`,
        `5. 中长期结论必须给兑现窗口、中间验证节点、战略仓/战术仓处理，以及到期未兑现的重估条件。`,
        `6. 默认不主动把结论写回观察仓；确需持续跟踪时，仅在结尾建议使用「观察仓管理」添加对应标的，不直接修改任何清单。`,
        `7. 按 SKILL.md 模板输出人类可读结论，并在末尾提醒仅供研究参考，不构成投资建议。`,
      ]).join('\n')
    }
    // bili-video-summary：自包含脚本型 tool（与 w-bottom-screener 同一分发模式）。
    // 脚本真源 src/workspace-init/transcribe_video.py 随插件包分发，由面板预置到
    // 工作区 scripts/；目标工作区无需任何 .agents/skills 即可运行。依赖
    // yt-dlp + faster-whisper，非纯标准库，故指令里显式处理「依赖自检/补装 +
    // 模型缓存收拢 + 幂等跳过转录 + 读稿分析」。
    if (skill.id === 'bili-video-summary') {
      const video = String(values.video || '').trim()
      const model = (values.model && String(values.model).trim()) || 'small'
      const modelFlag = model && model !== 'small' ? ` --model ${model}` : ''
      return [
        `用「B站视频总结」工具总结一个 B站视频。视频：${video}（识别模型：${model}）`,
        `先给用户一句「时间预期与说明」再动手：首次运行需装依赖（yt-dlp、faster-whisper）并下载一次 whisper 模型（${model==='tiny'?'约75MB':model==='base'?'约145MB':'约460MB'}），这一步只在第一次发生、之后每台机器永久复用；转录本身约 ${model==='tiny'?'0.5~1':model==='base'?'0.6~1.2':'0.4~0.8'} 倍音频时长（cpu）。也就是说：长视频慢在所难免，但换用 base/tiny 会显著更快，且二次总结同一 BV 号直接秒出（跳过转录）。请把这段话简要转达给用户。`,
        `严格按以下步骤执行，不要跳步；环境相关步骤有明确边界：权限不足→提权重跑交用户审批，其它失败→停下转告用户，均不换路径。`,
        `第一步：所需三个脚本（transcribe_video.py、setup_runtime.py、fetch_quotes.py）已由投研工具面板预置到当前工作区 scripts/ 目录（与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若 scripts/ 下缺失对应脚本，直接如实报告「面板脚本预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `第二步（环境门禁，先判定再决定，不要上来就装）：在工作区根目录运行 \`python scripts/transcribe_video.py --selfcheck\`，看返回的 status 与退出码，按以下三分法处置：`,
        `  · status=python_missing（退出码 2，JSON 里无 python）：系统没有可用的 Python。这是「非插件问题」，**立即停下**，把 JSON 里的 hint 原样转告用户：请用户安装完整版 CPython 3.11+（勾选 Add to PATH）后自己重试；本工具**不会**自动安装 Python，也不做任何替代兜底。`,
        `  · status=ready（退出码 0）：环境已就绪，直接跳到第四步跑转录，跳过第三步。`,
        `  · status=deps_missing（退出码 1）：Python 可用但缺 yt-dlp/faster-whisper。这是插件可自动修复的步骤，进入第三步。`,
        `第三步（装依赖，插件自动，仅在 deps_missing 时执行）：运行 \`python scripts/setup_runtime.py --target .\` 建工作区持久 .venv 并装 yt-dlp、faster-whisper（可选 --with-opencc）。脚本幂等、带 [1/2][2/2] 进度；装完用 \`.venv\\Scripts\\python.exe scripts/transcribe_video.py --selfcheck\` 复核至 ready 再进第四步。安装失败按脚本退出码分流处置：`,
        `  · 退出码 3（需提权：安装撞权限拒绝，Errno 13 / WinError 5 / Access denied——典型是会话文件沙箱的受限令牌与 Python 建临时目录的行为冲突，venv/ensurepip/pip 都会因此失败）：**不要尝试任何替代路径，--target / monkeypatch / 换目录 / 换镜像 / 反复重试都会同样失败**。唯一动作：用本会话的提权机制（如 sandbox_permissions / danger-full-access）重跑**同一条** setup_runtime 命令，附一句理由交用户审批；批准则继续，拒绝则停下如实转告。`,
        `  · 退出码 1（网络/编译/磁盘等安装失败）或提权后仍失败：脚本已打印具体原因，**这是环境问题、非插件问题，立即停下，把原因原样转告用户、等待用户确认后再重试**，不要 monkeypatch、不要 --target 临时目录。`,
        `第四步（跑转录）：用 \`.venv\\Scripts\\python.exe scripts/transcribe_video.py "${video}"${modelFlag} --out output/videos --models output/videos/models\`。`,
        `该脚本已内置：B站 412 反爬、hf-mirror 镜像、关闭 Xet；whisper 模型缓存收拢到 output/videos/models/（有模型就用、没有才下载）；脚本按 [1/4][2/4][3/4][4/4] 打印阶段进度，把每阶段进度如实转达给用户。`,
        `脚本幂等：若 output/videos/<BV号>/transcript.txt 已存在会跳过转录直接输出「完成」；换 BV 号落在各自目录。`,
        `第五步：读取 output/videos/<BV号>/transcript.txt（连同 meta.json 的标题/UP主/时长）做后续分析。`,
        `第六步：按顺序输出结论：`,
        `  (a) 先按语境还原 whisper 谐音错/繁体错（人名、机构、术语不通时纠正，不照抄错字）；`,
        `  (b) 一段话内容摘要（视频在讲什么、结论是什么）；`,
        `  (c) 核心论点链——拆成「前提→推理→结论」，标每一步是事实还是推测；`,
        `  (d) 论据核查表——逐条可验证论据打「属实/基本属实/夸大或错误/无法验证」；A股/港股个股行情论据优先用 scripts/fetch_quotes.py --snapshot <代码>${tokenArg} 核对（脚本已随面板预置到工作区 scripts/，纯标准库，${tokenSourceText}），宏观等取不到的数据如实标注「无法验证」并提示自查方式，绝不编数据；`,
        `  (e) 方法论评价与立场提示——指出论证硬伤（如概率连乘当必然、样本偏差）与 UP 主立场；`,
        `  (f) 免责收尾：以上仅供研究参考，不构成投资建议。`,
      ].join('\n')
    }
    // stock-valuation：方法论资产 + 取数脚本型工具（与 feishen-trading-logic 同模式，不调用 skill）。
    // 目标工作区可能不含 .agents/skills：方法论文档（SKILL.md + data-source.md，预置到
    // scripts/stock-valuation/）与取数脚本（fetch_quotes.py / fetch_forecast.py /
    // fetch_snapshot.py，预置到 scripts/）均随插件分发、由面板在投递前预置到工作区，
    // 按其框架就地估值，任意工作区都能跑通完整取数链。
    if (skill.id === 'stock-valuation') {
      const symbol = String(values.symbol || '').trim()
      if (!symbol) {
        return '「个股估值」需要填写「股票名或代码」后运行。'
      }
      const isHK = String(values.market || 'A').trim() === 'HK'
      const assetDir = 'scripts/stock-valuation'
      return [
        `执行「个股估值」工具，估值对象：${symbol}（市场：${isHK ? '港股' : 'A股'}）。目标工作区可能不含本仓库的 .agents/skills：不要调用 skill，也不要寻找本地技能目录；该工具的方法论文档与取数脚本均随插件分发。`,
        `第一步：五份资产已由投研工具面板预置到当前工作区——方法论文档 SKILL.md 与 data-source.md 在 ${assetDir}/、取数脚本 fetch_quotes.py、fetch_forecast.py、fetch_snapshot.py 在 scripts/（均与插件同版本，每次投递自动覆盖更新）；无需下载、无需自行获取，直接从第二步开始执行。若对应目录下缺失资产，直接如实报告「面板资产预置异常」，不要跳过、不要去找别的本地路径碰运气。`,
        `第二步：完整阅读 ${assetDir}/SKILL.md 与 ${assetDir}/data-source.md（含口径局限：tushare 快照只有静态/TTM 两档 PE、港股缺一致预期取数链与部分估值字段、财务同比字段需逻辑自检），只按这两份本地文档的框架执行估值——识别生意属性 → 选估值锚口径 → 质量溢价/行业β 两个横切维度校准 → 收敛方向性结论，不调用同名 skill；三个取数脚本均为纯标准库自包含脚本，无需安装任何依赖。`,
        `第三步（解析交易代码）：若「${symbol}」是股票名称，先解析到唯一对应代码（A股 6 位、港股 5 位，可联网检索核对公司全称）；解析不出或有多只可能时，先向用户确认再取数，不要凭印象选代码。`,
        `第四步（取实时估值字段）：python scripts/fetch_quotes.py --snapshot <代码>${tokenArg}（${tokenSourceText}），一次取最新收盘/换手/PE 两口径/PB/市值/股息率；该脚本不可用或失败时退回 python scripts/fetch_snapshot.py <代码>（腾讯/东财双源直连，PE 三口径，旁路系统代理）。`,
        isHK
          ? `第五步（机构一致预期，本轮跳过）：港股无同花顺 F10 盈利预测页，一致预期取数链缺失——如实标注数据缺口；前瞻口径改用可得的公开预测或历史增速外推并注明替代口径，不编造机构预测数字。`
          : `第五步（取机构一致预期——前瞻PE/PEG 的分母）：python scripts/fetch_forecast.py <代码> --mcap <总市值亿元>（直连同花顺 F10 盈利预测页、无需 token；总市值亿元 = 快照输出「总市值（万元）÷ 1e4」）；无机构覆盖或取数失败时如实标注数据缺口与替代口径（历史增速外推等），不编造。`,
        `第六步（估值与汇报）：按 SKILL.md 的输出格式模板逐节输出（实时快照 / 一致预期 / 与预期差异 / 生意属性 / 估值口径 / 方向结论 / 胜负手 / 关键节点 / 风险），全部数据标注时点；输出前逐一过 SKILL.md 的「常见陷阱自检」清单。`,
        `硬约束：`,
        `1. 估值口径必须匹配生意属性（地产金融 PB+RNAV+股息率 / 周期资源中枢盈利+EV/EBITDA / 景气成长前瞻PE+PEG+周期中枢+逆向DCF / 稳定价值 DCF+股息率+稳态PE）；明确禁止拿 PE 套地产金融，或用孤立静态 PE/PB 下「贵/便宜」结论（周期股 PE 悖论：盈利顶点静态 PE 最低恰是风险）。`,
        `2. 行情与一致预期数字一律来自脚本输出，不做无来源估计；取不到数据如实标缺口，不编造。`,
        `3. 自己的方向结论与机构一致预期相悖或差距显著时，必须做差异分析（预期透支/预测滞后/覆盖不足/年内进度不符/口径差异/独立判断）并给出采信立场，不盲抄机构数字、不无据否定。`,
        `4. 结论需要持续跟踪时，建议用「观察仓管理」加入观察仓（add --note "<等什么/目标价>" --source stock-valuation），不直接改任何清单文件。`,
        `5. 输出仅供研究参考，不构成投资建议。`,
      ].join('\n')
    }
    const generic = `请调用 skill「${skill.id}」执行${skill.label}${paramsText}。${skill.description}`
    if (!token) return generic
    // 面板已配置 token：给走 skill 方法论的流程（持仓复核/抄作业等）补一行取数提示，
    // 让 fetch_quotes.py 不依赖目标工作区的 .env / 环境变量也能取到数。
    return `${generic}\n行情取数提示：工作区用 fetch_quotes.py 取数（估值快照/刷库）时，命令加 --token ${token}（来自投研面板配置；环境变量或工作区 .env 未配置时以此为准）。`
  }

  // ── 投递：把一条指令发到目标会话 ────────────────────────────────────────────
  // target: 'new'（新开会话）| 'current'（当前会话）
  //
  // 「新开会话」= 必须给出一个新会话，所以不走官方的 uiWorkspace.startSession：
  // 它内部是 reuseOrCreateBlank——同工作区里已有空白会话（侧边栏那行还没说过话的
  // 「新会话」、或刚打开应用时生成的空白会话）就直接复用它。用户点「新开会话」
  // 却看不到新会话、指令落进旧会话，根因就在这里（2026-10-13 排查）。
  // 这里自己解析目标工作区 → sessions.create 显式新建 → openSession 把主视图切过去
  // （openSession 内部 retain + 写 selection，新会话的 binding 才可用）。
  // 宿主缺少 create/openSession 时退回官方 startSession（复用空白会话优先）。
  // 读取 uiWorkspace 当前选中会话的 id。
  // 官方选中状态写在 uiWorkspace.selection（persist: dsh.sessions.current）里，
  // 不是 sessions.list；同时保留 list.current 做回退以备不同宿主版本差异。
  function readCurrentSessionId() {
    const uiWorkspace = runtime.uiWorkspace
    const fromSelection = uiWorkspace && uiWorkspace.selection && typeof uiWorkspace.selection.getSnapshot === 'function'
      ? uiWorkspace.selection.getSnapshot().sessionId
      : undefined
    const sessions = runtime.sessions
    const fromList = sessions && sessions.list && typeof sessions.list.getSnapshot === 'function'
      ? sessions.list.getSnapshot().current
      : undefined
    return fromSelection ?? fromList
  }

  async function submitInstruction(instruction, target, skill, values) {
    const sessions = runtime.sessions
    if (!sessions || typeof sessions.binding !== 'function') {
      throw new Error('未连接到会话服务，请刷新页面后重试。')
    }
    let sessionId
    if (target === 'new') {
      const uiWorkspace = runtime.uiWorkspace
      const canForceCreate = typeof sessions.create === 'function'
        && uiWorkspace && typeof uiWorkspace.openSession === 'function'
      if (canForceCreate) {
        // 强制新建：显式 create 一个新会话（不经过 reuseOrCreateBlank 的复用逻辑）。
        const workspaceId = resolveNewSessionWorkspaceId()
        if (!workspaceId) {
          throw new Error('未找到可用工作区，无法新开会话。请先在侧边栏打开一个工作区后重试。')
        }
        try {
          sessionId = await sessions.create({ workspaceId })
        } catch (e) {
          throw new Error(`新开会话失败（${e && e.message ? e.message : e}），指令未投递`)
        }
        // 打开新会话（retain + 选中 + 主视图切过去）：用户看到新会话，
        // 新会话的 binding 也才可用（sessions.binding 只认已 retain 的会话）。
        uiWorkspace.openSession(sessionId)
      } else {
        // 宿主未提供显式 create/openSession：退回官方 startSession（复用空白会话优先）。
        if (!uiWorkspace || typeof uiWorkspace.startSession !== 'function') {
          throw new Error('未连接到工作区服务，请刷新页面后重试。')
        }
        const before = readCurrentSessionId()
        uiWorkspace.startSession()
        sessionId = await waitForSessionChange(uiWorkspace, before)
        if (!sessionId) throw new Error('新开会话超时或失败，请重试。')
      }
    } else {
      sessionId = readCurrentSessionId()
      if (!sessionId) throw new Error('未找到当前会话，请先在对话中开启会话。')
    }

    // 预置该技能所需资产到目标会话所属工作区（指令零下载 URL 的前提）：
    // 预置失败必须在这里拦下、如实报错，绝不把「先去下载」式的指令投给 agent
    // （桌面原生外壳下会话 shell 拿不到插件端点，那种指令必然失败）。
    const assets = provisionAssetsFor(skill, values)
    if (assets.length > 0) {
      const workspaceRoot = await waitWorkspacePathForSession(sessionId)
      if (!workspaceRoot) {
        throw new Error('未能解析目标会话所属工作区的路径，无法预置脚本，指令未投递。请确认会话已连接到工作区后重试。')
      }
      await provisionAssets(workspaceRoot, assets)
    }

    const binding = await waitSessionBinding(sessionId)
    const session = binding && binding.session
    if (!session || typeof session.prompt !== 'function') {
      throw new Error('目标会话不可用（会话可能尚未就绪），请重试。')
    }
    const res = await session.prompt([{ type: 'text', text: instruction }], 'queue')
    if (res && res.ok === false) {
      throw new Error((res.error && res.error.message) || '发送失败')
    }
  }

  // 退回官方 startSession 时的等待（仅宿主无显式 create/openSession 的降级路径）：
  // startSession 是异步的（内部 openWorkspace → connectWorkspace → replaceMain 写 selection），
  // 订阅 uiWorkspace.selection 等选中会话换成新的 id；超时兜底返回当前选中的，
  // 覆盖「复用了同一个 blank 会话导致 id 没有变化」的情形（此时会把指令投给当前会话）。
  function waitForSessionChange(uiWorkspace, before, timeoutMs = 8000) {
    return new Promise((resolve) => {
      const sel = uiWorkspace && uiWorkspace.selection
      if (!sel || typeof sel.subscribe !== 'function' || typeof sel.getSnapshot !== 'function') {
        resolve(readCurrentSessionId())
        return
      }
      let done = false
      let unsubscribe = null // 需先声明：immediate 命中时 finish 同步执行，const 声明会踩 TDZ
      const finish = (value) => {
        if (done) return
        done = true
        if (unsubscribe) unsubscribe()
        resolve(value)
      }
      const timer = setTimeout(() => finish(readCurrentSessionId()), timeoutMs)
      // 先同步看一次（startSession 虽是 fire-and-forget，但命中同一工作区复用 blank 时可能极快）
      const immediate = sel.getSnapshot().sessionId
      if (immediate && immediate !== before) {
        clearTimeout(timer)
        finish(immediate)
        return
      }
      unsubscribe = sel.subscribe(() => {
        const cur = sel.getSnapshot().sessionId
        if (cur && cur !== before) {
          clearTimeout(timer)
          finish(cur)
        }
      })
    })
  }

  // ── 单字段渲染（参数 schema 驱动） ───────────────────────────────────────────
  function renderField(name, spec, value, onChange) {
    const val = value !== undefined && value !== null ? value : (spec.default !== undefined ? spec.default : '')
    const common = { value: val, style: inputStyle }
    let control
    if (spec.type === 'select') {
      control = React.createElement('select', Object.assign({}, common, {
        onChange: (e) => onChange(name, e.target.value),
      }), (spec.options || []).map((opt) =>
        React.createElement('option', { key: opt.value, value: opt.value }, opt.label)))
    } else {
      control = React.createElement('input', Object.assign({}, common, {
        type: spec.type === 'number' ? 'number' : 'text',
        placeholder: spec.placeholder || '',
        onChange: (e) => onChange(name, e.target.value),
      }))
    }
    return React.createElement('div', { key: name, style: fieldStyle },
      React.createElement('label', { style: labelStyle }, spec.label || name),
      control,
      spec.hint ? React.createElement('span', { style: hintStyle }, spec.hint) : null,
    )
  }

  // ── 技能表单主面板 ─────────────────────────────────────────────────────────
  // 作为主面板渲染（占满 main 区域），不再是模态弹窗。
  // 点侧边栏 panellist 图标 → ctx.layout.selectPanel('advisor-agent') → DSH 切换到本面板。
  function AdvisorPanel({ skills, config, defaultTarget }) {
    const [selectedId, setSelectedId] = useState(skills[0] ? skills[0].id : '')
    const [values, setValues] = useState({})
    const [target, setTarget] = useState(defaultTarget) // 'new' | 'current'
    const [busy, setBusy] = useState(false)
    const [feedback, setFeedback] = useState(null)

    const selected = useMemo(() => skills.find((s) => s.id === selectedId) || skills[0] || null, [skills, selectedId])

    // 首次打开即按默认选中的工具预填（含 workspace-init 的目标目录）。
    useEffect(() => {
      if (selected) resetValues(selected.id)
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [])

    // 切换工具时：清空表单，并对「目标目录」字段预填当前工作区的绝对路径，
    // 让用户直接在弹窗里看到并确认要生成的目录，而不是由 agent 事后猜。
    const resetValues = (skillId) => {
      const next = {}
      const s = skills.find((x) => x.id === skillId)
      if (s) {
        for (const [k, spec] of Object.entries(s.params)) {
          if (skillId === 'workspace-init' && k === 'target') {
            next[k] = resolveCurrentWorkspacePath() || ''
          } else if (spec.default !== undefined) {
            next[k] = spec.default
          }
        }
      }
      setValues(next)
    }

    const handleSubmit = async () => {
      if (!selected || busy) return
      for (const [k, spec] of Object.entries(selected.params)) {
        const v = values[k]
        if (spec.required && (v === undefined || v === null || String(v).trim() === '')) {
          setFeedback({ kind: 'err', text: `请填写「${spec.label || k}」` })
          return
        }
      }
      setBusy(true)
      setFeedback(null)
      try {
        // 提交时再读一次配置兜底：面板打开期间（如另一标签页）新保存的 token 也能立即生效；
        // 读取失败则沿用打开时的快照，不会退回默认值丢掉 token。
        const cfg = await readConfig(config)
        // 投递前由面板把该技能所需脚本/文档预置进目标工作区（submitInstruction 内完成，
        // 预置失败会在此抛错并阻止投递，不会把含下载步骤的指令发给 agent）。
        await submitInstruction(buildInstruction(selected, values, cfg), target, selected, values)
        // 投递成功：通常通过 layout 切换到会话面板，这里留本面板但提示已投递。
        setFeedback({ kind: 'ok', text: '指令已投递，可切换到「会话」面板查看执行过程' })
      } catch (e) {
        setFeedback({ kind: 'err', text: (e && e.message) || String(e) })
      } finally {
        setBusy(false)
      }
    }

    // 主面板布局：占满 main 区域，左边一列说明，右边一列表单。
    const pageStyle = {
      display: 'flex', flexDirection: 'column', gap: 16,
      padding: 24, width: '100%', maxWidth: 720, margin: '0 auto',
      overflowY: 'auto', height: '100%',
      boxSizing: 'border-box',
      background: cssVars.surface,
      color: cssVars.inputFg,
    }
    return React.createElement('div', { style: pageStyle, role: 'main', 'aria-label': '投研工具' },
      React.createElement('div', { style: { display: 'grid', gap: 6 } },
        React.createElement('h2', { style: { margin: 0, fontSize: 18, fontWeight: 600 } }, '投研工具'),
        React.createElement('p', { style: { margin: 0, opacity: 0.7, fontSize: 13 } },
          '选择工具 → 填参数 → 投递到目标会话执行任务。')),
      React.createElement('div', { style: fieldStyle },
        React.createElement('span', { style: labelStyle }, '选择工具'),
        React.createElement('select', {
          style: inputStyle, value: selected ? selected.id : '',
          onChange: (e) => { setSelectedId(e.target.value); resetValues(e.target.value); setFeedback(null) },
        }, skills.map((s) => React.createElement('option', { key: s.id, value: s.id }, s.label))),
        selected ? React.createElement('span', { style: hintStyle }, selected.description) : null),
      selected ? Object.entries(selected.params).map(([k, spec]) =>
        renderField(k, spec, values[k], (n, v) => setValues((p) => Object.assign({}, p, { [n]: v })))) : null,
      React.createElement('div', { style: fieldStyle },
        React.createElement('span', { style: labelStyle }, '投递到'),
        React.createElement('select', {
          style: inputStyle, value: target, onChange: (e) => setTarget(e.target.value),
        },
          React.createElement('option', { value: 'new' }, '新开会话'),
          React.createElement('option', { value: 'current' }, '当前会话'))),
      React.createElement('button', {
        type: 'button', onClick: handleSubmit, disabled: busy,
        style: busy ? Object.assign({}, buttonStyle, { opacity: 0.5, cursor: 'not-allowed' }) : buttonStyle,
      }, busy ? '运行中…' : '运行'),
      feedback ? React.createElement('span', {
        style: { fontSize: 12, color: feedback.kind === 'err' ? cssVars.err : cssVars.ok, marginTop: 2 },
      }, feedback.text) : null,
    )
  }

  // ── 设置卡片（技能开关 + 默认目标会话） ─────────────────────────────────────
  function SettingsCard() {
    const [local, setLocal] = useState(DEFAULT_CONFIG)
    const [busy, setBusy] = useState(false)
    const [status, setStatus] = useState(null)
    const [ready, setReady] = useState(false)
    const [tokenDraft, setTokenDraft] = useState('')

    useEffect(() => {
      let active = true
      readConfig().then((c) => { if (active) { setLocal(c); setReady(true) } })
      return () => { active = false }
    }, [])

    const commit = async (patch) => {
      setBusy(true)
      setStatus(null)
      try {
        const next = await writeConfig(patch)
        setLocal(next)
      } catch (e) {
        setStatus((e && e.message) || String(e))
      } finally {
        setBusy(false)
      }
    }

    const toggleSkill = (id, checked) => {
      const set = new Set(local.enabledSkills)
      if (checked) set.add(id)
      else set.delete(id)
      void commit({ enabledSkills: [...set] })
    }

    const cardStyle = {
      listStyle: 'none', border: cssVars.card, borderRadius: 12, padding: 16,
      background: cssVars.surface, display: 'grid', gap: 12, margin: 0,
    }
    const row = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }

    return React.createElement('li', { style: cardStyle, 'data-testid': 'advisor-agent-settings' },
      React.createElement('div', null,
        React.createElement('strong', { style: { fontSize: 15 } }, '投研工具'),
        React.createElement('p', { style: { margin: '4px 0 0', opacity: 0.72 } },
          '在侧边栏提供“投研工具”入口，以表单化方式调用投顾技能（个股估值等）。')),
      React.createElement('div', { style: row },
        React.createElement('span', null, '启用入口'),
        React.createElement('input', {
          type: 'checkbox', checked: local.enabled !== false, disabled: busy || !ready,
          onChange: (e) => void commit({ enabled: e.target.checked }),
        })),
      ...ADVISOR_SKILLS.map((skill) => React.createElement('div', { key: skill.id, style: { display: 'grid', gap: 4 } },
        React.createElement('div', { style: row },
          React.createElement('div', null,
            React.createElement('div', { style: { fontWeight: 600, fontSize: 13 } }, skill.label),
            React.createElement('small', { style: { opacity: 0.6 } }, skill.id)),
          React.createElement('input', {
            type: 'checkbox', checked: local.enabledSkills.includes(skill.id), disabled: busy || !ready,
            onChange: (e) => toggleSkill(skill.id, e.target.checked),
          })))),
      React.createElement('div', { style: row },
        React.createElement('span', null, '默认投递目标'),
        React.createElement('select', {
          value: local.defaultTarget || 'new', disabled: busy || !ready, style: inputStyle,
          onChange: (e) => void commit({ defaultTarget: e.target.value }),
        },
          React.createElement('option', { value: 'new' }, '新开会话'),
          React.createElement('option', { value: 'current' }, '当前会话'))),
      React.createElement('div', { style: { display: 'grid', gap: 6 } },
        React.createElement('span', { style: labelStyle }, '行情数据（tushare token）'),
        React.createElement('div', { style: row },
          React.createElement('input', {
            type: 'password', value: tokenDraft, disabled: busy || !ready, style: inputStyle,
            placeholder: local.tushareToken
              ? '已配置（输入新值覆盖；清空后保存即移除）'
              : '粘贴 tushare pro token；留空则用环境变量 TUSHARE_TOKEN / 工作区 .env',
            onChange: (e) => setTokenDraft(e.target.value),
          }),
          React.createElement('button', {
            type: 'button', disabled: busy || !ready,
            onClick: () => {
              void commit({ tushareToken: tokenDraft.trim() })
              setTokenDraft('')
            },
          }, '保存')),
        React.createElement('span', { style: hintStyle },
          '配置后随工具指令以 --token 参数传给 fetch_quotes.py（tushare pro token 见 https://tushare.pro/user/token）；'
          + '留空保存则回退环境变量 TUSHARE_TOKEN / 工作区 .env。注意：token 会随指令文本提交给执行会话，介意可改用环境变量方式。')),
      busy ? React.createElement('small', { role: 'status' }, '正在保存…') : null,
      status ? React.createElement('small', { style: { color: cssVars.err } }, `保存失败：${status}`) : null,
    )
  }

  // ── 侧边栏入口按钮（sidebar.footer.action） ─────────────────────────────────
  // 与 agents-anywhere-next（远程控制）/ 设置 对齐：
  // - 图标 + 标签（宽态），仅图标 + title（56px rail 窄态）
  // - hover / active 底纹 + circular 转角，与 sidebar footer 按钮家族一致
  // ownerProps.wide 由侧边栏主控传入；插件在窄态下主动收起 label。

  // 投研工具的 outline 图标（与 DSH 图标同款的 24×24、stroke 1.75、端点圆角风格）。
  // 选「上升趋势折线」：M3 17 至 M21 6 加末端箭头，简洁的图表语义与「插件」「自动化任务」权重一致。
  const AdvisorIcon = (props) => React.createElement('svg', {
    width: props.size || 20,
    height: props.size || 20,
    viewBox: '0 0 24 24',
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.75,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
    'aria-hidden': true,
  },
    // 上升折线
    React.createElement('path', { d: 'M3 17 L9 11 L13 14 L21 6' }),
    // 折线末端箭头
    React.createElement('path', { d: 'M16 6 L21 6 L21 11' }),
  )

  // 按钮样式已改为 inline style（直接挂在 JSX 元素上），不再走 styles.insert 注入；
  // 这样能避开宿主打包机制的不确定性，确保改动立刻在浏览器生效。

  // 侧边栏 panellist 图标按钮：点击调用 layout.selectPanel('advisor-agent')
  // 切换到本插件的主面板。
  function AdvisorPanelIcon({ size }) {
    return React.createElement(AdvisorIcon, {
      size: typeof size === 'number' ? size : 18,
    })
  }

  // panellist 槽位的 content：DSH 主控为我们渲染「图标按钮 + label」，
  // 我们的 component 只负责**图标本身**。ownerProps: { size, active }。
  // 点击与高亮由 DSH 通过 id/main key 同名机制处理，无需我们写 onClick 或按钮外层。
  function AdvisorLauncher(props) {
    const size = props && typeof props.size === 'number' ? props.size : 20
    return React.createElement(AdvisorPanelIcon, { size })
  }

  // 主面板组件：包装 AdvisorPanel，从配置加载技能清单后再渲染。
  // DSH 在 panellist 点图标时把这个 entry 显示到 main 区域。
  function AdvisorMainPanel() {
    const [config, setConfig] = useState(DEFAULT_CONFIG)
    useEffect(() => {
      let active = true
      readConfig().then((c) => { if (active) setConfig(c) })
      return () => { active = false }
    }, [])
    const enabled = config.enabled !== false
    const enabledSkills = ADVISOR_SKILLS.filter((s) => config.enabledSkills.includes(s.id))
    if (!enabled || enabledSkills.length === 0) {
      return React.createElement('div', {
        style: { padding: 24, color: cssVars.inputFg },
      }, '投研工具未启用，请在「设置 → 插件」里打开。')
    }
    return React.createElement(AdvisorPanel, {
      skills: enabledSkills,
      config,
      defaultTarget: config.defaultTarget || 'new',
    })
  }

  // ── apply：注入三个槽位（panellist 图标 + main 主面板 + 设置卡片） ────────────
  function apply(ctx) {
    try {
      runtime.sessions = ctx.get('sessions')
    } catch (e) {}
    try {
      runtime.workspaces = ctx.get('workspaces')
    } catch (e) {}
    try {
      runtime.uiWorkspace = ctx.get('uiWorkspace')
    } catch (e) {}
    try {
      runtime.layout = ctx.get('layout')
    } catch (e) {}

    const injectSidebarIcon = () => {
      try {
        // sidebar.panellist：DSH 标准的「全局面板图标」位置，与「插件」「自动化任务」并列；
        // 一个 entry 独占一行。id 必须与 main key 同名 —— DSH 通过 id 自动把
        // 点击映射到对应 main panel 而非弹层。
        ctx.slots.register({
          name: 'sidebar.panellist',
          key: 'advisor-agent',
          id: 'advisor-agent',
          // 显示名：与「插件」「自动化任务」对齐的中文标签
          label: '投研工具',
          order: 100,
          inject: () => ({}),
        }, AdvisorLauncher)
      } catch (e) {
        if (console && console.error) console.error('[advisor-agent] sidebar panellist slot failed:', e)
      }
    }
    const injectMainPanel = () => {
      try {
        // main 槽注册 keyed entry：key='advisor-agent' 与 panellist id 对应；
        // 当用户在 panellist 点「投研工具」图标时，DSH 把主面板内容切到这个 entry。
        ctx.slots.register({
          name: 'main',
          key: 'advisor-agent',
          inject: () => ({}),
        }, AdvisorMainPanel)
      } catch (e) {
        if (console && console.error) console.error('[advisor-agent] main panel slot failed:', e)
      }
    }
    const injectSettings = () => {
      try {
        ctx.slots.register({
          name: 'settings.plugin.item',
          key: 'advisor-agent',
          id: 'advisor-agent',
          order: 40,
          inject: () => ({}),
        }, SettingsCard)
      } catch (e) {
        if (console && console.error) console.error('[advisor-agent] settings slot failed:', e)
      }
    }
    try { ctx.slots.inject('sidebar.panellist', injectSidebarIcon) } catch (e) {}
    try { ctx.slots.inject('main', injectMainPanel) } catch (e) {}
    try { ctx.slots.inject('settings.plugin.item', injectSettings) } catch (e) {}
  }

  module.exports = {
    name: 'advisor-agent-client',
    inject: ['slots', 'sessions', 'workspaces', 'uiWorkspace', 'layout'],
    apply,
  }
  return module.exports
} })