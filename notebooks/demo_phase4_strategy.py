"""阶段 4 教学演示：横截面多空策略全链路（打分→组合→降杠杆→无操作带→成交时点）。

把阶段 1~4 串起来,在埋了'动量真有预测力 + 中段危机'的虚构市场上,完整生成一本
多空持仓账本,并展示每一道纪律如何生效：

    因子(阶段1) → 截面打分(signal) → 分层等权美元中性(portfolio)
    → Regime/相关飙升降杠杆(阶段3 + portfolio) → 无操作带省换手(rebalance)
    → 次日开盘成交时点(rebalance) → 组合风控校验(risk)

跑法（仓库根目录）：
    .venv/Scripts/python.exe notebooks/demo_phase4_strategy.py
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from loguru import logger  # noqa: E402

logger.remove()
logging.getLogger("hmmlearn").setLevel(logging.ERROR)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factors.ic_analysis import cross_sectional_ic, forward_return, ic_summary  # noqa: E402
from features.cross_sectional import momentum  # noqa: E402
from regime.correlation_spike import detect_correlation_spike  # noqa: E402
from regime.market_regime import compute_market_features, label_regimes_expanding  # noqa: E402
from strategy.portfolio import apply_regime_leverage, build_target_weights  # noqa: E402
from strategy.rebalance import apply_no_trade_band, to_execution, turnover  # noqa: E402
from strategy.risk import check_portfolio, gross_exposure, net_exposure  # noqa: E402
from strategy.signal import cross_sectional_score, signs_from_mean_ic  # noqa: E402

pd.set_option("display.float_format", lambda x: f"{x:8.4f}")
pd.set_option("display.width", 140)
LINE = "=" * 82


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def make_market(seed: int = 4):
    """20 币 × 360 天：持续 drift(动量可预测) + 中段 60 天危机(齐跌高相关)。"""
    rng = np.random.default_rng(seed)
    n_coins = 20
    mu = rng.normal(0.0, 0.0025, n_coins)  # 持续横截面 drift → 动量预测力
    blocks = [(150, 0.006, 0.0005, 0.020), (60, 0.05, -0.012, 0.006), (150, 0.006, 0.0005, 0.020)]
    rows = []
    for days, common_sig, drift, idio_sig in blocks:
        common = rng.normal(drift, common_sig, days)
        idio = mu[None, :] + rng.normal(0, idio_sig, (days, n_coins))
        rows.append(common[:, None] + idio)
    ret = np.vstack(rows)
    n = ret.shape[0]
    dates = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    close = pd.DataFrame(100 * np.cumprod(1 + ret, axis=0), index=dates,
                         columns=[f"C{i:02d}" for i in range(n_coins)])
    return close, (150, 210)


def main() -> None:
    close, (c0, c1) = make_market()
    crisis = (close.index >= close.index[c0]) & (close.index < close.index[c1])

    section("阶段 4 全链路｜20 币 × 360 天（动量可预测 + 中段危机）")

    # --- 1) 因子 + IC 定方向 ---
    section("第 1 步｜因子 + 用 IC 符号定方向（阶段 1/2）")
    panels = {"momentum_10": momentum(close, 10), "momentum_20": momentum(close, 20)}
    mean_ic = {n: ic_summary(cross_sectional_ic(p, forward_return(close, 5)))["mean_ic"]
               for n, p in panels.items()}
    signs = signs_from_mean_ic(mean_ic)
    print(f"各因子 5d 平均 IC：{ {k: round(v, 4) for k, v in mean_ic.items()} }")
    print(f"由 IC 符号定的方向：{signs}（>0 正用,<0 反用;此处动量 IC 为正）")

    # --- 2) 截面打分 ---
    section("第 2 步｜多因子等权合成截面打分（signal）")
    score = cross_sectional_score(panels, signs)
    print("某日打分采样（高=预期强,应做多;低=预期弱,应做空;每日近似零均值）：")
    print(score.iloc[250].dropna().sort_values(ascending=False).head(4).to_string())
    print("  …（中间略）…")
    print(score.iloc[250].dropna().sort_values().head(2).to_string())

    # --- 3) 组合：分层等权 + 美元中性 ---
    section("第 3 步｜分层等权 + 美元中性 目标权重（portfolio,决策 6/7）")
    raw_w = build_target_weights(score, quantile=0.2)
    d = close.index[250]
    book = raw_w.loc[d]
    print(f"{d.date()} 目标账本：做多 {int((book>0).sum())} 个、做空 {int((book<0).sum())} 个")
    print(f"  多腿名义 = {book[book>0].sum():.2f}x　空腿名义 = {book[book<0].sum():.2f}x　"
          f"净 = {book.sum():.2f}（美元中性）　合计 = {book.abs().sum():.2f}x")

    # --- 4) 无操作带省换手（在目标层先做,杠杆裁剪放最后保证红线）---
    section("第 4 步｜无操作带省换手（rebalance,决策 4b）")
    banded = apply_no_trade_band(raw_w, band=0.05)
    t_no, t_band = turnover(raw_w).mean(), turnover(banded).mean()
    print(f"平均日换手(目标层)：无带 {t_no:.4f}　有带(0.05) {t_band:.4f}　"
          f"省下 {(t_no - t_band) / t_no:.0%}")
    print("  诚实发现：等权分位账本的持仓是'整档进/出'的离散跳变(±0.25),小于跳变的")
    print("  band(0.05)几乎不触发 → 省得很少。对分位策略,真正的换手开关是'分位宽度 +")
    print("  成员滞回(hysteresis)',留待阶段 5 调;weight-delta band 对连续/小幅调仓才明显。")
    print("  另注：无操作带会让账本略偏离精确中性/2x,所以**杠杆裁剪必须放最后**(见第 5 步)。")

    # --- 5) Regime/相关飙升 → 降杠杆 + 红线裁剪（流水线最后一步）---
    section("第 5 步｜Regime + 相关飙升 → 降杠杆 + 红线裁剪（阶段 3 接入,放最后）")
    feats = compute_market_features(close, vol_window=14, corr_window=20)
    regime = label_regimes_expanding(feats, n_states=3, min_train=120, refit_every=20)
    spike = detect_correlation_spike(close, corr_window=20)["corr_spike"]
    final_w = apply_regime_leverage(banded, regime=regime, corr_spike=spike)
    gross = gross_exposure(final_w)
    print(f"平静段平均合计杠杆 = {gross[~crisis].mean():.3f}x")
    print(f"危机段平均合计杠杆 = {gross[crisis].mean():.3f}x  ← 自动压低")
    print(f"全程最大合计杠杆   = {gross.max():.3f}x（红线 2.0x,never 超 = {gross.max() <= 2.0 + 1e-9}）")

    # --- 6) 成交时点 ---
    section("第 6 步｜成交时点：收盘后决定,下一根 bar 开盘生效（决策 9）")
    exec_w = to_execution(final_w)
    print(f"首日开盘前持仓全为 0：{(exec_w.iloc[0].abs().sum() == 0)}")
    print("  exec_w 第 t 行 = 第 t-1 日收盘后决定、第 t 日开盘起持有 → 无前视。")

    # --- 7) 组合风控校验 ---
    section("第 7 步｜组合风控校验（risk,决策 7/8）")
    rep = check_portfolio(exec_w.dropna(how="all"))
    print(f"  风控校验通过(无 error=红线全守住)：{rep.ok}")
    print(f"  合计杠杆最大：{gross_exposure(exec_w).max():.3f}x（≤2x 红线,硬约束）")
    print(f"  净敞口绝对值最大：{net_exposure(exec_w).abs().max():.4f}"
          f"（无操作带带来的轻微偏离,risk 层记为 warning 而非 error）")

    # --- 8) 小结 ---
    section("第 8 步｜小结（阶段 1~4 串起来了）")
    print("  打分(因子+IC方向) → 分层等权美元中性 → 危机自动降杠杆 → 无操作带省换手")
    print("  → 次日开盘成交 → 风控校验。一本可执行、守纪律的多空账本就此成形。")
    print("  ⚠️ 这还没扣成本、没做样本外检验——'到底赚不赚钱'是阶段 5 回测的事：")
    print("     扣 taker 费 + 滑点 + funding,Deflated Sharpe + locked holdout 诚实判定。")
    print("     量化参数(分位、band、窗口 N)都留待阶段 5 在样本外选并记录原因。")


if __name__ == "__main__":
    main()
