# 阶段 −0.5：数据可得性审计表

> 目的（CLAUDE.md 阶段 −0.5）：**避免把 edge 押在拿不到历史数据的特征上。**
> 原则：历史 L2 盘口（OBI/WMP/Depth）在交易所 REST 拿不到历史 → **整体删除或降级为"从现在起录制、仅供未来 paper trading"**，不进历史回测。
>
> 本表是进入阶段 1 的前置门槛：**只有"是否可回测 = ✅"的特征才允许进入因子工程。**

## 交易所与接口

- 交易所：**OKX USDT 本位永续合约**（swap），通过 `ccxt`（`okx`）访问。
  - 交易所选择由 Binance 改为 **OKX**：本项目运行网络为美国 IP，Binance 全球站（`fapi.binance.com`）对美 IP 返回 **451「restricted location」**（合规封锁，非故障），而 OKX 永续公共接口可直连。CLAUDE.md 的统计诚实根决策（横截面/多空中性/动态池/FDR/locked holdout/杠杆红线）不受影响，仅交易所标的来源更换。
- 数据全部走 REST / public archives（决策 B）：OHLCV、funding rate、合约信息。OKX 历史 K线单次最多返回 100 条，需分页（`data/fetcher.py` 已处理）。对 OKX 已下架且 REST 不再返回的早期合约，日线 K 线走 Binance Vision USDT-M daily/monthly klines 归档 fallback。回测期 funding 统一走 Binance Vision USDT-M monthly `fundingRate` 归档（2026-06-13 用户确认，见说明②）。
- 时间口径：日频（1d）bar，UTC。信号用收盘后因子值，下一根 bar 开盘成交（决策 9）。

## 审计表

| 特征 / 数据 | 用途 | 数据源 | 接口（ccxt 统一） | 历史起始 | 获取成本 | 是否可回测 | 处置 |
|---|---|---|---|---|---|---|---|
| **日线 OHLCV** | 截面动量、波动率、收益 | OKX swap；退市 fallback: Binance Vision | `fetch_ohlcv(source='okx'/'binance_vision')` | 各合约上市日；fallback 覆盖归档存在区间 | 免费，分页 REST / daily zip | ✅ | 进回测，数据层核心；退市合约按日历路由 fallback |
| **成交量（base volume）** | 成交量变化因子、动态池排名 | OKX swap；退市 fallback: Binance Vision | OHLCV 第 6 列 | 同上 | 免费 | ✅ | 进回测；美元成交额≈`close×volume`（近似，见说明①） |
| **Funding rate** | carry 因子（决策 3）+ 逐期损益结算 | Binance Vision USDT-M fundingRate archive；OKX live funding 从 Phase 6 起记录 | `fetch_binance_vision_funding`（backtest）；`fetch_funding_rate_history`（OKX live/recent only） | Binance Vision monthly 归档实测覆盖 BTC/ETH 至 2020-08；单币以 archive 月份为准 | 免费，monthly zip；404 月份为 coverage gap | ✅/⚠️ | 回测期所有进池 asset_id 统一用 Binance funding 归档；缺失 archive 月/日按 0 处理并在 validation 披露；Phase 6 用 OKX live funding 做一致性验证 |
| **合约元信息 / 永续日历** | 动态池候选集 | OKX instruments + 手工核验退市 fallback | `metadata/okx_perp_calendar.json` / `load_perp_calendar()` | 当前 OKX + 已核验退市样本 | 免费 | ⚠️ 见说明② | 进回测；动态池主键为 `asset_id`，优先用日历上市/退市日 |
| **历史市值快照** | 动态池可选排名口径、BTC 主导率（regime） | CoinGecko | REST `/coins/markets` | 2013+ | 免费档有限速 | ✅ | 备用：本项目动态池以 OKX 成交额为主口径 |
| **CVD / Trade Delta** | 订单流，**仅二次确认**（非主力） | OKX trades | `fetch_trades` | 理论可回溯但量极大、成本高 | 高（逐笔，TB 级） | ⚠️ 降级 | 暂不进历史回测；如需，仅"从现在起录制"，明确标注确认项 |
| **L2 盘口（OBI / WMP / Depth）** | 微观结构 | OKX books | `fetch_order_book`（仅当前快照） | ❌ **无历史** | — | ❌ | **删除**。仅"从现在起录制，供未来 paper trading"，永不进历史回测 |

## 说明

**① 美元成交额近似**
`ccxt.fetch_ohlcv` 仅返回 `[ts, open, high, low, close, volume]`，其中 `volume` 是**基础资产**成交量。本项目动态池排名（决策 1 的"过去 30 天平均成交额"）用 `dollar_volume ≈ close × volume` 作为美元成交额代理。OKX 原始 candle 含 `volCcyQuote`（计价币成交额）字段，未来若需精确值可改走原始 `publicGetMarketHistoryCandles` 取该字段。当前近似对"按成交额排 Top N"的排序结论影响很小，记录在案。

**② 幸存者偏差与退市合约（决策 1 铁律）**
`load_markets()` 只返回**当前仍在交易**的永续，直接用它回测过去 = 幸存者偏差（LUNA、FTT 等已退市币被剔除，收益系统性高估）。
应对：
- 已提交小型永续日历 `metadata/okx_perp_calendar.json`，包含当前 OKX USDT 永续和核验过的退市 fallback 样本（旧 LUNA/FTT/SRM/ANC）。`data/universe.py` 优先使用日历上市/退市日，退市日只用于截断退市后的月份，不作为退市前的排序信号。
- 日历主键是唯一 `asset_id`（如 `LUNA-20190726` 与 `LUNA-20220528`），不是裸交易所 symbol。LUNA 符号已确认复用：OKX 当前 `LUNA-USDT-SWAP` 最早数据为 2022-05-28，属于新 LUNA；旧 LUNA 的 Binance `LUNAUSDT` 2022-05-01 归档存在，但 2022-06-01 返回 404。两代资产严禁拼接成一条价格序列。
- 探测结果显示，OKX 对旧 LUNA/FTT/SRM/ANC 等已下架合约的历史 K 线和 funding REST 请求返回业务错误或不覆盖旧代际；因此这些样本的 OHLCV 走 Binance Vision USDT-M daily/monthly klines 归档 fallback。
- **Funding 口径更新（2026-06-13 用户确认）**：OKX REST funding 回溯深度约 3 个月，不足以覆盖 2020+ 回测。为避免 OKX/Binance funding 拼接造成额外错位，回测期所有进池 asset_id 的 funding 统一采用 Binance Vision monthly `fundingRate` 归档；缺失 archive 月份视为 coverage gap，不是下载失败。研究中缺失 funding 按 0 处理：若真实 funding 为正，低估多头付费/空头收款；若真实 funding 为负，低估多头收款/空头付费。该有界偏差必须在 validation/report 中披露，并从 Phase 6 起用 OKX live funding 记录验证实时一致性。
- 早期年份仍不能声称“完整无偏”：当前日历是代表性退市样本，不是完整退市合约全集。`data.validate.check_required_historical_members` 用于阻止 2021/2022 样本完全没有现已退市代表 `asset_id` 的情况。

## 进入阶段 1 的结论

允许进入因子工程的历史可得特征：
- ✅ 截面动量（OHLCV close）
- ✅ 波动率排名（OHLCV 已实现波动率）
- ✅ 成交量变化（OHLCV volume）
- ✅ funding carry（Binance Vision monthly fundingRate archive；archive gaps 按 0 并披露偏差）

降级 / 删除：
- ⚠️ CVD/Trade Delta —— 仅未来录制，标注为确认项，不进历史回测
- ❌ L2 盘口全系列 —— 删除，仅未来 paper trading 录制

待补强（不阻塞阶段 1，但阻塞早期年份完整无偏回测）：
- 扩展退市日历到完整历史合约全集；当前旧 LUNA/FTT/SRM/ANC 只是代表性 fallback 样本。
