# xsec-crypto-quant

> 横截面加密货币多空量化**研究**项目。日频再平衡，多空中性，永续合约。

## 这是什么

一套以**盈利为目的、统计上诚实**的横截面量化研究系统。它在一个动态选取的 Top N 永续合约篮子里，做多相对强势的币、做空相对弱势的币，组合美元中性，每日再平衡。

项目的最高信条：

> **诚实研究不是放弃盈利动机，而是不让"我想盈利"这个动机污染"它到底能不能盈利"这个判断。**

盈利是目的，统计诚实是手段——是确保"如果说找到了 edge，那它大概率是真的"的那道防线。完整的定位与全部根决策见 [`CLAUDE.md`](./CLAUDE.md) 第一部分《阶段 −1 立项决策清单》。

## 核心决策一览

| 维度 | 选择 |
|---|---|
| 资产维度 | 横截面（动态 Top 30 永续，逐月按成交额选，防幸存者偏差） |
| 交易周期 | 日频再平衡 |
| 方向 | 多空中性（相对收益） |
| 标的 | 永续合约（funding 进损益，同时作 carry 因子） |
| 配权 | 分层等权 |
| 两腿平衡 | 美元中性 |
| 杠杆红线 | 单边 ≤ 1x，合计名义 ≤ 2x |
| 因子预处理 | 截面 rank（起步） |
| 多重检验 | Benjamini-Hochberg FDR + Deflated Sharpe |
| 样本外 | 三段切分，locked holdout 全周期只解锁一次 |

## 风控铁律

1. 杠杆：单边 ≤ 1x，合计 ≤ 2x，全程不可放开。
2. 相关性危机：加密资产相关性危机时趋近 1，多空对冲会失效；regime 模块检测到横截面相关性飙升即整体降杠杆/空仓。
3. 成本：回测扣 taker 费 + 滑点 + 逐期 funding，宁可低估收益。
4. 数据时点：收盘后算信号，下一根 bar 开盘成交，严禁 look-ahead。
5. 样本外：locked holdout 只看一次，看完不改参数（git tag 留证）。

## 阶段路线

- **−0.5 数据可得性审计** — 砍掉拿不到历史的特征
- **0 环境 + 数据层** — conda 环境、动态池、多标的 OHLCV+funding、验证
- **1 横截面因子工程** — 截面动量 / carry / 波动率排名，rank 标准化
- **2 因子分析** — 横截面 IC/ICIR、FDR 校正、分层回测
- **3 市场层面 Regime** — 相关性飙升检测 → 降杠杆
- **4 多空策略** — 打分→排序→分层等权→美元中性→杠杆约束
- **5 回测** — 三段切分、Deflated Sharpe、bootstrap 置信区间
- **6 Paper Trading** — vnpy 模拟，≥4 周验证一致性

成功标准是统计诚实地跑通全流程并诚实回答"edge 是否真实"——找到 edge 进 paper trading 是主线；未找到则记录否定结论收尾，同样视为成功（不调参硬凑）。

## 快速上手

```bash
# 1. 创建环境
conda create -n xsec-crypto-quant python=3.11 -y
conda activate xsec-crypto-quant

# 2. 安装依赖
pip install -e .

# 3. 配置密钥
cp .env.example .env
# 填入 BINANCE_API_KEY / BINANCE_SECRET / COINGECKO_API_KEY

# 4. 跑测试
pytest -q
```

## 目录结构

```
data/       动态池、数据拉取、存储、验证
features/   横截面因子、carry、订单流（仅确认项）、rank 预处理
factors/    IC 分析、FDR 校正、分层回测、相关剔除
regime/     市场层面 HMM、相关性飙升检测、风控参数
strategy/   打分、组合构建、再平衡、风控
backtest/   横截面回测器、walk-forward、过拟合检验、Deflated Sharpe
live/        vnpy paper trading、监控告警
notebooks/  各阶段研究 notebook
tests/      单元测试
```

## 工作约定

- 全程 git 管理，Conventional Commits，每阶段一条分支，验收打 tag。
- `.env` 与 `data/` 永不入库。
- 代码逐步推进：每完成一个文件/函数即提交，跑通后再写下一步。
- 任何与决策清单冲突的实现视为 bug。

---

*版本 v2.0 ｜ 详见 [`CLAUDE.md`](./CLAUDE.md)*
