"""阶段 1 收尾教学演示：四类因子 + 截面 rank + 多空合成的完整链路。

与第一个 demo（demo_phase1_cross_sectional.py，只讲"截面 rank 概念"）不同，
本脚本**调用阶段 1 真正的生产模块**：
    features.cross_sectional  —— 动量 / 已实现波动率 / 成交量变化
    features.carry            —— funding carry
    features.orderflow        —— CVD / Trade Delta（仅二次确认）
    features.preprocess       —— 截面 rank 标准化

跑法（仓库根目录）：
    .venv/Scripts/python.exe notebooks/demo_phase1_all_factors.py

数据是虚构但结构清晰的小样本（5 个币、50 天），目的是让你"看见"每个因子在
截面上长什么样、rank 之后变成什么、多个因子怎么合成多空。最后做阶段 1 的性能验收。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from loguru import logger  # noqa: E402

logger.remove()  # 教学脚本：静音日志

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from features.carry import funding_carry, to_daily_funding  # noqa: E402
from features.cross_sectional import (  # noqa: E402
    momentum,
    realized_volatility,
    volume_change,
)
from features.orderflow import cvd, daily_trade_delta  # noqa: E402
from features.preprocess import RankConfig, cross_sectional_rank  # noqa: E402

pd.set_option("display.float_format", lambda x: f"{x:8.4f}")
COINS = ["BTC", "ETH", "SOL", "DOGE", "XRP"]
LINE = "=" * 76


def section(title: str) -> None:
    print(f"\n{LINE}\n{title}\n{LINE}")


# ====================================================================== 造数据


def make_dataset(n_days: int = 50, seed: int = 7):
    """造一个结构清晰的小宇宙：close / volume / 8h funding / 一段成交流。

    刻意埋入：
      - SOL/ETH 相对强（动量高）；XRP/DOGE 相对弱
      - DOGE 波动最大（已实现波动率高）
      - ETH 近期放量（成交量 z-score 高）
      - SOL funding 持续偏高（拥挤多头，carry 高）；XRP funding 偏负
    """
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2024-01-01", periods=n_days, freq="D", tz="UTC")

    market = rng.normal(0.005, 0.02, n_days)        # 共同的大盘日收益
    rel = {"BTC": 0.000, "ETH": 0.004, "SOL": 0.006, "DOGE": -0.003, "XRP": -0.005}
    vol_scale = {"BTC": 1.0, "ETH": 1.1, "SOL": 1.2, "DOGE": 2.2, "XRP": 1.3}

    close = {}
    for c in COINS:
        idio = rng.normal(0, 0.02 * vol_scale[c], n_days)  # 各币特异波动（DOGE 最大）
        daily_ret = market + rel[c] + idio
        close[c] = 100 * np.cumprod(1 + daily_ret)
    close_df = pd.DataFrame(close, index=dates)

    # 成交量：基线 + 噪声；ETH 最近 5 天放量
    volume = {c: rng.uniform(800, 1200, n_days) for c in COINS}
    volume["ETH"][-5:] *= 3.0
    volume_df = pd.DataFrame(volume, index=dates)

    # 8h funding（一天 3 次）：SOL 持续高、XRP 偏负、其余近 0
    f_idx = pd.date_range("2024-01-01", periods=n_days * 3, freq="8h", tz="UTC")
    base = {"BTC": 0.00005, "ETH": 0.0001, "SOL": 0.0004, "DOGE": 0.00008, "XRP": -0.0002}
    funding = {
        c: base[c] + rng.normal(0, 0.00003, len(f_idx)) for c in COINS
    }
    funding_df = pd.DataFrame(funding, index=f_idx)

    # 一段录制的逐笔成交（仅 orderflow 演示用，单标的 SOL 某天）
    t_idx = pd.date_range("2024-02-18 00:00", periods=12, freq="2h", tz="UTC")
    trades = pd.DataFrame(
        {
            "side": ["buy", "buy", "sell", "buy", "buy", "sell",
                     "buy", "sell", "buy", "buy", "sell", "buy"],
            "amount": [3, 2, 1, 4, 2, 1, 5, 2, 3, 1, 1, 4.0],
        },
        index=t_idx,
    )
    return close_df, volume_df, funding_df, trades


def show_factor(name: str, raw: pd.DataFrame, ascending: bool = True) -> None:
    """打印某因子最后一天的【原始值 vs 截面 rank】对照。"""
    last = raw.index[-1]
    rank = cross_sectional_rank(raw, RankConfig(ascending=ascending))
    tbl = pd.DataFrame({"原始值": raw.loc[last], "截面rank": rank.loc[last]})
    tbl = tbl.sort_values("截面rank", ascending=False)
    print(f"\n[{name}]  {last.date()} 当日截面（按 rank 从高到低）：")
    print(tbl)


# ====================================================================== 主流程


def main() -> None:
    close, volume, funding8h, trades = make_dataset()
    W = 14  # 演示用窗口（候选集 {7,14,30,60,90} 之一；真正的 N 留待阶段 5 选）

    section("阶段 1 全因子演示｜数据：5 个币、50 天（虚构，结构清晰）")
    print("最近 3 天收盘价：")
    print(close.tail(3))
    print("\n四类历史可得因子（窗口 N=14 演示）：动量 / 已实现波动率 / 成交量变化 / funding carry")
    print("订单流（CVD/Delta）单独演示，且标注为'仅二次确认、非历史可回测'。")

    # --- 1) 动量：值越大越强，rank 升序（高动量→高 rank）
    section("因子 1｜截面动量 momentum(close, 14)：过去 14 天累计收益")
    mom = momentum(close, W)
    show_factor("动量", mom, ascending=True)
    print("\n  读法：rank=1.0 是当日最强势的币。埋设里 SOL/ETH 偏强，应排在前面。")

    # --- 2) 已实现波动率
    section("因子 2｜已实现波动率 realized_volatility(close, 14)：14 天日对数收益标准差")
    vol = realized_volatility(close, W)
    show_factor("波动率", vol, ascending=True)
    print("\n  读法：rank=1.0 是当日波动最大的币（埋设里 DOGE 最高）。")
    print("  注意：因子本身不含方向。是'做空高波/做多低波'(低波异象)还是反过来，")
    print("        要等阶段 2 用 IC 验证，阶段 4 才定方向——这里只是把波动率排了个名。")

    # --- 3) 成交量变化
    section("因子 3｜成交量变化 volume_change(volume, 14)：成交量对自身历史的滚动 z-score")
    volc = volume_change(volume, W)
    show_factor("成交量z", volc, ascending=True)
    print("\n  读法：rank=1.0 是相对自身常态放量最猛的币（埋设里 ETH 近期放量）。")

    # --- 4) funding carry（先 8h→日度，再滚动）
    section("因子 4｜funding carry：8h funding → 日度(求和) → 滚动均值(7天)")
    daily_f = to_daily_funding(funding8h)            # 8h(每天3次) → 日度
    carry = funding_carry(daily_f, window=7)
    print("8h funding 最近 6 条（一天 3 次）：")
    print(funding8h.tail(6))
    print("\n聚合成日度后最近 3 天：")
    print(daily_f.tail(3))
    show_factor("carry", carry, ascending=True)
    print("\n  读法：rank=1.0 是 funding 最高的币（埋设里 SOL 拥挤多头）。")
    print("  假设：funding 极高=拥挤多头=有均值回归倾向（待阶段 2 用 IC 验证，非先验）。")

    # --- 5) 订单流（确认项）
    section("因子 5｜订单流 CVD / Trade Delta  ⚠️ 仅二次确认、非历史可回测")
    print("一段录制的 SOL 逐笔成交（共 12 笔）→ CVD（累计带符号成交量）：")
    c = cvd(trades)
    print(pd.DataFrame({"side": trades["side"], "amount": trades["amount"], "CVD": c}).tail(6))
    print(f"\n当日净主动买量 (Trade Delta) = {daily_trade_delta(trades).iloc[0]:.1f}")
    print("\n  ⚠️ 逐笔历史量大成本高，审计已降级为'从现在起录制、仅供未来 paper trading'。")
    print("     它不是 alpha 主力，只在入场时做二次确认，且严禁用于历史回测或声称历史 edge。")

    # --- 6) 多因子合成 → 多空（演示机制，方向未经验证）
    section("综合｜多个 rank 合成打分 → 多空选股（⚠️ 方向待阶段 2/4 验证，此处仅演示机制）")
    last = close.index[-1]
    # 用 center=True 让每个 rank 居中（行和≈0），等权平均成一个合成分
    mom_c = cross_sectional_rank(mom, RankConfig(center=True)).loc[last]
    volc_c = cross_sectional_rank(volc, RankConfig(center=True)).loc[last]
    score = (mom_c + volc_c) / 2  # 朴素等权（仅演示；真权重/方向出自阶段 2/4）
    score = score.sort_values(ascending=False)
    print(f"{last.date()} 合成打分（动量 + 成交量，等权、居中）从高到低：")
    print(score)
    longs, shorts = score.index[:2].tolist(), score.index[-2:].tolist()
    print(f"\n  做多(强) → {longs}    做空(弱) → {shorts}")
    print("  两腿等额=美元中性（决策 7）；做多/做空各分层等权（决策 6）。")
    print("  ⚠️ 这里把'动量高、放量'当强势只是演示合成【机制】；每个因子到底该正着用")
    print("     还是反着用、各占多少权重，必须由阶段 2 的 IC 和阶段 4 的策略来定。")

    # --- 7) 阶段 1 性能验收
    section("阶段 1 验收｜性能：池内全标的单日因子计算 < 5 秒")
    big_days, big_n = 365, 30
    bidx = pd.date_range("2023-01-01", periods=big_days, freq="D", tz="UTC")
    rng = np.random.default_rng(0)
    big_close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.03, (big_days, big_n)), axis=0),
        index=bidx, columns=[f"C{i}" for i in range(big_n)],
    )
    big_vol = pd.DataFrame(
        rng.uniform(500, 1500, (big_days, big_n)), index=bidx, columns=big_close.columns
    )
    t0 = time.perf_counter()
    for w in (7, 14, 30, 60, 90):           # 全候选窗口、全因子，整段面板
        momentum(big_close, w)
        realized_volatility(big_close, w)
        volume_change(big_vol, w)
    elapsed = time.perf_counter() - t0
    per_day = elapsed / big_days
    print(f"  {big_n} 标的 × {big_days} 天 × 5 窗口 × 3 因子：全量 {elapsed*1000:.1f} ms")
    print(f"  折算单日：{per_day*1000:.3f} ms  →  验收 < 5 秒：{'通过 ✅' if per_day < 5 else '不通过 ❌'}")

    section("小结｜阶段 1 做了什么、为什么")
    print("  1. 造了 4 类历史可得的【原始因子】+ 1 类确认项（orderflow）。")
    print("  2. 因子本身不含方向、不含权重——阶段 1 只负责'干净地造料'。")
    print("  3. 截面 rank 把'市场 beta'剥掉，只留'相对强弱'（决策 5）。")
    print("  4. 多空合成、各因子方向与权重 → 阶段 4；'到底有没有 edge' → 阶段 2 用 IC 验。")
    print("  5. 窗口 N 全程参数化，绝不在全量数据上挑最优（留给阶段 5 Walk-Forward）。")


if __name__ == "__main__":
    main()
