# V5 Research Report

## 1. Executive Summary
V5 rearchitects the strategy around extreme-abnormal-volume breakouts.

## 2. V4 Failure Analysis
V4.1 conclusively rejected the old range-breakout architecture (PF<1 on 7/7 gates).

## 3. Hypotheses Tested
- H1: Extreme abnormal volume (3.0×RV, volume percentile 80-99)
- H2: Candle quality (close-location, body) + displacement
- H3: Market regime (NIFTY EMA) + direction

## 4. Results by Hypothesis
### H1: Extreme Volume
- H1_rv=1.2: train=  -892 pf=0.813  val=   -79 pf=0.936  test=   -94 pf=0.910
- H1_rv=2.0: train=  -295 pf=0.933  val=   157 pf=1.133  test=    61 pf=1.057
- H1_rv=3.0: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H1_rv=4.0: train=   429 pf=1.179  val=   321 pf=1.369  test=   356 pf=1.455
- H1_vpct=80: train= -1102 pf=0.785  val=    59 pf=1.049  test=   206 pf=1.208
- H1_vpct=90: train=  -858 pf=0.826  val=   174 pf=1.167  test=   566 pf=1.599
- H1_vpct=95: train=   223 pf=1.062  val=   145 pf=1.144  test=   453 pf=1.574
- H1_vpct=99: train=     0 pf=0.000  val=     0 pf=0.000  test=     0 pf=0.000

### H2: Candle Quality
- H2_disp=0.1_cl=0.0: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H2_disp=0.1_cl=0.6: train=   347 pf=1.116  val=  -322 pf=0.739  test=  -146 pf=0.869
- H2_disp=0.1_cl=0.7: train=   116 pf=1.042  val=  -420 pf=0.652  test=   151 pf=1.186
- H2_disp=0.25_cl=0.0: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H2_disp=0.25_cl=0.6: train=   347 pf=1.116  val=  -322 pf=0.739  test=  -146 pf=0.869
- H2_disp=0.25_cl=0.7: train=   116 pf=1.042  val=  -420 pf=0.652  test=   151 pf=1.186
- H2_disp=0.5_cl=0.0: train=   657 pf=1.235  val=   131 pf=1.120  test=    45 pf=1.041
- H2_disp=0.5_cl=0.6: train=   395 pf=1.149  val=  -234 pf=0.796  test=  -191 pf=0.819
- H2_disp=0.5_cl=0.7: train=   279 pf=1.116  val=  -399 pf=0.664  test=   105 pf=1.141
- H2_disp=1.0_cl=0.0: train=   511 pf=1.259  val=   220 pf=1.229  test=   284 pf=1.368
- H2_disp=1.0_cl=0.6: train=   377 pf=1.204  val=    38 pf=1.043  test=    69 pf=1.092
- H2_disp=1.0_cl=0.7: train=   200 pf=1.112  val=  -194 pf=0.781  test=   357 pf=1.796

### H3: Market Regime
- H3_regime_rv=2.0: train=  -295 pf=0.933  val=   157 pf=1.133  test=    61 pf=1.057
- H3_regime_rv=3.0: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H3_regime_rv=4.0: train=   429 pf=1.179  val=   321 pf=1.369  test=   356 pf=1.455
- H3_both_rv=3: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H3_long_rv=3: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196
- H3_short_rv=3: train=   221 pf=1.065  val=     7 pf=1.006  test=   203 pf=1.196

## 5. Best Composite
Selected: H1_rv=4.0
Train: net=429 pf=1.179
Val: net=321 pf=1.369
Test: net=356 pf=1.455
Fresh OOS: net=214.31999999999994 pf=1.595 wr=42.9%

## 6. Walk-Forward
Window 1: test_n=20 net=243 pf=1.642
Window 2: test_n=25 net=37 pf=1.045
Window 3: test_n=29 net=-533 pf=0.488

## 7. Cost Stress
  cost=0.00: n=39 net=    735 pf=2.178
  cost=0.01: n=39 net=    609 pf=1.907
  cost=0.02: n=39 net=    483 pf=1.665
  cost=0.03: n=39 net=    356 pf=1.455
  cost=0.05: n=39 net=    100 pf=1.111

## 8. Monte Carlo (best composite)
              final  max_dd    lowest
0.05  100578.609227     0.0  100000.0
0.25  102336.561881     0.0  100000.0
0.50  103523.604534     0.0  100000.0
0.75  104692.220624     0.0  100000.0
0.95  106372.870121     0.0  100000.0
95th pctile DD: 0

## 9. Decision Table
| Test | Result | Status |
| --- | --- | --- |
| Fresh OOS | net=214 pf=1.60 | PASS |
| Walk-forward | avg pf=1.058 | FAIL |
| Cost robust | min_pf=1.111 | PASS |
| Monte Carlo DD | 95th=0 | PASS |

## Final Recommendation: **PROMISING BUT NEEDS MORE DATA**

