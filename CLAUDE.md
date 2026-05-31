# 横截面加密多空量化研究项目 — 立项决策 + Claude Code 项目指令文件

> 本文件分两部分：
> **第一部分**是《阶段 −1 立项决策清单》，固化了项目所有根决策与取舍，是不可在开发中途随意推翻的地基。
> **第二部分**是 Claude Code 的实际项目指令（原 CLAUDE.md 的改写版），所有阶段任务都与第一部分的决策保持一致。
>
> 阅读顺序：先读第一部分理解"为什么这么定"，再按第二部分执行。任何与第一部分决策冲突的实现都视为 bug。

---

# 第一部分：阶段 −1 立项决策清单

本清单在写任何一行代码之前完成，目的是让整个项目有一个**内部自洽、不可中途漂移**的地基。每条决策都记录了选项、取舍和最终选择。

## 项目定位（最高优先级，统领一切）

本项目是一个**统计上诚实的横截面加密多空量化研究项目**。

核心信条（决策 12 的灵魂，反复回看）：

> **诚实研究的意思不是放弃盈利动机，而是：不让"我想盈利"这个动机污染"它到底能不能盈利"这个判断。**

盈利是**目的**。统计诚实是**手段**——它是确保"如果我说找到了 edge，那它大概率是真的"的那道防线，从而保护未来投入的真金白银。在量化里，唯一比"没找到 edge"更糟的，是"以为找到了 edge 但其实没有，然后拿钱去交易它"。前者只是没赚到，后者会亏钱。

## 根决策

### 决策 A：资产维度 —— 横截面（已定）
- **选项**：单资产时序择时 vs. 横截面资产池。
- **取舍**：单资产样本量天花板极低、IC 工具不适用、edge 真伪难证；横截面让 IC/alphalens 名正言顺、样本量提升一个数量级、alpha 来源（相对动量/carry）不依赖微观结构。
- **最终选择**：**横截面**。

### 决策 B：交易周期 —— 小时/天级别，日频再平衡（已定）
- **取舍**：微观结构 alpha 衰减以秒分钟计，与小时/天持仓错配，且历史 L2 数据难获取；慢变量（截面动量、carry、波动率）在日级别有横截面区分度，且数据全部走 REST 可得。
- **最终选择**：**小时/天级别，再平衡频率为日频**（见决策 4）。

## 具体决策

### 决策 1：资产池构成与幸存者偏差 —— 动态池（已定）
- **风险**：用"今天的 Top N"回测历史 = 幸存者偏差，归零/退市币（LUNA、FTT 等）被剔除，收益系统性高估。
- **最终选择**：**动态池**。规则：每月初按过去 30 天平均成交额选 Top 30 永续合约，要求该合约上市满 90 天。接受早期年份池子偏小。需要逐月历史市值/成交量快照（CoinGecko / CoinMarketCap）+ 上市/退市日历。
- **铁律**：每个历史时点只能使用"当时真实可交易"的标的集合，严禁用未来的池子成分回测过去。

### 决策 2：目标变量 —— 相对收益 / 多空中性（已定）
- **取舍**：纯多头简单但收益混入大量市场 beta，分不清 alpha 与踩对牛市；多空中性剥离市场 beta，剩下的才是因子真 alpha，研究上更干净，但需能做空。
- **最终选择**：**多空中性**。预测的是"在池子里哪些跑赢截面均值"，做多打分最高组、做空最低组。

### 决策 3：交易标的 —— 永续合约（已定，被决策 2 绑定）
- **最终选择**：**永续合约**（现货做空成本高、难执行）。
- **附带要求**：funding rate 必须进数据层并在回测损益里逐期结算；funding 同时作为**一等公民 carry 因子**使用（资金费率极高的币往往拥挤多头、有均值回归倾向）。

### 决策 4：再平衡频率 —— 日频（已定）
- **风险**：横截面策略换手率天然高，手续费 + 滑点是"回测漂亮、扣成本归零"的头号死法。
- **最终选择**：**日频再平衡**。成本假设要狠（OKX taker 费率 + 真实滑点 + funding），宁可低估收益。
- **配套（决策 4b）**：加**无操作带（no-trade band）**——仅当新信号与当前持仓差异超过阈值才调仓，抑制无谓换手。阈值是可调参数，需记录调整原因。

### 决策 5：因子横截面预处理 —— 截面 rank 起步（已定，先最简单）
- **最终选择**：每个时点对池内所有标的做**截面 rank**（rank 天然抗极值）。
- **暂缓**：z-score、winsorize 去极值、对市值/波动率的中性化回归——记录在案，将来需要时再补（见决策 11）。

### 决策 6：组合配权 —— 分层等权起步（已定）
- **取舍**：等权更稳健、更难过拟合、更透明；因子值加权更激进、更易过拟合。
- **最终选择**：**分层等权**。进多头组的币各等权做多，进空头组的币各等权做空。

### 决策 7：两腿平衡 —— 美元中性起步（已定）
- **取舍**：美元中性简单（两腿名义金额相等）；beta 中性更严格但需估 beta、引入估计误差。加密里多数币对 BTC 的 beta 接近 1，美元中性通常已接近 beta 中性。
- **最终选择**：**美元中性**。评估阶段实测组合对 BTC 的实际 beta，若显著偏离 0 再考虑切换 beta 中性。

### 决策 8：杠杆上限 —— 风控红线（已定，写死）
- **最终选择**：**单边 ≤ 1x，合计名义敞口 ≤ 2x**（多头 1x + 空头 1x）。
- **性质**：这不是优化项，是**风控铁律**，回测与 paper trading 全程不可放开。严禁因"回测里加杠杆更好看"而上调。

### 决策 9：信号时点与前视偏差（记录，实现时落实）
- **规则**：用**收盘后**确定的因子值，在**下一根 bar 开盘**成交。严禁用当根收盘价算信号又用当根收盘价成交。

### 决策 10：停牌 / 流动性枯竭处理（记录，实现时定规则）
- **场景**：日频 + 动态池下，某币突然下架或极端行情无法交易。
- **初步规则**：在下一个 rebalance 强制平掉该腿，期间按最后可成交价标记。细节实现时确认。

### 决策 11：去极值（记录）
- 截面 rank 已天然处理极值，**暂不单独 winsorize**。将来若改用 z-score，再补 winsorize。

### 决策 12：项目成功标准与合法终点（已定，灵魂条款）

项目目标是找到一个**统计上稳健、扣除全部成本后仍盈利**的横截面多空策略。"稳健"由 FDR 校正、Deflated Sharpe、锁死 holdout 共同定义。

- **若验证通过**（因子经 FDR 校正后显著，多空组合扣除成本与 funding 后 Sharpe 的 bootstrap 置信区间显著为正，且锁死 holdout 上未崩）→ 进入 paper trading。**这是项目的成功主线，是做这一切的目的。**
- **若所有候选因子经 FDR 校正后均不显著，或扣成本与 funding 后 Sharpe 的 bootstrap 置信区间包含 0** → **不调参硬凑**，记录否定结论并复盘方法论，作为该设定下的合法终点。这不是失败：你没赔钱，还得到一整套可复用的方法论，可换标的/换设定重来。

预先把"没找到"这条路也修好，是因为等真在 validation 上卡住、已投入数月心血时，人没法冷静决定该收手还是再试——那时每一次"再调一下参数"都感觉合情合理，而那恰恰是过拟合发生的方式。

---

# 第二部分：Claude Code 项目指令（CLAUDE.md 正文）

## 项目定位与核心原则

构建一套**统计上诚实、以盈利为目的**的横截面加密货币多空量化研究系统。资产为动态选取的 Top N 永续合约篮子，策略为**横截面多空中性**（做多相对强势、做空相对弱势），再平衡频率为日频。

**绝对禁止的做法：**
- 不使用 LLM 做任何价格预测、信号生成或买卖决策——LLM 不参与任何交易逻辑
- 不用神经网络直接预测价格——神经网络仅可用于特征提取或状态分类，输出必须是可解释的中间量
- 不跳过因子有效性验证直接进入回测
- 不在全量数据上调参后直接报告回测结果
- **不做多重检验校正就声称因子显著**（详见阶段 2）
- **不用未来的池子成分回测过去**（幸存者偏差，详见决策 1）
- **不超过杠杆红线**（单边 ≤ 1x，合计 ≤ 2x）

**核心设计哲学：**
- 每一步都必须有**统计验证**，拒绝"看起来有效"的直觉
- 横截面相对强弱是 alpha 来源；订单流/微观结构最多是入场二次确认，不是主力（在日级别上其增量信息很可能很弱，需用数据证伪而非先验假定）
- Regime 感知是市场层面的，用于在全市场相关性飙升时整体降杠杆
- 代码必须**模块化、可复现、有单元测试**
- 全程用 git 管理（见下文 GitHub 工作约定）

## 风控铁律（不可违反）

1. **杠杆**：单边名义敞口 ≤ 账户净值 1x，多空合计名义 ≤ 2x。
2. **相关性危机**：加密资产相关性在危机时趋近 1，多空对冲会瞬间失效（如 2022-05 LUNA 崩盘，所有币齐跌，空头腿补不上多头腿的亏）。这是结构性特征，不是 bug。Regime 模块一旦检测到全市场横截面相关性中位数飙升，**整体降杠杆甚至空仓**，不得相信对冲仍在保护组合。
3. **成本**：回测必须扣除 taker 费率 + 真实滑点 + 逐期 funding，宁可低估收益。
4. **数据时点**：信号用收盘后因子值，下一根 bar 开盘成交，严禁 look-ahead（决策 9）。
5. **样本外**：锁死 holdout 全周期只解锁一次（详见阶段 5）。

---

## 技术栈与环境

### 操作系统
Linux 或 macOS。

### Python 环境
```
Python 3.12+
conda 管理虚拟环境
环境名称：xsec-crypto-quant
所有依赖必须装在此环境内，严禁向系统 Python 或 base 环境安装任何包
```

### 依赖库（按模块分类）

**数据层**
```
ccxt>=4.0.0          # OHLCV + funding rate + 合约信息
pandas>=2.0.0
numpy>=1.24.0
pyarrow
aiohttp
websockets
requests             # CoinGecko/CMC 历史市值快照
```

**特征 / 因子**
```
pandas-ta
numba
```

**统计与因子分析**
```
statsmodels
scipy
scikit-learn
alphalens-reloaded   # 横截面下名正言顺
statsmodels.stats.multitest  # Benjamini-Hochberg FDR
```

**Regime 分析（市场层面）**
```
hmmlearn
ruptures
arch
```

**回测**
```
# 注意：vectorbt 对单资产时序顺手，但横截面多空组合（每个 rebalance 对几十个标的
# 排序/分组/配权/算组合损益与换手成本）用它较别扭。阶段 5 评估是否自写轻量横截面
# 回测器，或用更偏"截面打分→组合构建"的框架。先不预设 vectorbt 是唯一选择。
vectorbt>=0.26.0     # 备选/对照
```

**实盘模拟**
```
vnpy_core
vnpy_binance
vnpy_ctastrategy
vnpy_paperaccount
vnpy_datamanager
```

**工具**
```
loguru
pydantic>=2.0
pytest
jupyter
plotly
python-dotenv
```

---

## 项目目录结构

```
xsec-crypto-quant/
│
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
│
├── data/
│   ├── __init__.py
│   ├── universe.py        # 动态池构建（决策1）：逐月Top N，幸存者偏差处理
│   ├── fetcher.py         # 多标的 OHLCV + funding rate 拉取，分页/断点续传
│   ├── realtime.py
│   ├── storage.py         # 按标的+按月分片 parquet
│   └── validate.py        # 时间戳连续性、OHLCV合法性、池成分一致性检验
│
├── features/
│   ├── __init__.py
│   ├── cross_sectional.py # 截面动量、波动率排名、成交量变化等
│   ├── carry.py           # funding rate carry 因子（决策3）
│   ├── orderflow.py       # CVD/Delta 等，仅作二次确认，非主力
│   └── preprocess.py      # 截面 rank 标准化（决策5）
│
├── factors/
│   ├── __init__.py
│   ├── ic_analysis.py     # 横截面 IC / ICIR / alphalens
│   ├── multiple_testing.py# Benjamini-Hochberg FDR 校正
│   ├── quantile_backtest.py # 分层回测（5层单调性）
│   ├── correlation.py     # 因子间相关剔除
│   ├── selection.py
│   └── report.py
│
├── regime/
│   ├── __init__.py
│   ├── market_regime.py   # 市场层面状态（总市值波动率、横截面相关性中位数、BTC主导率）
│   ├── correlation_spike.py # 相关性飙升检测 → 降杠杆触发
│   └── risk_params.py
│
├── strategy/
│   ├── __init__.py
│   ├── signal.py          # 横截面打分
│   ├── portfolio.py       # 分层等权 + 美元中性 + 杠杆约束（决策6/7/8）
│   ├── rebalance.py       # 日频再平衡 + 无操作带（决策4b）
│   └── risk.py
│
├── backtest/
│   ├── __init__.py
│   ├── xsec_runner.py     # 横截面多空组合回测器
│   ├── walkforward.py
│   ├── overfitting_check.py # 参数敏感性 + Deflated Sharpe + Monte Carlo
│   ├── deflated_sharpe.py
│   └── metrics.py
│
├── live/
│   ├── __init__.py
│   ├── vnpy_app.py
│   └── monitor.py
│
├── notebooks/
│   ├── 01_data_exploration.ipynb
│   ├── 02_universe_construction.ipynb
│   ├── 03_factor_analysis.ipynb
│   ├── 04_regime_analysis.ipynb
│   ├── 05_strategy_research.ipynb
│   └── 06_backtest_results.ipynb
│
└── tests/
    ├── test_data.py
    ├── test_universe.py
    ├── test_features.py
    ├── test_factors.py
    └── test_backtest.py
```

---

## 开发阶段与任务分解

### 阶段 −0.5：数据可得性审计（在写任何拉取代码之前）

**目标**：避免"把 edge 押在拿不到历史数据的特征上"。

任务：制作一张表格，每个计划使用的因子写明：数据源、历史起始日、获取成本、是否可回测。原则——历史 L2 盘口（OBI/WMP/Depth）在交易所 REST 拿不到历史，**整体删除或降级为"从现在起录制、仅供未来 paper trading"**，不进历史回测。逐月历史市值快照确认 CoinGecko/CMC 可得。

**验收**：审计表完成，所有进入阶段 1 的因子均确认历史可得。

---

### 阶段 0：环境与数据层

**目标**：环境 100% 可运行，多标的数据层 + 动态池验证通过。

任务清单：
1. 创建 conda 环境 `xsec-crypto-quant` 并安装全部依赖
2. 创建 `.env.example`（OKX_API_KEY, OKX_SECRET, OKX_PASSPHRASE, COINGECKO_API_KEY）
3. `data/universe.py`：实现动态池（决策 1）——逐月按过去 30 天平均成交额选 Top 30 永续，上市满 90 天，输出每个月份的真实可交易成分列表
4. `data/fetcher.py`：多标的 `fetch_ohlcv` 与 `fetch_funding_rate`，自动分页 + 断点续传
5. `data/storage.py`：按标的 + 按月分片 parquet（命名 `okx_perp_{symbol}_1d_{year}_{month}.parquet`）
6. `data/validate.py`：时间戳连续性、OHLCV 合法性、异常值检测、**池成分一致性**（确认无幸存者偏差）
7. `tests/test_data.py` + `tests/test_universe.py` 全部通过
8. `notebooks/01` + `02`：演示拉取→存储→读取，及动态池随时间演化的可视化

**验收标准**：
- 能拉取动态池内全部标的 1D OHLCV + funding，无 gap
- 动态池能正确反映历史上市/退市（抽查 2021 的池子应含当时存在、现已消失的币）
- 本地存储读取与原始一致
- 验证报告能发现人为注入的 gap 和异常值

---

### 阶段 1：横截面因子工程

**目标**：构建有横截面区分度的日级因子，截面 rank 标准化。

`features/cross_sectional.py`：
```
# 截面动量：过去 N 天累计收益的截面排名（N 通过 Walk-Forward 选，禁止全量选参）
# 波动率排名：已实现波动率的截面排名
# 成交量/换手变化：成交量 z-score 的截面排名
```

`features/carry.py`：
```
# funding rate carry（决策3）：当前/滚动 funding 的截面排名
# 假设：funding 极高 = 拥挤多头 = 均值回归倾向（需在阶段2用数据验证，非先验断定）
```

`features/orderflow.py`（仅二次确认，非主力）：
```
# 保留 CVD、Trade Delta 各一两个，明确标注为确认项
# 不实现任何依赖历史 L2 盘口的特征（已在审计阶段删除）
```

`features/preprocess.py`：
```
# 每个时点对池内所有标的做截面 rank（决策5）
# 暂不 winsorize / 不中性化（决策11，记录待补）
```

**验收标准**：所有因子有单元测试；notebook 目视验证截面排名逻辑；池内全标的单日因子计算 < 5 秒。

---

### 阶段 2：横截面因子分析

**目标**：验证因子横截面预测力，剔除冗余，**做多重检验校正**。

`factors/ic_analysis.py`：
```
# 预测目标：forward_return(t, n)，n 测 1D/3D/5D/10D（日级）
# 横截面 IC：每个时点因子排名与未来截面收益的 Spearman 相关
# ICIR = mean(IC) / std(IC)
# alphalens-reloaded 在横截面下名正言顺，可直接用
```

`factors/multiple_testing.py`（**新增，最关键**）：
```
# 同时测 N 因子 × 4 horizon，不校正几乎必出假因子
# 用 Benjamini-Hochberg (FDR) 控制错误发现率，而非裸 p<0.05
# 报告校正前后通过的因子数对比
```

`factors/quantile_backtest.py`（**新增**）：
```
# 按因子打分分5层，看 Q1→Q5 收益是否单调递增
# 这是判断因子真伪最直观的图，比单一 IC 数字可信
```

`factors/correlation.py`：
```
# Spearman 相关矩阵；>0.7 的因子对保留 ICIR 更高者
# 层次聚类分组；最终池 ≤ 10 个
```

> **概念提示**（首次出现需向用户一句话解释）：IC（信息系数）= 因子打分与未来收益的截面相关性；ICIR = IC 的稳定性（均值/标准差）；FDR = 多重检验下控制"假阳性比例"的方法；Deflated Sharpe = 扣除试错运气后的 Sharpe。

**验收标准**：
- 生成 HTML 因子分析报告，含 IC、ICIR、**FDR 校正后 p 值**、**分层回测图**
- 经 FDR 校正后仍显著的因子筛出 3–8 个、低相关
- 所有筛选决策有统计依据并记录阈值调整原因

---

### 阶段 3：市场层面 Regime 分析

**目标**：识别全市场状态，用于相关性危机时降杠杆（**不是给单个币定状态**）。

`regime/market_regime.py`：
```
# 三状态 HMM：低波动 / 趋势 / 高波动危机
# 输入：总市值已实现波动率、横截面相关性中位数、BTC 主导率变化、池内收益绝对值均值
# 严禁 look-ahead：扩展窗口重拟合，不得全序列拟合后回用状态
```

`regime/correlation_spike.py`（铁律配套）：
```
# 实时监测池内横截面相关性中位数
# 飙升超阈值 → 触发整体降杠杆/空仓信号
```

`regime/risk_params.py`：
```python
REGIME_RISK_PARAMS = {
    0: {"gross_leverage_multiplier": 1.0},   # 低波动
    1: {"gross_leverage_multiplier": 1.0},   # 趋势
    2: {"gross_leverage_multiplier": 0.2},   # 高波动危机：大幅降杠杆
}
# 注意：杠杆乘子作用后仍不得突破单边1x/合计2x红线
```

**验收标准**：
- HMM 状态对照总市值走势可解释；状态切换频率合理
- **删除原"必须识别出2020熔断/2021顶部"的验收项**——事后贴标签不等于预测力，且全序列拟合是 look-ahead

---

### 阶段 4：横截面多空策略

**目标**：因子打分 → 排序 → 分层等权多空 → 美元中性 → 杠杆约束。

`strategy/signal.py`：
```
# 多因子合成截面打分（等权或简单线性合成，先最简单）
# 输出：池内每个标的的截面 rank 分
```

`strategy/portfolio.py`（决策 6/7/8）：
```
# 做多打分最高 20%（等权），做空最低 20%（等权）
# 美元中性：多空两腿名义金额相等
# 杠杆：单边 ≤ 1x，合计 ≤ 2x（硬约束，不可调）
# 乘以 Regime 杠杆乘子后再次裁剪到红线内
```

`strategy/rebalance.py`（决策 4/4b/9）：
```
# 日频再平衡
# 无操作带：新旧持仓差异 < 阈值则不调，抑制换手
# 信号用收盘后因子，下一根 bar 开盘成交
```

---

### 阶段 5：回测（统计诚实的核心）

**目标**：严格验证策略，量化过拟合，诚实判定 edge 真伪。

数据分割（铁律，三段而非两段）：
```
In-sample:     2020-01-01 ~ 2022-12-31  （因子筛选、参数初步范围）
Validation:    2023-01-01 ~ 2023-12-31  （参数最终选择，日常迭代只在这里做）
Locked holdout:最近 6~12 个月            （全周期只解锁一次，看完不许再改任何参数）
```
> "样本外只看一次"靠 locked holdout 落地。日常所有调参、试错都在 validation 上完成；holdout 是给清醒时的自己立的、防止冲动时偷看的规矩。

`backtest/xsec_runner.py`：横截面多空组合回测器（排序/分组/等权/组合损益/逐期 funding/换手成本）。

`backtest/metrics.py` 必算指标：
```
收益类：年化、总收益
风险类：最大回撤及持续时间、年化波动、下行偏差
风险调整：Sharpe、Sortino、Calmar（均报 bootstrap 置信区间，非点估计）
横截面专属：分层回测单调性、组合对 BTC 的实际 beta（验证美元中性是否近似 beta 中性）
交易质量：胜率、盈亏比、平均持仓、换手率、交易次数
基准对比：与 BTC Buy&Hold、与等权持有全池对比
```

`backtest/overfitting_check.py` + `deflated_sharpe.py`：
```
1. 参数敏感性：最优值±20%扫描，Sharpe下降>30%则不稳健
2. Walk-Forward：训练18M/验证6M/步进3M；标准是各窗口 Sharpe 分布不极端分化
   （不要求"每个窗口都正收益"——那是对 walk-forward 的过拟合）
3. Monte Carlo：bootstrap 1000 次，报告最大回撤与 Sharpe 的 95% 置信区间
4. Deflated Sharpe Ratio：输入试过的参数组合数，惩罚试错次数，给出扣运气后的 Sharpe
```

**验收标准（统计诚实，非盈利门槛）**：
- 完整跑通全流程，每步统计决策可追溯
- 因子经 FDR 校正后显著、分层回测单调
- 扣除成本+funding 后，Sharpe 的 bootstrap 置信区间**与 0 的关系**被诚实报告
- **达成主线（edge 稳健）→ 进阶段 6；触发否定结论（决策 12）→ 记录复盘收尾，同样视为项目成功**

---

### 阶段 6：实盘模拟（Paper Trading）

**目标**：真实市场环境验证，零资金风险。仅当阶段 5 达成主线才进入。

`live/vnpy_app.py`：
```
- vnpy_paperaccount，初始 100,000 USDT 模拟
- 永续合约，手续费 0.05%（OKX Taker），滑点 1 tick，逐期结算 funding
- 实时 WebSocket 数据；每笔交易记录所有因子值、Regime、仓位、盈亏归因
```

`live/monitor.py`：
```
- 日回撤 >3%：警告
- 横截面相关性飙升：触发降杠杆（与 regime 模块联动）
- 连续 N 笔亏损：暂停，人工检查
- 数据中断 >5min：告警
```

运行 ≥ 4 周后评估与回测一致性。

**验收标准**：胜率、盈亏比、换手率与回测相差 ≤15%；无代码 bug 导致的异常交易；每笔可溯源到信号。

---

## GitHub 仓库管理（工作约定，从立项第一步执行）

Claude Code 全程用 git 管理本项目，规范如下。

### 仓库初始化（阶段 0 第一步）
- 在阶段 0-2 创建目录骨架时，立即 `git init`，并创建 `.gitignore`。
- `.gitignore` 必须包含：`.env`、`data/`（所有 parquet 与原始数据）、`__pycache__/`、`*.pyc`、`.ipynb_checkpoints/`、conda/venv 目录、`*.log`、模型与缓存产物。
- 引导用户在 GitHub 创建空私有仓库，`git remote add origin <url>`，首次 `git push -u origin main`。

### 分支策略
- `main`：始终保持可运行、测试通过的状态。
- 每个阶段开一条分支：`phase-0-data`、`phase-1-features`、`phase-2-factors`…
- 阶段内每个具体功能可再开短分支：`phase-2-fdr-correction`。
- 一个阶段验收通过后，合并回 `main` 并打 tag。

### 提交规范（Conventional Commits）
- 格式：`<type>(<scope>): <subject>`，type 用 `feat / fix / test / docs / refactor / data / chore`。
- 示例：
  - `feat(data): add dynamic universe builder with survivorship handling`
  - `test(factors): add FDR correction unit tests`
  - `fix(portfolio): clamp gross leverage to 2x hard limit`
- **每完成一个文件或一个功能函数就提交一次**（与下方"逐步推进"约定对齐），不堆积大提交。
- 提交信息说清"做了什么 + 为什么"，便于将来复盘方法论（决策 12 的否定结论复盘也依赖 git 历史）。

### 里程碑标签
- 每个阶段验收通过打 tag：`v0.0-data`、`v0.1-features`、`v0.2-factors`、`v0.5-backtest`、`v1.0-paper`。
- **特别地**：阶段 5 解锁 locked holdout 之前打一个 tag（如 `pre-holdout-freeze`），解锁并看完结果之后**不得再修改任何参数**——git 历史用于证明"holdout 只看了一次、之后无参数改动"。这是统计诚实的可审计证据。

### 严禁提交
- `.env` 与任何 API key/secret（一旦误提交，立即轮换密钥并用 `git filter-repo` 清史）。
- `data/` 下的市场数据（体积大且可重新拉取）。
- 任何在 in-sample/holdout 上反复试参数的"隐藏迭代"——所有迭代都应体现在 commit 历史里，保持研究过程透明可追溯。

### 关键节点要求 PR/自审
- 阶段合并回 `main` 前，Claude Code 应输出一段变更摘要（改了什么、测试是否全过、有无偏离决策清单），相当于一次自审 PR description，由用户确认后再合并。

---

## Claude Code 工作方式约定（重要）

**逐步推进，禁止一次性生成所有代码**：
- 每次只完成一个具体文件或一个功能函数，完成后输出、提交 git，等用户实际运行反馈后再继续。
- 不允许在用户还没跑通上一步时提前给下一步代码。

**遇到以下情况必须停下来提问**：
- 统计参数选择（HMM 状态数、IC/FDR 阈值、动量窗口 N、无操作带阈值）有多个合理选项时
- 设计有歧义或两种实现各有利弊时
- 用户反馈的错误涉及业务逻辑判断而非纯技术时
- 需要用户提供真实运行结果（如 IC 数值、FDR 通过数）才能决定方向时

**主动解释统计概念**：首次出现 IC、ICIR、FDR、Deflated Sharpe、Walk-Forward、Calmar、HMM、bootstrap 置信区间等概念时，用一两句话解释，不假设用户记得每个细节。

**守住地基**：任何实现若与第一部分决策清单冲突（尤其杠杆红线、美元中性、动态池防幸存者偏差、FDR 校正、locked holdout 只看一次），视为 bug，必须停下来纠正。

---

## 第一个任务（立即开始）

按顺序逐步执行，每步等用户确认后再继续，**每步完成即 git 提交**：

- **Step 0-1**：`conda create -n xsec-crypto-quant python=3.12 -y` → 激活 → 输出环境信息让用户确认。
- **Step 0-2**：创建目录骨架 + 空 `__init__.py` + `.gitignore` + `.env.example`；`git init`、首次提交、引导用户建 GitHub 私有仓库并 push。
- **Step 0-3**：生成 `pyproject.toml`，安装依赖；等用户反馈安装结果，逐个解决失败包。
- **Step 0-4**：（阶段 −0.5）先和用户一起完成数据可得性审计表，确认每个因子历史可得。
- **Step 0-5**：实现 `data/universe.py`（动态池），跑最小测试（取最近 3 个月的池成分）确认无误。
- **Step 0-6**：实现 `data/fetcher.py`，跑最小测试（取池内 1 个标的最近 100 根 1D + funding）。
- **Step 0-7**：实现 `data/storage.py`，跑存取往返测试。
- **Step 0-8**：实现 `data/validate.py`，跑验证脚本，确认能检出人为注入的问题与池成分不一致。
- **Step 0-9**：编写并跑通 `tests/test_data.py` + `tests/test_universe.py`，全 pass。
- **Step 0-10**：完成 `notebooks/01` + `02`，合并 `phase-0-data` 回 `main`，打 tag `v0.0-data`，阶段 0 结束，进入阶段 1。

---

*文档版本：v2.0 | 维度：横截面 Top N 永续 | 策略：日频多空中性（美元中性，单边≤1x/合计≤2x）| 因子预处理：截面 rank | 交易所：OKX | 定位：统计诚实的盈利研究 | 决策清单见第一部分*
