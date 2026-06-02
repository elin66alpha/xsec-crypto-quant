"""因子分析 HTML 报告测试（阶段 2）。验收：含 IC/ICIR/FDR q 值/分层图。"""

from __future__ import annotations

import numpy as np
import pandas as pd

from factors.report import render_html, save_html
from factors.selection import analyze_factors
from features.cross_sectional import momentum


def _analysis(seed: int = 5):
    rng = np.random.default_rng(seed)
    n_days, n_coins = 180, 15
    dates = pd.date_range("2023-01-01", periods=n_days, freq="D", tz="UTC")
    coins = [f"C{i:02d}" for i in range(n_coins)]
    mu = rng.normal(0.0, 0.003, n_coins)
    market = rng.normal(0.0005, 0.02, n_days)
    ret = mu[None, :] + market[:, None] + rng.normal(0, 0.02, (n_days, n_coins))
    close = pd.DataFrame(100 * np.cumprod(1 + ret, axis=0), index=dates, columns=coins)
    panels = {
        "momentum_10": momentum(close, 10),
        "momentum_20": momentum(close, 20),
        "noise": pd.DataFrame(rng.normal(size=close.shape), index=dates, columns=coins),
    }
    return analyze_factors(panels, close, horizons=(1, 5), q=0.10)


def test_render_html_contains_key_sections():
    htmlstr = render_html(_analysis())
    assert htmlstr.lstrip().lower().startswith("<!doctype html>")
    assert "IC / ICIR / FDR" in htmlstr
    assert "qvalue" in htmlstr            # FDR 校正后 q 值列
    assert "monotonicity" in htmlstr      # 分层回测图
    assert "0.1" in htmlstr               # q 值出现在元信息
    assert "诚实声明" in htmlstr          # 诚实免责声明


def test_render_html_lists_selected_factors():
    res = _analysis()
    htmlstr = render_html(res)
    assert res.selected, "应至少选出一个因子"
    for s in res.selected:
        assert s in htmlstr


def test_save_html_writes_file(tmp_path):
    out = save_html(_analysis(), path=tmp_path / "rep.html")
    assert out.exists()
    text = out.read_text(encoding="utf-8")
    assert "<html" in text.lower()
    assert "横截面因子分析报告" in text


def test_render_html_handles_no_survivors():
    """无入选因子时报告也应能渲染,不报错。"""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2023-01-01", periods=120, freq="D", tz="UTC")
    cols = [f"C{i}" for i in range(12)]
    close = pd.DataFrame(
        100 * np.cumprod(1 + rng.normal(0, 0.02, (120, 12)), axis=0), index=dates, columns=cols
    )
    panels = {"pure_noise": pd.DataFrame(rng.normal(size=(120, 12)), index=dates, columns=cols)}
    res = analyze_factors(panels, close, horizons=(1, 5))
    htmlstr = render_html(res)
    assert "<!doctype html>" in htmlstr.lower()
