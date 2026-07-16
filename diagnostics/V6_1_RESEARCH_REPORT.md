# V6.1 Robustness Validation Report

## 1. Executive Summary

## 2. V6 Reproduction
## 3. V6 Implementation Audit
## 4. Trade Population Audit
## 5. EMA Calculation Audit
## 6. NIFTY Regime Audit
## 7. Volume Audit

## 11. MA Ablation (V6.1 corrected)
                   config  tr_n  tr_net  tr_pf  te_n  te_net  te_pf  te_wr
0    ALL bars (no filter)  1350   -2511  0.931   437    2042  1.195   46.2
1  close>EMA20 (momentum)  1350  -11006  0.726   437   -1650  0.853   42.1
2            + EMA9>EMA20  1350  -12794  0.675   436   -1393  0.871   42.4
3  + EMA20>EMA50 (struct)  1350  -15506  0.620   437   -2590  0.769   40.0
4     + EMA9>20>50 struct  1350  -11614  0.698   436   -1896  0.820   40.4
5               + slope>0  1350  -14867  0.624   436   -3723  0.654   37.6
6          + volume>=1.5x  1334  -15284  0.546   401   -4039  0.575   33.4
7          + NIFTY regime     0       0  0.000   401   -4039  0.575   33.4

## 12. EMA Robustness

## 26. Walk-Forward
   window    n    net     pf    wr
0       1  269  -3623  0.502  29.0
1       2  461  -6972  0.425  28.4
2       3  645  -8048  0.509  31.2
3       4  828 -10271  0.497  31.8

## 27. Fresh OOS
n=400 net=-5315 pf=0.472 wr=30.5%

## 28. Monte Carlo
   index          final        max_dd
0   0.05   90435.529525    585.954471
1   0.25   93988.316334   2616.893832
2   0.50   96647.776489   4552.169536
3   0.75   99105.539817   6912.398453
4   0.95  102613.990388  10324.153489

## Final Decision
**PROMISING — NEED MORE DATA**

