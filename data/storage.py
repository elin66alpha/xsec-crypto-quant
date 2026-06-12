"""本地存储：按标的 + 按月分片 parquet（Step 0-7）。

命名（CLAUDE.md）：
    OHLCV   : ``okx_perp_{symbol}_{timeframe}_{year}_{month:02d}.parquet``
    funding : ``okx_perp_{symbol}_funding_{year}_{month:02d}.parquet``

``symbol`` 经 ``sanitize_symbol`` 规整（``BTC/USDT:USDT`` -> ``BTCUSDT``）。
写入幂等：同一分片重复写会与已存数据按索引去重合并，支持断点续传。
索引统一 tz-aware UTC。所有 parquet 受 .gitignore 忽略，不入库。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pandas as pd
from loguru import logger

DEFAULT_DATA_DIR = os.getenv("DATA_DIR", "./data")


def _utc(ts) -> pd.Timestamp:
    """归一到 tz-aware UTC（naive 视为 UTC，已带 tz 则转换）。"""
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tz is None else t.tz_convert("UTC")


def sanitize_symbol(symbol: str) -> str:
    """``BTC/USDT:USDT`` / ``BTC/USDT`` -> ``BTCUSDT``（用于文件名）。

    先去掉 ccxt 的 ``:settle`` 后缀（否则结算币会被并进文件名，如 BTCUSDTUSDT），
    再去除非字母数字字符。
    """
    base = symbol.split(":")[0]
    return re.sub(r"[^A-Za-z0-9]", "", base)


def _shard_path(data_dir: str | Path, symbol: str, kind: str, year: int, month: int) -> Path:
    sym = sanitize_symbol(symbol)
    fname = f"okx_perp_{sym}_{kind}_{year}_{month:02d}.parquet"
    return Path(data_dir) / sym / fname


def _save_sharded(df: pd.DataFrame, symbol: str, kind: str, data_dir: str | Path) -> list[Path]:
    """按 (year, month) 切分写出；与已存分片去重合并。返回写出的分片路径。"""
    if df.empty:
        logger.warning("_save_sharded: {} {} 空数据，跳过", symbol, kind)
        return []
    if df.index.tz is None:
        raise ValueError("DataFrame 索引必须是 tz-aware UTC（请先 fetch 层处理）")

    written: list[Path] = []
    for (year, month), group in df.groupby([df.index.year, df.index.month]):
        path = _shard_path(data_dir, symbol, kind, int(year), int(month))
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            existing = pd.read_parquet(path)
            merged = pd.concat([existing, group])
            merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        else:
            merged = group.sort_index()
        merged.to_parquet(path, engine="pyarrow")
        written.append(path)
    logger.info("save {} {}: {} 根 -> {} 个分片", symbol, kind, len(df), len(written))
    return written


def save_ohlcv(df: pd.DataFrame, symbol: str, timeframe: str = "1d",
               data_dir: str | Path = DEFAULT_DATA_DIR) -> list[Path]:
    """保存 OHLCV，按月分片。"""
    return _save_sharded(df, symbol, timeframe, data_dir)


def save_funding(df: pd.DataFrame, symbol: str,
                 data_dir: str | Path = DEFAULT_DATA_DIR) -> list[Path]:
    """保存 funding rate 历史，按月分片。"""
    return _save_sharded(df, symbol, "funding", data_dir)


def _load_sharded(symbol: str, kind: str, start, end, data_dir: str | Path) -> pd.DataFrame:
    sym = sanitize_symbol(symbol)
    base = Path(data_dir) / sym
    pattern = f"okx_perp_{sym}_{kind}_*.parquet"
    files = sorted(base.glob(pattern))
    if not files:
        logger.warning("_load_sharded: 未找到 {} {} 分片", symbol, kind)
        return pd.DataFrame()
    frames = [pd.read_parquet(f) for f in files]
    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    if start is not None:
        df = df.loc[df.index >= _utc(start)]
    if end is not None:
        df = df.loc[df.index <= _utc(end)]
    return df


def load_ohlcv(symbol: str, timeframe: str = "1d", start=None, end=None,
               data_dir: str | Path = DEFAULT_DATA_DIR) -> pd.DataFrame:
    """读取 OHLCV，合并所有分片并按 [start, end] 过滤。"""
    return _load_sharded(symbol, timeframe, start, end, data_dir)


def load_funding(symbol: str, start=None, end=None,
                 data_dir: str | Path = DEFAULT_DATA_DIR) -> pd.DataFrame:
    """读取 funding rate 历史。"""
    return _load_sharded(symbol, "funding", start, end, data_dir)


def available_range(symbol: str, timeframe: str = "1d",
                    data_dir: str | Path = DEFAULT_DATA_DIR) -> tuple[pd.Timestamp, pd.Timestamp] | None:
    """返回已存 OHLCV 的 (最早, 最晚) 时间戳，用于断点续传决定 ``since``；无数据返回 None。"""
    df = load_ohlcv(symbol, timeframe, data_dir=data_dir)
    if df.empty:
        return None
    return df.index[0], df.index[-1]
