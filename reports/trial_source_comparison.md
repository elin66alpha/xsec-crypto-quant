# Trial Source Comparison

Window: 2020-12-01 to 2021-01-31
OKX daily candles are requested through ccxt with UTC timezone defaults; ccxt maps daily OKX bars to the UTC candle key for >=6h timeframes.

Minimum return correlation: 0.999492

| base | OKX rows | Binance rows | aligned returns | mean diff bp | max abs diff bp | corr | UTC boundary |
|---|---:|---:|---:|---:|---:|---:|---|
| BTC | 62 | 62 | 61 | -0.0260 | 28.7025 | 0.999830 | yes |
| ETH | 62 | 62 | 61 | 0.0128 | 25.5462 | 0.999888 | yes |
| LTC | 62 | 62 | 61 | -0.3741 | 42.8182 | 0.999857 | yes |
| XRP | 62 | 62 | 61 | 0.1215 | 203.3662 | 0.999492 | yes |
| DOGE | 62 | 62 | 61 | -7.0326 | 416.2594 | 0.999986 | yes |

## Funding Depth Diagnostics

| asset_id | rows | first | last | reaches start | max gap hours |
|---|---:|---|---|---|---:|
| BTC-20191112 | 0 | None | None | None | None |
| ETH-20191112 | 0 | None | None | None | None |
