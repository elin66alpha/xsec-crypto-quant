# 阶段 −0.5：数据可得性审计表

> 目的（CLAUDE.md 阶段 −0.5）：**避免把 edge 押在拿不到历史数据的特征上。**
> 原则：历史 L2 盘口（OBI/WMP/Depth）在交易所 REST 拿不到历史 → **整体删除或降级为"从现在起录制、仅供未来 paper trading"**，不进历史回测。
>
> 本表是进入阶段 1 的前置门槛：**只有"是否可回测 = ✅"的特征才允许进入因子工程。**

## 交易所与接口

- 交易所：**OKX USDT 本位永续合约**（swap），通过 `ccxt`（`okx`）访问。
  - 交易所选择由 Binance 改为 **OKX**：本项目运行网络为美国 IP，Binance 全球站（`fapi.binance.com`）对美 IP 返回 **451「restricted location」**（合规封锁，非故障），而 OKX 永续公共接口可直连。CLAUDE.md 的统计诚实根决策（横截面/多空中性/动态池/FDR/locked holdout/杠杆红线）不受影响，仅交易所标的来源更换。
- 数据全部走 REST（决策 B）：OHLCV、funding rate、合约信息。OKX 历史 K线/funding 单次最多返回 100 条，需分页（`data/fetcher.py` 已处理）。
- 时间口径：日频（1d）bar，UTC。信号用收盘后因子值，下一根 bar 开盘成交（决策 9）。

## 审计表

| 特征 / 数据 | 用途 | 数据源 | 接口（ccxt 统一） | 历史起始 | 获取成本 | 是否可回测 | 处置 |
|---|---|---|---|---|---|---|---|
| **日线 OHLCV** | 截面动量、波动率、收益 | OKX swap | `fetch_ohlcv(tf='1d')` | 各合约上市日 | 免费，分页 REST（≤100/次） | ✅ | 进回测，数据层核心 |
| **成交量（base volume）** | 成交量变化因子、动态池排名 | OKX swap | OHLCV 第 6 列 | 同上 | 免费 | ✅ | 进回测；美元成交额≈`close×volume`（近似，见说明①） |
| **Funding rate** | carry 因子（决策 3）+ 逐期损益结算 | OKX swap | `fetch_funding_rate_history` | 各合约上市日（8h 一次） | 免费，分页 REST | ✅ | 进回测，一等公民因子 + 成本项 |
| **合约元信息 / 现存永续列表** | 动态池候选集 | OKX swap | `load_markets()` | 实时快照 | 免费 | ⚠️ 见说明② | 进回测，但需配合历史 OHLCV 推断上市/退市 |
| **历史市值快照** | 动态池可选排名口径、BTC 主导率（regime） | CoinGecko | REST `/coins/markets` | 2013+ | 免费档有限速 | ✅ | 备用：本项目动态池以 OKX 成交额为主口径 |
| **CVD / Trade Delta** | 订单流，**仅二次确认**（非主力） | OKX trades | `fetch_trades` | 理论可回溯但量极大、成本高 | 高（逐笔，TB 级） | ⚠️ 降级 | 暂不进历史回测；如需，仅"从现在起录制"，明确标注确认项 |
| **L2 盘口（OBI / WMP / Depth）** | 微观结构 | OKX books | `fetch_order_book`（仅当前快照） | ❌ **无历史** | — | ❌ | **删除**。仅"从现在起录制，供未来 paper trading"，永不进历史回测 |

## 说明

**① 美元成交额近似**
`ccxt.fetch_ohlcv` 仅返回 `[ts, open, high, low, close, volume]`，其中 `volume` 是**基础资产**成交量。本项目动态池排名（决策 1 的"过去 30 天平均成交额"）用 `dollar_volume ≈ close × volume` 作为美元成交额代理。OKX 原始 candle 含 `volCcyQuote`（计价币成交额）字段，未来若需精确值可改走原始 `publicGetMarketHistoryCandles` 取该字段。当前近似对"按成交额排 Top N"的排序结论影响很小，记录在案。

**② 幸存者偏差与现存合约列表（决策 1 铁律）**
`load_markets()` 只返回**当前仍在交易**的永续，直接用它回测过去 = 幸存者偏差（LUNA、FTT 等已退市币被剔除，收益系统性高估）。
应对：
- 退市币的 OHLCV 在 OKX 下架后通常**无法再通过 REST 取到**。因此真正干净的历史池需要一份**上市/退市日历**。
- 本阶段先实现"基于现存合约 + 其 OHLCV 数据存在区间推断上市日"的版本，可正确支撑**近段时间**（最近数月）的池构建与最小验收测试。
- **早期年份（含退市币）的完整无偏池构建留作后续补强**：需引入退市日历（CoinGecko 上市/退市记录或归档的历史合约清单）。该限制必须在回测报告中显式声明，严禁默默用现存池回测早期年份。

## 进入阶段 1 的结论

允许进入因子工程的历史可得特征：
- ✅ 截面动量（OHLCV close）
- ✅ 波动率排名（OHLCV 已实现波动率）
- ✅ 成交量变化（OHLCV volume）
- ✅ funding carry（funding rate history）

降级 / 删除：
- ⚠️ CVD/Trade Delta —— 仅未来录制，标注为确认项，不进历史回测
- ❌ L2 盘口全系列 —— 删除，仅未来 paper trading 录制

待补强（不阻塞阶段 1，但阻塞早期年份无偏回测）：
- 退市日历 → 早期年份完整动态池
