"""动态资产池构建（决策 1）。

铁律（CLAUDE.md 决策 1 / 风控铁律 4）：
    每个历史时点只能使用"当时真实可交易"的标的集合，
    严禁用未来的池子成分回测过去（幸存者偏差）。

规则：
    每月初，按"过去 LOOKBACK 天的平均美元成交额"对永续合约排名，
    取 Top N，且要求该合约在该时点已上市满 MIN_LISTING_DAYS 天。

设计：
    - 纯逻辑核心（``select_universe`` / ``build_universe_history`` / ``infer_listing_dates``）
      只依赖 pandas/numpy，**可在无网络、无 ccxt 时单元测试**。
    - ccxt 相关函数（``list_perpetual_symbols`` / ``load_dollar_volume_panel`` /
      ``build_recent_universe``）懒加载 ccxt，仅在真正取数时才需要它。

美元成交额近似见 ``docs/data_availability_audit.md`` 说明①：dollar_volume ≈ close × volume。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

import numpy as np
import pandas as pd
from loguru import logger

# --- 决策 1 的默认参数（与 .env.example 对齐；env 可覆盖，但代码不依赖 env 存在）---
DEFAULT_TOP_N = int(os.getenv("UNIVERSE_TOP_N", "30"))
DEFAULT_MIN_LISTING_DAYS = int(os.getenv("UNIVERSE_MIN_LISTING_DAYS", "90"))
DEFAULT_LOOKBACK_DAYS = int(os.getenv("UNIVERSE_VOLUME_LOOKBACK_DAYS", "30"))
# lookback 窗口内至少要有这么多个有效成交额日，才有资格参与排名（防止"上市一两天就刷量"入选）
DEFAULT_MIN_VALID_DAYS_RATIO = 0.5


@dataclass(frozen=True)
class UniverseConfig:
    """动态池参数。默认值即决策 1 的取值。"""

    top_n: int = DEFAULT_TOP_N
    min_listing_days: int = DEFAULT_MIN_LISTING_DAYS
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    min_valid_days_ratio: float = DEFAULT_MIN_VALID_DAYS_RATIO


# ======================================================================
# 纯逻辑核心（无网络依赖，单元可测）
# ======================================================================


def _align_tz(ts: pd.Timestamp, index: pd.Index) -> pd.Timestamp:
    """把 ``ts`` 的时区对齐到面板索引：索引 tz-aware 则本地化/转换，否则去 tz。

    避免 tz-aware 索引与 tz-naive 时间戳比较（pandas 3.0 会直接抛 TypeError）。
    """
    idx_tz = getattr(index, "tz", None)
    if idx_tz is not None:
        return ts.tz_localize(idx_tz) if ts.tz is None else ts.tz_convert(idx_tz)
    return ts.tz_localize(None) if ts.tz is not None else ts


def infer_listing_dates(dollar_volume: pd.DataFrame) -> pd.Series:
    """由成交额面板推断每个标的的"上市日"代理 = 第一条有效（非 NaN）数据日。

    说明②（见审计表）：这只能覆盖现存合约；退市币的无偏处理需外部上市/退市日历。

    Parameters
    ----------
    dollar_volume : DataFrame
        index = 日期（UTC, 升序），columns = 标的，values = 当日美元成交额。

    Returns
    -------
    Series
        index = 标的，value = 首个有效数据日（Timestamp）。
    """
    first_valid = dollar_volume.apply(lambda col: col.first_valid_index())
    return first_valid


def select_universe(
    dollar_volume: pd.DataFrame,
    as_of: pd.Timestamp,
    listing_dates: pd.Series | None = None,
    config: UniverseConfig | None = None,
) -> list[str]:
    """选出 ``as_of`` 时点（收盘后）真实可交易的 Top N 标的。

    无前视（决策 9）：只用 ``as_of`` 当日及之前的成交额；选出的池子在**下一根 bar 起**生效。

    Parameters
    ----------
    dollar_volume : DataFrame
        index = 日期（UTC），columns = 标的，values = 当日美元成交额。
    as_of : Timestamp
        判定时点（含当日）。
    listing_dates : Series, optional
        每个标的的上市日；缺省则由 ``dollar_volume`` 推断。
    config : UniverseConfig, optional

    Returns
    -------
    list[str]
        入选标的，按平均成交额降序。
    """
    config = config or UniverseConfig()
    as_of = _align_tz(pd.Timestamp(as_of), dollar_volume.index)
    if listing_dates is None:
        listing_dates = infer_listing_dates(dollar_volume)

    # 1) lookback 窗口：(as_of - lookback, as_of]，严格不含未来
    window_start = as_of - timedelta(days=config.lookback_days)
    window = dollar_volume.loc[
        (dollar_volume.index > window_start) & (dollar_volume.index <= as_of)
    ]
    if window.empty:
        logger.warning("select_universe: 窗口内无数据 (as_of={})", as_of.date())
        return []

    min_valid_days = max(1, int(round(config.lookback_days * config.min_valid_days_ratio)))

    candidates: dict[str, float] = {}
    for symbol in dollar_volume.columns:
        # 2) 上市满 min_listing_days
        listed = listing_dates.get(symbol)
        if pd.isna(listed):
            continue
        if (as_of - pd.Timestamp(listed)).days < config.min_listing_days:
            continue
        # 3) 窗口内有足够有效成交额天数
        col = window[symbol].dropna()
        if len(col) < min_valid_days:
            continue
        avg_vol = float(col.mean())
        if not np.isfinite(avg_vol) or avg_vol <= 0:
            continue
        candidates[symbol] = avg_vol

    # 4) 按平均成交额降序取 Top N
    ranked = sorted(candidates.items(), key=lambda kv: kv[1], reverse=True)
    return [sym for sym, _ in ranked[: config.top_n]]


def month_start_dates(start: pd.Timestamp, end: pd.Timestamp) -> list[pd.Timestamp]:
    """生成 [start, end] 区间内每个自然月的月初（UTC）。"""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    return list(pd.date_range(start=start.normalize(), end=end, freq="MS"))


def build_universe_history(
    dollar_volume: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
    listing_dates: pd.Series | None = None,
    config: UniverseConfig | None = None,
) -> dict[pd.Timestamp, list[str]]:
    """逐月构建动态池：返回 {月初日期 -> 当月可交易成分列表}。

    每个月初用"截至该日"的成交额重新选池；该池在当月内有效（决策 4 的月度池再平衡，
    与日频持仓再平衡是两个层面：池是月度的，持仓是日度的）。
    """
    config = config or UniverseConfig()
    if listing_dates is None:
        listing_dates = infer_listing_dates(dollar_volume)

    history: dict[pd.Timestamp, list[str]] = {}
    for month_start in month_start_dates(start, end):
        members = select_universe(dollar_volume, month_start, listing_dates, config)
        history[month_start] = members
        logger.debug("universe {} -> {} 个标的", month_start.date(), len(members))
    return history


# ======================================================================
# ccxt 取数封装（懒加载 ccxt；fetcher.py 是更健壮的分页版本）
# ======================================================================


def _make_okx_perp(exchange=None):
    """返回一个配置好的 OKX 永续 ccxt 实例（懒加载 ccxt）。"""
    if exchange is not None:
        return exchange
    import ccxt  # 懒加载：纯逻辑路径无需安装 ccxt

    return ccxt.okx({"enableRateLimit": True})


def list_perpetual_symbols(exchange=None, quote: str = "USDT") -> list[str]:
    """列出当前在交易的 USDT 本位永续合约 symbol（ccxt 统一格式，如 ``BTC/USDT:USDT``）。

    说明②：仅返回**现存**合约 —— 直接用于早期年份回测会有幸存者偏差。
    """
    exchange = _make_okx_perp(exchange)
    markets = exchange.load_markets()
    symbols = [
        m["symbol"]
        for m in markets.values()
        if m.get("swap")
        and m.get("active")
        and m.get("quote") == quote
        and m.get("settle") == quote
        and m.get("linear")
    ]
    logger.info("发现 {} 个在交易的 {} 本位永续", len(symbols), quote)
    return sorted(symbols)


def load_dollar_volume_panel(
    exchange=None,
    symbols: list[str] | None = None,
    since: pd.Timestamp | None = None,
    until: pd.Timestamp | None = None,
) -> pd.DataFrame:
    """拉取各标的日线，构造美元成交额宽表（index=日期, columns=symbol）。

    复用 ``data/fetcher.py`` 的分页拉取（OKX 单次最多 100 根，必须分页）。
    dollar_volume ≈ close × base_volume（审计说明①）。
    """
    from .fetcher import fetch_ohlcv  # 局部导入，避免纯逻辑路径牵入 fetcher

    exchange = _make_okx_perp(exchange)
    if symbols is None:
        symbols = list_perpetual_symbols(exchange)

    series_map: dict[str, pd.Series] = {}
    for symbol in symbols:
        try:
            df = fetch_ohlcv(symbol, timeframe="1d", since=since, until=until, exchange=exchange)
        except Exception as exc:  # noqa: BLE001 — 单标的失败不应中断整体
            logger.warning("拉取 {} 失败：{}", symbol, exc)
            continue
        if df.empty:
            continue
        idx = df.index.normalize()
        series_map[symbol] = pd.Series((df["close"] * df["volume"]).values, index=idx)

    if not series_map:
        return pd.DataFrame()
    panel = pd.DataFrame(series_map).sort_index()
    if until is not None:
        panel = panel.loc[panel.index <= _align_tz(pd.Timestamp(until), panel.index)]
    return panel


def build_recent_universe(
    exchange=None,
    months: int = 3,
    config: UniverseConfig | None = None,
) -> dict[pd.Timestamp, list[str]]:
    """最小验收用：取最近 ``months`` 个月的逐月池成分（Step 0-5 验收）。"""
    config = config or UniverseConfig()
    now = pd.Timestamp.utcnow().normalize()
    start = (now - pd.DateOffset(months=months)).normalize()
    # 多取 lookback + listing 余量，保证窗口与上市判定有数据
    fetch_since = start - timedelta(days=config.min_listing_days + config.lookback_days + 5)
    panel = load_dollar_volume_panel(exchange, since=fetch_since, until=now)
    if panel.empty:
        logger.error("成交额面板为空，无法构建动态池")
        return {}
    return build_universe_history(panel, start=start, end=now, config=config)
