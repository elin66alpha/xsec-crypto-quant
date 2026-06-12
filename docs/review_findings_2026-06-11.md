# 全项目严谨性审查记录（2026-06-11）

> 来源：对阶段 0–5 已合并代码的整体审查。每条记录：问题、位置、为什么重要、修复方案、状态。
> 修复纪律：每修一条，更新本文件的状态栏并单独提交 git；涉及统计方法的修改需在提交信息里写明理由（决策 12 复盘依赖）。

状态图例：`[ ]` 待处理 ｜ `[~]` 处理中 ｜ `[x]` 已修复（附 commit）｜ `[-]` 决定不修（附原因）

---

## P0 —— 动摇统计结论，上真实数据前必须修

### 1. `[x]` 重叠前向收益导致 IC 的 t 统计量被系统性高估（commit eb10617，codex-w1 实现 / claude-lead 审核；HAC/裸 t 比值：模拟 10D 重叠序列 0.38、白噪声 0.94）

- **位置**：`factors/ic_analysis.py`（`ic_summary` 的 `t_stat = ICIR × sqrt(N)`）→ 下游 `factors/multiple_testing.py`（FDR 建在这些 p 值上）。
- **问题**：horizon = 3D/5D/10D 时前向收益逐日滚动计算，相邻观测共享绝大部分数据（10D 下相邻两天共享 9 天），IC 序列高度自相关，有效样本量远小于 N。`t = ICIR×√N` 假设 IC 独立同分布，t 值在 10D 下可能虚高 2–3 倍。
- **为什么严重**：BH-FDR 只在输入 p 值本身诚实时才控制得住错误发现率。p 值结构性虚高 → 校正后仍会放进假因子 → 阶段 2 的全部筛选结论无效。
- **修复方案**（三选一，倾向①）：
  1. 对 IC 均值用 Newey-West（HAC）标准误，lag ≈ horizon（statsmodels 可直接做）；
  2. 每 horizon 天取一个不重叠的 IC 观测；
  3. 保守近似：把 N 除以 horizon 再算 t。
- **验收**：单测构造一个已知自相关的 IC 序列，确认修正后 t 值显著低于裸 `ICIR×√N`；horizon=1 时与原结果一致。

### 2. 幸存者偏差在实现上仍未解决（退市币进不了池）

- **位置**：`data/universe.py`（`list_perpetual_symbols` 只返回现存合约；`infer_listing_dates` 用首条数据日做上市日代理）。代码注释"说明②"已诚实标注，但未解决。
- **问题**：LUNA、FTT 等退市币根本不在候选集里。2021–2022 回测池缺了恰恰最该被做空的那批币，多空两腿的损益都被系统性扭曲，方向不可知。这直接违反决策 1 铁律。
- **机会**：历史数据源已改用 data.binance.vision（美国 IP 访问 Binance API 被 451 封锁），其归档**包含已退市合约的历史文件**——可以从归档文件清单反推完整的"当时存在过的合约全集 + 上市/退市日期"。
- **修复方案**：
  1. 写一个脚本枚举 data.binance.vision 上 USDT 永续的全部历史 symbol（含退市），生成上市/退市日历，存为 `data/` 下的元数据文件；
  2. `universe.py` 接受外部 listing/delisting 日历（接口已有 `listing_dates` 参数，需补退市日截断）；
  3. `data/validate.py` 的池成分一致性检查加一条：抽查 2021/2022 的池子必须含现已退市的币。
- **附带决策（已定，2026-06-11 用户确认）**：**数据口径统一 OKX**——价格、funding、费率、执行全部以 OKX 为准，消除 Binance/OKX 错位。
- **由此产生的实现风险（实现 #2 时必须先验证）**：OKX REST 的 instruments 接口只列现存合约，**已退市合约的 K 线/funding 能否通过 history-candles 等接口用旧 instId 拉到，需要实测**。验证结果分两种处理：
  1. 能拉到 → 用 OKX 官方下架公告/第三方数据整理"退市日历 + instId 清单"，补全历史池；
  2. 拉不到 → 在审计表记录一条**有界偏差**：退市币的价格序列以 Binance 归档（data.binance.vision）替代、其余全部 OKX，并在回测报告中披露该混合口径只影响已退市标的。

### 3. `[x]` 退市/数据中断的损失被静默吞掉（commit 7bbfc36：缺失收益按最后有效价标记一根 bar 后强平、计成本，新增 missing_return_exposure 序列 + RuntimeWarning）

- **位置**：`backtest/xsec_runner.py`（`gross_return = (execution * asset_returns.fillna(0.0)).sum(axis=1)`）。
- **问题**：持仓标的价格数据中断（退市、停牌）时按 0 收益记账，随后"免费"平仓。等于假设总能在崩盘前以原价逃出。决策 10 要求"按最后可成交价标记 + 下个 rebalance 强平"，未实现。
- **修复方案**：
  1. 持仓权重非零但收益为 NaN 时：记 warning、计数、写进 `BacktestResult`（如 `missing_return_exposure` 序列），不再静默填零；
  2. 实现决策 10：标的离池/数据中断 → 下一 bar 强平，期间按最后有效价格的收益（而非 0）标记；
  3. 报告里强制展示"缺失收益敞口"统计，超过阈值的回测结果视为不可信。

### 4. `[x]` IID bootstrap 给出过窄的置信区间（commit a8152ee：bootstrap_ci 默认 circular block bootstrap，block_length=20 为显式记录参数；IID 版保留为 iid_bootstrap_ci 仅作对照）

- **位置**：`backtest/metrics.py`（`bootstrap_ci`，注释"保守不确定性显示"）。
- **问题**：日收益有波动率聚集与自相关，IID 重采样打散了这种结构，**低估** Sharpe 的方差、CI 过窄 → 偏向"假装更确定"。决策 12 的判定（CI 是否含 0）直接依赖这个区间。
- **修复方案**：改用 stationary/circular block bootstrap（块长 10–20 天，作为显式参数记录）；`overfitting_check.monte_carlo_bootstrap` 同步切换；保留 IID 版本仅作对照展示。
- **验收**：单测用 AR(1) 正收益序列验证 block CI 显著宽于 IID CI。

---

## P1 —— 影响结论质量或换手成本，阶段 5 真跑之前修

### 5. `[x]` 无操作带对分位组合基本无效，应换成排名缓冲带（commit 41ccfb3：build_target_weights 增加 exit_quantile；入选 20%/退出 30% 实测日均换手降 31%。exit_quantile 取值进入阶段 5 参数台账）

- **位置**：`strategy/rebalance.py`（band=0.05 作用于权重变化）＋ `strategy/portfolio.py`（权重只有 ±1/n_side 或 0 两种取值）。
- **问题**：quantile=0.2、池 30 只 → 每边 6 只，标的进出档位时 |Δw| = 1/6 ≈ 0.167 ≫ 0.05，band 拦不住；不进不出时 Δw=0，band 没事干。它只在每边只数变化引起的微调（1/6→1/7 ≈ 0.024）时生效。决策 4b 想抑制的"分位边界来回横跳"完全没被抑制。
- **修复方案**：在 `portfolio.py` 增加排名缓冲带——新标的需排进前 q（如 20%）才入选；已持有标的要跌出 q_exit（如 30–35%）才剔除。q_exit 是可调参数，进入阶段 5 的参数台账。权重层的 no-trade band 可保留处理微调。
- **验收**：在合成数据上对比缓冲带前后的平均换手与净 Sharpe；单测覆盖"排名在边界震荡的标的不被反复进出"。

### 6. 三段切分日期已过时（今天是 2026-06）

- **位置**：`backtest/walkforward.py`（`SplitConfig` 默认 validation=2023、holdout 自 2024-01 起）。
- **问题**：holdout 已膨胀到 2.5 年，与 CLAUDE.md"最近 6–12 个月"矛盾；validation 只覆盖 2023 单一行情。
- **修复方案（已定，2026-06-11 用户确认遵循"最近 6–12 个月"）**：新三段切分：
  - In-sample：2020-01-01 ~ 2024-06-30（约 4.5 年，覆盖 2021 牛、2022 LUNA/FTX 危机、2023 修复、2024 上半年——四种行情都在因子筛选样本内）
  - Validation：2024-07-01 ~ 2025-09-30（15 个月，跨越不止一种行情，避免参数只为单一年份调优）
  - Locked holdout：2025-10-01 ~ 解锁日（今日约 8.4 个月，落在 6–12 个月区间；**holdout_start 固定不再移动**，解锁时以当日为 holdout_end 一次性冻结）
  - 落地：更新 `backtest/walkforward.py` 的 `SplitConfig` 默认值，提交信息引用本条。

### 7. `[x]` Walk-forward 只有"评估"没有"选参"管线（commit feat(backtest): add walk-forward parameter selection pipeline，codex-w2 实现）

- **位置**：`backtest/overfitting_check.py` ＋ `notebooks/demo_phase5_backtest.py`（固定参数在各 validation 窗口算 Sharpe）。
- **问题**：CLAUDE.md 承诺"动量窗口 N 等参数由 Walk-Forward 选"，需要每窗口 train→网格选参→该窗口 validation 评估的完整循环，目前没接起来。
- **修复方案**：在 `backtest/walkforward.py` 或新模块实现 `run_walk_forward(grid, panels, windows)`：逐窗口在 train 段选参、在 validation 段出样本外收益，拼接成真正的 walk-forward 净值曲线；所有试过的组合数自动累计进 DSR 的 n_trials。

### 8. `[x]` DSR 的 n_trials 只数了阶段 5 网格，缺试错台账（commit feat(backtest): add trials ledger feeding DSR n_trials，codex-w2 实现）

- **位置**：`backtest/overfitting_check.py`（`deflated_sharpe_report` 用参数网格大小做 n_trials）。
- **问题**：阶段 2 试过的全部"因子×窗口×horizon"组合也是试错，不计入则 DSR 惩罚不足。
- **修复方案**：建 `docs/trials_ledger.md`（或 json），从阶段 2 起累计记录每一批试验的组合数与日期；`deflated_sharpe_report` 的 n_trials 从台账读取总数。git 历史佐证台账完整性。

### 9. 滑点假设对全池一刀切

- **位置**：`backtest/xsec_runner.py`（`BacktestConfig.slippage = 0.0005` 统一 5bp）。
- **问题**：池子第 25–30 名的小币滑点远高于 BTC/ETH；多空腿恰好系统性持有流动性较差的标的。
- **修复方案**（分两步）：先在验收标准里加"成本 ×2 / ×3 压力测试"（便宜且立刻可做）；后续可按 ADV 或波动率分档设滑点。

---

## P2 —— 改进项，不阻塞主线

### 10. `[x]` Regime 分块 Viterbi 块首缺历史条件（commit 1099a76：predict 输入截至块尾的全部历史、只取当前块标签，仍无前视）

- **位置**：`regime/market_regime.py`（`label_regimes_expanding` 对每个 21 天块孤立跑 `model.predict`）。
- **修复方案**：改为 `model.predict(X.iloc[:end])` 后只取最后一块标签——仍无前视，条件更充分，几乎零成本。

### 11. 无操作带假设"不调仓 = 权重不变"，忽略持仓漂移

- **位置**：`strategy/rebalance.py`（`apply_no_trade_band` 的 `held` 状态）。
- **说明**：实际上价格变动会让权重漂移；日频下是二阶小量。先记录在案，待缓冲带（问题 5）落地后评估是否还需要建模漂移。

### 12. 美元中性的实际 beta 需分 regime 监测

- **位置**：`backtest/metrics.py`（`realized_beta` 已有全样本版本）。
- **说明**：横截面动量在加密里常系统性做空高 beta 山寨 → 持续负 beta 倾向，且危机时最伤。建议报告里按 regime 分段报 beta，而不只报全样本一个数。决策 7 本来就预留了"实测 beta 显著偏 0 再切 beta 中性"。

---

## 已确认的优点（保持，不要在修复时破坏）

- `to_execution` shift-1 + open-to-open 收益的时点对齐干净无前视（决策 9 落地）。
- funding 日度聚合 `min_count=1` 区分"无数据"与"零费率"。
- 年化用 365（加密无休市）。
- `split_three_way` 默认不返回 holdout 的代码护栏 + `HoldoutLockedError`。
- `label_regimes_full` 反复标注"含前视、仅研究可视化"。
- FDR 校正前后通过数对比报告。

---

## 建议处理顺序

1. **#1 IC 重叠样本 t 检验**（不修则阶段 2 无效）
2. **#2 退市币数据补齐**（不修则阶段 5 无效；依赖 data.binance.vision 归档枚举）
3. **#4 block bootstrap**（决策 12 判定标准依赖）
4. **#3 退市损失记账**（与 #2 同主题，可一起做）
5. **#5 排名缓冲带**（收益/成本比最高的优化）
6. #6 切分日期（需用户确认口径）→ #7 walk-forward 选参管线 → #8 trials 台账 → #9 成本压力测试
7. P2 各项穿插处理
