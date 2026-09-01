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
  const DEFAULT_CONFIG = { enabled: true, enabledSkills: ['stock-valuation', 'copy-trade', 'workspace-init'], defaultTarget: 'new', tushareMcpUrl: '' }

  async function readConfig() {
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
      console.error('[advisor-agent] readConfig failed, using defaults:', e)
      return Object.assign({}, DEFAULT_CONFIG)
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
  function buildInstruction(skill, values) {
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
      return [
        `在目录 ${target} 初始化投资顾问工作区。`,
        `第一步：从 ${assetUrl} 下载 init_workspace.py（用 PowerShell 的 Invoke-WebRequest 或 curl，原样保存为 UTF-8 到本地临时路径）。`,
        `第二步：python <下载到的脚本路径> --target ${target}`,
        `若下载返回 404/失败，直接如实报告"脚本资产端点不可用"，不要跳过、不要去找别的本地路径碰运气。`,
        `该脚本会创建：output/copy-trade/（抄作业 copy-trade 回测产物）、output/reports/（个股分析 stock-valuation 研报）、output/w-bottom/ 与 output/watchlist/（W底搜索 w-bottom-screener 报告与观察仓标的池）、output/portfolio/（持仓清单）、output/sectors/（景气快照）、output/skill-state/（知识更新状态）、output/videos/（B站视频转录）、knowledge/（个股知识库，含 stocks/ 清单索引与模板）。`,
        `并写入 README.md、.gitignore、holdings.yaml、watchlist.yaml、update-manifest.yaml、alias-map.override.yaml、knowledge/index.md、knowledge/stocks/_template.md 等模板文件（仅当文件不存在时）。`,
        `执行完成后，用一句话汇报生成了哪些目录与模板文件、哪些已存在被跳过。`,
      ].join('\n')
    }
    return `请调用 skill「${skill.id}」执行${skill.label}${paramsText}。${skill.description}`
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
        await submitInstruction(buildInstruction(selected, values), target)
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
    const [urlDraft, setUrlDraft] = useState(null) // null = 尚未编辑；否则为用户正在输入的草稿
    const [urlStatus, setUrlStatus] = useState(null)

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

    // 保存 tushare MCP URL：写回宿主 → settings.update → 立即重连（立即生效）。
    const saveMcpUrl = async () => {
      const next = urlDraft === null ? (local.tushareMcpUrl || '') : urlDraft
      setBusy(true)
      setUrlStatus(null)
      try {
        const saved = await writeConfig({ tushareMcpUrl: next })
        setLocal(saved)
        setUrlDraft(null)
        setUrlStatus({ kind: 'ok', text: '已保存并立即生效' })
      } catch (e) {
        setUrlStatus({ kind: 'err', text: (e && e.message) || String(e) })
      } finally {
        setBusy(false)
      }
    }

    const cardStyle = {
      listStyle: 'none', border: cssVars.card, borderRadius: 12, padding: 16,
      background: cssVars.surface, display: 'grid', gap: 12, margin: 0,
    }
    const row = { display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16 }
    const urlValue = urlDraft === null ? (local.tushareMcpUrl || '') : urlDraft

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
        React.createElement('span', { style: labelStyle }, 'tushare MCP 地址'),
        React.createElement('span', { style: hintStyle },
          '粘贴完整 MCP URL（含 token 查询参数）；保存后立即重连，工具沿用 mcp__tushareMcp__* 名称。'),
        React.createElement('div', { style: { display: 'flex', gap: 8, alignItems: 'center' } },
          React.createElement('input', {
            type: 'password', value: urlValue, placeholder: 'http://…/dingall?token=…',
            disabled: busy || !ready, style: Object.assign({}, inputStyle, { flex: 1 }),
            onChange: (e) => setUrlDraft(e.target.value),
            onKeyDown: (e) => { if (e.key === 'Enter') void saveMcpUrl() },
          }),
          React.createElement('button', {
            type: 'button', onClick: () => void saveMcpUrl(), disabled: busy || !ready,
            style: Object.assign({}, buttonStyle, { justifySelf: 'auto' }),
          }, '保存')),
        urlStatus ? React.createElement('small', {
          style: { color: urlStatus.kind === 'err' ? cssVars.err : cssVars.ok },
        }, urlStatus.text) : null),
      busy ? React.createElement('small', { role: 'status' }, '正在保存…') : null,
      status ? React.createElement('small', { style: { color: cssVars.err } }, `保存失败：${status}`) : null,
    )
  }

  // ── 侧边栏入口按钮（sidebar.footer.action） ─────────────────────────────────
  function AdvisorLauncher() {
    const [config, setConfig] = useState(DEFAULT_CONFIG)
    const [open, setOpen] = useState(false)

    useEffect(() => {
      let active = true
      readConfig().then((c) => { if (active) setConfig(c) })
      return () => { active = false }
    }, [])

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