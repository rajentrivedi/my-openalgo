# V5.1 Extreme-Volume Breakout Robustness

## 1. Does extreme volume improve expectancy?
RV>4 trades: 252 net=-1238 pf=2.00 wr=39.7%

Volume distribution shows smooth improvement:
  1-1.5: n=0 wr=nan% net=0 avgR=nan
  1.5-2: n=0 wr=nan% net=0 avgR=nan
  2-2.5: n=0 wr=nan% net=0 avgR=nan
  2.5-3: n=0 wr=nan% net=0 avgR=nan
  3-3.5: n=0 wr=nan% net=0 avgR=nan
  3.5-4: n=0 wr=nan% net=0 avgR=nan
  4-5: n=77 wr=39.0% net=-430 avgR=0.018
  5-7.5: n=85 wr=41.2% net=-633 avgR=0.011
  7.5-10: n=44 wr=34.1% net=-597 avgR=-0.073
  10-inf: n=46 wr=43.5% net=421 avgR=0.056

## 2. Is RV=4 a robust region?
Volume x Displacement PF matrix:
disp_bucket  <=0  0-0.25  0.25-0.5     0.5-1        >1
rv_bucket                                             
<1.5         NaN     NaN       NaN       NaN       NaN
1.5-2.5      NaN     NaN       NaN       NaN       NaN
2.5-3.5      NaN     NaN       NaN       NaN       NaN
3.5-4.5      NaN     NaN  1.502222  0.585424  1.544068
>4.5         NaN     inf  0.053777  0.163566  1.216215

## 3. Does volume need displacement?
Breakout quality combos:
    body  disp  trades      net        pf         wr
0    0.0   0.0     252 -1238.42  0.815860  39.682540
1    0.0   0.3     250 -1233.02  0.815688  39.600000
2    0.0   0.5     236  -866.45  0.859088  40.677966
3    0.0   1.0     178   933.45  1.243534  46.067416
4    0.5   0.0     252 -1238.42  0.815860  39.682540
5    0.5   0.3     250 -1233.02  0.815688  39.600000
6    0.5   0.5     236  -866.45  0.859088  40.677966
7    0.5   1.0     178   933.45  1.243534  46.067416
8    0.6   0.0     252 -1238.42  0.815860  39.682540
9    0.6   0.3     250 -1233.02  0.815688  39.600000
10   0.6   0.5     236  -866.45  0.859088  40.677966
11   0.6   1.0     178   933.45  1.243534  46.067416

## 4. Which market regimes support the strategy?
  nifty_regime  trades         wr      net     avg_r
0      BEARISH     124  41.935484  -158.79  0.027097
1      BULLISH     128  37.500000 -1079.63 -0.013516

## 5. Walk-Forward Failure Analysis
   window                     start                       end   n     net        pf         wr     avg_rv  avg_disp   avg_mfe   avg_mae
0       1 2026-07-30 10:35:00+05:30 2026-08-11 10:15:00+05:30  38 -385.94  0.662955  39.473684   8.541263         0  0.278000 -0.282579
1       2 2026-08-11 10:15:00+05:30 2026-08-21 10:00:00+05:30  40 -459.37  0.631546  42.500000  11.504525         0  0.281550 -0.310275
2       3 2026-08-21 10:00:00+05:30 2026-09-02 09:45:00+05:30  44 -614.19  0.562087  36.363636   6.790341         0  0.281705 -0.341500

## 6. Time-of-Day Analysis
N/A

## 7. Direction Analysis
  direction  trades         wr      net        pf
0      LONG     128  37.500000 -1079.63  0.732808
1     SHORT     124  41.935484  -158.79  0.940855

## 8. Cost & Slippage
Slippage:    mult   n     net        pf
0  1.00  55  105.94  1.106523
1  1.25  55   17.54  1.016918
2  1.50  55  -70.93  0.934274
3  2.00  55 -250.27  0.784808

## 9. Monte Carlo (5000 reshuffles)
           final    max_dd     lowest
0.05   97797.125    10.000  97312.375
0.25   99322.500   322.500  98510.000
0.50  100385.000   797.500  99202.500
0.75  101448.125  1490.000  99677.500
0.95  103010.000  2687.625  99990.000

## 10. Final Decision
Fresh OOS trades: 25 net=-2
Walk-forward: 0/3 windows positive

**Final Verdict: NO ROBUST EDGE**
