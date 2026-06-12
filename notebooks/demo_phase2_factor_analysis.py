"""阶段 2 教学演示：因子分析全流水线（IC → FDR → 分层回测 → 相关剔除）。

调用阶段 1 真因子模块 + 阶段 2 分析模块,在一个**埋了 ground truth** 的虚构宇宙上,
完整走一遍"诚实判因子真伪"的流程：

    1. 算每个因子×horizon 的截面 IC / ICIR / t 统计量
    2. 多重检验 FDR 校正：对比"裸 p<0.05 通过数" vs "FDR 校正后通过数"
    3. 对最强因子做分层回测：Q1→Q5 是否单调递增
    4. 因子间相关剔除：把冗余的动量变体去掉

埋设（我们知道答案,用来验证流水线是否诚实）：
    - 每个币有持续的横截面 drift → **动量真能预测未来相对收益**（真因子）
    - momentum_20 与 momentum_30 窗口重叠 → 高度相关,相关剔除应只留一个
    - 成交量/funding/波动率 + 一批纯随机因子 → **噪声**
    - 一堆纯噪声因子里,裸 p<0.05 会靠运气冒出假阳性 → FDR 把假阳性【比例】压到 q 内
      （但不保证为零——这正是后续 Deflated Sharpe + locked holdout 存在的理由）

跑法（仓库根目录）：
    .venv/Scripts/python.exe notebooks/demo_phase2_factor_analysis.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from loguru import logger  # noqa: E402

logger.remove()

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from factors.correlation import (  # noqa: E402
    factor_correlation_matrix,
    select_low_correlation,
)
from factors.ic_analysis import (  # noqa: E402
    cross_sectional_ic,
    forward_return,
    ic_summary,
)
from factors.multiple_testing import fdr_report, fdr_summary  # noqa: E402
from factors.quantile_backtest import quantile_backtest  # noqa: E402
from factors.report import save_html  # noqa: E402
from factors.selection import analyze_factors  # noqa: E402
from features.carry import funding_carry, to_daily_funding  # noqa: E402
from features.cross_sectional import (  # noqa: E402
    momentum,
    realized_volatility,
    volume_change,
)

pd.set_option("display.float_format", lambda x: f"{x:9.4f}")
pd.set_option("display.width", 130)
LINE = "=" * 80
N_NOISE = 12  # 纯噪声因子个数,用来制造多重检验压力


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def make_dataset(n_days: int = 300, n_coins: int = 20, seed: int = 3):
    """造一个埋了 ground truth 的宇宙：动量真有预测力,其余是噪声。"""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2023-01-01", periods=n_days, freq="D", tz="UTC")
    coins = [f"C{i:02d}" for i in range(n_coins)]

    mu = rng.normal(0.0, 0.0025, n_coins)            # 持续的横截面 drift = 动量预测力之源
    market = rng.normal(0.0005, 0.02, n_days)
    ret = mu[None, :] + market[:, None] + rng.normal(0, 0.02, (n_days, n_coins))
    close = pd.DataFrame(100 * np.cumprod(1 + ret, axis=0), index=dates, columns=coins)

    volume = pd.DataFrame(rng.uniform(800, 1200, (n_days, n_coins)), index=dates, columns=coins)
    f_idx = pd.date_range("2023-01-01", periods=n_days * 3, freq="8h", tz="UTC")
    funding8h = pd.DataFrame(
        rng.normal(0.0001, 0.00005, (len(f_idx), n_coins)), index=f_idx, columns=coins
    )
    return close, volume, funding8h, rng


def main() -> None:
    close, volume, funding8h, rng = make_dataset()
    carry_daily = to_daily_funding(funding8h)

    # 结构化因子：2 个重叠动量（高相关）+ 3 个弱/噪声因子
    panels: dict[str, pd.DataFrame] = {
        "momentum_20": momentum(close, 20),
        "momentum_30": momentum(close, 30),
        "realized_vol_20": realized_volatility(close, 20),
        "volume_chg_20": volume_change(volume, 20),
        "carry_7": funding_carry(carry_daily, 7),
    }
    # 一批纯随机噪声因子：制造多重检验压力,看 FDR 能否拦住"碰巧显著"
    for i in range(N_NOISE):
        panels[f"noise_{i:02d}"] = pd.DataFrame(
            rng.normal(size=close.shape), index=close.index, columns=close.columns
        )
    horizons = [1, 5, 10]
    n_combos = len(panels) * len(horizons)

    section("阶段 2 全流水线｜20 币 × 300 天（埋设：仅动量真有预测力）")
    print("结构化因子：momentum_20/30（应真）, realized_vol_20/volume_chg_20/carry_7（应弱）")
    print(f"另加 {N_NOISE} 个纯随机噪声因子；前瞻 horizon={horizons}")
    print(f"→ 共 {len(panels)} 因子 × {len(horizons)} horizon = {n_combos} 个 IC 组合要测")
    print("埋设答案：动量=真因子,其余=噪声。看流水线能否诚实区分,并拦住碰巧显著的。")

    # --- 1) IC 汇总 ---
    section("第 1 步｜横截面 IC / ICIR / t 统计量（按 ICIR 排序,只显示前 10）")
    summaries: dict[str, dict[str, float]] = {}
    for name, fac in panels.items():
        for h in horizons:
            ic = cross_sectional_ic(fac, forward_return(close, h))
            summaries[f"{name}@{h}d"] = ic_summary(ic)
    ic_table = pd.DataFrame(summaries).T[["mean_ic", "icir", "t_stat", "n_obs"]]
    ic_table = ic_table.sort_values("icir", ascending=False)
    print(ic_table.head(10))
    print(f"  …（其余 {len(ic_table) - 10} 个组合 ICIR 更低,略）")
    print("\n  读法：mean_ic 正且 ICIR 高 = 排得准又稳。动量类应居前,噪声类趋近 0。")

    # --- 2) FDR ---
    q = 0.10
    section(f"第 2 步｜FDR 校正（q={q}）：裸 p 会靠运气冒假阳性,FDR 控制其比例")
    table = fdr_report(summaries, q=q)
    s = fdr_summary(table)
    survivors = table.index[table["reject"]].tolist()
    caught = table[(table["pvalue"] < 0.05) & (~table["reject"])]          # naive 显著但被 FDR 拦下
    noise_surv = [x for x in survivors if x.startswith("noise")]           # 蒙混过 FDR 的噪声

    print("经 FDR 校正后仍显著的因子：")
    print(table.loc[survivors, ["mean_ic", "icir", "pvalue", "qvalue"]])
    print(f"\n  校正前（裸 p<0.05）通过：{s['n_naive']} 个")
    print(f"  FDR 校正后通过        ：{s['n_fdr']} 个    （共测 {s['n_total']} 个组合）")

    if len(caught) > 0:
        print(f"\n  ✅ FDR 拦下了 {len(caught)} 个'裸 p<0.05 但其实是噪声'的假阳性：")
        print(caught[["mean_ic", "pvalue", "qvalue"]].to_string())
        print("     不做校正,你就会把这些纯随机因子当成 edge。")

    # FDR 的诚实真相：它控制"假阳性比例"≤q,不保证一个都没有
    print(f"\n  统计诚实的关键认识：FDR 把假阳性【比例】控制在 q={q} 以内,")
    print(f"  即 {s['n_fdr']} 个'发现'里,允许期望约 {q*s['n_fdr']:.1f} 个其实是假的——不保证为零。")
    if noise_surv:
        print(f"  本次确实有纯噪声靠运气挤进来：{noise_surv}")
        print("  这不是 bug,正是为什么后面还要 Deflated Sharpe（扣试错运气）+ locked holdout")
        print("  （只看一次）层层设防——单靠 FDR 不够,这就是三段切分存在的理由（决策见阶段5）。")
    else:
        print("  本次没有噪声蒙混过关,但这靠的是运气好;防线不能只靠 FDR 一道。")

    # --- 3) 分层回测 ---
    section("第 3 步｜分层回测：最强因子 Q1→Q5 是否单调递增")
    best = table.index[0]
    best_name, best_h = best.split("@")
    res = quantile_backtest(
        panels[best_name], forward_return(close, int(best_h.rstrip("d"))), n_quantiles=5
    )
    means = res["quantile_means"]
    print(f"最强因子：{best}\n各层平均前向收益（Q1=最低分 … Q5=最高分）：")
    for q, v in means.items():
        print(f"  Q{int(q)}: {v: .5f}  {'█' * max(0, int(v * 3000))}")
    print(f"\n  单调性 = {res['monotonicity']:.3f}（+1=完美单调）   顶-底价差 = {res['spread']:.5f}")
    print("  真因子应逐层抬升;若五层乱成一团,IC 那个数字就不可信。")

    # --- 4) 相关剔除 ---
    section("第 4 步｜相关剔除：去掉冗余动量变体（|相关|>0.7 留 ICIR 更高者）")
    structural = ["momentum_20", "momentum_30", "realized_vol_20", "volume_chg_20", "carry_7"]
    corr = factor_correlation_matrix({k: panels[k] for k in structural})
    print("结构化因子相关矩阵：")
    print(corr.round(3))
    icir5 = {k: summaries[f"{k}@5d"]["icir"] for k in structural}
    kept = select_low_correlation(corr, icir5, threshold=0.7, max_factors=10)
    print(f"\n  各因子 5d ICIR：{ {k: round(v, 3) for k, v in icir5.items()} }")
    print(f"  momentum_20 ↔ momentum_30 相关 = {corr.loc['momentum_20', 'momentum_30']:.3f}")
    print(f"  低相关精选池：{kept}")
    print("  两个动量窗口重叠高相关 → 只留 ICIR 更高者,避免在多重检验里重复消耗试错预算。")

    # --- 5) 诚实判读 ---
    section("第 5 步｜诚实判读（决策 12 的灵魂）")
    real_survived = any(x.startswith("momentum") for x in survivors)
    noise_survived = [x for x in survivors if x.startswith("noise")]
    mono_ok = isinstance(res["monotonicity"], float) and res["monotonicity"] > 0.8
    print(f"  • 真因子（动量）经 FDR 后仍显著：{'是' if real_survived else '否'}")
    print(f"  • 有纯噪声因子蒙混过 FDR：{noise_survived if noise_survived else '无 ✅'}")
    print(f"  • 最强因子分层单调（>0.8）：{'是' if mono_ok else '否'}")
    print(f"  • FDR 拦下的假阳性数：{len(caught)}")
    print()
    print("  这套流程让'我们找到了 edge'变得可被审计：真因子通过、噪声被挡、分层单调,")
    print("  才有底气进下一阶段;若全军覆没 → 诚实记录否定结论,绝不调参硬凑")
    print("  （决策 12：没赚到 ≠ 失败,自欺地拿假因子去交易、亏钱,才是失败）。")
    print("  ⚠️ 本 demo 用虚构数据,只为验证流水线本身;真实判读须在真实数据上,")
    print("     且 q 值与 horizon 取舍要等真实 p 值分布出来再定并记录原因。")

    # --- 6) 一行编排 + 生成 HTML 报告 ---
    section("第 6 步｜一行编排 analyze_factors + 导出 HTML 报告")
    analysis = analyze_factors(panels, close, horizons=tuple(horizons), q=0.10)
    out = save_html(analysis, path="reports/factor_analysis_demo.html")
    print(f"  selection.analyze_factors 一行跑完上面 1~4 步,精选池：{analysis.selected}")
    print(f"  report.save_html 已生成报告：{out}")
    print("  用浏览器打开它,即可看到 IC/ICIR/FDR q 值表 + 分层条形图 + 诚实声明。")


if __name__ == "__main__":
    main()
