# 阶段2 真实 in-sample 因子筛选（决策A：IC × 等权多空价差 并列主筛）

价差(ls_spread)=每日顶层均值−底层均值的时间均值；ls_ci 为 circular block bootstrap (block=20) 95% 区间，不含 0 即显著(ls_sig)。direction=按价差符号定多空腿方向。
class：robust=IC与价差同号且都显著；divergent(tail)=价差显著但IC反号(尾部驱动、脆弱)；weak=价差不显著。

| factor | best_h | class | direction | mean_ic | ic_t | ic_sig | ls_spread | ls_ci_lo | ls_ci_hi | ls_sig |
|---|---|---|---|---|---|---|---|---|---|---|
| momentum_7 | 1 | divergent(tail) | long-high | -0.03304 | -4.71 | True | +0.00221 | +0.00015 | +0.00451 | True |
| realized_vol_30 | 10 | ic-only | long-low | -0.10805 | -6.09 | True | -0.00505 | -0.02226 | +0.01219 | False |
| volume_chg_30 | 3 | ic-only | long-low | -0.04953 | -6.24 | True | -0.00264 | -0.00734 | +0.00207 | False |
| momentum_30 | 1 | ic-only | long-high | -0.02501 | -3.53 | True | +0.00175 | -0.00000 | +0.00366 | False |
| realized_vol_14 | 10 | ic-only | long-low | -0.10088 | -6.02 | True | -0.00123 | -0.01560 | +0.01342 | False |
| momentum_90 | 1 | ic-only | long-high | -0.03712 | -5.28 | True | +0.00121 | -0.00045 | +0.00304 | False |
| momentum_14 | 1 | ic-only | long-high | -0.02738 | -3.86 | True | +0.00105 | -0.00076 | +0.00301 | False |
| momentum_60 | 1 | ic-only | long-high | -0.03902 | -5.48 | True | +0.00067 | -0.00097 | +0.00247 | False |
| volume_chg_14 | 1 | ic-only | long-low | -0.03931 | -6.45 | True | -0.00040 | -0.00212 | +0.00131 | False |
| carry_14 | 10 | weak | long-high | +0.00313 | +0.22 | False | +0.00060 | -0.01422 | +0.01623 | False |
| carry_7 | 5 | weak | long-low | -0.01425 | -1.47 | False | -0.00036 | -0.00702 | +0.00625 | False |
| carry_30 | 1 | weak | long-high | +0.00658 | +1.10 | False | +0.00020 | -0.00140 | +0.00181 | False |
