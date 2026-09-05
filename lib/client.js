window.__ModuleLoader__.load({ id: 'advisor-agent', factory: (require) => {
  const module = { exports: {} }
  const exports = module.exports
  const React = require('react')
  const { useEffect, useMemo, useRef, useState } = React

  const CONFIG_ENDPOINT = '/plugins/advisor-agent/config'
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
  }

  // ── 配置读写（/config 端点，宿主侧提供） ─────────────────────────────────────
  const DEFAULT_CONFIG = { enabled: true, enabledSkills: ['stock-valuation', 'copy-trade', 'workspace-init', 'w-bottom-screener', 'bili-video-summary', 'feishen-trading-logic'], defaultTarget: 'new', tushareToken: '' }

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

  function resolveTargetWorkspaceId() {
    const view = resolveTargetWorkspace()
    return view ? view.workspaceId : undefined
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
    // workspace-init：产出确定性的执行指令。
    // 脚本随插件分发，通过宿主静态端点下载（ASSET_ENDPOINT）。这是唯一可靠来源：
    // 目标工作区 cwd 下不存在仓库本地的 .agents/skills 路径，不能拿它当回退。
    if (skill.id === 'workspace-init') {
      const target = (values.target && String(values.target).trim()) || resolveCurrentWorkspacePath() || '.'
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/init_workspace.py`
      const setupUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/setup_runtime.py`
      return [
        `在目录 ${target} 初始化投资顾问工作区。`,
        `第一步：把两个脚本下载到当前工作区 scripts/ 目录（不存在则先创建）：从 ${assetUrl} 下载 init_workspace.py、从 ${setupUrl} 下载 setup_runtime.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8，每次覆盖下载，保证版本与插件一致）。`,
        `第二步：python scripts/init_workspace.py --target ${target}`,
        `若下载返回 404/失败，直接如实报告"脚本资产端点不可用"，不要跳过、不要去找别的本地路径碰运气。`,
        `两个脚本自身均为纯标准库（setup_runtime.py 负责装第三方依赖，它自己不需要依赖）；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `该脚本会创建：output/copy-trade/（抄作业 copy-trade 回测产物）、output/reports/（个股分析 stock-valuation 研报）、output/w-bottom/ 与 output/watchlist/（W底搜索 w-bottom-screener 报告与观察仓标的池）、output/quotes-store/（共享本地行情库，fetch_quotes.py 刷库写回）、output/momentum/（动量轮动报告与持仓状态）、output/portfolio/（持仓清单）、output/sectors/（景气快照）、output/skill-state/（知识更新状态）、output/videos/（B站视频转录）、knowledge/（个股知识库，含 stocks/ 清单索引与模板）。`,
        `并写入 README.md、.gitignore、holdings.yaml、watchlist.yaml、update-manifest.yaml、alias-map.override.yaml、knowledge/index.md、knowledge/stocks/_template.md 等模板文件（仅当文件不存在时）。`,
        `第三步（可选，供后续 B站视频总结工具直接复用）：若用户要用 B站视频总结，运行 \`python scripts/setup_runtime.py --target ${target}\` 建工作区持久 .venv 并装 yt-dlp、faster-whisper（可选 --with-opencc）。此步幂等、首次较慢，非必须时不主动跑；B站工具跑时会按需自行调用。若该脚本报 python_missing（退出码 2，系统无可用 Python）或安装失败（退出码 1），这是「非插件问题」：如实把原因转告用户、请用户确认后处理，不要自动安装 Python、不要换路径兜底。若报退出码 3（需提权：安装撞权限拒绝 Errno 13 / WinError 5），唯一动作是用会话提权机制重跑同一条命令、交用户审批，不换路径。`,
        `执行完成后，用一句话汇报生成了哪些目录与模板文件、哪些已存在被跳过。`,
      ].join('\n')
    }
    // w-bottom-screener：两步确定性执行（fetch_quotes.py 刷库 → w_bottom_screen.py 读库判定）。
    // 两个脚本均自包含（纯标准库）随插件分发，通过宿主静态端点下载；无仓库 .agents/skills 也能跑。
    // 行情由脚本直连 tushare REST API 拉取并写本地 CSV 行情库，全程不经过任何 LLM 上下文。
    if (skill.id === 'w-bottom-screener') {
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const fetchUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py`
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/w_bottom_screen.py`
      const watchlist = 'output/watchlist/watchlist.yaml'
      const extras = []
      if (values.lookback && String(values.lookback).trim() !== '') extras.push(`--lookback ${values.lookback}`)
      if (values.trough_tol && String(values.trough_tol).trim() !== '') extras.push(`--trough-tol ${values.trough_tol}`)
      const extraText = extras.length > 0 ? ` ${extras.join(' ')}` : ''
      return [
        `执行「观察仓 W底放量筛选」流程。`,
        `第一步：把两个脚本下载到当前工作区 scripts/ 目录（不存在则先创建）：从 ${fetchUrl} 下载 fetch_quotes.py、从 ${assetUrl} 下载 w_bottom_screen.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8；每次覆盖下载，保证版本与插件一致）。`,
        `若下载返回 404/失败，直接如实报告"脚本资产端点不可用"，不要跳过、不要去找别的本地路径碰运气。`,
        `两个脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `第二步（刷库）：python scripts/fetch_quotes.py --watchlist ${watchlist} --min-bars 30 --full-days 90${tokenArg}`,
        `脚本直连 tushare REST API，对照本地行情库 output/quotes-store/ 把每只增量补到最新交易日（新票或库内不足 30 根的自动全量取近 90 自然日），幂等合并写回；输出只有每只一行入库摘要，K 线明细不会出现，把摘要原样转达给用户即可。`,
        `第二步失败分流（退出码见脚本输出）：退出码 2（缺 token）→ 如实转告用户按提示配置 TUSHARE_TOKEN（--token 参数 / 环境变量 / 工作区 .env 写 TUSHARE_TOKEN=…）后重跑第二步；退出码 1 或 3（网络/接口失败、部分标的失败）→ 原样转告失败清单与原因，不要编造行情、不要跳过刷库直接判定。`,
        `第三步（判定）：python scripts/w_bottom_screen.py --watchlist ${watchlist}${extraText}`,
        `脚本直接读行情库判定 W底+放量形态（库内数据缺失或过期超过 10 自然日会 fail-closed 并提示先刷库），输出命中报告 output/w-bottom/screen_*.md；把报告内容与命中清单原样汇报给用户。`,
      ].join('\n')
    }
    // momentum-rotation：对观察仓所有标的做「中期动量排名 → 目标组合」。
    // 取数与计算分离：fetch_quotes.py 直连 tushare 刷库（行情不经过任何 LLM 上下文），
    // momentum_strategy.py 直接读本地行情库计算，两步出结果。
    if (skill.id === 'momentum-rotation') {
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const fetchUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py`
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/momentum_strategy.py`
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
        `第一步：把两个脚本下载到当前工作区 scripts/ 目录（不存在则先创建）：从 ${fetchUrl} 下载 fetch_quotes.py、从 ${assetUrl} 下载 momentum_strategy.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8；每次覆盖下载，保证版本与插件一致）。若下载返回 404/失败，直接如实报告"脚本资产端点不可用"，不要跳过、不要去找别的本地路径碰运气。`,
        `两个脚本均为纯标准库自包含脚本，无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码，直接执行即可。`,
        `第二步（刷库）：python scripts/fetch_quotes.py --watchlist ${watchlist} --min-bars 121 --full-days 420${tokenArg}`,
        `脚本直连 tushare REST API，对照本地行情库 output/quotes-store/ 把每只增量补到最新交易日（新票或库内不足 121 根的自动全量取近 420 自然日），幂等合并写回；输出只有每只一行入库摘要，K 线明细不会出现，把摘要原样转达给用户即可。`,
        `第二步失败分流（退出码见脚本输出）：退出码 2（缺 token）→ 如实转告用户按提示配置 TUSHARE_TOKEN（--token 参数 / 环境变量 / 工作区 .env 写 TUSHARE_TOKEN=…）后重跑第二步；退出码 1 或 3（网络/接口失败、部分标的失败）→ 原样转告失败清单与原因，不要编造行情、不要跳过刷库直接计算。`,
        `第三步（计算）：python scripts/momentum_strategy.py --watchlist ${watchlist} --state ${stateFile}${extraText}`,
        `脚本直接读行情库计算 mom20/mom120/mom60 三条动量 + 三道过滤 + 快慢双榜（buffer16 粘性、快4慢4补满）算出目标持仓（库内数据缺失或过期超过 10 自然日会 fail-closed 并提示先刷库），标准输出只打印摘要（大盘状态、目标持仓表、调仓变动——老仓被剔除的标的及原因、新增汇总）——「逐只动量与过滤」全池明细不打印、只写入完整报告 output/momentum/plan_<时间戳>.md，并回写持仓状态 ${stateFile}。`,
        `把标准输出中的摘要、目标持仓与调仓变动（被剔除的老仓及原因、新增）原样汇报给用户，不要自行改写或省略数字；「逐只动量与过滤」明细不要读入或整表复述，向用户提示报告 md 文件位置即可（需要逐只明细时让用户直接打开该文件）。`,
        ...(marketGuardOff ? [`注意：本次按表单选择关闭了大盘状态关（--market-guard false），即使大盘状态为下行脚本也会照常选股出目标组合；报告中仍照常展示大盘状态（regime），请人工确认大盘风险敞口。`] : []),
      ].join('\n')
    }
    // watchlist-manager：观察仓清单的增删改查 + 其他工具 param 的透传写入口。
    // 与 w-bottom-screener 同一分发模式：脚本真源 src/workspace-init/manage_watchlist.py
    // 随插件包分发，经宿主静态端点下载执行；纯标准库，无需联网/无需 MCP。
    if (skill.id === 'watchlist-manager') {
      const action = String(values.action || 'add').trim()
      const code = String(values.code || '').trim()
      const name = String(values.name || '').trim()
      const note = String(values.note || '').trim()
      const setParams = String(values.set_params || '').trim()
      if (action !== 'list' && !code) {
        return `「观察仓管理」${action} 操作需要股票代码：请在表单里填写「股票代码」（如 600519 / 600519.SH / 00700）后重新运行。`
      }
      if (action === 'set' && !setParams) {
        return `「观察仓管理」set 操作需要至少一对 param：请在表单「param 透传」里填写，如 --BS B --BS_DATE 2025-06-03，然后重新运行。`
      }
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/manage_watchlist.py`
      const watchlist = 'output/watchlist/watchlist.yaml'
      let cmd = `python scripts/manage_watchlist.py --watchlist ${watchlist}`
      if (action === 'list') {
        cmd += '' // 无子命令即 list
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
        `第一步：把脚本下载到当前工作区 scripts/ 目录（不存在则先创建）：从 ${assetUrl} 下载 manage_watchlist.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8 到 scripts/manage_watchlist.py；每次覆盖下载，保证版本与插件一致）。`,
        `若下载返回 404/失败，直接如实报告"脚本资产端点不可用"，不要跳过、不要去找别的本地路径碰运气。`,
        `该脚本为纯标准库自包含脚本，无需联网、无需 MCP、无需安装任何依赖；用法已在后续命令中完整给出，无需阅读或分析脚本源码。`,
        `第二步：${cmd}`,
        `行为说明：add 幂等（已在仓则只更新 name/note、保留首加日期）；清单不存在时 add 自动创建、其他操作会报错并提示；rm 对不在仓的标的返回退出码 1；check 退出码 0=在仓、1=不在（这是查询结果而非命令失败，如实转达）；set 只更新已在仓的条目（不在仓会报错，此时提示用户改用 add）。`,
        `第三步：把脚本的标准输出原样汇报给用户（list 会列出全部观察标的、各自备注与 param），不要省略条目。`,
      ].join('\n')
    }
    // feishen-trading-logic：决策框架型工具。
    // 目标工作区可能是空目录，会话里没有本仓库的 .agents/skills；因此不调用 skill，
    // 而是下载随插件分发的三份 Markdown 资产后按其执行，个股行情证据由 fetch_quotes.py 直连补齐。
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
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const skillUrl = `${origin}/plugins/advisor-agent/assets/feishen-trading-logic/SKILL.md`
      const rulebookUrl = `${origin}/plugins/advisor-agent/assets/feishen-trading-logic/feishen-rulebook.md`
      const aliasesUrl = `${origin}/plugins/advisor-agent/assets/feishen-trading-logic/feishen-aliases.md`
      const assetDir = 'scripts/feishen-trading-logic'
      const fetchQuotesUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py`
      const lines = [
        `执行「飞神交易逻辑」工具。目标工作区可能不含本仓库的 .agents/skills：不要调用 skill，也不要寻找本地技能目录；该工具的方法论与参考文档以三份 Markdown 资产随插件分发。`,
        `第一步：把资产下载到当前工作区 ${assetDir}/ 目录（不存在则先创建）：从 ${skillUrl} 下载 SKILL.md、从 ${rulebookUrl} 下载 feishen-rulebook.md、从 ${aliasesUrl} 下载 feishen-aliases.md（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8，每次覆盖下载，保证版本与插件一致）。`,
        `若任一资产下载返回 404/失败，直接如实报告「飞神资产端点不可用」，不要跳过、不要去找别的本地路径碰运气。`,
        `第二步：完整阅读 ${assetDir}/SKILL.md，然后阅读 ${assetDir}/feishen-rulebook.md 与 ${assetDir}/feishen-aliases.md；只允许用这三份本地资产实施该框架，不调用同名 skill。`,
        `任务：${question}`,
        `表单指定的分析模式：${modeLabels[mode]}；若与任务本身语义冲突，以任务语义而定，但不要降低证据要求。`,
        `表单指定的交易周期：${horizonLabels[horizon]}；若未明确，先由市场/板块/个股证据推断并说明。`,
      ]
      if (symbols) lines.push(`标的线索：${symbols}。如含昵称/谐音，先按下载解析的 feishen-aliases.md 解析；别名未确认且影响结论时必须留确认状态。`)
      if (theme) lines.push(`板块/题材线索：${theme}。先判断行业/题材阶段，再判断个股角色。`)
      if (positionContext) lines.push(`持仓/成本上下文：${positionContext}。按战略仓/战术仓、做T边界和失效条件处理。`)
      return lines.concat([
        `硬约束：`,
        `1. 严格按「市场环境→板块阶段→个股角色→量价触发→交易周期→成本仓位→条件动作」顺序推理，不允许从股票名直接跳到买卖结论。`,
        `2. 个股价格/量能证据优先用本地脚本校验：工作区 scripts/ 下没有 fetch_quotes.py 时，先从 ${fetchQuotesUrl} 下载（纯标准库，${tokenSourceText}），然后跑 python scripts/fetch_quotes.py --snapshot <代码>${tokenArg} 取最新收盘/涨跌幅/换手/PE/PB/市值快照；板块/市场级证据取不到时明确输出证据缺口，并只停留在候选观察/待验证，不编造价格、成交量、板块强弱或新闻。`,
        `3. 候选池模式只给方向级触发与失效条件；进入 1–3 只标的或问到买点、卖点、当前价格、剔除条件时，明确切换为个股/持仓模式，并给触发、持有、做T、减仓/退出、失效、证据与置信度。`,
        `4. 动作只使用条件式标签：核心观察、候选观察、持有观察、分层参与、做T观察、减仓/回避；不输出「必买」「必卖」。`,
        `5. 中长期结论必须给兑现窗口、中间验证节点、战略仓/战术仓处理，以及到期未兑现的重估条件。`,
        `6. 默认不主动把结论写回观察仓；确需持续跟踪时，仅在结尾建议使用「观察仓管理」添加对应标的，不直接修改任何清单。`,
        `7. 按下载的 SKILL.md 模板输出人类可读结论，并在末尾提醒仅供研究参考，不构成投资建议。`,
      ]).join('\n')
    }
    // bili-video-summary：自包含脚本型 tool（与 w-bottom-screener 同一分发模式）。
    // 脚本真源 src/workspace-init/transcribe_video.py 随插件包分发，经宿主静态端点下载；
    // 目标工作区无需任何 .agents/skills 即可运行。依赖 yt-dlp + faster-whisper，非纯标准库，
    // 故指令里显式处理「依赖自检/补装 + 模型缓存收拢 + 幂等跳过转录 + 读稿分析」。
    if (skill.id === 'bili-video-summary') {
      const video = String(values.video || '').trim()
      const model = (values.model && String(values.model).trim()) || 'small'
      const modelFlag = model && model !== 'small' ? ` --model ${model}` : ''
      const origin = (typeof window !== 'undefined' && window.location && window.location.origin) || 'http://127.0.0.1:3080'
      const assetUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/transcribe_video.py`
      const setupUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/setup_runtime.py`
      const fetchQuotesUrl = `${origin}/plugins/advisor-agent/assets/workspace-init/fetch_quotes.py`
      return [
        `用「B站视频总结」工具总结一个 B站视频。视频：${video}（识别模型：${model}）`,
        `先给用户一句「时间预期与说明」再动手：首次运行需装依赖（yt-dlp、faster-whisper）并下载一次 whisper 模型（${model==='tiny'?'约75MB':model==='base'?'约145MB':'约460MB'}），这一步只在第一次发生、之后每台机器永久复用；转录本身约 ${model==='tiny'?'0.5~1':model==='base'?'0.6~1.2':'0.4~0.8'} 倍音频时长（cpu）。也就是说：长视频慢在所难免，但换用 base/tiny 会显著更快，且二次总结同一 BV 号直接秒出（跳过转录）。请把这段话简要转达给用户。`,
        `严格按以下步骤执行，不要跳步；环境相关步骤有明确边界：权限不足→提权重跑交用户审批，其它失败→停下转告用户，均不换路径。`,
        `第一步：把两个脚本下载到当前工作区 scripts/ 目录（不存在则先创建）：从 ${assetUrl} 下载 transcribe_video.py、从 ${setupUrl} 下载 setup_runtime.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8，每次覆盖下载保证版本与插件一致）。若任一脚本下载 404/失败，直接如实报告「脚本资产端点不可用」，不要跳过去找别的本地路径碰运气。`,
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
        `  (d) 论据核查表——逐条可验证论据打「属实/基本属实/夸大或错误/无法验证」；A股/港股个股行情论据优先用 scripts/fetch_quotes.py --snapshot <代码>${tokenArg} 核对（工作区 scripts/ 下没有该脚本时先从 ${fetchQuotesUrl} 下载；纯标准库，${tokenSourceText}），宏观等取不到的数据如实标注「无法验证」并提示自查方式，绝不编数据；`,
        `  (e) 方法论评价与立场提示——指出论证硬伤（如概率连乘当必然、样本偏差）与 UP 主立场；`,
        `  (f) 免责收尾：以上仅供研究参考，不构成投资建议。`,
      ].join('\n')
    }
    const generic = `请调用 skill「${skill.id}」执行${skill.label}${paramsText}。${skill.description}`
    if (!token) return generic
    // 面板已配置 token：给走 skill 方法论的流程（估值/持仓复核/抄作业等）补一行取数提示，
    // 让 fetch_quotes.py 不依赖目标工作区的 .env / 环境变量也能取到数。
    return `${generic}\n行情取数提示：工作区用 fetch_quotes.py 取数（估值快照/刷库）时，命令加 --token ${token}（来自投研面板配置；环境变量或工作区 .env 未配置时以此为准）。`
  }

  // ── 投递：把一条指令发到目标会话 ────────────────────────────────────────────
  // target: 'new'（新开会话）| 'current'（当前会话）
  //
  // 「新开会话」落在哪个工作区，遵循官方的解析顺序（见 dsh workspace 的 startSession 语义）：
  //   1. 当前会话所属的工作区（用户正在看的会话在哪个目录，新会话就跟在哪个目录）
  //   2. 最近活动的工作区（recentWorkspaceId）
  //   3. 第一个工作区
  // 这样「在另一个工作区开会话再点投研工具」时，新会话不会串到别的工作区去。
  async function submitInstruction(instruction, target) {
    const sessions = runtime.sessions
    if (!sessions || typeof sessions.binding !== 'function') {
      throw new Error('未连接到会话服务，请刷新页面后重试。')
    }
    let sessionId
    if (target === 'new') {
      // 复用官方「新建会话」语义：复用/新建 blank session，并拿回已在 list 里的 id。
      const ws = runtime.workspaces
      const wsId = resolveTargetWorkspaceId()
      if (wsId) {
        sessionId = await ws.connectWorkspace(wsId)
      }
      if (!sessionId) {
        // 无工作区可用：退化为新建+等一会再找 blank；再退化为当前会话。
        const cur = sessions.list && sessions.list.getSnapshot ? sessions.list.getSnapshot().current : undefined
        sessionId = cur
      }
      if (sessionId) {
        if (typeof sessions.open === 'function') sessions.open(sessionId)
      } else {
        throw new Error('无法新开会话（没有可用工作区），请先在工作区创建会话后再试。')
      }
    } else {
      const listState = sessions.list && sessions.list.getSnapshot ? sessions.list.getSnapshot() : {}
      sessionId = listState.current
      if (!sessionId) throw new Error('未找到当前会话，请先在对话中开启会话。')
    }

    const binding = sessions.binding(sessionId)
    const session = binding && binding.session
    if (!session || typeof session.prompt !== 'function') {
      throw new Error('目标会话不可用（会话可能尚未就绪），请重试。')
    }
    const res = await session.prompt([{ type: 'text', text: instruction }], 'queue')
    if (res && res.ok === false) {
      throw new Error((res.error && res.error.message) || '发送失败')
    }
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

  // ── 技能表单弹窗 ──────────────────────────────────────────────────────────
  function AdvisorDialog({ skills, config, defaultTarget, onClose }) {
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
        // 提交时再读一次配置兜底：面板开着时（如另一标签页）新保存的 token 也能立即生效；
        // 读取失败则沿用打开时的快照，不会退回默认值丢掉 token。
        const cfg = await readConfig(config)
        await submitInstruction(buildInstruction(selected, values, cfg), target)
        // 投递成功（新开会话时已把焦点切到目标会话），直接关闭弹窗，让用户回到对话。
        onClose()
      } catch (e) {
        setFeedback({ kind: 'err', text: (e && e.message) || String(e) })
      } finally {
        setBusy(false)
      }
    }

    return React.createElement('div', {
      style: overlayStyle,
      onMouseDown: (e) => { if (e.target === e.currentTarget) onClose() },
    },
      React.createElement('div', {
        style: dialogStyle,
        role: 'dialog',
        'aria-label': '投研工具',
        onMouseDown: (e) => e.stopPropagation(),
      },
        React.createElement('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' } },
          React.createElement('strong', { style: { fontSize: 15 } }, '投研工具'),
          React.createElement('button', {
            type: 'button', onClick: onClose, 'aria-label': '关闭',
            style: { border: 'none', background: 'none', cursor: 'pointer', fontSize: 16, color: cssVars.inputFg, opacity: 0.6 },
          }, '×')),
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
      ))
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
  function AdvisorLauncher() {
    const [config, setConfig] = useState(DEFAULT_CONFIG)
    const [open, setOpen] = useState(false)

    // 挂载时读一次；每次打开面板再重读一次——设置页刚保存的 tushareToken / 技能开关
    // 无需刷新页面即可生效（设置卡片与入口按钮是两个组件，状态不会自动同步）。
    useEffect(() => {
      let active = true
      readConfig().then((c) => { if (active) setConfig(c) })
      return () => { active = false }
    }, [open])

    const enabled = config.enabled !== false
    const enabledSkills = ADVISOR_SKILLS.filter((s) => config.enabledSkills.includes(s.id))

    return React.createElement(React.Fragment, null,
      React.createElement('button', {
        type: 'button',
        onClick: () => setOpen(true),
        disabled: !enabled || enabledSkills.length === 0,
        style: {
          display: 'flex', alignItems: 'center', gap: 8, padding: '7px 10px',
          borderRadius: 8, border: '1px solid transparent', background: 'none',
          color: cssVars.inputFg, cursor: 'pointer', width: '100%',
          opacity: (!enabled || enabledSkills.length === 0) ? 0.4 : 1,
          fontSize: 13,
        },
        title: '投研工具',
      }, '投研工具'),
      open && enabledSkills.length > 0
        ? React.createElement(AdvisorDialog, {
            skills: enabledSkills, config, defaultTarget: config.defaultTarget || 'new',
            onClose: () => setOpen(false),
          })
        : null,
    )
  }

  // ── apply：注入两个槽位（side panel 入口 + 设置卡片） ───────────────────────
  function apply(ctx) {
    try {
      runtime.sessions = ctx.get('sessions')
    } catch (e) {}
    try {
      runtime.workspaces = ctx.get('workspaces')
    } catch (e) {}

    const injectSidebar = () => {
      try {
        ctx.slots.register({
          name: 'sidebar.footer.action',
          key: 'advisor-agent',
          id: 'advisor-agent',
          order: 50,
          inject: () => ({}),
        }, AdvisorLauncher)
      } catch (e) {
        if (console && console.error) console.error('[advisor-agent] sidebar slot failed:', e)
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
    try { ctx.slots.inject('sidebar.footer.action', injectSidebar) } catch (e) {}
    try { ctx.slots.inject('settings.plugin.item', injectSettings) } catch (e) {}
  }

  module.exports = {
    name: 'advisor-agent-client',
    inject: ['slots', 'sessions', 'workspaces'],
    apply,
  }
  return module.exports
} })