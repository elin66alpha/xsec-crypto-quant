# 阶段 3 真实数据 Regime 分析驱动 — 实现规格（codex 任务书）

> 主脑（Claude）审核，codex 一次性实现。本文件是唯一事实源；任何与 CLAUDE.md
> 第一部分决策清单或风控铁律冲突的实现都视为 bug，须停下来问。

## 背景与现状

阶段 3 的**模块核心代码已存在且经测试**，无需重写：

- `regime/market_regime.py` —— 纯逻辑特征层（`equal_weight_pool_return`、`realized_vol`、
  `cross_sectional_corr_median`、`compute_market_features`）+ 懒加载 HMM 层
  （`fit_hmm`、`label_regimes_expanding`=诚实默认/无前视、`label_regimes_full`=⚠️含前视仅研究）。
  三状态语义：`REGIME_LOW_VOL=0` / `REGIME_TREND=1` / `REGIME_CRISIS=2`（按波动率升序重排）。
- `regime/correlation_spike.py` —— `SpikeConfig`（abs_threshold=0.7、baseline_window=60、
  z_threshold=2.0、min_baseline=20）、`correlation_spike_signal`、`detect_correlation_spike`。
- `regime/risk_params.py` —— `effective_gross_multiplier(regime, corr_spike)`（两道防线取更保守者）、
  `apply_regime_leverage`、红线 `MAX_SINGLE_SIDE=1.0`/`MAX_GROSS=2.0`（写死，不可放开）。

**缺口（你要做的）**：把这些模块接到全量真实数据上的**驱动脚本 + 报告 + 可视化**，
类比已存在的 `scripts/run_factor_analysis.py`。

## 交付物

### 1. `scripts/run_regime_analysis.py`（主交付）

参照 `scripts/run_factor_analysis.py` 的数据加载范式（动态池掩码、UTC 日频、warmup 余量）。
**允许复制**该脚本里的最小加载器（`_utc`、`load_universe_history`、`pool_asset_ids`、
`daily_pool_mask`、`apply_mask`，以及只读 close 的 `load_price_volume_panels` 的 close 部分）。
轻度重复可接受——**不要重构 `run_factor_analysis.py`**（它已提交并验证，勿动）。

流程：

1. 默认区间 **in-sample 2020-01-01 ~ 2024-06-30**（`backtest.walkforward.SplitConfig`），
   `--start/--end` 可覆盖。**绝不触碰 holdout（2025-10-01 起）**——`--end` 上限钳到
   validation 末（2025-09-30），若用户传更晚的 end 直接报错退出。
2. 加载**动态池掩码后的 close 宽面板**（列=asset_id）。市场层面特征用"当月真实在池"
   资产的横截面（杜绝幸存者偏差）。注意 `cross_sectional_corr_median` 对列数敏感，
   掩码后某些早期月份池子小是正常的，不要 dropna 掉整列。
3. `compute_market_features(close)` → market_vol / xs_corr_median / mean_abs_return。
4. **两套标注都跑**：
   - `label_regimes_expanding(features)` —— 诚实默认（无前视），是任何下游用途的唯一合法来源。
   - `label_regimes_full(features)` —— ⚠️ 含前视，**仅供研究可视化**，报告里必须显著标注警示。
5. `detect_correlation_spike(close)` → xs_corr_median + corr_spike 布尔信号。
6. 逐日 `effective_gross_multiplier(regime_expanding[t], corr_spike[t])` → 杠杆乘子时间线。

### 2. `reports/regime_timeline_is.png`（多面板，英文标签）

> CJK 在 matplotlib 渲染成方块（已知问题）——**所有图内文字用英文**。

三面板共享时间轴：
- **Panel 1**：等权池累计净值（`equal_weight_pool_return` 累乘）或 BTC 收盘价做参照，
  **用 expanding regime 给背景上色**（低波/趋势/危机三色半透明背景带）。
- **Panel 2**：market_vol 与 xs_corr_median 两条线，corr_spike=True 的日子打标记/竖线。
- **Panel 3**：杠杆乘子阶梯线（1.0 正常、0.2 危机），直观看降杠杆何时触发。

### 3. `reports/regime_summary_is.md`（体检表）

- 三状态占比 %（expanding 与 full 各一列对照）。
- 状态切换次数与频率（年化切换次数）。
- 每个状态下三个特征的均值（用于"危机态确实高波动高相关"的可解释性核对）。
- corr_spike 触发天数、处于降杠杆（乘子<1）的天数占比。
- 危机态（expanding）的日期区间列表 —— **纯描述性**。

## 诚实研究护栏（违反即 bug）

- **n_states=3 写死**（CLAUDE.md 阶段 3），`SpikeConfig` 用默认值。**严禁为"对上"已知崩盘
  （2022-05 LUNA、2022-11 FTT）去调 n_states 或阈值**——事后贴标签不是预测力，那是过拟合。
  若你看了真实分布后认为默认阈值明显不合理，**不要自行改**，在 inbox 里报告分布证据，等主脑定夺。
- **验收标准（按 CLAUDE.md 阶段 3，已删除"必须识别出某次崩盘"那条）**：HMM 状态对照总市值
  走势**可解释**、状态切换频率**合理**（不是每隔几天乱跳）。危机区间与历史事件的吻合度**只作
  描述性观察写进报告**，不作为通过/不通过门槛，更不可反向调参去凑。
- expanding=唯一可用于任何信号的来源；full 仅可视化且必须带 ⚠️ 警示。
- **不写 `docs/trials_ledger.md`**：regime 拟合不是因子试验，不消耗阶段 5 Deflated Sharpe 的试错预算。
- 不碰 holdout。无前视由模块保证，驱动层不得引入跨期泄漏。

## 测试

- 模块核心已有 `tests/test_market_regime.py`，无需重复。
- 若驱动里有**新的纯逻辑**（如把 expanding 标签+corr_spike 合成逐日乘子时间线的函数），
  抽成可单测的纯函数并加 1~2 个用例（参照 `tests/test_quantile_backtest.py` 的精简风格）。
- 跑通：`~/miniconda3/envs/xsec-crypto-quant/bin/python scripts/run_regime_analysis.py`
  产出两份报告，且 `pytest` 全绿。

## 环境与协作

- Python：`~/miniconda3/envs/xsec-crypto-quant/bin/python`（已装 hmmlearn 0.3.3、matplotlib）。
- 工作目录：主仓 `/home/pc/code/xsec-crypto-quant`（**不用 worktree**——`data/historical/` 是
  gitignore 的大文件，新 worktree 拿不到数据，你需要真数据才能产出报告）。
- **不要自己 git commit**：实现完、跑通驱动与 pytest 后，把"改了哪些文件、驱动输出的关键数字
  （三状态占比、切换次数、危机区间、降杠杆天数占比）、pytest 结果"发到 inbox 给 claude-lead，
  由主脑 review 后统一提交。遇到与本规格或决策清单的冲突/歧义，停下来在 inbox 提问。
