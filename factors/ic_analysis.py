"""横截面 IC 分析（阶段 2 地基）。

阶段 2 的目标：验证因子的**横截面预测力**——"今天的因子排名,能不能预测未来一段时间
里各标的的相对收益"。核心工具是 **IC（Information Coefficient,信息系数）**：

    IC(t) = 当日因子值 与 未来 n 天前向收益 在【截面上】的相关系数（默认 Spearman 秩相关）

把每个时点的 IC 串起来得到一条 IC 时间序列,再看：

    mean(IC)  —— 平均预测方向与强度（正=因子大者未来收益高）
    ICIR      —— IC 的稳定性 = mean(IC) / std(IC)，比单个 IC 数字更可信
    t 统计量  —— = ICIR × sqrt(N)，用于判断 mean(IC) 是否显著异于 0

> 概念（首次出现）：**IC** = 因子打分与未来收益的截面相关性，衡量"排得准不准";
> **ICIR** = IC 的稳定性（均值/标准差），衡量"每期都稳不稳"。两者都高才算好因子。

预测目标 horizon：1D / 3D / 5D / 10D（日级,见 CLAUDE.md 阶段 2）。

前视说明（决策 9）：IC 分析里"前向收益"用未来数据是**正确的**——我们就是要看因子能否
预测未来,这不是 look-ahead 泄漏（因子只用了 t 及以前的信息）。真实交易的"下一根 bar
开盘成交"执行时滞,留到阶段 5 回测里落实,不影响这里 IC 的有效性。

纯逻辑,仅依赖 pandas/numpy/scipy,可离线单测。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 计算截面相关所需的最少有效标的对数（少于此当日 IC 记为 NaN）。
# 数据卫生阈值,非 alpha 参数；动态池 Top N 远大于它。
DEFAULT_MIN_PAIRS = 5


def forward_return(close: pd.DataFrame, horizon: int) -> pd.DataFrame:
    """前向收益：标的在未来 ``horizon`` 天的收盘到收盘收益,**对齐在 t 上**。

    forward_return(t) = close(t + horizon) / close(t) - 1。末尾 ``horizon`` 行因无未来
    数据为 NaN。它将与"在 t 已知的因子值"配对算 IC——因子只用 t 及以前信息,故无泄漏。

    Parameters
    ----------
    close : DataFrame
        index = 日期（UTC 升序）,columns = 标的,values = 收盘价。
    horizon : int
        前瞻天数（>=1）。

    Returns
    -------
    DataFrame
        同形状的前向收益,对齐在 t。
    """
    if horizon < 1:
        raise ValueError(f"horizon 必须 >= 1,收到 {horizon}")
    return close.shift(-horizon) / close - 1.0


def cross_sectional_ic(
    factor: pd.DataFrame,
    forward_returns: pd.DataFrame,
    method: str = "spearman",
    min_pairs: int = DEFAULT_MIN_PAIRS,
) -> pd.Series:
    """逐日截面 IC：每个时点因子值与前向收益在标的维度上的相关系数。

    默认 Spearman 秩相关（抗极值,且因子常已是 rank,秩相关最自然）。

    Parameters
    ----------
    factor : DataFrame
        因子面板（原始值或截面 rank 均可,Spearman 下等价）。
    forward_returns : DataFrame
        ``forward_return`` 的输出,形状/索引应与 ``factor`` 对应。
    method : {"spearman", "pearson", "kendall"}
        相关系数类型,默认 spearman。
    min_pairs : int
        当日有效标的对数下限,不足则该日 IC 记为 NaN。

    Returns
    -------
    Series
        index = 日期,values = 当日截面 IC,name = ``IC_<method>``。
    """
    f, r = factor.align(forward_returns, join="inner")
    ic_values: dict[pd.Timestamp, float] = {}
    for date in f.index:
        a = f.loc[date]
        b = r.loc[date]
        mask = a.notna() & b.notna()
        if int(mask.sum()) < min_pairs:
            ic_values[date] = np.nan
            continue
        ic_values[date] = float(a[mask].corr(b[mask], method=method))
    return pd.Series(ic_values, name=f"IC_{method}")


def ic_summary(ic: pd.Series) -> dict[str, float]:
    """汇总一条 IC 序列：均值、标准差、ICIR、胜率、t 统计量、有效期数。

    - ``icir`` = mean(IC) / std(IC)（std 用样本标准差 ddof=1）
    - ``hit_rate`` = IC > 0 的比例（方向稳定性的直观补充）
    - ``t_stat`` = ICIR × sqrt(N)，检验 mean(IC) 是否显著异于 0
    """
    valid = ic.dropna()
    n = int(len(valid))
    if n == 0:
        return {"mean_ic": np.nan, "std_ic": np.nan, "icir": np.nan,
                "hit_rate": np.nan, "t_stat": np.nan, "n_obs": 0}
    mean_ic = float(valid.mean())
    std_ic = float(valid.std(ddof=1)) if n > 1 else np.nan
    icir = mean_ic / std_ic if std_ic and np.isfinite(std_ic) and std_ic != 0 else np.nan
    hit_rate = float((valid > 0).mean())
    t_stat = icir * np.sqrt(n) if np.isfinite(icir) else np.nan
    return {
        "mean_ic": mean_ic,
        "std_ic": std_ic,
        "icir": icir,
        "hit_rate": hit_rate,
        "t_stat": t_stat,
        "n_obs": n,
    }
