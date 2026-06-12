# OKX 已退市永续合约历史数据可得性探测

任务：审查问题 #2 的前置调查。只探测和记录结论，不修改 `data/universe.py`。

运行命令：

```bash
~/miniconda3/envs/xsec-crypto-quant/bin/python scripts/probe_okx_delisted.py --max-pages 20 --sleep 0.25
```

运行时间：2026-06-12T02:08:43Z。

## 结论

**结论档位：B（部分可得），但对真正已从 OKX instruments 移除的合约，实际等同 C。**

- `LUNA-USDT-SWAP` 当前仍在 OKX `SWAP` instruments 清单中，因此 K 线可取；探测到日线范围为 2022-05-28 至 2026-06-12。这个范围不覆盖 2022-05 LUNA 崩盘前的旧 LUNA 历史，不能证明 OKX 可取“已退市旧合约”的完整历史。
- `FTT-USDT-SWAP`、`SRM-USDT-SWAP`、`ANC-USDT-SWAP` 在 OKX instruments 中不存在；`history-candles` 和 `funding-rate-history` 均返回 HTTP 200 但 OKX code `51001`，0 行，消息为 `Instrument ID, Instrument ID code, or Spread ID doesn't exist.`
- 未找到 OKX 公共 REST 可列出历史上曾存在的全部 SWAP 合约的接口。`instruments` 的 `state` 参数探测没有增加历史合约；猜测的 history instruments 端点返回 404 或参数错误。
- Binance `data.binance.vision` 的 USD-M futures daily klines 归档对四个探测币种均有至少一个历史日 zip 返回 HTTP 200，可作为降级预案的 K 线存在性佐证。

实现含义：**仅靠 OKX 公共 REST 不能构建早期年份完整、退市币无偏的 OKX-only 动态池。** 后续实现需要 leader 决策：使用 OKX 官方下架公告/外部日历补合约生命周期；对 OKX 无法拉回的已退市价格序列，采用 Binance archive 等归档源并在回测报告中披露混合口径。

## OKX 当前 instruments 对照

ccxt `okx.public_get_public_instruments({"instType": "SWAP"})`：

| 项目 | 结果 |
|---|---:|
| HTTP/API | OKX code `0` |
| 当前 SWAP instruments 行数 | 371 |
| `LUNA-USDT-SWAP` | present |
| `FTT-USDT-SWAP` | absent |
| `SRM-USDT-SWAP` | absent |
| `ANC-USDT-SWAP` | absent |

直接 REST `/api/v5/public/instruments?instType=SWAP` 结果一致。额外测试 `state=live/suspend/preopen/test/expired/all` 均返回同样 371 行，未暴露退市历史合约。按 `instId` 直接查询时：

| instId | HTTP | OKX code | rows |
|---|---:|---:|---:|
| `LUNA-USDT-SWAP` | 200 | `0` | 1 |
| `FTT-USDT-SWAP` | 200 | `51001` | 0 |
| `SRM-USDT-SWAP` | 200 | `51001` | 0 |
| `ANC-USDT-SWAP` | 200 | `51001` | 0 |

历史合约列表接口探测：

| Endpoint | Params | HTTP | OKX code/msg | rows |
|---|---|---:|---|---:|
| `/api/v5/public/instruments-history` | `instType=SWAP` | 404 | `404 / Not Found` | 0 |
| `/api/v5/public/instrument-history` | `instType=SWAP` | 404 | `404 / Not Found` | 0 |
| `/api/v5/public/delivery-exercise-history` | `instType=SWAP` | 400 | `51000 / Parameter instType error` | 0 |

## OKX 历史 K 线与 funding 探测

探测端点：

- `/api/v5/market/history-candles`，参数 `instId=<target>`, `bar=1Dutc`, `limit=100`
- `/api/v5/public/funding-rate-history`，参数 `instId=<target>`, `limit=100`

分页策略：从默认返回页开始，自动测试 `before/after` 哪个 cursor 能取到更老数据；本次 `LUNA-USDT-SWAP` 可用 cursor 为 `after`。

| instId | history-candles | funding-rate-history | 解释 |
|---|---|---|---|
| `LUNA-USDT-SWAP` | HTTP 200, code `0`, 1477 rows, 2022-05-28 至 2026-06-12 | HTTP 200, code `0`, 556 rows, 2026-03-11 12:00 至 2026-06-12 00:00 | 当前仍存在的 LUNA swap 可取；不覆盖 2022-05-28 之前旧 LUNA 崩盘期 |
| `FTT-USDT-SWAP` | HTTP 200, code `51001`, 0 rows | HTTP 200, code `51001`, 0 rows | OKX 不识别旧 instId |
| `SRM-USDT-SWAP` | HTTP 200, code `51001`, 0 rows | HTTP 200, code `51001`, 0 rows | OKX 不识别旧 instId |
| `ANC-USDT-SWAP` | HTTP 200, code `51001`, 0 rows | HTTP 200, code `51001`, 0 rows | OKX 不识别旧 instId |

## Binance data.binance.vision 降级预案佐证

HEAD 请求路径格式：

```text
https://data.binance.vision/data/futures/um/daily/klines/{SYMBOL}/1d/{SYMBOL}-1d-{YYYY-MM-DD}.zip
```

结果：

| OKX instId | Binance symbol | Tested dates | HTTP 200 dates | Notes |
|---|---|---|---|---|
| `LUNA-USDT-SWAP` | `LUNAUSDT` | 2022-05-01, 2022-04-01, 2022-03-01 | all three | 旧 LUNA 崩盘前后 K 线归档存在 |
| `FTT-USDT-SWAP` | `FTTUSDT` | 2022-11-01, 2022-10-01, 2022-09-01 | all three | FTX 崩盘期前后 K 线归档存在 |
| `SRM-USDT-SWAP` | `SRMUSDT` | 2022-11-01, 2022-10-01, 2022-09-01 | all three | K 线归档存在 |
| `ANC-USDT-SWAP` | `ANCUSDT` | 2022-05-01, 2022-04-01, 2022-03-01 | 2022-05-01, 2022-04-01 | 2022-03-01 返回 404，但后续月份归档存在 |

## 原始探测要点

- OKX 对不存在旧 instId 的行为不是 HTTP 404，而是 HTTP 200 + OKX code `51001` + 空 `data`。
- `LUNA-USDT-SWAP` 不能作为“OKX 可取退市旧合约历史”的正例：当前 instruments 中仍存在该 instId，且 OKX 日线最早只到 2022-05-28。
- Binance archive HEAD 只证明日 K zip 文件存在，不代表 Binance/OKX 口径一致；若采用该降级方案，回测报告必须披露混合口径只用于 OKX 已退市且 OKX REST 无法回拉的标的。
