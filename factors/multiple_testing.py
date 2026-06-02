"""多重检验校正：Benjamini-Hochberg FDR（阶段 2,最关键）。

为什么必须做（CLAUDE.md 阶段 2 / 决策清单）：
    本项目要测 ~4 因子族 × 5 窗口 × 4 horizon ≈ 数十个 IC 组合。若对每个都用裸
    ``p < 0.05``,纯靠运气也会冒出一批"假显著"因子（测 80 个,期望 ~4 个假阳性）。
    **不做多重检验校正就声称因子显著 = 项目明令禁止的做法之一。**

FDR（False Discovery Rate,错误发现率）= 在"被判为显著"的因子里,**假阳性所占的比例**。
Benjamini-Hochberg 过程把这个比例控制在目标 ``q`` 以内：
    1. 把 m 个 p 值升序排列 p(1) ≤ … ≤ p(m)；
    2. 找最大的 k 使 p(k) ≤ (k/m)·q；
    3. 拒绝前 k 个原假设（即认定它们显著）。
同时给出 BH 调整后的 q 值（q-value）：可直接和 q 比较的"校正后 p 值"。

> 概念（首次出现）：**FDR** = 多重检验下控制"假阳性比例"的方法,比逐个看 p<0.05 诚实
> 得多；**q-value** = 某因子被判显著时,所牵连的整体假阳性率。

参数 q：**默认 0.10,但最终取值是待定决策**——按约定,阶段 2 先把全部 p 值算出来,看到
真实分布后再定 q,且任何调整都要记录原因（决策清单不可中途随意漂移）。

纯逻辑,仅依赖 pandas/numpy/scipy,可离线单测。BH 实现与
``statsmodels.stats.multitest(method="fdr_bh")`` 一致,这里自写以便透明与单测。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

# FDR 目标错误发现率默认值。注意：这是**待定决策**,见模块 docstring。
DEFAULT_FDR_Q = 0.10
# 朴素（未校正）显著性阈值,仅用于"校正前 vs 校正后"对比展示,不作判定依据。
NAIVE_ALPHA = 0.05


def pvalue_from_tstat(t_stat: float, n_obs: int, two_sided: bool = True) -> float:
    """由 IC 的 t 统计量和有效期数算双侧 p 值（t 分布,df = n_obs - 1）。

    t_stat 见 ``factors.ic_analysis.ic_summary``（= ICIR × sqrt(N)）。
    ``n_obs <= 1`` 或 t_stat 非有限时返回 NaN。
    """
    if n_obs is None or n_obs <= 1 or not np.isfinite(t_stat):
        return float("nan")
    df = n_obs - 1
    if two_sided:
        return float(2.0 * stats.t.sf(abs(t_stat), df))
    return float(stats.t.sf(t_stat, df))


def benjamini_hochberg(pvalues: pd.Series, q: float = DEFAULT_FDR_Q) -> pd.DataFrame:
    """对一组 p 值做 BH-FDR 校正,返回按 p 值升序的结果表。

    Parameters
    ----------
    pvalues : Series
        index = 因子（或因子×horizon）名,values = 原始 p 值。NaN 会被剔除。
    q : float
        目标错误发现率（待定决策,默认 0.10）。

    Returns
    -------
    DataFrame
        index = 因子名（按 p 值升序）,列：
            pvalue       原始 p 值
            rank         升序名次（1..m）
            bh_critical  BH 临界线 (rank/m)·q
            qvalue       BH 调整后 q 值（可直接与 q 比较）
            reject       是否在 q 下判为显著（True=发现）
    """
    if not 0 < q < 1:
        raise ValueError(f"q 必须在 (0, 1),收到 {q}")
    p = pvalues.dropna().sort_values()
    m = int(len(p))
    if m == 0:
        return pd.DataFrame(
            columns=["pvalue", "rank", "bh_critical", "qvalue", "reject"]
        )
    ranks = np.arange(1, m + 1)
    pv = p.to_numpy(dtype=float)
    bh_critical = ranks / m * q

    # 找最大的 k 使 p(k) <= (k/m)·q；拒绝该 p 值及更小的全部
    below = pv <= bh_critical
    if below.any():
        kmax = int(np.max(np.nonzero(below)[0]))  # 0-based
        reject = np.arange(m) <= kmax
    else:
        reject = np.zeros(m, dtype=bool)

    # BH 调整 q 值：从大到小取累计最小的 (m/rank)·p,并裁剪到 [0,1]
    raw_adj = (m / ranks) * pv
    qvalue = np.minimum.accumulate(raw_adj[::-1])[::-1]
    qvalue = np.clip(qvalue, 0.0, 1.0)

    return pd.DataFrame(
        {
            "pvalue": pv,
            "rank": ranks,
            "bh_critical": bh_critical,
            "qvalue": qvalue,
            "reject": reject,
        },
        index=p.index,
    )


def fdr_report(
    summaries: dict[str, dict[str, float]], q: float = DEFAULT_FDR_Q
) -> pd.DataFrame:
    """把多个因子的 IC 汇总表 → p 值 → BH 校正,产出阶段 2 的核心判读表。

    Parameters
    ----------
    summaries : dict
        {因子名: ``ic_analysis.ic_summary`` 的输出}。需含 mean_ic / icir / t_stat / n_obs。
    q : float
        目标 FDR（待定决策,默认 0.10）。

    Returns
    -------
    DataFrame
        index = 因子名（按 p 值升序）,列含 mean_ic / icir / t_stat / n_obs / pvalue /
        qvalue / reject,便于直接阅读"哪些因子经 FDR 校正后仍显著"。
    """
    pvals = {
        name: pvalue_from_tstat(s.get("t_stat", float("nan")), int(s.get("n_obs", 0)))
        for name, s in summaries.items()
    }
    bh = benjamini_hochberg(pd.Series(pvals), q=q)
    context = pd.DataFrame(
        {
            name: {
                "mean_ic": s.get("mean_ic", float("nan")),
                "icir": s.get("icir", float("nan")),
                "t_stat": s.get("t_stat", float("nan")),
                "n_obs": s.get("n_obs", 0),
            }
            for name, s in summaries.items()
        }
    ).T
    out = context.join(bh[["pvalue", "qvalue", "reject"]], how="right")
    return out.loc[bh.index]  # 保持按 p 值升序


def fdr_summary(table: pd.DataFrame, naive_alpha: float = NAIVE_ALPHA) -> dict[str, int]:
    """"校正前 vs 校正后通过数"对比（CLAUDE.md 要求的报告项）。

    Returns
    -------
    dict
        n_total      参与检验的因子数
        n_naive      裸 p < naive_alpha 的个数（未校正,易高估）
        n_fdr        BH 校正后判为显著的个数
    """
    if table.empty:
        return {"n_total": 0, "n_naive": 0, "n_fdr": 0}
    return {
        "n_total": int(len(table)),
        "n_naive": int((table["pvalue"] < naive_alpha).sum()),
        "n_fdr": int(table["reject"].sum()),
    }
