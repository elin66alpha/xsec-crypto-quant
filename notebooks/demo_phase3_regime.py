"""阶段 3 教学演示：市场 Regime + 相关性飙升 → 自动降杠杆。

调用阶段 3 真模块（market_regime / correlation_spike / risk_params）,在一段
**埋了'平静→危机→平静'结构**的虚构市场上展示风控铁律 2 如何落地：

    市场特征(波动/横截面相关中位数) → HMM 三状态 + 相关飙升检测 → 杠杆乘子 → 红线裁剪

埋设：中段 60 天是危机（波动放大数倍 + 各币齐跌 → 相关趋近 1）。看流水线能否
自动把危机段的合计杠杆从 2x 压到 0.4x,以及'诚实(扩展窗口,无前视)'与'研究(全样本,
含前视)'两种标注的差别。

跑法（仓库根目录）：
    .venv/Scripts/python.exe notebooks/demo_phase3_regime.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import logging  # noqa: E402

from loguru import logger  # noqa: E402

logger.remove()
logging.getLogger("hmmlearn").setLevel(logging.ERROR)  # 静音 HMM 收敛提示

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from regime.correlation_spike import detect_correlation_spike  # noqa: E402
from regime.market_regime import (  # noqa: E402
    REGIME_CRISIS,
    compute_market_features,
    label_regimes_expanding,
    label_regimes_full,
)
from regime.risk_params import apply_regime_leverage  # noqa: E402

pd.set_option("display.float_format", lambda x: f"{x:8.4f}")
pd.set_option("display.width", 130)
LINE = "=" * 80
REGIME_NAME = {0: "低波", 1: "趋势", 2: "危机", -1: "预热"}


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def make_market(seed: int = 7):
    """平静(150) → 危机(60) → 平静(150)，8 个币。

    平静段：共同成分小、各币特异成分大 → **横截面相关低**、波动低（正常市场）。
    危机段：共同成分大且齐跌、特异成分小 → **相关趋近 1**、波动高（相关性危机）。
    """
    rng = np.random.default_rng(seed)
    n_coins = 8
    # (天数, 共同成分波动, 共同成分漂移, 各币特异波动)
    blocks = [
        (150, 0.006, 0.0005, 0.020),   # 平静：特异主导 → 低相关
        (60, 0.050, -0.012, 0.006),    # 危机：共同主导、齐跌 → 高相关、高波动
        (150, 0.006, 0.0005, 0.020),   # 平静
    ]
    rows = []
    for days, common_sig, drift, idio_sig in blocks:
        common = rng.normal(drift, common_sig, days)        # 共同(市场)成分
        idio = rng.normal(0, idio_sig, (days, n_coins))     # 各币特异成分
        rows.append(common[:, None] + idio)
    ret = np.vstack(rows)
    n = ret.shape[0]
    dates = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    close = pd.DataFrame(
        100 * np.cumprod(1 + ret, axis=0), index=dates, columns=[f"C{i}" for i in range(n_coins)]
    )
    return close, (150, 210)  # 危机段 [150,210)


def main() -> None:
    close, (c0, c1) = make_market()
    crisis_start, crisis_end = close.index[c0], close.index[c1]

    section("阶段 3 全流水线｜8 币 × 360 天（埋设：中段 60 天为危机）")
    print("市场特征 → HMM 三状态 + 相关飙升 → 杠杆乘子 → 红线裁剪(单边≤1x/合计≤2x)")

    # --- 1) 市场特征 ---
    section("第 1 步｜市场层面特征")
    feats = compute_market_features(close, vol_window=14, corr_window=20)
    print("危机前后采样（注意危机段 market_vol 与 xs_corr_median 双双抬升）：")
    sample = feats.iloc[[140, 155, 175, 195, 215, 240]]
    print(sample)

    # --- 2) HMM regime（诚实 vs 研究）---
    section("第 2 步｜HMM 三状态：诚实(扩展窗口,无前视) vs 研究(全样本,含前视)")
    reg_full = label_regimes_full(feats, n_states=3)
    reg_exp = label_regimes_expanding(feats, n_states=3, min_train=120, refit_every=20)

    def _crisis(idx: pd.Index) -> np.ndarray:  # 按各序列自身索引取危机段(序列长度可能不同)
        return (idx >= crisis_start) & (idx < crisis_end)

    crisis_mask = _crisis(feats.index)  # 用于长度=360 的 feats/spike/gross
    full_hit = (reg_full[_crisis(reg_full.index)] == REGIME_CRISIS).mean()
    exp_valid = reg_exp[_crisis(reg_exp.index)].dropna()
    exp_hit = (exp_valid == REGIME_CRISIS).mean() if len(exp_valid) else float("nan")
    print(f"危机段被标为'危机'的比例：全样本(研究)={full_hit:.0%}　扩展窗口(诚实)={exp_hit:.0%}")
    print("  全样本拟合含前视,只能用于事后研究可视化;回测信号必须用扩展窗口那条(无前视)。")

    # --- 3) 相关性飙升（铁律 2）---
    section("第 3 步｜相关性飙升检测（风控铁律 2：更快的一道开关）")
    spike_df = detect_correlation_spike(close, corr_window=20)
    n_spike_crisis = int(spike_df.loc[crisis_mask, "corr_spike"].sum())
    n_spike_calm = int(spike_df.loc[~crisis_mask, "corr_spike"].sum())
    print(f"相关飙升触发天数：危机段 {n_spike_crisis} 天　非危机段 {n_spike_calm} 天")
    print("  危机段各币齐跌、相关趋近 1 → 飙升信号密集亮起,即使 HMM 稍有滞后也能兜底。")

    # --- 4) 合成 → 杠杆乘子 → 红线裁剪 ---
    section("第 4 步｜regime + 飙升 → 合计杠杆（目标 2x,危机自动压到 0.4x,且不破红线）")
    base_gross = 2.0
    gross = pd.Series(
        [
            apply_regime_leverage(base_gross, reg_exp.get(d, float("nan")),
                                  corr_spike=bool(spike_df.loc[d, "corr_spike"]))
            for d in close.index
        ],
        index=close.index, name="gross_leverage",
    )
    calm_after = (close.index >= close.index[c1 + 25])  # 危机+滞后之后的平静段
    print(f"目标合计杠杆 base_gross = {base_gross}x")
    print(f"  危机段平均合计杠杆 = {gross[crisis_mask].mean():.3f}x  ← 被自动压低")
    print(f"  危机后平静段平均   = {gross[calm_after].mean():.3f}x")
    print(f"  全程最大合计杠杆   = {gross.max():.3f}x  （红线 2.0x,never 超过 = {gross.max() <= 2.0}）")

    # --- 5) 时间线采样 ---
    section("第 5 步｜时间线采样：危机如何触发降杠杆")
    tl = pd.DataFrame({
        "market_vol": feats["market_vol"],
        "xs_corr_med": feats["xs_corr_median"],
        "regime": reg_exp.map(lambda v: REGIME_NAME.get(int(v) if pd.notna(v) else -1, "?")),
        "spike": spike_df["corr_spike"],
        "gross_lev": gross,
    })
    print(tl.iloc[[140, 152, 160, 180, 205, 215, 245]].to_string())

    # --- 6) 诚实判读 ---
    section("第 6 步｜诚实判读")
    print("  • 危机段波动+相关双升 → HMM 标危机 + 相关飙升,两道防线齐触发。")
    print("  • 合计杠杆在危机段被自动压到约 0.4x,任何时点都不破 2x 红线（决策 8 写死）。")
    print("  • '诚实(扩展窗口)'与'研究(全样本)'分得很清：回测只能用前者,杜绝事后贴标签。")
    print("  • 相关飙升是比 HMM 更快的兜底开关：不赌'对冲仍在保护',直接降杠杆（铁律 2）。")
    print("  ⚠️ 本 demo 用虚构数据,只为验证风控流水线;真实阈值(abs/z、min_train 等)需在")
    print("     真实数据上看分布后再定并记录原因。")


if __name__ == "__main__":
    main()
