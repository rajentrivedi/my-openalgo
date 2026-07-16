"""
N7Quality Strategy Monitor
===========================
Run after the strategy to check order intents, holdings, and portfolio state.
Usage: uv run python monitor_n7.py
"""

import csv
import os
from datetime import datetime

from openalgo import api

API_KEY = "f7c106276c5424d3e7f077d08fee157acedb9d460bea00f3b53fc5e5d3799360"
HOST = "http://127.0.0.1:5000"

client = api(api_key=API_KEY, host=HOST)

print("=" * 60)
print(f"  N7Quality Monitor — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
print("=" * 60)

# --- 1. Order Intent Log ---
print("\n[1] RECENT ORDER INTENTS (last 20)")
print("-" * 60)
log_file = "openalgo_intent_log.csv"
if os.path.exists(log_file):
    with open(log_file) as f:
        rows = list(csv.DictReader(f))
    for row in rows[-20:]:
        print(f"  {row.get('ts','')[:10]} | {row['action']:4s} | {row['symbol']:12s} | "
              f"qty={row['quantity']:>6} | {row['status']}")
    print(f"  Total intents logged: {len(rows)}")
else:
    print("  No intent log found.")

# --- 2. OpenAlgo Order Book ---
print("\n[2] OPENALGO ORDER BOOK")
print("-" * 60)
try:
    book = client.orderbook()
    if book:
        for o in book[-10:]:
            print(f"  {o.get('order_status',''):10s} | {o.get('action',''):4s} | "
                  f"{o.get('symbol',''):12s} | qty={o.get('quantity',''):>6} | "
                  f"price={o.get('average_price','')}")
        print(f"  Total orders: {len(book)}")
    else:
        print("  No orders found.")
except Exception as e:
    print(f"  Error: {e}")

# --- 3. Position Book ---
print("\n[3] OPENALGO POSITIONS")
print("-" * 60)
try:
    pos = client.positionbook()
    if pos:
        for p in pos:
            print(f"  {p.get('symbol',''):12s} | {p.get('product',''):4s} | "
                  f"qty={p.get('quantity',''):>6} | avg={p.get('average_price',''):>10} | "
                  f"P&L={p.get('pnl',''):>10}")
    else:
        print("  No positions.")
except Exception as e:
    print(f"  Error: {e}")

# --- 4. Holdings ---
print("\n[4] OPENALGO HOLDINGS")
print("-" * 60)
try:
    holdings = client.holdings()
    if holdings:
        for h in holdings:
            print(f"  {h.get('symbol',''):12s} | qty={h.get('quantity',''):>6} | "
                  f"avg={h.get('average_price',''):>10} | "
                  f"LTP={h.get('ltp',''):>10} | P&L={h.get('pnl',''):>10}")
    else:
        print("  No holdings.")
except Exception as e:
    print(f"  Error: {e}")

# --- 5. Funds ---
print("\n[5] ACCOUNT FUNDS")
print("-" * 60)
try:
    funds = client.funds()
    if funds:
        d = funds.get("data", funds) if isinstance(funds, dict) else funds
        for k, v in (d.items() if isinstance(d, dict) else []):
            print(f"  {k}: {v}")
except Exception as e:
    print(f"  Error: {e}")

print("\n" + "=" * 60)
print("  Monitor complete.")
print("=" * 60)
