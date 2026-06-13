# 实现规格：全量历史数据下载驱动 `scripts/download_history.py`

> 阶段 5 真实数据运行的第 1 步。目标：把 2020-01 至今、动态池涉及的全部永续合约
> 1D OHLCV + funding 拉到本地 parquet，构建逐月动态池，并通过验证闸门。
> 本规格由 claude-lead 起草，codex worker 实现，claude-lead 审核验收。

## 背景与已有设施（必须复用，不要重写）

- `metadata/okx_perp_calendar.json`：804 个合约（含 449 个退市代际）的上市/退市日历，
  含 `asset_id`（资产代际主键，如 `LUNA-20190716`）、`data_source`（`okx` / `binance_vision`）、
  `binance_symbol`。读取用 `data.universe.load_perp_calendar()`。
- `data/fetcher.py`：`fetch_ohlcv(symbol, source=..., binance_symbol=...)` 已支持 OKX 与
  Binance Vision daily/monthly 归档两条通道；`fetch_binance_vision_funding` 支持 Binance
  Vision monthly `fundingRate` 归档；`fetch_funding_rate_history` 仅保留给 OKX 近实时/Phase 6
  记录用途。
- `data/storage.py`：按月分片 parquet，写入幂等（重复写去重合并），`available_range()` 支持断点续传。
- `data/universe.py`：`build_universe_history(panel, start, end, calendar=...)` 逐月选池。
- `data/validate.py`：`validate_ohlcv`、`check_delisted_symbol_coverage`、`check_universe_consistency`。
- 口径决定（2026-06-13 用户确认）：OHLCV 仍按日历在 OKX 与 Binance Vision 间路由；
  回测期 funding 对**所有进池 asset_id**统一使用 Binance Vision USDT-M monthly
  `fundingRate` 归档。原因：OKX REST funding 深度约 3 个月，不足以覆盖 2020+ 回测；
  选择全 Binance funding 归档而不是 OKX/Binance 拼接，以保持 backtest funding 单一来源。
  缺失 archive 月份按 coverage gap 披露，研究中按 0 处理，并在 Phase 6 用 OKX live funding
  记录做一致性验证。

## 交付物

### 1. fetcher 扩展：Binance Vision 月度归档

`fetch_binance_vision_ohlcv` 当前逐日下载 zip——449 个退市合约 × 数年逐日文件请求量不可接受。

- 增加月度归档支持：`https://data.binance.vision/data/futures/um/monthly/klines/{SYM}/1d/{SYM}-1d-{YYYY-MM}.zip`。
- 策略：请求区间内的**完整自然月**走月度 zip；首尾不完整月及月度 404 的月份回退逐日 zip。
- 保持函数签名兼容（新增参数给默认值即可），返回格式不变。
- 对 429/5xx 加重试退避（requests Session + 重试适配器或手写 backoff），404 视为"该日/月无数据"。

### 2. 下载驱动 `scripts/download_history.py`

CLI（argparse）：
```
--start / --end       目标回测区间，默认 2020-01-01 ~ 今天
--data-dir            默认 data/historical
--trial               试跑模式（见下）
--symbols             逗号分隔 asset_id 子集（调试用）
--skip-funding        只拉 OHLCV
```

流水线（每阶段打印进度与汇总）：

- **Stage A — OHLCV**：从日历筛出生命周期与 `[start − buffer, end]` 相交的合约
  （buffer = lookback 30 + min_listing 90 + 5 天，保证 start 当月就能选池）。
  每个合约拉 `[max(listing, start−buffer), min(delisting, end)]` 的 1D OHLCV，
  按 `data_source` 走对应通道，存 parquet。
  - **分片必须以 `asset_id` 为键**（`save_ohlcv(df, symbol=asset_id, ...)`），不能用裸
    symbol——新旧代际（如新旧 LUNA）会撞文件名。
  - 断点续传：先 `available_range(asset_id)`，已覆盖区间不重拉；单合约失败记入失败清单，
    不中断整体；结束时把失败清单写 `reports/download_failures.json`，失败数 > 0 时退出码非 0。
- **Stage B — 成交额面板**：**从本地 parquet** 构建 dollar_volume 宽表
  （close × volume，列 = asset_id），不要二次网络请求。实现为可复用函数
  （建议放 `data/storage.py` 或脚本内：`build_dollar_volume_panel_from_storage(asset_ids, data_dir)`）。
- **Stage C — 动态池**：`build_universe_history(panel, start, end, calendar=...)`，
  结果写 `data/historical/universe_history.json`（{月初: [asset_id]}），并打印每月池大小
  及退市成员数。
- **Stage D — funding**：对历史上**进过池**的每个 asset_id 拉 Binance Vision monthly
  `fundingRate` 归档，区间为 `[max(listing, start−buffer), min(delisting, end)]`（结束日覆盖
  到当天 23:59:59.999）。请求 symbol 优先使用日历的 `binance_symbol`，否则用
  `to_binance_usdt_symbol(symbol)` 映射；存储仍必须用 `asset_id`：
  `save_funding(df, symbol=asset_id, ...)`。
  - Binance funding URL：
    `https://data.binance.vision/data/futures/um/monthly/fundingRate/{SYM}/{SYM}-fundingRate-{YYYY-MM}.zip`。
  - 只有 monthly 归档，没有 daily fallback。单月 404 是 coverage gap，不是下载失败；合约所有
    月份都缺失时 log 为 archive gap，validation 以 warning 披露，失败清单不计 failure。
- **Stage E — 验证**：对全部已下载 asset_id 跑 `validate_ohlcv`，并用日历 expected start/end
  做边界覆盖闸门（实际边界短缺 >3 天为 error）；对池历史跑
  `check_universe_consistency` 与 `check_delisted_symbol_coverage`；
  汇总写 `reports/download_validation.md`（区分 error/warning，给出每合约 expected/actual
  边界与 gap 统计）。报告必须包含 funding coverage section：每个进池 asset_id 的 in-pool
  days、funding-covered days、coverage fraction、funding rows、first/last funding，以及 aggregate。

### 3. 试跑模式 `--trial`（用户已确认先试跑再放全量）

固定窗口：`--start 2020-12-01 --end 2021-01-31`（buffer 自动外推到 2020-08 附近），
只处理生命周期与该窗口相交的合约。额外做两件事：

1. **退市覆盖抽查**：打印 2021-01 月初池成分，标注其中现已退市的 asset_id 数
   （预期 ≥ 5；为 0 说明幸存者偏差仍在，视为试跑失败）。
2. **跨源对比报告**（量化混合口径的有界偏差）：对 BTC、ETH、LTC、XRP、DOGE 五个
   两边都有的标的，同区间分别从 OKX 与 Binance Vision 拉 1D，按日对齐后报告：
   日收益差的均值/最大值（bp）、收益相关系数、bar 边界是否对齐。
   写 `reports/trial_source_comparison.md`。
   - **特别注意 OKX 日线的时区边界**：OKX 部分 K 线接口默认 UTC+8 切日，Binance 是 UTC。
     若对比发现系统性错位（相关系数明显 < 0.99），检查 ccxt okx 的 1d 是否映射到 UTC 口径
     （必要时显式用 1Dutc），修正后重跑。这是试跑最重要的检查项之一。

### 4. 必须实测验证的风险点（试跑中确认，结果写进对比报告）

- OKX REST 能否翻页拉到 2020 年的 BTC-USDT-SWAP 日线（普通 candles 端点深度有限，
  可能需要 ccxt 走 history-candles；试跑必须实际拿到 2020-08 起的数据才算通过）。
- Binance Vision funding 归档深度：试跑 diagnostics 必须显示 BTC/ETH funding 回到 2020-08
  且接近 8h cadence；LUNA 在 2021 覆盖；FTT pre-2022-04 gap 在 coverage/probe 统计中可见。

### 5. 测试

`tests/test_fetcher.py` / `tests/test_download_history.py`：
- 月度 zip 解析（构造内存 zip，mock HTTP）；月度 404 回退逐日的路径。
- funding monthly zip 解析（构造内存 zip，mock HTTP），monthly 404 作为 coverage gap 跳过。
- 驱动的断点续传逻辑：mock fetcher，已有分片时不重复请求已覆盖区间。
- Stage D funding：所有 pool asset_id 都用 Binance funding 归档，存储键为 `asset_id`，空 archive
  是 skip/gap 而不是 failure。
- Stage B 面板构建：写临时 parquet，验证宽表形状与 close×volume 数值。
- 现有 `pytest -q` 全部保持通过。

## 验收标准

1. `pytest -q` 全绿（含新增测试）。
2. `python scripts/download_history.py --trial` 在真实网络下跑通：
   - BTC 数据回溯到 2020-08；
   - 2021-01 池成分含 ≥ 5 个现已退市 asset_id；
   - 跨源对比相关系数 ≥ 0.99（或文档化修正后达到）；
   - BTC/ETH funding diagnostics 回到 2020-08，LUNA/FTT archive probes 写入报告；
   - `reports/download_validation.md` 与 `reports/trial_source_comparison.md` 生成。
3. 全程不改动统计/回测逻辑模块；不提交任何 parquet/数据文件（.gitignore 已覆盖 data/）。
4. Conventional Commits，提交信息说明做了什么 + 为什么。

## 工作方式

- 在自己的 worktree 分支上实现，逐功能提交。
- 完成后 `clawteam task update` 置 completed，并 `clawteam inbox send xsec-fixes claude-lead <摘要>`，
  附：分支名、跑试跑的实际输出要点（池大小、退市数、对比统计）、遗留问题。
- 遇到规格与实测冲突（如 OKX 深度不足）：先按规格内预案处理；预案外的卡点发 inbox 问 claude-lead，不要自行改口径。
