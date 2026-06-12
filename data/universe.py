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

import json
import os
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

# --- 决策 1 的默认参数（与 .env.example 对齐；env 可覆盖，但代码不依赖 env 存在）---
DEFAULT_TOP_N = int(os.getenv("UNIVERSE_TOP_N", "30"))
DEFAULT_MIN_LISTING_DAYS = int(os.getenv("UNIVERSE_MIN_LISTING_DAYS", "90"))
DEFAULT_LOOKBACK_DAYS = int(os.getenv("UNIVERSE_VOLUME_LOOKBACK_DAYS", "30"))
# lookback 窗口内至少要有这么多个有效成交额日，才有资格参与排名（防止"上市一两天就刷量"入选）
DEFAULT_MIN_VALID_DAYS_RATIO = 0.5
DEFAULT_PERP_CALENDAR_PATH = Path(__file__).resolve().parents[1] / "metadata" / "okx_perp_calendar.json"


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


def load_perp_calendar(path: str | Path = DEFAULT_PERP_CALENDAR_PATH) -> pd.DataFrame:
    """读取 USDT 永续合约上市/退市日历。

    日历用于修正仅从成交额面板推断上市日的幸存者偏差：历史回测应优先使用外部
    listing/delisting 元数据，尤其是现在已退市、但在早年真实可交易的合约。退市日只
    用来截断退市后的月份，不能在退市前作为选池信号。
    """
    with Path(path).open(encoding="utf-8") as fp:
        payload = json.load(fp)

    contracts = payload.get("contracts", payload)
    calendar = pd.DataFrame(contracts)
    if calendar.empty:
        return calendar

    required = {"symbol", "listing_date", "delisting_date", "data_source"}
    missing = required.difference(calendar.columns)
    if missing:
        raise ValueError(f"永续合约日历缺少字段: {sorted(missing)}")

    calendar["listing_date"] = pd.to_datetime(calendar["listing_date"], utc=True, errors="coerce")
    calendar["delisting_date"] = pd.to_datetime(calendar["delisting_date"], utc=True, errors="coerce")
    calendar = calendar.drop_duplicates(subset=["symbol"], keep="last").set_index("symbol").sort_index()
    return calendar


def calendar_listing_dates(calendar: pd.DataFrame) -> pd.Series:
    """从合约日历提取上市日期 Series。"""
    if "listing_date" not in calendar.columns:
        raise ValueError("永续合约日历缺少 listing_date 字段")
    return calendar["listing_date"].dropna()


def calendar_delisting_dates(calendar: pd.DataFrame) -> pd.Series:
    """从合约日历提取退市日期 Series。"""
    if "delisting_date" not in calendar.columns:
        raise ValueError("永续合约日历缺少 delisting_date 字段")
    return calendar["delisting_date"].dropna()


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
    delisting_dates: pd.Series | None = None,
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
    delisting_dates : Series, optional
        每个标的的退市日；``as_of`` 晚于退市日时剔除。退市日不能参与排序，只用于
        防止退市后继续出现在动态池中。
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
        listed_ts = _align_tz(pd.Timestamp(listed), dollar_volume.index)
        if (as_of - listed_ts).days < config.min_listing_days:
            continue
        if delisting_dates is not None:
            delisted = delisting_dates.get(symbol)
            if pd.notna(delisted):
                delisted_ts = _align_tz(pd.Timestamp(delisted), dollar_volume.index)
                if as_of > delisted_ts:
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
    delisting_dates: pd.Series | None = None,
    calendar: pd.DataFrame | None = None,
    config: UniverseConfig | None = None,
) -> dict[pd.Timestamp, list[str]]:
    """逐月构建动态池：返回 {月初日期 -> 当月可交易成分列表}。

    每个月初用"截至该日"的成交额重新选池；该池在当月内有效（决策 4 的月度池再平衡，
    与日频持仓再平衡是两个层面：池是月度的，持仓是日度的）。
    """
    config = config or UniverseConfig()
    if calendar is not None:
        listing_dates = calendar_listing_dates(calendar)
        delisting_dates = calendar_delisting_dates(calendar)
    if listing_dates is None:
        listing_dates = infer_listing_dates(dollar_volume)

    history: dict[pd.Timestamp, list[str]] = {}
    for month_start in month_start_dates(start, end):
        members = select_universe(dollar_volume, month_start, listing_dates, delisting_dates, config)
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
    calendar: pd.DataFrame | None = None,
    fetch_session=None,
) -> pd.DataFrame:
    """拉取各标的日线，构造美元成交额宽表（index=日期, columns=symbol）。

    复用 ``data/fetcher.py`` 的分页拉取（OKX 单次最多 100 根，必须分页）。
    若传入合约日历，则按 ``data_source`` 对已退市合约走 Binance Vision fallback。
    dollar_volume ≈ close × base_volume（审计说明①）。
    """
    from .fetcher import fetch_ohlcv  # 局部导入，避免纯逻辑路径牵入 fetcher

    if symbols is None:
        if calendar is not None:
            symbols = list(calendar.index)
        else:
            exchange = _make_okx_perp(exchange)
            symbols = list_perpetual_symbols(exchange)

    source_map: dict[str, tuple[str, str | None]] = {}
    for symbol in symbols:
        source = "okx"
        binance_symbol = None
        if calendar is not None and symbol in calendar.index:
            source_value = calendar.loc[symbol].get("data_source", "okx")
            source = "okx" if pd.isna(source_value) else str(source_value)
            binance_value = calendar.loc[symbol].get("binance_symbol")
            if pd.notna(binance_value):
                binance_symbol = str(binance_value)
        source_map[symbol] = (source, binance_symbol)

    if any(source == "okx" for source, _ in source_map.values()):
        exchange = _make_okx_perp(exchange)

    series_map: dict[str, pd.Series] = {}
    for symbol in symbols:
        source, binance_symbol = source_map[symbol]
        try:
            df = fetch_ohlcv(
                symbol,
                timeframe="1d",
                since=since,
                until=until,
                exchange=exchange if source == "okx" else None,
                source=source,
                binance_symbol=binance_symbol,
                session=fetch_session,
            )
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
