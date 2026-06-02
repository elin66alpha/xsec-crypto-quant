"""阶段 1 教学演示：横截面因子工程到底在干什么。

这不是生产模块，是给学习者"看见"机制的脚本。它用一小撮（虚构但贴近真实的）
币价，一步步展示：

    原始价格 → 计算一个因子（动量）→ 截面 rank 标准化 → 多空选股 → 为什么市场中性

跑法（仓库根目录）：
    .venv/Scripts/python.exe notebooks/demo_phase1_cross_sectional.py

它只依赖 pandas/numpy 和我们阶段 1 写的 features/preprocess.py。
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Windows 终端默认 cp1252，无法打印中文；强制用 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# 教学脚本：关掉 loguru 的调试日志，让输出干净
from loguru import logger  # noqa: E402

logger.remove()

# 让脚本能从仓库根 import features 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features.preprocess import cross_sectional_rank  # noqa: E402

pd.set_option("display.float_format", lambda x: f"{x:7.3f}")
LINE = "=" * 72


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


def make_prices() -> pd.DataFrame:
    """虚构 5 个币、8 天的日收盘价。

    每天的收益 = 一个**共同的市场涨跌** + 每个币各自的**相对强弱**。
    SOL/ETH 相对偏强，XRP/DOGE 相对偏弱，BTC 居中——刻意造出清晰的截面差异。
    """
    coins = ["BTC", "ETH", "SOL", "DOGE", "XRP"]
    dates = pd.date_range("2024-01-01", periods=8, freq="D", tz="UTC")

    # 共同的市场日收益（所有币当天一起涨/跌的部分 = 市场 beta）
    market = np.array([0.05, -0.03, 0.02, 0.04, -0.06, 0.03, 0.01, 0.02])
    # 每个币每天叠加的"相对强弱"（这才是因子想捕捉的 alpha 来源）
    rel = {"BTC": 0.00, "ETH": 0.01, "SOL": 0.02, "DOGE": -0.01, "XRP": -0.02}

    prices = {}
    for c in coins:
        daily_ret = market + rel[c]            # 市场 + 该币相对强弱
        prices[c] = 100 * np.cumprod(1 + daily_ret)  # 由收益累乘成价格
    return pd.DataFrame(prices, index=dates)


def momentum(prices: pd.DataFrame, window: int) -> pd.DataFrame:
    """最朴素的动量因子：过去 window 天的累计收益。

    （阶段 1 的 features/cross_sectional.py 会把它做成正式模块，这里先内联演示。）
    """
    return prices / prices.shift(window) - 1.0


def main() -> None:
    prices = make_prices()

    section("第 1 步｜原始数据：5 个币、8 天的收盘价")
    print(prices)
    print("\n直觉：每一行是某一天，每一列是一个币。注意整段时间大盘是涨的——")
    print("      所有币的价格大体一起往上走（这就是'市场 beta'，我们想剥掉它）。")

    section("第 2 步｜算一个因子：3 天动量 = 过去 3 天涨了多少")
    mom = momentum(prices, window=3)
    print(mom)
    print("\n直觉：前 3 行是 NaN（没有足够历史算 3 天动量）。")
    print("      看最后一天那一行：每个币的动量是一个**绝对数字**，而且都为正——")
    print("      因为大盘在涨。光看这些数字，分不清'谁是真强'还是'只是被大盘抬着'。")

    section("第 3 步｜截面 rank：在【同一天】把币们互相比较、排名")
    rank = cross_sectional_rank(mom)           # 默认归一到 (0,1] 百分位
    print(rank)
    print("\n直觉：'横截面(cross-sectional)' = 沿着**每一行(同一天)**横着比，")
    print("      不是沿着时间纵着看单个币。rank 把'绝对动量'变成'相对名次'：")
    print("      1.00 = 当天最强，越小越弱。大盘涨跌这个共同成分被排名抵消掉了。")

    section("第 4 步｜多空选股：做多最强、做空最弱（美元中性）")
    last_day = rank.index[-1]
    today = rank.loc[last_day].sort_values(ascending=False)
    print(f"最后一天 {last_day.date()} 的截面排名（从强到弱）：")
    print(today)
    longs = today.index[:2].tolist()           # 最强 2 个做多
    shorts = today.index[-2:].tolist()         # 最弱 2 个做空
    print(f"\n  做多(强) → {longs}")
    print(f"  做空(弱) → {shorts}")
    print("  两腿名义金额相等 = 美元中性：大盘整体涨或跌，多空互相抵消，")
    print("  组合赚的是'强的继续比弱的强'这件事，而不是赌大盘方向。")

    section("第 5 步｜关键证明：为什么要 rank —— 它把'市场涨跌'剥掉了")
    bull = prices.copy()
    # 人为制造一场"额外大牛市"：让每个币每天都多涨 10%（纯市场成分，对所有币一视同仁）
    extra = np.cumprod(np.full(len(prices), 1.10))
    bull = bull.mul(extra, axis=0)
    mom_bull = momentum(bull, window=3)
    rank_bull = cross_sectional_rank(mom_bull)

    print("把每个币每天都额外 +10%（一场假牛市，对所有币完全一样）后：")
    print("\n  原始动量(最后一天)：")
    print(mom.loc[last_day])
    print("\n  牛市动量(最后一天)：  ← 绝对数字全被推高了")
    print(mom_bull.loc[last_day])
    print("\n  但两种情形的截面 rank 完全相同：")
    same = np.allclose(
        rank.loc[last_day].values, rank_bull.loc[last_day].values, equal_nan=True
    )
    print(f"    rank 一致？ {same}")
    print("\n  这就是横截面因子工程的灵魂：原始因子里混着'市场 beta'，")
    print("  截面 rank 把它消掉，只留下'相对强弱'——这才是可能存在 alpha 的地方。")
    print("  阶段 2 会用 IC 去检验：这个'相对强弱'到底能不能预测未来的相对收益。")


if __name__ == "__main__":
    main()
