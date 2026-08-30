# Discovery edge-map summary

Window: first 12 months, 60 most-liquid NIFTY 500 symbols.
Net = gross forward return minus 20 bps round-trip cost (applied for hold>=3).
Forward returns computed within the same trading day only.

## Best cell per signal (max net bps across time-bucket x quantile x ATR-quintile x trend)

| signal | hold | tb | q | atr_q | trend | n | gross bps | net bps | hit | PF |
|---|---|---|---|---|---|---|---|---|---|---|
| bbpct | 10 | open | 1 | 5 | 1 | 5605 | 10.900 | -9.100 | 0.5707 | 1.622 |
| or_fade | 15 | open | 1 | 3 | 1 | 583 | 8.357 | -11.643 | 0.5609 | 1.809 |
| roll_z | 15 | morning | 1 | 5 | 1 | 607 | 6.634 | -13.366 | 0.5502 | 1.432 |
| rvol | 5 | open | 5 | 2 | 1 | 276 | 4.855 | -15.145 | 0.5290 | 1.453 |
| vwap_z | 15 | morning | 4 | 5 | 0 | 426 | 4.850 | -15.150 | 0.4812 | 1.225 |
| rsi2 | 5 | open | 5 | 2 | 1 | 501 | 3.754 | -16.246 | 0.5230 | 1.419 |
