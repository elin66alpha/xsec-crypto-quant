"""数据验证（Step 0-8）：时间戳连续性、OHLCV 合法性、异常值、池成分一致性。

定位：进入因子工程前的数据完整性闸门。能检出**人为注入**的 gap / 非法 OHLC /
异常跳变，以及池成分的幸存者偏差 / 前视（决策 1 铁律）。

严重度：
    error   —— 硬性违规（OHLC 不自洽、重复时间戳、池中含"当时尚未上市"的标的）
    warning —— 可能合法但需人看（时间 gap 可能是真实停牌、极端单日跳变）
纯逻辑，仅依赖 pandas/numpy，无网络。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class ValidationReport:
    """验证结果。``ok`` 为 True 表示无 error（warning 不影响 ok）。"""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def extend(self, other: ValidationReport) -> ValidationReport:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)
        return self

    def summary(self) -> str:
        head = "PASS" if self.ok else "FAIL"
        return (f"[{head}] {len(self.errors)} error(s), {len(self.warnings)} warning(s)\n"
                + "\n".join(f"  ✗ {e}" for e in self.errors)
                + ("\n" if self.errors and self.warnings else "")
                + "\n".join(f"  ! {w}" for w in self.warnings))


def _timeframe_to_offset(timeframe: str) -> pd.Timedelta:
    unit = {"d": "D", "h": "h", "m": "min"}.get(timeframe[-1].lower())
    if unit is None:
        raise ValueError(f"不支持的 timeframe: {timeframe}")
    return pd.Timedelta(int(timeframe[:-1]), unit)


def check_timestamp_continuity(df: pd.DataFrame, timeframe: str = "1d",
                               symbol: str = "") -> ValidationReport:
    """检测重复时间戳（error）与时间 gap（warning）。"""
    rep = ValidationReport()
    tag = f"[{symbol}] " if symbol else ""
    if df.empty:
        rep.add_warning(f"{tag}空数据，跳过连续性检查")
        return rep
    idx = df.index
    if idx.duplicated().any():
        dups = idx[idx.duplicated()].tolist()
        rep.add_error(f"{tag}重复时间戳 {len(dups)} 处，例：{dups[:3]}")
    if not idx.is_monotonic_increasing:
        rep.add_error(f"{tag}时间戳非单调递增")
    step = _timeframe_to_offset(timeframe)
    expected = pd.date_range(idx.min(), idx.max(), freq=step)
    missing = expected.difference(idx)
    if len(missing) > 0:
        rep.add_warning(f"{tag}时间 gap {len(missing)} 处（可能为停牌），例：{[str(m.date()) for m in missing[:3]]}")
    return rep


def check_ohlcv_validity(df: pd.DataFrame, symbol: str = "") -> ValidationReport:
    """OHLCV 合法性：正价、量非负、high≥low、high≥max(o,c)、low≤min(o,c)。"""
    rep = ValidationReport()
    tag = f"[{symbol}] " if symbol else ""
    if df.empty:
        rep.add_warning(f"{tag}空数据，跳过合法性检查")
        return rep
    need = {"open", "high", "low", "close", "volume"}
    missing_cols = need - set(df.columns)
    if missing_cols:
        rep.add_error(f"{tag}缺少列：{missing_cols}")
        return rep

    o, h, low, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        rep.add_error(f"{tag}存在非正价格")
    if (v < 0).any():
        rep.add_error(f"{tag}存在负成交量")
    if df[["open", "high", "low", "close", "volume"]].isna().any().any():
        rep.add_error(f"{tag}存在 NaN")
    bad_hl = (h < low).sum()
    if bad_hl:
        rep.add_error(f"{tag}high < low 共 {int(bad_hl)} 处")
    bad_h = (h < o).sum() + (h < c).sum()
    if bad_h:
        rep.add_error(f"{tag}high < open/close 共 {int(bad_h)} 处")
    bad_l = (low > o).sum() + (low > c).sum()
    if bad_l:
        rep.add_error(f"{tag}low > open/close 共 {int(bad_l)} 处")
    return rep


def check_outliers(df: pd.DataFrame, symbol: str = "",
                   abs_return_threshold: float = 2.0) -> ValidationReport:
    """异常值（warning）：单 bar 收盘对数收益绝对值超阈值（默认 |logret|>2 ≈ ±7x）。

    阈值取得很宽：加密单日 ±50% 常见、不算异常；这里只抓极可能是数据错误的离群跳变。
    """
    rep = ValidationReport()
    tag = f"[{symbol}] " if symbol else ""
    if df.empty or "close" not in df.columns or len(df) < 2:
        return rep
    logret = np.log(df["close"]).diff()
    extreme = logret[logret.abs() > abs_return_threshold]
    if len(extreme) > 0:
        rep.add_warning(f"{tag}极端单bar收益 {len(extreme)} 处（|logret|>{abs_return_threshold}），"
                        f"例：{[str(i.date()) for i in extreme.index[:3]]}")
    return rep


def validate_ohlcv(df: pd.DataFrame, timeframe: str = "1d", symbol: str = "") -> ValidationReport:
    """对单标的 OHLCV 跑全部检查并汇总。"""
    rep = ValidationReport()
    rep.extend(check_timestamp_continuity(df, timeframe, symbol))
    rep.extend(check_ohlcv_validity(df, symbol))
    rep.extend(check_outliers(df, symbol))
    return rep


def _as_utc_naive(ts) -> pd.Timestamp:
    """验证层统一把时间戳视为 UTC 后比较，避免 tz-aware/naive 混比。"""
    out = pd.Timestamp(ts)
    if out.tz is not None:
        out = out.tz_convert("UTC").tz_localize(None)
    return out


def check_required_historical_members(
    universe_history: dict[pd.Timestamp, list[str]],
    required_symbols: set[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    min_required: int = 1,
    label: str = "now-delisted",
) -> ValidationReport:
    """检查历史样本期是否至少包含指定数量的代表性标的。

    用途：早年回测若只含现存合约，容易产生幸存者偏差。对 2021/2022 等历史窗口，
    可传入现在已退市但当时真实可交易的代表标的集合（如 FTT/SRM/ANC）；若窗口内
    一个都没有，应视为数据池构建失败，而不是继续做无偏性声称。
    """
    rep = ValidationReport()
    if not required_symbols:
        return rep
    if min_required < 1:
        raise ValueError("min_required 必须 >= 1")

    start_ts = _as_utc_naive(start).normalize()
    end_ts = _as_utc_naive(end).normalize()
    if end_ts < start_ts:
        raise ValueError("end 必须不早于 start")

    seen: set[str] = set()
    months_checked = 0
    for month_start, members in universe_history.items():
        ms = _as_utc_naive(month_start).normalize()
        if start_ts <= ms <= end_ts:
            months_checked += 1
            seen.update(members)

    present = sorted(required_symbols & seen)
    if len(present) < min_required:
        missing = sorted(required_symbols - seen)
        rep.add_error(
            f"{start_ts.date()}~{end_ts.date()}: 历史池仅包含 {len(present)} 个 {label} 代表标的 "
            f"{present}，低于要求 {min_required}；缺失候选 {missing}，疑似幸存者偏差"
        )
    if months_checked == 0:
        rep.add_warning(f"{start_ts.date()}~{end_ts.date()}: 无月度池记录，无法检查 {label} 代表标的")
    return rep


def check_universe_consistency(
    universe_history: dict[pd.Timestamp, list[str]],
    listing_dates: pd.Series,
    min_listing_days: int = 90,
    delisting_dates: pd.Series | None = None,
    required_presence_symbols: set[str] | None = None,
    required_presence_start: pd.Timestamp | None = None,
    required_presence_end: pd.Timestamp | None = None,
    required_presence_min_count: int = 1,
) -> ValidationReport:
    """池成分一致性（决策 1 铁律）：检出幸存者偏差 / 前视。

    对每个月初的池子，逐标的核验：
      - 该标的有已知上市日（否则无法证明当时存在）；
      - 上市日 ≤ 月初（否则是"用未来才上市的币回测过去"= 前视，error）；
      - 上市满 min_listing_days（否则违反决策 1 选池规则，error）。
      - 若提供退市日，月初晚于退市日后不得继续入池。
    可选的 required_presence_* 用于确保早期历史窗口含有现已退市代表标的，避免
    只用现存合约声称无偏历史回测。
    """
    rep = ValidationReport()
    for month_start, members in universe_history.items():
        ms = _as_utc_naive(month_start)
        for sym in members:
            listed = listing_dates.get(sym)
            if listed is None or pd.isna(listed):
                rep.add_error(f"{ms.date()}: 池含 {sym} 但无上市日记录（无法证明当时存在，疑似幸存者偏差）")
                continue
            listed = _as_utc_naive(listed)
            age = (ms - listed).days
            if age < 0:
                rep.add_error(f"{ms.date()}: 池含 {sym}，其上市日 {listed.date()} 晚于该月（前视/幸存者偏差）")
            elif age < min_listing_days:
                rep.add_error(f"{ms.date()}: 池含 {sym}，上市仅 {age} 天 < {min_listing_days}（违反决策1选池规则）")
            if delisting_dates is not None:
                delisted = delisting_dates.get(sym)
                if pd.notna(delisted):
                    delisted = _as_utc_naive(delisted)
                    if ms > delisted:
                        rep.add_error(f"{ms.date()}: 池含 {sym}，但其退市日为 {delisted.date()}（退市后仍入池）")
    if required_presence_symbols is not None:
        months = sorted(_as_utc_naive(ts) for ts in universe_history)
        start = required_presence_start or (months[0] if months else pd.Timestamp.min)
        end = required_presence_end or (months[-1] if months else pd.Timestamp.max)
        rep.extend(
            check_required_historical_members(
                universe_history,
                required_presence_symbols,
                start=start,
                end=end,
                min_required=required_presence_min_count,
            )
        )
    if rep.ok:
        logger.info("池成分一致性检查通过：{} 个月份", len(universe_history))
    return rep
