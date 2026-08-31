# 数据源直连方法与字段映射

## 首选数据源：tushare MCP（agent 在会话内直接调用）

通过 `mcp__tushareMcp__*` 工具直接取数（无需写脚本、无系统代理问题、字段稳定）。工具名即 tushare pro API 名：`daily`（日线）、`daily_basic`（每日估值指标）、`stock_basic`（名称/行业）、`fina_indicator`（财务指标）、`income`（利润表）、港股则 `hk_daily`/`hk_basic`。

- `ts_code` 格式（非 6 位数字）：沪市 `600519.SH` / 深市 `000333.SZ`（创业板 `300xxx.SZ`）/ 科创板 `688xxx.SH` / 北交所 `8xxxxx.BJ` / 港股 `00700.HK`。6 位代码需按交易所补后缀。

### 字段映射（技能字段 → tushare 工具/字段）

| 技能字段 | 工具 | 字段 | 说明 |
|---|---|---|---|
| 名称 / 行业 | `stock_basic` | `name` / `industry` | |
| 现价 | `daily_basic` | `close` | 也等价于 `daily.close` 最新行 |
| 昨收 / 涨跌幅% | `daily` | `pre_close` / `pct_chg` | `pct_chg` 单位已是 %（如 `-0.05` = -0.05%） |
| PE(静态) | `daily_basic` | `pe` | 总市值/上年净利，亏损为空 |
| PE(TTM) | `daily_basic` | `pe_ttm` | 滚动 12 个月 |
| PB | `daily_basic` | `pb` | |
| 股息率% | `daily_basic` | `dv_ratio` / `dv_ttm` | |
| 换手率% | `daily_basic` | `turnover_rate` | |
| 总市值(亿) | `daily_basic` | `total_mv` | 单位**万元**，÷1e4 得亿 |
| 流通市值(亿) | `daily_basic` | `circ_mv` | 单位万元，÷1e4 |
| 营收同比% | `fina_indicator` | `or_yoy` / `q_gr_yoy` | 取最新报告期 |
| 归母净利同比% | `fina_indicator` | `netprofit_yoy` / `q_netprofit_yoy` | 同上 |
| 毛利率% / 净利率% | `fina_indicator` | `grossprofit_margin` / `netprofit_margin` | |
| ROE% | `fina_indicator` | `roe` / `roe_dt` | |
| 每股净资产 | `fina_indicator` | `bps` | 地产/金融 PB 估值锚 |

### 口径与覆盖注意

- **PE 三口径只有两档**：tushare 只给 `pe`(静态) 与 `pe_ttm`(TTM)，**没有腾讯源的「PE动/前瞻(年化)」**——做前瞻 PE 用 `pe_ttm` 或按前瞻盈利自行折算，勿硬找"动态 PE"。
- **港股限制**：tushare MCP 侧港股仅 `hk_daily`（价/量）与 `hk_basic`（名称），**缺 PB/PE/市值/股息率**等估值字段；港股估值指标退回下方腾讯源。
- 财务同比(`fina_indicator`)由报告期驱动，取最新已披露的 `end_date` 一行即可；绝对值(`income`)另行取。

---

以下接口为**兜底方案**（tushare 不可用或需港股估值字段时）。原因：本机 `requests`/akshare 会读取 **Windows 系统代理**，而该代理对本机部分数据域名（东财 `82.push2.eastmoney.com` 等）不可达，会抛 `ProxyError` 并让 CLI 回退到 `LocalProvider` 示例数据；而 Git Bash 下的 `curl` **不读系统代理**，可直连这些公开接口。

## 可用接口（腾讯行情快照，实测可用）

### 1. 腾讯行情 `qt.gtimg.cn`（最稳定，GBK 编码）
```
curl -sS "https://qt.gtimg.cn/q=sz002244" | iconv -f gbk -t utf-8
```
- 代码前缀：深市 `sz`（主板 000/002、创业板 300）、沪市 `sh`（600/688）。
- 返回 `v_sz002244="51~滨江集团~002244~9.09~...`，按 `~` 分隔，字段从 0 起（下表序号=索引）：

| 索引 | 字段 | 例(滨江集团) |
|---|---|---|
| 1 | 名称 | 滨江集团 |
| 3 | 现价 | 9.09 |
| 4 | 昨收 | 9.16 |
| 30 | 时间 `YYYYMMDDHHMMSS` | 20260817161412 |
| 31 | 涨跌 | -0.07 |
| 32 | 涨跌幅% | -0.76 |
| 38 | 换手率% | 0.97 |
| 39 | **PE(TTM)** | 14.50 |
| 44 | 流通市值(亿) | 250.66 |
| 45 | **总市值(亿)** | 282.83 |
| 46 | **市净率 PB** | 0.94 |
| 52 | **PE(动/年化)** | 8.72 |
| 53 | **PE(静/上年度)** | 13.36 |

### 2. 东财个股 `push2.eastmoney.com`（JSON，含经营同比）
```
curl -sS "https://push2.eastmoney.com/api/qt/stock/get?secid=0.002244&invt=2&fltt=2&fields=f43,f60,f116,f117,f162,f163,f164,f167,f168,f169,f170,f183,f184,f185,f186,f187,f188"
```
- `secid` 前缀：深 `0`、沪 `1`（`0.002244`、`1.600519`）。
- 偶尔返回「Failure when receiving data」，重试或退回腾讯源。

| 字段 | 含义 |
|---|---|
| f43 | 现价 |
| f60 | 昨收 |
| f116 / f117 | 总市值 / 流通市值（元，除以 1e8 为亿） |
| f162 / f163 / f164 | PE(动) / PE(静) / PE(TTM) |
| f167 | 市净率 PB |
| f168 | 换手率% |
| f169 / f170 | 涨跌 / 涨跌幅% |
| f183 / f184 | 营业收入 / 营收同比% |
| f185 / f186 | 归母净利 / 净利同比% |
| f187 / f188 | 净利率% / 毛利率% |

## 双源一致性
价格、PB、PE三口径两源应基本一致（会因"动态/静态/TTM"口径差异略有不同，属正常）。若差异大，先看是否把 PE 静/TTM/动 混淆——三类口径本身就是不同分母，**不要混用**。

## 免责
字段随接口版本可能变动，以脚本解析为准；数据均标注时点。
