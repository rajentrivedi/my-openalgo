# V6 Research Report — Intraday Momentum + MA Trend + Abnormal Volume

## Attribution (train/test)
                             config  tr_n  tr_net     tr_pf  te_n  te_net  te_pf  te_wr
0     A close>EMA20 (bare momentum)   160    -158  0.967575    70    1100  1.819   60.0
1                    B + EMA9>EMA20   160    -262  0.945082    70    1397  2.259   61.4
2                   C + EMA20>EMA50   160     156  1.035241    70    1475  2.329   64.3
3                 D + EMA20 slope>0   160    -621  0.872788    65     532  1.489   58.5
4  E EMA9>EMA20>EMA50 (full struct)   160    -249  0.946555    70    1495  2.347   64.3
5            F struct + slope + vol   160     565  1.127907    65     437  1.358   60.0
6              FULL recipe + regime   160     565  1.127907    65     437  1.358   60.0

## Test-period equity rows: 995
## Final Recommendation: **PROMISING — NEED MORE DATA**

