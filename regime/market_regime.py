"""市场层面 Regime（阶段 3）。

定位（CLAUDE.md 阶段 3）：识别**全市场状态**,用于相关性危机时**整体降杠杆**——
这是市场层面的风控开关,**不是给单个币定状态**。三状态：低波动 / 趋势 / 高波动危机。

两层设计（沿用本仓库"纯逻辑核心 + 懒加载重依赖"的惯例,见 data/universe.py）：
    1. 纯逻辑特征层（``compute_market_features`` 及其辅助）——只依赖 pandas/numpy,
       可在无 hmmlearn 时离线单测。
    2. HMM 层（``fit_hmm`` / ``label_regimes_*``）——懒加载 hmmlearn。

无前视铁律（CLAUDE.md：严禁 look-ahead）：
    - ``label_regimes_expanding`` 是**诚实默认**：扩展窗口,只用"截至当时"的数据拟合,
      状态的波动率排序映射也只用训练段——可安全用于回测信号。
    - ``label_regimes_full`` 在全样本上拟合,**含前视**,仅供研究可视化,
      **严禁用于回测信号**（函数名与文档反复警示）。

> 概念（首次出现）：**HMM（隐马尔可夫模型）**用"隐藏状态 + 状态间转移 + 各状态下观测
> 分布"来刻画时间序列;这里用它把市场特征聚成 3 个状态。HMM 给出的状态编号是任意的,
> 需按"平均波动率"重排成 低波(0)/趋势(1)/危机(2)。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# 三状态语义（按波动率升序重排后的标签）
DEFAULT_N_STATES = 3
REGIME_LOW_VOL = 0     # 低波动（平静）
REGIME_TREND = 1       # 趋势（中波动）
REGIME_CRISIS = 2      # 高波动危机 → 触发降杠杆

DEFAULT_VOL_WINDOW = 14
DEFAULT_CORR_WINDOW = 30


# ======================================================================
# 纯逻辑特征层（无 hmmlearn,可离线单测）
# ======================================================================


def equal_weight_pool_return(close: pd.DataFrame) -> pd.Series:
    """等权池日收益 = 各标的日收益的横截面均值（市场层面代理）。"""
    return close.pct_change().mean(axis=1)


def realized_vol(returns: pd.Series, window: int = DEFAULT_VOL_WINDOW) -> pd.Series:
    """滚动已实现波动率（样本标准差,需满 window 个有效收益）。"""
    return returns.rolling(window=window, min_periods=window).std()


def cross_sectional_corr_median(
    close: pd.DataFrame, window: int = DEFAULT_CORR_WINDOW
) -> pd.Series:
    """滚动**横截面相关性中位数**：相关性危机的核心指标。

    每个时点对过去 ``window`` 天的日对数收益算标的两两相关矩阵,取上三角（非对角）中位数。
    加密危机时各币齐跌、相关趋近 1,该指标飙升 → 多空对冲失效（风控铁律 2）。
    """
    log_ret = np.log(close).diff()
    idx = close.index
    out: dict[pd.Timestamp, float] = {}
    for i in range(len(idx)):
        if i + 1 < window:
            out[idx[i]] = np.nan
            continue
        win = log_ret.iloc[i + 1 - window : i + 1]
        corr = win.corr()  # 标的×标的，窗口内时序相关
        vals = corr.to_numpy()[np.triu_indices(corr.shape[0], k=1)]
        vals = vals[np.isfinite(vals)]
        out[idx[i]] = float(np.median(vals)) if vals.size else np.nan
    return pd.Series(out, name="xs_corr_median")


def compute_market_features(
    close: pd.DataFrame,
    vol_window: int = DEFAULT_VOL_WINDOW,
    corr_window: int = DEFAULT_CORR_WINDOW,
) -> pd.DataFrame:
    """从池内 OHLCV 收盘价构造市场层面特征面板（HMM 的输入）。

    列：
        market_vol      —— 等权池收益的已实现波动率（总市场波动）
        xs_corr_median  —— 横截面相关性中位数（相关危机指标）
        mean_abs_return —— 各标的日收益绝对值的横截面均值（普遍波动幅度）

    说明：CLAUDE.md 还提到 BTC 主导率变化,但其需市值数据,本阶段先用 OHLCV 可得的
    三个特征,BTC 主导率留待接入市值快照后补（记录在案,不阻塞阶段 3）。

    无前视：每个特征都用 rolling/diff 向过去取窗口,某日特征不依赖未来。
    """
    pool_ret = equal_weight_pool_return(close)
    daily_ret = close.pct_change()
    feats = pd.DataFrame(
        {
            "market_vol": realized_vol(pool_ret, vol_window),
            "xs_corr_median": cross_sectional_corr_median(close, corr_window),
            "mean_abs_return": daily_ret.abs().mean(axis=1),
        }
    )
    return feats


# ======================================================================
# HMM 层（懒加载 hmmlearn）
# ======================================================================


def fit_hmm(
    features: pd.DataFrame,
    n_states: int = DEFAULT_N_STATES,
    random_state: int = 42,
    n_iter: int = 200,
):
    """在给定特征上拟合 Gaussian HMM（懒加载 hmmlearn）。

    调用方负责保证 ``features`` 只含"允许使用"的时间段（决策 9）：
    全样本拟合含前视,只可用于研究;回测请用 ``label_regimes_expanding``。
    """
    from hmmlearn.hmm import GaussianHMM  # 懒加载：纯逻辑路径无需 hmmlearn

    X = features.dropna().to_numpy()
    model = GaussianHMM(
        n_components=n_states,
        covariance_type="diag",  # diag 对小样本更稳健
        n_iter=n_iter,
        random_state=random_state,
    )
    model.fit(X)
    return model


def _vol_order_mapping(
    raw_states: np.ndarray, vol_values: np.ndarray, n_states: int
) -> dict[int, int]:
    """按各状态的平均波动率升序,把原始 HMM 状态号映射到 低波(0)…危机(n-1)。"""
    means = {}
    for s in range(n_states):
        mask = raw_states == s
        means[s] = float(np.nanmean(vol_values[mask])) if mask.any() else np.inf
    order = sorted(means, key=lambda s: means[s])
    return {old: new for new, old in enumerate(order)}


def label_regimes_full(
    features: pd.DataFrame,
    n_states: int = DEFAULT_N_STATES,
    vol_col: str = "market_vol",
    random_state: int = 42,
) -> pd.Series:
    """⚠️ 全样本拟合 + 标注（**含前视,仅研究可视化,严禁回测用**）。

    返回按波动率重排后的 regime 序列（0=低波 … n-1=危机）,index 对齐有效特征行。
    """
    X = features.dropna()
    model = fit_hmm(X, n_states=n_states, random_state=random_state)
    raw = model.predict(X.to_numpy())
    mapping = _vol_order_mapping(raw, X[vol_col].to_numpy(), n_states)
    return pd.Series([mapping[s] for s in raw], index=X.index, name="regime")


def label_regimes_expanding(
    features: pd.DataFrame,
    n_states: int = DEFAULT_N_STATES,
    vol_col: str = "market_vol",
    min_train: int = 252,
    refit_every: int = 21,
    random_state: int = 42,
) -> pd.Series:
    """诚实默认：扩展窗口重拟合 → 无前视的 regime 标注（可用于回测）。

    流程：前 ``min_train`` 行无模型 → NaN;此后每 ``refit_every`` 步,用"截至该块起点"的
    历史重拟合 HMM,并**仅用训练段**确定波动率排序映射。预测时把截至块尾的历史
    ``X.iloc[:end]`` 一起送入 Viterbi,再只取当前 ``[start:end)`` 块的标签,避免块首
    状态缺少历史条件。fitting 与排序映射都不使用未来数据。

    注：块内用 Viterbi,块内标签依赖该块观测;若要严格逐点因果,把 ``refit_every`` 设为 1
    （更慢）。本项目主要防的是"全序列拟合后回用状态",扩展窗口已杜绝该前视。
    """
    X = features.dropna()
    labels = pd.Series(np.nan, index=X.index, name="regime")
    n = len(X)
    start = min_train
    while start < n:
        end = min(start + refit_every, n)
        train = X.iloc[:start]
        model = fit_hmm(train, n_states=n_states, random_state=random_state)
        train_raw = model.predict(train.to_numpy())
        mapping = _vol_order_mapping(train_raw, train[vol_col].to_numpy(), n_states)
        full_raw = model.predict(X.iloc[:end].to_numpy())
        block_raw = full_raw[start:end]
        labels.iloc[start:end] = [float(mapping.get(s, np.nan)) for s in block_raw]
        start = end
    return labels
