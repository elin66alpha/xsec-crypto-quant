"""多标的数据拉取：OHLCV + funding rate，自动分页 + 断点续传（Step 0-6）。

接口：OKX USDT 本位永续（ccxt ``okx``），全部 REST（决策 B）。
（交易所由 Binance 改为 OKX：美国 IP 可直连 OKX，Binance 全球站对美 IP 451 封锁。）
时间口径统一 UTC；返回的 DataFrame 以 ``datetime``（tz-aware UTC）为索引，升序、去重。

断点续传：调用方传入 ``since``（如已存数据的最后时间戳 + 1 个 bar）即可从断点继续；
``fetch_ohlcv`` 内部分页直到 ``until``（或当前时刻），自动处理交易所单次返回上限。
"""

from __future__ import annotations

import time

import pandas as pd
from loguru import logger

# OKX 历史 K线 / funding 历史每次最多返回 100 条；PAGE_LIMIT 必须等于交易所单次上限，
# 否则"返回数 < 请求数即视为末页"的判定会提前误停。
OHLCV_PAGE_LIMIT = 100
FUNDING_PAGE_LIMIT = 100
MAX_EMPTY_PAGES = 2  # 连续空页则停止，避免死循环


def make_okx_perp(exchange=None):
    """配置好的 OKX 永续 ccxt 实例（懒加载 ccxt，开启限速）。"""
    if exchange is not None:
        return exchange
    import ccxt

    return ccxt.okx({"enableRateLimit": True})


def _to_ms(ts) -> int | None:
    """Timestamp/str/datetime → epoch 毫秒（naive 视为 UTC）。"""
    if ts is None:
        return None
    t = pd.Timestamp(ts)
    if t.tz is None:
        t = t.tz_localize("UTC")
    return int(t.timestamp() * 1000)


def fetch_ohlcv(
    symbol: str,
    timeframe: str = "1d",
    since=None,
    until=None,
    exchange=None,
) -> pd.DataFrame:
    """分页拉取单标的 OHLCV，返回 UTC 索引的 DataFrame。

    Columns: open, high, low, close, volume。``since`` 缺省取该合约最早数据；
    ``until`` 缺省取当前时刻。可重复调用做断点续传（传入上次最后时间戳的下一个 bar）。
    """
    exchange = make_okx_perp(exchange)
    tf_ms = exchange.parse_timeframe(timeframe) * 1000
    cursor = _to_ms(since)
    until_ms = _to_ms(until) or exchange.milliseconds()

    rows: list[list] = []
    empty_pages = 0
    while True:
        batch = exchange.fetch_ohlcv(symbol, timeframe=timeframe, since=cursor, limit=OHLCV_PAGE_LIMIT)
        # 过滤掉 until 之后的数据
        batch = [r for r in batch if r[0] <= until_ms]
        if not batch:
            empty_pages += 1
            if cursor is None or empty_pages >= MAX_EMPTY_PAGES:
                break
            cursor += tf_ms * OHLCV_PAGE_LIMIT
            continue
        empty_pages = 0
        rows.extend(batch)
        last_ts = batch[-1][0]
        if last_ts >= until_ms or len(batch) < OHLCV_PAGE_LIMIT:
            break
        cursor = last_ts + tf_ms  # 下一页从最后一根的下一根开始
        if exchange.rateLimit:
            time.sleep(exchange.rateLimit / 1000)

    if not rows:
        logger.warning("fetch_ohlcv: {} 无数据 (since={}, until={})", symbol, since, until)
        return _empty_ohlcv()

    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume"])
    df["datetime"] = pd.to_datetime(df["ts"], unit="ms", utc=True)
    df = (
        df.drop(columns="ts")
        .drop_duplicates(subset="datetime")
        .set_index("datetime")
        .sort_index()
    )
    logger.info("fetch_ohlcv {} {}: {} 根 [{} ~ {}]", symbol, timeframe, len(df),
                df.index[0].date(), df.index[-1].date())
    return df


def fetch_funding_rate_history(
    symbol: str,
    since=None,
    until=None,
    exchange=None,
) -> pd.DataFrame:
    """分页拉取单标的 funding rate 历史（决策 3：carry 因子 + 损益结算项）。

    Returns
    -------
    DataFrame
        index = datetime（UTC，资金费结算时点，通常每 8h 一次），列 ``fundingRate``。
    """
    exchange = make_okx_perp(exchange)
    cursor = _to_ms(since)
    until_ms = _to_ms(until) or exchange.milliseconds()

    rows: list[dict] = []
    empty_pages = 0
    while True:
        batch = exchange.fetch_funding_rate_history(symbol, since=cursor, limit=FUNDING_PAGE_LIMIT)
        batch = [r for r in batch if r["timestamp"] is not None and r["timestamp"] <= until_ms]
        if not batch:
            empty_pages += 1
            if cursor is None or empty_pages >= MAX_EMPTY_PAGES:
                break
            cursor = (cursor or 0) + 8 * 3600 * 1000 * FUNDING_PAGE_LIMIT
            continue
        empty_pages = 0
        rows.extend(batch)
        last_ts = batch[-1]["timestamp"]
        if last_ts >= until_ms or len(batch) < FUNDING_PAGE_LIMIT:
            break
        cursor = last_ts + 1
        if exchange.rateLimit:
            time.sleep(exchange.rateLimit / 1000)

    if not rows:
        logger.warning("fetch_funding_rate_history: {} 无数据", symbol)
        return pd.DataFrame(columns=["fundingRate"], index=pd.DatetimeIndex([], name="datetime"))

    df = pd.DataFrame(
        {
            "datetime": pd.to_datetime([r["timestamp"] for r in rows], unit="ms", utc=True),
            "fundingRate": [r["fundingRate"] for r in rows],
        }
    )
    df = df.drop_duplicates(subset="datetime").set_index("datetime").sort_index()
    logger.info("fetch_funding {}: {} 条 [{} ~ {}]", symbol, len(df),
                df.index[0].date(), df.index[-1].date())
    return df


def fetch_many_ohlcv(
    symbols: list[str],
    timeframe: str = "1d",
    since=None,
    until=None,
    exchange=None,
) -> dict[str, pd.DataFrame]:
    """批量拉取多标的 OHLCV，单标的失败不中断整体。"""
    exchange = make_okx_perp(exchange)
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            out[symbol] = fetch_ohlcv(symbol, timeframe, since, until, exchange)
        except Exception as exc:  # noqa: BLE001
            logger.error("fetch_many_ohlcv: {} 失败 {}", symbol, exc)
            out[symbol] = _empty_ohlcv()
    return out


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(
        columns=["open", "high", "low", "close", "volume"],
        index=pd.DatetimeIndex([], name="datetime"),
    )
